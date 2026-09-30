"""原生运行目录构造；不在 Python 数据面生成或编码报文。"""

from __future__ import annotations

import ipaddress
import json
import logging
from pathlib import Path
from typing import Any

from someip_agent.config import Settings
from someip_agent.domain.models import SimulationConfig

logger = logging.getLogger(__name__)


class SimulationPermissionError(PermissionError):
    pass


def validate_network(config: SimulationConfig, settings: Settings) -> None:
    if config.transport == "internal":
        return
    if not settings.network_send_enabled:
        raise SimulationPermissionError("真实网络发送默认关闭，请显式启用并配置目标白名单")
    # 原生事件按订阅端点发送，不再向任意固定端口盲发；必须授权目标主机。
    if config.destination_host not in settings.allowed_destinations:
        raise SimulationPermissionError("vsomeip 订阅模式需要目标主机级白名单，不能仅授权固定端口")
    ipaddress.IPv4Address(config.destination_host)
    if ipaddress.IPv4Address(settings.native_unicast).is_loopback:
        raise SimulationPermissionError("真实网络仿真需要配置 NATIVE_UNICAST 为本机网卡 IPv4 地址")
    if config.enable_sd and not (
        config.sd_multicast_group in settings.allowed_destinations
        or f"{config.sd_multicast_group}:{config.sd_port}" in settings.allowed_destinations
    ):
        raise SimulationPermissionError("SOME/IP-SD 组播目标未加入发送白名单")
    if config.destination_port == config.sd_port and config.enable_sd:
        raise ValueError("服务端点与 SD 端口不能相同，请使用独立的服务端口")


def write_inputs(
    directory: Path,
    identifier: str,
    config: SimulationConfig,
    settings: Settings,
) -> tuple[Path, Path, str]:
    if config.method_id < 0x8000:
        raise ValueError("周期通知需要 >= 0x8000 的事件 ID；方法调用请使用 SOA client API")
    validate_network(config, settings)
    directory.mkdir(parents=True, exist_ok=False)
    name = "sim_" + identifier.replace("-", "")
    event = "UpdateSampleEvent"
    spec: dict[str, Any] = {
        "service_id": config.service_id,
        "instance_id": config.instance_id,
        "major_version": config.interface_version,
        "events": {
            event: {
                "id": config.method_id,
                "eventgroups": [1],
                "schema": {
                    "type": "struct",
                    "fields": [
                        {
                            "name": config.generator.signal_name,
                            "type": config.generator.data_type.value,
                        }
                    ],
                },
            }
        },
    }
    service: dict[str, Any] = {
        "service": f"0x{config.service_id:04x}",
        "instance": f"0x{config.instance_id:04x}",
        "events": [{"event": f"0x{config.method_id:04x}", "is_field": False, "is_reliable": False}],
        "eventgroups": [{"eventgroup": "0x0001", "events": [f"0x{config.method_id:04x}"]}],
    }
    internal = config.transport == "internal"
    if not internal:
        service["unreliable"] = str(config.destination_port)
        spec["allowed_subscribers"] = [config.destination_host]
    native = {
        "unicast": "127.0.0.1" if internal else settings.native_unicast,
        "network": name,
        "routing": name,
        "applications": [{"name": name, "id": "0x1101"}],
        "logging": {"level": "warning", "console": True, "dlt": False},
        "services": [service],
        "service-discovery": {
            "enable": not internal and config.enable_sd,
            "multicast": config.sd_multicast_group,
            "port": str(config.sd_port),
            "protocol": "udp",
            "ttl": str(config.sd_ttl),
            "initial_delay_min": "10",
            "initial_delay_max": "50",
            "repetitions_base_delay": "50",
            "repetitions_max": "3",
            "cyclic_offer_delay": str(config.sd_offer_cycle_ms),
            "request_response_delay": "10",
        },
    }
    catalog_path, config_path = directory / "catalog.json", directory / "vsomeip.json"
    catalog_path.write_text(json.dumps({"Simulation": spec}), encoding="utf-8")
    config_path.write_text(json.dumps(native), encoding="utf-8")
    logger.info(
        "原生仿真配置已生成", extra={"operation": "native.configure", "simulation_id": identifier}
    )
    return catalog_path, config_path, name
