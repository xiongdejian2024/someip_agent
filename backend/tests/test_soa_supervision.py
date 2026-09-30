"""SAT 对象构造和本实例进程监督策略；端到端异常恢复另在 veth 验证。"""

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from soa_partner.src.base_partner import PartnerKeyInfo, PartnerStartConfig, S2sBaseClass
from someip_agent.soa.ipc import member_messages
from someip_agent.soa.operator import NativeRuntimeError, SOAOperator
from someip_agent.soa.supervision import NativeSupervisor


def wait_state(supervisor, state):
    deadline = time.monotonic() + 1
    while supervisor.state != state and time.monotonic() < deadline:
        time.sleep(0.005)
    assert supervisor.state == state


@pytest.mark.parametrize("suffix", ["", "2026-10-01 [warning] Network interface eth0 up"])
def test_dynamic_control_port_reads_complete_json_before_mixed_log(tmp_path, caplog, suffix):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    path = tmp_path / "native.log"
    path.write_text(
        "2026-10-01 [warning] 初始化日志\n"
        + json.dumps({"operation": "service.start"})
        + "\n"
        + json.dumps({"operation": "native.ready", "port": port})
        + suffix
        + "\n"
    )
    operator = SOAOperator(operator_port=0, log_path=path)
    try:
        operator.create_socket(timeout=0.1)
        assert operator.operator_port == port and operator.tcp_socket is not None
        if suffix:
            assert any(record.operation == "native.ready.mixed_log" for record in caplog.records)
    finally:
        operator.stop_operator()
        listener.close()


@pytest.mark.parametrize("port", [0, -1, 65536, True, "1234", None])
def test_dynamic_control_port_rejects_invalid_ready_record_with_stack(tmp_path, caplog, port):
    path = tmp_path / "native.log"
    path.write_text(json.dumps({"operation": "native.ready", "port": port}) + "\n")
    operator = SOAOperator(operator_port=0, log_path=path)
    with pytest.raises(NativeRuntimeError, match="端口非法"):
        operator._read_ready_port()
    assert any(record.exc_info for record in caplog.records)
    assert operator.operator_port == 0


def test_dynamic_control_port_waits_for_partial_record_but_reports_corruption(tmp_path, caplog):
    path = tmp_path / "native.log"
    path.write_text('{"operation":"native.ready","port":1234}')
    operator = SOAOperator(operator_port=0, log_path=path)
    assert operator._read_ready_port() is None
    path.write_text('{"operation":"native.ready","port":\n')
    with pytest.raises(json.JSONDecodeError):
        operator._read_ready_port()
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize(
    "role,alias", [("client", "Service"), ("client_1", "Service_1"), ("server", "Service")]
)
def test_original_partner_info_constructor_is_detached(role, alias):
    info = PartnerKeyInfo("Service", role, "Instance", 12)
    assert info.socket is None and info.ip_port == ("127.0.0.1", 0)
    assert info.running and info.service_status == "OFFLINE" and info.instance is None
    assert isinstance(info.start_config, PartnerStartConfig)
    assert info.start_args == {
        alias: {"role": role.split("_")[0], "name": "Instance", "status": True, "heartbeat": 12}
    }
    assert info.req_queue.empty() and info.resp_queue.empty() and info.event_queue.empty()
    with pytest.raises(NativeRuntimeError, match="尚未建立"):
        info.require_socket()


def test_original_keyword_constructor_and_native_extension_have_independent_state():
    first = PartnerKeyInfo(service="Service", role="server", instance="Instance", heartbeat=4)
    second = PartnerKeyInfo("Service", "server", "Instance", 4)
    first.callback.append(lambda *_args: None)
    first.req_queue.put({"function": "Set"})
    first.start_args["Service"]["heartbeat"] = 9
    assert not second.callback and second.req_queue.empty()
    assert second.start_args["Service"]["heartbeat"] == 4
    left, right = socket.socketpair()
    try:
        extension = PartnerKeyInfo(
            "Service", "client", "Service", 600, socket=left, ip_port=("127.0.0.1", 2345)
        )
        assert extension.require_socket() is left
        assert extension.ip_port == ("127.0.0.1", 2345)
        assert PartnerKeyInfo(left, ("127.0.0.1", 2345)).require_socket() is left
        with pytest.raises(ValueError, match="混用"):
            PartnerKeyInfo(left, ("127.0.0.1", 0), socket=left)
    finally:
        left.close()
        right.close()


def test_detached_member_send_and_close_do_not_raise_attribute_error():
    partner = S2sBaseClass(auto_start=False)
    info = PartnerKeyInfo("Service", "client", "Service", 600)
    partner.partner_infos["Service_client"] = info
    info.service_status = "START"
    try:
        with pytest.raises(NativeRuntimeError, match="尚未建立"):
            partner.send_method_request("Service_client", "Get", {})
        assert not partner._method_timing._pending
    finally:
        partner.close()
        partner.close()


