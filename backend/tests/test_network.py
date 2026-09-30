import asyncio
import socket
from datetime import datetime, timezone

import pytest

from someip_agent.domain.models import ListenerConfig, ListenerStatus
from someip_agent.protocol.someip import SomeIpMessage
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager


@pytest.mark.asyncio
async def test_ingest_live_someip_message() -> None:
    monitor = MonitorStore()
    manager = NetworkCaptureManager(monitor)
    listener_id = "test-listener"
    manager._statuses[listener_id] = ListenerStatus(  # noqa: SLF001 - 聚焦解码单测
        id=listener_id,
        config=ListenerConfig(port=30490),
        started_at=datetime.now(timezone.utc),
    )
    wire = SomeIpMessage.build(
        service_id=0x4321,
        method_id=0x8002,
        payload=b"\x01\x02",
        message_type=0x02,
    ).encode()
    await manager.ingest(
        listener_id,
        wire,
        source="192.168.1.2:40000",
        destination="192.168.1.3:30500",
        transport="udp",
    )
    messages = await monitor.list()
    assert len(messages) == 1
    assert messages[0].service_id == 0x4321
    assert manager.list()[0].received_count == 1


@pytest.mark.asyncio
async def test_udp_listener_receives_datagram() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    monitor = MonitorStore()
    manager = NetworkCaptureManager(monitor)
    status = await manager.start(
        ListenerConfig(name="loopback", bind_host="127.0.0.1", port=port)
    )
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        wire = SomeIpMessage.build(
            service_id=0x1000,
            method_id=0x8001,
            payload=b"live",
            message_type=0x02,
        ).encode()
        sender.sendto(wire, ("127.0.0.1", port))
        for _ in range(20):
            if await monitor.list():
                break
            await asyncio.sleep(0.01)
        messages = await monitor.list()
        assert len(messages) == 1
        assert messages[0].payload_hex == b"live".hex()
        assert manager.list()[0].received_count == 1
    finally:
        sender.close()
        await manager.stop(status.id)
