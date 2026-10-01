from pathlib import Path

import pytest

from someip_agent.arxml.parser import ArxmlParseError, ArxmlParser
from someip_agent.domain.models import SignalDataType

FIXTURE = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"


def test_parses_service_deployment() -> None:
    model = ArxmlParser().parse(FIXTURE.read_bytes(), FIXTURE.name)
    assert model.autosar_version == "00048"
    assert len(model.services) == 1
    service = model.services[0]
    assert service.name == "VehicleStatus"
    assert service.service_id == 0x1234
    assert service.instance_ids == [1]
    assert service.minor_version == 2
    assert service.methods[0].method_id == 1
    assert service.methods[0].input_signals[0].data_type == SignalDataType.UINT16
    assert service.events[0].event_id == 0x8001
    assert service.events[0].event_group_ids == [1]
    assert service.fields[0].notifier_id == 0x8101


def test_rejects_external_entities() -> None:
    malicious = (
        b'<!DOCTYPE AUTOSAR [<!ENTITY x SYSTEM "file:///etc/passwd">]><AUTOSAR>&x;</AUTOSAR>'
    )
    with pytest.raises(ArxmlParseError, match="DOCTYPE"):
        ArxmlParser().parse(malicious, "unsafe.arxml")


def test_parses_classic_socket_service_table() -> None:
    content = b"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR>
  <CLIENT-SERVER-INTERFACE>
    <SHORT-NAME>DoorCommand</SHORT-NAME>
    <IS-SERVICE>false</IS-SERVICE>
    <OPERATIONS>
      <CLIENT-SERVER-OPERATION>
        <SHORT-NAME>SetDoor</SHORT-NAME>
        <ARGUMENTS>
          <ARGUMENT-DATA-PROTOTYPE>
            <SHORT-NAME>DoorTarget</SHORT-NAME>
            <TYPE-TREF>/DataTypes/uint8</TYPE-TREF>
            <DIRECTION>IN</DIRECTION>
          </ARGUMENT-DATA-PROTOTYPE>
        </ARGUMENTS>
      </CLIENT-SERVER-OPERATION>
    </OPERATIONS>
  </CLIENT-SERVER-INTERFACE>
  <SENDER-RECEIVER-INTERFACE>
    <SHORT-NAME>DoorStatus</SHORT-NAME>
    <IS-SERVICE>false</IS-SERVICE>
    <DATA-ELEMENTS>
      <VARIABLE-DATA-PROTOTYPE>
        <SHORT-NAME>DoorPosition</SHORT-NAME>
        <TYPE-TREF>/DataTypes/uint16</TYPE-TREF>
      </VARIABLE-DATA-PROTOTYPE>
    </DATA-ELEMENTS>
  </SENDER-RECEIVER-INTERFACE>
  <PROVIDED-SERVICE-INSTANCE>
    <SHORT-NAME>BodyService</SHORT-NAME>
    <INSTANCE-IDENTIFIER>2</INSTANCE-IDENTIFIER>
    <SD-SERVER-CONFIG>
      <SERVER-SERVICE-MAJOR-VERSION>2</SERVER-SERVICE-MAJOR-VERSION>
      <SERVER-SERVICE-MINOR-VERSION>3</SERVER-SERVICE-MINOR-VERSION>
    </SD-SERVER-CONFIG>
    <SERVICE-IDENTIFIER>0x1234</SERVICE-IDENTIFIER>
  </PROVIDED-SERVICE-INSTANCE>
  <CONSUMED-SERVICE-INSTANCE>
    <SHORT-NAME>BodyClient</SHORT-NAME>
    <CONSUMED-EVENT-GROUPS>
      <CONSUMED-EVENT-GROUP>
        <SHORT-NAME>BodyEvents</SHORT-NAME>
        <EVENT-GROUP-IDENTIFIER>7</EVENT-GROUP-IDENTIFIER>
        <ROUTING-GROUP-REF>/Routing/BodyService_EventGroup</ROUTING-GROUP-REF>
      </CONSUMED-EVENT-GROUP>
    </CONSUMED-EVENT-GROUPS>
    <PROVIDED-SERVICE-INSTANCE-REF>/BodyService</PROVIDED-SERVICE-INSTANCE-REF>
  </CONSUMED-SERVICE-INSTANCE>
  <AR-PACKAGES><AR-PACKAGE><SHORT-NAME>Routing</SHORT-NAME><ELEMENTS>
    <SO-AD-ROUTING-GROUP><SHORT-NAME>BodyService_EventGroup</SHORT-NAME></SO-AD-ROUTING-GROUP>
  </ELEMENTS></AR-PACKAGE></AR-PACKAGES>
  <SOCKET-CONNECTION-IPDU-IDENTIFIER>
    <HEADER-ID>0x12340001</HEADER-ID>
    <PDU-TRIGGERING-REF>/Topology/DoorCommand_IPDUCall_Instance1</PDU-TRIGGERING-REF>
    <ROUTING-GROUP-REF>/Routing/BodyService</ROUTING-GROUP-REF>
  </SOCKET-CONNECTION-IPDU-IDENTIFIER>
  <SOCKET-CONNECTION-IPDU-IDENTIFIER>
    <HEADER-ID>0x12348001</HEADER-ID>
    <PDU-TRIGGERING-REF>/Topology/DoorStatus_Signal_Instance1</PDU-TRIGGERING-REF>
    <ROUTING-GROUP-REF>/Routing/BodyService_EventGroup</ROUTING-GROUP-REF>
  </SOCKET-CONNECTION-IPDU-IDENTIFIER>
</AUTOSAR>
"""

    model = ArxmlParser().parse(content, "classic-service-table.arxml")

    assert len(model.services) == 1
    service = model.services[0]
    assert service.name == "BodyService"
    assert service.service_id == 0x1234
    assert service.instance_ids == [2]
    assert (service.major_version, service.minor_version) == (2, 3)
    assert service.methods[0].name == "SetDoor"
    assert service.methods[0].method_id == 1
    assert service.methods[0].input_signals[0].name == "DoorTarget"
    assert service.events[0].name == "DoorStatus"
    assert service.events[0].event_id == 0x8001
    assert service.events[0].event_group_ids == [7]
    assert service.events[0].signals[0].data_type == SignalDataType.UINT16
    assert any("Classic AUTOSAR" in warning for warning in model.warnings)
