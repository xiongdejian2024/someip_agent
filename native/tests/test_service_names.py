"""名称审计正反向验证；测试模型不作为生产目录。"""

from copy import deepcopy
from pathlib import Path

import pytest
from audit_service_names import audit_names
from someip_agent.arxml.parser import ArxmlParser
from someip_agent.domain.models import ArxmlModel, ServiceDefinition


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
