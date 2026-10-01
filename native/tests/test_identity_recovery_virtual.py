"""双具名 Client ID 在真实崩溃/暂停后的恢复；专用端口供独立 PCAP 审计。"""

import json
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from someip_agent.soa.operator import NativeRuntimeError
from test_recovery_virtual import EVIDENCE, owned_partners, wait_until

CLIENTS = ("DoorService_client", "DoorService_client_1")


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("owned_role", ["server", "client"])
@pytest.mark.parametrize("fault", ["exit", "pause"])
def test_independent_identities_survive_real_process_recovery(
    transport, owned_role, fault
):
    index = (fault == "pause") * 4 + (owned_role == "client") * 2 + (transport == "tcp")
    port = 30760 + index
    with owned_partners(
        transport,
        owned_role,
        identity_port=port,
        liveness_timeout=0.15,
        liveness_failures=3,
    ) as (server, client, _transport):
        owned = server if owned_role == "server" else client
        observed = []

        def reply(key, message):
            if message["action"] == "request" and message["function"] == "SetPosition":
                args = json.loads(message["args"])
                observed.append((message["request_id"], args["position"]))
                server.send_method_response(
                    key, "SetPosition", args, request_id=message["request_id"]
                )

        server.register_callback("DoorService_server", reply)
        for key, identifier in zip(CLIENTS, (0x7841, 0x7842), strict=True):
            assert client.partner_infos[key].application_id == identifier
            client.stop_single_partner(key)
            client.start_single_partner(
                "DoorService", "client" if identifier == 0x7841 else "client_1"
            )
            assert client.wait_for_service_reconnect(key, timeout=5)
        # 先检查停止成员不会复活；第三成员借用 A application，不应增加新 Client ID。
        client.start_single_partner("DoorService", "client_2")
        client.stop_single_partner("DoorService_client_2")
        client.unregister_event(CLIENTS[0], ["Angle"])
        client.unregister_event(CLIENTS[1], ["Position"])
        for key, value in zip(CLIENTS, (0xE100 + index, 0xE200 + index), strict=True):
            assert client.send_request_and_return_resp(
                key, "SetPosition", {"position": value}, timeout=3
            ) == {"out": {"position": value}}
        server.send_event_notify_thread_start(
            "DoorService_server",
            "Position",
            {"position": 0xE300 + index},
            cycle_time=0.05,
        )
        server.send_event_notify_thread_update(
            "DoorService_server", "Position", {"position": 0xE400 + index}
        )
        assert client.ck_coming_event(
            CLIENTS[0], "Position", {"position": 0xE400 + index}, timeout=3
        )
        old = owned.sim_operator.process
        assert old is not None and old.poll() is None
        old_infos = [client.partner_infos[key] for key in CLIENTS]
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [
                pool.submit(
                    client.send_request_and_return_resp,
                    key,
                    "GetPosition",
                    {},
                    timeout=1.5,
                )
                for key in CLIENTS
            ]
            wait_until(
                lambda: all(info.response_waiters for info in old_infos), timeout=1
            )
            requests = [
                server._take(
                    server.partner_infos["DoorService_server"].req_queue,
                    lambda message: message["function"] == "GetPosition",
                    2,
                )
                for _ in CLIENTS
            ]
            assert {request["request_id"] >> 16 for request in requests} == {
                0x7841,
                0x7842,
            }
            fault_at = time.time()
            old.send_signal(signal.SIGSTOP if fault == "pause" else signal.SIGUSR1)
            paused_state = None
            if fault == "pause":
                wait_until(
                    lambda: (
                        "\nState:\tT" in Path(f"/proc/{old.pid}/status").read_text()
                    ),
                    timeout=1,
                )
                paused_state = Path(f"/proc/{old.pid}/status").read_text()
                assert old.poll() is None
            wait_until(lambda: owned._supervisor.restart_count == 1, timeout=10)
            for future in pending:
                with pytest.raises((NativeRuntimeError, TimeoutError)):
                    future.result(timeout=3)
            assert all(not info.response_waiters for info in old_infos)
        new = owned.sim_operator.process
        assert new is not None and new is not old and new.poll() is None
        assert old.poll() == (-signal.SIGKILL if fault == "pause" else -signal.SIGUSR1)
        reason = "control_unresponsive" if fault == "pause" else "process_exit"
        assert owned._supervisor.restart_reason == reason
        for key, identifier in zip(CLIENTS, (0x7841, 0x7842), strict=True):
            assert client.wait_for_service_reconnect(key, timeout=5)
            assert client.partner_infos[key].application_id == identifier
        assert set(client.partner_infos) == set(CLIENTS), "显式停止的第三成员被恢复"
        assert "UpdateAngleEvent" not in client.partner_infos[CLIENTS[0]].subscriptions
        assert (
            "UpdatePositionEvent" not in client.partner_infos[CLIENTS[1]].subscriptions
        )
        assert client.ck_coming_event(
            CLIENTS[0], "Position", {"position": 0xE400 + index}, timeout=3
        )
        assert client.ck_no_event(CLIENTS[1], "Position", timeout=0.2)
        server.send_event_notify(
            "DoorService_server", "Angle", {"angle": 0xE500 + index}
        )
        assert client.ck_coming_event(
            CLIENTS[1], "Angle", {"angle": 0xE500 + index}, timeout=3
        )
        assert client.ck_no_event(CLIENTS[0], "Angle", timeout=0.2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [
                pool.submit(
                    client.send_request_and_return_resp,
                    key,
                    "SetPosition",
                    {"position": value},
                    timeout=3,
                )
                for key, value in zip(
                    CLIENTS, (0xE600 + index, 0xE700 + index), strict=True
                )
            ]
            assert [task.result(timeout=4) for task in tasks] == [
                {"out": {"position": 0xE600 + index}},
                {"out": {"position": 0xE700 + index}},
            ]
        assert sorted(value for _request, value in observed) == sorted(
            (0xE100 + index, 0xE200 + index, 0xE600 + index, 0xE700 + index)
        ), "业务请求被重放或丢失"
        assert server.ck_no_req("DoorService_server", "GetPosition", timeout=0.2), (
            "未完成请求被恢复重放"
        )
        server.send_event_notify_thread_stop("DoorService_server")
        new_pid = new.pid
        owned.close()
        assert new.poll() == 0 and not owned._supervisor.thread.is_alive()
        (EVIDENCE / f"identity-recovery-{port}.json").write_text(
            json.dumps(
                {
                    "transport": transport,
                    "owned_role": owned_role,
                    "fault": fault,
                    "port": port,
                    "old_pid": old.pid,
                    "new_pid": new_pid,
                    "old_exit": old.poll(),
                    "new_exit": new.poll(),
                    "paused_state": paused_state,
                    "restart_reason": reason,
                    "restart_count": owned._supervisor.restart_count,
                    "fault_at": fault_at,
                    "observed_requests": observed,
                    "client_ids": [0x7841, 0x7842],
                    "business_replay": False,
                    "stopped_member_restored": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
