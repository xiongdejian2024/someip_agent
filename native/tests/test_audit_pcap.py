"""以实际 veth 文件验证审计边界；注入副本不冒充真实线上证据。"""

from __future__ import annotations

import os
import socket
from collections import Counter, defaultdict
from pathlib import Path

import dpkt
import pytest
from audit_applications import audit as audit_applications
from audit_offline import audit as audit_native
from audit_pcap import audit, composite_golden, composite_payload


@pytest.fixture(scope="module")
def captured():
    path = Path(
        os.environ.get("SOMEIP_AGENT_AUDIT_PCAP", "build/virtual-evidence/soa.pcap")
    )
    # 正式入口先产生真实抓包；缺少证据必须失败，不跳过或用模拟文件代替。
    assert path.is_file(), f"缺少真实虚拟网 PCAP：{path}"
    with path.open("rb") as source:
        reader = dpkt.pcap.Reader(source)
        frames = list(reader)
        link_type = reader.datalink()
    assert frames
    reference = audit(path)
    return frames, link_type, reference


def write_copy(path, frames, link_type, packet):
    with path.open("wb") as output:
        # veth/offload 的真实记录可能大于 MTU；Writer 默认 1500 不能承载原始帧。
        writer = dpkt.pcap.Writer(
            output,
            linktype=link_type,
            snaplen=max(len(packet), *(len(f) for _, f in frames)),
        )
        # 插在最前且采用相同文件时间，不制造离线时钟倒退。
        writer.writepkt(packet, ts=frames[0][0])
        for timestamp, frame in frames:
            writer.writepkt(frame, ts=timestamp)


@pytest.mark.parametrize(
    "change", ["client", "session", "payload", "missing", "duplicate"]
)
def test_application_audit_rejects_tampered_real_messages(tmp_path, captured, change):
    frames, link_type, _ = captured
    changed, modified = [], False
    for timestamp, frame in frames:
        network = dpkt.ethernet.Ethernet(frame).data
        if (
            not modified
            and isinstance(network, dpkt.ip.IP)
            and isinstance(network.data, dpkt.udp.UDP)
        ):
            layer = network.data
            if layer.dport == 30740 and len(layer.data) == 18 and layer.data[14] == 0:
                modified = True
                if change == "missing":
                    continue
                if change == "duplicate":
                    changed.append((timestamp, frame))
                else:
                    raw = bytearray(frame)
                    offset = 14 + network.hl * 4 + 8
                    raw[
                        offset + {"client": 9, "session": 11, "payload": 17}[change]
                    ] ^= 1
                    frame = bytes(raw)
        changed.append((timestamp, frame))
    assert modified, "缺少真实两身份请求，不能用空测试验收审计器"
    path = tmp_path / (change + ".pcap")
    with path.open("wb") as output:
        writer = dpkt.pcap.Writer(
            output, linktype=link_type, snaplen=max(len(frame) for _, frame in changed)
        )
        for timestamp, frame in changed:
            writer.writepkt(frame, ts=timestamp)
    with pytest.raises(AssertionError, match="Client ID|黄金|会话"):
        audit_applications(path, tmp_path)


def collision_packet(kind):
    source, destination = "10.77.0.2", "10.77.0.1"
    protocol, identifier = (17, 0x7711) if kind == "ordinary_udp" else (6, 0x7721)
    if kind == "ordinary_udp":
        body = dpkt.udp.UDP(sport=42000, dport=42001, ulen=8)
    elif kind == "ordinary_tcp":
        body = dpkt.tcp.TCP(sport=42000, dport=42001, flags=dpkt.tcp.TH_ACK)
    else:
        # 与首片相同的协议/ID/片偏移，但地址或方向不同，属于独立数据报。
        body = b"\x00" * 24
        if kind == "foreign_source":
            source = "10.77.0.3"
        else:
            source, destination = destination, source
    packet = dpkt.ip.IP(
        src=socket.inet_aton(source),
        dst=socket.inet_aton(destination),
        p=protocol,
        id=identifier,
        data=body,
    )
    packet.mf = kind in {"foreign_source", "reverse_direction"}
    packet.len = len(packet)
    return bytes(
        dpkt.ethernet.Ethernet(
            src=b"\x02\x00\x00\x00\x00\x02",
            dst=b"\x02\x00\x00\x00\x00\x01",
            type=dpkt.ethernet.ETH_TYPE_IP,
            data=packet,
        )
    )


