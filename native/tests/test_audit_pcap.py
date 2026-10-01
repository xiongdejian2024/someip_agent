"""以实际 veth 文件验证审计边界；注入副本不冒充真实线上证据。"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import dpkt
import pytest
from audit_offline import audit as audit_native
from audit_pcap import audit


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
    assert len(result["golden_messages"]) == 164
    assert len(result["fragment_imports"]) == 16
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
