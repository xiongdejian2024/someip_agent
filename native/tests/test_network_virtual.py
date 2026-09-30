"""默认后端在 server 网卡节点控制原生监听，数据来自独立 client 网卡节点。"""

import asyncio
import os
import time
from pathlib import Path

import pytest
from someip_agent.config import Settings
from someip_agent.domain.models import ListenerConfig
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager

# 固定黄金字节，不通过产品编码器构造期望：service=0x4567/event=0x8003/client=17/session=23。
GOLDEN = "456780030000000c001100170101020041280000"
ROOT = Path("/workspace/native/tests")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport,multicast,ipv6",
    [
        ("udp", False, False),
        ("tcp", False, False),
        ("udp", True, False),
        ("udp", False, True),
        ("tcp", False, True),
    ],
)
async def test_native_listener_between_virtual_nics(
    tmp_path, transport, multicast, ipv6
):
    monitor = MonitorStore()
    manager = NetworkCaptureManager(
        monitor,
        settings=Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
        ),
    )
    group = "239.255.77.2" if multicast else None
    host = "fd77::1" if ipv6 else "10.77.0.1"
    status = await manager.start(
        ListenerConfig(
            transport=transport,
            bind_host="0.0.0.0" if multicast else host,
            port=30607,
            multicast_group=group,
            interface_ip="10.77.0.1",
        )
    )
    try:
        process = await asyncio.create_subprocess_exec(
            "ip",
            "netns",
            "exec",
            "soa-client",
            "python",
            str(ROOT / "network_sender.py"),
            transport,
            group or host,
            "30607",
            GOLDEN if transport == "tcp" else GOLDEN * 2,
            "--interface",
            "fd77::2" if ipv6 else "10.77.0.2",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, (stdout, stderr)
        expected = 1 if transport == "tcp" else 2
        deadline = time.monotonic() + 3
        while (
            manager.list()[0].received_count < expected and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.01)
        messages = await monitor.list()
        assert len(messages) == expected
        assert all(item.payload_hex == "41280000" for item in messages)
        assert all(
            item.service_id == 0x4567 and item.method_id == 0x8003 for item in messages
        )
        assert all(
            item.source.startswith("[fd77::2]:" if ipv6 else "10.77.0.2:")
            for item in messages
        )
        assert all(
            item.metadata["wire_verified"] and item.metadata["runtime"] == "vsomeip"
            for item in messages
        )
        assert manager.list()[0].parse_error_count == 0
    finally:
        await manager.stop(status.id)
