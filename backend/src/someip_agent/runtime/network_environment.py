"""iproute2 主机配置控制层；不发 SOME/IP、不变更既有网卡地址或默认路由。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from someip_agent.config import Settings
from someip_agent.runtime.network_gate import NetworkTaskGate

logger = logging.getLogger(__name__)


class NetworkProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    parent: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")
    interface: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")
    vlan_id: int | None = Field(default=None, ge=1, le=4094, strict=True)
    ipv4: str
    mtu: int | None = Field(default=None, ge=576, le=9000, strict=True)
    sd_multicast: str | None = None

    @field_validator("ipv4")
    @classmethod
    def ipv4_cidr(cls, value: str) -> str:
        if "/" not in value:
            raise ValueError("IPv4 地址须包含前缀，如 192.168.10.2/24")
        item = ipaddress.IPv4Interface(value)
        if item.ip.is_loopback or item.ip.is_multicast or item.ip.is_unspecified:
            raise ValueError("配置须使用非回环、非组播、非零 IPv4 地址")
        if item.network.prefixlen == 0 or (
            item.network.prefixlen < 31
            and item.ip in {item.network.network_address, item.network.broadcast_address}
        ):
            raise ValueError("地址须为有效主机地址，不能使用 /0、网络或广播地址")
        return str(item)

    @field_validator("sd_multicast")
    @classmethod
    def multicast(cls, value: str | None) -> str | None:
        if value is not None and not ipaddress.IPv4Address(value).is_multicast:
            raise ValueError("SD 路由目标必须为 IPv4 组播地址")
        return value

    @model_validator(mode="after")
    def interface_mode(self) -> NetworkProfile:
        if self.vlan_id is None and (self.interface != self.parent or self.mtu is not None):
            raise ValueError("无 VLAN 模式仅为父网卡增加自有地址，不改网卡名称或 MTU")
        if self.vlan_id is not None and self.interface == self.parent:
            raise ValueError("VLAN 子网卡名称不能与父网卡相同")
        return self


class EnvironmentUnavailable(RuntimeError):
    pass


class EnvironmentConflict(ValueError):
    pass


class NetworkEnvironmentManager:
    """有界系统命令、显式预检、持久所有权记录；重启和工程加载不执行命令。"""

    def __init__(
        self, settings: Settings, active: Callable[[], bool], gate: NetworkTaskGate | None = None
    ) -> None:
        self.settings = settings
        self._active = active
        self._gate = gate or NetworkTaskGate()
        self._lock = threading.RLock()
        self._path = settings.data_dir / "network-environment.sqlite3"
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS environments (id TEXT PRIMARY KEY, body TEXT)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def _save(self, record: dict[str, Any]) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO environments VALUES (?, ?)",
                (record["id"], json.dumps(record)),
            )

    def records(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM environments")]

    @staticmethod
    def _namespace() -> str:
        return (
            Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            + ":"
            + str(Path("/proc/self/ns/net").stat().st_ino)
        )

    @staticmethod
    def _binary() -> str:
        binary = shutil.which("ip")
        if sys.platform != "linux" or not binary:
            raise EnvironmentUnavailable("网络环境配置需要 Linux 和已安装的 iproute2")
        return binary

    def _run(self, *args: str) -> str:
        try:
            result = subprocess.run(
                [self._binary(), *args],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            if len(result.stdout) > 1024 * 1024:
                raise EnvironmentUnavailable("网卡环境输出超过 1 MiB 上限")
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            logger.exception(
                "iproute2 环境命令失败：%s",
                getattr(exc, "stderr", ""),
                extra={"operation": "network.environment"},
            )
            raise

    def _inventory(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        links = json.loads(self._run("-j", "-d", "link", "show"))
        addresses = json.loads(self._run("-j", "address", "show"))
        routes = json.loads(self._run("-j", "-4", "route", "show", "table", "main"))
        if not all(isinstance(value, list) for value in (links, addresses, routes)):
            raise EnvironmentUnavailable("iproute2 未返回有效 JSON 列表")
        indices = {item["ifname"]: item["ifindex"] for item in links}
        by_index = {item["ifindex"]: item.get("addr_info", []) for item in addresses}
        for link in links:
            link["addr_info"] = by_index.get(link["ifindex"], [])
            # 同命名空间 iproute2 常返回父名称，而非 link_index；解析实际父索引。
            if "link_index" not in link and link.get("link") in indices:
                link["link_index"] = indices[link["link"]]
        for route in routes:
            if route.get("dst", "default") != "default":
                route["dst"] = str(ipaddress.IPv4Network(route["dst"], strict=False))
        return links, routes

    def status(self) -> dict[str, Any]:
        with self._lock:
            try:
                interfaces, routes = self._inventory()
                available, reason = True, ""
            except EnvironmentUnavailable as exc:
                interfaces, routes = [], []
                available, reason = False, str(exc)
            return {
                "available": available,
                "reason": reason,
                "write_enabled": self.settings.network_config_enabled,
                "allowed_parents": self.settings.network_config_interfaces,
                "native_unicast": self.settings.native_unicast,
                "interfaces": interfaces,
                "routes": routes,
                "managed": self.records(),
                "activity": self._gate.status(),
                "active_tasks": self._active(),
            }

    def _permission(self, profile: NetworkProfile) -> None:
        if not self.settings.network_config_enabled:
            raise PermissionError("主机未开启 NETWORK_CONFIG_ENABLED；预检仍可使用")
        if profile.parent not in self.settings.network_config_interfaces:
            raise PermissionError("父网卡不在 NETWORK_CONFIG_INTERFACES 主机白名单内")
        if self._active():
            raise EnvironmentConflict("请先停止服务、仿真和监听，再应用或清理网卡配置")

    def _plan(self, profile: NetworkProfile) -> dict[str, Any]:
        links, routes = self._inventory()
        parent = next((item for item in links if item["ifname"] == profile.parent), None)
        if not parent or parent.get("link_type") != "ether" or "UP" not in parent["flags"]:
            raise EnvironmentConflict("父网卡须为已启用的以太网接口；不会代你启用物理网卡")
        if parent.get("master"):
            raise EnvironmentConflict("不配置已加入桥接／绑定的父网卡")
        if profile.vlan_id is not None and any(
            item["ifname"] == profile.interface for item in links
        ):
            raise EnvironmentConflict("目标 VLAN 网卡已经存在；不接管或覆盖既有网卡")
        if profile.mtu is not None and profile.mtu > parent["mtu"]:
            raise EnvironmentConflict("VLAN MTU 不能超过父网卡 MTU")
        address = ipaddress.IPv4Interface(profile.ipv4)
        for item in links:
            for existing in item.get("addr_info", []):
                if existing.get("family") == "inet" and existing["local"] == str(address.ip):
                    raise EnvironmentConflict("该 IPv4 已配置在主机上；不会接管已有地址")
        destinations = [str(address.network)]
        if profile.sd_multicast:
            destinations.append(profile.sd_multicast + "/32")
        for route in routes:
            if route.get("dst", "default") == "default":
                continue
            existing = ipaddress.IPv4Network(route["dst"], strict=False)
            if any(existing.overlaps(ipaddress.IPv4Network(dst)) for dst in destinations):
                raise EnvironmentConflict("目标子网或 SD 路由与已有路由重叠，不覆盖既有路由")
        if any(
            item["profile"]["interface"] == profile.interface and item["status"] != "removed"
            for item in self.records()
        ):
            raise EnvironmentConflict("该网卡已有管理记录，请先核对或清理，不重复应用")
        commands: list[list[str]] = []
        if profile.vlan_id is not None:
            commands.append(
                [
                    "link",
                    "add",
                    "link",
                    profile.parent,
                    "name",
                    profile.interface,
                    "type",
                    "vlan",
                    "protocol",
                    "802.1Q",
                    "id",
                    str(profile.vlan_id),
                ]
            )
            commands.append(["link", "set", "dev", profile.interface, "alias", "<自有运行标记>"])
            if profile.mtu is not None:
                commands.append(["link", "set", "dev", profile.interface, "mtu", str(profile.mtu)])
        commands.append(["address", "add", profile.ipv4, "dev", profile.interface, "noprefixroute"])
        if profile.vlan_id is not None:
            commands.append(["link", "set", "dev", profile.interface, "up"])
        for dst in destinations:
            commands.append(["route", "add", dst, "dev", profile.interface, "proto", "static"])
        # 原始环境冻结为预检令牌；明确应用时再次核对，不沿用过期预览。
        token = hashlib.sha256(
            json.dumps(
                [profile.model_dump(), links, routes],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "token": token,
            "profile": profile.model_dump(),
            "commands": commands,
            "parent_index": parent["ifindex"],
            "destinations": destinations,
        }

    def plan(self, profile: NetworkProfile) -> dict[str, Any]:
        with self._lock:
            plan = self._plan(profile)
            logger.info(
                "网卡环境预检完成，未修改系统", extra={"operation": "network.environment.plan"}
            )
            return plan

    def _owned_link(self, record: dict[str, Any]) -> dict[str, Any] | None:
        if record["namespace"] != self._namespace():
            raise EnvironmentConflict("记录来自另一主机启动或网络命名空间，不执行清理")
        links, _ = self._inventory()
        profile = NetworkProfile.model_validate(record["profile"])
        item = next((link for link in links if link["ifname"] == profile.interface), None)
        if item is None:
            return None
        if profile.vlan_id is not None:
            info = item.get("linkinfo", {})
            if (
                item.get("ifalias") != record["marker"]
                or info.get("info_kind") != "vlan"
                or info.get("info_data", {}).get("id") != profile.vlan_id
                or item.get("link_index") != record["parent_index"]
            ):
                logger.error(
                    "VLAN 所有权核对不符：alias_match=%s, link_index=%s, link=%s, info=%s",
                    item.get("ifalias") == record["marker"],
                    item.get("link_index"),
                    item.get("link"),
                    info,
                )
                raise EnvironmentConflict("VLAN 所有权或父网卡已变更，不删除该网卡")
        if record.get("ifindex") is not None and item["ifindex"] != record["ifindex"]:
            raise EnvironmentConflict("网卡标识已经变化，不清理替代网卡")
        return item

    def apply(self, profile: NetworkProfile, token: str) -> dict[str, Any]:
        with self._lock, self._gate.configuration():
            self._permission(profile)
            plan = self._plan(profile)
            if token != plan["token"]:
                raise EnvironmentConflict("环境或配置已变化，请重新预检")
            identifier = str(uuid4())
            record = {
                "id": identifier,
                "profile": profile.model_dump(),
                "marker": "someip-agent:" + identifier,
                "namespace": self._namespace(),
                "parent_index": plan["parent_index"],
                "status": "pending",
                "routes": [],
                "route_snapshots": {},
                "address_added": False,
            }
            self._save(record)
            try:
                if profile.vlan_id is not None:
                    # iproute2/内核创建时可能忽略 alias：先保存意图，再创建并核对索引。
                    # 若进程在设置标记前中断，遗留网卡不具备清理授权，必须人工核对。
                    self._run(*plan["commands"][0])
                    links, _ = self._inventory()
                    created = next(
                        (item for item in links if item["ifname"] == profile.interface), None
                    )
                    if created is None:
                        raise EnvironmentConflict("创建回执未找到 VLAN，不能继续配置")
                    record["ifindex"] = created["ifindex"]
                    self._save(record)
                    self._run("link", "set", "dev", profile.interface, "alias", record["marker"])
                    created = self._owned_link(record)
                    if created is None:
                        raise EnvironmentConflict("VLAN 创建后未找到自有网卡，请明确核对")
                    record["ifindex"] = created["ifindex"]
                    self._save(record)
                    if profile.mtu is not None:
                        self._run("link", "set", "dev", profile.interface, "mtu", str(profile.mtu))
                else:
                    record["ifindex"] = plan["parent_index"]
                    self._save(record)
                self._run("address", "add", profile.ipv4, "dev", profile.interface, "noprefixroute")
                record["address_added"] = True
                self._save(record)
                if profile.vlan_id is not None:
                    self._run("link", "set", "dev", profile.interface, "up")
                for dst in plan["destinations"]:
                    self._run("route", "add", dst, "dev", profile.interface, "proto", "static")
                    record["routes"].append(dst)
                    _, observed_routes = self._inventory()
                    matches = [item for item in observed_routes if item.get("dst") == dst]
                    if len(matches) != 1:
                        raise EnvironmentConflict("新增路由回执不唯一，请明确核对环境")
                    record["route_snapshots"][dst] = matches[0]
                    self._save(record)
                record["status"] = "applied"
                self._save(record)
                logger.info(
                    "自有网卡环境已应用",
                    extra={"operation": "network.environment.apply", "id": identifier},
                )
                return record
            except BaseException:
                logger.exception(
                    "网卡环境应用失败，保留所有权记录",
                    extra={"operation": "network.environment.apply", "id": identifier},
                )
                record["status"] = "failed"
                self._save(record)
                # 不在未知写结果下删除网卡，必须由用户明确核对并清理。
                raise

    def remove(self, identifier: UUID) -> dict[str, Any]:
        with self._lock, self._gate.configuration():
            record = next((item for item in self.records() if item["id"] == str(identifier)), None)
            if record is None or record["status"] == "removed":
                raise EnvironmentConflict("没有可清理的管理记录")
            profile = NetworkProfile.model_validate(record["profile"])
            self._permission(profile)
            if self.settings.native_unicast == str(ipaddress.IPv4Interface(profile.ipv4).ip):
                raise EnvironmentConflict("请先解除原生运行时地址绑定，再清理网卡")
            item = self._owned_link(record)
            if item is not None:
                if profile.vlan_id is not None:
                    foreign = [
                        address
                        for address in item.get("addr_info", [])
                        if address.get("scope") != "link"
                        and f"{address['local']}/{address['prefixlen']}" != profile.ipv4
                    ]
                    _, routes = self._inventory()
                    if foreign or any(
                        route.get("dev") == profile.interface
                        and route != record["route_snapshots"].get(route.get("dst"))
                        for route in routes
                    ):
                        raise EnvironmentConflict("自有 VLAN 上出现额外地址或路由，拒绝整体删除")
                    self._run("link", "delete", "dev", profile.interface, "type", "vlan")
                else:
                    if not record["address_added"] and any(
                        address.get("family") == "inet"
                        and f"{address['local']}/{address['prefixlen']}" == profile.ipv4
                        for address in item.get("addr_info", [])
                    ):
                        raise EnvironmentConflict("地址写入结果未知，不能据此接管或删除地址")
                    _, routes = self._inventory()
                    for dst in record["routes"]:
                        matches = [route for route in routes if route.get("dst") == dst]
                        if matches and (
                            len(matches) != 1 or matches[0] != record["route_snapshots"].get(dst)
                        ):
                            raise EnvironmentConflict("自有路由已变化，拒绝清理")
                    for dst in record["routes"]:
                        if any(route.get("dst") == dst for route in routes):
                            self._run(
                                "route", "del", dst, "dev", profile.interface, "proto", "static"
                            )
                    if record["address_added"] and any(
                        address.get("family") == "inet"
                        and f"{address['local']}/{address['prefixlen']}" == profile.ipv4
                        for address in item.get("addr_info", [])
                    ):
                        self._run("address", "del", profile.ipv4, "dev", profile.interface)
            record["status"] = "removed"
            self._save(record)
            logger.info(
                "自有网卡环境已清理",
                extra={"operation": "network.environment.remove", "id": str(identifier)},
            )
            return record

    def bind(self, identifier: UUID | None) -> dict[str, str]:
        with self._lock, self._gate.configuration():
            if self._active():
                raise EnvironmentConflict("请先停止服务、仿真和监听，再切换原生地址绑定")
            address = "127.0.0.1"
            if identifier is not None:
                record = next(
                    (item for item in self.records() if item["id"] == str(identifier)), None
                )
                if not record or record["status"] != "applied":
                    raise EnvironmentConflict("只能绑定已明确应用的自有配置")
                profile = NetworkProfile.model_validate(record["profile"])
                self._permission(profile)
                item = self._owned_link(record)
                address = str(ipaddress.IPv4Interface(profile.ipv4).ip)
                if not item or not any(
                    info.get("local") == address for info in item.get("addr_info", [])
                ):
                    raise EnvironmentConflict("自有网卡地址已不存在，不能绑定")
            self.settings.native_unicast = address
            logger.info(
                "原生运行时地址绑定已切换，发送授权保持不变",
                extra={"operation": "network.environment.bind"},
            )
            return {"native_unicast": address}
