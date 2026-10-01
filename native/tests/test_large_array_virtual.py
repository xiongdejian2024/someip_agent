"""大数组 TCP 分段、源字典与离线重组的安装包验收；仅操作隔离 veth。"""

import hashlib
import json
import logging
import os
import socket
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

import dpkt
import pytest
from audit_pcap import vsa_payload
from performance_metrics import kernel_capture_drops
from someip_agent.arxml import wire_types
from someip_agent.config import Settings
from someip_agent.pcap.importer import PcapImporter
from someip_agent.protocol.someip import decode_many
from test_arxml_composite_virtual import (
    test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth as run_composite,
)

logger = logging.getLogger(__name__)
EVIDENCE = Path("build/virtual-evidence")


def audit_large_array(path, order, port, directory):
    """沿用既有短单向流黄金审计，仅为本测试作独立 oracle，不进入产品栈。"""
    streams, starts = defaultdict(list), {}
    frames, frame_bytes = 0, 0
    with path.open("rb") as source:
        for _, raw in dpkt.pcap.Reader(source):
            frames += 1
            frame_bytes += len(raw)
            packet = dpkt.ethernet.Ethernet(raw).data
            assert isinstance(packet, dpkt.ip.IP)
            layer = packet.data
            assert isinstance(layer, dpkt.tcp.TCP)
            flow = (
                socket.inet_ntoa(packet.src),
                layer.sport,
                socket.inet_ntoa(packet.dst),
                layer.dport,
            )
            assert port in (layer.sport, layer.dport)
            if layer.flags & dpkt.tcp.TH_SYN:
                assert flow not in starts or starts[flow] == layer.seq + 1
                starts[flow] = layer.seq + 1
            if layer.data:
                streams[flow].append((layer.seq, bytes(layer.data)))
    assert len(streams) == 2, "应有请求和响应两个单向 TCP 流"
    independent = Counter()
    for flow, chunks in streams.items():
        chunks.sort(key=lambda item: item[0])
        assert chunks[0][0] == starts[flow], "缺失流起始帧不能冒充完整抓包"
        stream = bytearray()
        for seq, body in chunks:
            offset = seq - starts[flow]
            assert offset <= len(stream), "大数组抓包存在 TCP 缺口"
            overlap = min(len(body), len(stream) - offset)
            assert bytes(stream[offset : offset + overlap]) == body[:overlap], (
                "冲突重传"
            )
            stream.extend(body[overlap:])
        for message in decode_many(stream):
            header = message.header
            assert header.service_id == 0x3456
            assert header.interface_version == 1 and header.return_code == 0
            assert (flow[0], flow[2]) == (
                ("10.77.0.2", "10.77.0.1")
                if header.message_type == 0
                else ("10.77.0.1", "10.77.0.2")
            )
            assert header.message_type in (0, 0x80, 2)
            if header.message_type != 2:
                assert header.client_id == 0x7722 and header.session_id != 0
            independent[
                (
                    flow,
                    header.method_id,
                    header.message_type,
                    header.client_id,
                    header.session_id,
                    message.payload.hex(),
                )
            ] += 1

    words = (0x1234, 0xABCD) * 35000
    payload = vsa_payload(order, 4, words=words)
    changed = vsa_payload(order, 4, tag=8, words=words)
    variants = [vsa_payload(order, 4, words=values) for values in ((), (1, 2, 3))]
    scalar = (0x1234).to_bytes(2, order).hex()
    golden = Counter(
        {
            (1, 0, payload): 1,
            (1, 0x80, payload): 1,
            (2, 0, scalar): 1,
            (2, 0x80, scalar): 1,
            (0x8001, 2, payload): 1,
            (0x0101, 0, ""): 2,
            (0x0101, 0x80, payload): 1,
            (0x0101, 0x80, changed): 1,
            (0x0102, 0, changed): 1,
            (0x0102, 0x80, changed): 1,
            (0x8101, 2, payload): 1,
            (0x8101, 2, changed): 1,
            **{(1, kind, variant): 1 for variant in variants for kind in (0, 0x80)},
        }
    )
    observed, requests, responses = Counter(), Counter(), Counter()
    for (_, method, kind, client, session, body), count in independent.items():
        observed[method, kind, body] += count
        if kind in (0, 0x80):
            (requests if kind == 0 else responses)[client, session, method] += count
    assert requests == responses and all(count == 1 for count in requests.values())
    assert set(observed) == set(golden), "存在非法或非预期线上请求/通知"
    assert all(observed[key] >= minimum for key, minimum in golden.items())
    # 定期事件可多次出现；RPC 必须严格等于预期，不能忽略重复。
    assert all(
        observed[key] == minimum for key, minimum in golden.items() if key[1] != 2
    )
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=directory / "runtime")
    ).parse(path.read_bytes(), path.name)
    assert result.runtime == "vsomeip" and result.packet_count == frames
    assert result.captured_bytes == frame_bytes
    native = Counter()
    for message in messages:
        assert message.service_id == 0x3456
        src, sport = message.source.rsplit(":", 1)
        dst, dport = message.destination.rsplit(":", 1)
        native[
            (
                (src, int(sport), dst, int(dport)),
                message.method_id,
                message.message_type,
                message.client_id,
                message.session_id,
                message.payload_hex,
            )
        ] += 1
    assert native == independent, "产品 libtins/vsomeip 重组与独立黄金流不一致"
    report = {
        "runtime": "vsomeip",
        "byte_order": order,
        "transport": "tcp",
        "declared_max_elements": 1048576,
        "actual_elements": 70000,
        "array_wire_bytes": 140000,
        "payload_bytes": len(payload) // 2,
        "payload_sha256": hashlib.sha256(bytes.fromhex(payload)).hexdigest(),
        "frames": frames,
        "native_messages": sum(native.values()),
        "rpc_pairs": sum(requests.values()),
        "golden_vectors": len(golden),
        "pcap_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(
            Path(os.environ["SOMEIP_AGENT_NATIVE_BINARY"]).read_bytes()
        ).hexdigest(),
        "type_resolver_module": wire_types.__file__,
        "type_resolver_sha256": hashlib.sha256(
            Path(wire_types.__file__).read_bytes()
        ).hexdigest(),
        "verified": True,
        "scope": "双字节序短测，不证明声明最大数量均能通过 IPC/传输或长期负载",
    }
    (directory / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    return report


@pytest.mark.parametrize("order", ["big", "little"])
def test_large_vsa_tcp_rpc_events_fields_and_native_reassembly(order):
    directory = EVIDENCE / f"large-array-{order}"
    directory.mkdir(exist_ok=False)
    port = 31000 + (order == "little")
    path, log_path = directory / "wire.pcap", directory / "tcpdump.log"
    capture = None
    try:
        with log_path.open("wb") as output:
            capture = subprocess.Popen(
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
                    "tcp",
                    "port",
                    str(port),
                ],
                stdout=output,
                stderr=output,
            )
        deadline = time.monotonic() + 5
        while "listening on" not in log_path.read_text():
            assert capture.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        run_composite("tcp", order, 0, 4, 64, 32, 70000)
    except Exception:
        logger.exception("大数组隔离 TCP 验收失败")
        raise
    finally:
        if capture is not None:
            capture.send_signal(2)
            try:
                capture.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.exception("大数组自有采集进程退出超时")
                capture.kill()
                capture.wait(timeout=5)
                raise
    assert capture.returncode == 0
    assert kernel_capture_drops(log_path.read_text()) == 0
    assert "site-packages/" in wire_types.__file__ or os.environ.get("PYTHONPATH")
    try:
        report = audit_large_array(path, order, port, directory)
        logger.info("大数组 TCP 黄金验收完成：%s", report)
    except Exception:
        logger.exception("大数组独立黄金字节/产品原生重组审计失败")
        raise


