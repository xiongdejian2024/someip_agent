"""只校验原生结果契约；协议解码由原生 CTest、监听/PCAP 集成另行证明。"""

from __future__ import annotations

import logging

import pytest

from someip_agent.agent.evidence import EvidenceTools
from someip_agent.domain.models import MonitorMessage
from someip_agent.protocol.sd_metadata import project_sd, read_sd


def record(kind=1, ttl=3):
    group = kind in (4, 5, 6, 7)
    return {
        "sd": {
            "schema_version": 1,
            "decoder": "vsomeip-3.5.10",
            "flags": 192,
            "entries": [
                {
                    "entry_type": kind,
                    "service_id": 0x1234,
                    "instance_id": 2,
                    "major_version": 1,
                    "ttl": ttl,
                    "minor_version": None if group else 2,
                    "eventgroup_id": 7 if group else None,
                    "counter": 3 if group else None,
                    "option_indices": [[], []],
                }
            ],
            "options": [],
        }
    }


@pytest.mark.parametrize(
    "kind,ttl,name",
    [
        (0, 3, "FindService"),
        (1, 3, "OfferService"),
        (1, 0, "StopOfferService"),
        (6, 3, "SubscribeEventgroup"),
        (6, 0, "StopSubscribeEventgroup"),
        (7, 3, "SubscribeEventgroupAck"),
        (7, 0, "SubscribeEventgroupNack"),
        (2, 3, "Unknown(0x02)"),
        (4, 3, "Unknown(0x04)"),
        (5, 3, "Unknown(0x05)"),
    ],
)
def test_entry_display_retains_ttl_and_library_type(kind, ttl, name):
    payload = read_sd(record(kind, ttl))
    assert payload.entries[0].name == name
    assert payload.summary().startswith(name)
    assert "service=0x1234 instance=0x0002" in payload.summary()


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_type", True),
        ("entry_type", 1.0),
        ("entry_type", "1"),
        ("entry_type", 255),
        ("service_id", -1),
        ("service_id", 65536),
        ("service_id", "4660"),
        ("service_id", True),
        ("instance_id", 65536),
        ("major_version", 256),
        ("ttl", -1),
        ("ttl", 0x1000000),
        ("minor_version", None),
        ("minor_version", 0x100000000),
        ("eventgroup_id", 7),
        ("counter", 0),
        ("option_indices", [[]]),
        ("option_indices", [[], [], []]),
        ("option_indices", [[0], []]),
        ("option_indices", [[0, 2], []]),
        ("option_indices", [list(range(16)), []]),
        ("option_indices", [[255, 0], []]),
    ],
)
def test_rejects_invalid_entry_contract(field, value):
    source = record()
    source["sd"]["entries"][0][field] = value
    with pytest.raises(ValueError):
        read_sd(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("decoder", "python"),
        ("flags", "192"),
        ("flags", 256),
        ("flags", True),
        ("entries", [record()["sd"]["entries"][0]] * 4097),
        ("options", [{"option_type": 4, "length": 9}] * 4097),
        ("options", [{"option_type": 256, "length": 9}]),
        ("options", [{"option_type": 4, "length": 0}]),
        ("options", [{"option_type": 4, "length": 65536}]),
    ],
)
def test_rejects_invalid_payload_contract(field, value):
    source = record()
    source["sd"][field] = value
    with pytest.raises(ValueError):
        read_sd(source)


def test_options_and_empty_result():
    source = record()
    source["sd"]["options"] = [{"option_type": 4, "length": 9}] * 2
    source["sd"]["entries"][0]["option_indices"] = [[0, 1], [1]]
    assert read_sd(source).entries[0].option_indices == [[0, 1], [1]]
    source["sd"]["entries"] = []
    assert read_sd(source).summary() == "SOME/IP-SD（无 Entry）"


@pytest.mark.parametrize("error", ["原生报错", "", None, True, "x" * 2049])
def test_error_record_never_falls_back_to_valid_sd(error):
    source = record()
    source["sd_error"] = error
    with pytest.raises(ValueError):
        read_sd(source)


def test_missing_metadata_keeps_raw_bytes_and_logs_full_exception(caplog):
    # 即使 raw bytes 为合法空 SD，也不得绕过缺失的原生证据。
    raw = "000000000000000000000000"
    summary, metadata = project_sd({"payload_hex": raw}, logging.getLogger(__name__))
    assert "不使用 Python 解码回退" in summary
    assert "sd_error" in metadata and "sd" not in metadata
    assert any(item.exc_info for item in caplog.records)
    message = MonitorMessage(
        service_id=0xFFFF,
        method_id=0x8100,
        is_sd=True,
        payload_hex=raw,
        payload_size=12,
        metadata=metadata,
    )
    assert "error" in EvidenceTools.decode_sd(message)
    assert message.payload_hex == raw


def test_agent_uses_structured_result_not_raw_bytes():
    message = MonitorMessage(
        service_id=0xFFFF,
        method_id=0x8100,
        is_sd=True,
        payload_hex="ff",
        payload_size=1,
        metadata=record(),
    )
    result = EvidenceTools.decode_sd(message)
    assert result["entries"][0]["service_id"] == 0x1234
    assert result["entries"][0]["type"] == "OfferService"
    assert result["flags"] == 192
    assert result["option_count"] == 0


def test_contract_rejects_extra_fields():
    source = record()
    source["sd"]["guessed"] = True
    with pytest.raises(ValueError):
        read_sd(source)
