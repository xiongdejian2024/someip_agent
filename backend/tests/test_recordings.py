import hashlib
import json
import time
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.domain.models import MonitorMessage
from someip_agent.main import create_app
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.workbench.recordings import (
    RecordingConflict,
    RecordingManager,
    RecordingNotFound,
    RecordingRequest,
)


def message(index, service=0x1234, payload=""):
    return MonitorMessage(
        service_id=service,
        method_id=0x8001,
        signal_values={"counter": 18446744073709551615, "value": index},
        payload_hex=payload,
    )


async def test_continuous_rotation_integrity_filters_and_restart(tmp_path):
    monitor = MonitorStore(2)
    manager = RecordingManager(tmp_path, monitor)
    started = await manager.start(
        RecordingRequest(segment_bytes=65536, quota_bytes=524288, queue_capacity=512)
    )
    with pytest.raises(RecordingConflict):
        manager.frames(started.id)
    await monitor.publish_many(
        message(i, service=0x1234 if i % 2 else 0x1235, payload="ab" * 700) for i in range(150)
    )
    stopped = await manager.stop(started.id)
    assert stopped.state == "stopped" and stopped.frame_count == 150
    assert len(stopped.segments) >= 4
    assert sum(segment.frame_count for segment in stopped.segments) == 150
    assert stopped.queue_discarded == 0
    assert (await monitor.summary())["total"] == 2, "连续记录不能依赖历史缓冲还保留哪些消息"
    restored = RecordingManager(tmp_path, monitor)
    first = restored.frames(started.id, limit=3)
    assert len(first["frames"]) == 3 and first["next_offset"] == 3
    assert first["frames"][0]["message"]["signal_values"]["counter"] == 18446744073709551615
    assert not first["wire_replay"]
    filtered = restored.frames(started.id, service_id=0x1234, limit=1000)
    assert len(filtered["frames"]) == 75 and filtered["complete"]
    assert all(frame["message"]["service_id"] == 0x1234 for frame in filtered["frames"])
    assert restored.frames(started.id, direction="tx")["frames"] == []
    assert restored.frames(started.id, start_ms=10**9)["complete"]
    body = restored.segment(started.id, 0)
    assert hashlib.sha256(body).hexdigest() == stopped.segments[0].sha256
    path = tmp_path / str(started.id) / "segment-00000.jsonl"
    path.write_bytes(body.replace(b'"value":0', b'"value":9', 1))
    with pytest.raises(ValueError, match="SHA-256"):
        restored.frames(started.id)
    assert (await monitor.statistics())["active_subscribers"] == 0


async def test_queue_overflow_quota_filter_and_shutdown(tmp_path):
    monitor = MonitorStore(2)
    manager = RecordingManager(tmp_path, monitor)
    started = await manager.start(RecordingRequest(queue_capacity=16))
    await monitor.publish_many(message(i) for i in range(100))
    stopped = await manager.stop(started.id)
    assert stopped.frame_count == 16 and stopped.queue_discarded == 84
    quota = await manager.start(
        RecordingRequest(segment_bytes=65536, quota_bytes=65536, queue_capacity=512)
    )
    await monitor.publish_many(message(i, payload="ab" * 1000) for i in range(80))
    result = await manager.stop(quota.id)
    assert result.state == "quota" and result.bytes <= 65536
    assert result.buffered_not_recorded > 0
    selected = await manager.start(RecordingRequest(service_id=0x1234))
    await monitor.publish(message(1, service=0x1235))
    await monitor.publish(message(2))
    await manager.shutdown()
    final = manager.get(selected.id)
    assert final.frame_count == 1 and final.skipped_by_filter == 1
    assert (await monitor.statistics())["active_subscribers"] == 0
    with pytest.raises(RecordingNotFound):
        manager.get(uuid4())


