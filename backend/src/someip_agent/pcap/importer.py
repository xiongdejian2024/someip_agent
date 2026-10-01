"""原生 PCAP/PCAPNG 的展示聚合；Python 不读链路层或解码 SOME/IP 头。"""

from __future__ import annotations

import ipaddress
import logging
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from someip_agent.config import Settings
from someip_agent.domain.models import MonitorMessage, PcapEndpointStat, PcapImportResult
from someip_agent.protocol.sd import SdPayload
from someip_agent.protocol.someip import SomeIpDecodeError

from .native import PcapImportError as PcapImportError
from .native import records

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 120,
        max_messages: int = 100_000,
        max_payload_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.settings = settings or Settings()
        self.timeout = timeout
        self.max_messages = max_messages
        self.max_payload_bytes = max_payload_bytes

    def parse(
        self, content: bytes, source_name: str
    ) -> tuple[PcapImportResult, list[MonitorMessage]]:
        messages: list[MonitorMessage] = []
        endpoints: dict[str, _EndpointAccumulator] = {}
        transport_counts: Counter[str] = Counter({"udp": 0, "tcp": 0, "other": 0})
        protocol_counts: Counter[str] = Counter(
            {"someip": 0, "someip_sd": 0, "someip_mixed": 0, "other": 0}
        )
        sd_entry_counts: Counter[str] = Counter(
            {
                name: 0
                for name in (
                    "FindService",
                    "OfferService",
                    "StopOfferService",
                    "SubscribeEventgroup",
                    "StopSubscribeEventgroup",
                    "SubscribeEventgroupAck",
                    "SubscribeEventgroupNack",
                )
            }
        )
        number = captured_bytes = skipped_count = someip_packets = sd_packets = payload_bytes = 0
        summary: dict[str, Any] | None = None
        try:
            with closing(records(content, self.settings, self.timeout)) as stream:
                for frame in stream:
                    if frame["action"] == "pcap_done":
                        summary = frame
                        break
                    number += 1
                    if frame["number"] != number:
                        raise PcapImportError("原生 PCAP 帧序号缺失或重复")
                    captured_bytes += int(frame["captured_bytes"])
                    transport = str(frame["transport"])
                    if transport not in transport_counts:
                        raise PcapImportError("原生 PCAP 传输类型非法")
                    transport_counts[transport] += 1
                    source, destination = frame.get("source_ip"), frame.get("destination_ip")
                    if source is not None and destination is not None:
                        version = frame["ip_version"]
                        if version not in (4, 6):
                            raise PcapImportError("原生 PCAP IP 版本非法")
                        self._record_frame_endpoints(
                            endpoints, source, destination, version, transport
                        )
                    decoded: list[MonitorMessage] = []
                    for index, packet in enumerate(frame["messages"]):
                        if packet.get("frame_message_index") != index:
                            raise PcapImportError("原生 PCAP 帧内消息序号缺失或重复")
                        message = self._to_monitor_message(packet, number)
                        payload_bytes += message.payload_size
                        if (
                            len(messages) + len(decoded) >= self.max_messages
                            or payload_bytes > self.max_payload_bytes
                        ):
                            raise PcapImportError(
                                "PCAP 解码消息数或 Payload 配额超限，整份导入未提交"
                            )
                        decoded.append(message)
                    if not decoded:
                        skipped_count += 1
                        protocol_counts["other"] += 1
                        continue
                    someip_packets += 1
                    count_sd = sum(message.is_sd for message in decoded)
                    sd_packets += bool(count_sd)
                    category = (
                        "someip_sd"
                        if count_sd == len(decoded)
                        else "someip_mixed"
                        if count_sd
                        else "someip"
                    )
                    protocol_counts[category] += 1
                    messages.extend(decoded)
                    if source is None or destination is None:
                        raise PcapImportError("原生 PCAP 解码消息缺少 IP 来源")
                    for endpoint in {source, destination}:
                        endpoints[endpoint].someip_count += len(decoded)
                    for message in decoded:
                        if message.is_sd:
                            self._record_sd_entries(message, source, endpoints, sd_entry_counts)
            if (
                summary is None
                or summary["packet_count"] != number
                or summary["captured_bytes"] != captured_bytes
            ):
                raise PcapImportError("原生 PCAP 缺少完整结束记录或统计不一致")
            start, end = summary["start_ns"], summary["end_ns"]
            result = PcapImportResult(
                source_name=source_name,
                packet_count=number,
                captured_bytes=captured_bytes,
                someip_count=len(messages),
                someip_packet_count=someip_packets,
                sd_count=sum(message.is_sd for message in messages),
                sd_packet_count=sd_packets,
                skipped_count=skipped_count,
                duration_seconds=(end - start) / 1_000_000_000
                if start is not None and end is not None
                else 0,
                start_time=self._to_datetime(start),
                end_time=self._to_datetime(end),
                endpoint_count=len(endpoints),
                top_endpoints=self._top_endpoints(endpoints),
                transport_counts=dict(transport_counts),
                protocol_counts=dict(protocol_counts),
                sd_entry_counts=dict(sd_entry_counts),
                errors=summary["errors"],
                runtime=summary["runtime"],
                link_type=summary["link_type"],
                reassembled_datagrams=summary["reassembled_datagrams"],
                fragment_error_count=summary["fragment_error_count"],
            )
            logger.info(
                "原生 PCAP 聚合完成", extra={"operation": "pcap.aggregate", "packet_count": number}
            )
            return result, messages
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            logger.exception("原生 PCAP 导入结果校验失败", extra={"operation": "pcap.aggregate"})
            if isinstance(exc, PcapImportError):
                raise
            raise PcapImportError("原生 PCAP 导入结果不符合契约") from exc

    @staticmethod
    def _record_frame_endpoints(
        endpoints: dict[str, _EndpointAccumulator],
        source: str,
        destination: str,
        version: Literal[4, 6],
        transport: str,
    ) -> None:
        for endpoint in {source, destination}:
            accumulator = endpoints.setdefault(
                endpoint,
                _EndpointAccumulator(
                    ip_version=version, is_multicast=ipaddress.ip_address(endpoint).is_multicast
                ),
            )
            accumulator.packet_count += 1
            accumulator.transport_counts[transport] += 1
        endpoints[source].sent_count += 1
        endpoints[destination].received_count += 1

    @staticmethod
    def _record_sd_entries(
        message: MonitorMessage,
        source: str,
        endpoints: dict[str, _EndpointAccumulator],
        counts: Counter[str],
    ) -> None:
        try:
            payload = SdPayload.decode(bytes.fromhex(message.payload_hex))
        except (ValueError, SomeIpDecodeError):
            logger.exception("离线 SD 展示分析失败", extra={"operation": "pcap.sd.analysis"})
            return
        for entry in payload.entries:
            if entry.entry_type == 0x00:
                name = "FindService"
            elif entry.entry_type == 0x01:
                name = "StopOfferService" if entry.ttl == 0 else "OfferService"
                if entry.ttl > 0:
                    endpoints[source].offered_service_ids.add(entry.service_id)
            elif entry.entry_type == 0x06:
                name = "StopSubscribeEventgroup" if entry.ttl == 0 else "SubscribeEventgroup"
            elif entry.entry_type == 0x07:
                name = "SubscribeEventgroupAck" if entry.ttl > 0 else "SubscribeEventgroupNack"
            else:
                name = f"Unknown(0x{entry.entry_type:02X})"
            counts[name] += 1

    @staticmethod
    def _top_endpoints(endpoints: dict[str, _EndpointAccumulator]) -> list[PcapEndpointStat]:
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
            for endpoint, stats in ranked[:20]
        ]

    @staticmethod
    def _to_datetime(timestamp_ns: int | None) -> datetime | None:
        return (
            None
            if timestamp_ns is None
            else datetime(1970, 1, 1, tzinfo=timezone.utc)
            + timedelta(microseconds=timestamp_ns // 1000)
        )

    @classmethod
    def _to_monitor_message(cls, packet: dict[str, Any], number: int) -> MonitorMessage:
        sd_summary = None
        if packet["is_sd"]:
            try:
                sd_summary = SdPayload.decode(bytes.fromhex(packet["payload_hex"])).summary()
            except SomeIpDecodeError as exc:
                logger.exception("离线 SD 摘要分析失败", extra={"operation": "pcap.sd.summary"})
                sd_summary = f"SOME/IP-SD 解析失败: {exc}"
        timestamp = cls._to_datetime(packet["received_at_ns"])
        if timestamp is None:
            raise PcapImportError("原生 PCAP 消息缺少时间戳")
        return MonitorMessage(
            timestamp=timestamp,
            direction="pcap",
            transport=packet["transport"],
            source=packet["source"],
            destination=packet["destination"],
            service_id=packet["service_id"],
            method_id=packet["method_id"],
            client_id=packet["client_id"],
            session_id=packet["session_id"],
            interface_version=packet["interface_version"],
            message_type=packet["message_type"],
            return_code=packet["return_code"],
            payload_hex=packet["payload_hex"],
            payload_size=packet["payload_size"],
            is_sd=packet["is_sd"],
            sd_summary=sd_summary,
            metadata={
                "pcap_frame": number,
                "frame_message_index": packet["frame_message_index"],
                "runtime": "vsomeip",
                "observation": "pcap_import",
                "timestamp_source": "pcap_file",
                "native_timestamp_ns": packet["received_at_ns"],
                "wire_verified": False,
                "ip_reassembled": packet["ip_reassembled"],
                "ip_fragment_count": packet["ip_fragment_count"],
                "tcp_partial": packet["tcp_partial"],
            },
        )
