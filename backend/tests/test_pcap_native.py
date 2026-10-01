"""用成熟 dpkt 生成确定夹具；产品必须由真实原生进程读取与解码。"""

import io
import socket
from decimal import Decimal

import dpkt
import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.pcap.importer import PcapImporter, PcapImportError

GOLDEN = bytes.fromhex("456780030000000c001100170101020041280000")


def ethernet(transport, *, ipv6=False, reverse=False):
    source, destination = ("fd77::2", "fd77::1") if ipv6 else ("10.77.0.2", "10.77.0.1")
    if reverse:
        source, destination = destination, source
    protocol = 17 if isinstance(transport, dpkt.udp.UDP) else 6
    if ipv6:
        network = dpkt.ip6.IP6(
            src=socket.inet_pton(socket.AF_INET6, source),
            dst=socket.inet_pton(socket.AF_INET6, destination),
            nxt=protocol,
            data=transport,
        )
        network.plen = len(transport)
    else:
        network = dpkt.ip.IP(
            src=socket.inet_aton(source),
            dst=socket.inet_aton(destination),
            p=protocol,
            data=transport,
        )
        network.len = len(network)
    return bytes(dpkt.ethernet.Ethernet(type=0x86DD if ipv6 else 0x0800, data=network))


def udp(body=GOLDEN, *, ipv6=False):
    transport = dpkt.udp.UDP(sport=41014, dport=30614, data=body)
    transport.ulen = len(transport)
    return ethernet(transport, ipv6=ipv6)


def tcp(seq, body=b"", *, flags=dpkt.tcp.TH_ACK | dpkt.tcp.TH_PUSH, reverse=False, ipv6=False):
    return ethernet(
        dpkt.tcp.TCP(
            sport=30614 if reverse else 41014,
            dport=41014 if reverse else 30614,
            seq=seq & 0xFFFFFFFF,
            ack=101 if reverse else 701,
            flags=flags,
            data=body,
        ),
        reverse=reverse,
        ipv6=ipv6,
    )


def capture(frames, *, link_type=1, fmt="pcap", nano=False, timestamps=None):
    output = io.BytesIO()
    writer = (
        dpkt.pcapng.Writer(output, linktype=link_type)
        if fmt == "pcapng"
        else dpkt.pcap.Writer(output, linktype=link_type, nano=nano)
    )
    for index, frame in enumerate(frames):
        if link_type in {101, 228, 229}:
            frame = bytes(dpkt.ethernet.Ethernet(frame).data)
        elif link_type == 113:
            packet = dpkt.ethernet.Ethernet(frame)
            frame = bytes(dpkt.sll.SLL(ethtype=packet.type, data=packet.data))
        writer.writepkt(frame, ts=timestamps[index] if timestamps else 1700000000 + index)
    return output.getvalue()


@pytest.fixture
def importer(tmp_path, native_runtime):
    return PcapImporter(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))


@pytest.mark.parametrize("fmt", ["pcap", "pcapng"])
@pytest.mark.parametrize("link_type", [1, 101, 113])
def test_native_container_formats_links_and_multiple_messages(importer, fmt, link_type):
    result, messages = importer.parse(
        capture([udp(GOLDEN * 2)], fmt=fmt, link_type=link_type), "格式.pcap"
    )
    assert result.runtime == "vsomeip" and result.packet_count == 1 and result.someip_count == 2
    assert result.source_name == "格式.pcap" and result.errors == []
    assert [message.payload_hex for message in messages] == ["41280000"] * 2
    assert [message.metadata["frame_message_index"] for message in messages] == [0, 1]
    assert all(
        message.metadata["runtime"] == "vsomeip" and message.metadata["pcap_frame"] == 1
        for message in messages
    )
    assert all(not message.metadata["wire_verified"] for message in messages)


@pytest.mark.parametrize("link_type", [1, 101, 113, 229])
@pytest.mark.parametrize("transport", ["udp", "tcp"])
def test_native_ipv6_endpoints_and_partial_tcp(importer, link_type, transport):
    frame = udp(ipv6=True) if transport == "udp" else tcp(101, GOLDEN, ipv6=True)
    result, messages = importer.parse(capture([frame], link_type=link_type), "ipv6.pcap")
    assert result.someip_count == 1 and result.errors == []
    assert messages[0].source == "[fd77::2]:41014" and messages[0].destination == "[fd77::1]:30614"
    assert messages[0].metadata["tcp_partial"] == (transport == "tcp")
    assert all(endpoint.ip_version == 6 for endpoint in result.top_endpoints)


def test_native_nanosecond_timestamp_preserves_raw_integer(importer):
    timestamp = Decimal("1700000000.123456789")
    result, messages = importer.parse(
        capture([udp()], nano=True, timestamps=[timestamp]), "nano.pcap"
    )
    assert result.start_time.isoformat() == "2023-11-14T22:13:20.123456+00:00"
    assert messages[0].metadata["native_timestamp_ns"] == 1700000000123456789


@pytest.mark.parametrize("fmt", ["pcap", "pcapng"])
def test_native_empty_file_has_complete_zero_summary(importer, fmt):
    result, messages = importer.parse(capture([], fmt=fmt), "empty.pcap")
    assert result.packet_count == result.someip_count == result.endpoint_count == 0
    assert (
        result.start_time is None and result.end_time is None and not messages and not result.errors
    )


