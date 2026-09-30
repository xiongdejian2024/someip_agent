"""SAT 缓存、命名和断言语义单测；线上互通另由虚拟以太网验收。"""

import json
import socket
import threading
import time

import pytest

from someip_agent.soa.naming import member_key
from someip_agent.soa.partner import (
    FailType,
    PartnerKeyInfo,
    PartnerStartConfig,
    S2sBaseClass,
    ServiceState,
    ck_data,
)


@pytest.fixture
def partner():
    facade = S2sBaseClass(auto_start=False)
    left, right = socket.socketpair()
    facade.partner_infos["Service_client"] = PartnerKeyInfo(left, ("127.0.0.1", 0))
    try:
        yield facade
    finally:
        facade.close()
        right.close()


def event(partner, name="Position", data=None, age=0):
    message = {
        "action": "event",
        "function": f"Update{name}Event",
        "args": json.dumps(data),
        "timestamp": time.time() - age,
        "monotonic_timestamp": time.monotonic() - age,
    }
    info = partner.partner_infos["Service_client"]
    with info.changed:
        info.event_queue.put_nowait(message)
        info.changed.notify_all()


@pytest.mark.parametrize(
    "name,key",
    [
        ("VehicleModeService_client_1", "VehicleModeService_client_1"),
        ("cockpit_perception_service_server", "cockpit_perception_service_server"),
        ("GNSSService_server_GNSSService_HD", "GNSSService_server_GNSSService_HD"),
    ],
)
def test_sat_string_members_preserve_role_and_instance(name, key):
    cfg = S2sBaseClass._members_config([name])
    alias, definition = next(iter(cfg.items()))
    assert member_key(alias, definition) == key


def test_same_service_client_and_server_can_be_initialized_together():
    cfg = S2sBaseClass._members_config([("Service", "client"), ("Service", "server")])
    assert {member_key(alias, definition) for alias, definition in cfg.items()} == {
        "Service_client",
        "Service_server",
    }


def test_sat_config_and_enum_values_match_reference():
    assert PartnerStartConfig("Service", "client_1", "Service", 600).args() == {
        "Service_1": {"role": "client", "name": "Service", "status": True, "heartbeat": 600}
    }
    assert FailType.FAILTYPE_BAD_PARAM.value == 609
    assert FailType.FAILTYPE_OTHER_ERROR.value == 616
    assert ServiceState.START.value == 0 and ServiceState.OFFLINE.value == 6


@pytest.mark.parametrize(
    "actual,expected,result",
    [
        ([1], [], False),
        ([], [], True),
        ("Ready", "READY", True),
        (1.23449, 1.2345, True),
        (12.345, 12.3, True),
        ({"x": [{"n": 1}, {"n": 2}]}, {"x": [{"n": 2}]}, True),
    ],
)
def test_fuzzy_match_uses_sat_rules(actual, expected, result):
    assert ck_data(actual, expected) is result


def test_event_returns_payload_and_latest_pop_is_non_destructive_to_other_events(partner):
    event(partner, data={"position": 1}, age=1)
    event(partner, "Other", {"position": 9})
    event(partner, data={"position": 2})
    assert partner.return_latest_event("Service_client", "Position", False) == {"position": 2}
    assert partner.chk_notify("Service_client", "Position", {"position": 2})
    assert partner.return_latest_event("Service_client", "Position") == {"position": 2}
    assert partner.ck_s2s_event("Service_client", "Position", {"position": 1}) == {"position": 1}
    assert partner.ck_s2s_event("Service_client", "Other", {"position": 9}) == {"position": 9}


def test_coming_event_ignores_matching_history(partner):
    event(partner, data={"position": 3}, age=1)
    timer = threading.Timer(0.02, event, args=(partner, "Position", {"position": 3}))
    timer.start()
    try:
        assert partner.ck_coming_event(
            "Service_client", "Position", {"position": 3}, timeout=0.2
        ) == {"position": 3}
        assert partner.return_latest_event("Service_client", "Position") == {"position": 3}
    finally:
        timer.join()