@pytest.mark.parametrize(
    "kind", ["ordinary_tcp", "ordinary_udp", "foreign_source", "reverse_direction"]
)
def test_unrelated_id_collision_does_not_change_fragment_evidence(
    tmp_path, captured, kind
):
    frames, link_type, reference = captured
    path = tmp_path / (kind + ".pcap")
    write_copy(path, frames, link_type, collision_packet(kind))
    checked = audit(path)
    assert checked["ipv4_fragment_packets"] == reference["ipv4_fragment_packets"]
    # 原生导入审计必须采用相同的实际分片/方向边界，而非从其他流借帧数。
    result = audit_native(path, checked, tmp_path)
    assert len(result["golden_messages"]) == 932
    assert len(result["fragment_imports"]) == 16
    assert len(result["ipv4_option_imports"]) == 40
    assert result["statistics"]["packet_count"] == len(frames) + 1


def test_extra_real_fragment_still_fails_strict_sequence(tmp_path, captured):
    frames, link_type, reference = captured
    extra = next(
        frame
        for _timestamp, frame in frames
        if isinstance((packet := dpkt.ethernet.Ethernet(frame).data), dpkt.ip.IP)
        and packet.p == 6
        and packet.id == 0x7721
        and packet.src == socket.inet_aton("10.77.0.2")
        and packet.dst == socket.inet_aton("10.77.0.1")
        and packet.mf
        and not packet.offset
    )
    path = tmp_path / "extra-real-fragment.pcap"
    write_copy(path, frames, link_type, extra)
    with pytest.raises(AssertionError, match=r"tcp ordered \[0, 0, 24\]"):
        audit(path)
    with pytest.raises(AssertionError, match="分片审计原始帧数量不一致"):
        audit_native(path, reference, tmp_path)


def test_complete_packet_cannot_replace_missing_real_fragment(tmp_path, captured):
    frames, link_type, reference = captured
    retained = []
    for timestamp, frame in frames:
        packet = dpkt.ethernet.Ethernet(frame).data
        if (
            isinstance(packet, dpkt.ip.IP)
            and packet.p == 6
            and packet.id == 0x7721
            and packet.src == socket.inet_aton("10.77.0.2")
            and packet.dst == socket.inet_aton("10.77.0.1")
            and packet.offset == 3
        ):
            continue
        retained.append((timestamp, frame))
    assert len(retained) == len(frames) - 1
    path = tmp_path / "missing-fragment-with-ordinary-packet.pcap"
    write_copy(path, retained, link_type, collision_packet("ordinary_tcp"))
    with pytest.raises(AssertionError, match=r"tcp ordered \[0\]"):
        audit(path)
    with pytest.raises(AssertionError, match="分片审计原始帧数量不一致"):
        audit_native(path, reference, tmp_path)


@pytest.mark.parametrize(
    "change", ["option", "checksum", "payload", "missing", "duplicate", "truncated"]
)
def test_ipv4_options_independent_audit_rejects_corrupted_real_copy(
    tmp_path, captured, change
):
    frames, link_type, reference = captured
    changed = []
    found = False
    for timestamp, frame in frames:
        packet = dpkt.ethernet.Ethernet(frame).data
        if (
            not found
            and isinstance(packet, dpkt.ip.IP)
            and packet.src == socket.inet_aton("10.77.0.2")
            and packet.dst == socket.inet_aton("10.77.0.1")
            and packet.p == 17
            and packet.id == 0x7912
        ):
            found = True
            if change == "missing":
                continue
            if change == "duplicate":
                changed.append((timestamp, frame))
            elif change == "truncated":
                frame = frame[:-1]
            elif change == "checksum":
                raw = bytearray(frame)
                raw[24] ^= 1
                frame = bytes(raw)
            else:
                raw = bytearray(frame)
                raw[34 if change == "option" else -1] ^= 1
                # option 变更重算头和，避免只靠 checksum 断言掩盖黄金字节缺失。
                raw[24:26] = b"\x00\x00"
                raw[24:26] = dpkt.in_cksum(raw[14:38]).to_bytes(2, "big")
                frame = bytes(raw)
        changed.append((timestamp, frame))
    assert found
    path = tmp_path / ("options-" + change + ".pcap")
    with path.open("wb") as output:
        writer = dpkt.pcap.Writer(
            output, linktype=link_type, snaplen=max(len(f) for _, f in changed)
        )
        for timestamp, frame in changed:
            writer.writepkt(frame, ts=timestamp)
    with pytest.raises(AssertionError, match="IPv4 选项"):
        audit(path)
    if change in {"missing", "duplicate"}:
        with pytest.raises(AssertionError, match="选项审计原始帧数量不一致"):
            audit_native(path, reference, tmp_path)


