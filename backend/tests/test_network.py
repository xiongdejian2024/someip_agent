"""原生端口监听验收；不用 Python 监听器或 mock 代替实际收包。"""

import asyncio
import socket
import struct
import time

import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.domain.models import ListenerConfig
from someip_agent.main import create_app
from someip_agent.protocol.sd import SdEntry, SdPayload
from someip_agent.protocol.someip import SomeIpMessage
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager


def free_port(transport="udp"):
    with socket.socket(
        socket.AF_INET, socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
    ) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wire(payload=b"live"):
    return SomeIpMessage.build(
        service_id=0x4321,
        method_id=0x8002,
        payload=payload,
        client_id=17,
        session_id=23,
        message_type=0x02,
    ).encode()


async def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("原生监听状态未在时限内满足断言")
        await asyncio.sleep(0.01)


@pytest.fixture
async def manager(tmp_path, native_runtime):
    instance = NetworkCaptureManager(
        MonitorStore(),
        settings=Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime),
    )
    try:
        yield instance
    finally:
        await instance.shutdown()


@pytest.mark.asyncio
async def test_udp_decodes_multiple_messages_and_native_metadata(manager):
    port = free_port()
    status = await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(wire() + wire(b"second"), ("127.0.0.1", port))
    await until(lambda: manager.list()[0].received_count == 2)
    messages = await manager._monitor.list()
    assert [item.payload_hex for item in messages] == [b"live".hex(), b"second".hex()]
    assert messages[0].service_id == 0x4321
    assert messages[0].client_id == 17 and messages[0].session_id == 23
    assert messages[0].metadata["runtime"] == "vsomeip"
    assert messages[0].metadata["wire_verified"] is True
    assert messages[0].metadata["observation"] == "socket_receive"
    assert messages[0].destination == f"127.0.0.1:{port}"
    assert status.running


@pytest.mark.asyncio
async def test_zero_length_someip_payload_is_valid(manager):
    port = free_port()
    await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(wire(b""), ("127.0.0.1", port))
    await until(lambda: manager.list()[0].received_count == 1)
    assert (await manager._monitor.list())[0].payload_hex == ""
    assert manager.list()[0].parse_error_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"short",
        wire()[:-1],
        wire()[:4] + b"\xff" * 4 + wire()[8:],
        wire()[:12] + b"\x02" + wire()[13:],
    ],
)
async def test_udp_invalid_packet_does_not_kill_listener(manager, bad):
    port = free_port()
    await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(bad, ("127.0.0.1", port))
        await until(lambda: manager.list()[0].parse_error_count == 1)
        sender.sendto(wire(), ("127.0.0.1", port))
        await until(lambda: manager.list()[0].received_count == 1)
    assert manager.list()[0].running
    assert manager.list()[0].last_error
    log = next(manager._settings.data_dir.glob("native-network/*/native.log")).read_text()
    assert '"exception_stack"' in log


@pytest.mark.asyncio
async def test_tcp_fragmented_and_coalesced_frames(manager):
    port = free_port("tcp")
    await manager.start(ListenerConfig(transport="tcp", bind_host="127.0.0.1", port=port))
    _, sender = await asyncio.open_connection("127.0.0.1", port)
    try:
        data = wire() + wire(b"joined")
        for part in (data[:5], data[5:17], data[17:]):
            sender.write(part)
            await sender.drain()
            await asyncio.sleep(0.01)
        await until(lambda: manager.list()[0].received_count == 2)
        assert [item.payload_hex for item in await manager._monitor.list()] == [
            b"live".hex(),
            b"joined".hex(),
        ]
    finally:
        sender.close()
        await sender.wait_closed()


@pytest.mark.asyncio
async def test_partial_frame_times_out_but_idle_peer_survives(manager):
    port = free_port("tcp")
    await manager.start(ListenerConfig(transport="tcp", bind_host="127.0.0.1", port=port))
    partial_reader, partial = await asyncio.open_connection("127.0.0.1", port)
    _, idle = await asyncio.open_connection("127.0.0.1", port)
    try:
        partial.write(wire()[:1])
        await partial.drain()
        await until(lambda: manager.list()[0].parse_error_count == 1, timeout=12)
        assert "读取超时" in manager.list()[0].last_error
        assert await asyncio.wait_for(partial_reader.read(1), 1) == b""
        idle.write(wire())
        await idle.drain()
        await until(lambda: manager.list()[0].received_count == 1)
        assert manager.list()[0].running
    finally:
        partial.close()
        idle.close()
        await partial.wait_closed()
        await idle.wait_closed()


