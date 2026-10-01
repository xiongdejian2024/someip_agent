"""Classic 显式 payload 部署归一化；不绕过尚未验收的 header/session 门禁。"""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from someip_agent.arxml.classic import ClassicReferenceResolver
from someip_agent.arxml.parser import ArxmlParser, _element_path
from someip_agent.arxml.wire_types import WireTypeError

FIXTURE = Path(__file__).parent / "fixtures/classic_service.arxml"
SCHEMA = {
    "type": "struct",
    "fields": [
        {"name": "tag", "type": "uint8"},
        {"name": "items", "type": "array", "max_length": 3, "element": {"type": "uint16"}},
        {"name": "tail", "type": "struct", "fields": [{"name": "value", "type": "uint8"}]},
    ],
}


def inputs():
    root = etree.fromstring(FIXTURE.read_bytes())
    props = root.xpath("//*[local-name()='SOMEIP-TRANSFORMATION-I-SIGNAL-PROPS-CONDITIONAL']")[0]
    for name, value in (
        ("SIZE-OF-STRUCT-LENGTH-FIELDS", "2"),
        ("SIZE-OF-ARRAY-LENGTH-FIELDS", "1"),
    ):
        etree.SubElement(props, name).text = value
    resolver = ClassicReferenceResolver(root, _element_path)
    bindings = [item.source for item in resolver.resolve("/Classic/UnrelatedEventTrigger")]
    return root, props, resolver, bindings


def test_classic_layout_reads_signal_props_and_description_not_ap_defaults():
    _, _, resolver, bindings = inputs()
    source = deepcopy(SCHEMA)
    schema = resolver.apply_layout(source, bindings)
    assert source == SCHEMA
    assert schema["length_bytes"] == 2 and schema["alignment_bytes"] == 8
    assert schema["fields"][1]["length_bytes"] == 1
    assert schema["fields"][1]["element"]["byte_order"] == "big"
    assert schema["fields"][2]["length_bytes"] == 2


def test_classic_parser_resolves_nested_payload_through_real_type_and_signal_references():
    root, _, _, _ = inputs()
    composite = etree.fromstring((FIXTURE.parent / "composite_service.arxml").read_bytes())
    package = composite.xpath("//*[local-name()='AR-PACKAGE']")[0]
    elements = package.xpath("./*[local-name()='ELEMENTS']")[0]
    for child in list(elements):
        if etree.QName(child).localname not in {"SW-BASE-TYPE", "IMPLEMENTATION-DATA-TYPE"}:
            elements.remove(child)
    root.xpath("./*[local-name()='AR-PACKAGES']")[0].append(package)
    prototype = root.xpath(
        "//*[local-name()='VARIABLE-DATA-PROTOTYPE'][*[local-name()='SHORT-NAME']='Position']"
    )[0]
    prototype.xpath("./*[local-name()='TYPE-TREF']")[0].text = "/Composite/Envelope"
    model = ArxmlParser().parse(etree.tostring(root), FIXTURE.name)
    signal = model.services[0].events[0].signals[0]
    assert signal.wire_error is None
    schema = signal.wire_schema
    assert schema["type"] == "struct" and schema["length_bytes"] == 2
    assert schema["fields"][2]["max_length"] == 3
    assert schema["fields"][2]["length_bytes"] == 1
    assert schema["fields"][3]["element"]["element"]["type"] == "uint8"
    assert schema["fields"][4]["fields"][0]["alignment_bytes"] == 8
    assert model.services[0].deployment_errors


@pytest.mark.parametrize(
    "tag,value,match",
    [
        ("PROTOCOL", "CUSTOM", "SOMEIP"),
        ("VERSION", "2.0.0", "SOMEIP"),
        ("TRANSFORMER-CLASS", "SAFETY", "SOMEIP"),
        ("HEADER-LENGTH", "128", "header"),
        ("ALIGNMENT", "63", "整字节"),
        ("BYTE-ORDER", "OPAQUE", "BYTE-ORDER"),
        ("SIZE-OF-STRUCT-LENGTH-FIELDS", "3", "LENGTH-FIELD"),
        ("SIZE-OF-ARRAY-LENGTH-FIELDS", "0", "变长数组"),
    ],
)
def test_invalid_classic_layout_is_rejected(tag, value, match):
    root, _, resolver, bindings = inputs()
    root.xpath(f"//*[local-name()='{tag}']")[0].text = value
    with pytest.raises(WireTypeError, match=match):
        resolver.apply_layout(SCHEMA, bindings)


def test_missing_composite_length_does_not_guess_from_numeric_message_type():
    _, props, resolver, bindings = inputs()
    props.remove(props.xpath("./*[local-name()='SIZE-OF-ARRAY-LENGTH-FIELDS']")[0])
    with pytest.raises(WireTypeError, match="LENGTH-FIELD"):
        resolver.apply_layout(SCHEMA, bindings)


