from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

SOMEIP_HEADER_SIZE = 16
_HEADER = struct.Struct("!HHIHHBBBB")


class SomeIpDecodeError(ValueError):
    """SOME/IP 报文结构不合法。"""


@dataclass(frozen=True, slots=True)
class SomeIpHeader:
    service_id: int
    method_id: int
    length: int
    client_id: int = 0
    session_id: int = 1
    protocol_version: int = 1
    interface_version: int = 1
    message_type: int = 0x00
    return_code: int = 0x00

    @property
    def payload_length(self) -> int:
        return self.length - 8

    @property
    def total_length(self) -> int:
        return self.length + 8

    def validate(self) -> None:
        for label, value, maximum in (
            ("service_id", self.service_id, 0xFFFF),
            ("method_id", self.method_id, 0xFFFF),
            ("client_id", self.client_id, 0xFFFF),
            ("session_id", self.session_id, 0xFFFF),
            ("protocol_version", self.protocol_version, 0xFF),
            ("interface_version", self.interface_version, 0xFF),
            ("message_type", self.message_type, 0xFF),
            ("return_code", self.return_code, 0xFF),
        ):
            if not 0 <= value <= maximum:
                raise ValueError(f"{label} 超出范围: {value}")
        if self.length < 8:
            raise ValueError(f"SOME/IP length 不能小于 8: {self.length}")
        if self.protocol_version != 1:
            raise ValueError(f"不支持的 SOME/IP 协议版本: {self.protocol_version}")

    def encode(self) -> bytes:
        self.validate()
        return _HEADER.pack(
            self.service_id,
            self.method_id,
            self.length,
            self.client_id,
            self.session_id,
            self.protocol_version,
            self.interface_version,
            self.message_type,
            self.return_code,
        )

    @classmethod
    def decode(cls, data: bytes | bytearray | memoryview) -> SomeIpHeader:
        if len(data) < SOMEIP_HEADER_SIZE:
            raise SomeIpDecodeError(
                f"SOME/IP 头长度不足: 需要 {SOMEIP_HEADER_SIZE}，实际 {len(data)}"
            )
        header = cls(*_HEADER.unpack_from(data))
        try:
            header.validate()
        except ValueError as exc:
            raise SomeIpDecodeError(str(exc)) from exc
        return header


@dataclass(frozen=True, slots=True)
class SomeIpMessage:
    header: SomeIpHeader
    payload: bytes = b""

    @classmethod
    def build(
        cls,
        *,
        service_id: int,
        method_id: int,
        payload: bytes = b"",
        client_id: int = 0,
        session_id: int = 1,
        interface_version: int = 1,
        message_type: int = 0x00,
        return_code: int = 0x00,
    ) -> SomeIpMessage:
        header = SomeIpHeader(
            service_id=service_id,
            method_id=method_id,
            length=len(payload) + 8,
            client_id=client_id,
            session_id=session_id,
            interface_version=interface_version,
            message_type=message_type,
            return_code=return_code,
        )
        return cls(header=header, payload=bytes(payload))

    def encode(self) -> bytes:
        if self.header.payload_length != len(self.payload):
            raise ValueError(
                "SOME/IP length 与 payload 不一致: "
                f"header={self.header.payload_length}, payload={len(self.payload)}"
            )
        return self.header.encode() + self.payload

    @classmethod
    def decode(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        allow_trailing: bool = False,
    ) -> SomeIpMessage:
        header = SomeIpHeader.decode(data)
        total_length = header.total_length
        if total_length > len(data):
            raise SomeIpDecodeError(
                f"SOME/IP 报文被截断: 头声明 {total_length} 字节，实际 {len(data)} 字节"
            )
        if not allow_trailing and total_length != len(data):
            raise SomeIpDecodeError(
                f"SOME/IP 报文包含尾随数据: 头声明 {total_length} 字节，实际 {len(data)} 字节"
            )
        return cls(header=header, payload=bytes(data[SOMEIP_HEADER_SIZE:total_length]))


def decode_many(data: bytes | bytearray | memoryview) -> Iterator[SomeIpMessage]:
    """解析一个 UDP 数据报或 TCP 缓冲区中的连续 SOME/IP 报文。"""

    view = memoryview(data)
    offset = 0
    while offset < len(view):
        remaining = view[offset:]
        header = SomeIpHeader.decode(remaining)
        total_length = header.total_length
        if total_length > len(remaining):
            raise SomeIpDecodeError(
                f"第 {offset} 字节处报文被截断: 需要 {total_length}，剩余 {len(remaining)}"
            )
        yield SomeIpMessage.decode(remaining[:total_length])
        offset += total_length
