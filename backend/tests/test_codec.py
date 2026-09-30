import struct

import pytest

from someip_agent.domain.models import SignalDataType, SignalDefinition
from someip_agent.protocol.codec import SignalCodec, SignalCodecError


def test_decodes_physical_signal_values() -> None:
    payload = struct.pack(">Hf", 120, 36.5)
    signals = [
        SignalDefinition(
            name="Speed",
            data_type=SignalDataType.UINT16,
            factor=0.5,
            unit="km/h",
        ),
        SignalDefinition(name="Temperature", data_type=SignalDataType.FLOAT32),
    ]
    values = SignalCodec.decode(payload, signals)
    assert values["Speed"] == 60
    assert values["Temperature"] == pytest.approx(36.5)


def test_rejects_truncated_signal() -> None:
    with pytest.raises(SignalCodecError, match="截断"):
        SignalCodec.decode(
            b"\x01",
            [SignalDefinition(name="Counter", data_type=SignalDataType.UINT32)],
        )
