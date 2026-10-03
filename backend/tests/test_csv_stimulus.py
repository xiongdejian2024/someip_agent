"""CSV 文本有界预编译、原生冻结类型与工程／场景真实接收。"""

import csv
import io
import json
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_event_stimulus import VALUE
from test_scenarios import project, request, wait_run
from test_service_cycles import create_session

from someip_agent.config import Settings
from someip_agent.domain import csv_stimulus
from someip_agent.domain.csv_stimulus import compile_csv_stimulus
from someip_agent.main import create_app
from someip_agent.runtime.service_models import EventGeneratorConfig, ServiceCycleCommand
from someip_agent.workbench.projects import CycleDraft, ProjectSave

FIXTURES = Path(__file__).parent / "fixtures"
GOLDENS = {
    "big": [
        "001b0900041234abcd00020102000a000301020300030405060002fffd",
        "001b0a00041234000200020102000a0003010203000304050600020004",
    ],
    "little": [
        "1b000904003412cdab020001020a00030001020303000405060200fdff",
        "1b000a040034120200020001020a000300010203030004050602000400",
    ],
}


def test_csv_preserves_scalars_quotes_bom_and_maximum_time():
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["time_ms", "", "/signed", "/flag", "/text", "/float"])
    for at in (0, 2**64 - 1):
        writer.writerow([at, 2**64 - 1, -(2**63), "true", json.dumps('=SUM(1,2)\n中文"'), "0.1"])
    sources = compile_csv_stimulus("\ufeff" + stream.getvalue())
    assert compile_csv_stimulus(None) == []
    values = [source["generator"]["timeline"][0]["value"] for source in sources]
    assert values == [2**64 - 1, -(2**63), True, '=SUM(1,2)\n中文"', 0.1]
    assert [type(value) for value in values] == [int, int, bool, str, float]
    assert sources[0]["path"] == ""
    assert sources[0]["generator"]["timeline"][1]["at_ms"] == 2**64 - 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        "time,/x\n0,1\n",
        "time_ms\n0\n",
        "time_ms,/x,/x\n0,1,2\n",
        "time_ms,x\n0,1\n",
        "time_ms,/x\n",
        "time_ms,/x\n0\n",
        "time_ms,/x\n0,1,2\n",
        'time_ms,/x\n0,"unfinished\n',
        *[f"time_ms,/x\n{clock},1\n" for clock in ("true", "0.0", "-1", "1", str(2**64))],
        "time_ms,/x\n0,1\n0,2\n",
        "time_ms,/x\n0,1\n2,2\n1,3\n",
        *[
            f"time_ms,/x\n0,{cell}\n"
            for cell in (
                "null",
                "[]",
                "{}",
                "NaN",
                "Infinity",
                "1e309",
                "1e-9999",
                str(2**64),
                str(-(2**63) - 1),
                "=SUM(1)",
                "",
            )
        ],
        "time_ms,/" + "x" * 512 + "\n0,1\n",
    ],
)
def test_csv_invalid_inputs_log_full_exception(text, caplog):
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
        compile_csv_stimulus(text)
    assert any(record.exc_info for record in caplog.records), "失败必须记录异常堆栈"


