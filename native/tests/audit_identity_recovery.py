"""故障恢复专用短流黄金审计；按 TCP SYN 分代，不把跨进程会话回绕当成串线。"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
from collections import Counter
from pathlib import Path

import dpkt
from performance_metrics import kernel_capture_drops
from someip_agent.config import Settings
from someip_agent.pcap.importer import PcapImporter

logger = logging.getLogger(__name__)


def golden():
    expected = Counter()
    for index in range(8):
        transport, port = ("tcp" if index % 2 else "udp"), 30760 + index
        for client, before, after in (
            (0x7841, 0xE100, 0xE600),
            (0x7842, 0xE200, 0xE700),
        ):
            for value in (before + index, after + index):
                for kind in (0, 0x80):
                    expected[transport, port, client, 1, kind, f"{value:04x}"] += 1
            expected[transport, port, client, 2, 0, ""] += 1
    return expected


def audit(path: Path, evidence: Path, directory: Path):
    reports = {}
    for index in range(8):
        port = 30760 + index
        report = json.loads((evidence / f"identity-recovery-{port}.json").read_text())
        if (
            report["old_pid"] == report["new_pid"]
            or report["old_exit"] != (-9 if index >= 4 else -10)
            or report["new_exit"] != 0
            or report["restart_count"] != 1
            or report["client_ids"] != [0x7841, 0x7842]
            or report["business_replay"] is not False
            or report["stopped_member_restored"] is not False
        ):
            raise AssertionError("故障恢复进程证据不符合黄金契约")
        reports[port] = report
    independent, methods, requests, responses = (
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    streams = {}
    frames, frame_bytes, epochs = 0, 0, 0

    def record(transport, flow, client, session, method, kind, payload, timestamp=None):
        source, destination, sport, dport = flow
        port = next(port for port in (sport, dport) if 30760 <= port <= 30767)
        if kind == 2:
            if client != 0:
                raise AssertionError("恢复通知 Client ID 非零")
            return  # 字段周期次数不固定；方法与不重放按严格黄金计数核对。
        report = reports[port]
        server_address = (
            "10.77.0.1" if report["owned_role"] == "server" else "10.77.0.2"
        )
        client_address = "10.77.0.2" if server_address == "10.77.0.1" else "10.77.0.1"
        if (
            kind not in (0, 0x80)
            or client not in (0x7841, 0x7842)
            or session == 0
            or (source, destination)
            != (
                (client_address, server_address)
                if kind == 0
                else (server_address, client_address)
            )
            or (dport if kind == 0 else sport) != port
        ):
            raise AssertionError("恢复线上 Client ID、会话、消息类型或方向非法")
        if timestamp is not None:
            after = method == 1 and int(payload, 16) >= 0xE600
            if (timestamp > report["fault_at"]) != after:
                raise AssertionError("恢复黄金消息出现在错误的故障前后阶段")
        key = (
            transport,
            port,
            source,
            destination,
            client,
            session,
            method,
            kind,
            payload,
        )
        return key

    def observe(transport, flow, body, times):
        offset = 0
        while offset < len(body):
            if len(body) - offset < 16:
                raise AssertionError("恢复专用 TCP/UDP 黄金报文头不完整")
            service, method, length, client, session, protocol, version, kind, code = (
                struct.unpack_from(">HHIHHBBBB", body, offset)
            )
            end = offset + 8 + length
            if (
                service != 0x6790
                or protocol != 1
                or version != 1
                or code != 0
                or length < 8
                or end > len(body)
            ):
                raise AssertionError("恢复黄金报文服务、长度或版本非法")
            payload = bytes(body[offset + 16 : end]).hex()
            key = record(
                transport, flow, client, session, method, kind, payload, times[offset]
            )
            if key is not None:
                independent[key] += 1
                port = key[1]
                methods[transport, port, client, method, kind, payload] += 1
                if method == 1:
                    (requests if kind == 0 else responses)[
                        transport, port, client, session, method, payload
                    ] += 1
            offset = end

    def flush(flow, state):
        chunks = state[1]
        if not chunks:
            return
        chunks.sort(key=lambda item: item[0])
        start, body, times = chunks[0][0], bytearray(), []
        for sequence, data, timestamp in chunks:
            offset = sequence - start
            if offset > len(body):
                raise AssertionError("恢复短 TCP 流存在抓包缺口")
            overlap = min(len(data), len(body) - offset)
            if bytes(body[offset : offset + overlap]) != data[:overlap]:
                raise AssertionError("恢复短 TCP 流存在冲突重传")
            body.extend(data[overlap:])
            times.extend([timestamp] * (len(data) - overlap))
        observe("tcp", flow, body, times)

    with path.open("rb") as source:
        for timestamp, raw in dpkt.pcap.Reader(source):
            frames += 1
            frame_bytes += len(raw)
            packet = dpkt.ethernet.Ethernet(raw).data
            if not isinstance(packet, dpkt.ip.IP) or not isinstance(
                packet.data, (dpkt.udp.UDP, dpkt.tcp.TCP)
            ):
                continue
            layer = packet.data
            if not any(30760 <= port <= 30767 for port in (layer.sport, layer.dport)):
                continue
            flow = (
                socket.inet_ntoa(packet.src),
                socket.inet_ntoa(packet.dst),
                layer.sport,
                layer.dport,
            )
            if isinstance(layer, dpkt.udp.UDP):
                if layer.data:
                    observe("udp", flow, layer.data, [timestamp] * len(layer.data))
                continue
            if layer.flags & dpkt.tcp.TH_SYN:
                previous = streams.get(flow)
                if previous is None or previous[0] != layer.seq:
                    if previous is not None:
                        flush(flow, previous)
                    streams[flow] = (layer.seq, [])
                    epochs += 1
            if layer.data:
                if flow not in streams:
                    raise AssertionError("恢复 TCP 流缺少握手，不接受部分抓包")
                streams[flow][1].append(
                    (
                        layer.seq + bool(layer.flags & dpkt.tcp.TH_SYN),
                        bytes(layer.data),
                        timestamp,
                    )
                )
    for flow, state in streams.items():
        flush(flow, state)
    if methods != golden():
        raise AssertionError(
            f"恢复身份黄金字节/计数不一致: 缺失 {golden() - methods}, 多余 {methods - golden()}"
        )
    if requests != responses or any(count != 1 for count in requests.values()):
        raise AssertionError("恢复前后请求响应错配或业务重放")
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=directory / "runtime")
    ).parse(path.read_bytes(), path.name)
    if (
        result.runtime != "vsomeip"
        or result.packet_count != frames
        or result.captured_bytes != frame_bytes
    ):
        raise AssertionError("恢复 PCAP 原生来源或帧/字节总数不一致")
    native = Counter()
    for message in messages:
        if message.service_id != 0x6790:
            continue
        src, sport = message.source.rsplit(":", 1)
        dst, dport = message.destination.rsplit(":", 1)
        if not any(30760 <= int(port) <= 30767 for port in (sport, dport)):
            continue
        key = record(
            message.transport,
            (src, dst, int(sport), int(dport)),
            message.client_id,
            message.session_id,
            message.method_id,
            message.message_type,
            message.payload_hex,
        )
        if key is not None:
            native[key] += 1
    if native != independent:
        raise AssertionError("恢复独立抓包与原生离线解析身份消息不一致")
    return {
        "packet_count": frames,
        "captured_bytes": frame_bytes,
        "rpc_pairs": sum(requests.values()),
        "intentionally_unanswered_requests": 16,
        "recovery_cases": 8,
        "client_ids": [0x7841, 0x7842],
        "tcp_directional_epochs": epochs,
        "independent_and_native_equal": True,
        "business_replay": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcap", type=Path)
    parser.add_argument("--capture-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        drops = kernel_capture_drops(args.capture_log.read_text())
        if drops:
            raise AssertionError(f"故障恢复采集内核丢包 {drops}")
        result = audit(
            args.pcap,
            args.output.parent,
            args.output.parent / "identity-recovery-offline",
        )
        result["kernel_capture_drops"] = drops
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        logger.info("故障恢复双身份独立黄金审计通过: %s", result)
    except Exception:
        logger.exception("故障恢复双身份独立黄金审计失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
