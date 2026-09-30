"""在 server netns 运行 Python 拥有的原生进程，验证异常恢复后的真实 UDP/TCP。"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from soa_partner.src.base_partner import PartnerKeyInfo, S2sBaseClass
from soa_partner.src.Operator import SOAOperator

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent
EVIDENCE = ROOT.parents[1] / "build" / "virtual-evidence"


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise TimeoutError("等待虚拟网进程恢复状态超时")


@pytest.fixture(params=["udp", "tcp"])
def recovery_partners(request):
    with owned_partners(request.param, "server") as partners:
        yield partners


@pytest.fixture(params=["udp", "tcp"])
def recovering_clients(request):
    with owned_partners(request.param, "client") as partners:
        yield partners


@contextmanager
def owned_partners(transport, owned_role):
    catalog = json.loads((ROOT / "catalog.json").read_text())
    catalog["DoorService"]["transport"] = transport
    catalog_path = EVIDENCE / f"recovery-{transport}-catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    handles, partners, remote = [], [], None
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    log_handler = logging.FileHandler(
        EVIDENCE / f"recovery-python-{transport}-{time.time_ns()}.log"
    )
    log_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root_logger.addHandler(log_handler)
    root_logger.setLevel(logging.INFO)
    try:
        for node, address in (
            (owned_role, "10.77.0.1"),
            ("client" if owned_role == "server" else "server", "10.77.0.2"),
        ):
            name = f"recovery_{node}"
            config = json.loads((ROOT / f"{node}.json").read_text())
            config.update(
                {
                    "network": f"soa-recovery-{node}",
                    "unicast": address,
                    "applications": [
                        {"name": name, "id": "0x5511" if node == "server" else "0x5522"}
                    ],
                    "routing": name,
                }
            )
            for service in config.get("services", []):
                service.pop("unreliable" if transport == "tcp" else "reliable", None)
                for event in service.get("events", []):
                    event["is_reliable"] = transport == "tcp"
            config_path = EVIDENCE / f"recovery-{node}-{transport}-config.json"
            config_path.write_text(json.dumps(config))
            members = {
                "DoorService": {
                    "role": node,
                    "name": "DoorService",
                    "transport": transport,
                }
            }
            if node == owned_role:
                operator = SOAOperator(
                    name,
                    operator_port=0,
                    binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    catalog=catalog_path,
                    config=config_path,
                    log_path=EVIDENCE
                    / f"recovery-owned-{node}-{transport}-{time.time_ns()}.log",
                )
                partner = S2sBaseClass(
                    members, operator=operator, monitor_interval=0.05
                )
            else:
                handle = (EVIDENCE / f"recovery-remote-{node}-{transport}.log").open(
                    "ab"
                )
                handles.append(handle)
                remote = subprocess.Popen(
                    [
                        "ip",
                        "netns",
                        "exec",
                        "soa-client",
                        os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                        "run",
                        "--name",
                        name,
                        "-p",
                        "16789",
                        "--bind",
                        address,
                        "--catalog",
                        str(catalog_path),
                        "--config",
                        str(config_path),
                    ],
                    stdout=handle,
                    stderr=handle,
                )
                partner = S2sBaseClass(
                    members, operator=SOAOperator(name, host=address), attach=True
                )
            partners.append(partner)
        server, client = partners if owned_role == "server" else reversed(partners)
        assert client.wait_for_service_reconnect("DoorService_client", timeout=10)
        assert (
            client if owned_role == "server" else server
        )._supervisor.thread is None  # attach 不拥有远端进程，不启动自动恢复。
        time.sleep(0.3)
        yield server, client, transport
    finally:
        for partner in reversed(partners):
            partner.close()
        if remote is not None:
            remote.terminate()
            try:
                remote.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.exception(
                    "恢复验收的远端原生进程退出超时",
                    extra={"operation": "test.recovery.cleanup"},
                )
                remote.kill()
                remote.wait(timeout=5)
        for handle in handles:
            handle.close()
        root_logger.removeHandler(log_handler)
        root_logger.setLevel(previous_level)
        log_handler.close()


def test_owned_native_process_recovers_members_callbacks_and_latest_cycle(
    recovery_partners,
):
    server, client, _ = recovery_partners
    calls = []

    def reply(key, message):
        if message["action"] == "request" and message["function"] == "SetPosition":
            args = json.loads(message["args"])
            calls.append(args)
            server.send_method_response(
                key, "SetPosition", args, request_id=message["request_id"]
            )

    server.register_callback("DoorService_server", reply)
    server.register_auto_response("DoorService_server", "Echo", echo=True)
    server.start_single_partner("DoorService", "client_1")
    server.stop_single_partner("DoorService_client_1")
    server.start_single_partner("DoorService", "client_2")
    assert server.wait_for_service_reconnect("DoorService_client_2", timeout=5)
    server.unregister_event("DoorService_client_2", ["Angle"])
    before = set(server.partner_infos["DoorService_client_2"].subscriptions)
    with pytest.raises(RuntimeError, match="未知订阅"):
        server.register_event("DoorService_client_2", ["Angle", "Unknown"])
    assert server.partner_infos["DoorService_client_2"].subscriptions == before
    info = server.partner_infos["DoorService_server"]
    assert isinstance(info, PartnerKeyInfo) and info.start_config.name == "DoorService"
    assert (
        info.start_config.heartbeat == 600
        and info.require_socket().getpeername()[1] > 0
    )
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 5}, timeout=1
    ) == {"out": {"position": 5}}
    server.send_event_notify_thread_start(
        "DoorService_server", "Position", {"position": 77}, 0.02
    )
    server.send_event_notify_thread_update(
        "DoorService_server", "Position", {"position": 88}
    )
    assert client.chk_notify(
        "DoorService_client", "Position", {"position": 88}, timeout=2
    )
    old = server.sim_operator.process
    # SIGUSR1 没有被运行时处理，会真实退出；不用 SIGABRT 生成 core 文件，也不动宿主进程。
    old.send_signal(signal.SIGUSR1)
    assert old.wait(timeout=3) == -signal.SIGUSR1
    wait_until(lambda: server._supervisor.restart_count == 1, timeout=10)
    assert server.sim_operator.process.pid != old.pid
    assert server.sim_operator.process.poll() is None
    assert set(server.partner_infos) == {"DoorService_server", "DoorService_client_2"}
    assert (
        "UpdateAngleEvent"
        not in server.partner_infos["DoorService_client_2"].subscriptions
    )
    server.empty_event_list("DoorService_client_2")
    server.send_event_notify("DoorService_server", "Angle", {"angle": 321})
    assert server.ck_no_event("DoorService_client_2", "Angle", timeout=0.1)
    server.register_event("DoorService_client_2", ["Angle"])
    server.send_event_notify("DoorService_server", "Angle", {"angle": 322})
    assert server.chk_notify("DoorService_client_2", "Angle", {"angle": 322}, timeout=2)
    assert server.partner_infos["DoorService_server"].callback == [reply]
    assert server.partner_infos["DoorService_server"].cycle_config["args"] == {
        "position": 88
    }
    assert client.wait_for_service_reconnect("DoorService_client", timeout=5)
    # 新观测窗口必须收到恢复进程的新通知，不能用旧缓存证明恢复。
    assert client.ck_coming_event(
        "DoorService_client", "Position", {"position": 88}, timeout=2
    )
    assert calls == [{"position": 5}], "恢复不能重放业务请求"
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 42}, timeout=2
    ) == {"out": {"position": 42}}
    assert calls == [{"position": 5}, {"position": 42}]
    args = {"label": "恢复后", "values": [1, -2], "reading": 3.25}
    assert client.send_request_and_return_resp(
        "DoorService_client", "Echo", args, timeout=2
    ) == {"out": args}
    assert server.send_request_and_return_resp(
        "DoorService_client_2", "SetPosition", {"position": 3}, timeout=2
    ) == {"out": {"position": 3}}
    client.ck_method_timeout()
    server.ck_method_timeout()
    evidence = {
        "transport": recovery_partners[2],
        "old_pid": old.pid,
        "new_pid": server.sim_operator.process.pid,
        "old_exit_code": old.returncode,
        "restart_count": server._supervisor.restart_count,
        "active_members": sorted(server.partner_infos),
        "business_calls": calls,
        "restored_cycle": server.partner_infos["DoorService_server"].cycle_config,
        "subscriptions": sorted(
            server.partner_infos["DoorService_client_2"].subscriptions
        ),
    }
    server.close()
    assert server.sim_operator.process.poll() is not None
    assert not server._supervisor.thread.is_alive()
    evidence["closed_process_exit_code"] = server.sim_operator.process.returncode
    evidence["supervisor_stopped"] = not server._supervisor.thread.is_alive()
    (EVIDENCE / f"recovery-{recovery_partners[2]}-audit.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2)
    )


def test_explicit_kill_is_not_automatically_restarted(recovery_partners):
    server, _, _ = recovery_partners
    process = server.sim_operator.process
    process.kill()
    assert process.wait(timeout=3) == -9
    wait_until(lambda: server._supervisor.state == "stopped")
    assert (
        server.sim_operator.process is process and server._supervisor.restart_count == 0
    )


def test_owned_client_recovers_veth_methods_and_per_member_subscriptions(
    recovering_clients,
):
    server, client, transport = recovering_clients
    calls = []

    def reply(key, message):
        if message["action"] == "request" and message["function"] == "SetPosition":
            args = json.loads(message["args"])
            calls.append(args)
            server.send_method_response(
                key, "SetPosition", args, request_id=message["request_id"]
            )

    server.register_callback("DoorService_server", reply)
    client.start_single_partner("DoorService", "client_1")
    client.stop_single_partner("DoorService_client_1")
    client.start_single_partner("DoorService", "client_2")
    client.wait_for_service_reconnect("DoorService_client_2", timeout=5)
    client.unregister_event("DoorService_client", ["Angle"])
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 5}, timeout=2
    ) == {"out": {"position": 5}}
    server.send_event_notify_thread_start(
        "DoorService_server", "Position", {"position": 88}, 0.02
    )
    assert client.chk_notify(
        "DoorService_client", "Position", {"position": 88}, timeout=2
    )
    old = client.sim_operator.process
    old.send_signal(signal.SIGUSR1)
    assert old.wait(timeout=3) == -signal.SIGUSR1
    wait_until(lambda: client._supervisor.restart_count == 1, timeout=10)
    assert (
        client.sim_operator.process.pid != old.pid
        and client.sim_operator.process.poll() is None
    )
    assert set(client.partner_infos) == {"DoorService_client", "DoorService_client_2"}
    assert client.wait_for_service_reconnect("DoorService_client", timeout=5)
    assert client.wait_for_service_reconnect("DoorService_client_2", timeout=5)
    assert (
        "UpdateAngleEvent"
        not in client.partner_infos["DoorService_client"].subscriptions
    )
    assert (
        "UpdateAngleEvent" in client.partner_infos["DoorService_client_2"].subscriptions
    )
    assert client.ck_coming_event(
        "DoorService_client", "Position", {"position": 88}, timeout=2
    )
    assert calls == [{"position": 5}]
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 42}, timeout=2
    ) == {"out": {"position": 42}}
    client.empty_event_list()
    server.send_event_notify("DoorService_server", "Angle", {"angle": 322})
    assert client.ck_no_event("DoorService_client", "Angle", timeout=0.1)
    assert client.chk_notify("DoorService_client_2", "Angle", {"angle": 322}, timeout=2)
    client.register_event("DoorService_client", ["Angle"])
    server.send_event_notify("DoorService_server", "Angle", {"angle": 323})
    assert client.chk_notify("DoorService_client", "Angle", {"angle": 323}, timeout=2)
    assert client.send_request_and_return_resp(
        "DoorService_client_2", "SetPosition", {"position": 3}, timeout=2
    ) == {"out": {"position": 3}}
    client.ck_method_timeout()
    evidence = {
        "transport": transport,
        "old_pid": old.pid,
        "new_pid": client.sim_operator.process.pid,
        "old_exit_code": old.returncode,
        "restart_count": client._supervisor.restart_count,
        "active_members": sorted(client.partner_infos),
        "business_calls": calls,
        "subscriptions": {
            key: sorted(info.subscriptions)
            for key, info in client.partner_infos.items()
        },
    }
    client.close()
    assert (
        not client._supervisor.thread.is_alive()
        and client.sim_operator.process.poll() is not None
    )
    evidence["closed_process_exit_code"] = client.sim_operator.process.returncode
    (EVIDENCE / f"recovery-client-{transport}-audit.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2)
    )


def test_explicit_client_kill_is_not_automatically_restarted(recovering_clients):
    _, client, _ = recovering_clients
    process = client.sim_operator.process
    process.kill()
    assert process.wait(timeout=3) == -9
    wait_until(lambda: client._supervisor.state == "stopped")
    assert (
        client.sim_operator.process is process and client._supervisor.restart_count == 0
    )


def test_shared_event_registration_survives_member_stop_and_last_consumer_restart(
    recovering_clients,
):
    server, client, _ = recovering_clients
    client.start_single_partner("DoorService", "client_1")
    assert client.wait_for_service_reconnect("DoorService_client_1", timeout=5)
    server.send_event_notify_thread_start(
        "DoorService_server", "Position", {"position": 100}, 0.02
    )
    for key in ("DoorService_client", "DoorService_client_1"):
        assert client.ck_coming_event(key, "Position", {"position": 100}, timeout=2)
    client.unregister_event("DoorService_client", ["Position"])
    client.empty_event_list("DoorService_client")
    assert client.ck_no_event("DoorService_client", "Position", timeout=0.1)
    assert client.ck_coming_event(
        "DoorService_client_1", "Position", {"position": 100}, timeout=2
    )
    client.stop_single_partner("DoorService_client_1")
    # 最后一个 Position 订阅者退出，不能把仍使用的其他事件注册一并释放。
    server.send_event_notify_thread_stop("DoorService_server")
    server.send_event_notify_thread_start(
        "DoorService_server", "Angle", {"angle": 200}, 0.02
    )
    assert client.ck_coming_event(
        "DoorService_client", "Angle", {"angle": 200}, timeout=2
    )
    client.stop_single_partner("DoorService_client")
    assert not client.partner_infos
    assert not client.sim_operator.send_request("running_service")
    client.start_single_partner("DoorService", "client_3")
    assert client.wait_for_service_reconnect("DoorService_client_3", timeout=5)
    assert client.ck_coming_event(
        "DoorService_client_3", "Angle", {"angle": 200}, timeout=2
    )
    server.send_event_notify_thread_stop("DoorService_server")
    server.send_event_notify_thread_start(
        "DoorService_server", "Position", {"position": 101}, 0.02
    )
    assert client.ck_coming_event(
        "DoorService_client_3", "Position", {"position": 101}, timeout=2
    )