def test_native_tcp_reorder_duplicate_and_stream_eof(importer):
    frames = [
        tcp(100, flags=dpkt.tcp.TH_SYN),
        tcp(700, flags=dpkt.tcp.TH_SYN | dpkt.tcp.TH_ACK, reverse=True),
        tcp(101, flags=dpkt.tcp.TH_ACK),
        tcp(109, GOLDEN[8:]),
        tcp(101, GOLDEN[:8]),
        tcp(101, GOLDEN[:8]),
        tcp(121, flags=dpkt.tcp.TH_RST),
    ]
    result, messages = importer.parse(capture(frames), "tcp-stream.pcap")
    assert result.packet_count == 7 and result.someip_count == 1 and result.skipped_count == 6
    assert not result.errors and messages[0].payload_hex == "41280000"
    assert messages[0].metadata["pcap_frame"] == 5 and not messages[0].metadata["tcp_partial"]


@pytest.mark.parametrize("ipv6", [False, True])
@pytest.mark.parametrize("initial", [100, 0xFFFFFFF8])
def test_native_syn_payload_split_retransmission_and_wrap(importer, ipv6, initial):
    frames = [tcp(initial, GOLDEN[:8], flags=dpkt.tcp.TH_SYN, ipv6=ipv6)] * 2
    frames += [tcp(initial + 9, GOLDEN[8:], ipv6=ipv6), tcp(initial + 1, GOLDEN, ipv6=ipv6)]
    result, messages = importer.parse(capture(frames), "syn-payload.pcap")
    assert result.someip_count == 1 and not result.errors
    assert messages[0].payload_hex == "41280000" and messages[0].metadata["pcap_frame"] == 3


def test_native_tcp_half_frame_is_reported_at_eof(importer):
    result, messages = importer.parse(capture([tcp(101, GOLDEN[:10])]), "half.pcap")
    assert not messages and result.someip_count == 0 and result.skipped_count == 1
    assert any("TCP 残留不完整" in error for error in result.errors)


def fragments():
    packet = dpkt.ethernet.Ethernet(udp()).data
    body = bytes(packet)[20:]
    result = []
    for offset, data, more in ((0, body[:16], True), (16, body[16:], False)):
        fragment = dpkt.ip.IP(src=packet.src, dst=packet.dst, p=17, id=99, data=data)
        fragment.offset = offset // 8
        fragment.mf = more
        fragment.len = len(fragment)
        result.append(bytes(dpkt.ethernet.Ethernet(type=0x0800, data=fragment)))
    return result


def test_native_fragment_expiry_uses_file_time_not_import_speed(importer):
    head, tail = fragments()
    result, messages = importer.parse(
        capture([head, head, tail], timestamps=[1, 32, 33]), "fragment-clock.pcap"
    )
    assert result.fragment_error_count == 1 and result.reassembled_datagrams == 1
    assert messages[0].payload_hex == "41280000" and messages[0].metadata["ip_fragment_count"] == 2
    assert messages[0].metadata["pcap_frame"] == 3 and messages[0].metadata["ip_reassembled"]
    assert any("30 秒" in error for error in result.errors)


def test_native_fragment_eof_does_not_claim_timeout_elapsed(importer):
    result, messages = importer.parse(capture([fragments()[0]]), "fragment-eof.pcap")
    assert not messages and result.fragment_error_count == 1
    assert any("文件结束，IPv4 分片不完整" in error for error in result.errors)
    assert all("30 秒" not in error for error in result.errors)


@pytest.mark.parametrize(
    "content", [b"not-a-capture", capture([udp()])[:-3], capture([], link_type=147)]
)
def test_native_invalid_truncated_or_unsupported_file_is_rejected(importer, content):
    with pytest.raises(PcapImportError):
        importer.parse(content, "invalid.pcap")
    assert not list((importer.settings.data_dir / "native-pcap").glob("import-*"))


@pytest.mark.parametrize("quota", ["messages", "payload"])
def test_native_aggregate_quota_is_not_silent_truncation(importer, quota):
    if quota == "messages":
        importer.max_messages = 1
    else:
        importer.max_payload_bytes = 3
    with pytest.raises(PcapImportError, match="配额超限"):
        importer.parse(capture([udp(GOLDEN * 2)]), "quota.pcap")
    assert not list((importer.settings.data_dir / "native-pcap").glob("import-*"))


def test_pcap_api_missing_binary_is_503_without_python_fallback(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, native_binary="missing-native-for-pcap-test")
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/pcap/import",
            files={"file": ("offer.pcap", capture([udp()]), "application/octet-stream")},
        )
        assert response.status_code == 503
        assert client.get("/api/v1/monitor/messages").json() == []


def test_pcap_api_failed_file_does_not_commit_partial_messages(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        content = capture([udp(), udp()])[:-3]
        response = client.post(
            "/api/v1/pcap/import",
            files={"file": ("partial.pcap", content, "application/octet-stream")},
        )
        assert response.status_code == 422
        assert client.get("/api/v1/monitor/messages").json() == []
        good = client.post(
            "/api/v1/pcap/import",
            files={"file": ("valid.pcap", capture([udp()]), "application/octet-stream")},
        )
        assert good.status_code == 200 and good.json()["runtime"] == "vsomeip"
        assert len(client.get("/api/v1/monitor/messages").json()) == 1
