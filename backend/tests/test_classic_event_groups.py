"""Classic 事件组逐成员完整引用：拒绝服务级扩散、短名借用和歧义。"""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from someip_agent.arxml.parser import ArxmlParser

FIXTURE = Path(__file__).parent / "fixtures/classic_service.arxml"


def add(parent, tag, text=None):
    child = etree.SubElement(parent, tag)
    child.text = text
    return child


def inputs():
    root = etree.fromstring(FIXTURE.read_bytes())
    elements = root.xpath("//*[local-name()='AR-PACKAGE']/*[local-name()='ELEMENTS']")[0]
    for name in ("RouteA", "RouteB"):
        add(add(elements, "SO-AD-ROUTING-GROUP"), "SHORT-NAME", name)
    consumer = add(elements, "CONSUMED-SERVICE-INSTANCE")
    add(consumer, "SHORT-NAME", "Client")
    add(consumer, "PROVIDED-SERVICE-INSTANCE-REF", "/Classic/BodyService")
    for name, identifier in (("RouteA", "7"), ("RouteB", "8")):
        group = add(consumer, "CONSUMED-EVENT-GROUP")
        add(group, "SHORT-NAME", name)
        add(group, "EVENT-GROUP-IDENTIFIER", identifier)
        add(group, "ROUTING-GROUP-REF", f"/Classic/{name}")
    first = root.xpath("//*[local-name()='SOCKET-CONNECTION-IPDU-IDENTIFIER']")[0]
    first.xpath("./*[local-name()='ROUTING-GROUP-REF']")[0].text = "/Classic/RouteA"
    second = deepcopy(first)
    second.xpath("./*[local-name()='HEADER-ID']")[0].text = "0x12348002"
    second.xpath("./*[local-name()='ROUTING-GROUP-REF']")[0].text = "/Classic/RouteB"
    root.append(second)
    return root, elements, consumer, first, second


def parse(root):
    return ArxmlParser().parse(etree.tostring(root), FIXTURE.name).services[0]


def test_two_events_bind_only_their_explicit_routing_groups_and_keep_gate():
    root, _, _, _, _ = inputs()
    service = parse(root)
    assert [(event.event_id, event.event_group_ids) for event in service.events] == [
        (0x8001, [7]),
        (0x8002, [8]),
    ]
    assert service.deployment_errors == [
        "Classic SOME/IP transformer payload 布局尚未接入，禁止自动原生初始化"
    ]
    assert service.model_validate_json(service.model_dump_json()) == service


def test_one_event_can_explicitly_belong_to_two_groups_without_duplicates():
    root, elements, consumer, first, _ = inputs()
    add(first, "ROUTING-GROUP-REF", "/Classic/RouteB")
    repeated = deepcopy(consumer)
    repeated.xpath("./*[local-name()='SHORT-NAME']")[0].text = "OtherClient"
    elements.append(repeated)
    events = parse(root).events
    assert events[0].event_group_ids == [7, 8] and events[1].event_group_ids == [8]


def test_same_short_provider_or_routing_name_cannot_borrow_groups():
    root, _, consumer, first, _ = inputs()
    consumer.xpath("./*[local-name()='PROVIDED-SERVICE-INSTANCE-REF']")[
        0
    ].text = "/Other/BodyService"
    assert all(not event.event_group_ids for event in parse(root).events)
    consumer.xpath("./*[local-name()='PROVIDED-SERVICE-INSTANCE-REF']")[
        0
    ].text = "/Classic/BodyService"
    first.xpath("./*[local-name()='ROUTING-GROUP-REF']")[0].text = "/Other/RouteA"
    events = parse(root).events
    assert events[0].event_group_ids == [] and events[1].event_group_ids == [8]


@pytest.mark.parametrize("raw", [None, "", "bad", "0", "65535", "-1", "1.5"])
def test_invalid_group_id_blocks_affected_member_with_full_exception(raw, caplog):
    root, _, consumer, _, _ = inputs()
    consumer.xpath(".//*[local-name()='EVENT-GROUP-IDENTIFIER']")[0].text = raw
    service = parse(root)
    assert service.events[0].event_group_ids == []
    assert service.events[1].event_group_ids == [8]
    assert any("EventGroup" in error for error in service.deployment_errors)
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize(
    "target", ["provider", "route", "group", "missing_route", "multi_provider"]
)
def test_ambiguous_or_missing_full_paths_are_not_first_or_last_wins(target, caplog):
    root, elements, consumer, _, _ = inputs()
    if target == "multi_provider":
        add(consumer, "PROVIDED-SERVICE-INSTANCE-REF", "/Other/BodyService")
    elif target == "missing_route":
        route = elements.xpath("./*[local-name()='SO-AD-ROUTING-GROUP']")[0]
        elements.remove(route)
    elif target == "group":
        consumer.append(deepcopy(consumer.xpath("./*[local-name()='CONSUMED-EVENT-GROUP']")[0]))
    else:
        tag = "PROVIDED-SERVICE-INSTANCE" if target == "provider" else "SO-AD-ROUTING-GROUP"
        elements.append(deepcopy(elements.xpath(f"./*[local-name()='{tag}']")[0]))
    service = parse(root)
    assert service.events[0].event_group_ids == []
    assert any("EventGroup" in error for error in service.deployment_errors)
    assert any(record.exc_info for record in caplog.records)


def test_missing_event_routing_does_not_fall_back_to_service_groups():
    root, _, _, first, _ = inputs()
    reference = first.xpath("./*[local-name()='ROUTING-GROUP-REF']")[0]
    first.remove(reference)
    events = parse(root).events
    assert events[0].event_group_ids == [] and events[1].event_group_ids == [8]
