"""复杂 ARXML 类型/映射/部署拒绝边界；真实黄金报文另在 veth 验证。"""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.domain.models import SignalDataType, SignalGeneratorConfig
from someip_agent.soa.catalog import CatalogBuildError, NativeCatalogRequest, build_native_bundle

FIXTURE = Path(__file__).parent / "fixtures/composite_service.arxml"


def parse(content=None):
    return ArxmlParser().parse(content or FIXTURE.read_bytes(), FIXTURE.name)


def bundle(model, **options):
    request = NativeCatalogRequest.model_validate(
        {"members": {"EnvelopeService": {"role": "server", **options}}}
    )
    return build_native_bundle(model, request, Settings(_env_file=None))


def test_composite_types_mapping_field_order_and_deployment_are_explicit():
    model = parse()
    assert not model.warnings
    method = model.services[0].methods[0]
    signal = method.input_signals[0]
    assert signal.data_type == SignalDataType.STRUCT
    schema = signal.wire_schema
    assert schema["length_bytes"] == 2 and schema["byte_order"] == "big"
    assert [field["name"] for field in schema["fields"]] == ["tag", "samples", "bytes", "nested"]
    fixed, variable = schema["fields"][1:3]
    assert fixed["length"] == 2 and fixed["length_bytes"] == 2
    assert variable["max_length"] == 3 and variable["length_bytes"] == 2
    assert schema["fields"][3]["fields"][0]["type"] == "int16"
    assert model.services[0].methods[1].input_signals[0].wire_schema["type"] == "uint16"
    assert bundle(model).catalog["EnvelopeService"]["methods"]["Transform"]["input"]["fields"][
        0
    ] == {"name": "payload", **schema}


def test_little_endian_is_not_overwritten_by_default_or_conflicting_ui():
    model = parse(
        FIXTURE.read_bytes().replace(b"MOST-SIGNIFICANT-BYTE-FIRST", b"MOST-SIGNIFICANT-BYTE-LAST")
    )
    schema = bundle(model).catalog["EnvelopeService"]["methods"]["Transform"]["input"]["fields"][0]
    assert schema["byte_order"] == "little"
    assert model.services[0].methods[0].input_signals[0].byte_order == "little"
    assert schema["fields"][1]["element"]["byte_order"] == "little"
    with pytest.raises(CatalogBuildError, match="冲突"):
        bundle(model, byte_order="big")
    assert bundle(model, byte_order="little")


@pytest.mark.parametrize(
    "before,after,match",
    [
        (b"<ARRAY-SIZE>2", b"<ARRAY-SIZE>0", "ARRAY-SIZE"),
        (b"FIXED-SIZE", b"UNKNOWN-SIZE", "SEMANTICS"),
        (b"<SIZE-OF-STRUCT-LENGTH-FIELD>2", b"<SIZE-OF-STRUCT-LENGTH-FIELD>3", "LENGTH-FIELD"),
        (b"<SIZE-OF-ARRAY-LENGTH-FIELD>2", b"<SIZE-OF-ARRAY-LENGTH-FIELD>0", "变长数组"),
        (b"<ALIGNMENT>8", b"<ALIGNMENT>32", "padding"),
        (
            b"/Composite/Layout</TRANSFORMATION-PROPS-REF>",
            b"/Other/Layout</TRANSFORMATION-PROPS-REF>",
            "完整引用",
        ),
        (
            b"/Composite/WordValue</IMPLEMENTATION-DATA-TYPE-REF>",
            b"/Other/WordValue</IMPLEMENTATION-DATA-TYPE-REF>",
            "完整引用",
        ),
        (b"<CATEGORY>STRUCTURE</CATEGORY>", b"<CATEGORY>UNION</CATEGORY>", "尚未实现"),
        (
            b"<ALIGNMENT>8</ALIGNMENT>",
            b"<ALIGNMENT>8</ALIGNMENT><IS-DYNAMIC-LENGTH-FIELD-SIZE>true</IS-DYNAMIC-LENGTH-FIELD-SIZE>",
            "未支持",
        ),
    ],
)
def test_unsupported_or_missing_layout_cannot_be_sent(before, after, match, caplog):
    model = parse(FIXTURE.read_bytes().replace(before, after))
    with pytest.raises(CatalogBuildError, match=match):
        bundle(model)
    assert any(record.exc_info for record in caplog.records)


def test_missing_transformation_cannot_apply_legacy_scalar_default_to_composite():
    root = etree.fromstring(FIXTURE.read_bytes())
    mapping = root.xpath(
        "//*[local-name()='TRANSFORMATION-PROPS-TO-SERVICE-INTERFACE-ELEMENT-MAPPING']"
    )[0]
    mapping.getparent().remove(mapping)
    model = parse(etree.tostring(root))
    assert model.services[0].methods[0].input_signals[0].wire_schema is None
    with pytest.raises(CatalogBuildError, match="transformation"):
        bundle(model)


def test_ambiguous_application_mapping_cannot_choose_first():
    root = etree.fromstring(FIXTURE.read_bytes())
    mapping = root.xpath("//*[local-name()='DATA-TYPE-MAP']")[0]
    other = deepcopy(mapping)
    other.xpath("./*[local-name()='IMPLEMENTATION-DATA-TYPE-REF']")[0].text = "/Composite/ByteValue"
    mapping.getparent().append(other)
    model = parse(etree.tostring(root))
    with pytest.raises(CatalogBuildError, match="唯一 DATA-TYPE-MAP"):
        bundle(model)


def test_fine_grained_mapping_is_not_silently_ignored():
    root = etree.fromstring(FIXTURE.read_bytes())
    etree.SubElement(
        root, "{http://autosar.org/schema/r4.0}SOMEIP-DATA-PROTOTYPE-TRANSFORMATION-PROPS"
    )
    model = parse(etree.tostring(root))
    with pytest.raises(CatalogBuildError, match="细粒度"):
        bundle(model)


def test_cached_type_graph_is_not_mutated_by_another_signal_or_bundle():
    model = parse()
    source = deepcopy(model.services[0].methods[0].input_signals[0].wire_schema)
    target = bundle(model)
    target.catalog["EnvelopeService"]["methods"]["Transform"]["input"]["fields"][0]["fields"][1][
        "length"
    ] = 99
    assert model.services[0].methods[0].input_signals[0].wire_schema == source
    assert model.services[0].events[0].signals[0].wire_schema == source


@pytest.mark.parametrize("kind", [SignalDataType.STRUCT, SignalDataType.ARRAY])
def test_scalar_generator_rejects_complex_types(kind):
    with pytest.raises(ValueError, match="标量"):
        SignalGeneratorConfig(data_type=kind)