def test_subscription_calls_wait_for_correlated_native_state():
    facade = S2sBaseClass(auto_start=False)
    left, right = socket.socketpair()
    info = PartnerKeyInfo("Service", "client", "Service", 600, socket=left)
    facade.partner_infos["Service_client"] = info
    info.thread = threading.Thread(target=facade._reader, args=("Service_client", info))
    info.thread.start()
    commands = []

    def respond():
        for request in member_messages(right):
            commands.append((request["function"], json.loads(request["args"])))
            subscriptions = ["UpdatePositionEvent"] if request["function"] == "RegistEvent" else []
            right.sendall(
                json.dumps(
                    {
                        "action": "response",
                        "function": request["function"],
                        "correlation_id": request["correlation_id"],
                        "failtype": "FAILTYPE_SUCCESS",
                        "result": "true",
                        "subscriptions": subscriptions,
                    }
                ).encode()
            )

    thread = threading.Thread(target=respond)
    thread.start()
    try:
        facade.register_event("Service_client", ["Position"])
        assert info.subscriptions == {"UpdatePositionEvent"}
        facade.unregister_event("Service_client", ["Position"])
        assert info.subscriptions == set()
        assert commands == [
            ("RegistEvent", {"event_list": ["Position"]}),
            ("UnRegistEvent", {"event_list": ["Position"]}),
        ]
        assert not info.response_waiters and not facade._method_timing._pending
        assert info.resp_queue.qsize() == 2  # 原有回执缓存不能被内部等待者吞掉。
        facade.empty_resp_list("Service_client")

        def on_event(_key, message):
            if message["action"] == "event" and message["function"] == "Trigger":
                facade.register_event("Service_client", ["Position"])

        info.callback.append(on_event)
        right.sendall(b'{"action":"event","function":"Trigger","args":"{}"}')
        assert info.resp_queue.get(timeout=1)["function"] == "RegistEvent"
        assert info.subscriptions == {"UpdatePositionEvent"}
    finally:
        facade.close()
        thread.join(timeout=1)
        right.close()


@pytest.mark.parametrize("exit_code", [0, 1, -9])
def test_sat_excluded_exit_codes_are_not_restarted(exit_code):
    operator = SOAOperator()
    operator.process = SimpleNamespace(poll=lambda: exit_code)
    calls = []
    supervisor = NativeSupervisor(operator, lambda: calls.append("恢复"), interval=0.005)
    supervisor.start()
    try:
        wait_state(supervisor, "stopped")
        assert not calls and supervisor.restart_count == 0
    finally:
        supervisor.stop()


def test_owned_process_recovers_once_and_resumes_watching():
    operator = SOAOperator()
    code = [-6]
    operator.process = SimpleNamespace(poll=lambda: code[0])

    def recover():
        code[0] = None

    supervisor = NativeSupervisor(operator, recover, interval=0.005)
    supervisor.start()
    try:
        deadline = time.monotonic() + 1
        while supervisor.restart_count == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert supervisor.restart_count == 1 and supervisor.state == "watching"
        assert supervisor.last_error is None
    finally:
        supervisor.stop()
    assert not supervisor.thread.is_alive()


def test_restart_storm_is_bounded_and_logged_with_stack(caplog):
    operator = SOAOperator()
    operator.process = SimpleNamespace(poll=lambda: -6)
    calls = []
    supervisor = NativeSupervisor(
        operator, lambda: calls.append("恢复"), interval=0.005, restart_limit=2
    )
    supervisor.start()
    try:
        wait_state(supervisor, "failed")
        assert len(calls) == 2 and supervisor.restart_count == 2
        assert "窗口限制" in supervisor.last_error
        assert any(record.exc_info for record in caplog.records)
    finally:
        supervisor.stop()


def test_recovery_failure_does_not_silently_resume(caplog):
    operator = SOAOperator()
    operator.process = SimpleNamespace(poll=lambda: -6)

    def recover():
        raise OSError("配置无法恢复")

    supervisor = NativeSupervisor(operator, recover, interval=0.005)
    supervisor.start()
    try:
        wait_state(supervisor, "failed")
        assert supervisor.restart_count == 0 and "OSError" in supervisor.last_error
        assert any(record.exc_info for record in caplog.records)
    finally:
        supervisor.stop()


@pytest.mark.parametrize(
    "options",
    [
        {"interval": 0},
        {"interval": float("nan")},
        {"restart_limit": 0},
        {"restart_window": float("inf")},
    ],
)
def test_invalid_supervision_limits_are_rejected(options):
    with pytest.raises(ValueError):
        NativeSupervisor(SOAOperator(), lambda: None, **options)
