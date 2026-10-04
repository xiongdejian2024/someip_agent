"""环境命令的隔离控制测试；模拟命令不冒充 Linux 网卡验收。"""

from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.runtime.network_environment import (
    EnvironmentConflict,
    NetworkEnvironmentManager,
    NetworkProfile,
)
from someip_agent.workbench.projects import ProjectDocument, ProjectRepository, ProjectSave


def profile(**changes):
    return NetworkProfile.model_validate(
        {
            "name": "隔离 VLAN",
            "parent": "test0",
            "interface": "test0.100",
            "vlan_id": 100,
            "ipv4": "10.88.0.2/24",
            "mtu": 1400,
            "sd_multicast": "224.224.224.245",
            **changes,
        }
    )


@pytest.fixture
def manager(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        network_config_enabled=True,
        network_config_interfaces=["test0"],
    )
    state = {
        "links": [
            {
                "ifname": "test0",
                "ifindex": 2,
                "link_type": "ether",
                "mtu": 1500,
                "flags": ["UP"],
                "addr_info": [],
            }
        ],
        "routes": [],
    }
    calls = []
    env = NetworkEnvironmentManager(settings, lambda: False)
    monkeypatch.setattr(env, "_namespace", lambda: "test-namespace")
    monkeypatch.setattr(
        env, "_inventory", lambda: (deepcopy(state["links"]), deepcopy(state["routes"]))
    )

    def command(*args):
        calls.append(args)
        if args[:2] == ("link", "add"):
            state["links"].append(
                {
                    "ifname": args[5],
                    "ifindex": 3,
                    "link_type": "ether",
                    "link_index": 2,
                    "ifalias": "",
                    "mtu": 1500,
                    "flags": [],
                    "linkinfo": {"info_kind": "vlan", "info_data": {"id": 100}},
                    "addr_info": [],
                }
            )
        elif args[:2] == ("link", "set"):
            link = next(item for item in state["links"] if item["ifname"] == args[3])
            if "alias" in args:
                link["ifalias"] = args[5]
            elif "mtu" in args:
                link["mtu"] = int(args[5])
            else:
                link["flags"] = ["UP"]
        elif args[0] == "address":
            link = next(item for item in state["links"] if item["ifname"] == args[4])
            ip, prefix = args[2].split("/")
            address = {"family": "inet", "local": ip, "prefixlen": int(prefix), "scope": "global"}
            if args[1] == "add":
                link["addr_info"].append(address)
            else:
                link["addr_info"].remove(address)
        elif args[:2] == ("route", "add"):
            state["routes"].append({"dst": args[2], "dev": args[4], "protocol": "static"})
        elif args[:2] == ("route", "del"):
            state["routes"] = [item for item in state["routes"] if item["dst"] != args[2]]
        elif args[:2] == ("link", "delete"):
            state["links"] = [item for item in state["links"] if item["ifname"] != args[3]]
            state["routes"] = [item for item in state["routes"] if item["dev"] != args[3]]
        return ""

    monkeypatch.setattr(env, "_run", command)
    return env, state, calls


@pytest.mark.parametrize(
    "changes",
    [
        {"parent": "eth0;touch"},
        {"interface": "../eth0"},
        {"vlan_id": True},
        {"vlan_id": 0},
        {"vlan_id": 4095},
        {"ipv4": "127.0.0.1/8"},
        {"ipv4": "10.88.0.0/24"},
        {"ipv4": "10.88.0.2"},
        {"sd_multicast": "10.88.0.1"},
        {"vlan_id": None},
    ],
)
def test_invalid_inputs(changes):
    with pytest.raises(ValidationError):
        profile(**changes)


def test_preflight_no_writes_and_expired_token(manager):
    env, state, calls = manager
    plan = env.plan(profile())
    assert calls == []
    state["links"][0]["mtu"] = 1490
    with pytest.raises(EnvironmentConflict, match="环境或配置已变化"):
        env.apply(profile(), plan["token"])
    assert calls == []


def test_apply_bind_release_and_owned_cleanup(manager):
    env, state, calls = manager
    receipt = env.apply(profile(), env.plan(profile())["token"])
    assert receipt["status"] == "applied"
    assert calls[0][:2] == ("link", "add")
    assert "alias" in calls[1]
    assert env.bind(UUID(receipt["id"])) == {"native_unicast": "10.88.0.2"}
    assert not env.settings.network_send_enabled
    with pytest.raises(EnvironmentConflict, match="解除"):
        env.remove(UUID(receipt["id"]))
    env.bind(None)
    assert env.remove(UUID(receipt["id"]))["status"] == "removed"
    assert [item["ifname"] for item in state["links"]] == ["test0"]
    assert state["routes"] == []


@pytest.mark.parametrize("change", ["alias", "ifindex", "address", "route", "namespace"])
def test_refuse_foreign_changes(manager, monkeypatch, change):
    env, state, calls = manager
    record = env.apply(profile(), env.plan(profile())["token"])
    if change == "alias":
        state["links"][1]["ifalias"] = "user-owned"
    elif change == "ifindex":
        state["links"][1]["ifindex"] = 4
    elif change == "address":
        state["links"][1]["addr_info"].append(
            {"local": "10.88.0.3", "prefixlen": 24, "scope": "global"}
        )
    elif change == "route":
        state["routes"][0]["metric"] = 50
    else:
        monkeypatch.setattr(env, "_namespace", lambda: "different")
    before = len(calls)
    with pytest.raises(EnvironmentConflict):
        env.remove(UUID(record["id"]))
    assert len(calls) == before


