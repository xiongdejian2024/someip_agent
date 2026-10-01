"""VSA 类型图与 CP 4.4.0 布局；不把有效元素数当线上字节长度。"""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from someip_agent.arxml.parser import ArxmlParser, _element_path
from someip_agent.arxml.transformation import apply_layout
from someip_agent.arxml.wire_types import WireTypeError, WireTypeResolver

FIXTURES = Path(__file__).parent / "fixtures"


def source(bits=16):
    root = etree.fromstring((FIXTURES / "composite_service.arxml").read_bytes())
    extra = etree.fromstring((FIXTURES / "vsa_type.xml").read_bytes())
    extra.xpath(".//*[local-name()='BASE-TYPE-SIZE']")[0].text = str(bits)
    root.xpath("//*[local-name()='AR-PACKAGE']/*[local-name()='ELEMENTS']")[0].extend(extra)
    return root


def resolver(root):
    return WireTypeResolver(
        {
            _element_path(e): e
            for e in root.iter()
            if isinstance(e.tag, str)
            and etree.QName(e).localname
            in {"SW-BASE-TYPE", "IMPLEMENTATION-DATA-TYPE", "APPLICATION-PRIMITIVE-DATA-TYPE"}
        }
    )


@pytest.mark.parametrize("bits", [8, 16, 32])
def test_vsa_type_keeps_source_names_and_derives_cp44_width(bits):
    types = resolver(source(bits))
    schema = types.resolve("/Composite/LinearWords")
    assert schema == {
        "type": "array",
        "max_length": 3,
        "element": {"type": "uint16"},
        "vsa": {
            "profile": "VSA_LINEAR",
            "size_name": "validElements",
            "payload_name": "words",
            "size_type": f"uint{bits}",
        },
    }
    deployed = apply_layout(
        schema, "MOST-SIGNIFICANT-BYTE-FIRST", "64", None, None, classic_cp44=True
    )
    assert deployed["length_bytes"] == bits // 8
    assert deployed["alignment_bytes"] == 8
    deployed["vsa"]["size_name"] = "mutated"
    assert types.resolve("/Composite/LinearWords") == schema


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("VSA_LINEAR", "VSA_SQUARE", "尚未接通"),
        ("<SHORT-NAME>words", "<SHORT-NAME>validElements", "重复"),
        ("VARIABLE-SIZE", "FIXED-SIZE", "变长维度"),
        ("ALL-INDICES-SAME-ARRAY-SIZE", "ALL-INDICES-DIFFERENT-ARRAY-SIZE", "HANDLING"),
        ("<ARRAY-SIZE>3", "<ARRAY-SIZE>256", "容量不足"),
        (
            "/Composite/CountValue</IMPLEMENTATION-DATA-TYPE-REF>",
            "/Composite/SignedValue</IMPLEMENTATION-DATA-TYPE-REF>",
            "uint8/16/32",
        ),
        (
            "/Composite/WordValue</IMPLEMENTATION-DATA-TYPE-REF>",
            "/Composite/FixedWords</IMPLEMENTATION-DATA-TYPE-REF>",
            "多维",
        ),
        (
            "/Composite/WordValue</IMPLEMENTATION-DATA-TYPE-REF>",
            "/Composite/LinearWords</IMPLEMENTATION-DATA-TYPE-REF>",
            "循环",
        ),
    ],
)
def test_malformed_vsa_is_not_silently_treated_as_structure(old, new, match):
    root = source(8)
    node = root.xpath(
        "//*[local-name()='IMPLEMENTATION-DATA-TYPE'][*[local-name()='SHORT-NAME']='LinearWords']"
    )[0]
    node.getparent().replace(
        node, etree.fromstring(etree.tostring(node).decode().replace(old, new).encode())
    )
    with pytest.raises(WireTypeError, match=match):
        resolver(root).resolve("/Composite/LinearWords")


def test_vsa_indicator_order_and_outer_dimensions_are_checked():
    root = source()
    children = root.xpath(
        "//*[local-name()='IMPLEMENTATION-DATA-TYPE']["
        "*[local-name()='SHORT-NAME']='LinearWords']/*[local-name()='SUB-ELEMENTS']"
    )[0]
    children.insert(0, children[1])
    with pytest.raises(WireTypeError):
        resolver(root).resolve("/Composite/LinearWords")
    root = source()
    indicator = root.xpath(
        "//*[local-name()='IMPLEMENTATION-DATA-TYPE-ELEMENT']["
        "*[local-name()='SHORT-NAME']='validElements']"
    )[0]
    etree.SubElement(indicator, "ARRAY-SIZE").text = "3"
    with pytest.raises(WireTypeError, match="外层"):
        resolver(root).resolve("/Composite/LinearWords")


def test_application_profile_cannot_disagree_with_mapped_implementation():
    root = source()
    application = root.xpath("//*[local-name()='APPLICATION-PRIMITIVE-DATA-TYPE']")[0]
    etree.SubElement(application, "DYNAMIC-ARRAY-SIZE-PROFILE").text = "VSA_LINEAR"
    with pytest.raises(WireTypeError, match="profile 不一致"):
        WireTypeResolver(
            resolver(root)._index, {"/Composite/LogicalWord": {"/Composite/WordValue"}}
        ).resolve("/Composite/LogicalWord")


def test_cp44_optional_fixed_prefixes_do_not_default_unidentified_dynamic_arrays():
    types = resolver(source())
    schema = types.resolve("/Composite/Envelope")
    schema["fields"][2] = {"name": "samples", **types.resolve("/Composite/LinearWords")}
    original = deepcopy(schema)
    deployed = apply_layout(
        schema, "MOST-SIGNIFICANT-BYTE-FIRST", "64", None, None, classic_cp44=True
    )
    assert schema == original
    assert deployed["length_bytes"] == 0
    assert deployed["fields"][1]["length_bytes"] == 0
    assert deployed["fields"][2]["length_bytes"] == 2
    assert deployed["fields"][4]["length_bytes"] == 0
    with pytest.raises(WireTypeError, match="LENGTH-FIELD"):
        apply_layout(schema, "MOST-SIGNIFICANT-BYTE-FIRST", "64", None, None)
    with pytest.raises(WireTypeError, match="冲突"):
        apply_layout(schema, "MOST-SIGNIFICANT-BYTE-FIRST", "64", "0", "4", classic_cp44=True)
    with pytest.raises(WireTypeError, match="LENGTH-FIELD"):
        apply_layout(
            types.resolve("/Composite/BoundedBytes"),
            "MOST-SIGNIFICANT-BYTE-FIRST",
            "64",
            None,
            None,
            classic_cp44=True,
        )


def test_parser_projects_vsa_as_array_with_original_dictionary_metadata():
    root = source()
    for reference in root.xpath("//*[local-name()='IMPLEMENTATION-DATA-TYPE-REF']"):
        if reference.text == "/Composite/FixedWords":
            reference.text = "/Composite/LinearWords"
    model = ArxmlParser().parse(etree.tostring(root), "vsa.arxml")
    schema = model.services[0].methods[0].input_signals[0].wire_schema
    assert schema["fields"][1]["vsa"]["size_name"] == "validElements"
    assert schema["fields"][1]["type"] == "array"
    assert not model.warnings