@pytest.mark.parametrize("schema_file", ["AUTOSAR_00046.xsd", "AUTOSAR_00048.xsd", "unknown.xsd"])
def test_optional_fixed_length_defaults_only_for_identified_cp44_schema(schema_file):
    root, props, _, bindings = inputs()
    root.set(
        "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
        f"http://autosar.org/schema/r4.0 {schema_file}",
    )
    props.remove(props.xpath("./*[local-name()='SIZE-OF-STRUCT-LENGTH-FIELDS']")[0])
    props.remove(props.xpath("./*[local-name()='SIZE-OF-ARRAY-LENGTH-FIELDS']")[0])
    schema = deepcopy(SCHEMA)
    schema["fields"][1].pop("max_length")
    schema["fields"][1]["length"] = 3
    resolver = ClassicReferenceResolver(root, _element_path)
    if schema_file == "AUTOSAR_00046.xsd":
        deployed = resolver.apply_layout(schema, bindings)
        assert deployed["length_bytes"] == deployed["fields"][1]["length_bytes"] == 0
        assert deployed["fields"][2]["length_bytes"] == 0
    else:
        with pytest.raises(WireTypeError, match="LENGTH-FIELD"):
            resolver.apply_layout(schema, bindings)


def test_missing_classic_alignment_cannot_borrow_ap_default():
    root, _, resolver, bindings = inputs()
    alignment = root.xpath("//*[local-name()='ALIGNMENT']")[0]
    alignment.getparent().remove(alignment)
    with pytest.raises(WireTypeError, match="缺少明确 ALIGNMENT"):
        resolver.apply_layout(SCHEMA, bindings)


@pytest.mark.parametrize(
    "tag", ["TLV-DATA-IDS", "IMPLEMENTS-SOMEIP-STRING-HANDLING", "UNKNOWN-PROP"]
)
def test_unknown_or_tlv_classic_props_cannot_be_silently_ignored(tag):
    _, props, resolver, bindings = inputs()
    etree.SubElement(props, tag).text = "true"
    with pytest.raises(WireTypeError, match="未支持属性"):
        resolver.apply_layout(SCHEMA, bindings)


def test_classic_binding_does_not_silently_drop_nonzero_position_or_chain():
    _, _, resolver, bindings = inputs()
    bindings[0].start_position = 8
    with pytest.raises(WireTypeError, match="START-POSITION"):
        resolver.apply_layout(SCHEMA, bindings)
    bindings[0].start_position = 0
    bindings[0].transformer_paths.append("/Classic/Other")
    with pytest.raises(WireTypeError, match="唯一"):
        resolver.apply_layout(SCHEMA, bindings)


def test_different_signal_layouts_cannot_merge_into_one_parameter():
    root, _, resolver, bindings = inputs()
    signal = deepcopy(root.xpath("//*[local-name()='I-SIGNAL']")[0])
    signal.xpath("./*[local-name()='SHORT-NAME']")[0].text = "OtherSignal"
    signal.xpath(".//*[local-name()='SIZE-OF-ARRAY-LENGTH-FIELDS']")[0].text = "2"
    root.xpath("//*[local-name()='ELEMENTS']")[0].append(signal)
    other = bindings[0].model_copy(deep=True)
    other.signal_path = "/Classic/OtherSignal"
    with pytest.raises(WireTypeError, match="冲突"):
        ClassicReferenceResolver(root, _element_path).apply_layout(SCHEMA, [*bindings, other])


def test_classic_parser_applies_different_call_and_return_byte_orders():
    root, props, _, _ = inputs()
    tech = root.xpath("//*[local-name()='TRANSFORMATION-TECHNOLOGY']")[0]
    little = deepcopy(tech)
    little.xpath("./*[local-name()='SHORT-NAME']")[0].text = "LittleTransformer"
    little.xpath(".//*[local-name()='BYTE-ORDER']")[0].text = "MOST-SIGNIFICANT-BYTE-LAST"
    tech.getparent().append(little)
    transformation = deepcopy(root.xpath("//*[local-name()='DATA-TRANSFORMATION']")[0])
    transformation.xpath("./*[local-name()='SHORT-NAME']")[0].text = "LittleTransformation"
    transformation.xpath(".//*[local-name()='TRANSFORMER-CHAIN-REF']")[
        0
    ].text = "/Classic/LittleTransformer"
    tech.getparent().append(transformation)
    for name, little_endian in (("CallSignal", False), ("ReturnSignal", True)):
        signal = root.xpath(f"//*[local-name()='I-SIGNAL'][*[local-name()='SHORT-NAME']='{name}']")[
            0
        ]
        wrapper = etree.SubElement(signal, "TRANSFORMATION-I-SIGNAL-PROPSS")
        variant = deepcopy(props)
        wrapper.append(variant)
        if little_endian:
            variant.xpath("./*[local-name()='TRANSFORMER-REF']")[
                0
            ].text = "/Classic/LittleTransformer"
            signal.xpath(".//*[local-name()='DATA-TRANSFORMATION-REF']")[
                0
            ].text = "/Classic/LittleTransformation"
    model = ArxmlParser().parse(etree.tostring(root), FIXTURE.name)
    method = model.services[0].methods[0]
    assert method.input_signals[0].wire_schema["byte_order"] == "big"
    assert method.output_signals[0].wire_schema["byte_order"] == "little"
    assert model.services[0].deployment_errors  # payload 成功不等于 header/session 验收成功。
