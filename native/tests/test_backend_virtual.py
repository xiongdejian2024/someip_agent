"""在 server netns 内运行真正的默认后端，与另一网卡节点的原生 client 互通。"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from someip_agent.config import Settings
from someip_agent.domain.models import SignalGeneratorConfig, SimulationConfig
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.simulator import SimulationManager
from someip_agent.soa.operator import SOAOperator
from someip_agent.soa.partner import S2sBaseClass

ROOT = Path("/workspace/native/tests")


@pytest.fixture
def consumer(tmp_path):
    catalog = json.loads((ROOT / "catalog.json").read_text())
    catalog["DoorService"]["events"]["UpdateSampleEvent"]["eventgroups"] = [1]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    with (tmp_path / "client.log").open("wb") as log:
        process = subprocess.Popen(
            [
                "ip",
                "netns",
                "exec",
                "soa-client",
                os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                "run",
                "--name",
                "client",
                "--bind",
                "10.77.0.2",
                "-p",
                "16789",
                "--catalog",
                str(catalog_path),
                "--config",
                str(ROOT / "client.json"),
            ],
            stdout=log,
            stderr=log,
        )
        client = None
        try:
            client = S2sBaseClass(
                {"DoorService": {"role": "client"}},
                operator=SOAOperator("client", host="10.77.0.2"),
                attach=True,
            )
            yield client
        finally:
            if client:
                client.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_backend_native_provider_and_subscriber_policy(
    tmp_path, consumer, authorized
):
    host = "10.77.0.2" if authorized else "10.77.0.99"
    manager = SimulationManager(
        MonitorStore(),
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            native_unicast="10.77.0.1",
            network_send_enabled=True,
            allowed_destinations=[host, "239.255.77.1:30490"],
        ),
    )
    status = await manager.start(
        SimulationConfig(
            service_id=0x1234,
            method_id=0x8002,
            transport="udp",
            destination_host=host,
            destination_port=30501,
            sd_multicast_group="239.255.77.1",
            interval_ms=10,
            generator=SignalGeneratorConfig(kind="constant", initial=12.5),
        )
    )
    try:
        await asyncio.to_thread(
            consumer.wait_for_service_reconnect, "DoorService_client", 5
        )
        if authorized:
            assert await asyncio.to_thread(
                consumer.ck_s2s_event,
                "DoorService_client",
                "Sample",
                {"value": 12.5},
                5,
            )
        else:
            with pytest.raises(TimeoutError):
                await asyncio.to_thread(
                    consumer.ck_s2s_event,
                    "DoorService_client",
                    "Sample",
                    {"value": 12.5},
                    0.5,
                )
        assert manager.list()[0].emitted_count > 0
        messages = await manager._monitor.list()
        assert messages[-1].payload_hex == "41480000"
        assert messages[-1].metadata["wire_verified"] is False
        log = (tmp_path / "native" / status.id / "native.log").read_text()
        assert '"accepted":' + ("true" if authorized else "false") in log
    finally:
        assert not (await manager.stop(status.id))[0].running
