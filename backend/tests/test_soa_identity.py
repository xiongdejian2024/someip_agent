"""新旧二进制契约的真实控制 socket；线上身份和恢复另由 veth 抓包验收。"""

import json
import logging
import socket
import threading
from contextlib import contextmanager

import pytest

from someip_agent.soa.identity import CAPABILITY, prepare_identities, verify_identities
from someip_agent.soa.ipc import recv_data_from, send_data_to
from someip_agent.soa.operator import NativeOperationError, SOAOperator
from someip_agent.soa.partner import S2sBaseClass

logger = logging.getLogger(__name__)
MEMBERS = {"DoorService": {"role": "client", "application_name": "consumer"}}
EXPECTED = {"DoorService_client": ("consumer", 0x7741)}
ADDRESS = {"DoorService_client": ["127.0.0.1", 1234]}


def capabilities(**values):
    return {
        "runtime": "vsomeip",
        "protocol": 1,
        "capabilities": [CAPABILITY],
        "configured_applications": {"routing": 0x7740, "consumer": 0x7741},
        **values,
    }


@contextmanager
def control(results):
    left, right = socket.socketpair()
    operator = SOAOperator()
    operator.tcp_socket = right
    right.settimeout(1)
    calls, errors = [], []

    def serve():
        try:
            for result in results:
                request = json.loads(recv_data_from(left))
                calls.append(request)
                send_data_to(
                    left,
                    {
                        "action": "response",
                        "function": request["function"],
                        "failtype": "FAILTYPE_SUCCESS",
                        "result": json.dumps(result),
                    },
                )
        except Exception as error:
            logger.exception("身份契约测试控制对端失败")
            errors.append(error)
        finally:
            left.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield operator, calls
    finally:
        operator.stop_operator()
        thread.join(timeout=2)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize(
    "result",
    [
        {"runtime": "vsomeip", "protocol": 1, "version": "3.5.10"},
        capabilities(capabilities=[]),
        capabilities(capabilities=CAPABILITY),
        capabilities(capabilities=[CAPABILITY, True]),
        capabilities(runtime="other"),
        capabilities(protocol=True),
        capabilities(protocol=2),
        capabilities(configured_applications=None),
        capabilities(configured_applications={}),
        capabilities(configured_applications={"routing": 0x7741, "consumer": 0x7741}),
        capabilities(configured_applications={"consumer": True}),
        capabilities(configured_applications={"consumer": 0xFFFF}),
        capabilities(configured_applications={"consumer": 0}),
        capabilities(configured_applications={"consumer": "0x7741"}),
    ],
)
def test_incompatible_binary_rejected_before_start_mutation(result, caplog, monkeypatch):
    with control([result]) as (operator, calls):
        monkeypatch.setattr(operator, "create_socket", lambda: None)
        partner = S2sBaseClass(operator=operator, attach=True, auto_start=False)
        try:
            with pytest.raises(NativeOperationError):
                partner.start_soa(MEMBERS)
            assert [call["function"] for call in calls] == ["ping"]
            assert not partner.partner_infos and not partner._member_configs
            assert partner._supervisor.thread is None and operator.process is None
        finally:
            partner.close()
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize(
    "config",
    [
        {"role": "client", "application_id": 0x7741},
        {"role": "client", "application_name": "undeclared"},
        {"role": "client", "application_name": "consumer", "application_id": 0x7742},
        {"role": "client", "application_name": "consumer", "application_id": True},
    ],
)
def test_bad_identity_selection_rejected_before_mutation(config):
    with control([capabilities()]) as (operator, calls):
        with pytest.raises(NativeOperationError):
            prepare_identities(operator, {"DoorService": config})
        assert [call["function"] for call in calls] == ["ping"]


def test_default_and_disabled_members_do_not_require_new_capability():
    operator = SOAOperator()
    assert prepare_identities(operator, {"DoorService": {"role": "client"}}) == {}
    assert (
        prepare_identities(
            operator, {"DoorService": {**MEMBERS["DoorService"], "enable": "disable"}}
        )
        == {}
    )
    verify_identities(operator, {}, {})


def test_negotiation_is_not_cached_across_runtime_replacement():
    with control([capabilities(), capabilities(capabilities=[])]) as (operator, calls):
        assert prepare_identities(operator, MEMBERS) == EXPECTED
        with pytest.raises(NativeOperationError):
            prepare_identities(operator, MEMBERS)
        assert len(calls) == 2


@pytest.mark.parametrize(
    "actual",
    [
        None,
        {},
        {"application_name": "routing", "application_id": 0x7740},
        {"application_name": "consumer", "application_id": 0x7742},
        {"application_name": "consumer", "application_id": True},
    ],
)
def test_actual_identity_cannot_be_replaced_by_requested_metadata(actual, caplog):
    with control([{"DoorService_client": actual}]) as (operator, calls):
        with pytest.raises(NativeOperationError):
            verify_identities(operator, EXPECTED, ADDRESS)
        assert [call["function"] for call in calls] == ["running_service"]
    assert any(record.exc_info for record in caplog.records)


def test_missing_member_address_is_rejected_even_if_runtime_claims_identity():
    with control(
        [{"DoorService_client": {"application_name": "consumer", "application_id": 0x7741}}]
    ) as (operator, _calls):
        with pytest.raises(NativeOperationError):
            verify_identities(operator, EXPECTED, {})


def test_start_verifies_observed_identity_before_connect(monkeypatch):
    actual = {"DoorService_client": {"application_name": "consumer", "application_id": 0x7741}}
    with control([capabilities(), ADDRESS, actual]) as (operator, calls):
        monkeypatch.setattr(operator, "create_socket", lambda: None)
        partner = S2sBaseClass(operator=operator, attach=True, auto_start=False)
        connected = []
        monkeypatch.setattr(partner, "_connect", lambda key, addr: connected.append((key, addr)))
        try:
            partner.start_soa(MEMBERS)
            assert [call["function"] for call in calls] == [
                "ping",
                "start_config",
                "running_service",
            ]
            assert connected == [("DoorService_client", ADDRESS["DoorService_client"])]
        finally:
            partner.close()


def test_incremental_restart_checks_retained_selector_again():
    with control([capabilities(capabilities=[])]) as (operator, calls):
        partner = S2sBaseClass(operator=operator, attach=True, auto_start=False)
        partner._member_configs["DoorService_client"] = MEMBERS["DoorService"].copy()
        try:
            with pytest.raises(NativeOperationError):
                partner.start_single_partner("DoorService", "client")
            assert [call["function"] for call in calls] == ["ping"]
            assert not partner.partner_infos
        finally:
            partner.close()


def test_wrong_actual_identity_never_opens_business_socket(monkeypatch):
    actual = {"DoorService_client": {"application_name": "routing", "application_id": 0x7740}}
    with control([capabilities(), ADDRESS, actual]) as (operator, calls):
        monkeypatch.setattr(operator, "create_socket", lambda: None)
        partner = S2sBaseClass(operator=operator, attach=True, auto_start=False)
        connected = []
        monkeypatch.setattr(partner, "_connect", lambda *_args: connected.append(True))
        try:
            with pytest.raises(NativeOperationError, match="实际"):
                partner.start_soa(MEMBERS)
            assert [call["function"] for call in calls] == [
                "ping",
                "start_config",
                "running_service",
            ]
            assert not connected and not partner._member_configs
            assert operator.process is None  # attach 不能终止外部运行时。
        finally:
            partner.close()
