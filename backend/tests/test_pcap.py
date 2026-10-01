import io
import socket

import dpkt
import pytest

from someip_agent.agent.evidence import EvidenceTools
from someip_agent.config import Settings
from someip_agent.pcap.importer import PcapImporter
from someip_agent.protocol.sd import SdEntry, SdPayload
from someip_agent.protocol.someip import SomeIpMessage


def _ethernet_ipv4(
    source: str,
    destination: str,
    transport: dpkt.udp.UDP | dpkt.tcp.TCP,
) -> bytes:
    protocol = dpkt.ip.IP_PROTO_UDP if isinstance(transport, dpkt.udp.UDP) else dpkt.ip.IP_PROTO_TCP
    ip = dpkt.ip.IP(
        src=socket.inet_aton(source),
        dst=socket.inet_aton(destination),
        p=protocol,
        data=transport,
    )
    ip.len = len(ip)
    return bytes(
        dpkt.ethernet.Ethernet(
            src=b"\x00\x11\x22\x33\x44\x55",
            dst=b"\x01\x00\x5e\x40\xff\xfb",
            type=dpkt.ethernet.ETH_TYPE_IP,
            data=ip,
        )
    )


def _pcap_with_sd_offer(sd_payload: bytes | None = None) -> bytes:
    entry = SdEntry(
        entry_type=0x01,
        index_first_option=0,
        index_second_option=0,
        number_first_options=0,
        number_second_options=0,
        service_id=0x1234,
        instance_id=1,
        major_version=1,
        ttl=3,
        minor_version=0,
    )
    payload = (
        SdPayload(entries=(entry,)).to_someip().encode()
        if sd_payload is None
        else SomeIpMessage.build(
            service_id=0xFFFF, method_id=0x8100, message_type=2, payload=sd_payload
        ).encode()
    )
    udp = dpkt.udp.UDP(sport=30490, dport=30490, data=payload)
    udp.ulen = len(udp)
    output = io.BytesIO()
    writer = dpkt.pcap.Writer(output)
    writer.writepkt(
        _ethernet_ipv4("192.168.1.10", "239.192.255.251", udp),
        ts=1_700_000_000.25,
    )
    return output.getvalue()


def _pcap_with_mixed_traffic() -> bytes:
    sd_capture = _pcap_with_sd_offer()
    reader = dpkt.pcap.Reader(io.BytesIO(sd_capture))
    first_timestamp, first_frame = next(iter(reader))

    application = SomeIpMessage.build(
        service_id=0x1234,
        method_id=0x8001,
        payload=b"\x01\x02",
        message_type=0x02,
    ).encode()
    tcp = dpkt.tcp.TCP(sport=30501, dport=30502, data=application)
    tcp.off = 5

    unrelated = dpkt.udp.UDP(sport=53, dport=51000, data=b"not-someip")
    unrelated.ulen = len(unrelated)

    output = io.BytesIO()
    writer = dpkt.pcap.Writer(output)
    writer.writepkt(first_frame, ts=first_timestamp)
    writer.writepkt(
        _ethernet_ipv4("192.168.1.10", "192.168.1.11", tcp),
        ts=first_timestamp + 1.5,
    )
    writer.writepkt(
        _ethernet_ipv4("192.168.1.11", "192.168.1.10", unrelated),
        ts=first_timestamp + 3.5,
    )
    return output.getvalue()


def test_imports_someip_sd_from_pcap(tmp_path, native_runtime, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("生产路径不得调用 Python SD 解码")

    monkeypatch.setattr(SdPayload, "decode", forbidden)
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    ).parse(_pcap_with_sd_offer(), "offer.pcap")
    assert result.packet_count == 1
    assert result.someip_count == 1
    assert result.sd_count == 1
    assert result.duration_seconds == 0
    assert result.start_time == result.end_time
    assert result.transport_counts == {"udp": 1, "tcp": 0, "other": 0}
    assert result.protocol_counts == {
        "someip": 0,
        "someip_sd": 1,
        "someip_mixed": 0,
        "other": 0,
    }
    assert result.sd_entry_counts["OfferService"] == 1
    assert messages[0].service_id == 0xFFFF
    assert messages[0].is_sd
    assert "OfferService" in (messages[0].sd_summary or "")
    assert messages[0].metadata["sd"]["decoder"] == "vsomeip-3.5.10"
    assert messages[0].metadata["sd"]["entries"][0]["service_id"] == 0x1234