@pytest.mark.parametrize("order", ["big", "little"])
@pytest.mark.parametrize("change", ["missing_segment", "length", "payload", "client"])
def test_large_array_audit_rejects_modified_real_capture(tmp_path, order, change):
    # 仅修改已产生抓包的副本，不能把注入文件说成真实线上流量。
    path = EVIDENCE / f"large-array-{order}" / "wire.pcap"
    assert path.is_file(), "缺少前置真实大数组验收抓包"
    port = 31000 + (order == "little")
    with path.open("rb") as source:
        reader = dpkt.pcap.Reader(source)
        link_type, frames = reader.datalink(), list(reader)
    first = None
    for _, raw in frames:
        packet = dpkt.ethernet.Ethernet(raw).data
        layer = packet.data
        if (
            layer.dport == port
            and len(layer.data) > 40
            and layer.data[:2] == b"\x34\x56"
        ):
            first = layer.seq
            break
    assert first is not None
    changed = []
    for timestamp, raw in frames:
        packet = dpkt.ethernet.Ethernet(raw).data
        layer = packet.data
        if layer.dport == port and layer.seq == first and layer.data:
            if change == "missing_segment":
                continue  # 同一段的重传也移除，不能靠重传恰好补齐。
            offset = 14 + packet.hl * 4 + layer.off * 4
            modified = bytearray(raw)
            modified[offset + {"length": 17, "payload": 21, "client": 8}[change]] ^= 1
            raw = bytes(modified)
        changed.append((timestamp, raw))
    copy = tmp_path / "tampered.pcap"
    with copy.open("wb") as output:
        writer = dpkt.pcap.Writer(
            output, linktype=link_type, snaplen=max(len(raw) for _, raw in changed)
        )
        for timestamp, raw in changed:
            writer.writepkt(raw, ts=timestamp)
    with pytest.raises(AssertionError):
        audit_large_array(copy, order, port, tmp_path)