def test_coming_event_rejects_timing_outside_deviation(partner):
    timer = threading.Timer(0.01, event, args=(partner, "Position", {"position": 3}))
    timer.start()
    try:
        with pytest.raises(AssertionError):
            partner.ck_coming_event(
                "Service_client", "Position", {"position": 3}, timeout=0.2, deviation=0.02
            )
    finally:
        timer.join()


def test_response_retry_preserves_async_and_uses_total_timeout(partner, monkeypatch):
    calls = []

    def request(key, name, args, timeout, is_async):
        calls.append((timeout, is_async))
        return {"failtype": "FAILTYPE_SUCCESS", "result": json.dumps({"out": len(calls)})}

    monkeypatch.setattr(partner, "_request_response", request)
    assert partner.send_request_and_ck_resp(
        "Service_client", "GetPosition", {}, {"out": 3}, timeout=0.2, cycle_time=0.01, is_async=True
    )
    assert len(calls) == 3 and all(item[1] for item in calls)
    assert calls[0][0] > calls[1][0] > calls[2][0]


def test_v20_duplicate_names_require_separate_requests_and_preserve_unrelated(partner):
    info = partner.partner_infos["Service_client"]
    for name, data in [("Other", {}), ("Set", {"info": {"x": 2}}), ("Set", {"info": {"x": 1}})]:
        info.req_queue.put_nowait({"function": name, "args": json.dumps(data)})
    assert (
        partner.ck_s2s_req_v20("Service_client", ["Set", "Set"], [{"x": 1}, {"x": 2}])["function"]
        == "Set"
    )
    assert partner.ck_s2s_req("Service_client", "Other")


def test_empty_event_list_does_not_clear_requests_or_other_members(partner):
    event(partner, data={"position": 1})
    info = partner.partner_infos["Service_client"]
    info.req_queue.put_nowait({"function": "Set", "args": "{}"})
    partner.empty_event_list("Service_client")
    assert info.event_queue.empty() and not info.req_queue.empty()


def test_missing_auto_response_handler_does_not_invent_echo_reply(partner, monkeypatch, caplog):
    replies = []
    monkeypatch.setattr(partner, "send_method_response", lambda *a, **kw: replies.append((a, kw)))
    partner.register_auto_response("Service_client", "Set")
    partner._auto_reply("Service_client", {"function": "Set", "args": '{"x":1}'})
    assert not replies and "不发送默认业务响应" in caplog.text


def test_explicit_echo_extension_and_unregister(partner, monkeypatch):
    replies = []
    monkeypatch.setattr(partner, "send_method_response", lambda *a, **kw: replies.append((a, kw)))
    partner.register_auto_response("Service_client", "Set", echo=True)
    partner._auto_reply(
        "Service_client", {"function": "Set", "args": '{"x":1}', "request_id": 17}
    )
    assert replies == [(("Service_client", "Set", {"x": 1}), {"request_id": 17})]
    partner.unregister_auto_response("Service_client", "Set")
    partner._auto_reply("Service_client", {"function": "Set", "args": '{"x":2}'})
    assert len(replies) == 1


def test_named_instance_auto_response_uses_sat_handler_name(partner, monkeypatch):
    info = partner.partner_infos["Service_client"]
    partner.partner_infos["Service_server_BGM"] = info
    partner.partner_infos.add_alias("Alias_server", "Service_server_BGM")
    calls = []
    monkeypatch.setattr(
        partner, "send_method_response_Service_Set", lambda: calls.append("基础"), raising=False
    )
    monkeypatch.setattr(
        partner, "send_method_response_Service_BGM_Set", lambda: calls.append("实例"), raising=False
    )
    partner._auto_reply("Alias_server", {"function": "Set", "args": "{}"})
    assert calls == ["实例"]
    # 单测共享一个假 socket，仅保留一个所有者以便清理。
    del partner.partner_infos["Service_server_BGM"]
    with pytest.raises(ValueError):
        partner.empty_event_list("missing")
