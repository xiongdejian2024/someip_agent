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
