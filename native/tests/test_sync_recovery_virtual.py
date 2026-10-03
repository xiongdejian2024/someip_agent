"""自有原生进程异常恢复：重新预编译最新多事件草案并暂停，绝不冒充连续时间轴。"""

import signal
import time

import pytest
from test_recovery_virtual import owned_partners, wait_until


@pytest.mark.parametrize("transport", ["udp", "tcp"])
def test_sync_recovery_reprepares_csv_and_state_paused(transport):
    with owned_partners(transport, "server") as (server, client, _):
        events = [
            {
                "member": "DoorService_server",
                "function": "Position",
                "args": {"position": 0},
                "interval_ms": 20,
                "csv_text": "time_ms,/position\n0,77\n29,88\n",
            },
            {
                "member": "DoorService_server",
                "function": "Sample",
                "args": {"value": 0},
                "interval_ms": 30,
                "sources": [
                    {
                        "path": "/value",
                        "generator": {
                            "kind": "state_machine",
                            "initial_state": "idle",
                            "states": [
                                {
                                    "name": "idle",
                                    "value": 1.25,
                                    "duration_ms": 29,
                                    "next": "active",
                                },
                                {"name": "active", "value": 2.5},
                            ],
                        },
                    }
                ],
            },
        ]
        first = server.start_event_sync(events, paused=False)
        assert client.chk_notify(
            "DoorService_client", "Position", {"position": 88}, timeout=3
        )
        assert client.chk_notify(
            "DoorService_client", "Sample", {"value": 2.5}, timeout=3
        )
        server.control_event_sync("speed", speed=4)
        server.control_event_sync("pause")
        old = server.sim_operator.process
        old.send_signal(signal.SIGUSR1)
        assert old.wait(timeout=3) == -signal.SIGUSR1
        wait_until(lambda: server._supervisor.restart_count == 1, timeout=10)
        recovered = server.event_sync_status()
        assert recovered["active"] and recovered["paused"] and recovered["speed"] == 4
        assert recovered["group_id"] != first["group_id"]
        assert recovered["frame_index"] == recovered["logical_ms"] == 0
        assert all(entry["emitted_count"] == 0 for entry in recovered["events"])
        assert server._sync_config["events"] == events
        time.sleep(0.08)
        assert server.event_sync_status() == recovered
        assert client.wait_for_service_reconnect("DoorService_client", timeout=5)
        server.control_event_sync("resume")
        assert client.ck_coming_event(
            "DoorService_client", "Position", {"position": 88}, timeout=3
        )
        assert client.ck_coming_event(
            "DoorService_client", "Sample", {"value": 2.5}, timeout=3
        )
        stopped = server.control_event_sync("stop")
        assert not stopped["active"] and server._sync_config is None
        time.sleep(0.06)
        assert server.event_sync_status() == stopped
