"""原生抓包配置、状态契约与故障隔离；统计契约测试不替代真实网卡验收。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from someip_agent.config import Settings
from someip_agent.domain.models import ListenerConfig, ListenerStatus
from someip_agent.main import create_app
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager


def test_capture_requires_explicit_interface_and_bounded_filter():
    assert ListenerConfig().mode == "socket"
    with pytest.raises(ValidationError, match="明确选择网卡"):
        ListenerConfig(mode="pcap")
    with pytest.raises(ValidationError):
        ListenerConfig(mode="pcap", capture_interface="eth0", capture_filter="")
    with pytest.raises(ValidationError):
        ListenerConfig(mode="pcap", capture_interface="eth0", capture_filter="x" * 4097)
    assert not ListenerConfig(mode="pcap", capture_interface="eth0").promiscuous


def test_capture_missing_binary_and_missing_interface_api(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, native_binary="missing-capture-runtime")
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/network/interfaces").status_code == 503
        response = client.post("/api/v1/network/listeners/start", json={"mode": "pcap"})
        assert response.status_code == 422
        response = client.post(
            "/api/v1/network/listeners/start", json={"mode": "pcap", "capture_interface": "eth0"}
        )
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_capture_fatal_stats_preserve_other_listener_and_unknown_drop_counts():
    manager = NetworkCaptureManager(MonitorStore())
    manager._statuses = {
        name: ListenerStatus(id=name, config=ListenerConfig(), running=True)
        for name in ("capture", "other")
    }
    manager._active = {"capture", "other"}
    operator = MagicMock()
    reader = asyncio.StreamReader()
    task = asyncio.create_task(manager._read_monitor(reader, operator))
    message = {
        "action": "listener_error",
        "listener_id": "capture",
        "running": False,
        "error": "TCP 流资源超限",
        "received_count": 5,
        "parse_error_count": 1,
        "captured_count": 9,
        "kernel_dropped_count": None,
        "interface_dropped_count": None,
        "active_streams": 128,
        "active_fragment_datagrams": 2,
        "fragment_buffered_bytes": 32,
        "reassembled_datagrams": 3,
        "fragment_error_count": 1,
    }
    body = json.dumps(message).encode()
    reader.feed_data(f"{len(body):08x}".encode() + body)
    try:
        await asyncio.sleep(0.02)
        capture, other = manager.list()
        assert not capture.running and other.running
        assert capture.captured_count == 9 and capture.received_count == 5
        assert capture.kernel_dropped_count is None
        assert capture.last_error == "TCP 流资源超限"
        assert (
            capture.active_streams
            == capture.active_fragment_datagrams
            == capture.fragment_buffered_bytes
            == 0
        )
        assert capture.reassembled_datagrams == 3 and capture.fragment_error_count == 1
        assert manager._active == {"other"}
        operator.stop_operator.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_interface_enumeration_keeps_active_runtime():
    manager = NetworkCaptureManager(MonitorStore())
    operator = MagicMock()
    operator.send_request.return_value = [{"name": "eth0", "addresses": ["10.77.0.1"]}]
    manager._active.add("existing")
    manager._ensure_runtime = AsyncMock(return_value=operator)
    manager._close_runtime = AsyncMock()
    assert (await manager.interfaces())[0]["name"] == "eth0"
    manager._close_runtime.assert_not_called()


@pytest.mark.asyncio
async def test_fragment_stats_are_preserved_and_live_resources_clear_on_stop():
    manager = NetworkCaptureManager(MonitorStore())
    manager._statuses["capture"] = ListenerStatus(id="capture", config=ListenerConfig())
    manager._active.add("capture")
    reader = asyncio.StreamReader()
    task = asyncio.create_task(manager._read_monitor(reader, MagicMock()))
    message = {
        "action": "listener_stats",
        "listener_id": "capture",
        "running": True,
        "received_count": 1,
        "parse_error_count": 0,
        "active_fragment_datagrams": 2,
        "fragment_buffered_bytes": 32,
        "reassembled_datagrams": 3,
        "fragment_error_count": 1,
    }
    body = json.dumps(message).encode()
    reader.feed_data(f"{len(body):08x}".encode() + body)
    try:
        await asyncio.sleep(0.02)
        current = manager.list()[0]
        assert current.active_fragment_datagrams == 2 and current.fragment_buffered_bytes == 32
        assert current.reassembled_datagrams == 3 and current.fragment_error_count == 1
        await manager.stop("capture")
        current = manager.list()[0]
        assert (
            not current.running
            and current.active_fragment_datagrams == current.fragment_buffered_bytes == 0
        )
        assert current.reassembled_datagrams == 3 and current.fragment_error_count == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_monitor_retains_native_fragment_provenance():
    packet = {
        "listener_id": "capture",
        "received_at_ns": 1_000_000_000,
        "transport": "udp",
        "source": "10.77.0.2:41014",
        "destination": "10.77.0.1:30614",
        "service_id": 0x4567,
        "method_id": 0x8003,
        "client_id": 0x11,
        "session_id": 0x17,
        "interface_version": 1,
        "message_type": 2,
        "return_code": 0,
        "payload_hex": "41280000",
        "payload_size": 4,
        "is_sd": False,
        "observation": "pcap_capture",
        "ip_reassembled": True,
        "ip_fragment_count": 2,
    }
    message = NetworkCaptureManager._to_monitor_message(packet)
    assert message.metadata["ip_reassembled"] and message.metadata["ip_fragment_count"] == 2
    assert message.metadata["wire_verified"] and message.payload_hex == "41280000"
