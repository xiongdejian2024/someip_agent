"""隔离 veth 上的可复现负载证据；混合 Python 链路与原生发生器分开报告。"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import math
import os
import platform
import resource
import signal
import subprocess
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import dpkt
from performance_metrics import (
    distribution,
    intervals,
    kernel_capture_drops,
    sequence_accounting,
)
from soa_partner.src.base_partner import S2sBaseClass
from soa_partner.src.Operator import SOAOperator
from someip_agent.config import Settings
from someip_agent.pcap.importer import PcapImporter
from someip_agent.protocol.someip import decode_many

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent
SERVICE, CLIENT_ID = 0x5678, 0x8822


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def proc_sample(pid):
    # comm 可以含空格/括号，不能按整行空格位置误读 utime/rss；核对 starttime 防 PID 复用。
    values = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "start_ticks": int(values[19]),
        "cpu_seconds": (int(values[11]) + int(values[12])) / os.sysconf("SC_CLK_TCK"),
        "rss_kib": int(values[21]) * os.sysconf("SC_PAGE_SIZE") / 1024,
    }


def resources(processes):
    sample = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "harness_cpu_seconds": sample.ru_utime + sample.ru_stime,
        "harness_peak_rss_kib": sample.ru_maxrss,
        "native": [proc_sample(p.pid) for p in processes],
    }


def resource_delta(before, after, elapsed):
    native = []
    for old, new in zip(before["native"], after["native"], strict=True):
        if (old["pid"], old["start_ticks"]) != (new["pid"], new["start_ticks"]):
            raise RuntimeError("测量期间原生进程身份改变")
        seconds = new["cpu_seconds"] - old["cpu_seconds"]
        native.append(
            {
                **new,
                "cpu_seconds": seconds,
                "one_core_cpu_percent": 100 * seconds / elapsed,
            }
        )
    return {
        "scope_seconds": elapsed,
        "native_server_client": native,
        "harness_cpu_seconds": after["harness_cpu_seconds"]
        - before["harness_cpu_seconds"],
        "harness_peak_rss_kib": after["harness_peak_rss_kib"],
        "scope": "Python 编排、服务回调、接收与采集总 CPU；原生 CPU 仅各自拥有的 PID；不含离线审计",
    }


def stop_process(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.exception("自有验收进程退出超时")
        process.kill()
        process.wait(timeout=5)


@contextmanager
def peers(directory, transport):
    processes, handles, partners = [], [], []
    records, errors, lock = [], [], threading.Condition()
    rpc_schema = {
        "type": "struct",
        "fields": [
            {"name": "sequence", "type": "uint32"},
            {"name": "blob", "type": "bytes", "length_bytes": 4},
        ],
    }
    catalog = {
        "BenchService": {
            "service_id": SERVICE,
            "instance_id": 1,
            "major_version": 1,
            "methods": {"Echo": {"id": 1, "input": rpc_schema, "output": rpc_schema}},
            "events": {
                "UpdateSampleEvent": {
                    "id": 0x8001,
                    "eventgroups": [1],
                    "schema": {
                        "type": "struct",
                        "fields": [{"name": "value", "type": "uint32"}],
                    },
                }
            },
        }
    }
    catalog_path = directory / "catalog.json"
    write_json(catalog_path, catalog)

    def callback(_key, message):
        with lock:
            if (
                message["action"] == "event"
                and message["function"] == "UpdateSampleEvent"
            ):
                records.append(
                    (json.loads(message["args"])["value"], time.monotonic_ns())
                )
            elif message["action"] == "error":
                errors.append(message)
            lock.notify_all()

    try:
        for node, address, app_id in (
            ("server", "10.77.0.1", 0x8811),
            ("client", "10.77.0.2", CLIENT_ID),
        ):
            name = f"bench_{directory.name}_{node}"
            config = json.loads((ROOT / f"{node}.json").read_text())
            config.update(
                network=name,
                routing=name,
                applications=[{"name": name, "id": hex(app_id)}],
            )
            if node == "server":
                config["services"] = [
                    {
                        "service": hex(SERVICE),
                        "instance": "0x1",
                        "reliable" if transport == "tcp" else "unreliable": 30631
                        if transport == "tcp"
                        else 30630,
                        "events": [
                            {
                                "event": "0x8001",
                                "is_field": False,
                                "is_reliable": transport == "tcp",
                            }
                        ],
                        "eventgroups": [{"eventgroup": "0x1", "events": ["0x8001"]}],
                    }
                ]
            config_path = directory / f"{node}.json"
            write_json(config_path, config)
            handle = (directory / f"{node}.log").open("ab")
            handles.append(handle)
            process = subprocess.Popen(
                [
                    "ip",
                    "netns",
                    "exec",
                    "soa-" + node,
                    os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    "run",
                    "--name",
                    name,
                    "--bind",
                    address,
                    "--catalog",
                    str(catalog_path),
                    "--config",
                    str(config_path),
                ],
                stdout=handle,
                stderr=handle,
            )
            processes.append(process)
            partners.append(
                S2sBaseClass(
                    {"BenchService": {"role": node, "transport": transport}},
                    operator=SOAOperator(name, host=address),
                    attach=True,
                )
            )
        server, client = partners
        server.register_auto_response("BenchService_server", "Echo", echo=True)
        server.register_callback("BenchService_server", callback)
        client.register_callback("BenchService_client", callback)
        assert client.wait_for_service_reconnect("BenchService_client", timeout=10)
        deadline = time.monotonic() + 5
        with lock:
            while not records and time.monotonic() < deadline:
                server.send_event_notify("BenchService_server", "Sample", {"value": 0})
                lock.wait(timeout=0.05)
        if not records:
            raise RuntimeError("未收到真实准备通知，不能开始测量")
        yield server, client, processes, records, errors, lock
    finally:
        for partner in partners:
            partner.close()
        for process in processes:
            stop_process(process)
        for handle in handles:
            handle.close()


@contextmanager
def capture(directory):
    path = directory / "wire.pcap"
    log = directory / "tcpdump.log"
    with log.open("ab") as handle:
        process = subprocess.Popen(
            [
                "tcpdump",
                "--immediate-mode",
                "-i",
                "soa-bridge",
                "-s",
                "0",
                "-B",
                "16384",
                "-w",
                str(path),
                "port 30630 or port 30631",
            ],
            stdout=handle,
            stderr=handle,
        )
        try:
            deadline = time.monotonic() + 5
            while "listening on" not in log.read_text():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("tcpdump 未在限定时限内就绪")
                time.sleep(0.01)
            yield path
            if process.poll() is not None:
                raise RuntimeError("tcpdump 在测量结束前已退出，不能使用不完整抓包")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.exception("tcpdump 停止超时，性能抓包不完整")
                    stop_process(process)
                    raise


def wire_audit(path, directory, mode, transport, expected, blob):
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=directory / "offline")
    ).parse(path.read_bytes(), path.name)
    frames, total_bytes, independent_udp = 0, 0, Counter()
    with path.open("rb") as source:
        for _, raw in dpkt.pcap.Reader(source):
            frames += 1
            total_bytes += len(raw)
            packet = dpkt.ethernet.Ethernet(raw).data
            if isinstance(packet, dpkt.ip.IP) and isinstance(packet.data, dpkt.udp.UDP):
                for message in decode_many(packet.data.data):
                    if message.header.service_id == SERVICE:
                        independent_udp[
                            (message.header.message_type, message.payload.hex())
                        ] += 1
    if (
        result.runtime != "vsomeip"
        or result.packet_count != frames
        or result.captured_bytes != total_bytes
    ):
        raise AssertionError("原生解码和独立 PCAP 帧/字节统计不一致")
    matched, timestamps, checks = {0: [], 0x80: [], 2: []}, [], []
    for message in messages:
        if message.service_id != SERVICE:
            continue
        if (
            message.transport != transport
            or message.interface_version != 1
            or message.return_code != 0
        ):
            raise AssertionError("负载报文协议属性错误")
        payload = bytes.fromhex(message.payload_hex)
        if len(payload) < 4:
            raise AssertionError("负载报文缺少完整四字节序号")
        seq = int.from_bytes(payload[:4], "big")
        if seq == 0:
            if (
                payload != bytes(4)
                or message.method_id != 0x8001
                or message.message_type != 2
            ):
                raise AssertionError("准备探针线上内容异常")
            continue  # 启动准备探针不计入测量。
        kind = message.message_type
        expected_payload = seq.to_bytes(4, "big") + (
            len(blob).to_bytes(4, "big") + blob if mode == "rpc" else b""
        )
        if payload != expected_payload or message.method_id != (
            1 if mode == "rpc" else 0x8001
        ):
            raise AssertionError("负载报文字节与手工黄金不一致")
        if kind not in ({0, 0x80} if mode == "rpc" else {2}):
            raise AssertionError("负载报文消息类型错误")
        if kind != 2 and message.client_id != CLIENT_ID:
            raise AssertionError("RPC 线上客户端身份错误")
        source = "10.77.0.2" if kind == 0 else "10.77.0.1"
        destination = "10.77.0.1" if kind == 0 else "10.77.0.2"
        if (
            message.source.rsplit(":", 1)[0] != source
            or message.destination.rsplit(":", 1)[0] != destination
        ):
            raise AssertionError("负载报文方向错误")
        service_endpoint = message.destination if kind == 0 else message.source
        if int(service_endpoint.rsplit(":", 1)[1]) != (
            30631 if transport == "tcp" else 30630
        ):
            raise AssertionError("负载报文服务端口错误")
        matched[kind].append(seq)
        if kind == 2:
            timestamps.append(message.metadata["native_timestamp_ns"])
        checks.append(
            {
                "sequence": seq,
                "kind": kind,
                "payload_hex": message.payload_hex,
                "frame": message.metadata["pcap_frame"],
                "timestamp_ns": message.metadata["native_timestamp_ns"],
            }
        )
    if transport == "udp":
        native_counts = Counter((v["kind"], v["payload_hex"]) for v in checks)
        independent_udp = Counter(
            {
                k: v
                for k, v in independent_udp.items()
                if int.from_bytes(bytes.fromhex(k[1])[:4], "big") != 0
            }
        )
        if native_counts != independent_udp:
            raise AssertionError("UDP 独立 Python 分析器与原生字节/次数不一致")
    write_json(
        directory / "wire-audit.json",
        {"statistics": result.model_dump(mode="json"), "messages": checks},
    )
    drops = kernel_capture_drops((directory / "tcpdump.log").read_text())
    return {
        "kernel_capture_drops": drops,
        "pcap_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sequences": {
            str(k): sequence_accounting(expected, v)
            for k, v in matched.items()
            if mode == "rpc" and k != 2 or mode != "rpc" and k == 2
        },
        "timestamps_ns": timestamps,
        "timestamp_source": "pcap 软件时间；TCP 为完成帧时间，可含合并消息零间隔",
    }


def run_profile(directory, spec, duration, rpc_count):
    base = spec["id"] * 1_000_000
    blob = bytes([0xAB]) * spec.get("blob_bytes", 0)
    report = {**spec, "status": "measuring"}
    write_json(directory / "result.json", report)
    with peers(directory, spec["transport"]) as (
        server,
        client,
        processes,
        received,
        errors,
        changed,
    ):
        with capture(directory) as path:
            before, started = resources(processes), time.monotonic()
            samples = []
            if spec["mode"] == "rpc":
                expected = list(range(base, base + rpc_count))

                def call(seq):
                    args = {"sequence": seq, "blob": blob.hex()}
                    begin = time.monotonic_ns()
                    try:
                        response = client.send_request_and_return_resp(
                            "BenchService_client", "Echo", args, timeout=5
                        )
                        if response != {"out": args}:
                            raise AssertionError("RPC 回执与请求黄金值不一致")
                        return {
                            "sequence": seq,
                            "elapsed_ms": (time.monotonic_ns() - begin) / 1_000_000,
                            "ok": True,
                        }
                    except Exception:
                        logger.exception("负载 RPC 失败")
                        return {
                            "sequence": seq,
                            "ok": False,
                            "exception": traceback.format_exc(),
                        }

                with ThreadPoolExecutor(max_workers=spec["workers"]) as workers:
                    samples = list(workers.map(call, expected))
                produced_seconds = time.monotonic() - started
                report.update(
                    latency_ms=distribution(
                        [s["elapsed_ms"] for s in samples if s["ok"]]
                    ),
                    successful=sum(s["ok"] for s in samples),
                    failed=sum(not s["ok"] for s in samples),
                    completed_rpc_per_second=sum(s["ok"] for s in samples)
                    / produced_seconds,
                )
            else:
                interval = spec["interval_ms"] / 1000
                if spec["mode"] == "native_sequence":
                    limit = math.ceil(duration / interval) + 1000
                    sequence = list(range(base, base + limit))
                    server.sim_operator.send_request(
                        "generator_start",
                        {
                            "member": "BenchService_server",
                            "function": "UpdateSampleEvent",
                            "interval_ms": spec["interval_ms"],
                            "generator": {
                                "kind": "sequence",
                                "sequence": sequence,
                                "data_type": "uint32",
                                "signal_name": "value",
                            },
                        },
                    )
                    time.sleep(duration)
                    server.sim_operator.send_request(
                        "generator_stop", {"member": "BenchService_server"}
                    )
                    count = server.sim_operator.send_request("running_service")[
                        "BenchService_server"
                    ]["emitted_count"]
                    if count > limit:
                        raise AssertionError("原生序列已循环，不能据此计算唯一消息缺失")
                    expected = list(range(base, base + count))
                    report["production_scope"] = (
                        "包含一次序列构造、generator_start/stop 和计数查询；原生计数为尝试通知数，逐条与线上和客户端交付核对"
                    )
                    report["sequence_length"] = limit
                else:
                    expected, deadline = [], started + duration
                    report["production_scope"] = (
                        "按绝对时刻逐条调用兼容 SAT 通知接口；调用完成不等同于原生确认或远端交付"
                    )
                    while time.monotonic() < deadline:
                        seq = base + len(expected)
                        tick = time.monotonic_ns()
                        server.send_event_notify(
                            "BenchService_server", "Sample", {"value": seq}
                        )
                        expected.append(seq)
                        samples.append({"sequence": seq, "submitted_ns": tick})
                        time.sleep(
                            max(
                                0,
                                min(
                                    started
                                    + len(expected) * interval
                                    - time.monotonic(),
                                    deadline - time.monotonic(),
                                ),
                            )
                        )
                produced_seconds = time.monotonic() - started
                end = time.monotonic() + 2
                with changed:
                    while (
                        sum(base <= seq < base + len(expected) for seq, _ in received)
                        < len(expected)
                        and time.monotonic() < end
                    ):
                        changed.wait(timeout=0.05)
                actual = [
                    (seq, timestamp) for seq, timestamp in received if seq >= base
                ]
                report.update(
                    client_delivery=sequence_accounting(
                        expected, [seq for seq, _ in actual]
                    ),
                    client_intervals=intervals(
                        [t for _, t in actual], spec["interval_ms"] * 1_000_000
                    ),
                    offered_count=len(expected),
                    produced_per_second=len(expected) / produced_seconds,
                )
            elapsed = time.monotonic() - started
            report["resources"] = resource_delta(before, resources(processes), elapsed)
            report["native_errors"] = errors
            report["production_seconds"] = produced_seconds
            write_json(directory / "samples.json", samples)
        audit = wire_audit(
            path, directory, spec["mode"], spec["transport"], expected, blob
        )
        stamps = audit.pop("timestamps_ns")
        if spec["mode"] != "rpc":
            audit["wire_intervals"] = intervals(stamps, spec["interval_ms"] * 1_000_000)
        report["wire"] = audit
        sequences = [
            *audit["sequences"].values(),
            *([report["client_delivery"]] if spec["mode"] != "rpc" else []),
        ]
        valid = (
            bool(expected)
            and not errors
            and not audit["kernel_capture_drops"]
            and not report.get("failed", 0)
        )
        valid = valid and all(
            not any(v[k] for k in ("missing", "duplicates", "unexpected"))
            for v in sequences
        )
        report["status"] = "verified" if valid else "observation_failed"
        write_json(directory / "result.json", report)
    logger.info("负载阶段结束：%s %s", directory.name, report["status"])
    return report


def main():
    parser = argparse.ArgumentParser(
        description="隔离虚拟网负载测量，不声明线速或硬实时"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--rpc-count", type=int, default=200)
    parser.add_argument("--native-only", action="store_true")
    parser.add_argument("--require-installed", action="store_true")
    args = parser.parse_args()
    if (
        not math.isfinite(args.duration)
        or not 1 <= args.duration <= 30
        or not 10 <= args.rpc_count <= 10000
    ):
        parser.error("持续时间必须为 1-30 秒，RPC 次数必须为 10-10000")
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    output = args.output.resolve()
    if (output / "report.json").exists() or any(output.glob("profile-*")):
        raise RuntimeError("性能目录已有负载证据，请指定新目录；拒绝覆盖旧报告或抓包")
    binary = Path(os.environ["SOMEIP_AGENT_NATIVE_BINARY"])
    module_path = Path(inspect.getfile(S2sBaseClass)).resolve()
    if args.require_installed and (
        os.environ.get("PYTHONPATH") or "site-packages" not in module_path.parts
    ):
        raise RuntimeError(f"性能安装包验收不得使用源码模块：{module_path}")
    report = {
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "load_average": os.getloadavg(),
            "native_version": subprocess.check_output(
                [str(binary), "--version"], text=True
            ).strip(),
            "native_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "module_path": str(module_path),
            "duration_seconds": args.duration,
            "rpc_count": args.rpc_count,
        },
        "profiles": [],
        "scope": "单次 Linux Docker/veth 软件时间观测；RPC 含 Python 调用/回调/IPC；原生序列含编码/定时器/vsomeip；不是纯 C++ 吞吐对照、HIL、线速或长稳承诺",
    }
    cases = []
    for transport in ("udp", "tcp"):
        if not args.native_only:
            cases.extend(
                {
                    "transport": transport,
                    "mode": "rpc",
                    "blob_bytes": size,
                    "workers": workers,
                }
                for size in (32, 1024)
                for workers in (1, 4)
            )
        cases.extend(
            {"transport": transport, "mode": mode, "interval_ms": interval}
            for mode in (
                ("native_sequence",)
                if args.native_only
                else ("python_events", "native_sequence")
            )
            for interval in (10, 1)
        )
    try:
        for index, case in enumerate(cases, 1):
            directory = output / f"profile-{index:02d}"
            directory.mkdir()
            spec = {"id": index, **case}
            try:
                measured = run_profile(directory, spec, args.duration, args.rpc_count)
            except Exception:
                logger.exception("负载阶段异常，保留部分证据")
                measured = {
                    **spec,
                    "status": "failed",
                    "exception": traceback.format_exc(),
                }
                write_json(directory / "failure.json", measured)
            report["profiles"].append(measured)
            write_json(output / "report.json", report)
    finally:
        write_json(output / "report.json", report)
    if any(p["status"] != "verified" for p in report["profiles"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