def test_ipv4_options_id_collision_on_foreign_ports_is_not_borrowed(tmp_path, captured):
    frames, link_type, reference = captured
    packet = next(
        dpkt.ethernet.Ethernet(frame)
        for _, frame in frames
        if isinstance((network := dpkt.ethernet.Ethernet(frame).data), dpkt.ip.IP)
        and network.p == 17
        and network.id == 0x7912
        and isinstance(network.data, dpkt.udp.UDP)
        and network.data.dport == 30618
    )
    packet.data.data.sport, packet.data.data.dport = 42000, 42001
    packet.data.sum = packet.data.data.sum = 0
    path = tmp_path / "options-foreign-port-id-collision.pcap"
    write_copy(path, frames, link_type, bytes(packet))
    checked = audit(path)
    assert checked["ipv4_option_packets"] == reference["ipv4_option_packets"]
    result = audit_native(path, checked, tmp_path)
    assert len(result["ipv4_option_imports"]) == 40
    assert result["statistics"]["packet_count"] == len(frames) + 1


def test_paused_inflight_business_replay_is_rejected(tmp_path, captured):
    frames, link_type, _reference = captured
    for _timestamp, frame in frames:
        packet = dpkt.ethernet.Ethernet(frame)
        network = packet.data
        if not isinstance(network, dpkt.ip.IP) or network.src != socket.inet_aton(
            "10.77.0.1"
        ):
            continue
        transport = network.data
        if not isinstance(transport, dpkt.udp.UDP) or transport.dport != 30501:
            continue
        body = transport.data
        # 手工 SOME/IP 头：服务 1234、方法 1、ClientID 5522、REQUEST、长度为两字节。
        if (
            len(body) != 18
            or body[:4] != bytes.fromhex("12340001")
            or body[8:10] != bytes.fromhex("5522")
            or body[14] != 0
            or body[16:] != bytes.fromhex("0039")
        ):
            continue
        transport.data = body[:16] + bytes.fromhex("007b")
        transport.sum = network.sum = 0
        replay = bytes(packet)
        break
    else:
        pytest.fail("真实抓包缺少 owned client 的 UDP 恢复前请求")
    path = tmp_path / "inflight-business-replay.pcap"
    write_copy(path, frames, link_type, replay)
    with pytest.raises(AssertionError, match="在途请求被重放"):
        audit(path)


def test_composite_manual_golden_anchors_and_all_field_width_profiles():
    assert composite_payload("big", 0, 0) == "071234abcd0102010203040506fffe"
    assert (
        composite_payload("big", 2, 2)
        == "001b0700041234abcd00020102000a000301020300030405060002fffe"
    )
    assert (
        composite_payload("little", 2, 2)
        == "1b000704003412cdab020001020a00030001020303000405060200feff"
    )
    vectors = composite_golden(defaultdict(lambda: 2))
    assert len(vectors) == 768
    assert len({(v["transport"], v["service_port"]) for v in vectors}) == 64
    assert {v["struct_length_bytes"] for v in vectors} == {0, 1, 2, 4}
    assert {v["array_length_bytes"] for v in vectors} == {0, 1, 2, 4}
    assert all(
        v["required_messages"]
        == (2 if (v["method_id"], v["message_type"]) == (0x0101, 0) else 1)
        for v in vectors
    )


def composite_counters():
    return Counter(
        {
            (
                v["transport"],
                v["service_port"],
                v["method_id"],
                v["message_type"],
                v["payload_hex"],
            ): 2
            for v in composite_golden(defaultdict(lambda: 2))
        }
    )


def test_composite_audit_requires_both_getter_calls_and_changed_field_notification():
    packets = composite_counters()
    packets[("udp", 30530, 0x0101, 0, "")] = 1
    with pytest.raises(AssertionError, match="黄金报文缺失"):
        composite_golden(packets)
    packets[("udp", 30530, 0x0101, 0, "")] = 2
    packets[("tcp", 30593, 0x8101, 2, composite_payload("little", 4, 4, tag=8))] = 0
    with pytest.raises(AssertionError, match="黄金报文缺失"):
        composite_golden(packets)


def test_composite_audit_rejects_invalid_request_not_just_callback_count():
    packets = composite_counters()
    packets[("udp", 30530, 1, 0, composite_payload("big", 0, 0, tag=99))] = 1
    with pytest.raises(AssertionError, match="非法复合 ARXML 请求"):
        composite_golden(packets)
