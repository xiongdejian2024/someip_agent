"""专用 --network none + NET_ADMIN 容器的真实 VLAN/地址/路由验收。"""

import json
import logging
import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
from someip_agent.config import Settings
from someip_agent.runtime.network_environment import (
    EnvironmentConflict,
    NetworkEnvironmentManager,
    NetworkProfile,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SOMEIP_AGENT_NETWORK_ENV_TEST") != "1",
    reason="需要显式隔离的网卡环境验收容器，不能在宿主运行",
)
logger = logging.getLogger(__name__)


def ip(*args):
    return subprocess.run(
        ["ip", *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def environment(tmp_path):
    assert Path("/.dockerenv").exists(), "网卡验收只能在专用临时容器运行"
    baseline = {item["ifname"] for item in json.loads(ip("-j", "link", "show"))}
    # Docker 内核可默认带这两个未启用隧道，不将它们误认为外部以太网。
    assert baseline <= {"lo", "tunl0", "ip6tnl0"}
    ip("link", "add", "envparent0", "type", "dummy")
    ip("link", "set", "envparent0", "up")
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        network_config_enabled=True,
        network_config_interfaces=["envparent0"],
    )
    manager = NetworkEnvironmentManager(settings, lambda: False)
    yield manager, settings
    ip("link", "delete", "envparent0")


def config(**changes):
    return NetworkProfile.model_validate(
        {
            "name": "容器 VLAN 验收",
            "parent": "envparent0",
            "interface": "envparent0.100",
            "vlan_id": 100,
            "ipv4": "10.88.0.2/24",
            "mtu": 1400,
            "sd_multicast": "224.224.224.245",
            **changes,
        }
    )


def test_real_vlan_alias_routes_restart_binding_and_cleanup(environment):
    manager, settings = environment
    try:
        record = manager.apply(config(), manager.plan(config())["token"])
    except EnvironmentConflict:
        logger.exception("真实链路原始诊断：%s", ip("-j", "-d", "link", "show"))
        raise
    interface = next(
        item
        for item in manager.status()["interfaces"]
        if item["ifname"] == "envparent0.100"
    )
    assert interface["linkinfo"]["info_data"]["id"] == 100
    assert interface["ifalias"] == record["marker"]
    assert interface["mtu"] == 1400
    assert "UP" in interface["flags"]
    assert manager.bind(UUID(record["id"]))["native_unicast"] == "10.88.0.2"
    manager.bind(None)
    restarted = NetworkEnvironmentManager(settings, lambda: False)
    assert restarted.records()[0]["status"] == "applied"
    assert restarted.remove(UUID(record["id"]))["status"] == "removed"
    assert {item["ifname"] for item in restarted.status()["interfaces"]} <= {
        "lo",
        "tunl0",
        "ip6tnl0",
        "envparent0",
    }
    assert restarted.status()["routes"] == []


def test_real_untagged_keeps_foreign_address(environment):
    manager, _ = environment
    ip("address", "add", "192.168.60.2/24", "dev", "envparent0")
    profile = config(interface="envparent0", vlan_id=None, mtu=None)
    record = manager.apply(profile, manager.plan(profile)["token"])
    manager.remove(UUID(record["id"]))
    item = next(
        link
        for link in manager.status()["interfaces"]
        if link["ifname"] == "envparent0"
    )
    assert [
        address["local"] for address in item["addr_info"] if address["family"] == "inet"
    ] == ["192.168.60.2"]


def test_real_foreign_vlan_and_alias_not_removed(environment):
    manager, _ = environment
    ip(
        "link",
        "add",
        "link",
        "envparent0",
        "name",
        "envparent0.100",
        "type",
        "vlan",
        "id",
        "100",
    )
    with pytest.raises(EnvironmentConflict, match="已经存在"):
        manager.plan(config())
    ip("link", "delete", "envparent0.100")
    record = manager.apply(config(), manager.plan(config())["token"])
    ip("link", "set", "envparent0.100", "alias", "user-owned")
    with pytest.raises(EnvironmentConflict, match="所有权"):
        manager.remove(UUID(record["id"]))
    assert "envparent0.100" in [
        link["ifname"] for link in manager.status()["interfaces"]
    ]
