import json
import socket
import threading

import pytest

from someip_agent.soa.ipc import member_messages, recv_data_from, send_data_to
from someip_agent.soa.partner import S2sBaseClass


def test_control_framing_fragmented_utf8_and_coalesced() -> None:
    left, right = socket.socketpair()
    payload = {"args": "中文 }{ 测试", "function": "start_config"}
    try:
        send_data_to(left, payload)
        send_data_to(left, {"function": "ping"})
        assert json.loads(recv_data_from(right)) == payload
        assert json.loads(recv_data_from(right)) == {"function": "ping"}
    finally:
        left.close()
        right.close()


def test_member_stream_partial_utf8_braces_and_multiple_messages() -> None:
    left, right = socket.socketpair()
    messages = [{"args": '中文 }{ \\"', "function": "Test"}, {"args": {"n": [1, 2]}}]
    raw = b"".join(json.dumps(m, ensure_ascii=False).encode() for m in messages)

    def sender() -> None:
        for byte in raw:
            left.sendall(bytes([byte]))
        left.close()

    thread = threading.Thread(target=sender)
    thread.start()
    try:
        assert list(member_messages(right)) == messages
    finally:
        right.close()
        thread.join()


def test_control_eof_mid_frame_is_not_busy_loop() -> None:
    left, right = socket.socketpair()
    left.sendall(b"00000020{}")
    left.close()
    try:
        with pytest.raises(EOFError):
            recv_data_from(right)
    finally:
        right.close()


def test_sat_tuple_and_dictionary_start_config() -> None:
    cfg = S2sBaseClass._members_config([("DoorService", "client_1", "DoorService", 600)])
    assert cfg == {
        "DoorService_1": {
            "service": "DoorService",
            "role": "client",
            "name": "DoorService",
            "status": True,
            "heartbeat": 600,
        }
    }
    dictionary = {"DoorService": {"role": "server", "name": "DoorService"}}
    assert S2sBaseClass._members_config(dictionary) is dictionary
