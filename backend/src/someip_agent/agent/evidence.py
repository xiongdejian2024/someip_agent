"""有界、可复核的 SOME/IP 领域证据；不依赖模型推理或外部网络。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import Counter
from collections.abc import Callable
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from someip_agent.domain.models import (
    GeneratorKind,
    MessageType,
    MonitorMessage,
    ReturnCode,
    ServiceDefinition,
    SignalDefinition,
    SignalGeneratorConfig,
    SimulationConfig,
)
from someip_agent.protocol.native_payload import NativePayloadError, NativeSignalDecoder
from someip_agent.protocol.sd_metadata import read_sd
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.soa.operator import NativeRuntimeError

logger = logging.getLogger(__name__)
SCAN_LIMIT = 5000
PAYLOAD_LIMIT = 65536


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ServiceQuery(Arguments):
    keyword: str = Field(default="", max_length=100)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=50)


class SchemaQuery(Arguments):
    service_id: int = Field(ge=0, le=0xFFFF)
    method_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=50)


class MessageQuery(Arguments):
    service_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    method_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    source: Literal["live", "pcap"] | None = None
    errors_only: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class MessageTarget(Arguments):
    message_id: str = Field(min_length=1, max_length=128)
    source: Literal["live", "pcap"] | None = None


class SdQuery(Arguments):
    service_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    instance_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    eventgroup_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    source: Literal["live", "pcap"] | None = None
    limit: int = Field(default=30, ge=1, le=100)


class SignalQuery(Arguments):
    service_id: int = Field(ge=0, le=0xFFFF)
    method_id: int = Field(ge=0, le=0xFFFF)
    signal_name: str = Field(min_length=1, max_length=256)
    source: Literal["live", "pcap"] | None = None


class SimulationPlan(Arguments):
    service_id: int = Field(ge=0, le=0xFFFF)
    method_id: int = Field(ge=0, le=0xFFFF)
    signal_name: str | None = Field(default=None, max_length=256)
    kind: Literal["constant", "sine", "ramp"] = "constant"
    initial: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    interval_ms: int = Field(default=100, ge=10, le=60_000)
    period_seconds: float = Field(default=5, gt=0, le=86400)


def service_brief(service: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_id": service.get("service_id"),
        "name": str(service.get("name", ""))[:256],
        "major_version": service.get("major_version", 1),
        "instance_ids": service.get("instance_ids", [])[:20],
        "method_count": len(service.get("methods", [])),
        "event_count": len(service.get("events", [])),
        "field_count": len(service.get("fields", [])),
    }


def message_brief(message: MonitorMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "timestamp": message.timestamp.isoformat(),
        "service_id": message.service_id,
        "method_id": message.method_id,
        "direction": message.direction,
        "transport": message.transport,
        "source": message.source[:256],
        "destination": message.destination[:256],
        "interface_version": message.interface_version,
        "message_type": message.message_type,
        "return_code": message.return_code,
        "payload_size": message.payload_size,
        "is_sd": message.is_sd,
    }


def signal_brief(signal: SignalDefinition) -> dict[str, Any]:
    result = signal.model_dump(mode="json", exclude={"path", "type_ref"})
    result["name"] = signal.name[:256]
    if result.get("unit"):
        result["unit"] = result["unit"][:64]
    return result


class EvidenceTools:
    SPECS: dict[str, tuple[str, type[Arguments]]] = {
        "list_services": ("分页搜索 ARXML 服务目录；先缩小目标，禁止全量展开", ServiceQuery),
        "get_service_schema": ("读取指定服务/方法/事件/字段的 ARXML 信号定义", SchemaQuery),
        "query_messages": ("查询有限缓存中的真实报文摘要，返回可用于诊断的报文 ID", MessageQuery),
        "analyze_message": (
            "按真实报文 ID 检查 ARXML 匹配、返回码、版本与 payload 解码",
            MessageTarget,
        ),
        "analyze_sd": (
            "解析 SD Entry，区分 Offer/StopOffer、订阅/Ack/Nack；不把缺证据当失败",
            SdQuery,
        ),
        "analyze_signal": ("指定信号的有限缓存数值、ARXML 范围与采样间隔摘要", SignalQuery),
        "prepare_simulation": (
            "从 ARXML 创建单信号事件/Notifier 的虚拟仿真计划；只准备不执行",
            SimulationPlan,
        ),
    }

    def __init__(
        self,
        monitor: MonitorStore,
        get_services: Callable[[], list[dict[str, Any]]],
        *,
        decoder: NativeSignalDecoder | None = None,
    ) -> None:
        self.monitor = monitor
        self.get_services = get_services
        self.decoder = decoder

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        parsed = self.SPECS[name][1].model_validate(arguments)
        return await getattr(self, name)(parsed)

    def service(self, service_id: int) -> ServiceDefinition:
        matches = [item for item in self.get_services() if item.get("service_id") == service_id]
        if not matches:
            raise ValueError(f"当前 ARXML 未定义服务 0x{service_id:04X}")
        if len(matches) != 1:
            raise ValueError("当前 ARXML 存在重复服务 ID，请先消除歧义")
        return ServiceDefinition.model_validate(matches[0])

    async def snapshot(
        self, source: str | None = None
    ) -> tuple[list[MonitorMessage], dict[str, Any]]:
        cached = await self.monitor.list(limit=SCAN_LIMIT)
        messages = [m for m in cached if source != "pcap" or m.direction == "pcap"]
        times = [m.timestamp for m in messages]
        return messages, {
            "scan_limit": SCAN_LIMIT,
            "scanned_count": len(cached),
            "source": source or "all",
            "source_count": len(messages),
            "start_time": min(times).isoformat() if times else None,
            "end_time": max(times).isoformat() if times else None,
            "limitation": "仅最近有限内存缓存，不代表完整抓包或网络历史；未观测到不等于失败。",
            "source_note": (
                "live 表示当前工作台缓存，可能混有仿真与 PCAP；pcap 仅包括 direction=pcap。"
            ),
        }

    async def list_services(self, args: ServiceQuery) -> dict[str, Any]:
        services = self.get_services()
        keyword = args.keyword.casefold()
        matches = [
            s
            for s in services
            if keyword
            in (
                f"{s.get('name', '')} {s.get('service_id')} "
                f"{hex(s['service_id']) if s.get('service_id') is not None else ''}"
            ).casefold()
        ]
        return {
            "total": len(services),
            "matched": len(matches),
            "offset": args.offset,
            "items": [service_brief(s) for s in matches[args.offset : args.offset + args.limit]],
            "has_more": args.offset + args.limit < len(matches),
            "source": "current_arxml",
        }

    @staticmethod
    def endpoints(service: ServiceDefinition) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        for method in service.methods:
            endpoints.append(
                {
                    "kind": "method",
                    "method_id": method.method_id,
                    "name": method.name[:256],
                    "input_signals": [signal_brief(s) for s in method.input_signals[:30]],
                    "output_signals": [signal_brief(s) for s in method.output_signals[:30]],
                    "signal_count": len(method.input_signals) + len(method.output_signals),
                }
            )
        for event in service.events:
            endpoints.append(
                {
                    "kind": "event",
                    "method_id": event.event_id,
                    "name": event.name[:256],
                    "event_group_ids": event.event_group_ids[:20],
                    "signals": [signal_brief(s) for s in event.signals[:30]],
                    "signal_count": len(event.signals),
                }
            )
        for field in service.fields:
            for kind, identifier in (
                ("getter", field.getter_id),
                ("setter", field.setter_id),
                ("notifier", field.notifier_id),
            ):
                if identifier is not None:
                    endpoints.append(
                        {
                            "kind": kind,
                            "method_id": identifier,
                            "name": field.name[:256],
                            "signals": [signal_brief(field.signal)] if field.signal else [],
                            "signal_count": int(field.signal is not None),
                        }
                    )
        return endpoints

    async def get_service_schema(self, args: SchemaQuery) -> dict[str, Any]:
        service = self.service(args.service_id)
        endpoints = [
            e
            for e in self.endpoints(service)
            if args.method_id is None or e["method_id"] == args.method_id
        ]
        return {
            "service": service_brief(service.model_dump(mode="json")),
            "endpoints": endpoints[args.offset : args.offset + args.limit],
            "matched": len(endpoints),
            "offset": args.offset,
            "has_more": args.offset + args.limit < len(endpoints),
            "limitations": ["信号定义每个方向最多展示 30 个；仅当前导入 ARXML 的基础类型投影。"],
        }

    async def query_messages(self, args: MessageQuery) -> dict[str, Any]:
        messages, scope = await self.snapshot(args.source)
        matches = [
            m
            for m in messages
            if (args.service_id is None or m.service_id == args.service_id)
            and (args.method_id is None or m.method_id == args.method_id)
            and (not args.errors_only or m.return_code != 0)
        ]
        return {
            "scope": scope,
            "matched": len(matches),
            "items": [message_brief(m) for m in reversed(matches[-args.limit :])],
            "truncated": len(matches) > args.limit,
        }

    @staticmethod
    def definitions(
        service: ServiceDefinition, message: MonitorMessage
    ) -> tuple[str, list[SignalDefinition]]:
        for event in service.events:
            if event.event_id == message.method_id:
                return "event", event.signals
        for method in service.methods:
            if method.method_id == message.method_id:
                response = bool(message.message_type & 0x80)
                return "method", method.output_signals if response else method.input_signals
        for field in service.fields:
            for kind, identifier in (
                ("getter", field.getter_id),
                ("setter", field.setter_id),
                ("notifier", field.notifier_id),
            ):
                if identifier is not None and identifier == message.method_id:
                    if kind == "getter" and not message.message_type & 0x80:
                        return kind, []
                    return kind, [field.signal] if field.signal else []
        return "unmatched", []

    async def analyze_message(self, args: MessageTarget) -> dict[str, Any]:
        messages, scope = await self.snapshot(args.source)
        message = next((m for m in reversed(messages) if m.id == args.message_id), None)
        if message is None:
            return {
                "status": "not_observed",
                "message_id": args.message_id,
                "scope": scope,
                "findings": ["此报文已离开最近缓存或来源不匹配，请重新选择当前报文。"],
            }
        result: dict[str, Any] = {
            "status": "observed",
            "evidence": message_brief(message),
            "scope": scope,
            "findings": [],
        }
        result["return_code_name"] = {code.value: code.name for code in ReturnCode}.get(
            message.return_code, f"UNKNOWN_0x{message.return_code:02X}"
        )
        findings: list[str] = result["findings"]
        if message.return_code:
            findings.append(
                f"报文返回码非 E_OK：{result['return_code_name']}，不能仅据此判定根因。"
            )
        if message.is_sd:
            result["sd"] = self.decode_sd(message)
            return result
        try:
            service = self.service(message.service_id)
        except ValueError as exc:
            logger.exception(
                "智能体 ARXML 服务匹配失败", extra={"operation": "agent.evidence.schema"}
            )
            result["schema_match"] = False
            findings.append(str(exc))
            return result
        kind, signals = self.definitions(service, message)
        result.update(
            service=service_brief(service.model_dump(mode="json")),
            endpoint_kind=kind,
            schema_match=kind != "unmatched",
        )
        if message.interface_version != service.major_version:
            findings.append(
                f"接口版本不匹配：报文 {message.interface_version}，ARXML {service.major_version}。"
            )
        if kind == "unmatched":
            findings.append("ARXML 中未找到此 method/event/field ID，不能猜测 payload 信号。")
            return result
        if kind in {"event", "notifier"} and message.message_type != MessageType.NOTIFICATION:
            findings.append("事件/Notifier 使用了非 NOTIFICATION 消息类型，请核对接口部署。")
        if len(signals) > 100 or len(message.payload_hex) > PAYLOAD_LIMIT * 2:
            findings.append("信号数或 payload 超过诊断上限，未进行解码。")
            return result
        try:
            if len(message.payload_hex) != message.payload_size * 2:
                findings.append("记录的 payload_size 与实际 payload 字节数不一致。")
            if self.decoder is None:
                raise NativeRuntimeError("原生信号解码会话未配置，不使用Python回退")
            decoded = await asyncio.to_thread(self.decoder.decode, message.payload_hex, signals)
            result["decoded_signals"] = {
                key[:256]: (
                    str(value)
                    if isinstance(value, float) and not math.isfinite(value)
                    else value
                    if not isinstance(value, str)
                    else value[:256]
                )
                for key, value in decoded.items()
            }
            result["signal_decoder"] = "someip-agent-native-codec"
            if not signals and message.payload_hex:
                findings.append("该方向未定义信号但 payload 非空，需检查 ARXML 完整性及序列化。")
            result["decode_status"] = "decoded" if signals else "no_signal_definition"
            if len(json.dumps(result["decoded_signals"], ensure_ascii=False)) > 8192:
                result.pop("decoded_signals")
                result["decoded_signal_names"] = [s.name[:256] for s in signals]
                result["decode_status"] = "decoded_summary"
                findings.append(
                    "原生解码成功，但结构化结果超过8KiB诊断预算；只返回信号名称，完整值保留在监控。"
                )
        except (ValueError, NativeRuntimeError) as exc:
            logger.exception(
                "智能体报文证据解码失败",
                extra={"operation": "agent.evidence.decode", "message_id": message.id},
            )
            result["decode_status"] = "error"
            findings.append(f"{type(exc).__name__}: {exc}")
        result["limitations"] = [
            "按明确ARXML wire_schema复用原生Codec；缺布局不猜测，不证明E2E或SOME/IP-TP完整性；"
            "缓存未保存协议头 protocol_version。"
        ]
        return result

    @staticmethod
    def decode_sd(message: MonitorMessage) -> dict[str, Any]:
        if len(message.payload_hex) > PAYLOAD_LIMIT * 2:
            return {"error": "SD payload 超过 64 KiB 诊断上限"}
        try:
            payload = read_sd(message.metadata)
            entries = []
            for entry in payload.entries[:256]:
                names = {
                    0: "FindService",
                    1: "OfferService" if entry.ttl else "StopOfferService",
                    6: "SubscribeEventgroup" if entry.ttl else "StopSubscribeEventgroup",
                    7: "SubscribeEventgroupAck" if entry.ttl else "SubscribeEventgroupNack",
                }
                entries.append(
                    {
                        "type": names.get(entry.entry_type, f"unknown:{entry.entry_type}"),
                        "service_id": entry.service_id,
                        "instance_id": entry.instance_id,
                        "major_version": entry.major_version,
                        "ttl": entry.ttl,
                        "eventgroup_id": entry.eventgroup_id,
                        "counter": entry.counter,
                    }
                )
            return {
                "entries": entries,
                "entry_count": len(payload.entries),
                "truncated": len(payload.entries) > 256,
                "option_count": len(payload.options),
                "flags": payload.flags,
            }
        except ValueError as exc:
            logger.exception(
                "智能体 SD 证据解析失败",
                extra={"operation": "agent.evidence.sd", "message_id": message.id},
            )
            return {"error": f"{type(exc).__name__}: {exc}"}

    async def analyze_sd(self, args: SdQuery) -> dict[str, Any]:
        messages, scope = await self.snapshot(args.source)
        matches: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        counts: Counter[str] = Counter()
        decoded_bytes = 0
        decoded_frames = 0
        for message in reversed(messages):
            if not message.is_sd:
                continue
            decoded_bytes += len(message.payload_hex) // 2
            decoded_frames += 1
            if decoded_bytes > 4 * 1024 * 1024 or decoded_frames > 500:
                scope["sd_scan_truncated"] = True
                break
            decoded = self.decode_sd(message)
            if "error" in decoded:
                if len(failures) < 10:
                    failures.append({"message_id": message.id, "error": decoded["error"]})
                continue
            for entry in decoded["entries"]:
                if any(
                    getattr(args, key) is not None and entry[key] != getattr(args, key)
                    for key in ("service_id", "instance_id", "eventgroup_id")
                ):
                    continue
                counts[entry["type"]] += 1
                if len(matches) < args.limit:
                    matches.append(
                        {
                            **entry,
                            "message_id": message.id,
                            "timestamp": message.timestamp.isoformat(),
                            "source": message.source[:256],
                            "destination": message.destination[:256],
                        }
                    )
        return {
            "scope": scope,
            "status": "observed" if counts else "not_observed",
            "entry_counts": dict(counts),
            "entries": matches,
            "parse_errors": failures,
            "limitations": [
                "按 SD Entry 内 service_id 过滤，不按外层 0xFFFF 过滤。",
                "TTL=0 的 Offer 是停止提供，TTL=0 的 Ack 是 Nack；不能据缓存推断当前在线状态。",
                "最多解析最近 500 个 SD 帧、4 MiB payload、每帧 256 个 Entry；"
                "缺失 Ack/Offer 不直接断言订阅失败。",
            ],
        }

    async def analyze_signal(self, args: SignalQuery) -> dict[str, Any]:
        messages, scope = await self.snapshot(args.source)
        service = self.service(args.service_id)
        values: list[tuple[MonitorMessage, float]] = []
        probe = MonitorMessage(service_id=args.service_id, method_id=args.method_id)
        _, input_signals = self.definitions(service, probe)
        probe.message_type = MessageType.RESPONSE
        _, output_signals = self.definitions(service, probe)
        definition = next(
            (s for s in [*input_signals, *output_signals] if s.name == args.signal_name), None
        )
        if definition is None:
            raise ValueError("当前 ARXML 目标接口未定义此信号，不能凭缓存中的名称猜测")
        skipped = 0
        decode_bytes = 0
        decode_errors = 0
        groups: dict[int, tuple[list[SignalDefinition], list[MonitorMessage]]] = {}
        recovered: dict[str, Any] = {}
        for message in messages:
            if (
                message.is_sd
                or message.service_id != args.service_id
                or message.method_id != args.method_id
                or message.signal_values.get(args.signal_name) is not None
            ):
                continue
            _, signals = self.definitions(service, message)
            decode_bytes += len(message.payload_hex) // 2
            if (
                len(signals) <= 100
                and decode_bytes <= 4 * 1024 * 1024
                and len(message.payload_hex) <= PAYLOAD_LIMIT * 2
            ):
                groups.setdefault(id(signals), (signals, []))[1].append(message)
        for signals, candidates in groups.values():
            try:
                if self.decoder is None:
                    raise NativeRuntimeError("原生信号解码会话未配置，不使用Python回退")
                records = await asyncio.to_thread(
                    self.decoder.decode_many, [m.payload_hex for m in candidates], signals
                )
                for message, record in zip(candidates, records, strict=True):
                    if "error" in record:
                        try:
                            raise NativePayloadError(record["error"])
                        except NativePayloadError:
                            decode_errors += 1
                            logger.exception(
                                "智能体信号统计原生解码失败",
                                extra={
                                    "operation": "agent.evidence.signal",
                                    "message_id": message.id,
                                },
                            )
                    else:
                        recovered[message.id] = record["values"].get(args.signal_name)
            except (ValueError, NativeRuntimeError):
                decode_errors += len(candidates)
                logger.exception(
                    "智能体信号统计原生批量解码失败", extra={"operation": "agent.evidence.signal"}
                )
        for message in messages:
            if (
                message.is_sd
                or message.service_id != args.service_id
                or message.method_id != args.method_id
            ):
                continue
            value = message.signal_values.get(args.signal_name)
            if value is None:
                value = recovered.get(message.id)
            if isinstance(value, (int, float)) and math.isfinite(value):
                values.append((message, float(value)))
            else:
                skipped += 1
        values.sort(key=lambda item: item[0].timestamp)
        intervals = [
            (b[0].timestamp - a[0].timestamp).total_seconds() * 1000
            for a, b in zip(values, values[1:], strict=False)
        ]
        numbers = [v for _, v in values]
        outside = [
            (m, v)
            for m, v in values
            if definition
            and (
                (definition.minimum is not None and v < definition.minimum)
                or (definition.maximum is not None and v > definition.maximum)
            )
        ]
        return {
            "scope": scope,
            "status": "observed" if values else "not_observed",
            "service_id": args.service_id,
            "method_id": args.method_id,
            "signal_name": args.signal_name,
            "signal_definition": signal_brief(definition) if definition else None,
            "sample_count": len(values),
            "missing_or_non_numeric": skipped,
            "decode_error_count": decode_errors,
            "decode_budget_exhausted": decode_bytes > 4 * 1024 * 1024,
            "statistics": {
                "minimum": min(numbers),
                "maximum": max(numbers),
                "mean": mean(numbers),
                "latest": numbers[-1],
            }
            if numbers
            else None,
            "interval_ms": {
                "minimum": min(intervals),
                "maximum": max(intervals),
                "mean": mean(intervals),
            }
            if intervals
            else None,
            "out_of_range_count": len(outside),
            "evidence": [
                {"message_id": m.id, "timestamp": m.timestamp.isoformat(), "value": v}
                for m, v in (outside[:10] if outside else values[-5:])
            ],
            "limitations": [
                "采样间隔不等于总线周期；抓包缺失、仿真/来源混合会影响统计。没有期望周期时不判定丢帧。"
            ],
        }

    def simulation_target(
        self, service_id: int, method_id: int
    ) -> tuple[ServiceDefinition, SignalDefinition]:
        service = self.service(service_id)
        candidates = [e.signals for e in service.events if e.event_id == method_id]
        candidates += [
            [f.signal] if f.signal else [] for f in service.fields if f.notifier_id == method_id
        ]
        if len(candidates) != 1 or len(candidates[0]) != 1:
            raise ValueError(
                "仅支持 ARXML 中唯一且单信号的 Event/Notifier；方法和复杂 payload 暂不可仿真"
            )
        signal = candidates[0][0]
        if (
            signal.data_type in {"string", "bytes"}
            or signal.byte_order != "big"
            or signal.factor != 1
            or signal.offset != 0
        ):
            raise ValueError("当前发生器仅支持大端、无缩放的单个数值/布尔信号")
        return service, signal

    @staticmethod
    def safe_range(signal: SignalDefinition) -> tuple[float, float]:
        dtype = signal.data_type.value
        if dtype == "boolean":
            low, high = 0.0, 1.0
        elif dtype.startswith("uint"):
            low, high = 0.0, float(min(2 ** int(dtype[4:]) - 1, 2**53 - 1))
        elif dtype.startswith("int"):
            bits = int(dtype[3:])
            low, high = (
                float(max(-(2 ** (bits - 1)), -(2**53 - 1))),
                float(min(2 ** (bits - 1) - 1, 2**53 - 1)),
            )
        else:
            low, high = -3.4e38, 3.4e38
        return max(low, signal.minimum if signal.minimum is not None else low), min(
            high, signal.maximum if signal.maximum is not None else high
        )

    def validate_simulation(self, config: SimulationConfig) -> None:
        if config.transport != "internal":
            raise PermissionError("智能体只能启动 internal 虚拟仿真，禁止真实网络发送")
        service, signal = self.simulation_target(config.service_id, config.method_id)
        if config.interface_version != service.major_version:
            raise ValueError("仿真接口版本与当前 ARXML 不一致")
        if service.instance_ids and config.instance_id not in service.instance_ids:
            raise ValueError("仿真实例 ID 不属于当前 ARXML")
        generator = config.generator
        if generator.signal_name != signal.name or generator.data_type != signal.data_type:
            raise ValueError("仿真信号名称/类型与当前 ARXML 不一致")
        low, high = self.safe_range(signal)
        numbers = [generator.initial, generator.minimum, generator.maximum, *generator.sequence]
        if len(generator.sequence) > 1000 or not math.isfinite(generator.period_seconds):
            raise ValueError("仿真序列或周期超出安全边界")
        if any(not math.isfinite(v) or v < low or v > high for v in numbers):
            raise ValueError(f"仿真数值必须位于类型与 ARXML 共同安全范围 {low}..{high}")

    async def prepare_simulation(self, args: SimulationPlan) -> dict[str, Any]:
        service, signal = self.simulation_target(args.service_id, args.method_id)
        if args.signal_name is not None and args.signal_name != signal.name:
            raise ValueError("选中的信号不属于该 Event/Notifier")
        low, high = self.safe_range(signal)
        minimum = args.minimum if args.minimum is not None else max(low, min(0, high))
        maximum = args.maximum if args.maximum is not None else max(minimum, min(100, high))
        initial = args.initial if args.initial is not None else minimum
        config = SimulationConfig(
            name=f"{service.name[:80]}/{signal.name[:80]}",
            service_id=args.service_id,
            method_id=args.method_id,
            instance_id=service.instance_ids[0] if service.instance_ids else 1,
            interface_version=service.major_version,
            transport="internal",
            interval_ms=args.interval_ms,
            generator=SignalGeneratorConfig(
                signal_name=signal.name,
                kind=GeneratorKind(args.kind),
                data_type=signal.data_type,
                minimum=minimum,
                maximum=maximum,
                initial=initial,
                period_seconds=args.period_seconds,
            ),
        )
        self.validate_simulation(config)
        logger.info(
            "智能体已准备虚拟仿真计划（未执行）",
            extra={
                "operation": "agent.simulation.prepare",
                "service_id": args.service_id,
                "method_id": args.method_id,
            },
        )
        return {
            "status": "prepared",
            "title": f"虚拟仿真：{signal.name[:128]}",
            "simulation_config": config.model_dump(mode="json"),
            "evidence": {
                "source": "current_arxml",
                "service": service_brief(service.model_dump(mode="json")),
                "signal": signal_brief(signal),
            },
            "limitations": [
                "这是计划，尚未执行；需用户确认后启动。",
                "仅 internal 虚拟总线，不发送真实以太网报文。",
            ],
        }
