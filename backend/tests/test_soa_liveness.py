"""控制通道活性、绝对时限及本实例所有权；真实暂停恢复另由 veth 验证。"""

import json
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from someip_agent.soa.ipc import recv_data_from, send_data_to
from someip_agent.soa.operator import NativeOperationError, NativeRuntimeError, SOAOperator
from someip_agent.soa.partner import S2sBaseClass
from someip_agent.soa.supervision import NativeSupervisor

logger = logging.getLogger(__name__)


@contextmanager
def peer(response):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(1)
    operator = SOAOperator(operator_port=listener.getsockname()[1])
    operator.create_socket()
    primary, _address = listener.accept()
    errors = []

    def serve():
        try:
            connection, _address = listener.accept()
            with connection:
                request = json.loads(recv_data_from(connection))
                assert request["function"] == "ping"
                send_data_to(connection, response)
        except Exception as error:
            logger.exception("活性测试对端失败")
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield operator, primary
    finally:
        operator.stop_operator()
        primary.close()
        listener.close()
        thread.join(timeout=2)
        assert not thread.is_alive() and not errors


def pong(**values):
    return {
        "action": "response",
        "function": "ping",
        "failtype": "FAILTYPE_SUCCESS",
        "result": json.dumps({"runtime": "vsomeip", "protocol": 1}),
        **values,
    }


def test_probe_uses_separate_connection_and_keeps_primary_timeout_and_response():
    with peer(pong()) as (operator, primary):
        primary.sendall(b"00000002{}")
        original = operator.tcp_socket
        timeout = original.gettimeout()
        assert 0 <= operator.probe_liveness(0.5) <= 0.5
        assert operator.tcp_socket is original and original.gettimeout() == timeout
        assert json.loads(recv_data_from(original)) == {}


def test_probe_never_waits_for_busy_primary_control_lock():
    with peer(pong()) as (operator, _primary):
        with ThreadPoolExecutor(max_workers=1) as executor:
            operator._lock.acquire()
            try:
                future = executor.submit(operator.probe_liveness, 0.5)
                assert 0 <= future.result(timeout=1) <= 0.5
            finally:
                operator._lock.release()


def test_network_mode_probe_requires_network_source():
    response = pong(result='{"runtime":"vsomeip","mode":"network"}')
    with peer(response) as (operator, _primary):
        operator.mode = "network"
        assert 0 <= operator.probe_liveness(0.5) <= 0.5


def test_network_mode_probe_rejects_services_pong():
    with peer(pong()) as (operator, _primary):
        operator.mode = "network"
        with pytest.raises(NativeOperationError, match="模式"):
            operator.probe_liveness(0.5)


@pytest.mark.parametrize("failures", [0, -1, True, 1.5, 101])
def test_invalid_liveness_failure_limit_is_rejected_before_start(failures):
    with pytest.raises(ValueError):
        S2sBaseClass(auto_start=False, liveness_failures=failures)


def test_liveness_none_keeps_exit_only_supervision():
    partner = S2sBaseClass(auto_start=False, liveness_timeout=None)
    assert partner._supervisor.probe is None
    partner.close()


@pytest.mark.parametrize(
    "response",
    [
        pong(function="running_service"),
        pong(failtype="FAILTYPE_BAD_PARAM"),
        pong(result='{"runtime":"unknown","protocol":1}'),
        pong(result='{"runtime":"vsomeip","protocol":true}'),
        pong(result='{"runtime":"vsomeip","protocol":2}'),
    ],
)
def test_probe_rejects_invalid_or_incompatible_pong_with_stack(response, caplog):
    with peer(response) as (operator, _primary):
        with pytest.raises(NativeOperationError):
            operator.probe_liveness(0.5)
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("timeout", [0, -1, 4, True, float("nan"), float("inf")])
def test_invalid_liveness_timeout_is_rejected_before_start(timeout):
    with pytest.raises(ValueError):
        S2sBaseClass(auto_start=False, liveness_timeout=timeout)


def test_probe_without_known_connected_endpoint_fails_closed():
    with pytest.raises(NativeRuntimeError, match="端点"):
        SOAOperator().probe_liveness()