@pytest.mark.parametrize(
    "payload",
    [
        bytes.fromhex("0000000000000000"),
        bytes.fromhex("00000000000000000000000000"),
        bytes.fromhex("c0000000000000100100001012340001010000030000000200000000"),
    ],
)
def test_malformed_native_sd_keeps_raw_capture_without_fake_offer(
    tmp_path, native_runtime, payload
):
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    ).parse(_pcap_with_sd_offer(payload), "bad-sd.pcap")
    assert result.someip_count == result.sd_count == 1
    assert not any(result.sd_entry_counts.values())
    assert all(not endpoint.offered_service_ids for endpoint in result.top_endpoints)
    assert messages[0].payload_hex == payload.hex()
    assert "sd_error" in messages[0].metadata and "sd" not in messages[0].metadata
    assert result.errors and "SD" in result.errors[0]


@pytest.mark.parametrize(
    "kind,ttl,name",
    [
        (0, 3, "FindService"),
        (1, 3, "OfferService"),
        (1, 0, "StopOfferService"),
        (6, 3, "SubscribeEventgroup"),
        (6, 0, "StopSubscribeEventgroup"),
        (7, 3, "SubscribeEventgroupAck"),
        (7, 0, "SubscribeEventgroupNack"),
    ],
)
def test_native_sd_golden_entries_reach_stats_and_agent(tmp_path, native_runtime, kind, ttl, name):
    # 人工黄金字节，不经 Python SD 编解码器，Counter=3、Eventgroup=7、Minor=2。
    entry = bytes([kind, 0, 0, 0]) + bytes.fromhex("1234000101") + ttl.to_bytes(3, "big")
    entry += bytes.fromhex("00030007" if kind in (6, 7) else "00000002")
    payload = bytes.fromhex("c000000000000010") + entry + bytes(4)
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    ).parse(_pcap_with_sd_offer(payload), "golden-sd.pcap")
    message = messages[0]
    assert not result.errors
    assert result.sd_entry_counts[name] == 1
    assert sum(result.sd_entry_counts.values()) == 1
    assert message.payload_hex == payload.hex()
    decoded = EvidenceTools.decode_sd(message)
    assert decoded["flags"] == 192 and decoded["entry_count"] == 1
    projected = decoded["entries"][0]
    assert projected["type"] == name
    assert projected["service_id"] == 0x1234 and projected["instance_id"] == 1
    assert projected["ttl"] == ttl
    assert projected["counter"] == (3 if kind in (6, 7) else None)
    assert projected["eventgroup_id"] == (7 if kind in (6, 7) else None)


def test_reports_capture_timing_protocols_and_top_endpoints(tmp_path, native_runtime) -> None:
    content = _pcap_with_mixed_traffic()
    result, messages = PcapImporter(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    ).parse(content, "mixed.pcap")

    assert result.packet_count == 3
    assert result.captured_bytes > 0
    assert result.duration_seconds == 3.5
    assert result.start_time is not None
    assert result.end_time is not None
    assert result.start_time.isoformat() == "2023-11-14T22:13:20.250000+00:00"
    assert result.someip_count == 2
    assert result.someip_packet_count == 2
    assert result.sd_count == 1
    assert result.sd_packet_count == 1
    assert result.skipped_count == 1
    assert result.transport_counts == {"udp": 2, "tcp": 1, "other": 0}
    assert result.protocol_counts == {
        "someip": 1,
        "someip_sd": 1,
        "someip_mixed": 0,
        "other": 1,
    }
    assert result.sd_entry_counts["OfferService"] == 1
    assert result.endpoint_count == 3
    assert len(messages) == 2

    top = result.top_endpoints[0]
    assert top.endpoint == "192.168.1.10"
    assert top.packet_count == 3
    assert top.sent_count == 2
    assert top.received_count == 1
    assert top.someip_count == 2
    assert top.transport_counts == {"udp": 2, "tcp": 1}
    assert top.offered_service_ids == [0x1234]
