"""真实 ARXML 完整动态事件与产品监控，复用既有周期 API 和原生运行时。"""

import copy
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_scenarios import project, request, wait_run
from test_service_cycles import create_session

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.runtime.service_models import EventGeneratorConfig, ServiceCycleCommand
from someip_agent.workbench.projects import CycleDraft, ProjectSave

FIXTURES = Path(__file__).parent / "fixtures"
VALUE = {
    "tag": 7,
    "samples": [0x1234, 0xABCD],
    "bytes": [1, 2],
    "matrix": [[1, 2, 3], [4, 5, 6]],
    "nested": {"temperature": -2},
}
SOURCES = [
    {"path": "/tag", "generator": {"kind": "sequence", "sequence": [7, 8]}},
    {"path": "/samples/1", "generator": {"kind": "sequence", "sequence": [0xABCD, 2]}},
    {"path": "/nested/temperature", "generator": {"kind": "sequence", "sequence": [-2, 3]}},
]


def test_source_contract_preserves_u64_and_rejects_unknown_layout():
    source = {"path": "", "generator": {"initial": 18446744073709551615, "seed": 7}}
    command = ServiceCycleCommand(member="Provider_server", function="Event", sources=[source])
    actual = command.model_dump(mode="json")["sources"][0]["generator"]
    assert type(actual["initial"]) is int and actual["initial"] == 18446744073709551615
    for invalid in (
        {**source, "type": "uint64"},
        {**source, "generator": {"data_type": "uint64"}},
        {**source, "generator": {"seed": True}},
        {**source, "generator": {"minimum": float("inf")}},
        {**source, "generator": {"sequence": [None]}},
    ):
        with pytest.raises(ValidationError):
            ServiceCycleCommand(member="Provider_server", function="Event", sources=[invalid])


@pytest.mark.parametrize(
    "config",
    [
        {"kind": "step", "step_value": 1},
        {"kind": "step", "step_at_ms": 1},
        {"kind": "step", "step_at_ms": True, "step_value": 1},
        {"kind": "step", "step_at_ms": 1.0, "step_value": 1},
        {"kind": "step", "step_at_ms": -1, "step_value": 1},
        {"kind": "step", "step_at_ms": 18446744073709551616, "step_value": 1},
        {"kind": "step", "step_at_ms": 1, "step_value": float("nan")},
        {"kind": "constant", "step_value": 1},
    ],
)
def test_step_contract_rejects_ambiguous_time_and_missing_values(config):
    with pytest.raises(ValidationError):
        EventGeneratorConfig.model_validate(config)


@pytest.mark.parametrize("order,at", [("big", 29), ("little", 29), ("big", 0)])
def test_step_event_shared_millisecond_clock_and_invalid_update(
    tmp_path, native_runtime, order, at
):
    content = (FIXTURES / "composite_service.arxml").read_bytes()
    if order == "little":
        content = content.replace(b"MOST-SIGNIFICANT-BYTE-FIRST", b"MOST-SIGNIFICANT-BYTE-LAST")
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(client, "EnvelopeService", content)
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        sources = [
            {
                "path": "/tag",
                "generator": {"kind": "step", "initial": 9, "step_at_ms": at, "step_value": 10},
            },
            {
                "path": "/nested/temperature",
                "generator": {"kind": "step", "initial": -3, "step_at_ms": at, "step_value": 4},
            },
        ]
        command = {
            "member": keys["server"],
            "function": "UpdateEnvelopeChangedEvent",
            "args": VALUE,
            "interval_ms": 10,
            "sources": sources,
        }
        started = client.post(base + "/start", json=command)
        assert started.status_code == 200, started.text
        deadline = time.monotonic() + 3
        received = []
        while time.monotonic() < deadline:
            received = [
                message
                for message in client.get("/api/v1/monitor/messages").json()
                if message["metadata"]["member"] == keys["client"]
            ]
            if len(received) >= 6:
                break
            time.sleep(0.01)
        assert len(received) >= 6
        before = client.get(base).json()[0]["emitted_count"]
        invalid = copy.deepcopy(sources)
        invalid[0]["generator"]["step_value"] = 256
        assert (
            client.post(base + "/update", json={**command, "sources": invalid}).status_code == 422
        )
        time.sleep(0.03)
        assert client.get(base).json()[0]["emitted_count"] > before
        for index, message in enumerate(received[:6]):
            phase = int(index * 10 >= at)
            values = message["signal_values"]
            assert values["/tag"] == [9, 10][phase]
            assert values["/nested/temperature"] == [-3, 4][phase]
            assert values["/samples/1"] == 0xABCD
            if order == "big":
                golden = [
                    "001b0900041234abcd00020102000a000301020300030405060002fffd",
                    "001b0a00041234abcd00020102000a0003010203000304050600020004",
                ][phase]
            else:
                golden = [
                    "1b000904003412cdab020001020a00030001020303000405060200fdff",
                    "1b000a04003412cdab020001020a000300010203030004050602000400",
                ][phase]
            assert message["payload_hex"] == golden
        stopped = client.post(base + "/stop", json={"member": keys["server"]})
        assert stopped.status_code == 200 and stopped.json()["source_count"] == 0
        count = stopped.json()["emitted_count"]
        time.sleep(0.03)
        assert client.get(base).json()[0]["emitted_count"] == count
        assert client.post(f"/api/v1/services/sessions/{session['id']}/stop").status_code == 200


