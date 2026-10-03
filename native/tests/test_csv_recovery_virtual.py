"""在 server netns 运行，验证 CSV 最新配置在异常恢复后重新预编译。"""

import signal
import time

import pytest
from test_recovery_virtual import owned_partners, wait_until


@pytest.mark.parametrize("transport", ["udp", "tcp"])
def test_csv_latest_raw_configuration_recovers_owned_native_process(transport):
    with owned_partners(transport, "server") as (server, client, _):
        text = "time_ms,/position\n0,77\n29,88\n"
        server.send_event_notify_thread_start(
            "DoorService_server", "Position", {"position": 0}, 0.02, csv_text=text
        )
        assert client.chk_notify(
            "DoorService_client", "Position", {"position": 88}, timeout=3
        )
        latest = "time_ms,/position\n0,99\n29,100\n"
        server.send_event_notify_thread_update(
            "DoorService_server", "Position", {"position": 0}, csv_text=latest
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
        assert config["csv_text"] == latest and config["sources"] == []
        assert server.event_cycle_status("DoorService_server")["source_count"] == 1
        server.send_event_notify_thread_stop("DoorService_server")
        stopped = server.event_cycle_status("DoorService_server")
        time.sleep(0.06)
        assert stopped["source_count"] == 0 and not stopped["running"]
        assert (
            server.event_cycle_status("DoorService_server")["emitted_count"]
            == stopped["emitted_count"]
        )
