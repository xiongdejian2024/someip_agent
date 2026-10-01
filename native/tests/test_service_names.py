"""名称审计正反向验证；测试模型不作为生产目录。"""

from copy import deepcopy

import pytest
from audit_service_names import audit_names
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
