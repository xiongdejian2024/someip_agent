"""真实共享生命周期门禁的竞争与取消测试；不冒充原生发包验收。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.runtime.network_gate import NetworkTaskConflict, NetworkTaskGate
from someip_agent.state import ApplicationState


def test_thread_writer_blocks_lifecycle_and_releases_on_failure():
    gate = NetworkTaskGate()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="明确失败"):
            with gate.configuration():
                future = pool.submit(lambda: next_operation(gate))
                with pytest.raises(NetworkTaskConflict):
                    future.result(timeout=2)
                raise RuntimeError("明确失败")
    next_operation(gate)
    assert gate.status() == {"lifecycle_operations": 0, "configuring": False}


def next_operation(gate):
    with gate.task_operation():
        assert gate.status()["lifecycle_operations"] == 1


@pytest.mark.parametrize(
    ("manager", "method", "delegate", "arguments"),
    [
        ("services", "start", "_start", (None, None)),
        ("services", "stop", "_stop", ("test",)),
        ("simulator", "start", "_start", (None,)),
        ("simulator", "stop", "_stop", (None,)),
        ("network", "start", "_start", (None,)),
        ("network", "stop", "_stop", (None,)),
        ("network", "interfaces", "_interfaces", ()),
    ],
)
@pytest.mark.asyncio
async def test_pending_manager_lifecycle_blocks_binding_and_cancellation_releases(
    tmp_path, monkeypatch, manager, method, delegate, arguments
):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    entered = asyncio.Event()
    release = asyncio.Event()
    target = getattr(state, manager)

    async def pending(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(target, delegate, pending)
    task = asyncio.create_task(getattr(target, method)(*arguments))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        # 任务仍在初始化、尚未生成 active 状态时，也不能从另一线程切换地址。
        assert not state.has_active_network_tasks()
        with pytest.raises(NetworkTaskConflict):
            await asyncio.to_thread(state.network_environment.bind, None)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert state.network_environment.bind(None) == {"native_unicast": "127.0.0.1"}
    # 原生配置线程持有写入预约时，所有入口均在执行委托前拒绝。
    with state.network_gate.configuration():
        with pytest.raises(NetworkTaskConflict):
            await getattr(target, method)(*arguments)
    assert state.network_gate.status()["lifecycle_operations"] == 0


def test_http_conflict_is_explicit_and_no_unknown_write_retry(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        state = app.state.container
        with state.network_gate.configuration():
            assert client.post(
                "/api/v1/network/environment/binding",
                json={"managed_id": None, "confirm": True},
            ).status_code == 409
            assert client.post("/api/v1/simulation/stop", json={}).status_code == 409
            assert client.post("/api/v1/network/listeners/stop", json={}).status_code == 409
            assert client.get("/api/v1/network/interfaces").status_code == 409
        assert client.post(
            "/api/v1/network/environment/binding", json={"managed_id": None, "confirm": True}
        ).status_code == 200
