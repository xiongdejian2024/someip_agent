import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.domain.models import MonitorMessage
from someip_agent.main import create_app
from someip_agent.runtime.monitor import MonitorStore


def message(index):
    return MonitorMessage(service_id=0x1234, method_id=0x8001, metadata={"index": index})


@pytest.mark.asyncio
async def test_each_subscriber_and_history_have_independent_counters():
    store = MonitorStore(2)
    slow = await store.subscribe(1)
    fast = await store.subscribe(10)
    for index in range(4):
        await store.publish(message(index))
    assert slow.get_nowait().metadata["index"] == 3
    assert fast.qsize() == 4
    slow_stats = await store.statistics(slow)
    assert slow_stats["published_total"] == 4
    assert slow_stats["retained_messages"] == 2
    assert slow_stats["history_evicted_total"] == 2
    assert slow_stats["subscriber_discarded_total"] == 3
    assert slow_stats["current_subscriber_discarded"] == 3
    assert (await store.statistics(fast))["current_subscriber_discarded"] == 0
    assert (await store.summary())["stream_counters"]["history_evicted_total"] == 2
    await store.clear()
    cleared = await store.statistics(slow)
    assert cleared["cleared_total"] == 2
    assert cleared["retained_messages"] == 0
    assert cleared["history_evicted_total"] == 2
    assert cleared["subscriber_discarded_total"] == 3
    await store.unsubscribe(slow)
    reconnected = await store.subscribe(1)
    stats = await store.statistics(reconnected)
    assert stats["current_subscriber_discarded"] == 0
    assert stats["subscriber_discarded_total"] == 3
    assert stats["backend_epoch"] == slow_stats["backend_epoch"]
    assert stats["backend_epoch"] != (await MonitorStore().statistics())["backend_epoch"]
    await store.unsubscribe(fast)
    await store.unsubscribe(reconnected)
    assert not store._subscriber_discards


@pytest.mark.asyncio
async def test_unbounded_subscription_is_rejected():
    store = MonitorStore()
    with pytest.raises(ValueError, match="无界"):
        await store.subscribe(0)
    assert (await store.statistics())["subscriber_discarded_total"] == 0
    assert (await store.statistics())["current_subscriber_discarded"] is None


def test_websocket_and_summary_report_counters(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key=""))
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/monitor/ws") as socket:
            data = socket.receive_json()
            counters = data["stream_counters"]
            assert data["type"] == "snapshot"
            assert counters["current_subscriber_discarded"] == 0
            assert counters["active_subscribers"] == 1
            response = client.get("/api/v1/monitor/summary").json()
            assert response["stream_counters"]["backend_epoch"] == counters["backend_epoch"]
            assert response["stream_counters"]["current_subscriber_discarded"] is None
            assert client.portal is not None
            client.portal.call(app.state.container.monitor.publish, message(7))
            update = socket.receive_json()
            assert update["type"] == "message"
            assert update["message"]["metadata"]["index"] == 7
            assert update["stream_counters"]["published_total"] == 1
        summary = client.get("/api/v1/monitor/summary").json()
        assert summary["stream_counters"]["active_subscribers"] == 0
