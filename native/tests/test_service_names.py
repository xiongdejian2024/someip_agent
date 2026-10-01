"""名称审计正反向验证；测试模型不作为生产目录。"""

from copy import deepcopy
from pathlib import Path

import pytest
from audit_service_names import audit_event_groups, audit_names, audit_vsa_declarations
from lxml import etree
from someip_agent.arxml.parser import ArxmlParser
from someip_agent.domain.models import (
    ArxmlModel,
    ClassicHeaderProperties,
    ClassicSignalBinding,
    EventDefinition,
    ServiceDefinition,
)


def inputs():
    model = ArxmlModel(
        source_name="名称审计夹具",
        services=[
            ServiceDefinition(
                name="SourceService", service_id=0x1234, path="/I/SourceService"
            )
        ],
    )
    definitions = [{"service_name": "SourceService", "service_id": "0x1234"}]
    # 同一服务可能有多个 ECU/client 行，不能误判为多个业务服务。
    communication = [{"vlan_list": [{"service_list": definitions * 2}]}]
    return model, definitions, deepcopy(communication)


def test_header_raw_counts_deduplicate_repeated_ecu_bindings_without_runtime_claim():
    model, definitions, communication = inputs()
    metadata = ClassicHeaderProperties(
        transformer_path="/T/Transformer",
        signal_props_present=True,
        description_count=1,
        message_type_raw="0",
        session_handling_sr_raw="SESSION-HANDLING-ACTIVE",
    )
    binding = ClassicSignalBinding(
        triggering_path="/Trigger/ECU1",
        pdu_path="/PDU/Event",
        mapping_path="/PDU/Event/Mapping",
        signal_path="/Signal/Event",
        system_signal_path="/SystemSignal/Event",
        target_path="/I/SourceService/Event",
        direction="data",
        start_position=0,
        transformation_paths=["/T/Transformation"],
        transformer_paths=["/T/Transformer"],
        header_properties=[metadata],
    )
    other = binding.model_copy(deep=True)
    other.triggering_path = "/Trigger/ECU2"
    model.services[0].events = [
        EventDefinition(name="Event", classic_bindings=[binding, other])
    ]
    before = model.model_dump()
    result = audit_names(model, definitions, communication)
    headers = result["classic_header_metadata"]
    assert headers["unique_signal_variant_count"] == 1
    assert headers["message_type_raw_counts"] == {"0": 1}
    assert headers["session_handling_sr_raw_counts"] == {"SESSION-HANDLING-ACTIVE": 1}
    assert not headers["runtime_verified"] and not result["runtime_verified"]
    assert model.model_dump() == before
    # 相同内容的两个源变体仍有歧义，不能因 JSON 相同合并成一个。
    for record in model.services[0].events[0].classic_bindings:
        record.header_properties.append(metadata.model_copy(deep=True))
    headers = audit_names(model, definitions, communication)["classic_header_metadata"]
    assert headers["unique_signal_variant_count"] == 2
    assert headers["message_type_raw_counts"] == {"0": 2}


def test_names_match_two_sources_without_applying_vehicle_endpoints():
    model, definitions, communication = inputs()
    definitions.append({"service_name": "OtherSourceService", "service_id": "0x1235"})
    before = model.model_dump()
    result = audit_names(model, definitions, communication)
    assert result["verified"] and result["service_count"] == 1
    assert result["communication_row_count"] == 2
    assert result["matrix_service_count"] == 2
    assert model.model_dump() == before
    assert "不证明序列化" in result["scope"]
    assert result["wire_schema_errors"] == []
    assert result["wire_schema_error_groups"] == {}


def test_classic_source_bindings_are_reported_without_claiming_runtime_support():
    fixture = (
        Path(__file__).resolve().parents[2]
        / "backend/tests/fixtures/classic_service.arxml"
    )
    model = ArxmlParser().parse(fixture.read_bytes(), fixture.name)
    definitions = [{"service_name": "BodyService", "service_id": "0x1234"}]
    communications = [{"vlan_list": [{"service_list": definitions}]}]
    result = audit_names(model, definitions, communications)
    assert result["classic_bound_member_count"] == 2
    assert result["classic_reference_binding_count"] == 3
    assert result["deployment_error_count"] == 1
    assert result["runtime_verified"] is False
    assert (
        result["classic_bindings"][0]["member_path"] == "/Classic/DoorControl/SetDoor"
    )
    assert result["classic_bindings"][0]["bindings"][0]["direction"] == "input"
    result["deployment_errors"][0]["errors"].clear()
    assert model.services[0].deployment_errors
    assert len(result["wire_schema_errors"]) == result["wire_schema_error_count"]
    assert (
        sum(result["wire_schema_error_groups"].values())
        == result["wire_schema_error_count"]
    )


def event_inputs():
    model, definitions, _ = inputs()
    model.services[0].events = [
        EventDefinition(name="ArxmlName", event_id=0x8001, event_group_ids=[7])
    ]
    definitions[0]["child"] = [
        {
            "method_event_id": "0x8001",
            "method_event_name": "CommName",
            "rpc_type": "Field",
            "rpc_specific_type": "Notification Event",
            "event_group_name_event_group_id": "ExplicitGroup@0x0007",
        }
    ]
    return model, definitions