def test_disabled_allowlist_active_and_existing_resources(manager, monkeypatch):
    env, state, calls = manager
    plan = env.plan(profile())
    env.settings.network_config_enabled = False
    with pytest.raises(PermissionError):
        env.apply(profile(), plan["token"])
    env.settings.network_config_enabled = True
    env.settings.network_config_interfaces = []
    with pytest.raises(PermissionError):
        env.apply(profile(), plan["token"])
    env.settings.network_config_interfaces = ["test0"]
    monkeypatch.setattr(env, "_active", lambda: True)
    with pytest.raises(EnvironmentConflict):
        env.apply(profile(), plan["token"])
    assert calls == []
    state["routes"].append({"dst": "10.88.0.0/16", "dev": "foreign"})
    with pytest.raises(EnvironmentConflict, match="重叠"):
        env.plan(profile())


def test_untagged_preserves_existing_address_and_device(manager):
    env, state, calls = manager
    existing = {"family": "inet", "local": "192.168.1.2", "prefixlen": 24, "scope": "global"}
    state["links"][0]["addr_info"].append(existing)
    config = profile(vlan_id=None, interface="test0", mtu=None)
    record = env.apply(config, env.plan(config)["token"])
    env.remove(UUID(record["id"]))
    assert state["links"][0]["addr_info"] == [existing]
    assert not any(args[:2] == ("link", "delete") for args in calls)
    assert all("flush" not in args and "replace" not in args for args in calls)


def test_partial_failure_keeps_record_for_explicit_cleanup(manager, monkeypatch):
    env, state, calls = manager
    original = env._run

    def fail(*args):
        if args[0] == "route":
            raise OSError("测试故障：路由操作失败")
        return original(*args)

    monkeypatch.setattr(env, "_run", fail)
    with pytest.raises(OSError):
        env.apply(profile(), env.plan(profile())["token"])
    assert env.records()[0]["status"] == "failed"
    assert len(state["links"]) == 2
    monkeypatch.setattr(env, "_run", original)
    env.remove(UUID(env.records()[0]["id"]))
    assert len(state["links"]) == 1


def test_api_confirmation_and_project_restart_no_network_writes(tmp_path, monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    doc = ProjectDocument(name="含网络草案", network_profiles=[profile()])
    repo = ProjectRepository(tmp_path / "someip-agent.sqlite3")
    saved = repo.save(ProjectSave(document=doc))
    repo.select(saved.id)
    calls = []
    monkeypatch.setattr(NetworkEnvironmentManager, "_run", lambda *args: calls.append(args))
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert calls == []
        assert (
            client.get("/api/v1/projects/current").json()["document"]["network_profiles"][0][
                "vlan_id"
            ]
            == 100
        )
        assert client.post(f"/api/v1/projects/{saved.id}/open").status_code == 200
        assert calls == []
        for value in (False, 1, "true"):
            response = client.post(
                "/api/v1/network/environment/apply",
                json={
                    "profile": profile().model_dump(),
                    "token": "a" * 64,
                    "confirm": value,
                },
            )
            assert response.status_code == 422
        denied = client.post(
            "/api/v1/network/environment/apply",
            json={
                "profile": profile().model_dump(),
                "token": "a" * 64,
                "confirm": True,
            },
        )
        assert denied.status_code == 403
        assert calls == []
        assert client.get("/api/v1/services/sessions").json() == []


def test_network_profile_duplicate_and_host_authorization_rejected():
    with pytest.raises(ValidationError):
        ProjectDocument(name="冲突", network_profiles=[profile(), profile()])
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate({"name": "权限", "network_config_enabled": True})


def test_explicit_binding_reset_requires_quiet_lifecycle_but_not_iproute_permissions(
    tmp_path, monkeypatch
):
    calls = []

    def unavailable(*args):
        calls.append(args)
        pytest.fail("解除内存地址绑定不能运行系统网络命令")

    monkeypatch.setattr(NetworkEnvironmentManager, "_run", unavailable)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        native_unicast="10.88.0.2",
        network_config_enabled=False,
        network_config_interfaces=[],
        network_send_enabled=True,
        allowed_destinations=["192.0.2.4"],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        state = app.state.container
        endpoint = "/api/v1/network/environment/binding"
        assert client.post(endpoint, json={"managed_id": None, "confirm": False}).status_code == 422
        with state.network_gate.task_operation():
            assert client.post(
                endpoint, json={"managed_id": None, "confirm": True}
            ).status_code == 409
        assert settings.native_unicast == "10.88.0.2"
        response = client.post(endpoint, json={"managed_id": None, "confirm": True})
        assert response.status_code == 200
        assert response.json() == {"native_unicast": "127.0.0.1"}
        assert settings.network_send_enabled is True
        assert settings.allowed_destinations == ["192.0.2.4"]
        assert settings.network_config_enabled is False
        assert settings.network_config_interfaces == []
        assert not state.has_active_network_tasks()
        assert calls == []
