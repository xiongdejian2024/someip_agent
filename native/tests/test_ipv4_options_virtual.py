"""真实 veth 上核对选项字节、原生解析/重组与 malformed 隔离；不靠返回值造报文。"""

import asyncio
import os
import time

import pytest
from ipv4_option_vectors import OPTION_CASES
from someip_agent.config import Settings
from someip_agent.domain.models import ListenerConfig
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("fragmented", [False, True])
@pytest.mark.parametrize("profile", OPTION_CASES)
async def test_ipv4_options_normal_and_fragmented_over_veth(
    tmp_path, transport, fragmented, profile
):
    manager = NetworkCaptureManager(
        MonitorStore(),
        settings=Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
        ),
    )
    index = list(OPTION_CASES).index(profile)
    expected_port = 41060 + index + (0 if fragmented else 20)
    valid = OPTION_CASES[profile][1]
    try:
        status = await manager.start(
            ListenerConfig(
                mode="pcap",
                capture_interface="eth0",
                capture_filter=f"ip proto {17 if transport == 'udp' else 6} and src host 10.77.0.2",
            )
        )
        process = await asyncio.create_subprocess_exec(
            "ip",
            "netns",
            "exec",
            "soa-client",
            "python",
            "/workspace/native/tests/ipv4_options_sender.py",
            transport,
            profile,
            *([] if fragmented else ["--unfragmented"]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, (stdout, stderr)
        frames = 2 if fragmented else 1
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = manager.list()[0]
            if current.captured_count >= frames and (
                current.received_count if valid else current.parse_error_count
            ):
                break
            await asyncio.sleep(0.01)
        if not valid:
            await asyncio.sleep(0.1)
        current = manager.list()[0]
        messages = await manager._monitor.list()
        assert current.running and current.captured_count >= frames
        if valid:
            assert len(messages) == current.received_count == 1
            assert current.reassembled_datagrams == int(fragmented)
            assert current.parse_error_count == current.fragment_error_count == 0
            assert (
                current.active_fragment_datagrams
                == current.fragment_buffered_bytes
                == 0
            )
            message = messages[0]
            assert message.payload_hex == "41280000"
            assert (message.service_id, message.method_id, message.message_type) == (
                0x4567,
                0x8003,
                2,
            )
            assert message.source == f"10.77.0.2:{expected_port}"
            assert message.destination == "10.77.0.1:30618"
            assert message.metadata["ip_reassembled"] == fragmented
            assert message.metadata["ip_fragment_count"] == (2 if fragmented else 0)
        else:
            assert (
                not messages
                and current.received_count == current.reassembled_datagrams == 0
            )
            assert current.parse_error_count == 1
            assert current.active_fragment_datagrams == int(fragmented)
            assert current.fragment_error_count == 0
            log = next(tmp_path.glob("native-network/*/native.log")).read_text()
            assert '"exception_stack"' in log and "malformed" in log.lower()
        await manager.stop(status.id)
        stopped = manager.list()[0]
        assert not stopped.running
        assert stopped.active_fragment_datagrams == stopped.fragment_buffered_bytes == 0
    finally:
        await manager.shutdown()