def test_member_group_audit_joins_ids_but_preserves_both_names_and_model():
    model, definitions = event_inputs()
    before = model.model_dump()
    report = audit_event_groups(model, definitions)
    assert report["verified"] and report["matched_count"] == 1
    assert report["comparisons"][0]["comm_member"] == "CommName"
    assert report["comparisons"][0]["arxml_member"] == "ArxmlName"
    assert not report["runtime_verified"] and model.model_dump() == before


@pytest.mark.parametrize("change", ["broadcast", "missing", "extra", "service_name"])
def test_member_group_audit_preserves_mismatch_and_missing_evidence(change):
    model, definitions = event_inputs()
    if change == "broadcast":
        model.services[0].events[0].event_group_ids.append(8)
    elif change == "missing":
        model.services[0].events.clear()
    elif change == "extra":
        model.services[0].events.append(EventDefinition(name="Extra", event_id=0x8002))
    else:
        model.services[0].name = "SATExampleService"
    report = audit_event_groups(model, definitions)
    assert not report["verified"] and not report["runtime_verified"]
    assert (
        report["mismatch_count"] + report["missing_count"] + report["extra_count"] == 1
    )


@pytest.mark.parametrize(
    "change", ["duplicate", "bool", "unknown_groups", "missing_name", "range"]
)
def test_member_group_audit_rejects_ambiguous_declarations(change):
    model, definitions = event_inputs()
    row = definitions[0]["child"][0]
    if change == "duplicate":
        other = deepcopy(row)
        other["field_property_data_type"] = "ConflictingType"
        definitions[0]["child"].append(other)
    elif change == "bool":
        row["method_event_id"] = True
    elif change == "unknown_groups":
        row["event_group_name_event_group_id"] = "A@7B@8"
    elif change == "missing_name":
        row["event_group_name_event_group_id"] = "@7"
    else:
        row["method_event_id"] = "0x0001"
    with pytest.raises((ValueError, TypeError)):
        audit_event_groups(model, definitions)


def test_member_group_audit_merges_only_explicit_groups_of_identical_business_definition():
    model, definitions = event_inputs()
    row = definitions[0]["child"][0]
    other = deepcopy(row)
    other["event_group_name_event_group_id"] = "OtherAudience@8"
    other["client"] = ""  # 消费者预留不改变同一事件的业务定义。
    definitions[0]["child"].extend([other, deepcopy(row)])
    model.services[0].events[0].event_group_ids = [7, 8]
    report = audit_event_groups(model, definitions)
    assert report["verified"] and report["declared_count"] == 1
    assert report["declaration_row_count"] == 3
    assert report["comparisons"][0]["comm_group_ids"] == [7, 8]


@pytest.mark.parametrize(
    "change", ["name", "id", "conflict", "communication", "undeployed", "empty", "bool"]
)
def test_bad_or_ambiguous_sources_cannot_claim_name_verification(change):
    model, definitions, communication = inputs()
    if change == "name":
        model.services[0].name = "SATExampleService"
    elif change == "id":
        definitions[0]["service_id"] = "0x9999"
    elif change == "conflict":
        definitions.append({"service_name": "OtherName", "service_id": "0x1234"})
    elif change == "communication":
        communication[0]["vlan_list"][0]["service_list"][0]["service_name"] = (
            "OtherName"
        )
    elif change == "undeployed":
        model.services[0].service_id = None
    elif change == "empty":
        model.services = []
    else:
        definitions[0]["service_id"] = True
    with pytest.raises((ValueError, TypeError)):
        audit_names(model, definitions, communication)


@pytest.mark.parametrize("bits,successful", [(32, True), (16, False)])
def test_vsa_graph_audit_preserves_success_and_failure_evidence(
    bits, successful, caplog
):
    fixtures = Path(__file__).resolve().parents[2] / "backend/tests/fixtures"
    root = etree.fromstring((fixtures / "composite_service.arxml").read_bytes())
    extra = etree.fromstring((fixtures / "vsa_type.xml").read_bytes())
    extra.xpath(".//*[local-name()='BASE-TYPE-SIZE']")[0].text = str(bits)
    extra.xpath(".//*[local-name()='ARRAY-SIZE']")[0].text = "1048576"
    root.xpath("//*[local-name()='AR-PACKAGE']/*[local-name()='ELEMENTS']")[0].extend(
        extra
    )
    report = audit_vsa_declarations(etree.tostring(root))
    assert report["declared_count"] == 1 and not report["runtime_verified"]
    assert report["resolved_count"] == int(successful)
    assert report["error_count"] == int(not successful)
    if successful:
        assert report["declarations"][0]["max_elements"] == 1048576
        assert not report["errors"]
    else:
        assert "容量不足" in report["errors"][0]["error"]
        assert any(record.exc_info for record in caplog.records)
