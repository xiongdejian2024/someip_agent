import json
import random
import socket
import threading

import pytest

from someip_agent.soa import ipc
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


class Fragments:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def recv(self, _size):
        return next(self.chunks, b"")


def test_large_fragmented_document_decodes_once_not_once_per_partial_chunk(monkeypatch):
    calls = []
    original = json.JSONDecoder.decode

    def counted(self, text, *args, **kwargs):
        calls.append(len(text))
        return original(self, text, *args, **kwargs)

    monkeypatch.setattr(json.JSONDecoder, "decode", counted)
    messages = [{"args": json.dumps({"values": [4660, 43981] * 35000})}, {"args": "中文"}]
    raw = b" \n" + b"\t ".join(json.dumps(m, ensure_ascii=False).encode() for m in messages)
    assert (
        list(member_messages(Fragments(raw[i : i + 8192] for i in range(0, len(raw), 8192))))
        == messages
    )
    assert len(calls) == 2


@pytest.mark.parametrize("cut", [1, 2, 3, 7, 8192])
def test_member_stream_all_split_points_for_escaped_quotes_and_brackets(cut):
    messages = [
        {"args": chr(92) + '" }{ ' + chr(92) * 2 + " [中文]"},
        {"nested": [{"x": chr(92) * 3 + '"'}, {}]},
    ]
    raw = b"".join(json.dumps(m, ensure_ascii=False).encode() for m in messages)
    assert (
        list(member_messages(Fragments(raw[i : i + cut] for i in range(0, len(raw), cut))))
        == messages
    )


@pytest.mark.parametrize(
    "raw,error",
    [
        (b"[]", ValueError),
        (b'{"x":]', json.JSONDecodeError),
        (b'{"x":1', EOFError),
        (b'{"x":"\\', EOFError),
        (b'{"x":"\xe4', UnicodeDecodeError),
        (b"{}\xe4", UnicodeDecodeError),
    ],
)
def test_member_stream_rejects_malformed_or_incomplete_documents(raw, error):
    with pytest.raises(error):
        list(member_messages(Fragments([raw])))


def test_member_budget_applies_per_document_not_coalesced_read(monkeypatch):
    monkeypatch.setattr(ipc, "MAX_FRAME", 10)
    assert list(member_messages(Fragments([b'{"x":1}{"x":2}']))) == [{"x": 1}, {"x": 2}]
    with pytest.raises(ValueError, match="超限"):
        list(member_messages(Fragments([b'{"x":"12345"}'])))
    with pytest.raises(ValueError, match="超限"):
        list(member_messages(Fragments([b'{"x":"', "中文".encode(), b'"}'])))


def test_member_framing_randomized_nested_documents_and_fragment_boundaries():
    rng = random.Random(7711)
    alphabet = '中文 {}[]"' + chr(92) + "abc\n\t"
    messages = [
        {
            "nested": [
                {"text": "".join(rng.choice(alphabet) for _ in range(50))},
                {"number": rng.randrange(100000), "empty": {}},
            ]
        }
        for _ in range(80)
    ]
    raw = b" \n ".join(json.dumps(m, ensure_ascii=False).encode() for m in messages)
    chunks, index = [], 0
    while index < len(raw):
        length = rng.randrange(1, 128)
        chunks.append(raw[index : index + length])
        index += length
    assert list(member_messages(Fragments(chunks))) == messages