def test_csv_all_resource_limits(monkeypatch):
    for attribute, limit, text in (
        ("CSV_MAX_BYTES", 15, "time_ms,/x\n0,1\n1,2\n"),
        ("CSV_MAX_ROWS", 1, "time_ms,/x\n0,1\n1,2\n"),
        ("CSV_MAX_COLUMNS", 1, "time_ms,/x,/y\n0,1,2\n"),
        ("CSV_MAX_CELL_CHARS", 2, "time_ms,/x\n0,123\n"),
        ("CSV_COMPILED_MAX_BYTES", 80, "time_ms,/x\n0,1\n1,2\n"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(csv_stimulus, attribute, limit)
            with pytest.raises(ValueError):
                compile_csv_stimulus(text)
    assert len(compile_csv_stimulus("time_ms,/x\n" + "".join(f"{i},1\n" for i in range(8192)))) == 1
    with pytest.raises(ValueError, match="8192"):
        compile_csv_stimulus("time_ms,/x\n" + "".join(f"{i},1\n" for i in range(8193)))


def test_csv_public_contract_rejects_overlap_private_kind_and_total_sources():
    base = {"member": "Provider_server", "function": "Event", "csv_text": "time_ms,/x\n0,1\n"}
    command = ServiceCycleCommand.model_validate(base)
    assert command.csv_text == base["csv_text"] and command.sources == []
    for change in (
        {"csv_text": True},
        {"sources": [{"path": "/x", "generator": {}}]},
        {"sources": [{"path": f"/p{i}", "generator": {}} for i in range(128)]},
    ):
        with pytest.raises(ValidationError):
            ServiceCycleCommand.model_validate({**base, **change})
    for config in ({"kind": "csv"}, {"timeline": [{"at_ms": 0, "value": 1}]}):
        with pytest.raises(ValidationError):
            EventGeneratorConfig.model_validate(config)


@pytest.mark.parametrize("order", ["big", "little"])
def test_csv_actual_frozen_event_and_rejected_update_preserves_task(
    tmp_path, native_runtime, order
):
    content = (FIXTURES / "composite_service.arxml").read_bytes()
    if order == "little":
        content = content.replace(b"MOST-SIGNIFICANT-BYTE-FIRST", b"MOST-SIGNIFICANT-BYTE-LAST")
    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    with TestClient(create_app(settings)) as client:
        session, keys = create_session(client, "EnvelopeService", content)
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        text = (FIXTURES / "stimulus_timeline.csv").read_text()
        command = {
            "member": keys["server"],
            "function": "UpdateEnvelopeChangedEvent",
            "args": VALUE,
            "interval_ms": 10,
            "csv_text": text,
        }
        started = client.post(base + "/start", json=command)
        assert started.status_code == 200, started.text
        assert started.json()["source_count"] == 3 and not started.json()["wire_verified"]
        deadline = time.monotonic() + 3
        received = []
        while time.monotonic() < deadline:
            received = [
                item
                for item in client.get("/api/v1/monitor/messages").json()
                if item["metadata"]["member"] == keys["client"]
            ]
            if len(received) >= 6:
                break
            time.sleep(0.01)
        assert len(received) >= 6
        for index, message in enumerate(received[:6]):
            phase = int(index * 10 >= 29)
            assert message["payload_hex"] == GOLDENS[order][phase]
            assert message["signal_values"]["/tag"] == [9, 10][phase]
            assert message["signal_values"]["/nested/temperature"] == [-3, 4][phase]
            assert message["signal_values"]["/samples/1"] == [0xABCD, 2][phase]
        before = client.get(base).json()[0]["emitted_count"]
        for bad in (
            "time_ms,/tag\n0,9\n29,256\n",
            "time_ms,/tag\n0,9\n29,10.0\n",
            "time_ms,/missing\n0,9\n",
            "time_ms,/tag\n0,9\n0,10\n",
        ):
            rejected = client.post(base + "/update", json={**command, "csv_text": bad})
            assert rejected.status_code == 422, rejected.text
        assert (
            client.post(
                base + "/update",
                json={**command, "sources": [{"path": "/tag", "generator": {"initial": 9}}]},
            ).status_code
            == 422
        )
        time.sleep(0.03)
        status = client.get(base).json()[0]
        assert (
            status["running"] and status["source_count"] == 3 and status["emitted_count"] > before
        )
        partner = client.app.state.container.services._sessions[session["id"]].partner
        assert partner.partner_infos[keys["server"]].cycle_config["csv_text"] == text
        mixed = {
            **command,
            "csv_text": "time_ms,/tag,/nested/temperature\n0,9,-3\n29,10,4\n",
            "sources": [{"path": "/samples/1", "generator": {"initial": 2}}],
        }
        updated = client.post(base + "/update", json=mixed)
        assert updated.status_code == 200 and updated.json()["source_count"] == 3, updated.text
        fixed = client.post(base + "/update", json={**command, "csv_text": None})
        assert fixed.status_code == 200 and fixed.json()["source_count"] == 0
        stopped = client.post(base + "/stop", json={"member": keys["server"]})
        assert stopped.status_code == 200 and stopped.json()["source_count"] == 0
        count = stopped.json()["emitted_count"]
        time.sleep(0.03)
        assert client.get(base).json()[0]["emitted_count"] == count
        assert client.post(f"/api/v1/services/sessions/{session['id']}/stop").status_code == 200


def test_csv_project_restart_and_actual_scenario(tmp_path, native_runtime, monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    text = "time_ms,\n0,0.1\n29,42.5\n"
    command = {
        "member": "Provider_server",
        "function": "UpdateSpeedChangedEvent",
        "args": 0,
        "interval_ms": 20,
        "csv_text": text,
    }
    with TestClient(create_app(settings)) as client:
        state = client.app.state.container
        saved = project(state)
        document = saved.document.model_copy(deep=True)
        document.cycles = [
            CycleDraft(service_profile="pair", command=ServiceCycleCommand.model_validate(command))
        ]
        saved = state.projects.save(ProjectSave(document=document, expected_revision=1), saved.id)
        exported = client.get(f"/api/v1/projects/{saved.id}/export")
        assert exported.status_code == 200
        assert exported.json()["cycles"][0]["command"]["csv_text"] == text
        imported = client.post("/api/v1/projects/import", json=exported.json())
        assert imported.status_code == 200 and imported.json()["id"] != str(saved.id)
        assert imported.json()["document"]["cycles"][0]["command"]["csv_text"] == text
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
        assert result["steps"][2]["result"]["source_count"] == 1
        inputs = client.get(f"/api/v1/scenarios/runs/{result['id']}/inputs").json()
        assert inputs["definition"]["steps"][2]["command"]["csv_text"] == text
        assert not any(item["active"] for item in client.get("/api/v1/services/sessions").json())
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get("/api/v1/projects/current").json()
        assert restored["revision"] == 2
        assert restored["document"]["cycles"][0]["command"]["csv_text"] == text
        assert restarted.get("/api/v1/services/sessions").json() == []
        history = restarted.get("/api/v1/scenarios/runs").json()
        assert len(history) == 1 and history[0]["status"] == "passed"
