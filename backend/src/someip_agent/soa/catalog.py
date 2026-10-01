"""把 ARXML 的明确服务部署转为原生目录及 SAT 成员字典。

配置生成不启动进程。在线配置和初始化共用发送授权校验；浏览中的类型猜测不能发包。
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from someip_agent.config import Settings
from someip_agent.domain.models import (
    ArxmlModel,
    ServiceDefinition,
    SignalDefinition,
    SimulationConfig,
)
from someip_agent.runtime.native_config import validate_network

logger = logging.getLogger(__name__)


class CatalogBuildError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("；".join(issues))


class NativeMemberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["client", "server"]
    service: str | None = None
    deployment_path: str | None = None
    name: str | None = None
    instance_id: int | None = Field(default=None, ge=0, le=0xFFFE)
    transport: Literal["internal", "udp", "tcp"] = "internal"
    port: int | None = Field(default=None, ge=1, le=65535)
    peer_host: str | None = None
    byte_order: Literal["big", "little"] | None = None
    status: bool = True
    heartbeat: int = Field(default=600, ge=1)


class NativeCatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_name: str = Field(default="arxml_partner", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    application_id: int = Field(default=0x1101, ge=1, le=0xFFFE)
    members: dict[str, NativeMemberConfig] = Field(min_length=1, max_length=128)
    sd_multicast_group: str = "239.192.255.251"
    sd_port: int = Field(default=30490, ge=1, le=65535)


class NativeServiceBundle(BaseModel):
    model_id: str
    source_sha256: str | None
    catalog: dict[str, Any]
    config: dict[str, Any]
    members: dict[str, Any]

    def write(self, directory: Path) -> tuple[Path, Path]:
        # 新目录避免覆盖正在运行的目录，也保留可审计的源模型和初始化参数。
        directory.mkdir(parents=True, exist_ok=False)
        catalog, config = directory / "catalog.json", directory / "vsomeip.json"
        try:
            catalog.write_text(json.dumps(self.catalog, ensure_ascii=False), encoding="utf-8")
            config.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
            (directory / "model-binding.json").write_text(self.model_dump_json(), encoding="utf-8")
        except Exception:
            logger.exception("ARXML 原生配置保存失败", extra={"operation": "soa.catalog.write"})
            raise
        logger.info("ARXML 原生配置已保存", extra={"operation": "soa.catalog.write"})
        return catalog, config


def _schema(signal: SignalDefinition, byte_order: str | None) -> dict[str, Any]:
    if signal.wire_schema is None:
        raise CatalogBuildError(
            [f"{signal.path or signal.name}: {signal.wire_error or '缺少已解析类型'}"]
        )
    schema = deepcopy(signal.wire_schema)
    pending = [schema]
    while pending:
        node = pending.pop()
        declared = node.get("byte_order")
        if byte_order is not None and declared is not None and byte_order != declared:
            raise CatalogBuildError([f"{signal.path}: 请求字节序与 ARXML 明确序列化部署冲突"])
        node["byte_order"] = declared or byte_order or "big"
        pending.extend(node.get("fields", []))
        if "element" in node:
            pending.append(node["element"])
    return schema


def _parameters(signals: list[SignalDefinition], byte_order: str | None) -> dict[str, Any]:
    if len({signal.name for signal in signals}) != len(signals):
        raise CatalogBuildError(["方法参数 SHORT-NAME 重复"])
    return {
        "type": "struct",
        "fields": [{"name": signal.name, **_schema(signal, byte_order)} for signal in signals],
    }


def _definition(
    service: ServiceDefinition, instance: int, byte_order: str | None
) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    events: dict[str, Any] = {}
    ids: set[int] = set()
    names: set[str] = set()

    def add(name: str, identifier: int | None, definition: dict[str, Any], event: bool) -> None:
        minimum, maximum = (0x8000, 0xFFFE) if event else (0, 0x7FFF)
        if identifier is None or not minimum <= identifier <= maximum:
            raise CatalogBuildError([f"{service.deployment_path}/{name}: 缺少或非法部署 ID"])
        if name in names or identifier in ids:
            raise CatalogBuildError([f"{service.deployment_path}/{name}: 名称或 ID 冲突"])
        if event:
            groups = definition["eventgroups"]
            if not groups or any(not 1 <= group <= 0xFFFE for group in groups):
                raise CatalogBuildError([f"{name}: 缺少或非法 Event Group ID"])
        names.add(name)
        ids.add(identifier)
        (events if event else methods)[name] = {"id": identifier, **definition}

    for method in service.methods:
        if method.fire_and_forget and method.output_signals:
            raise CatalogBuildError([f"方法 {method.path} 为 Fire-and-forget，但声明了响应参数"])
        add(
            method.name,
            method.method_id,
            {
                "input": _parameters(method.input_signals, byte_order),
                "output": _parameters(method.output_signals, byte_order),
                "fire_and_forget": method.fire_and_forget,
            },
            False,
        )
    for event in service.events:
        if not event.signals:
            raise CatalogBuildError([f"事件 {event.path} 缺少类型定义"])
        name = f"Update{event.name}Event"
        schema = (
            _schema(event.signals[0], byte_order)
            if len(event.signals) == 1
            else _parameters(event.signals, byte_order)
        )
        add(name, event.event_id, {"schema": schema, "eventgroups": event.event_group_ids}, True)
    for field in service.fields:
        if field.signal is None:
            raise CatalogBuildError([f"字段 {field.path} 缺少类型定义"])
        schema = _schema(field.signal, byte_order)
        if all(value is None for value in (field.getter_id, field.setter_id, field.notifier_id)):
            raise CatalogBuildError([f"字段 {field.path} 没有任何部署 ID"])
        if field.getter_id is not None:
            add(
                f"Get{field.name}",
                field.getter_id,
                {"input": _parameters([], byte_order), "output": schema},
                False,
            )
        if field.setter_id is not None:
            add(
                f"Set{field.name}",
                field.setter_id,
                {
                    "input": _parameters([field.signal], byte_order),
                    "output": schema,
                },
                False,
            )
        if field.notifier_id is not None:
            add(
                f"Update{field.name}Event",
                field.notifier_id,
                {
                    "schema": schema,
                    "field": True,
                    "eventgroups": field.event_group_ids,
                },
                True,
            )
    if not methods and not events:
        raise CatalogBuildError([f"服务 {service.path} 没有可初始化的成员"])
    return {
        "service_id": service.service_id,
        "instance_id": instance,
        "major_version": service.major_version,
        "minor_version": service.minor_version,
        "methods": methods,
        "events": events,
        "source": {"service_path": service.path, "deployment_path": service.deployment_path},
        "serialization_profile": "arxml-resolved-explicit-or-scalar",
    }


def build_native_bundle(
    model: ArxmlModel, request: NativeCatalogRequest, settings: Settings
) -> NativeServiceBundle:
    catalog: dict[str, Any] = {}
    members: dict[str, Any] = {}
    service_configs: dict[tuple[int, int], dict[str, Any]] = {}
    server_ids: set[tuple[int, int]] = set()
    online = any(member.transport != "internal" for member in request.members.values())
    if online and any(member.transport == "internal" for member in request.members.values()):
        raise CatalogBuildError(["同一原生进程不能混合内部隔离模式和在线模式"])
    for alias, member in request.members.items():
        if not alias or len(alias) > 256:
            raise CatalogBuildError(["成员字典键不能为空或超过 256 字符"])
        selection = member.service or alias
        candidates = [
            service for service in model.services if selection in {service.name, service.path}
        ]
        if member.deployment_path:
            candidates = [s for s in candidates if s.deployment_path == member.deployment_path]
        if len(candidates) != 1:
            raise CatalogBuildError(
                [f"成员 {alias} 的服务 {selection} 缺失或有歧义，请指定完整路径及部署"]
            )
        service = candidates[0]
        if service.deployment_errors:
            raise CatalogBuildError(service.deployment_errors)
        if (
            not service.deployment_path
            or service.service_id is None
            or not 0 <= service.service_id <= 0xFFFE
        ):
            raise CatalogBuildError([f"服务 {service.path} 缺少可确认的 SOME/IP 部署"])
        if not 0 <= service.major_version <= 0xFE or not 0 <= service.minor_version < 0xFFFFFFFF:
            raise CatalogBuildError([f"服务 {service.path} 版本超出原生支持范围"])
        instance = member.instance_id
        if instance is None:
            if len(service.instance_ids) != 1:
                raise CatalogBuildError(
                    [f"服务 {service.path} 缺少唯一实例，请显式指定 instance_id"]
                )
            instance = service.instance_ids[0]
        if instance not in service.instance_ids or not 0 <= instance <= 0xFFFE:
            raise CatalogBuildError([f"服务 {service.path} 的实例 {instance} 未部署或使用通配 ID"])
        spec = _definition(service, instance, member.byte_order)
        endpoint: dict[str, Any] = {
            "service": f"0x{service.service_id:04x}",
            "instance": f"0x{instance:04x}",
        }
        if online:
            if member.peer_host is None or member.port is None:
                raise CatalogBuildError([f"成员 {alias} 在线模式必须显式配置 peer_host 和 port"])
            peer = str(ipaddress.IPv4Address(member.peer_host))
            validate_network(
                SimulationConfig(
                    service_id=service.service_id,
                    instance_id=instance,
                    method_id=0x8000,
                    transport="udp",
                    destination_host=peer,
                    destination_port=member.port,
                    sd_multicast_group=request.sd_multicast_group,
                    sd_port=request.sd_port,
                ),
                settings,
            )
            if not ipaddress.IPv4Address(request.sd_multicast_group).is_multicast:
                raise CatalogBuildError(["SD 地址必须是 IPv4 组播地址"])
            endpoint["reliable" if member.transport == "tcp" else "unreliable"] = str(member.port)
            if member.role == "client":
                endpoint["unicast"] = peer
            spec["allowed_subscribers"] = [peer]
        groups: defaultdict[int, list[str]] = defaultdict(list)
        endpoint["events"] = []
        for event in spec["events"].values():
            identifier = f"0x{event['id']:04x}"
            endpoint["events"].append(
                {
                    "event": identifier,
                    "is_field": event.get("field", False),
                    "is_reliable": member.transport == "tcp",
                }
            )
            for group in event["eventgroups"]:
                groups[group].append(identifier)
        endpoint["eventgroups"] = [
            {"eventgroup": f"0x{g:04x}", "events": sorted(events)}
            for g, events in sorted(groups.items())
        ]
        identity = (service.service_id, instance)
        if member.role == "server":
            if identity in server_ids:
                raise CatalogBuildError([f"服务/实例 {identity} 不能重复初始化 server"])
            server_ids.add(identity)
        if identity in service_configs and service_configs[identity] != endpoint:
            raise CatalogBuildError([f"服务/实例 {identity} 的传输、端点或事件配置冲突"])
        service_configs[identity] = endpoint
        catalog[alias] = spec
        members[alias] = {
            "service": selection,
            "role": member.role,
            "name": member.name or selection,
            "instance_id": instance,
            "transport": "tcp" if member.transport == "tcp" else "udp",
            "status": member.status,
            "heartbeat": member.heartbeat,
        }
        if selection != alias:
            # 保留 SAT 的服务名、编号客户端键和实例命名；不同 alias 可绑定不同部署。
            members[alias]["definition"] = deepcopy(spec)
    name = request.application_name
    config = {
        "unicast": settings.native_unicast if online else "127.0.0.1",
        "network": name,
        "routing": name,
        "applications": [{"name": name, "id": f"0x{request.application_id:04x}"}],
        "logging": {"level": "warning", "console": True, "dlt": False},
        "services": list(service_configs.values()),
        "service-discovery": {
            "enable": online,
            "multicast": request.sd_multicast_group,
            "port": str(request.sd_port),
            "protocol": "udp",
            "ttl": "3",
            "initial_delay_min": "10",
            "initial_delay_max": "50",
            "repetitions_base_delay": "50",
            "repetitions_max": "3",
            "cyclic_offer_delay": "500",
            "request_response_delay": "10",
        },
    }
    logger.info(
        "ARXML 原生服务目录已校验", extra={"operation": "soa.catalog.build", "model_id": model.id}
    )
    return NativeServiceBundle(
        model_id=model.id,
        source_sha256=model.source_sha256,
        catalog=catalog,
        config=config,
        members=members,
    )