@pytest.mark.parametrize(
    "order,goldens",
    [
        (
            "big",
            [
                "001b0700041234abcd00020102000a000301020300030405060002fffe",
                "001b0800041234000200020102000a0003010203000304050600020003",
            ],
        ),
        (
            "little",
            [
                "1b000704003412cdab020001020a00030001020303000405060200feff",
                "1b0008040034120200020001020a000300010203030004050602000300",
            ],
        ),
    ],
)
def test_complete_event_paths_share_index_and_preserve_arxml(
    tmp_path, native_runtime, order, goldens
):
    content = (
        (FIXTURES / "composite_service.arxml")
        .read_bytes()
        .replace(b"<EVENT-GROUP-ID>1</EVENT-GROUP-ID>", b"<EVENT-GROUP-ID>7</EVENT-GROUP-ID>")
    )
    if order == "little":
        content = content.replace(b"MOST-SIGNIFICANT-BYTE-FIRST", b"MOST-SIGNIFICANT-BYTE-LAST")
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(client, "EnvelopeService", content)
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        command = {
            "member": keys["server"],
            "function": "UpdateEnvelopeChangedEvent",
            "args": VALUE,
            "interval_ms": 20,
            "sources": SOURCES,
        }
        started = client.post(base + "/start", json=command)
        assert started.status_code == 200, started.text
        assert started.json()["source_count"] == 3 and not started.json()["wire_verified"]
        native_session = app.state.container.services._sessions[session["id"]]
        event = native_session.bundle.catalog["Provider"]["events"][command["function"]]
        assert event["eventgroups"] == [7]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            observed = client.get("/api/v1/monitor/messages").json()
            received = [item for item in observed if item["metadata"]["member"] == keys["client"]]
            if {item["payload_hex"] for item in received} >= set(goldens):
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"未收到两组完整动态事件黄金 payload：{observed}")
        for item in received:
            values = item["signal_values"]
            phase = values["/tag"] - 7
            assert item["payload_hex"] == goldens[phase]
            assert values["/samples/0"] == 0x1234
            assert values["/samples/1"] == [0xABCD, 2][phase]
            assert values["/nested/temperature"] == [-2, 3][phase]
            assert item["direction"] == "sim" and not item["metadata"]["wire_verified"]
            assert not item["metadata"]["signal_values_truncated"]
        before = client.get(base).json()[0]["emitted_count"]
        for path in ("/missing", "/samples/2", "/samples", "/tag/child"):
            bad = {**command, "sources": [{"path": path, "generator": {"initial": 9}}]}
            rejected = client.post(base + "/update", json=bad)
            assert rejected.status_code == 422, rejected.text
        invalid = copy.deepcopy(SOURCES)
        invalid[0]["generator"]["sequence"] = [7, 256]
        assert (
            client.post(base + "/update", json={**command, "sources": invalid}).status_code == 422
        )
        assert (
            client.post(
                base + "/update", json={**command, "sources": SOURCES + [SOURCES[0]]}
            ).status_code
            == 422
        )
        time.sleep(0.06)
        state = client.get(base).json()[0]
        assert state["running"] and state["emitted_count"] > before and state["source_count"] == 3
        # 全局当前模型改变后，会话仍按冻结目录解码，不借新模型猜路径或布局。
        assert (
            client.post(
                "/api/v1/arxml/import",
                files={"file": ("other.arxml", (FIXTURES / "vehicle_service.arxml").read_bytes())},
            ).status_code
            == 200
        )
        fixed = client.post(base + "/update", json={**command, "sources": []})
        assert fixed.status_code == 200 and fixed.json()["source_count"] == 0
        stopped = client.post(base + "/stop", json={"member": keys["server"]})
        assert stopped.status_code == 200 and not stopped.json()["running"]
        count = stopped.json()["emitted_count"]
        time.sleep(0.08)
        assert client.get(base).json()[0]["emitted_count"] == count
        operator = native_session.partner.sim_operator
        assert client.post(f"/api/v1/services/sessions/{session['id']}/stop").status_code == 200
        assert operator.process is None or operator.process.poll() is not None


