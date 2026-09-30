"""SAT 成员命名与原生控制键隔离，避免将内部 alias 泄漏为调用方键名。"""

from __future__ import annotations

import re
from collections.abc import Iterator, MutableMapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .info import PartnerKeyInfo


class PartnerRegistry(MutableMapping[str, "PartnerKeyInfo"]):
    """迭代只返回 SAT 标准键；旧版内部键作为查找别名，不重复拥有 socket。"""

    def __init__(self) -> None:
        self._items: dict[str, PartnerKeyInfo] = {}
        self._aliases: dict[str, str] = {}

    def canonical(self, key: str) -> str:
        return self._aliases.get(key, key)

    def add_alias(self, alias: str, key: str) -> None:
        if alias != key:
            if alias in self._items or (alias in self._aliases and self._aliases[alias] != key):
                raise ValueError(f"SOA 成员别名冲突: {alias}")
            self._aliases[alias] = key

    def __getitem__(self, key: str) -> PartnerKeyInfo:
        return self._items[self.canonical(key)]

    def __setitem__(self, key: str, value: PartnerKeyInfo) -> None:
        self._items[self.canonical(key)] = value

    def __delitem__(self, key: str) -> None:
        canonical = self.canonical(key)
        del self._items[canonical]
        self._aliases = {
            alias: value for alias, value in self._aliases.items() if value != canonical
        }

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


def members_config(members: Any) -> dict[str, Any]:
    if isinstance(members, dict):
        return members
    config: dict[str, Any] = {}
    for item in members:
        if isinstance(item, str):
            match = re.fullmatch(r"(.+)_(client(?:_\d+)?|server)(?:_(.+))?", item)
            if match is None:
                raise ValueError(f"SOA 成员字符串缺少 client/server 角色: {item}")
            service, role, instance = match.groups()
            item = (service, role, instance or service)
        if not isinstance(item, (tuple, list)) or not 2 <= len(item) <= 4:
            raise ValueError("SOA 成员需要服务、角色、可选实例名和心跳的 2 至 4 项元组")
        service, role = item[:2]
        if not isinstance(service, str) or not service:
            raise ValueError("SOA 服务名不能为空")
        if not isinstance(role, str) or re.fullmatch(r"client(?:_\d+)?|server", role) is None:
            raise ValueError("SOA 角色必须为 client、client_编号或 server")
        instance = item[2] if len(item) > 2 else service
        heartbeat = item[3] if len(item) > 3 else 600
        base_role, _, index = role.partition("_")
        alias = service + ("_" + index if index else "")
        if alias in config:
            if config[alias]["role"] == base_role:
                raise ValueError(f"SOA 成员重复: {service}_{role}")
            alias = f"{service}_{base_role}"
        if alias in config:
            raise ValueError(f"SOA 成员重复: {service}_{role}")
        config[alias] = {
            "service": service,
            "role": base_role,
            "name": instance,
            "status": True,
            "heartbeat": heartbeat,
        }
    return config


def member_key(alias: str, config: dict[str, Any]) -> str:
    service = config.get("service", alias)
    role = config["role"]
    name = config.get("name", service)
    if alias in {service, f"{service}_{role}"}:
        key = f"{service}_{role}"
    elif alias.startswith(service + "_") and alias[len(service) + 1 :].isdigit():
        key = f"{service}_{role}_{alias[len(service) + 1 :]}"
    else:
        key = f"{alias}_{role}"
    # 与 SAT 一致，编号客户端以编号区分，不附加实例名称。
    if name != service and not re.search(r"_client_\d+$", key):
        key += "_" + name
    return key
