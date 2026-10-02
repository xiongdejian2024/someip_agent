"""Classic 全路径绑定与拒绝边界，不把引用成功冒充序列化成功。"""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from someip_agent.arxml.classic import ClassicReferenceResolver
from someip_agent.arxml.parser import ArxmlParser, _element_path
from someip_agent.arxml.wire_types import WireTypeError
from someip_agent.config import Settings
from someip_agent.soa.catalog import CatalogBuildError, NativeCatalogRequest, build_native_bundle

FIXTURE = Path(__file__).parent / "fixtures/classic_service.arxml"


def root():
    return etree.fromstring(FIXTURE.read_bytes())


def parse(tree):
    return ArxmlParser().parse(etree.tostring(tree), FIXTURE.name)


def node(tree, tag):
    return tree.xpath(f"//*[local-name()='{tag}']")[0]


def test_full_paths_override_pdu_prefix_and_duplicate_interface_short_names():
    service = parse(root()).services[0]
    event = service.events[0]
    assert (service.name, service.service_id) == ("BodyService", 0x1234)
    assert (event.name, event.path) == ("DoorStatus", "/Classic/DoorStatus")
    assert event.signals[0].name == "Position"
    binding = event.classic_bindings[0]
    assert binding.target_path == "/Classic/DoorStatus/Position"
    assert binding.triggering_path == "/Classic/UnrelatedEventTrigger"
    assert binding.pdu_path == "/Classic/EventPdu"
    assert binding.mapping_path == "/Classic/EventPdu/EventMapping"
    assert binding.signal_path == "/Classic/EventSignal"
    assert binding.system_signal_path == "/Classic/EventSystemSignal"
    assert binding.transformation_paths == ["/Classic/Transformation"]
    assert binding.transformer_paths == ["/Classic/Transformer"]
    assert binding.start_position == 0
    assert binding.direction == "data"


@pytest.mark.parametrize("major", ["0", "0x00", "1", "0xfe"])
def test_classic_explicit_major_version_is_preserved_including_zero(major):
    tree = root()
    node(tree, "SERVER-SERVICE-MAJOR-VERSION").text = major
    service = parse(tree).services[0]
    assert service.major_version == int(major, 0)
    assert service.name == "BodyService" and service.service_id == 0x1234
    # 修正源版本并非解除尚未验收的 Classic 运行门禁。
    assert service.deployment_errors


@pytest.mark.parametrize("missing", ["SERVER-SERVICE-MAJOR-VERSION", "SD-SERVER-CONFIG"])
def test_classic_absent_version_retains_existing_default(missing):
    tree = root()
    element = node(tree, missing)
    element.getparent().remove(element)
    service = parse(tree).services[0]
    assert service.major_version == 1 and service.minor_version == 0
    assert service.deployment_errors


def test_rpc_uses_target_operation_not_first_operation_and_keeps_both_directions():
    service = parse(root()).services[0]
    assert len(service.methods) == 1
    method = service.methods[0]
    assert method.name == "SetDoor" and method.path == "/Classic/DoorControl/SetDoor"
    assert method.input_signals[0].name == "Target"
    assert method.output_signals[0].name == "Actual"
    assert {binding.direction for binding in method.classic_bindings} == {"input", "output"}
    assert not method.fire_and_forget


def test_repeated_ecu_context_mappings_are_deduplicated_only_for_same_target():
    tree = root()
    mapping = node(tree, "SENDER-RECEIVER-TO-SIGNAL-MAPPING")
    mapping.getparent().append(deepcopy(mapping))
    event = parse(tree).services[0].events[0]
    assert event.signals[0].name == "Position" and len(event.classic_bindings) == 1


@pytest.mark.parametrize(
    "tag,match",
    [
        ("I-PDU-REF", "I-SIGNAL-I-PDU"),
        ("I-SIGNAL-REF", "I-SIGNAL"),
        ("SYSTEM-SIGNAL-REF", "SYSTEM-SIGNAL"),
        ("TARGET-DATA-PROTOTYPE-REF", "VARIABLE-DATA-PROTOTYPE"),
        ("DATA-TRANSFORMATION-REF", "DATA-TRANSFORMATION"),
        ("TRANSFORMER-CHAIN-REF", "TRANSFORMATION-TECHNOLOGY"),
    ],
)
def test_missing_full_reference_cannot_borrow_same_short_name(tag, match, caplog):
    tree = root()
    reference = node(tree, tag)
    reference.text = "/Other/" + reference.text.rsplit("/", 1)[-1]
    resolver = ClassicReferenceResolver(tree, _element_path)
    with pytest.raises(WireTypeError, match=match):
        resolver.resolve("/Classic/UnrelatedEventTrigger")
    service = parse(tree).services[0]
    assert any(match in error for error in service.deployment_errors)
    assert not service.events[0].classic_bindings
    assert any(record.exc_info for record in caplog.records)


