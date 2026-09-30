import pytest

from someip_agent.protocol.someip import SomeIpDecodeError, SomeIpMessage, decode_many


def test_someip_round_trip() -> None:
    message = SomeIpMessage.build(
        service_id=0x1234,
        method_id=0x8001,
        payload=b"\x01\x02\x03",
        client_id=0x10,
        session_id=0x20,
        message_type=0x02,
    )
    decoded = SomeIpMessage.decode(message.encode())
    assert decoded == message
    assert decoded.header.length == 11
    assert decoded.header.total_length == 19


def test_decode_many() -> None:
    first = SomeIpMessage.build(service_id=1, method_id=2, payload=b"abc")
    second = SomeIpMessage.build(service_id=3, method_id=4, payload=b"xyz")
    assert list(decode_many(first.encode() + second.encode())) == [first, second]


def test_rejects_truncated_message() -> None:
    encoded = SomeIpMessage.build(service_id=1, method_id=2, payload=b"payload").encode()
    with pytest.raises(SomeIpDecodeError, match="截断"):
        SomeIpMessage.decode(encoded[:-1])
