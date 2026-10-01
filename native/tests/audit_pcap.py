"""被动复核虚拟网 PCAP 中的 SD 握手和确定的 payload 黄金字节。"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
from collections import Counter
from pathlib import Path

import dpkt
from audit_ipv4_options import OptionAudit
from performance_metrics import kernel_capture_drops
from someip_agent.protocol.sd import SdPayload
from someip_agent.protocol.someip import SomeIpDecodeError, decode_many

logger = logging.getLogger(__name__)


def audit(path: Path) -> dict:
    option_audit = OptionAudit()
    patterns: Counter[tuple[str, int, int, str]] = Counter()
    sd: Counter[str] = Counter()
    listener_packets: Counter[str] = Counter()
    capture_packets: Counter[str] = Counter()
    arxml_packets: Counter[tuple[str, str, int, int, str]] = Counter()
    wti_packets: Counter[tuple[str, int, int, int, str]] = Counter()
    recovery_packets: Counter[tuple[str, str, int, int, str]] = Counter()
    service_packets: Counter[tuple[str, str, str, int, int, int, str]] = Counter()
    composite_packets: Counter[tuple[str, int, int, int, str]] = Counter()
    fragment_packets: dict[tuple[int, int], list[tuple[int, bool, bytes]]] = {}
    fragment_times: dict[tuple[int, int], list[float]] = {}
    fragment_headers: dict[tuple[int, int], list[tuple[int, bytes]]] = {}
    listener_streams: dict[tuple[str, int, str, int], list[tuple[int, bytes]]] = {}
    incomplete_segments = 0
    with path.open("rb") as source:
        for _timestamp, data in dpkt.pcap.Reader(source):
            network = dpkt.ethernet.Ethernet(data).data
            if not isinstance(network, (dpkt.ip.IP, dpkt.ip6.IP6)):
                continue
            option_audit.observe(network, data)
            ipv6 = isinstance(network, dpkt.ip6.IP6)
            family = socket.AF_INET6 if ipv6 else socket.AF_INET
            src, dst = (
                socket.inet_ntop(family, network.src),
                socket.inet_ntop(family, network.dst),
            )
            if (
                not ipv6
                and (network.mf or network.offset)
                and src == "10.77.0.2"
                and dst == "10.77.0.1"
                and (
                    (
                        network.p == 17
                        and (0x7711 <= network.id <= 0x7717 or network.id == 0x77E0)
                    )
                    or (
                        network.p == 6
                        and (0x7721 <= network.id <= 0x7727 or network.id == 0x7730)
                    )
                )
            ):
                fragment_packets.setdefault((network.p, network.id), []).append(
                    (network.offset * 8, bool(network.mf), bytes(network.data))
                )
                fragment_times.setdefault((network.p, network.id), []).append(
                    _timestamp
                )
                fragment_headers.setdefault((network.p, network.id), []).append(
                    (network.hl * 4, network.opts)
                )
            if src not in {
                "10.77.0.1",
                "10.77.0.2",
                "fd77::1",
                "fd77::2",
            } or dst not in {
                "10.77.0.1",
                "10.77.0.2",
                "239.255.77.1",
                "239.255.77.2",
                "fd77::1",
                "fd77::2",
            }:
                continue
            transport = network.data
            if not isinstance(transport, (dpkt.udp.UDP, dpkt.tcp.TCP)):
                continue
            body = transport.data
            passive = transport.dport == 30608 or transport.sport == 30608
            if passive or (
                src in {"10.77.0.2", "fd77::2"} and transport.dport == 30607
            ):
                if isinstance(transport, dpkt.tcp.TCP) and body:
                    key = (src, transport.sport, dst, transport.dport)
                    listener_streams.setdefault(key, []).append(
                        (transport.seq, bytes(body))
                    )
                elif isinstance(transport, dpkt.udp.UDP):
                    for message in decode_many(body):
                        if (
                            message.header.service_id,
                            message.header.method_id,
                            message.payload.hex(),
                        ) == (0x4567, 0x8003, "41280000"):
                            if passive:
                                capture_packets[
                                    ("udp6" if ipv6 else "udp")
                                    + (
                                        "_request"
                                        if transport.dport == 30608
                                        else "_reply"
                                    )
                                ] += 1
                            else:
                                listener_packets[
                                    "multicast"
                                    if dst == "239.255.77.2"
                                    else "udp6"
                                    if ipv6
                                    else "udp"
                                ] += 1
            if (
                len(body) < 16
                or body[12] != 1
                or body[:2]
                not in {b"\x12\x34", b"\xff\xff", b"\x23\x45", b"\x23\x46", b"\x34\x56"}
            ):
                continue
            name = "udp" if isinstance(transport, dpkt.udp.UDP) else "tcp"
            try:
                for message in decode_many(body):
                    header = message.header
                    if (
                        header.service_id == 0x3456
                        and header.interface_version == 1
                        and header.return_code == 0
                    ):
                        port = next(
                            (
                                port
                                for port in (
                                    *range(30530, 30594),
                                    *range(30730, 30858),
                                    *range(30900, 30912),
                                )
                                if port in (transport.sport, transport.dport)
                            ),
                            None,
                        )
                        if port is not None and (
                            (
                                header.message_type == 0
                                and src == "10.77.0.2"
                                and header.client_id == 0x7722
                            )
                            or (
                                header.message_type == 0x80
                                and dst == "10.77.0.2"
                                and header.client_id == 0x7722
                            )
                            or (header.message_type == 2 and src == "10.77.0.1")
                        ):
                            composite_packets[
                                (
                                    name,
                                    port,
                                    header.method_id,
                                    header.message_type,
                                    message.payload.hex(),
                                )
                            ] += 1
                    service_port = next(
                        (
                            port
                            for port in (30520, 30521, 30522, 30523)
                            if port in (transport.sport, transport.dport)
                        ),
                        None,
                    )
                    if (
                        service_port is not None
                        and header.service_id == 0x1234
                        and header.interface_version == 1
                        and header.return_code == 0
                        and header.message_type in {0, 0x80, 2}
                    ):
                        # 方法由真实 ClientID 与方向共同定角色；事件由生产者方向定角色。
                        requester = src if header.message_type == 0 else dst
                        local_client = (
                            src == "10.77.0.2"
                            if header.message_type == 2
                            else header.client_id == 0x6631 and requester == "10.77.0.1"
                        )
                        local_server = (
                            src == "10.77.0.1"
                            if header.message_type == 2
                            else header.client_id == 0x6632 and requester == "10.77.0.2"
                        )
                        if local_client or local_server:
                            service_packets[
                                (
                                    name,
                                    "client" if local_client else "server",
                                    "little"
                                    if service_port in {30522, 30523}
                                    else "big",
                                    service_port,
                                    header.method_id,
                                    header.message_type,
                                    message.payload.hex(),
                                )
                            ] += 1
                    if (
                        header.service_id == 0x1234
                        and header.client_id == 0x5522
                        and header.interface_version == 1
                        and header.return_code == 0
                        and header.message_type in {0, 0x80}
                        and (
                            transport.sport in {30501, 30502}
                            or transport.dport in {30501, 30502}
                        )
                    ):
                        recovery_packets[
                            (
                                name,
                                "client"
                                if (src if header.message_type == 0 else dst)
                                == "10.77.0.1"
                                else "server",
                                header.method_id,
                                header.message_type,
                                message.payload.hex(),
                            )
                        ] += 1
                    if header.service_id in {0x2345, 0x2346}:
                        if (
                            header.interface_version == 1
                            and header.return_code == 0
                            and (
                                transport.sport in {30510, 30511}
                                or transport.dport in {30510, 30511}
                            )
                        ):
                            wti_packets[
                                (
                                    name,
                                    header.service_id,
                                    header.method_id,
                                    header.message_type,
                                    message.payload.hex(),
                                )
                            ] += 1
                        continue
                    patterns[
                        (
                            name,
                            header.method_id,
                            header.message_type,
                            message.payload.hex(),
                        )
                    ] += 1
                    if (
                        header.service_id == 0x1234
                        and header.interface_version == 1
                        and header.return_code == 0
                        and (
                            transport.sport in {30503, 30504, 30505, 30506}
                            or transport.dport in {30503, 30504, 30505, 30506}
                        )
                    ):
                        arxml_packets[
                            (
                                name,
                                "little"
                                if transport.sport in {30505, 30506}
                                or transport.dport in {30505, 30506}
                                else "big",
                                header.method_id,
                                header.message_type,
                                message.payload.hex(),
                            )
                        ] += 1
                    if header.service_id == 0xFFFF and header.method_id == 0x8100:
                        for entry in SdPayload.decode(message.payload).entries:
                            if entry.entry_type == 1:
                                sd[
                                    "OfferService" if entry.ttl else "StopOfferService"
                                ] += 1
                            elif entry.entry_type == 6:
                                sd[
                                    "SubscribeEventgroup"
                                    if entry.ttl
                                    else "StopSubscribeEventgroup"
                                ] += 1
                            elif entry.entry_type == 7:
                                sd[
                                    "SubscribeEventgroupAck"
                                    if entry.ttl
                                    else "SubscribeEventgroupNack"
                                ] += 1
            except SomeIpDecodeError:
                incomplete_segments += 1
                logger.debug(
                    "审计跳过非完整 TCP 段，不能据此统计总报文数", exc_info=True
                )
    # 此处只重组监听测试的单向、短 TCP 流；不把它泛化为产品级任意 TCP 重组器。
    for flow, chunks in listener_streams.items():
        chunks.sort(key=lambda item: item[0])
        start = chunks[0][0]
        stream = bytearray()
        for sequence, body in chunks:
            offset = sequence - start
            if offset > len(stream):
                raise AssertionError("监听器 TCP 黄金流抓包有缺口")
            overlap = min(len(body), len(stream) - offset)
            if bytes(stream[offset : offset + overlap]) != body[:overlap]:
                raise AssertionError("监听器 TCP 黄金流存在冲突重传")
            stream.extend(body[overlap:])
        for message in decode_many(stream):
            if (
                message.header.service_id,
                message.header.method_id,
                message.payload.hex(),
            ) == (0x4567, 0x8003, "41280000"):
                if flow[1] == 30608 or flow[3] == 30608:
                    capture_packets[
                        ("tcp6" if ":" in flow[0] else "tcp")
                        + ("_request" if flow[3] == 30608 else "_reply")
                    ] += 1
                else:
                    listener_packets["tcp6" if ":" in flow[0] else "tcp"] += 1
    for kind, minimum in (
        ("udp", 2),
        ("multicast", 2),
        ("tcp", 1),
        ("udp6", 2),
        ("tcp6", 1),
    ):
        if listener_packets[kind] < minimum:
            raise AssertionError("抓包缺少原生端口监听黄金报文: " + kind)
    checks = []
    for kind in ("udp", "tcp", "udp6", "tcp6"):
        for direction, minimum in (("request", 2), ("reply", 1)):
            if capture_packets[kind + "_" + direction] < minimum:
                raise AssertionError(
                    "抓包缺少被动捕获双向黄金报文: " + kind + "_" + direction
                )
    for transport in ("udp", "tcp"):
        for method, kind, payload in (
            (1, 0, "00f0"),
            (1, 0x80, "00f0"),
            (0x8001, 2, "00f0"),
            (0x8002, 2, "422a0000"),
            (0x8001, 2, "0141"),
            (0x8001, 2, "0142"),
            (0x8003, 2, "0029"),
            (5, 0x80, "0029"),
            (5, 0x81, ""),
            (5, 0x80, "01"),
        ):
            count = patterns[(transport, method, kind, payload)]
            if not count:
                raise AssertionError(
                    f"抓包缺少黄金报文: {transport} method={method:#x} type={kind:#x} payload={payload}"
                )
            checks.append(
                {
                    "transport": transport,
                    "method_id": method,
                    "message_type": kind,
                    "payload_hex": payload,
                    "observed_segments": count,
                }
            )
    if not patterns[("udp", 0x8002, 2, "41480000")]:
        raise AssertionError("抓包缺少默认后端原生发生器的 12.5 浮点事件")
    arxml_checks = []
    for transport, byte_order in (
        ("udp", "big"),
        ("tcp", "big"),
        ("udp", "little"),
        ("tcp", "little"),
    ):
        for method, kind, payload in (
            (1, 0, "0180" if byte_order == "big" else "8001"),
            (1, 0x80, "01"),
            (0x0102, 0, "07"),
            (0x0102, 0x80, "07"),
            (0x0101, 0, ""),
            (0x0101, 0x80, "07"),
            (0x8101, 2, "07"),
            (0x8001, 2, "422a0000" if byte_order == "big" else "00002a42"),
        ):
            count = arxml_packets[(transport, byte_order, method, kind, payload)]
            if not count:
                raise AssertionError(
                    f"抓包缺少 ARXML 原生目录黄金报文: {transport} {byte_order} {method:#x} {kind:#x} {payload}"
                )
            arxml_checks.append(
                {
                    "transport": transport,
                    "byte_order": byte_order,
                    "method_id": method,
                    "message_type": kind,
                    "payload_hex": payload,
                    "observed_segments": count,
                }
            )
    service_checks = []
    for transport, port, byte_order in (
        ("udp", 30520, "big"),
        ("tcp", 30521, "big"),
        ("udp", 30522, "little"),
        ("tcp", 30523, "little"),
    ):
        for role in ("client", "server"):
            for method, kind, payload in (
                (1, 0, "0180" if byte_order == "big" else "8001"),
                (1, 0x80, "01"),
                (0x0102, 0, "07"),
                (0x0102, 0x80, "07"),
                (0x0101, 0, ""),
                (0x0101, 0x80, "07"),
                (0x8001, 2, "41c40000" if byte_order == "big" else "0000c441"),
            ):
                count = service_packets[
                    (transport, role, byte_order, port, method, kind, payload)
                ]
                if not count:
                    raise AssertionError(
                        f"抓包缺少服务页面 API 黄金报文: {transport} {role} {byte_order} {method:#x} {kind:#x} {payload}"
                    )
                client_host = "10.77.0.1" if role == "client" else "10.77.0.2"
                server_host = "10.77.0.2" if role == "client" else "10.77.0.1"
                service_checks.append(
                    {
                        "transport": transport,
                        "local_role": role,
                        "byte_order": byte_order,
                        "service_port": port,
                        "service_id": 0x1234,
                        "method_id": method,
                        "message_type": kind,
                        "payload_hex": payload,
                        "source_host": client_host if kind == 0 else server_host,
                        "destination_host": server_host if kind == 0 else client_host,
                        **(
                            {"client_id": 0x6631 if role == "client" else 0x6632}
                            if kind != 2
                            else {}
                        ),
                        "observed_segments": count,
                    }
                )
    recovery_checks = []
    for transport in ("udp", "tcp"):
        if recovery_packets[(transport, "client", 1, 0, "007b")]:
            raise AssertionError(f"暂停中的在途请求被重放到真实网络: {transport} 007b")
        for owner in ("server", "client"):
            for payload in ("0005", "002a", "0039", "0063"):
                for kind in (0, 0x80):
                    count = recovery_packets[(transport, owner, 1, kind, payload)]
                    if not count:
                        raise AssertionError(
                            f"抓包缺少进程恢复前后真实请求/响应: {transport} {owner} ClientID=0x5522 {kind:#x} {payload}"
                        )
                    recovery_checks.append(
                        {
                            "transport": transport,
                            "recovered_role": owner,
                            "client_id": 0x5522,
                            "method_id": 1,
                            "message_type": kind,
                            "payload_hex": payload,
                            "observed_segments": count,
                        }
                    )
    wti_checks = []
    # 人工推导的 UTF-8/四字节长度前缀黄金字节；不调用被测 Codec 生成预期值。
    warning = "000000130000000a4c6f77566f6c746167650000000139"
    telltale = "0000000e000000054272616b650000000131"
    for transport in ("udp", "tcp"):
        for service in (0x2345, 0x2346):
            for method, kind, payload in (
                (1, 0, ""),
                (2, 0, ""),
                (1, 0x80, warning),
                (2, 0x80, telltale),
                (0x8001, 2, warning),
                (0x8002, 2, telltale),
            ):
                count = wti_packets[(transport, service, method, kind, payload)]
                if not count:
                    raise AssertionError(
                        f"抓包缺少 WTI 测试布局黄金报文: {transport} {service:#x} {method:#x} {kind:#x} {payload}"
                    )
                wti_checks.append(
                    {
                        "transport": transport,
                        "service_id": service,
                        "method_id": method,
                        "message_type": kind,
                        "payload_hex": payload,
                        "observed_segments": count,
                    }
                )
    for name in (
        "OfferService",
        "StopOfferService",
        "SubscribeEventgroup",
        "SubscribeEventgroupAck",
    ):
        if not sd[name]:
            raise AssertionError("抓包缺少 SD 状态转换: " + name)
    fragment_checks = []
    for protocol, base, transport, split in (
        (17, 0x7710, "udp", 16),
        (6, 0x7720, "tcp", 24),
    ):
        for index, profile in enumerate(
            (
                "ordered",
                "reordered",
                "duplicate",
                "overlap",
                "missing",
                "options",
                "header_conflict",
            ),
            1,
        ):
            pieces = fragment_packets.get((protocol, base + index), [])
            offsets = [piece[0] for piece in pieces]
            expected = {
                "ordered": [0, split],
                "reordered": [split, 0],
                "duplicate": [0, 0, split],
                "overlap": [0, 8],
                "missing": [0],
                "options": [0, split],
                "header_conflict": [0, 0, split],
            }[profile]
            if offsets != expected:
                raise AssertionError(
                    f"抓包分片序列不符: {transport} {profile} {offsets}"
                )
            outcome = (
                "incomplete"
                if profile == "missing"
                else "rejected_overlap"
                if profile == "overlap"
                else "rejected_header_conflict"
                if profile == "header_conflict"
                else "reassembled"
            )
            headers = fragment_headers.get((protocol, base + index), [])
            if profile == "options" and headers != [(24, b"\x01" * 4), (20, b"")]:
                raise AssertionError("抓包中的 IPv4 首片选项与人工黄金头长度不符")
            if profile == "header_conflict" and (
                headers != [(20, b""), (24, b"\x01" * 4), (20, b"")]
                or pieces[0] != pieces[1]
            ):
                raise AssertionError("抓包缺少同内容但头长度冲突的重复首片证据")
            if outcome == "reassembled":
                unique = {offset: (more, body) for offset, more, body in pieces}
                if profile == "duplicate" and pieces[0] != pieces[1]:
                    raise AssertionError("重复分片黄金数据不一致")
                body = b"".join(unique[offset][1] for offset in sorted(unique))
                segment = dpkt.udp.UDP(body) if protocol == 17 else dpkt.tcp.TCP(body)
                if (segment.sport, segment.dport) != (
                    41014,
                    30614,
                ) or segment.data.hex() != "456780030000000c001100170101020041280000":
                    raise AssertionError(
                        f"分片重组人工黄金向量不符: {transport} {profile}"
                    )
                pseudo = (
                    socket.inet_aton("10.77.0.2")
                    + socket.inet_aton("10.77.0.1")
                    + struct.pack("!BBH", 0, protocol, len(body))
                )
                if dpkt.in_cksum(pseudo + body):
                    raise AssertionError(f"分片传输层校验和错误: {transport} {profile}")
            fragment_checks.append(
                {
                    "transport": transport,
                    "profile": profile,
                    "ip_id": base + index,
                    "captured_fragments": len(pieces),
                    "expected_outcome": outcome,
                }
            )
    syn = fragment_packets.get((6, 0x7730), [])
    if [piece[0] for piece in syn] != [0, 24]:
        raise AssertionError("抓包缺少真实分片 TCP SYN 数据")
    syn_segment = dpkt.tcp.TCP(syn[0][2] + syn[1][2])
    if (
        syn_segment.flags != dpkt.tcp.TH_SYN
        or syn_segment.sport != 41015
        or syn_segment.data.hex() != "456780030000000c001100170101020041280000"
    ):
        raise AssertionError("真实分片 SYN 的标志或人工黄金字节错误")
    fragment_checks.append(
        {
            "transport": "tcp",
            "profile": "syn_payload",
            "ip_id": 0x7730,
            "captured_fragments": 2,
            "expected_outcome": "reassembled",
        }
    )
    idle = fragment_packets.get((17, 0x77E0), [])
    times = fragment_times.get((17, 0x77E0), [])
    if (
        [piece[0] for piece in idle] != [0, 0, 16]
        or len(times) != 3
        or times[1] - times[0] < 29.5
    ):
        raise AssertionError("抓包缺少无新流量过期后复用同一 IP 标识的实际分片证据")
    if idle[0] != idle[1]:
        raise AssertionError("过期后复用 IP 标识的首片与黄金向量不符")
    joined = dpkt.udp.UDP(idle[1][2] + idle[2][2])
    if joined.data.hex() != "456780030000000c001100170101020041280000":
        raise AssertionError("过期后重组的 UDP 黄金字节不符")
    fragment_checks.append(
        {
            "transport": "udp",
            "profile": "idle_expiry_and_id_reuse",
            "ip_id": 0x77E0,
            "captured_fragments": 3,
            "capture_gap_seconds": times[1] - times[0],
            "expected_outcome": "expired_then_reassembled",
        }
    )
    return {
        "status": "verified",
        "sd_entries": dict(sd),
        "golden_packets": checks,
        "backend_event_payload": "41480000",
        "native_listener_packets": dict(listener_packets),
        "native_passive_capture_packets": dict(capture_packets),
        "arxml_catalog_packets": arxml_checks,
        "sat_wti_packets": wti_checks,
        "sat_recovery_packets": recovery_checks,
        "paused_inflight_replay_absent": ["udp", "tcp"],
        "service_api_packets": service_checks,
        "arxml_composite_packets": composite_golden(composite_packets)
        + vsa_golden(composite_packets),
        "ipv4_fragment_packets": fragment_checks,
        "ipv4_option_packets": option_audit.verify(),
        "incomplete_segments": incomplete_segments,
        "scope": "完整可解析段及确定 IPv4 分片向量验证；不替代通用 IP/TCP 重组，也不是吞吐/丢包基准",
    }


def composite_payload(order, struct_width, array_width, tag=7, alignment=8):
    # 独立手工拼接，不导入产品 schema/Codec；整数常量与每一层字节长度分别推导。
    def prefix(size, width):
        return size.to_bytes(width, order) if width else b""

    words = bytes.fromhex("1234abcd" if order == "big" else "3412cdab")
    temperature = bytes.fromhex("fffe" if order == "big" else "feff")
    rows = (
        prefix(3, array_width)
        + b"\x01\x02\x03"
        + prefix(3, array_width)
        + b"\x04\x05\x06"
    )
    first = (
        bytes([tag])
        + prefix(4, array_width)
        + words
        + prefix(2, array_width)
        + b"\x01\x02"
    )
    # 仅变长 bytes 非末尾时补齐，计算基点包括 16 字节头及外层结构长度字段。
    pad = (-(16 + struct_width + len(first))) % (alignment // 8) if array_width else 0
    body = (
        first
        + b"\x00" * pad
        + prefix(6 + 2 * array_width, array_width)
        + rows
        + prefix(2, struct_width)
        + temperature
    )
    assert len(body) == 15 + 5 * array_width + struct_width + pad
    return (prefix(len(body), struct_width) + body).hex()


def composite_golden(packets):
    checks = []
    widths = (0, 1, 2, 4)
    profiles = [
        (order, width, array_width, 8)
        for order in ("big", "little")
        for width in widths
        for array_width in widths
    ] + [
        (order, width, width, alignment)
        for order in ("big", "little")
        for width in (1, 2)
        for alignment in (32, 64)
    ]
    for order, width, array_width, alignment in profiles:
        payload = composite_payload(order, width, array_width, alignment=alignment)
        changed = composite_payload(
            order, width, array_width, tag=8, alignment=alignment
        )
        for transport in ("udp", "tcp"):
            port = (
                (30530 if alignment == 8 else 30730 + 64 * (alignment == 64))
                + (transport == "tcp")
                + 2 * (order == "little")
                + 4 * widths.index(width)
                + 16 * widths.index(array_width)
            )
            for (
                observed_transport,
                observed_port,
                method,
                kind,
                actual,
            ), count in packets.items():
                if (
                    count
                    and (observed_transport, observed_port, method, kind)
                    == (transport, port, 1, 0)
                    and actual != payload
                ):
                    raise AssertionError(
                        f"非法复合 ARXML 请求出现在实际报文: {transport} {port} {actual}"
                    )
            scalar = "1234" if order == "big" else "3412"
            for method, kind, expected, required in (
                (1, 0, payload, 1),
                (1, 0x80, payload, 1),
                (2, 0, scalar, 1),
                (2, 0x80, scalar, 1),
                (0x8001, 2, payload, 1),
                (0x0101, 0, "", 2),
                (0x0101, 0x80, payload, 1),
                (0x0101, 0x80, changed, 1),
                (0x0102, 0, changed, 1),
                (0x0102, 0x80, changed, 1),
                (0x8101, 2, payload, 1),
                (0x8101, 2, changed, 1),
            ):
                count = packets[(transport, port, method, kind, expected)]
                if count < required:
                    raise AssertionError(
                        f"复合 ARXML 黄金报文缺失: {transport} {order} {width}/{array_width} {method:#x} {kind:#x} {expected}"
                    )
                checks.append(
                    {
                        "transport": transport,
                        "service_port": port,
                        "service_id": 0x3456,
                        "method_id": method,
                        "message_type": kind,
                        "payload_hex": expected,
                        "byte_order": order,
                        "alignment_bits": alignment,
                        "struct_length_bytes": width,
                        "array_length_bytes": array_width,
                        "array_semantics": "fixed"
                        if array_width == 0
                        else "variable_bounded",
                        "source_host": "10.77.0.2" if kind == 0 else "10.77.0.1",
                        "destination_host": "10.77.0.1" if kind == 0 else "10.77.0.2",
                        **({"client_id": 0x7722} if kind != 2 else {}),
                        "observed_segments": count,
                        "required_messages": required,
                    }
                )
    return checks


def vsa_payload(order, width, tag=7, words=(0x1234, 0xABCD)):
    # 与产品类型图/Codec 独立；indicator 只作字典元素数量，不写入 wire。
    body = bytes([tag]) + (2 * len(words)).to_bytes(width, order)
    body += b"".join(word.to_bytes(2, order) for word in words)
    body += b"\0" * (-(16 + len(body)) % 8)
    body += (2).to_bytes(width, order) + b"\x01\x02"
    body += b"\0" * (-(16 + len(body)) % 8)
    body += (6 + 2 * width).to_bytes(width, order)
    body += (3).to_bytes(width, order) + b"\x01\x02\x03"
    body += (3).to_bytes(width, order) + b"\x04\x05\x06"
    body += (-2).to_bytes(2, order, signed=True)
    return body.hex()


def vsa_golden(packets):
    checks = []
    for order in ("big", "little"):
        for width in (1, 2, 4):
            payload = vsa_payload(order, width)
            changed = vsa_payload(order, width, tag=8)
            variants = [
                vsa_payload(order, width, words=words) for words in ((), (1, 2, 3))
            ]
            for transport in ("udp", "tcp"):
                port = (
                    30900
                    + (transport == "tcp")
                    + 2 * (order == "little")
                    + 4 * (1, 2, 4).index(width)
                )
                allowed = {payload, *variants}
                for (
                    observed_transport,
                    observed_port,
                    method,
                    kind,
                    actual,
                ), count in packets.items():
                    if (
                        count
                        and (observed_transport, observed_port, method, kind)
                        == (transport, port, 1, 0)
                        and actual not in allowed
                    ):
                        raise AssertionError(
                            f"非法 VSA 请求出现在报文: {transport} {port} {actual}"
                        )
                scalar = (0x1234).to_bytes(2, order).hex()
                required = [
                    (1, 0, payload, 1),
                    (1, 0x80, payload, 1),
                    (2, 0, scalar, 1),
                    (2, 0x80, scalar, 1),
                    (0x8001, 2, payload, 1),
                    (0x0101, 0, "", 2),
                    (0x0101, 0x80, payload, 1),
                    (0x0101, 0x80, changed, 1),
                    (0x0102, 0, changed, 1),
                    (0x0102, 0x80, changed, 1),
                    (0x8101, 2, payload, 1),
                    (0x8101, 2, changed, 1),
                    *[
                        (1, kind, variant, 1)
                        for variant in variants
                        for kind in (0, 0x80)
                    ],
                ]
                for method, kind, expected, minimum in required:
                    count = packets[(transport, port, method, kind, expected)]
                    if count < minimum:
                        raise AssertionError(
                            f"VSA 黄金报文缺失: {transport} {port} {method:#x} {kind:#x} {expected}"
                        )
                    checks.append(
                        {
                            "transport": transport,
                            "service_port": port,
                            "service_id": 0x3456,
                            "method_id": method,
                            "message_type": kind,
                            "payload_hex": expected,
                            "byte_order": order,
                            "alignment_bits": 64,
                            "struct_length_bytes": 0,
                            "array_length_bytes": width,
                            "array_semantics": "VSA_LINEAR",
                            "source_host": "10.77.0.2" if kind == 0 else "10.77.0.1",
                            "destination_host": "10.77.0.1"
                            if kind == 0
                            else "10.77.0.2",
                            **({"client_id": 0x7722} if kind != 2 else {}),
                            "observed_segments": count,
                            "required_messages": minimum,
                        }
                    )
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="虚拟以太网黄金报文审计")
    parser.add_argument("pcap", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-log", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        drops = None
        if args.capture_log is not None:
            drops = kernel_capture_drops(args.capture_log.read_text())
            if drops:
                raise AssertionError(f"采集内核丢了 {drops} 帧，不能声明抓包完整")
        result = audit(args.pcap)
        if drops is not None:
            result["kernel_capture_drops"] = drops
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("虚拟网抓包审计通过，SD 与 UDP/TCP 黄金字节均有真实线上证据")
    except Exception:
        logger.exception("虚拟网抓包审计失败")
        raise


if __name__ == "__main__":
    main()
