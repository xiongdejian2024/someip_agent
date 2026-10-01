"""独立两身份黄金审计；不导入发送 fixture，UDP/TCP 均与产品原生导入交叉核对。"""

from __future__ import annotations

import argparse
import json
import logging
import socket
from collections import Counter, defaultdict
from pathlib import Path

import dpkt
from performance_metrics import kernel_capture_drops
from someip_agent.config import Settings
from someip_agent.pcap.importer import PcapImporter
from someip_agent.protocol.someip import decode_many

logger = logging.getLogger(__name__)


def golden():
    expected = Counter()
    for transport, delta in (("udp", 0), ("tcp", 1)):

        def pair(
            port,
            client,
            method,
            payload,
            response=None,
            *,
            transport=transport,
            delta=delta,
        ):
            expected[transport, port + delta, client, method, 0, payload] += 1
            expected[
                transport,
                port + delta,
                client,
                method,
                0x80,
                response if response is not None else payload,
            ] += 1

        for client, base in ((0x7741, 0xA100), (0x7742, 0xB100)):
            for index in range(24):
                pair(30740, client, 1, f"{base + index:04x}")
        for client, value in (
            (0x7741, 0xA200),
            (0x7742, 0xB200),
            (0x7742, 0xB201),
            (0x7741, 0xA201),
        ):
            pair(30742, client, 1, f"{value:04x}")
        pair(30746, 0x7741, 1, "a300")
        pair(30746, 0x7742, 1, "b300")
        pair(30748, 0x7741, 2, "", "d400")
        pair(30748, 0x7741, 2, "", "d401")
        pair(30748, 0x7742, 1, "b400")
    return expected


def identity(
    transport,
    source,
    destination,
    sport,
    dport,
    client,
    session,
    method,
    kind,
    payload,
    version,
    code,
):
    port = dport if source == "10.77.0.2" else sport
    if not 30740 <= port <= 30749:
        raise AssertionError("独立 application 报文使用非预期服务端口或方向")
    if version != 1 or code != 0:
        raise AssertionError("独立 application 报文版本或返回码非法")
    if kind in (0, 0x80):
        if client not in (0x7741, 0x7742) or session == 0:
            raise AssertionError("线上 Client ID 未隔离或会话为零")
        if (source, destination) != (
            ("10.77.0.2", "10.77.0.1") if kind == 0 else ("10.77.0.1", "10.77.0.2")
        ):
            raise AssertionError("请求响应方向错误")
    elif (
        kind != 2 or client != 0 or (source, destination) != ("10.77.0.1", "10.77.0.2")
    ):
        raise AssertionError("独立 application 非预期消息类型或通知方向")
    return transport, port, source, destination, client, session, method, kind, payload


def audit(path: Path, directory: Path):
    independent = Counter()
    streams = defaultdict(list)
    frames, frame_bytes = 0, 0

    def observe(transport, flow, body):
        for message in decode_many(body):
            header = message.header
            if header.service_id != 0x6789:
                raise AssertionError("专用 application 端口出现非预期服务")
            independent[
                identity(
                    transport,
                    *flow,
                    header.client_id,
                    header.session_id,
                    header.method_id,
                    header.message_type,
                    message.payload.hex(),
                    header.interface_version,
                    header.return_code,
                )
            ] += 1

    with path.open("rb") as source:
        for _, raw in dpkt.pcap.Reader(source):
            frames += 1
            frame_bytes += len(raw)
            packet = dpkt.ethernet.Ethernet(raw).data
            if not isinstance(packet, dpkt.ip.IP) or not isinstance(
                packet.data, (dpkt.udp.UDP, dpkt.tcp.TCP)
            ):
                continue
            layer = packet.data
            if (
                not any(30740 <= port <= 30749 for port in (layer.sport, layer.dport))
                or not layer.data
            ):
                continue
            flow = (
                socket.inet_ntoa(packet.src),
                socket.inet_ntoa(packet.dst),
                layer.sport,
                layer.dport,
            )
            if isinstance(layer, dpkt.udp.UDP):
                observe("udp", flow, layer.data)
            else:
                streams[flow].append((layer.seq, bytes(layer.data)))
    # 复用既有黄金审计的短单向流算法；仅供专用测试流，不作为产品 TCP 回退。
    for flow, chunks in streams.items():
        chunks.sort(key=lambda item: item[0])
        start, stream = chunks[0][0], bytearray()
        for sequence, body in chunks:
            offset = sequence - start
            if offset > len(stream):
                raise AssertionError("独立 application TCP 黄金流抓包有缺口")
            overlap = min(len(body), len(stream) - offset)
            if bytes(stream[offset : offset + overlap]) != body[:overlap]:
                raise AssertionError("独立 application TCP 黄金流存在冲突重传")
            stream.extend(body[overlap:])
        observe("tcp", flow, stream)
    methods = Counter()
    requests, responses = Counter(), Counter()
    for key, count in independent.items():
        transport, port, _, _, client, session, method, kind, payload = key
        if kind == 2:
            continue
        methods[transport, port, client, method, kind, payload] += count
        (requests if kind == 0 else responses)[
            transport, port, client, session, method
        ] += count
    if methods != golden():
        raise AssertionError(
            f"两身份黄金字节/计数不一致: 缺失 {golden() - methods}, 多余 {methods - golden()}"
        )
    if requests != responses or any(count != 1 for count in requests.values()):
        raise AssertionError("两身份请求响应会话没有一一对应或存在 Request ID 重复")
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=directory / "runtime")
    ).parse(path.read_bytes(), path.name)
    if (
        result.runtime != "vsomeip"
        or result.packet_count != frames
        or result.captured_bytes != frame_bytes
    ):
        raise AssertionError("原生导入来源、原始帧数或字节数不一致")
    native = Counter()
    for message in messages:
        if message.service_id != 0x6789:
            continue
        src, sport = message.source.rsplit(":", 1)
        dst, dport = message.destination.rsplit(":", 1)
        if not any(30740 <= int(port) <= 30749 for port in (sport, dport)):
            continue
        native[
            identity(
                message.transport,
                src,
                dst,
                int(sport),
                int(dport),
                message.client_id,
                message.session_id,
                message.method_id,
                message.message_type,
                message.payload_hex,
                message.interface_version,
                message.return_code,
            )
        ] += 1
    if native != independent:
        raise AssertionError("独立 UDP/TCP 审计与原生 PCAP 导入的两身份消息不一致")
    return {
        "packet_count": frames,
        "captured_bytes": frame_bytes,
        "rpc_messages": sum(methods.values()),
        "rpc_pairs": sum(requests.values()),
        "all_application_messages": sum(independent.values()),
        "client_ids": [0x7741, 0x7742],
        "independent_and_native_equal": True,
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
            raise AssertionError(f"采集内核丢包 {drops}，不能证明身份消息完整")
        result = audit(args.pcap, args.output.parent / "application-offline")
        result["kernel_capture_drops"] = drops
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        logger.info("两身份独立黄金字节与原生导入验收通过: %s", result)
    except Exception:
        logger.exception("两身份抓包验收失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