def test_control_deadline_is_shared_by_header_and_trickling_body():
    left, right = socket.socketpair()

    def trickle():
        try:
            left.sendall(b"00000020")
            for _index in range(32):
                time.sleep(0.02)
                left.sendall(b" ")
        except OSError:
            logger.debug("测试接收方达到时限并关闭连接", exc_info=True)
        finally:
            left.close()

    thread = threading.Thread(target=trickle)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            recv_data_from(right, deadline=started + 0.08)
        assert time.monotonic() - started < 0.3
    finally:
        right.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


class Process:
    def __init__(self, pid=1234):
        self.pid, self.code, self.kills = pid, None, 0

    def poll(self):
        return self.code

    def kill(self):
        self.kills += 1
        self.code = -9

    def wait(self, timeout):
        return self.code


def wait(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.002)
    assert predicate()


def test_one_transient_probe_failure_does_not_kill_or_restart(caplog):
    operator = SOAOperator()
    process = operator.process = Process()
    attempts = []
    recovered = []

    def probe():
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("临时延迟")
        return 0.001

    supervisor = NativeSupervisor(
        operator, lambda: recovered.append(1), interval=0.005, probe=probe, liveness_failures=2
    )
    supervisor.start()
    try:
        wait(lambda: len(attempts) >= 3)
        assert supervisor.state == "watching" and supervisor.probe_failure_count == 1
        assert supervisor.consecutive_probe_failures == 0 and supervisor.last_probe_error is None
        assert not process.kills and not recovered
        assert any(record.exc_info for record in caplog.records)
    finally:
        supervisor.stop()


def test_sustained_stall_kills_only_owned_process_then_recovers_once():
    operator = SOAOperator()
    old = operator.process = Process()

    def probe():
        if operator.process is old:
            raise TimeoutError("持续暂停")
        return 0.001

    supervisor = NativeSupervisor(
        operator,
        lambda: setattr(operator, "process", Process(2345)),
        interval=0.005,
        probe=probe,
        liveness_failures=2,
    )
    supervisor.start()
    try:
        wait(lambda: supervisor.restart_count == 1)
        assert old.kills == 1 and operator.process.kills == 0
        assert supervisor.restart_reason == "control_unresponsive"
        assert supervisor.probe_failure_count == 2 and supervisor.last_error is None
    finally:
        supervisor.stop()


def test_unresponsive_restart_storm_obeys_same_window_limit(caplog):
    operator = SOAOperator()
    processes = [Process()]
    operator.process = processes[0]

    def probe():
        raise TimeoutError("持续故障")

    def recover():
        processes.append(Process())
        operator.process = processes[-1]

    supervisor = NativeSupervisor(
        operator, recover, interval=0.005, restart_limit=2, probe=probe, liveness_failures=1
    )
    supervisor.start()
    try:
        wait(lambda: supervisor.state == "failed")
        assert supervisor.restart_count == 2 and [p.kills for p in processes] == [1, 1, 0]
        assert "窗口限制" in supervisor.last_error
        assert any(record.exc_info for record in caplog.records)
    finally:
        supervisor.stop()


def test_incompatible_pong_never_triggers_kill_or_recovery():
    operator = SOAOperator()
    process = operator.process = Process()
    recovered = []

    def probe():
        raise NativeOperationError("协议版本不匹配")

    supervisor = NativeSupervisor(
        operator, lambda: recovered.append(1), interval=0.005, probe=probe
    )
    supervisor.start()
    try:
        wait(lambda: supervisor.state == "failed")
        assert not process.kills and not recovered
    finally:
        supervisor.stop()


def test_stop_during_failed_probe_never_resurrects_process():
    operator = SOAOperator()
    process = operator.process = Process()
    recovered = []

    def probe():
        supervisor.stop()
        raise TimeoutError("关闭中收到探测失败")

    supervisor = NativeSupervisor(
        operator, lambda: recovered.append(1), interval=0.005, probe=probe, liveness_failures=1
    )
    supervisor.start()
    wait(lambda: supervisor.state == "stopped")
    assert not process.kills and not recovered
    supervisor.stop()


def test_changed_process_handle_is_never_killed():
    operator = SOAOperator()
    old, new = Process(), Process(2345)
    operator.process = new
    assert not operator.kill_unresponsive_owned_process(old)
    assert not old.kills and not new.kills
