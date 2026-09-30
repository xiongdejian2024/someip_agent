from __future__ import annotations

import struct
from collections.abc import Sequence

from someip_agent.domain.models import SignalDataType, SignalDefinition


class SignalCodecError(ValueError):
    pass


_FORMATS: dict[SignalDataType, tuple[str, int]] = {
    SignalDataType.BOOLEAN: ("?", 1),
    SignalDataType.UINT8: ("B", 1),
    SignalDataType.UINT16: ("H", 2),
    SignalDataType.UINT32: ("I", 4),
    SignalDataType.UINT64: ("Q", 8),
    SignalDataType.INT8: ("b", 1),
    SignalDataType.INT16: ("h", 2),
    SignalDataType.INT32: ("i", 4),
    SignalDataType.INT64: ("q", 8),
    SignalDataType.FLOAT32: ("f", 4),
    SignalDataType.FLOAT64: ("d", 8),
}


class SignalCodec:
    """按 ARXML 投影中的顺序解码基础 SOME/IP payload。

    复杂 struct/union/动态数组会在后续由 autosar-data 适配层展开；这里严格拒绝
    截断数据，避免用猜测值污染波形和智能体证据。
    """

    @classmethod
    def decode(
        cls, payload: bytes, signals: Sequence[SignalDefinition]
    ) -> dict[str, bool | int | float | str]:
        values: dict[str, bool | int | float | str] = {}
        offset = 0
        for index, signal in enumerate(signals):
            value, consumed = cls._decode_one(payload[offset:], signal, index == len(signals) - 1)
            offset += consumed
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                physical: bool | int | float | str = value * signal.factor + signal.offset
            else:
                physical = value
            values[signal.name] = physical
        return values

    @staticmethod
    def _decode_one(
        data: bytes, signal: SignalDefinition, is_last: bool
    ) -> tuple[bool | int | float | str, int]:
        if signal.data_type in _FORMATS:
            format_code, size = _FORMATS[signal.data_type]
            if len(data) < size:
                raise SignalCodecError(
                    f"信号 {signal.name} 数据被截断: 需要 {size} 字节，剩余 {len(data)}"
                )
            endian = ">" if signal.byte_order == "big" else "<"
            return struct.unpack_from(endian + format_code, data)[0], size
        if signal.data_type == SignalDataType.STRING:
            if len(data) < 4:
                raise SignalCodecError(f"字符串信号 {signal.name} 缺少 4 字节长度字段")
            length = int.from_bytes(data[:4], signal.byte_order)
            if len(data) < 4 + length:
                raise SignalCodecError(
                    f"字符串信号 {signal.name} 被截断: 声明 {length}，剩余 {len(data) - 4}"
                )
            try:
                return data[4 : 4 + length].decode("utf-8"), 4 + length
            except UnicodeDecodeError as exc:
                raise SignalCodecError(f"字符串信号 {signal.name} 不是有效 UTF-8") from exc
        if signal.data_type == SignalDataType.BYTES:
            if not is_last:
                raise SignalCodecError(f"非末尾 BYTES 信号 {signal.name} 缺少长度定义")
            return data.hex(), len(data)
        raise SignalCodecError(f"不支持的数据类型: {signal.data_type}")