def test_existing_reference_to_wrong_element_kind_is_rejected():
    tree = root()
    node(tree, "I-PDU-REF").text = "/Classic/EventSignal"
    with pytest.raises(WireTypeError, match="I-SIGNAL-I-PDU"):
        ClassicReferenceResolver(tree, _element_path).resolve("/Classic/UnrelatedEventTrigger")


def test_distinct_targets_for_one_system_signal_are_ambiguous():
    tree = root()
    mapping = deepcopy(node(tree, "SENDER-RECEIVER-TO-SIGNAL-MAPPING"))
    node(mapping, "TARGET-DATA-PROTOTYPE-REF").text = "/Other/DoorStatus/WrongPosition"
    node(tree, "DATA-MAPPINGS").append(mapping)
    with pytest.raises(WireTypeError, match="唯一业务映射"):
        ClassicReferenceResolver(tree, _element_path).resolve("/Classic/UnrelatedEventTrigger")


@pytest.mark.parametrize("position", ["-1", "bad", ""])
def test_invalid_start_position_is_not_replaced_by_zero(position):
    tree = root()
    node(tree, "START-POSITION").text = position
    with pytest.raises(WireTypeError, match="START-POSITION"):
        ClassicReferenceResolver(tree, _element_path).resolve("/Classic/UnrelatedEventTrigger")


def test_signal_props_transformer_must_belong_to_declared_chain():
    tree = root()
    other = deepcopy(node(tree, "TRANSFORMATION-TECHNOLOGY"))
    node(other, "SHORT-NAME").text = "OtherTransformer"
    node(tree, "TRANSFORMATION-TECHNOLOGY").getparent().append(other)
    node(tree, "TRANSFORMER-REF").text = "/Classic/OtherTransformer"
    with pytest.raises(WireTypeError, match="不属于"):
        ClassicReferenceResolver(tree, _element_path).resolve("/Classic/UnrelatedEventTrigger")


def test_duplicate_full_path_is_isolated_not_last_value_wins():
    tree = root()
    signal = node(tree, "I-SIGNAL")
    signal.getparent().append(deepcopy(signal))
    with pytest.raises(WireTypeError, match="完整路径重复"):
        ClassicReferenceResolver(tree, _element_path).resolve("/Classic/UnrelatedEventTrigger")
    service = parse(tree).services[0]
    assert any("完整路径重复" in error for error in service.deployment_errors)
    assert not service.events[0].classic_bindings
    assert len(service.methods[0].classic_bindings) == 2


def test_complete_reference_chain_is_not_permission_to_guess_classic_payload():
    model = parse(root())
    assert model.services[0].events[0].classic_bindings
    # 显式标量布局已解析，但消息类型/session 未验证，不能因此解除服务门禁。
    assert model.services[0].events[0].signals[0].wire_schema == {
        "type": "uint16",
        "byte_order": "big",
        "alignment_bytes": 8,
    }
    request = NativeCatalogRequest(members={"BodyService": {"role": "server"}})
    with pytest.raises(CatalogBuildError, match="Classic SOME/IP transformer"):
        build_native_bundle(model, request, Settings(_env_file=None))


def test_header_properties_preserve_raw_values_and_model_roundtrip():
    tree = root()
    props = node(tree, "SOMEIP-TRANSFORMATION-I-SIGNAL-PROPS-CONDITIONAL")
    node(props, "MESSAGE-TYPE").text = "0"
    service = parse(tree).services[0]
    binding = service.events[0].classic_bindings[0]
    metadata = binding.header_properties[0]
    assert metadata.message_type_raw == "0"  # 不把数值0转换成 REQUEST 或 NOTIFICATION。
    assert metadata.session_handling_sr_raw == "SESSION-HANDLING-ACTIVE"
    assert metadata.protocol_raw == "SOMEIP"
    assert metadata.transformer_version_raw == "1.0.0"
    assert metadata.header_length_bits_raw == "64"
    assert metadata.description_interface_version_raw == "1"
    assert metadata.signal_interface_version_raw is None
    assert metadata.signal_props_present and metadata.description_count == 1
    assert service.model_validate_json(service.model_dump_json()) == service
    legacy = binding.model_dump()
    legacy.pop("header_properties")
    assert binding.model_validate(legacy).header_properties == []
    assert service.deployment_errors


