"""ARXML 实际引用到原生目录的契约，不把名称推断作为发送依据。"""

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from someip_agent.arxml.parser import ArxmlParseError, ArxmlParser
from someip_agent.config import Settings
from someip_agent.domain.models import SignalDataType
from someip_agent.main import create_app
from someip_agent.soa.catalog import CatalogBuildError, NativeCatalogRequest, build_native_bundle
from someip_agent.soa.naming import member_key
from someip_agent.soa.partner import S2sBaseClass

FIXTURE = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"


def parse(content=None):
    return ArxmlParser().parse(content or FIXTURE.read_bytes(), FIXTURE.name)


def request(**member):
    return NativeCatalogRequest.model_validate(
        {"members": {"VehicleStatus": {"role": "server", **member}}}
    )


def test_types_are_resolved_from_encoding_not_short_name():
    content = FIXTURE.read_bytes().replace(b"uint16_kph", b"ThisIsNotAnIntegerName")
    model = parse(content)
    signal = model.services[0].methods[0].input_signals[0]
    assert signal.data_type == SignalDataType.UINT16
    assert signal.wire_schema == {"type": "uint16"}
    assert model.source_sha256 == hashlib.sha256(content).hexdigest()


def test_bundle_contains_methods_fields_eventgroups_and_provenance(tmp_path):
    model = parse()
    bundle = build_native_bundle(model, request(), Settings(_env_file=None))
    spec = bundle.catalog["VehicleStatus"]
    assert spec["methods"]["SetSpeed"]["input"]["fields"] == [
        {"name": "Speed", "type": "uint16", "byte_order": "big"}
    ]
    assert spec["methods"]["SetSpeed"]["output"]["fields"][0]["type"] == "boolean"
    assert spec["methods"]["GetIgnitionState"]["output"]["type"] == "uint8"
    assert spec["methods"]["SetIgnitionState"]["input"]["fields"][0]["type"] == "uint8"
    assert spec["events"]["UpdateIgnitionStateEvent"]["eventgroups"] == [1]
    assert spec["events"]["UpdateIgnitionStateEvent"]["field"] is True
    assert bundle.config["services"][0]["eventgroups"] == [
        {"eventgroup": "0x0001", "events": ["0x8001", "0x8101"]}
    ]
    assert bundle.config["service-discovery"]["enable"] is False
    assert "unreliable" not in bundle.config["services"][0]
    paths = bundle.write(tmp_path / "runtime")
    assert json.loads(paths[0].read_text()) == bundle.catalog
    assert (
        json.loads((tmp_path / "runtime" / "model-binding.json").read_text())["source_sha256"]
        == model.source_sha256
    )
    with pytest.raises(FileExistsError):
        bundle.write(tmp_path / "runtime")


def test_member_applications_preserve_sat_aliases_and_default():
    body = NativeCatalogRequest.model_validate(
        {
            "members": {
                "VehicleStatus": {"role": "client"},
                "VehicleStatus_1": {
                    "service": "VehicleStatus",
                    "role": "client",
                    "application_name": "second_client",
                    "application_id": 0x2202,
                },
                "VehicleStatus_2": {
                    "service": "VehicleStatus",
                    "role": "client",
                    "application_name": "second_client",
                    "application_id": 0x2202,
                },
            }
        }
    )
    bundle = build_native_bundle(parse(), body, Settings(_env_file=None))
    assert bundle.config["applications"] == [
        {"name": "arxml_partner", "id": "0x1101"},
        {"name": "second_client", "id": "0x2202"},
    ]
    assert "application_name" not in bundle.members["VehicleStatus"]
    assert bundle.members["VehicleStatus_1"]["application_name"] == "second_client"
    assert (
        member_key("VehicleStatus_1", bundle.members["VehicleStatus_1"]) == "VehicleStatus_client_1"
    )


@pytest.mark.parametrize(
    "member",
    [
        {"application_name": "second_client"},
        {"application_id": 0x2202},
        {"application_name": "bad/name", "application_id": 0x2202},
        {"application_name": "second_client", "application_id": 0},
        {"application_name": "second_client", "application_id": 0xFFFF},
    ],
)
def test_application_selector_requires_explicit_valid_pair(member):
    with pytest.raises(ValueError):
        request(**member)


@pytest.mark.parametrize("name,identifier", [("arxml_partner", 0x2202), ("other", 0x1101)])
def test_application_name_or_id_collision_is_rejected(name, identifier):
    with pytest.raises(CatalogBuildError, match="冲突|多个"):
        build_native_bundle(
            parse(),
            request(application_name=name, application_id=identifier),
            Settings(_env_file=None),
        )


