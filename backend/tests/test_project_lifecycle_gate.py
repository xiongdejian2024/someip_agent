"""工程选择与原生启停的真实共享门禁回归；替身委托不冒充原生发包。"""

import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.runtime.network_gate import NetworkTaskConflict
from someip_agent.state import ApplicationState
from someip_agent.workbench.projects import (
    ProjectConflict,
    ProjectDocument,
    ProjectNotFound,
    ProjectRepository,
    ProjectSave,
)


def test_repository_selection_returns_transaction_snapshot_and_missing_preserves_current(
    tmp_path, monkeypatch
):
    repo = ProjectRepository(tmp_path / "projects.sqlite3")
    saved = repo.save(ProjectSave(document=ProjectDocument(name="事务读取工程")))

    def outside_read(*args):
        pytest.fail("选择不能依赖事务之外的另一轮读取")

    monkeypatch.setattr(repo, "get", outside_read)
    selected = repo.select(saved.id)
    assert selected.id == saved.id
    assert selected.document.name == saved.document.name
    with pytest.raises(ProjectNotFound):
        repo.select(uuid4())
    assert repo.current().id == saved.id


def test_http_project_load_conflict_keeps_original_selection(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        state = app.state.container
        saved = state.projects.save(ProjectSave(document=ProjectDocument(name="明确加载")))
        with state.network_gate.configuration():
            response = client.post(f"/api/v1/projects/{saved.id}/open")
        assert response.status_code == 409
        assert state.projects.current() is None
        assert client.post(f"/api/v1/projects/{saved.id}/open").status_code == 200
        assert state.projects.current().id == saved.id
        assert not state.has_active_network_tasks()


@pytest.mark.asyncio
async def test_project_open_rejects_pending_lifecycle_before_active_status(tmp_path, monkeypatch):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    saved = state.projects.save(ProjectSave(document=ProjectDocument(name="待加载工程")))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pending(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(state.services, "_start", pending)
    startup = asyncio.create_task(state.services.start(None, None))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        assert not state.has_active_network_tasks()
        with pytest.raises(ProjectConflict):
            await state.open_project(saved.id)
        assert state.projects.current() is None
    finally:
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup


@pytest.mark.asyncio
async def test_project_open_rejects_resources_without_running_status(tmp_path, monkeypatch):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    saved = state.projects.save(ProjectSave(document=ProjectDocument(name="待加载工程")))
    monkeypatch.setattr(state.network, "has_network_resources", lambda: True)
    assert state.network.list() == []
    with pytest.raises(ProjectConflict):
        await state.open_project(saved.id)
    assert state.projects.current() is None


@pytest.mark.asyncio
async def test_cancelled_project_selection_holds_gate_until_worker_finishes(tmp_path, monkeypatch):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    fixture = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"
    model = ArxmlParser().parse(fixture.read_bytes(), fixture.name)
    saved = state.projects.save(
        ProjectSave(document=ProjectDocument(name="明确选择的工程", model=model))
    )
    entered = threading.Event()
    release = threading.Event()
    original = state.projects.select

    def selection(identifier):
        entered.set()
        assert release.wait(5), "测试必须明确释放选择线程"
        return original(identifier)

    monkeypatch.setattr(state.projects, "select", selection)
    loading = asyncio.create_task(state.open_project(saved.id))
    assert await asyncio.to_thread(entered.wait, 2)
    try:
        with pytest.raises(NetworkTaskConflict):
            state.network_environment.bind(None)
        loading.cancel()
        await asyncio.sleep(0)  # 只让取消事件运行一轮，不等待外部状态。
        assert not loading.done()
        with pytest.raises(NetworkTaskConflict):
            with state.network_gate.task_operation():
                pytest.fail("不能在选择线程尚未完成时允许业务启动")
    finally:
        release.set()
        if not loading.done():
            loading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loading
    assert state.projects.current().id == saved.id
    assert (await state.get_arxml_model()).id == model.id
    assert state.network_gate.status()["configuring"] is False
    assert state.network_environment.bind(None)["native_unicast"] == "127.0.0.1"