def test_missing_signal_header_props_are_explicit_not_inherited_from_other_signal():
    service = parse(root()).services[0]
    bindings = service.methods[0].classic_bindings
    for binding in bindings:
        metadata = binding.header_properties[0]
        assert not metadata.signal_props_present
        assert metadata.message_type_raw is metadata.session_handling_sr_raw is None
        assert metadata.description_interface_version_raw == "1"


def test_multiple_signal_variants_are_preserved_not_first_value_wins():
    tree = root()
    props = node(tree, "SOMEIP-TRANSFORMATION-I-SIGNAL-PROPS-CONDITIONAL")
    node(props, "MESSAGE-TYPE").text = "0"
    other = deepcopy(props)
    node(other, "MESSAGE-TYPE").text = "3"
    props.getparent().append(other)
    service = parse(tree).services[0]
    metadata = service.events[0].classic_bindings[0].header_properties
    assert len(metadata) == 2
    assert {item.message_type_raw for item in metadata} == {"0", "3"}
    assert service.events[0].signals[0].wire_schema is None
    assert service.deployment_errors


def test_multiple_transformer_descriptions_do_not_project_first_interface_version():
    tree = root()
    description = node(tree, "SOMEIP-TRANSFORMATION-DESCRIPTION")
    other = deepcopy(description)
    node(other, "INTERFACE-VERSION").text = "2"
    description.getparent().append(other)
    service = parse(tree).services[0]
    metadata = service.events[0].classic_bindings[0].header_properties[0]
    assert metadata.description_count == 2
    assert metadata.description_interface_version_raw is None
    assert service.events[0].signals[0].wire_schema is None
    assert service.deployment_errors


def test_return_reference_to_different_operation_cannot_overwrite_call_target():
    tree = root()
    node(tree, "RETURN-SIGNAL-REF").getparent().remove(node(tree, "RETURN-SIGNAL-REF"))
    mapping = deepcopy(node(tree, "CLIENT-SERVER-TO-SIGNAL-MAPPING"))
    node(mapping, "CALL-SIGNAL-REF").tag = "RETURN-SIGNAL-REF"
    node(mapping, "RETURN-SIGNAL-REF").text = "/Classic/ReturnSystemSignal"
    node(mapping, "TARGET-OPERATION-REF").text = "/Classic/DoorControl/UnrelatedFirstOperation"
    node(tree, "DATA-MAPPINGS").append(mapping)
    service = parse(tree).services[0]
    assert any("同一 Header ID" in error for error in service.deployment_errors)
    assert service.methods[0].path == "/Classic/DoorControl/SetDoor"
    assert len(service.methods[0].classic_bindings) == 1


def test_sender_receiver_method_keeps_data_target_and_blocks_unverified_wire_semantics():
    tree = root()
    node(tree, "HEADER-ID").text = "0x12340002"
    node(tree, "ROUTING-GROUP-REF").text = "/Routing/BodyService"
    service = parse(tree).services[0]
    method = next(item for item in service.methods if item.method_id == 2)
    assert method.fire_and_forget and method.path == "/Classic/DoorStatus"
    assert method.input_signals[0].name == "Position"
    assert method.classic_bindings[0].direction == "data"
    request = NativeCatalogRequest(members={"BodyService": {"role": "server"}})
    with pytest.raises(CatalogBuildError, match="Classic SOME/IP transformer"):
        build_native_bundle(parse(tree), request, Settings(_env_file=None))


def test_bindings_are_preserved_by_model_json_round_trip():
    model = parse(root())
    restored = type(model).model_validate_json(model.model_dump_json())
    assert (
        restored.services[0].methods[0].classic_bindings
        == model.services[0].methods[0].classic_bindings
    )
    restored.services[0].methods[0].classic_bindings[0].transformer_paths.append("/Changed")
    assert model.services[0].methods[0].classic_bindings[0].transformer_paths == [
        "/Classic/Transformer"
    ]