async def test_interruption_and_symlink_are_not_complete_evidence(tmp_path):
    monitor = MonitorStore()
    manager = RecordingManager(tmp_path, monitor)
    view = await manager.start(RecordingRequest())
    await monitor.publish(message(1))
    # 独立目录模拟曾写入一帧但未封存的进程清单，不与活动 writer 争写临时文件。
    recovery = RecordingManager(tmp_path / "interrupted", MonitorStore())
    recovery._directory(view.id).mkdir()
    snapshot = manager.get(view.id)
    snapshot.frame_count = 1
    recovery._persist(snapshot)
    interrupted = recovery.get(view.id)
    assert interrupted.state == "interrupted" and interrupted.unsealed_frames == 1
    assert interrupted.frame_count == 0
    await manager.stop(view.id)
    path = tmp_path / str(view.id) / "segment-00000.jsonl"
    original = path.read_bytes()
    path.unlink()
    other = tmp_path / "outside.jsonl"
    other.write_bytes(original)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="路径"):
        manager.segment(view.id, 0)


def test_api_export_paging_and_no_wire_or_path_controls(tmp_path, monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        started = client.post("/api/v1/recordings", json={"name": "回放测试"})
        assert started.status_code == 200, started.text
        identifier = started.json()["id"]
        client.portal.call(app.state.container.monitor.publish, message(1))
        assert client.get(f"/api/v1/recordings/{identifier}/frames").status_code == 409
        stopped = client.post(f"/api/v1/recordings/{identifier}/stop")
        assert stopped.status_code == 200 and stopped.json()["frame_count"] == 1
        frames = client.get(f"/api/v1/recordings/{identifier}/frames?limit=1")
        assert frames.status_code == 200 and frames.json()["next_offset"] == 1
        assert client.get(f"/api/v1/recordings/{identifier}/frames?limit=1001").status_code == 422
        assert client.get(f"/api/v1/recordings/{identifier}/segments/0").status_code == 200
        exported = client.get(f"/api/v1/recordings/{identifier}/export")
        assert exported.status_code == 200
        with zipfile.ZipFile(BytesIO(exported.content)) as archive:
            assert archive.namelist() == ["manifest.json", "segment-00000.jsonl"]
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["scope"] == "monitor_publish"
            assert (
                hashlib.sha256(archive.read("segment-00000.jsonl")).hexdigest()
                == manifest["segments"][0]["sha256"]
            )
        assert (
            client.post("/api/v1/recordings", json={"output_path": "/tmp/unsafe"}).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/v1/recordings/{identifier}/replay", json={"destination": "192.168.1.1"}
            ).status_code
            == 404
        )
        assert app.state.container.network.list() == []
        assert app.state.container.services.statuses() == []
        assert client.get(f"/api/v1/recordings/{uuid4()}").status_code == 404
        assert UUID(identifier)


def test_actual_native_cycle_is_recorded_independently(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        fixture = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"
        assert (
            client.post(
                "/api/v1/arxml/import", files={"file": (fixture.name, fixture.read_bytes())}
            ).status_code
            == 200
        )
        created = client.post(
            "/api/v1/services/sessions",
            json={
                "application_name": "recording_native",
                "application_id": 0x4A01,
                "members": {
                    "Provider": {"service": "VehicleStatus", "role": "server"},
                    "Consumer": {"service": "VehicleStatus", "role": "client"},
                },
            },
        )
        assert created.status_code == 200, created.text
        session = created.json()
        time.sleep(0.15)
        started = client.post("/api/v1/recordings", json={"name": "真实原生事件证据"}).json()
        command = {
            "member": "Provider_server",
            "function": "UpdateSpeedChangedEvent",
            "args": 42.5,
            "interval_ms": 20,
        }
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        assert client.post(base + "/start", json=command).status_code == 200
        time.sleep(0.15)
        assert client.post(base + "/stop", json={"member": "Provider_server"}).status_code == 200
        stopped = client.post(f"/api/v1/recordings/{started['id']}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["frame_count"] >= 5
        assert client.delete("/api/v1/monitor/messages").status_code == 204
        frames = client.get(f"/api/v1/recordings/{started['id']}/frames").json()["frames"]
        assert any(
            frame["message"]["metadata"]["member"] == "Consumer_client"
            and frame["message"]["payload_hex"] == "422a0000"
            for frame in frames
        )
        assert frames[-1]["offset_ms"] >= frames[0]["offset_ms"]
        assert app.state.container.services.statuses()[0].active
        assert client.post(f"/api/v1/services/sessions/{session['id']}/stop").status_code == 200
