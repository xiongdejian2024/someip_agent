"""SAT 时延审计及 WTI 调用契约；网络功能另在隔离 veth 验收。"""

import json
import socket
import threading
import time

import pytest

from someip_agent.soa.operator import NativeRuntimeError
from someip_agent.soa.partner import PartnerKeyInfo, S2sBaseClass
from someip_agent.soa.timing import MethodTimingAudit


@pytest.fixture
def partner():
    facade = S2sBaseClass(auto_start=False)
    left, right = socket.socketpair()
    info = PartnerKeyInfo(left, ("127.0.0.1", 0))
    info.service_status = "START"
    facade.partner_infos["Service_client"] = info
    info.thread = threading.Thread(target=facade._reader, args=("Service_client", info))
    info.thread.start()
    try:
        yield facade, right
    finally:
        facade.close()
        right.close()


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize(
    "kind",
    ["warning", "telltale", "coming_warning", "coming_telltale", "no_warning", "no_telltale"],
)
def test_wti_six_helpers_match_sat_calls(auto, kind, monkeypatch):
    facade = S2sBaseClass(auto_start=False)
    calls = []
    for name in (
        "ck_event_and_resp",
        "ck_coming_event_and_resp",
        "ck_no_specific_event",
        "send_request_and_ck_resp",
    ):
        monkeypatch.setattr(
            facade, name, lambda *a, _name=name, **kw: calls.append((_name, a, kw)) or True
        )
    event = "TelltaleList" if "telltale" in kind else "WarningMsgList"
    field = "state" if "telltale" in kind else "info"
    key = "WTIAutoDriveService_client" if auto else "WTIService_client"
    method = f"ck_wti_{kind}_and_ck_resp" if kind.startswith("no_") else f"ck_wti_{kind}_and_resp"
    options = {"timeout": 0.7, "wti_auto": auto}
    if kind.startswith("coming_"):
        options["deviation"] = 0.1
    assert getattr(facade, method)("提示", 9, **options)
    if kind.startswith("no_"):
        assert calls == [
            ("ck_no_specific_event", (key, event, "提示", 0.7), {}),
            (
                "send_request_and_ck_resp",
                (key, f"Get{event}", {}, {"out": [{"name": "提示", field: "9"}]}),
                {"timeout": 0.2},
            ),
        ]
    else:
        expected = {"timeout": 0.7}
        if kind.startswith("coming_"):
            expected["deviation"] = 0.1
        assert calls == [
            (
                "ck_coming_event_and_resp" if kind.startswith("coming_") else "ck_event_and_resp",
                (key, event, {"list": [{"name": "提示", field: "9"}]}),
                expected,
            )
        ]


def test_timing_correlates_same_method_and_uses_monotonic(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("someip_agent.soa.timing.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("someip_agent.soa.timing.time.time", lambda: -12345.0)
    audit = MethodTimingAudit()
    audit.begin("slow", "Service_client", "Get", 0.1)
    audit.begin("fast", "Service_client", "Get", 1)
    clock[0] = 10.2
    audit.complete("fast")
    audit.complete("slow")
    assert len(audit.records) == 1
    assert audit.records[0][0:2] == ("Get", -12345.0)
    assert audit.records[0][2] == pytest.approx(0.2)
    with pytest.raises(AssertionError, match="超时"):
        audit.check()


def test_audit_bounds_reset_and_inflight_expiry(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr("someip_agent.soa.timing.time.monotonic", lambda: clock[0])
    audit = MethodTimingAudit(capacity=1, record_capacity=1)
    audit.begin("one", "client", "Get", 0.1)
    with pytest.raises(NativeRuntimeError, match="容量"):
        audit.begin("two", "client", "Get", 0.1)
    clock[0] = 2
    with pytest.raises(AssertionError):
        audit.check()
    audit.reset_records([])
    with pytest.raises(AssertionError):
        audit.check()
    audit.complete("one")
    audit.begin("two", "client", "Get", 1)
    audit.complete("two", "FAILTYPE_TIMEOUT")
    assert audit.dropped_records == 1 and len(audit.records) == 1
    audit.reset_records([])
    audit.begin("cancel", "client", "Get", 0.1)
    audit.cancel_member("client")
    audit.check()
    assert not audit._pending
    with pytest.raises(ValueError, match="容量"):
        MethodTimingAudit(record_capacity=0)


def test_response_audit_happens_on_socket_reader(partner):
    facade, peer = partner
    facade.method_default_timeout = 0.01
    correlation = facade.send_method_request("Service_client", "Get", {})
    assert json.loads(peer.recv(4096))["correlation_id"] == correlation
    time.sleep(0.03)
    peer.sendall(
        json.dumps(
            {
                "action": "response",
                "function": "Get",
                "correlation_id": correlation,
                "failtype": "FAILTYPE_SUCCESS",
                "result": "{}",
            }
        ).encode()
    )
    response = facade.partner_infos["Service_client"].resp_queue.get(timeout=1)
    assert response["correlation_id"] == correlation
    with pytest.raises(AssertionError, match="Get"):
        facade.ck_method_timeout()
    facade.method_is_timeout = []
    facade.ck_method_timeout()


def test_async_no_return_and_send_failure_do_not_leak_pending(partner, monkeypatch, caplog):
    facade, peer = partner
    facade.partner_infos["Service_client"].no_return_methods = {"Fire"}
    facade.send_method_request("Service_client", "Fire", {})
    peer.recv(4096)
    facade.send_method_request("Service_client", "Get", {}, is_async=True)
    peer.recv(4096)
    assert not facade._method_timing._pending

    def fail(*_args):
        raise OSError("测试发送失败")

    monkeypatch.setattr(facade, "_send", fail)
    with pytest.raises(OSError):
        facade.send_method_request("Service_client", "Get", {})
    assert not facade._method_timing._pending
    assert any(record.exc_info for record in caplog.records)


def test_absence_preserves_other_events_and_rejects_disconnect(partner):
    facade, peer = partner
    info = facade.partner_infos["Service_client"]
    info.event_queue.put(
        {"function": "UpdateWarningMsgListEvent", "args": '{"list":[{"name":"其他"}]}'}
    )
    assert facade.ck_no_specific_event("Service_client", "WarningMsgList", "目标", 0.01)
    assert info.event_queue.qsize() == 1
    with pytest.raises(AssertionError):
        facade.ck_no_specific_event("Service_client", "WarningMsgList", "其他", 0.01)
    peer.shutdown(socket.SHUT_RDWR)
    info.thread.join(timeout=1)
    with pytest.raises(NativeRuntimeError, match="断开"):
        facade.ck_no_event("Service_client", "Missing", 0.01)


def test_absence_checks_new_messages_during_window(partner):
    facade, peer = partner
    timer = threading.Timer(
        0.02,
        peer.sendall,
        args=(
            b'{"action":"event","function":"UpdateWarningMsgListEvent","args":"{\\"list\\":[{\\"name\\":\\"target\\"}]}"}',
        ),
    )
    timer.start()
    try:
        with pytest.raises(AssertionError):
            facade.ck_no_specific_event("Service_client", "WarningMsgList", "target", 0.2)
    finally:
        timer.join()
