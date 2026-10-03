"""server netns 内验收状态图最新配置、原生异常重建与停止清理。"""

import signal
import time

import pytest
from test_recovery_virtual import owned_partners, wait_until


def sources(first, second):
    return [
        {
            "path": "/position",
            "generator": {
                "kind": "state_machine",
                "initial_state": "idle",
                "states": [
                    {
                        "name": "idle",
                        "value": first,
                        "duration_ms": 29,
                        "next": "active",
                    },
                    {"name": "active", "value": second},
                ],
            },
        }
    ]


@pytest.mark.parametrize("transport", ["udp", "tcp"])
def test_latest_state_graph_recovers_owned_native_process(transport):
    with owned_partners(transport, "server") as (server, client, _):
        server.send_event_notify_thread_start(
            "DoorService_server",
            "Position",
            {"position": 0},
            0.02,
            sources=sources(77, 88),
        )
        assert client.chk_notify(
            "DoorService_client", "Position", {"position": 88}, timeout=3
        )
        latest = sources(99, 100)
        server.send_event_notify_thread_update(
            "DoorService_server", "Position", {"position": 0}, sources=latest
        )
        assert client.ck_coming_event(
            "DoorService_client", "Position", {"position": 100}, timeout=3
        )
        old = server.sim_operator.process
        old.send_signal(signal.SIGUSR1)
        assert old.wait(timeout=3) == -signal.SIGUSR1
        wait_until(lambda: server._supervisor.restart_count == 1, timeout=10)
        assert (
            server.sim_operator.process.pid != old.pid
            and server.sim_operator.process.poll() is None
        )
        assert client.wait_for_service_reconnect("DoorService_client", timeout=5)
        assert client.ck_coming_event(
            "DoorService_client", "Position", {"position": 100}, timeout=3
        )
        config = server.partner_infos["DoorService_server"].cycle_config
        assert config["sources"] == latest and "csv_text" not in config
        assert server.event_cycle_status("DoorService_server")["active_states"] == {
            "/position": "active"
        }
        server.send_event_notify_thread_stop("DoorService_server")
        stopped = server.event_cycle_status("DoorService_server")
        time.sleep(0.06)
        assert (
            stopped["source_count"] == 0
            and not stopped["running"]
            and stopped["active_states"] == {}
        )
        assert (
            server.event_cycle_status("DoorService_server")["emitted_count"]
            == stopped["emitted_count"]
        )