def test_root_scalar_source_uses_frozen_type(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(
            client, "VehicleStatus", (FIXTURES / "vehicle_service.arxml").read_bytes()
        )
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        started = client.post(
            base + "/start",
            json={
                "member": keys["server"],
                "function": "UpdateSpeedChangedEvent",
                "args": 0,
                "interval_ms": 10,
                "sources": [{"path": "", "generator": {"initial": 0.1}}],
            },
        )
        assert started.status_code == 200, started.text
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            messages = client.get("/api/v1/monitor/messages").json()
            if any(
                item["payload_hex"] == "3dcccccd"
                and item["signal_values"].get("") == 0.10000000149011612
                and item["metadata"]["member"] == keys["client"]
                for item in messages
            ):
                break
            time.sleep(0.01)
        else:
            pytest.fail("根标量激励未经实际 float32 类型量化并交付")


@pytest.mark.parametrize(
    "generator,payload,expected",
    [
        (
            {"kind": "sequence", "sequence": [0.1, 42.5], "seed": 123},
            "3dcccccd",
            0.10000000149011612,
        ),
        ({"kind": "step", "initial": 0.1, "step_at_ms": 29, "step_value": 42.5}, "422a0000", 42.5),
    ],
)
def test_dynamic_sources_project_restart_and_actual_scenario(
    tmp_path, native_runtime, monkeypatch, generator, payload, expected
):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    command = {
        "member": "Provider_server",
        "function": "UpdateSpeedChangedEvent",
        "args": 0,
        "interval_ms": 20,
        "sources": [{"path": "", "generator": generator}],
    }
    with TestClient(create_app(settings)) as client:
        state = client.app.state.container
        saved = project(state)
        document = saved.document.model_copy(deep=True)
        document.cycles = [
            CycleDraft(service_profile="pair", command=ServiceCycleCommand.model_validate(command))
        ]
        saved = state.projects.save(ProjectSave(document=document, expected_revision=1), saved.id)
        opened = client.post(f"/api/v1/projects/{saved.id}/open")
        assert opened.status_code == 200, opened.text
        assert client.get("/api/v1/services/sessions").json() == [], "恢复激励配置不得自动启动"
        definition = request(
            saved,
            [
                {"kind": "start_service", "profile": "pair"},
                {"kind": "wait_ready", "session": "pair"},
                {"kind": "cycle_start", "session": "pair", "command": command},
                {
                    "kind": "wait_message",
                    "session": "pair",
                    "match": {"member": "Consumer_client", "payload_hex": payload},
                    "save_as": "received",
                },
                {
                    "kind": "assert",
                    "path": ["received", "signal_values", ""],
                    "expected": expected,
                },
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
        assert result["steps"][2]["result"]["source_count"] == 1
        inputs = client.get(f"/api/v1/scenarios/runs/{result['id']}/inputs")
        assert inputs.status_code == 200, inputs.text
        sealed = inputs.json()["definition"]["steps"][2]["command"]["sources"][0]["generator"]
        assert all(sealed[key] == value for key, value in generator.items())
        assert not any(item["active"] for item in client.get("/api/v1/services/sessions").json())
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get("/api/v1/projects/current").json()
        assert restored["revision"] == 2
        source = restored["document"]["cycles"][0]["command"]["sources"][0]
        assert all(source["generator"][key] == value for key, value in generator.items())
        assert restarted.get("/api/v1/services/sessions").json() == []
        history = restarted.get("/api/v1/scenarios/runs").json()
        assert len(history) == 1 and history[0]["status"] == "passed", "重启不自动重跑场景"
