"""时间状态图契约、真实原生事件和工程／场景恢复验收。"""

import copy
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_agent_evidence import agent, invoke
from test_csv_stimulus import FIXTURES
from test_event_stimulus import VALUE
from test_scenarios import project, request, wait_run
from test_service_cycles import create_session

from someip_agent.config import Settings
from someip_agent.domain.models import SignalGeneratorConfig
from someip_agent.main import create_app
from someip_agent.runtime.service_models import EventGeneratorConfig, ServiceCycleCommand
from someip_agent.workbench.projects import CycleDraft, ProjectSave


def graph(first=9, second=10, duration=29):
    return {
        "kind": "state_machine",
        "initial_state": "idle",
        "states": [
            {"name": "idle", "value": first, "duration_ms": duration, "next": "active"},
            {"name": "active", "value": second},
        ],
    }


@pytest.mark.parametrize("model", [EventGeneratorConfig, SignalGeneratorConfig])
def test_graph_preserves_exact_scalars_time_and_cycles(model):
    config = model.model_validate(graph(2**64 - 1, -(2**63), 2**64 - 1))
    assert type(config.states[0].value) is int and config.states[0].value == 2**64 - 1
    assert config.states[1].value == -(2**63)
    assert config.states[0].duration_ms == 2**64 - 1
    cyclic = graph()
    cyclic["states"][1].update(duration_ms=5, next="idle")
    model.model_validate(cyclic)
    cyclic["states"].append({"name": "prefix", "value": 0, "duration_ms": 2, "next": "idle"})
    cyclic["initial_state"] = "prefix"
    model.model_validate(cyclic)
    model.model_validate(
        {
            "kind": "state_machine",
            "initial_state": "hold",
            "states": [
                {
                    "name": "hold",
                    "value": True,
                    "duration_ms": 1,
                    "next": "hold",
                }
            ],
        }
    )
    assert model().states == [] and model().initial_state is None


def invalid_graphs():
    for field in ("name", "value", "duration_ms", "next"):
        value = graph()
        del value["states"][0][field]
        yield value
    for duration in (0, -1, True, 1.5, 2**64):
        yield graph(duration=duration)
    for field, scalar in (
        ("name", "无效"),
        ("name", "bad name"),
        ("name", "a" * 65),
        ("next", "missing"),
        ("duration_ms", None),
        ("value", float("inf")),
        ("value", {}),
        ("script", "exec"),
    ):
        value = graph()
        value["states"][0][field] = scalar
        yield value
    for extra in ({"name": "idle", "value": 0}, {"name": "unused", "value": 0}):
        value = graph()
        value["states"].append(extra)
        yield value
    value = graph()
    value["states"][1]["duration_ms"] = 1
    yield value
    value = graph(duration=2**64 - 1)
    value["states"][1].update(duration_ms=1, next="idle")
    yield value
    yield {**graph(), "initial_state": "missing"}
    yield {**graph(), "states": []}
    yield {**graph(), "states": [{"name": f"s{i}", "value": 0} for i in range(129)]}
    yield {**graph(), "kind": "constant"}


@pytest.mark.parametrize("config", list(invalid_graphs()))
@pytest.mark.parametrize("model", [EventGeneratorConfig, SignalGeneratorConfig])
def test_invalid_graph_rejected_before_execution(model, config):
    with pytest.raises(ValidationError):
        model.model_validate(config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,allowed", [(0, True), (100, True), (101, False), (-1, False), ("1", False)]
)
async def test_agent_state_values_use_arxml_range_and_prepare_does_not_run(
    tmp_path, value, allowed
):
    subject = agent(tmp_path)
    result = await invoke(
        subject,
        "prepare_simulation",
        service_id=0x1234,
        method_id=0x8001,
        **graph(0, value, 2**64 - 1),
    )
    if allowed:
        assert result["status"] == "prepared"
        actual = result["simulation_config"]["generator"]
        assert actual["states"][0]["duration_ms"] == 2**64 - 1
        assert actual["states"][1]["value"] == value
    else:
        assert "error" in result
    assert subject._simulator.list() == []


GOLDENS = {
    "big": [
        "001b0900041234abcd00020102000a000301020300030405060002fffd",
        "001b0a00041234abcd00020102000a0003010203000304050600020004",
    ],
    "little": [
        "1b000904003412cdab020001020a00030001020303000405060200fdff",
        "1b000a04003412cdab020001020a000300010203030004050602000400",
    ],
}


