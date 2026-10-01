"""用真实 TCP socket 核对 IPC 选项；不把本端选项当作对端/线上 TCP 的证据。"""

import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.soa.catalog import NativeCatalogRequest, build_native_bundle
from someip_agent.soa.ipc import configure_ipc_socket, ipc_tcp_no_delay
from someip_agent.soa.operator import SOAOperator
from someip_agent.soa.partner import S2sBaseClass


@contextmanager
def connections():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname()) as client:
            peer, _address = listener.accept()
            with peer:
                yield client, peer


@pytest.mark.parametrize("value,expected", [(None, True), ("1", True), ("0", False)])
def test_option_default_and_switch_are_read_back_from_real_socket(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SOMEIP_AGENT_IPC_TCP_NODELAY", raising=False)
    else:
        monkeypatch.setenv("SOMEIP_AGENT_IPC_TCP_NODELAY", value)
    assert ipc_tcp_no_delay() is expected
    with connections() as (client, peer):
        timeout = client.gettimeout()
        configure_ipc_socket(client)
        assert bool(client.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)) is expected
        assert peer.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 0
        assert client.gettimeout() == timeout
        configure_ipc_socket(peer)
        assert bool(peer.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)) is expected


@pytest.mark.parametrize("value", ["", "true", "false", "2", "-1", " 1", "1 "])
def test_invalid_option_rejected_before_operator_or_partner_start(monkeypatch, value):
    monkeypatch.setenv("SOMEIP_AGENT_IPC_TCP_NODELAY", value)
    with pytest.raises(ValueError, match="只能为"):
        SOAOperator()
    with pytest.raises(ValueError, match="只能为"):
        S2sBaseClass(auto_start=False)


def test_configure_error_keeps_exception_stack(monkeypatch, caplog):
    monkeypatch.setenv("SOMEIP_AGENT_IPC_TCP_NODELAY", "1")
    connection = socket.socket()
    connection.close()
    with pytest.raises(OSError):
        configure_ipc_socket(connection)
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("value", ["0", "1"])
def test_operator_control_connection_configures_only_its_socket(monkeypatch, value):
    monkeypatch.setenv("SOMEIP_AGENT_IPC_TCP_NODELAY", value)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        operator = SOAOperator(operator_port=listener.getsockname()[1])
        try:
            operator.create_socket()
            peer, _address = listener.accept()
            with peer:
                # macOS 返回非零选项位（实测为 4），不是固定整数 1。
                assert bool(
                    operator.tcp_socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
                ) is (value == "1")
                assert peer.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 0
        finally:
            operator.stop_operator()


@pytest.mark.parametrize("value", [None, "0", "1"])
def test_native_control_and_member_options_are_actual_socket_values(
    monkeypatch, tmp_path, native_runtime, value
):
    if value is None:
        monkeypatch.delenv("SOMEIP_AGENT_IPC_TCP_NODELAY", raising=False)
    else:
        monkeypatch.setenv("SOMEIP_AGENT_IPC_TCP_NODELAY", value)
    expected = value != "0"
    fixture = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"
    model = ArxmlParser().parse(fixture.read_bytes(), fixture.name)
    request = NativeCatalogRequest(
        application_name=f"ipc_{uuid4().hex[:12]}",
        members={"VehicleStatus": {"role": "server"}},
    )
    bundle = build_native_bundle(model, request, Settings(_env_file=None))
    catalog, config = bundle.write(tmp_path / "runtime")
    operator = SOAOperator(
        request.application_name,
        operator_port=0,
        binary=native_runtime,
        catalog=catalog,
        config=config,
        log_path=tmp_path / "native.log",
    )
    partner = S2sBaseClass(bundle.members, operator=operator, auto_restart=False)
    try:
        pong = operator.send_request("ping", print_result=False)
        assert "ipc_tcp_no_delay_v1" in pong["capabilities"]
        assert pong["ipc_tcp_no_delay"] is expected
        assert pong["member_ipc_tcp_no_delay"] == {"VehicleStatus_server": [expected]}
        assert (
            bool(operator.tcp_socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)) is expected
        )
        info = partner.partner_infos["VehicleStatus_server"]
        assert (
            bool(info.require_socket().getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY))
            is expected
        )
        assert operator.probe_liveness(1) >= 0
    finally:
        partner.close()
    assert operator.process.poll() == 0


@pytest.mark.parametrize("value", ["", "true", "false", "2", "-1", " 1", "1 "])
def test_native_invalid_option_fails_before_listening(monkeypatch, native_runtime, value):
    monkeypatch.setenv("SOMEIP_AGENT_IPC_TCP_NODELAY", value)
    result = subprocess.run(
        [native_runtime, "run", "--network", "-p", "0"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "native.ready" not in result.stdout
    assert "native.main" in result.stdout + result.stderr
    assert "SOMEIP_AGENT_IPC_TCP_NODELAY" in result.stdout + result.stderr