def test_application_context_count_is_bounded_including_routing_host():
    members = {
        f"Consumer_{i}": {
            "role": "client",
            "service": "VehicleStatus",
            "application_name": f"consumer_{i}",
            "application_id": 0x3000 + i,
        }
        for i in range(16)
    }
    body = NativeCatalogRequest(members=members)
    with pytest.raises(CatalogBuildError, match="16"):
        build_native_bundle(parse(), body, Settings(_env_file=None))
    members.pop("Consumer_15")
    bundle = build_native_bundle(
        parse(), NativeCatalogRequest(members=members), Settings(_env_file=None)
    )
    assert len(bundle.config["applications"]) == 16


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_name",
        "duplicate_id",
        "zero",
        "wildcard",
        "missing_id",
        "missing_default",
        "wrong_routing",
        "object",
    ],
)
def test_native_application_configuration_is_validated_before_start(
    tmp_path, native_runtime, change
):
    bundle = build_native_bundle(parse(), request(), Settings(_env_file=None))
    config = bundle.config
    if change in {"duplicate_name", "duplicate_id"}:
        config["applications"].append(
            {
                "name": "arxml_partner" if change == "duplicate_name" else "other",
                "id": "0x2202" if change == "duplicate_name" else "0x1101",
            }
        )
    elif change in {"zero", "wildcard"}:
        config["applications"][0]["id"] = "0x0000" if change == "zero" else "0xffff"
    elif change == "missing_id":
        config["applications"][0].pop("id")
    elif change == "missing_default":
        config["applications"][0]["name"] = "other"
    elif change == "wrong_routing":
        config["routing"] = "other"
    else:
        config["applications"] = {"name": "arxml_partner", "id": "0x1101"}
    catalog_path, config_path = bundle.write(tmp_path / change)
    result = subprocess.run(
        [
            native_runtime,
            "run",
            "--name",
            "arxml_partner",
            "--catalog",
            str(catalog_path),
            "--config",
            str(config_path),
            "-p",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "native.main" in result.stdout + result.stderr
    assert "native.ready" not in result.stdout


@pytest.mark.parametrize(
    "replacement,match",
    [
        ((b"/DataTypes/uint16_kph", b"/Missing/uint16_kph"), "完整引用"),
        ((b"<CATEGORY>VALUE</CATEGORY>", b"<CATEGORY>STRUCTURE</CATEGORY>"), "复合类型"),
        ((b"<BASE-TYPE-ENCODING>BOOLEAN", b"<BASE-TYPE-ENCODING>UTF-16"), "不支持编码"),
    ],
)
def test_missing_or_unsupported_types_cannot_be_used_for_native_sends(replacement, match):
    model = parse(FIXTURE.read_bytes().replace(*replacement))
    assert model.services and model.warnings
    with pytest.raises(CatalogBuildError, match=match):
        build_native_bundle(model, request(), Settings(_env_file=None))


def test_inout_is_present_in_both_schemas():
    model = parse(
        FIXTURE.read_bytes().replace(b"<DIRECTION>IN</DIRECTION>", b"<DIRECTION>INOUT</DIRECTION>")
    )
    method = model.services[0].methods[0]
    assert [s.name for s in method.input_signals] == ["Speed"]
    assert [s.name for s in method.output_signals] == ["Speed", "Accepted"]


def test_full_paths_prevent_short_name_cross_binding():
    model = parse(
        FIXTURE.read_bytes().replace(
            b"<METHOD-REF>/Interfaces/VehicleStatus/SetSpeed",
            b"<METHOD-REF>/Other/VehicleStatus/SetSpeed",
        )
    )
    assert model.services[0].methods[0].method_id is None
    with pytest.raises(CatalogBuildError, match="部署 ID"):
        build_native_bundle(model, request(), Settings(_env_file=None))


def test_arbitrary_deployment_name_multiple_instances_and_zero_major():
    content = (
        FIXTURE.read_bytes()
        .replace(b"VehicleStatusDeployment", b"DeploymentWithoutInterfaceName")
        .replace(b"<MAJOR-VERSION>1", b"<MAJOR-VERSION>0")
    )
    root = etree.fromstring(content)
    deployment = root.xpath("//*[local-name()='SOMEIP-SERVICE-INTERFACE-DEPLOYMENT']")[0]
    other = deepcopy(deployment)
    other.xpath("./*[local-name()='SHORT-NAME']")[0].text = "SecondDeployment"
    other.xpath("./*[local-name()='SERVICE-INTERFACE-ID']")[0].text = "0x1235"
    deployment.getparent().append(other)
    instance = root.xpath("//*[local-name()='PROVIDED-SOMEIP-SERVICE-INSTANCE']")[0]
    second = deepcopy(instance)
    second.xpath("./*[local-name()='SHORT-NAME']")[0].text = "SecondInstance"
    second.xpath("./*[local-name()='SERVICE-INTERFACE-DEPLOYMENT-REF']")[
        0
    ].text = "/Deployment/SecondDeployment"
    second.xpath("./*[local-name()='SERVICE-INSTANCE-ID']")[0].text = "2"
    instance.getparent().append(second)
    model = parse(etree.tostring(root))
    assert len(model.services) == 2
    assert [(s.service_id, s.instance_ids, s.major_version) for s in model.services] == [
        (0x1234, [1], 0),
        (0x1235, [2], 0),
    ]
    with pytest.raises(CatalogBuildError, match="歧义"):
        build_native_bundle(model, request(), Settings(_env_file=None))
    # 第二份部署仍引用第一份字段部署，不能替它猜出事件组。
    selected = request(deployment_path="/Deployment/DeploymentWithoutInterfaceName")
    assert (
        build_native_bundle(model, selected, Settings(_env_file=None)).catalog["VehicleStatus"][
            "major_version"
        ]
        == 0
    )


def test_duplicate_type_paths_are_rejected():
    root = etree.fromstring(FIXTURE.read_bytes())
    base = root.xpath("//*[local-name()='SW-BASE-TYPE']")[0]
    base.getparent().append(deepcopy(base))
    with pytest.raises(ArxmlParseError, match="类型完整路径重复"):
        parse(etree.tostring(root))


def test_type_aliases_and_cycles_do_not_guess():
    root = etree.fromstring(FIXTURE.read_bytes())
    original = root.xpath("//*[local-name()='IMPLEMENTATION-DATA-TYPE']")[0]
    alias = deepcopy(original)
    alias.xpath("./*[local-name()='SHORT-NAME']")[0].text = "Alias"
    alias.xpath("./*[local-name()='CATEGORY']")[0].text = "TYPE_REFERENCE"
    target = alias.xpath(".//*[local-name()='BASE-TYPE-REF']")[0]
    target.tag = "{http://autosar.org/schema/r4.0}IMPLEMENTATION-DATA-TYPE-REF"
    target.text = "/DataTypes/uint16_kph"
    original.getparent().append(alias)
    root.xpath("//*[local-name()='ARGUMENT-DATA-PROTOTYPE']/*[local-name()='TYPE-TREF']")[
        0
    ].text = "/DataTypes/Alias"
    model = parse(etree.tostring(root))
    assert model.services[0].methods[0].input_signals[0].wire_schema == {"type": "uint16"}
    target.text = "/DataTypes/Alias"
    model = parse(etree.tostring(root))
    with pytest.raises(CatalogBuildError, match="循环"):
        build_native_bundle(model, request(), Settings(_env_file=None))


@pytest.mark.parametrize(
    "replacement,match",
    [
        ((b"<MAJOR-VERSION>1</MAJOR-VERSION>", b""), "版本"),
        ((b"<METHOD-ID>0x0001", b"<METHOD-ID>0x0101"), "冲突"),
        ((b"<NOTIFIER-ID>0x8101", b"<NOTIFIER-ID>0x8001"), "冲突"),
        ((b"<EVENT-GROUP-ID>0x0001", b"<EVENT-GROUP-ID>0xFFFF"), "Event Group"),
        (
            (
                b"<MINOR-VERSION>2</MINOR-VERSION>",
                b"<MINOR-VERSION>2</MINOR-VERSION><BYTE-ORDER>MOST-SIGNIFICANT-BYTE-LAST</BYTE-ORDER>",
            ),
            "序列化属性",
        ),
    ],
)
def test_invalid_deployments_do_not_initialize_partial_services(replacement, match):
    model = parse(FIXTURE.read_bytes().replace(*replacement))
    with pytest.raises(CatalogBuildError, match=match):
        build_native_bundle(model, request(), Settings(_env_file=None))


def test_unknown_direction_and_doctype_after_prefix_are_rejected():
    with pytest.raises(ArxmlParseError, match="方向非法"):
        parse(FIXTURE.read_bytes().replace(b"<DIRECTION>IN", b"<DIRECTION>UNKNOWN"))
    content = b" " * 8192 + b"<!DOCTYPE AUTOSAR><AUTOSAR/>"
    with pytest.raises(ArxmlParseError, match="DOCTYPE"):
        parse(content)


def test_numbered_clients_keep_sat_names():
    config = NativeCatalogRequest.model_validate(
        {
            "members": {
                "VehicleStatus_1": {"service": "VehicleStatus", "role": "client"},
            }
        }
    )
    bundle = build_native_bundle(parse(), config, Settings(_env_file=None))
    assert (
        member_key("VehicleStatus_1", bundle.members["VehicleStatus_1"]) == "VehicleStatus_client_1"
    )
    assert bundle.members["VehicleStatus_1"]["definition"] == bundle.catalog["VehicleStatus_1"]


@pytest.mark.parametrize("alias,role", [("VehicleStatus", "server"), ("VehicleStatus_1", "client")])
def test_full_path_selection_keeps_source_service_name_not_path_or_sat_example(alias, role):
    model = parse()
    service = model.services[0]
    body = NativeCatalogRequest.model_validate(
        {"members": {alias: {"service": service.path, "role": role}}}
    )
    bundle = build_native_bundle(model, body, Settings(_env_file=None))
    member = bundle.members[alias]
    assert member["service"] == member["name"] == service.name
    assert member_key(alias, member) == (
        "VehicleStatus_server" if role == "server" else "VehicleStatus_client_1"
    )
    assert bundle.catalog[alias]["source"] == {
        "service_name": service.name,
        "service_path": service.path,
        "deployment_path": service.deployment_path,
    }
    assert bundle.catalog[alias]["service_id"] == service.service_id
    assert ("definition" in member) is (alias != service.name)


def test_reference_example_service_cannot_replace_imported_arxml_service():
    body = NativeCatalogRequest(members={"DoorService": {"role": "server"}})
    with pytest.raises(CatalogBuildError, match="缺失或有歧义"):
        build_native_bundle(parse(), body, Settings(_env_file=None))


@pytest.mark.parametrize("alias,role", [("VehicleStatus", "server"), ("VehicleStatus_1", "client")])
def test_source_name_selected_by_full_path_initializes_real_native(
    tmp_path, native_runtime, alias, role
):
    model = parse()
    service = model.services[0]
    with S2sBaseClass.from_arxml(
        model,
        {alias: {"service": service.path, "role": role}},
        directory=tmp_path / "runtime",
        settings=Settings(_env_file=None, native_binary=native_runtime),
        auto_restart=False,
    ) as partner:
        expected = "VehicleStatus_server" if role == "server" else "VehicleStatus_client_1"
        assert list(partner.partner_infos) == [expected]
        assert partner.partner_infos[expected].require_socket().fileno() >= 0
        states = partner.sim_operator.send_request("running_service", print_result=False)
        assert f"{alias}_{role}" in states
        binding = json.loads((tmp_path / "runtime" / "model-binding.json").read_text())
        assert binding["members"][alias]["service"] == service.name
        assert binding["catalog"][alias]["source"]["service_name"] == service.name
        process = partner.sim_operator.process
    assert process.poll() == 0


def test_catalog_api_is_explicit_and_does_not_start_processes(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert (
            client.post("/api/v1/model/native-catalog", json=request().model_dump()).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/arxml/import",
                files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "application/xml")},
            ).status_code
            == 200
        )
        response = client.post("/api/v1/model/native-catalog", json=request().model_dump())
        assert response.status_code == 200, response.text
        assert response.json()["source_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        denied = client.post(
            "/api/v1/model/native-catalog",
            json=request(transport="udp", peer_host="10.77.0.2", port=30503).model_dump(),
        )
        assert denied.status_code == 403
        assert "默认关闭" in denied.json()["detail"]
        assert not (tmp_path / "native").exists()


def test_from_arxml_initializes_real_native_binary(tmp_path, native_runtime):
    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    with S2sBaseClass.from_arxml(
        parse(),
        {"VehicleStatus": {"role": "server"}},
        directory=tmp_path / "runtime",
        settings=settings,
    ) as partner:
        assert (
            partner.sim_operator.send_request("running_service")["VehicleStatus_server"]["state"]
            == "START"
        )
        assert "VehicleStatus_server" in partner.partner_infos
        partner.send_event_notify("VehicleStatus_server", "IgnitionState", 7)
        partner.send_event_notify("VehicleStatus_server", "SpeedChanged", 42.5)