@pytest.mark.parametrize("order", ["big", "little"])
def test_actual_frozen_event_states_and_invalid_update_preserves_cycle(
    tmp_path, native_runtime, order
):
    content = (FIXTURES / "composite_service.arxml").read_bytes()
    if order == "little":
        content = content.replace(b"MOST-SIGNIFICANT-BYTE-FIRST", b"MOST-SIGNIFICANT-BYTE-LAST")
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    ) as client:
        session, keys = create_session(client, "EnvelopeService", content)
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        command = {
            "member": keys["server"],
            "function": "UpdateEnvelopeChangedEvent",
            "args": VALUE,
            "interval_ms": 10,
            "sources": [
                {"path": "/tag", "generator": graph()},
                {"path": "/nested/temperature", "generator": graph(-3, 4)},
            ],
        }
        started = client.post(base + "/start", json=command)
        assert started.status_code == 200, started.text
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            messages = client.get("/api/v1/monitor/messages").json()
            received = [item for item in messages if item["metadata"]["member"] == keys["client"]]
            if len(received) >= 6:
                break
            time.sleep(0.01)
        assert len(received) >= 6
        for index, message in enumerate(received[:6]):
            assert message["payload_hex"] == GOLDENS[order][int(index * 10 >= 29)]
            assert message["metadata"]["active_states"] == {}, "接收者不得从线上猜测状态名"
        transmitted = [item for item in messages if item["metadata"]["member"] == keys["server"]]
        assert transmitted[0]["metadata"]["active_states"] == {
            "/tag": "idle",
            "/nested/temperature": "idle",
        }
        assert any(
            item["metadata"]["active_states"] == {"/tag": "active", "/nested/temperature": "active"}
            for item in transmitted
        )
        before = client.get(base).json()[0]
        assert before["active_states"] == {"/tag": "active", "/nested/temperature": "active"}
        for value in (256, 10.0, True):
            invalid = copy.deepcopy(command)
            invalid["sources"][0]["generator"]["states"][1]["value"] = value
            rejected = client.post(base + "/update", json=invalid)
            assert rejected.status_code == 422, rejected.text
        time.sleep(0.03)
        current = client.get(base).json()[0]
        assert current["running"] and current["emitted_count"] > before["emitted_count"]
        mixed = {
            **command,
            "sources": command["sources"][:1],
            "csv_text": "time_ms,/nested/temperature\n0,-3\n29,4\n",
        }
        assert client.post(base + "/update", json=mixed).status_code == 200
        stopped = client.post(base + "/stop", json={"member": keys["server"]}).json()
        assert (
            not stopped["running"]
            and stopped["source_count"] == 0
            and stopped["active_states"] == {}
        )
        time.sleep(0.03)
        assert client.get(base).json()[0]["emitted_count"] == stopped["emitted_count"]


def test_state_project_import_restart_and_actual_scenario(tmp_path, native_runtime, monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    command = {
        "member": "Provider_server",
        "function": "UpdateSpeedChangedEvent",
        "args": 0,
        "interval_ms": 20,
        "sources": [{"path": "", "generator": graph(0.1, 42.5)}],
    }
    with TestClient(create_app(settings)) as client:
        state = client.app.state.container
        saved = project(state)
        document = saved.document.model_copy(deep=True)
        document.cycles = [
            CycleDraft(service_profile="pair", command=ServiceCycleCommand.model_validate(command))
        ]
        saved = state.projects.save(ProjectSave(document=document, expected_revision=1), saved.id)
        exported = client.get(f"/api/v1/projects/{saved.id}/export").json()
        sources = exported["cycles"][0]["command"]["sources"]
        assert sources[0]["generator"]["states"][1]["value"] == 42.5
        imported = client.post("/api/v1/projects/import", json=exported)
        assert imported.status_code == 200 and imported.json()["id"] != str(saved.id)
        assert imported.json()["document"]["cycles"][0]["command"]["sources"] == sources
        assert client.post(f"/api/v1/projects/{saved.id}/open").status_code == 200
        assert client.get("/api/v1/services/sessions").json() == []
        definition = request(
            saved,
            [
                {"kind": "start_service", "profile": "pair"},
                {"kind": "wait_ready", "session": "pair"},
                {"kind": "cycle_start", "session": "pair", "command": command},
                {
                    "kind": "wait_message",
                    "session": "pair",
                    "match": {"member": "Consumer_client", "payload_hex": "422a0000"},
                    "save_as": "received",
                },
                {"kind": "assert", "path": ["received", "signal_values", ""], "expected": 42.5},
            ],
            cleanup=[
                {"kind": "cycle_stop", "session": "pair", "command": {"member": "Provider_server"}}
            ],
        )
        started = client.post(
            "/api/v1/scenarios/runs", json=definition.model_dump(mode="json", exclude_unset=True)
        )
        assert started.status_code == 202, started.text
        result = wait_run(client, started.json()["id"])
        assert result["status"] == "passed" and result["cleanup_complete"], result
        frozen = client.get(f"/api/v1/scenarios/runs/{result['id']}/inputs").json()
        assert frozen["definition"]["steps"][2]["command"]["sources"] == command["sources"]
        assert not any(item["active"] for item in client.get("/api/v1/services/sessions").json())
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get("/api/v1/projects/current").json()
        assert restored["document"]["cycles"][0]["command"]["sources"] == sources
        assert restarted.get("/api/v1/services/sessions").json() == []
        assert restarted.get("/api/v1/scenarios/runs").json()[0]["status"] == "passed"
