from __future__ import annotations

import io
import ipaddress
import math
import socket
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import BinaryIO, Literal

import dpkt

from someip_agent.domain.models import MonitorMessage, PcapEndpointStat, PcapImportResult
from someip_agent.protocol.sd import SdPayload, is_sd_message
from someip_agent.protocol.someip import SomeIpDecodeError, SomeIpMessage, decode_many


class PcapImportError(ValueError):
    """PCAP/PCAPNG 文件无法解析。"""


@dataclass(slots=True)
class _EndpointAccumulator:
    ip_version: Literal[4, 6]
    is_multicast: bool
    packet_count: int = 0
    sent_count: int = 0
    received_count: int = 0
    someip_count: int = 0
    transport_counts: Counter[str] = field(default_factory=Counter)
    offered_service_ids: set[int] = field(default_factory=set)


class PcapImporter:
    def parse(
        self, content: bytes, source_name: str
    ) -> tuple[PcapImportResult, list[MonitorMessage]]:
        reader, link_type = self._open_reader(io.BytesIO(content))
        messages: list[MonitorMessage] = []
        packet_count = 0
        captured_bytes = 0
        skipped_count = 0
        sd_count = 0
        someip_packet_count = 0
        sd_packet_count = 0
        start_timestamp: float | None = None
        end_timestamp: float | None = None
        endpoints: dict[str, _EndpointAccumulator] = {}
        transport_counts: Counter[str] = Counter({"udp": 0, "tcp": 0, "other": 0})
        protocol_counts: Counter[str] = Counter(
            {"someip": 0, "someip_sd": 0, "someip_mixed": 0, "other": 0}
        )
        sd_entry_counts: Counter[str] = Counter(
            {
                "FindService": 0,
                "OfferService": 0,
                "StopOfferService": 0,
                "SubscribeEventgroup": 0,
                "StopSubscribeEventgroup": 0,
                "SubscribeEventgroupAck": 0,
                "SubscribeEventgroupNack": 0,
            }
        )
        errors: list[str] = []
        try:
            for packet_count, (timestamp, raw_frame) in enumerate(reader, start=1):
                captured_bytes += len(raw_frame)
                transport_recorded = False
                protocol_recorded = False
                try:
                    numeric_timestamp = float(timestamp)
                    if not math.isfinite(numeric_timestamp):
                        raise ValueError("时间戳不是有限值")
                    start_timestamp = (
                        numeric_timestamp
                        if start_timestamp is None
                        else min(start_timestamp, numeric_timestamp)
                    )
                    end_timestamp = (
                        numeric_timestamp
                        if end_timestamp is None
                        else max(end_timestamp, numeric_timestamp)
                    )
                    network_packet = self._network_packet(raw_frame, link_type)
                    if network_packet is None:
                        transport_counts["other"] += 1
                        transport_recorded = True
                        protocol_counts["other"] += 1
                        protocol_recorded = True
                        skipped_count += 1
                        continue
                    transport = self._transport_name(network_packet)
                    transport_counts[transport] += 1
                    transport_recorded = True
                    source_ip, destination_ip, ip_version = self._packet_endpoints(network_packet)
                    self._record_frame_endpoints(
                        endpoints,
                        source_ip=source_ip,
                        destination_ip=destination_ip,
                        ip_version=ip_version,
                        transport=transport,
                    )
                    decoded = list(
                        self._decode_transport(numeric_timestamp, network_packet, packet_count)
                    )
                    if not decoded:
                        protocol_counts["other"] += 1
                        protocol_recorded = True
                        skipped_count += 1
                        continue
                    someip_packet_count += 1
                    frame_sd_count = sum(1 for message in decoded if message.is_sd)
                    if frame_sd_count:
                        sd_packet_count += 1
                    if frame_sd_count == len(decoded):
                        protocol_counts["someip_sd"] += 1
                    elif frame_sd_count:
                        protocol_counts["someip_mixed"] += 1
                    else:
                        protocol_counts["someip"] += 1
                    protocol_recorded = True
                    messages.extend(decoded)
                    sd_count += frame_sd_count
                    for endpoint in {source_ip, destination_ip}:
                        endpoints[endpoint].someip_count += len(decoded)
                    for message in decoded:
                        if message.is_sd:
                            self._record_sd_entries(
                                message,
                                source_ip=source_ip,
                                endpoints=endpoints,
                                counts=sd_entry_counts,
                            )
                except (
                    ValueError,
                    IndexError,
                    TypeError,
                    OverflowError,
                    OSError,
                    dpkt.UnpackError,
                ) as exc:
                    if not transport_recorded:
                        transport_counts["other"] += 1
                    if not protocol_recorded:
                        protocol_counts["other"] += 1
                    skipped_count += 1
                    if len(errors) < 100:
                        errors.append(f"frame {packet_count}: {type(exc).__name__}: {exc}")
        except (ValueError, dpkt.UnpackError) as exc:
            raise PcapImportError(f"读取抓包失败: {exc}") from exc
        return (
            PcapImportResult(
                source_name=source_name,
                packet_count=packet_count,
                captured_bytes=captured_bytes,
                someip_count=len(messages),
                someip_packet_count=someip_packet_count,
                sd_count=sd_count,
                sd_packet_count=sd_packet_count,
                skipped_count=skipped_count,
                duration_seconds=(
                    max(0.0, end_timestamp - start_timestamp)
                    if start_timestamp is not None and end_timestamp is not None
                    else 0.0
                ),
                start_time=self._to_datetime(start_timestamp),
                end_time=self._to_datetime(end_timestamp),
                endpoint_count=len(endpoints),
                top_endpoints=self._top_endpoints(endpoints),
                transport_counts=dict(transport_counts),
                protocol_counts=dict(protocol_counts),
                sd_entry_counts=dict(sd_entry_counts),
                errors=errors,
            ),
            messages,
        )

    @staticmethod
    def _transport_name(ip_packet: dpkt.ip.IP | dpkt.ip6.IP6) -> str:
        if isinstance(ip_packet.data, dpkt.udp.UDP):
            return "udp"
        if isinstance(ip_packet.data, dpkt.tcp.TCP):
            return "tcp"
        return "other"

    @staticmethod
    def _packet_endpoints(
        ip_packet: dpkt.ip.IP | dpkt.ip6.IP6,
    ) -> tuple[str, str, Literal[4, 6]]:
        is_ipv6 = isinstance(ip_packet, dpkt.ip6.IP6)
        family = socket.AF_INET6 if is_ipv6 else socket.AF_INET
        return (
            socket.inet_ntop(family, ip_packet.src),
            socket.inet_ntop(family, ip_packet.dst),
            6 if is_ipv6 else 4,
        )

    @staticmethod
    def _record_frame_endpoints(
        endpoints: dict[str, _EndpointAccumulator],
        *,
        source_ip: str,
        destination_ip: str,
        ip_version: Literal[4, 6],
        transport: str,
    ) -> None:
        for endpoint in {source_ip, destination_ip}:
            accumulator = endpoints.setdefault(
                endpoint,
                _EndpointAccumulator(
                    ip_version=ip_version,
                    is_multicast=ipaddress.ip_address(endpoint).is_multicast,
                ),
            )
            accumulator.packet_count += 1
            accumulator.transport_counts[transport] += 1
        endpoints[source_ip].sent_count += 1
        endpoints[destination_ip].received_count += 1

    @staticmethod
    def _record_sd_entries(
        message: MonitorMessage,
        *,
        source_ip: str,
        endpoints: dict[str, _EndpointAccumulator],
        counts: Counter[str],
    ) -> None:
        try:
            payload = SdPayload.decode(bytes.fromhex(message.payload_hex))
        except (ValueError, SomeIpDecodeError):
            return
        for entry in payload.entries:
            if entry.entry_type == 0x00:
                name = "FindService"
            elif entry.entry_type == 0x01:
                name = "StopOfferService" if entry.ttl == 0 else "OfferService"
                if entry.ttl > 0:
                    endpoints[source_ip].offered_service_ids.add(entry.service_id)
            elif entry.entry_type == 0x06:
                name = "StopSubscribeEventgroup" if entry.ttl == 0 else "SubscribeEventgroup"
            elif entry.entry_type == 0x07:
                name = "SubscribeEventgroupAck" if entry.ttl > 0 else "SubscribeEventgroupNack"
            else:
                name = f"Unknown(0x{entry.entry_type:02X})"
            counts[name] += 1

    @staticmethod
    def _top_endpoints(
        endpoints: dict[str, _EndpointAccumulator], limit: int = 20
    ) -> list[PcapEndpointStat]:
        ranked = sorted(
            endpoints.items(),
            key=lambda item: (-item[1].packet_count, -item[1].someip_count, item[0]),
        )
        return [
            PcapEndpointStat(
                endpoint=endpoint,
                ip_version=stats.ip_version,
                is_multicast=stats.is_multicast,
                packet_count=stats.packet_count,
                sent_count=stats.sent_count,
                received_count=stats.received_count,
                someip_count=stats.someip_count,
                transport_counts=dict(stats.transport_counts),
                offered_service_ids=sorted(stats.offered_service_ids),
            )
            for endpoint, stats in ranked[:limit]
        ]

    @staticmethod
    def _to_datetime(timestamp: float | None) -> datetime | None:
        if timestamp is None:
            return None
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _open_reader(stream: BinaryIO) -> tuple[Iterator[tuple[float, bytes]], int]:
        failures: list[str] = []
        for reader_type in (dpkt.pcap.Reader, dpkt.pcapng.Reader):
            stream.seek(0)
            try:
                reader = reader_type(stream)
                link_type = (
                    reader.datalink() if hasattr(reader, "datalink") else dpkt.pcap.DLT_EN10MB
                )
                return reader, link_type
            except (ValueError, dpkt.UnpackError, AttributeError, EOFError) as exc:
                failures.append(f"{reader_type.__name__}: {exc}")
        raise PcapImportError("不是有效的 PCAP/PCAPNG: " + "; ".join(failures))

    @staticmethod
    def _network_packet(raw_frame: bytes, link_type: int) -> dpkt.ip.IP | dpkt.ip6.IP6 | None:
        if link_type == dpkt.pcap.DLT_EN10MB:
            packet = dpkt.ethernet.Ethernet(raw_frame).data
        elif link_type in {12, 101}:  # DLT_RAW 在不同平台的常见编号
            version = raw_frame[0] >> 4 if raw_frame else 0
            packet = dpkt.ip.IP(raw_frame) if version == 4 else dpkt.ip6.IP6(raw_frame)
        elif link_type == 113 and hasattr(dpkt, "sll"):
            packet = dpkt.sll.SLL(raw_frame).data
        else:
            return None
        while isinstance(packet, dpkt.ethernet.VLANtag8021Q):
            packet = packet.data
        return packet if isinstance(packet, (dpkt.ip.IP, dpkt.ip6.IP6)) else None

    def _decode_transport(
        self,
        timestamp: float,
        ip_packet: dpkt.ip.IP | dpkt.ip6.IP6,
        frame_number: int,
    ) -> Iterator[MonitorMessage]:
        transport = ip_packet.data
        if isinstance(transport, dpkt.udp.UDP):
            protocol = "udp"
        elif isinstance(transport, dpkt.tcp.TCP):
            protocol = "tcp"
        else:
            return
        payload = bytes(transport.data)
        if len(payload) < 16:
            return
        source_ip = socket.inet_ntop(
            socket.AF_INET6 if isinstance(ip_packet, dpkt.ip6.IP6) else socket.AF_INET,
            ip_packet.src,
        )
        destination_ip = socket.inet_ntop(
            socket.AF_INET6 if isinstance(ip_packet, dpkt.ip6.IP6) else socket.AF_INET,
            ip_packet.dst,
        )
        try:
            decoded_messages = decode_many(payload)
            for index, someip in enumerate(decoded_messages):
                yield self._to_monitor_message(
                    someip,
                    timestamp=timestamp,
                    transport=protocol,
                    source=f"{source_ip}:{transport.sport}",
                    destination=f"{destination_ip}:{transport.dport}",
                    frame_number=frame_number,
                    frame_message_index=index,
                )
        except SomeIpDecodeError:
            return

    @staticmethod
    def _to_monitor_message(
        message: SomeIpMessage,
        *,
        timestamp: float,
        transport: str,
        source: str,
        destination: str,
        frame_number: int,
        frame_message_index: int,
    ) -> MonitorMessage:
        header = message.header
        sd_summary = None
        is_sd = is_sd_message(message)
        if is_sd:
            try:
                sd_summary = SdPayload.decode(message.payload).summary()
            except SomeIpDecodeError as exc:
                sd_summary = f"SOME/IP-SD 解析失败: {exc}"
        return MonitorMessage(
            timestamp=datetime.fromtimestamp(float(timestamp), tz=timezone.utc),
            direction="pcap",
            transport=transport,
            source=source,
            destination=destination,
            service_id=header.service_id,
            method_id=header.method_id,
            client_id=header.client_id,
            session_id=header.session_id,
            interface_version=header.interface_version,
            message_type=header.message_type,
            return_code=header.return_code,
            payload_hex=message.payload.hex(),
            payload_size=len(message.payload),
            is_sd=is_sd,
            sd_summary=sd_summary,
            metadata={
                "pcap_frame": frame_number,
                "frame_message_index": frame_message_index,
            },
        )
