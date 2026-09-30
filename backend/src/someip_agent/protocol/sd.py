from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

from .someip import SomeIpDecodeError, SomeIpMessage

SD_SERVICE_ID = 0xFFFF
SD_METHOD_ID = 0x8100
SD_PORT = 30490
_SD_PREFIX = struct.Struct("!B3sI")


class SdEntryType(IntEnum):
    FIND_SERVICE = 0x00
    OFFER_SERVICE = 0x01
    STOP_OFFER_SERVICE = 0x01  # TTL=0
    SUBSCRIBE_EVENTGROUP = 0x06
    SUBSCRIBE_EVENTGROUP_ACK = 0x07


@dataclass(frozen=True, slots=True)
class SdEntry:
    entry_type: int
    index_first_option: int
    index_second_option: int
    number_first_options: int
    number_second_options: int
    service_id: int
    instance_id: int
    major_version: int
    ttl: int
    minor_version: int | None = None
    counter: int | None = None
    eventgroup_id: int | None = None

    @property
    def is_eventgroup(self) -> bool:
        return self.entry_type in (0x06, 0x07)

    @classmethod
    def decode(cls, data: bytes) -> SdEntry:
        if len(data) != 16:
            raise SomeIpDecodeError(f"SOME/IP-SD Entry 必须为 16 字节，实际 {len(data)}")
        entry_type, index1, index2, option_counts = struct.unpack_from("!BBBB", data)
        service_id, instance_id, major = struct.unpack_from("!HHB", data, 4)
        ttl = int.from_bytes(data[9:12], "big")
        first_count = option_counts >> 4
        second_count = option_counts & 0x0F
        if entry_type in (0x06, 0x07):
            reserved_counter, eventgroup_id = struct.unpack_from("!HH", data, 12)
            return cls(
                entry_type,
                index1,
                index2,
                first_count,
                second_count,
                service_id,
                instance_id,
                major,
                ttl,
                counter=reserved_counter & 0x0F,
                eventgroup_id=eventgroup_id,
            )
        minor = struct.unpack_from("!I", data, 12)[0]
        return cls(
            entry_type,
            index1,
            index2,
            first_count,
            second_count,
            service_id,
            instance_id,
            major,
            ttl,
            minor_version=minor,
        )

    def encode(self) -> bytes:
        if not 0 <= self.ttl <= 0xFFFFFF:
            raise ValueError("SOME/IP-SD TTL 必须在 0..0xFFFFFF")
        option_counts = (self.number_first_options << 4) | self.number_second_options
        prefix = struct.pack(
            "!BBBBHHB",
            self.entry_type,
            self.index_first_option,
            self.index_second_option,
            option_counts,
            self.service_id,
            self.instance_id,
            self.major_version,
        ) + self.ttl.to_bytes(3, "big")
        if self.is_eventgroup:
            tail = struct.pack("!HH", self.counter or 0, self.eventgroup_id or 0)
        else:
            tail = struct.pack("!I", self.minor_version or 0)
        return prefix + tail

    def summary(self) -> str:
        names = {
            0x00: "FindService",
            0x01: "StopOfferService" if self.ttl == 0 else "OfferService",
            0x06: "StopSubscribeEventgroup" if self.ttl == 0 else "SubscribeEventgroup",
            0x07: "SubscribeEventgroupAck" if self.ttl else "SubscribeEventgroupNack",
        }
        label = names.get(self.entry_type, f"Entry(0x{self.entry_type:02X})")
        suffix = f" eventgroup=0x{self.eventgroup_id:04X}" if self.eventgroup_id is not None else ""
        return (
            f"{label} service=0x{self.service_id:04X} instance=0x{self.instance_id:04X}"
            f" ttl={self.ttl}{suffix}"
        )


@dataclass(frozen=True, slots=True)
class SdOption:
    option_type: int
    reserved: int = 0
    data: bytes = b""

    @classmethod
    def decode_from(cls, data: bytes, offset: int) -> tuple[SdOption, int]:
        if len(data) - offset < 4:
            raise SomeIpDecodeError("SOME/IP-SD Option 头被截断")
        option_length = struct.unpack_from("!H", data, offset)[0]
        # Length 不包含前两个长度字节和 Type，仅包含 Reserved 与选项正文。
        total = option_length + 3
        if option_length < 1 or offset + total > len(data):
            remaining = len(data) - offset
            raise SomeIpDecodeError(
                f"SOME/IP-SD Option 长度非法: length={option_length}, remaining={remaining}"
            )
        option_type = data[offset + 2]
        reserved = data[offset + 3]
        return cls(option_type, reserved, data[offset + 4 : offset + total]), offset + total

    def encode(self) -> bytes:
        option_length = len(self.data) + 1
        return struct.pack("!HBB", option_length, self.option_type, self.reserved) + self.data


@dataclass(frozen=True, slots=True)
class SdPayload:
    flags: int = 0
    entries: tuple[SdEntry, ...] = field(default_factory=tuple)
    options: tuple[SdOption, ...] = field(default_factory=tuple)

    @classmethod
    def decode(cls, payload: bytes) -> SdPayload:
        if len(payload) < 12:
            raise SomeIpDecodeError(f"SOME/IP-SD payload 长度不足: {len(payload)}")
        flags, _reserved, entries_length = _SD_PREFIX.unpack_from(payload)
        entries_start = 8
        entries_end = entries_start + entries_length
        if entries_length % 16:
            raise SomeIpDecodeError(f"SOME/IP-SD Entry 数组长度不是 16 的倍数: {entries_length}")
        if entries_end + 4 > len(payload):
            raise SomeIpDecodeError("SOME/IP-SD Entry 数组被截断")
        entries = tuple(
            SdEntry.decode(payload[offset : offset + 16])
            for offset in range(entries_start, entries_end, 16)
        )
        options_length = struct.unpack_from("!I", payload, entries_end)[0]
        options_start = entries_end + 4
        options_end = options_start + options_length
        if options_end > len(payload):
            raise SomeIpDecodeError("SOME/IP-SD Option 数组被截断")
        options: list[SdOption] = []
        offset = options_start
        while offset < options_end:
            option, offset = SdOption.decode_from(payload[:options_end], offset)
            options.append(option)
        return cls(flags=flags, entries=entries, options=tuple(options))

    def encode(self) -> bytes:
        entries = b"".join(entry.encode() for entry in self.entries)
        options = b"".join(option.encode() for option in self.options)
        return (
            _SD_PREFIX.pack(self.flags, b"\x00\x00\x00", len(entries))
            + entries
            + struct.pack("!I", len(options))
            + options
        )

    def to_someip(self, *, session_id: int = 1) -> SomeIpMessage:
        return SomeIpMessage.build(
            service_id=SD_SERVICE_ID,
            method_id=SD_METHOD_ID,
            payload=self.encode(),
            session_id=session_id,
            interface_version=1,
            message_type=0x02,
        )

    def summary(self) -> str:
        if not self.entries:
            return "SOME/IP-SD（无 Entry）"
        return "; ".join(entry.summary() for entry in self.entries)


def is_sd_message(message: SomeIpMessage) -> bool:
    return message.header.service_id == SD_SERVICE_ID and message.header.method_id == SD_METHOD_ID
