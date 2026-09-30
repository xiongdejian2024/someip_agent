"""ARXML 实际引用到原生目录的契约，不把名称推断作为发送依据。"""

import hashlib
import json
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