@pytest.mark.asyncio
async def test_large_tcp_payload_fits_monitor_frame(manager):
    port = free_port("tcp")
    await manager.start(ListenerConfig(transport="tcp", bind_host="127.0.0.1", port=port))
    _, sender = await asyncio.open_connection("127.0.0.1", port)
    payload = bytes(range(256)) * 512
    try:
        sender.write(wire(payload))
        await sender.drain()
        await until(lambda: manager.list()[0].received_count == 1)
        assert (await manager._monitor.list())[0].payload_hex == payload.hex()
    finally:
        sender.close()
        await sender.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["udp", "tcp"])
async def test_ipv6_listener_uses_native_decoder(manager, transport):
    kind = socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET6, kind) as probe:
        probe.bind(("::1", 0))
        port = probe.getsockname()[1]
    await manager.start(ListenerConfig(transport=transport, bind_host="::1", port=port))
    if transport == "udp":
        with socket.socket(socket.AF_INET6, kind) as sender:
            sender.sendto(wire(), ("::1", port))
    else:
        _, sender = await asyncio.open_connection("::1", port)
        sender.write(wire())
        await sender.drain()
        sender.close()
        await sender.wait_closed()
    await until(lambda: manager.list()[0].received_count == 1)
    assert (await manager._monitor.list())[0].source.startswith("[::1]:")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [wire()[:5], wire()[:-1], wire()[:4] + struct.pack("!I", 0xFFFFFFFF) + wire()[8:16]]
)
async def test_tcp_bad_peer_isolated_from_other_connection(manager, bad):
    port = free_port("tcp")
    await manager.start(ListenerConfig(transport="tcp", bind_host="127.0.0.1", port=port))
    _, sender = await asyncio.open_connection("127.0.0.1", port)
    sender.write(bad)
    await sender.drain()
    sender.close()
    await sender.wait_closed()
    await until(lambda: manager.list()[0].last_error is not None)
    _, good = await asyncio.open_connection("127.0.0.1", port)
    try:
        good.write(wire())
        await good.drain()
        await until(lambda: manager.list()[0].received_count == 1)
        assert manager.list()[0].running
    finally:
        good.close()
        await good.wait_closed()


@pytest.mark.asyncio
async def test_multiple_listeners_stop_releases_only_target_port(manager):
    ports = [free_port(), free_port()]
    first = await manager.start(ListenerConfig(bind_host="127.0.0.1", port=ports[0]))
    second = await manager.start(ListenerConfig(bind_host="127.0.0.1", port=ports[1]))
    process = manager._operator.process
    assert not (await manager.stop(first.id))[0].running
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", ports[0]))
        probe.sendto(wire(), ("127.0.0.1", ports[1]))
    await until(lambda: manager.list()[1].received_count == 1)
    assert manager.list()[1].running
    await manager.stop(second.id)
    assert process.poll() == 0
    assert manager._operator is None


@pytest.mark.asyncio
async def test_bind_failure_preserves_running_listener(manager):
    port = free_port()
    await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    with pytest.raises(ValueError):
        await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    assert manager.list()[0].running and not manager.list()[1].running
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(wire(), ("127.0.0.1", port))
    await until(lambda: manager.list()[0].received_count == 1)


@pytest.mark.asyncio
async def test_native_process_exit_marks_failed_and_can_restart(manager):
    port = free_port()
    await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    manager._operator.process.kill()
    await until(lambda: not manager.list()[0].running)
    assert manager.list()[0].last_error
    restarted = await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    assert restarted.running
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(wire(), ("127.0.0.1", port))
    await until(lambda: manager.list()[1].received_count == 1)


@pytest.mark.asyncio
async def test_sd_summary_uses_actual_received_payload(manager):
    port = free_port()
    await manager.start(ListenerConfig(bind_host="127.0.0.1", port=port))
    sd = SdPayload(entries=(SdEntry(1, 0, 0, 0, 0, 0x1234, 1, 1, 3, minor_version=0),))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(sd.to_someip().encode(), ("127.0.0.1", port))
    await until(lambda: manager.list()[0].received_count == 1)
    message = (await manager._monitor.list())[0]
    assert message.is_sd and "OfferService" in message.sd_summary


def test_missing_native_listener_returns_503(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, native_binary="missing-native-listener")
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/network/listeners/start", json={"port": 30501})
        assert response.status_code == 503
        assert not client.get("/api/v1/network/listeners").json()[0]["running"]
