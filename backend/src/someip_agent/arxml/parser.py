from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from lxml import etree

from someip_agent.domain.models import (
    ArxmlModel,
    EventDefinition,
    FieldDefinition,
    MethodDefinition,
    ServiceDefinition,
    SignalDataType,
    SignalDefinition,
)


class ArxmlParseError(ValueError):
    """ARXML 内容不安全或结构无法解析。"""


def _local_name(element: etree._Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    return str(etree.QName(element).localname)


def _children(element: etree._Element, name: str) -> Iterable[etree._Element]:
    return (child for child in element if _local_name(child) == name)


def _first_child(element: etree._Element, name: str) -> etree._Element | None:
    return next(iter(_children(element, name)), None)


def _descendants(element: etree._Element, name: str) -> list[etree._Element]:
    return [node for node in element.iter() if _local_name(node) == name]


def _first_descendant(element: etree._Element, *names: str) -> etree._Element | None:
    wanted = set(names)
    return next((node for node in element.iter() if _local_name(node) in wanted), None)


def _text(element: etree._Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _first_text(element: etree._Element, *names: str) -> str | None:
    return _text(_first_descendant(element, *names))


def _short_name(element: etree._Element, fallback: str = "Unnamed") -> str:
    direct = next(iter(_children(element, "SHORT-NAME")), None)
    return _text(direct) or fallback


def _reference_name(reference: str | None) -> str | None:
    if not reference:
        return None
    return PurePosixPath(reference).name


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip().replace("_", "")
    try:
        return int(cleaned, 0)
    except ValueError:
        try:
            return int(cleaned, 16) if any(c in "abcdefABCDEF" for c in cleaned) else int(cleaned)
        except ValueError:
            return None


def _infer_data_type(type_ref: str | None) -> SignalDataType:
    name = (_reference_name(type_ref) or "").lower().replace("_", "")
    ordered = (
        (("boolean", "bool"), SignalDataType.BOOLEAN),
        (("uint8", "uchar", "unsigned8"), SignalDataType.UINT8),
        (("uint16", "unsigned16"), SignalDataType.UINT16),
        (("uint32", "unsigned32"), SignalDataType.UINT32),
        (("uint64", "unsigned64"), SignalDataType.UINT64),
        (("sint8", "int8"), SignalDataType.INT8),
        (("sint16", "int16"), SignalDataType.INT16),
        (("sint32", "int32"), SignalDataType.INT32),
        (("sint64", "int64"), SignalDataType.INT64),
        (("float32", "single"), SignalDataType.FLOAT32),
        (("float64", "double"), SignalDataType.FLOAT64),
        (("string", "utf8", "utf16"), SignalDataType.STRING),
        (("array", "byte", "opaque"), SignalDataType.BYTES),
    )
    for markers, data_type in ordered:
        if any(marker in name for marker in markers):
            return data_type
    return SignalDataType.UINT32


def _element_path(element: etree._Element) -> str:
    names: list[str] = []
    current: etree._Element | None = element
    while current is not None:
        name = _short_name(current, "")
        if name:
            names.append(name)
        current = current.getparent()
    return "/" + "/".join(reversed(names))


def _signal_from_prototype(element: etree._Element) -> SignalDefinition:
    type_ref = _first_text(
        element,
        "TYPE-TREF",
        "TYPE-REF",
        "IMPLEMENTATION-DATA-TYPE-REF",
        "APPLICATION-DATA-TYPE-REF",
    )
    return SignalDefinition(
        name=_short_name(element),
        path=_element_path(element),
        data_type=_infer_data_type(type_ref),
        type_ref=type_ref,
    )


class ArxmlParser:
    """面向 SOME/IP 工具链的容错 ARXML 投影解析器。

    解析器不会修改原始 XML，也不会解析外部实体。它抽取稳定的服务投影，完整
    AUTOSAR round-trip 后续可由 py-autosar-data 适配器承接。
    """

    def parse(self, content: bytes, source_name: str) -> ArxmlModel:
        if not content.strip():
            raise ArxmlParseError("ARXML 文件为空")
        lowered = content[:4096].lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise ArxmlParseError("出于安全原因，不允许 ARXML 包含 DOCTYPE 或 ENTITY")
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
            recover=False,
            remove_comments=False,
        )
        try:
            root = etree.fromstring(content, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise ArxmlParseError(f"ARXML XML 语法错误: {exc}") from exc
        if _local_name(root) != "AUTOSAR":
            raise ArxmlParseError(f"根元素必须是 AUTOSAR，实际为 {_local_name(root) or root.tag}")

        model = ArxmlModel(
            source_name=source_name,
            autosar_version=self._detect_version(root),
        )
        services = self._extract_services(root)
        self._apply_deployments(root, services, model.warnings)
        self._apply_instances(root, services)
        if not services:
            services = self._extract_classic_service_table(root, model.warnings)
        model.services = sorted(services.values(), key=lambda item: item.name.lower())
        if not model.services:
            model.warnings.append(
                "未发现可用的 SOME/IP 服务定义：文件中既没有 SERVICE-INTERFACE 部署，"
                "也没有带 SERVICE-IDENTIFIER 的 PROVIDED-SERVICE-INSTANCE"
            )
        return model

    @staticmethod
    def _detect_version(root: etree._Element) -> str | None:
        schema_location = " ".join(value for value in root.attrib.values() if "AUTOSAR" in value)
        match = re.search(r"AUTOSAR_(\d{5})", schema_location, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        namespace = etree.QName(root).namespace or ""
        match = re.search(r"(?:r|R)(\d{2})[-_/](\d{2})", namespace + " " + schema_location)
        if match:
            return f"R{match.group(1)}-{match.group(2)}"
        return None

    def _extract_services(self, root: etree._Element) -> dict[str, ServiceDefinition]:
        services: dict[str, ServiceDefinition] = {}
        for element in root.iter():
            if _local_name(element) not in {"SERVICE-INTERFACE", "SOMEIP-SERVICE-INTERFACE"}:
                continue
            name = _short_name(element)
            if name in services:
                continue
            methods: list[MethodDefinition] = []
            for operation in _descendants(element, "CLIENT-SERVER-OPERATION"):
                inputs: list[SignalDefinition] = []
                outputs: list[SignalDefinition] = []
                for argument in _descendants(operation, "ARGUMENT-DATA-PROTOTYPE"):
                    direction = (_first_text(argument, "DIRECTION") or "IN").upper()
                    signal = _signal_from_prototype(argument)
                    (outputs if direction in {"OUT", "INOUT"} else inputs).append(signal)
                methods.append(
                    MethodDefinition(
                        name=_short_name(operation),
                        input_signals=inputs,
                        output_signals=outputs,
                        fire_and_forget=(
                            (_first_text(operation, "FIRE-AND-FORGET") or "false").lower() == "true"
                        ),
                    )
                )

            events: list[EventDefinition] = []
            for container in _descendants(element, "EVENTS"):
                for prototype in _descendants(container, "VARIABLE-DATA-PROTOTYPE"):
                    events.append(
                        EventDefinition(
                            name=_short_name(prototype),
                            signals=[_signal_from_prototype(prototype)],
                        )
                    )

            fields: list[FieldDefinition] = []
            for field_element in _descendants(element, "FIELD"):
                prototype = _first_descendant(field_element, "VARIABLE-DATA-PROTOTYPE")
                fields.append(
                    FieldDefinition(
                        name=_short_name(field_element),
                        signal=_signal_from_prototype(prototype) if prototype is not None else None,
                    )
                )

            services[name] = ServiceDefinition(
                name=name,
                path=_element_path(element),
                methods=methods,
                events=events,
                fields=fields,
            )
        return services

    def _extract_classic_service_table(
        self,
        root: etree._Element,
        warnings: list[str],
    ) -> dict[str, ServiceDefinition]:
        """从 Classic AUTOSAR Socket/SoAd 表重建 SOME/IP 服务投影。

        一些量产通信矩阵不包含 ``SERVICE-INTERFACE``，而是通过
        ``PROVIDED-SERVICE-INSTANCE`` 和 ``SOCKET-CONNECTION-IPDU-IDENTIFIER``
        直接表达 Service/Instance/Header ID。本投影只读取这些明确关系，不猜测
        缺失的部署信息。
        """

        client_interfaces, sender_interfaces = self._classic_interface_index(root)
        services: dict[str, ServiceDefinition] = {}
        services_by_id: defaultdict[int, list[ServiceDefinition]] = defaultdict(list)

        for instance in root.iter():
            if _local_name(instance) != "PROVIDED-SERVICE-INSTANCE":
                continue
            service_id = _parse_int(_first_text(instance, "SERVICE-IDENTIFIER"))
            instance_id = _parse_int(_first_text(instance, "INSTANCE-IDENTIFIER"))
            if service_id is None:
                continue
            name = _short_name(instance, f"Service_0x{service_id:04X}")
            service = services.get(name)
            if service is None:
                server_config = _first_child(instance, "SD-SERVER-CONFIG")
                service = ServiceDefinition(
                    name=name,
                    path=_element_path(instance),
                    service_id=service_id,
                    major_version=(
                        _parse_int(_first_text(server_config, "SERVER-SERVICE-MAJOR-VERSION"))
                        if server_config is not None
                        else None
                    )
                    or 1,
                    minor_version=(
                        _parse_int(_first_text(server_config, "SERVER-SERVICE-MINOR-VERSION"))
                        if server_config is not None
                        else None
                    )
                    or 0,
                )
                services[name] = service
                services_by_id[service_id].append(service)
            elif service.service_id != service_id:
                warnings.append(
                    f"服务实例 {name} 出现冲突的 Service ID: "
                    f"0x{service.service_id or 0:04X} / 0x{service_id:04X}"
                )
                continue
            if instance_id is not None and instance_id not in service.instance_ids:
                service.instance_ids.append(instance_id)

        if not services:
            return {}

        interface_names = sorted(
            set(client_interfaces) | set(sender_interfaces),
            key=len,
            reverse=True,
        )
        for identifier in root.iter():
            if _local_name(identifier) != "SOCKET-CONNECTION-IPDU-IDENTIFIER":
                continue
            header_id = _parse_int(_first_text(identifier, "HEADER-ID"))
            pdu_reference = _first_text(identifier, "PDU-TRIGGERING-REF")
            if header_id is None or pdu_reference is None:
                continue
            service_id = (header_id >> 16) & 0xFFFF
            method_id = header_id & 0xFFFF
            candidates = services_by_id.get(service_id, [])
            if not candidates:
                continue

            route_names = {
                self._service_name_from_routing_group(_text(reference))
                for reference in _descendants(identifier, "ROUTING-GROUP-REF")
            }
            route_names.discard(None)
            matched = [service for service in candidates if service.name in route_names]
            target_services = matched or candidates
            pdu_name = _reference_name(pdu_reference) or f"Message_0x{method_id:04X}"
            interface_name = next(
                (
                    name
                    for name in interface_names
                    if pdu_name == name or pdu_name.startswith(f"{name}_")
                ),
                None,
            )
            is_event = bool(method_id & 0x8000) or any(
                "EventGroup" in (_reference_name(_text(reference)) or "")
                for reference in _descendants(identifier, "ROUTING-GROUP-REF")
            )
            for service in target_services:
                if is_event:
                    self._merge_classic_event(
                        service,
                        method_id,
                        interface_name or self._message_name(pdu_name),
                        sender_interfaces,
                    )
                else:
                    self._merge_classic_method(
                        service,
                        method_id,
                        interface_name or self._message_name(pdu_name),
                        client_interfaces,
                        sender_interfaces,
                    )

        event_groups = self._classic_event_groups(root)
        for name, service in services.items():
            group_ids = event_groups.get(name, [])
            service.instance_ids.sort()
            service.methods.sort(
                key=lambda item: (item.method_id is None, item.method_id, item.name)
            )
            service.events.sort(key=lambda item: (item.event_id is None, item.event_id, item.name))
            for event in service.events:
                event.event_group_ids = list(group_ids)

        warnings.append(
            "未发现 SERVICE-INTERFACE；已从 Classic AUTOSAR Socket/SoAd "
            "服务实例和 SOME/IP Header ID 重建服务投影"
        )
        return services

    @staticmethod
    def _classic_interface_index(
        root: etree._Element,
    ) -> tuple[dict[str, MethodDefinition], dict[str, list[SignalDefinition]]]:
        client_interfaces: dict[str, MethodDefinition] = {}
        sender_interfaces: dict[str, list[SignalDefinition]] = {}
        for interface in root.iter():
            local_name = _local_name(interface)
            interface_name = _short_name(interface)
            if local_name == "CLIENT-SERVER-INTERFACE":
                operation = _first_descendant(interface, "CLIENT-SERVER-OPERATION")
                if operation is None:
                    continue
                inputs: list[SignalDefinition] = []
                outputs: list[SignalDefinition] = []
                for argument in _descendants(operation, "ARGUMENT-DATA-PROTOTYPE"):
                    direction = (_first_text(argument, "DIRECTION") or "IN").upper()
                    signal = _signal_from_prototype(argument)
                    (outputs if direction in {"OUT", "INOUT"} else inputs).append(signal)
                client_interfaces[interface_name] = MethodDefinition(
                    name=_short_name(operation, interface_name),
                    input_signals=inputs,
                    output_signals=outputs,
                )
            elif local_name == "SENDER-RECEIVER-INTERFACE":
                prototypes = _descendants(interface, "VARIABLE-DATA-PROTOTYPE")
                sender_interfaces[interface_name] = [
                    _signal_from_prototype(prototype) for prototype in prototypes
                ]
        return client_interfaces, sender_interfaces

    @staticmethod
    def _service_name_from_routing_group(reference: str | None) -> str | None:
        name = _reference_name(reference)
        if not name:
            return None
        for suffix in ("_EventGroup", "_Multicast"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    @staticmethod
    def _message_name(pdu_name: str) -> str:
        markers = ("_IPDUCall", "_IPDUReturn", "_Signal")
        positions = [pdu_name.find(marker) for marker in markers if marker in pdu_name]
        return pdu_name[: min(positions)] if positions else pdu_name

    @staticmethod
    def _merge_classic_event(
        service: ServiceDefinition,
        event_id: int,
        interface_name: str,
        sender_interfaces: dict[str, list[SignalDefinition]],
    ) -> None:
        if any(event.event_id == event_id for event in service.events):
            return
        service.events.append(
            EventDefinition(
                name=interface_name,
                event_id=event_id,
                signals=[
                    signal.model_copy(deep=True)
                    for signal in sender_interfaces.get(interface_name, [])
                ],
            )
        )

    @staticmethod
    def _merge_classic_method(
        service: ServiceDefinition,
        method_id: int,
        interface_name: str,
        client_interfaces: dict[str, MethodDefinition],
        sender_interfaces: dict[str, list[SignalDefinition]],
    ) -> None:
        if any(method.method_id == method_id for method in service.methods):
            return
        template = client_interfaces.get(interface_name)
        if template is not None:
            method = template.model_copy(deep=True)
            method.method_id = method_id
        else:
            method = MethodDefinition(
                name=interface_name,
                method_id=method_id,
                input_signals=[
                    signal.model_copy(deep=True)
                    for signal in sender_interfaces.get(interface_name, [])
                ],
                fire_and_forget=interface_name in sender_interfaces,
            )
        service.methods.append(method)

    @staticmethod
    def _classic_event_groups(root: etree._Element) -> dict[str, list[int]]:
        result: defaultdict[str, list[int]] = defaultdict(list)
        for instance in root.iter():
            if _local_name(instance) != "CONSUMED-SERVICE-INSTANCE":
                continue
            service_name = _reference_name(
                _first_text(instance, "PROVIDED-SERVICE-INSTANCE-REF")
            )
            if not service_name:
                continue
            for group in _descendants(instance, "CONSUMED-EVENT-GROUP"):
                group_id = _parse_int(_first_text(group, "EVENT-GROUP-IDENTIFIER"))
                if group_id is not None and group_id not in result[service_name]:
                    result[service_name].append(group_id)
        return {name: sorted(values) for name, values in result.items()}

    def _apply_deployments(
        self,
        root: etree._Element,
        services: dict[str, ServiceDefinition],
        warnings: list[str],
    ) -> None:
        for deployment in _descendants(root, "SOMEIP-SERVICE-INTERFACE-DEPLOYMENT"):
            service_ref = _first_text(deployment, "SERVICE-INTERFACE-REF")
            service_name = _reference_name(service_ref) or _short_name(deployment)
            service = services.get(service_name)
            if service is None:
                warnings.append(f"部署 {service_name} 找不到对应 SERVICE-INTERFACE")
                continue
            service.service_id = _parse_int(_first_text(deployment, "SERVICE-INTERFACE-ID"))
            service.major_version = _parse_int(_first_text(deployment, "MAJOR-VERSION")) or 1
            service.minor_version = _parse_int(_first_text(deployment, "MINOR-VERSION")) or 0

            method_ids = self._deployment_id_map(
                deployment, "SOMEIP-METHOD-DEPLOYMENT", ("METHOD-REF",), "METHOD-ID"
            )
            event_ids = self._deployment_id_map(
                deployment, "SOMEIP-EVENT-DEPLOYMENT", ("EVENT-REF",), "EVENT-ID"
            )
            field_ids = self._field_deployment_map(deployment)
            eventgroups = self._eventgroup_map(deployment)
            for method in service.methods:
                method.method_id = method_ids.get(method.name)
            for event in service.events:
                event.event_id = event_ids.get(event.name)
                event.event_group_ids = eventgroups.get(event.name, [])
            for field in service.fields:
                ids = field_ids.get(field.name, {})
                field.getter_id = ids.get("getter")
                field.setter_id = ids.get("setter")
                field.notifier_id = ids.get("notifier")

    @staticmethod
    def _deployment_id_map(
        deployment: etree._Element,
        deployment_tag: str,
        reference_tags: tuple[str, ...],
        id_tag: str,
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in _descendants(deployment, deployment_tag):
            name = _reference_name(_first_text(item, *reference_tags)) or _short_name(item)
            item_id = _parse_int(_first_text(item, id_tag))
            if item_id is not None:
                result[name] = item_id
        return result

    @staticmethod
    def _field_deployment_map(deployment: etree._Element) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for item in _descendants(deployment, "SOMEIP-FIELD-DEPLOYMENT"):
            name = _reference_name(_first_text(item, "FIELD-REF")) or _short_name(item)
            values: dict[str, int] = {}
            for key, tag in (
                ("getter", "GETTER-ID"),
                ("setter", "SETTER-ID"),
                ("notifier", "NOTIFIER-ID"),
            ):
                value = _parse_int(_first_text(item, tag))
                if value is not None:
                    values[key] = value
            result[name] = values
        return result

    @staticmethod
    def _eventgroup_map(deployment: etree._Element) -> dict[str, list[int]]:
        result: defaultdict[str, list[int]] = defaultdict(list)
        for group in _descendants(deployment, "SOMEIP-EVENT-GROUP"):
            group_id = _parse_int(_first_text(group, "EVENT-GROUP-ID"))
            if group_id is None:
                continue
            for reference_tag in ("EVENT-REF", "FIELD-REF"):
                for reference in _descendants(group, reference_tag):
                    name = _reference_name(_text(reference))
                    if name and group_id not in result[name]:
                        result[name].append(group_id)
        return dict(result)

    @staticmethod
    def _apply_instances(root: etree._Element, services: dict[str, ServiceDefinition]) -> None:
        by_deployment: dict[str, list[int]] = defaultdict(list)
        for tag in ("PROVIDED-SOMEIP-SERVICE-INSTANCE", "REQUIRED-SOMEIP-SERVICE-INSTANCE"):
            for instance in _descendants(root, tag):
                deployment_name = _reference_name(
                    _first_text(
                        instance,
                        "SERVICE-INTERFACE-DEPLOYMENT-REF",
                        "SERVICE-INTERFACE-REF",
                    )
                )
                instance_id = _parse_int(_first_text(instance, "SERVICE-INSTANCE-ID"))
                if deployment_name and instance_id is not None:
                    by_deployment[deployment_name].append(instance_id)
        for service in services.values():
            candidates = {service.name, f"{service.name}_Deployment", f"{service.name}Deployment"}
            ids = sorted({value for name in candidates for value in by_deployment.get(name, [])})
            service.instance_ids = ids
