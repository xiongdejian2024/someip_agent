"""被动抓包验收：独立服务已占用端口，原生进程只从网卡观察双向真实通信。"""

import asyncio
import os
import socket
import time
from pathlib import Path

import pytest
from someip_agent.config import Settings
from someip_agent.domain.models import ListenerConfig
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager

GOLDEN = bytes.fromhex("456780030000000c001100170101020041280000")


async def send_fragments(transport, profile, *extra):
    process = await asyncio.create_subprocess_exec(
        "ip",
        "netns",
        "exec",
        "soa-client",
        "python",
        "/workspace/native/tests/fragment_sender.py",
        transport,
        profile,
        *extra,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
    assert process.returncode == 0, (stdout, stderr)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport,ipv6", [("udp", False), ("tcp", False), ("udp", True), ("tcp", True)]
)
async def test_capture_does_not_bind_service_port(tmp_path, transport, ipv6):
    host = "fd77::1" if ipv6 else "10.77.0.1"
    client = "fd77::2" if ipv6 else "10.77.0.2"
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    manager = NetworkCaptureManager(
        MonitorStore(),
        settings=Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
        ),
    )
    # 此 socket 是测试的独立目标服务，绝不是产品 Python 收包回退。
    service = socket.socket(
        family, socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
    )
    service.bind((host, 30608))
    service.setblocking(False)
    if transport == "tcp":
        service.listen()
    server_task = None
    try:
        interfaces = await manager.interfaces()
        assert any(
            item["name"] == "eth0" and host in item["addresses"] for item in interfaces
        )
        status = await manager.start(
            ListenerConfig(
                mode="pcap",
                capture_interface="eth0",
                capture_filter=f"{transport} port 30608",
            )
        )
        # 无效 BPF 必须明确失败，不中断已经运行的捕获。
        with pytest.raises(ValueError, match="BPF"):
            await manager.start(
                ListenerConfig(
                    mode="pcap", capture_interface="eth0", capture_filter="udp and ((("
                )
            )
        assert manager.list()[0].running
        loop = asyncio.get_running_loop()

        async def respond():
            if transport == "udp":
                body, peer = await loop.sock_recvfrom(service, 4096)
                assert body == GOLDEN * 2
                await loop.sock_sendto(service, GOLDEN, peer)
            else:
                connection, _ = await loop.sock_accept(service)
                connection.setblocking(False)
                try:
                    body = bytearray()
                    while len(body) < len(GOLDEN) * 2:
                        chunk = await loop.sock_recv(connection, 4096)
                        assert chunk, "测试服务收到不完整 TCP 数据"
                        body.extend(chunk)
                    assert bytes(body) == GOLDEN * 2
                    await loop.sock_sendall(connection, GOLDEN)
                finally:
                    connection.close()

        server_task = asyncio.create_task(respond())
        process = await asyncio.create_subprocess_exec(
            "ip",
            "netns",
            "exec",
            "soa-client",
            "python",
            str(Path("/workspace/native/tests/network_sender.py")),
            transport,
            host,
            "30608",
            (GOLDEN * 2).hex(),
            "--interface",
            client,
            "--expect-reply",
            GOLDEN.hex(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, (stdout, stderr)
        await asyncio.wait_for(server_task, 3)
        deadline = time.monotonic() + 3
        while manager.list()[0].received_count < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        messages = await manager._monitor.list()
        assert len(messages) == 3
        assert all(item.payload_hex == "41280000" for item in messages)
        assert all(item.metadata["observation"] == "pcap_capture" for item in messages)
        assert all(
            item.metadata["capture_interface"] == "eth0"
            and item.metadata["wire_verified"]
            for item in messages
        )
        assert all(
            item.metadata["timestamp_source"] == "pcap_software" for item in messages
        )
        server_prefix = f"[{host}]:30608" if ipv6 else f"{host}:30608"
        client_prefix = f"[{client}]:" if ipv6 else f"{client}:"
        assert (
            len(
                [
                    item
                    for item in messages
                    if item.source.startswith(client_prefix)
                    and item.destination == server_prefix
                ]
            )
            == 2
        )
        assert (
            len(
                [
                    item
                    for item in messages
                    if item.source == server_prefix
                    and item.destination.startswith(client_prefix)
                ]
            )
            == 1
        )
        current = manager.list()[0]
        assert current.parse_error_count == 0 and current.running
        assert current.captured_count >= (2 if transport == "udp" else 5)
        assert current.kernel_dropped_count == 0
        await manager.stop(status.id)
        assert not manager.list()[0].running
        assert manager._operator is None
    finally:
        if server_task and not server_task.done():
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
        service.close()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_capture_filter_invalid_udp_and_missing_interface_isolation(tmp_path):
    manager = NetworkCaptureManager(
        MonitorStore(),
        settings=Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
        ),
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as independent:
        independent.bind(("10.77.0.1", 30610))
        status = await manager.start(
            ListenerConfig(
                mode="pcap",
                capture_interface="eth0",
                capture_filter="udp port 30610",
            )
        )
        try:
            operator = manager._operator
            assert await manager.interfaces()
            assert manager._operator is operator
            with pytest.raises(ValueError, match="被动抓包"):
                await manager.start(
                    ListenerConfig(
                        mode="pcap", capture_interface="missing-test-interface"
                    )
                )
            for port, body in ((30610, "00"), (30612, "01"), (30610, GOLDEN.hex())):
                process = await asyncio.create_subprocess_exec(
                    "ip",
                    "netns",
                    "exec",
                    "soa-client",
                    "python",
                    "/workspace/native/tests/network_sender.py",
                    "udp",
                    "10.77.0.1",
                    str(port),
                    body,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
                assert process.returncode == 0, (stdout, stderr)
            deadline = time.monotonic() + 3
            while manager.list()[0].received_count < 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            current = manager.list()[0]
            assert current.running and current.received_count == 1
            assert current.parse_error_count == 1 and current.captured_count == 2
            assert len(await manager._monitor.list()) == 1
            log = next(tmp_path.glob("native-network/*/native.log")).read_text()
            assert '"exception_stack"' in log
            await manager.stop(status.id)
            assert manager._operator is None
        finally:
            await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize(
    "profile",
    [
        "ordered",
        "reordered",
        "duplicate",
        "overlap",
        "missing",
        "options",
        "header_conflict",
    ],
)
async def test_ipv4_fragments_cross_veth_reassemble_or_isolate(
    tmp_path, transport, profile
):
    manager = NetworkCaptureManager(
        MonitorStore(),
        settings=Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
        ),
    )
    try:
        status = await manager.start(
            ListenerConfig(
                mode="pcap",
                capture_interface="eth0",
                capture_filter=f"ip proto {17 if transport == 'udp' else 6} and host 10.77.0.2",
            )
        )
        await send_fragments(transport, profile)
        good = profile in {"ordered", "reordered", "duplicate", "options"}
        rejected = profile in {"overlap", "header_conflict"}
        expected_frames = (
            1
            if profile == "missing"
            else 3
            if profile in {"duplicate", "header_conflict"}
            else 2
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = manager.list()[0]
            if (good and current.received_count) or (
                not good and current.captured_count >= expected_frames
            ):
                break
            await asyncio.sleep(0.01)
        # 异常/残片覆盖完整的新观测窗口，不把缓存中暂时没有消息当成成功。
        if not good:
            await asyncio.sleep(0.1)
        messages = await manager._monitor.list()
        current = manager.list()[0]
        assert current.running and current.captured_count >= expected_frames
        if good:
            assert (
                len(messages)
                == current.received_count
                == current.reassembled_datagrams
                == 1
            )
            assert messages[0].payload_hex == "41280000"
            assert messages[0].source == "10.77.0.2:41014"
            assert messages[0].destination == "10.77.0.1:30614"
            assert messages[0].metadata["ip_reassembled"]
            assert messages[0].metadata["ip_fragment_count"] == 2
            assert (
                current.active_fragment_datagrams
                == current.fragment_buffered_bytes
                == 0
            )
            assert current.fragment_error_count == current.parse_error_count == 0
        else:
            assert (
                not messages
                and current.received_count == current.reassembled_datagrams == 0
            )
            assert current.active_fragment_datagrams == 1
            assert current.fragment_error_count == current.parse_error_count == rejected
            if rejected:
                assert current.fragment_buffered_bytes == 0
                log = next(tmp_path.glob("native-network/*/native.log")).read_text()
                assert ("重叠" if profile == "overlap" else "头长度冲突") in log
                assert '"exception_stack"' in log
            else:
                assert current.fragment_buffered_bytes > 0
        await manager.stop(status.id)
        stopped = manager.list()[0]
        assert not stopped.running
        assert stopped.active_fragment_datagrams == stopped.fragment_buffered_bytes == 0
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_ipv4_missing_fragment_expires_without_new_traffic_and_reuses_id(
    tmp_path,
):
    manager = NetworkCaptureManager(
        MonitorStore(),
        settings=Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
        ),
    )
    try:
        await manager.start(
            ListenerConfig(
                mode="pcap",
                capture_interface="eth0",
                capture_filter="ip proto 17 and host 10.77.0.2",
            )
        )
        await send_fragments("udp", "missing", "--id", "0x77e0")
        deadline = time.monotonic() + 33
        while (
            time.monotonic() < deadline and manager.list()[0].fragment_error_count == 0
        ):
            await asyncio.sleep(0.1)
        current = manager.list()[0]
        assert (
            current.running
            and current.fragment_error_count == current.parse_error_count == 1
        )
        assert current.active_fragment_datagrams == current.fragment_buffered_bytes == 0
        assert current.captured_count == 1 and not await manager._monitor.list()
        assert "30 秒" in current.last_error
        await send_fragments("udp", "ordered", "--id", "0x77e0")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and manager.list()[0].received_count == 0:
            await asyncio.sleep(0.01)
        messages = await manager._monitor.list()
        assert len(messages) == manager.list()[0].reassembled_datagrams == 1
        assert (
            messages[0].payload_hex == "41280000"
            and messages[0].metadata["ip_reassembled"]
        )
        assert manager.list()[0].active_fragment_datagrams == 0
    finally:
        await manager.shutdown()
