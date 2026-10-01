from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from lxml import etree

from someip_agent.arxml.classic import ClassicReferenceResolver, ResolvedClassicBinding
from someip_agent.domain.models import (
    ArxmlModel,
    EventDefinition,
    FieldDefinition,
    MethodDefinition,
    ServiceDefinition,
    SignalDataType,
    SignalDefinition,
)

logger = logging.getLogger(__name__)


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
        if root.getroottree().docinfo.doctype:
            raise ArxmlParseError("出于安全原因，不允许 ARXML 包含 DOCTYPE 或 ENTITY")

        model = ArxmlModel(
            source_name=source_name,
            source_sha256=hashlib.sha256(content).hexdigest(),
            autosar_version=self._detect_version(root),
        )
        services = self._extract_services(root)
        services = self._apply_deployments(root, services, model.warnings)
        self._apply_instances(root, services)
        if not services:
            services = self._extract_classic_service_table(root, model.warnings)
        model.services = sorted(services.values(), key=lambda item: item.name.lower())
        self._resolve_wire_types(root, model)
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
            path = _element_path(element)
            if path in services:
                raise ArxmlParseError(f"服务完整路径重复: {path}")
            methods: list[MethodDefinition] = []
            for operation in _descendants(element, "CLIENT-SERVER-OPERATION"):
                inputs: list[SignalDefinition] = []
                outputs: list[SignalDefinition] = []
                for argument in _descendants(operation, "ARGUMENT-DATA-PROTOTYPE"):
                    direction = (_first_text(argument, "DIRECTION") or "IN").upper()
                    if direction not in {"IN", "OUT", "INOUT"}:
                        raise ArxmlParseError(
                            f"参数 {_element_path(argument)} 的方向非法: {direction}"
                        )
                    signal = _signal_from_prototype(argument)
                    if direction in {"IN", "INOUT"}:
                        inputs.append(signal)
                    if direction in {"OUT", "INOUT"}:
                        outputs.append(signal.model_copy(deep=True))
                methods.append(
                    MethodDefinition(
                        name=_short_name(operation),
                        path=_element_path(operation),
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
                            path=_element_path(prototype),
                            signals=[_signal_from_prototype(prototype)],
                        )
                    )

            fields: list[FieldDefinition] = []
            for field_element in _descendants(element, "FIELD"):
                prototype = _first_descendant(field_element, "VARIABLE-DATA-PROTOTYPE")
                if prototype is None:
                    prototype = field_element
                fields.append(
                    FieldDefinition(
                        name=_short_name(field_element),
                        path=_element_path(field_element),
                        signal=_signal_from_prototype(prototype) if prototype is not None else None,
                    )
                )

            services[path] = ServiceDefinition(
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
        from .wire_types import WireTypeError

        try:
            references = ClassicReferenceResolver(root, _element_path)
        except WireTypeError as exc:
            raise ArxmlParseError(str(exc)) from exc
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
            try:
                bindings = references.resolve(pdu_reference)
                for service in target_services:
                    self._merge_classic_bindings(service, method_id, is_event, bindings)
                    error = "Classic SOME/IP transformer payload 布局尚未接入，禁止自动原生初始化"
                    if error not in service.deployment_errors:
                        service.deployment_errors.append(error)
                continue
            except WireTypeError as exc:
                error = f"Classic 部署 {pdu_reference} 无法绑定: {exc}"
                logger.warning(
                    "Classic 完整引用解析失败，仅保留浏览投影",
                    extra={"operation": "arxml.classic.resolve", "pdu_reference": pdu_reference},
                    exc_info=True,
                )
                for service in target_services:
                    if error not in service.deployment_errors:
                        service.deployment_errors.append(error)
                if error not in warnings:
                    warnings.append(error)
            # 缺失链仍允许浏览旧投影，但 deployment_errors 禁止它进入原生配置。
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
    def _merge_classic_bindings(
        service: ServiceDefinition,
        identifier: int,
        is_event: bool,
        bindings: list[ResolvedClassicBinding],
    ) -> None:
        from .wire_types import WireTypeError

        targets = {binding.source.target_path for binding in bindings}
        directions = {binding.source.direction for binding in bindings}
        if directions == {"data"}:
            interface_paths = set()
            for binding in bindings:
                parent = binding.target.getparent()
                while parent is not None and _local_name(parent) != "SENDER-RECEIVER-INTERFACE":
                    parent = parent.getparent()
                if parent is None:
                    raise WireTypeError("Classic 数据目标不属于 SENDER-RECEIVER-INTERFACE")
                interface_paths.add(_element_path(parent))
            if len(interface_paths) != 1 or len(targets) != len(bindings):
                raise WireTypeError("Classic 单一 I-PDU 的数据目标重复或跨接口")
            path = next(iter(interface_paths))
            name = path.rsplit("/", 1)[-1]
            signals = [_signal_from_prototype(binding.target) for binding in bindings]
            if is_event:
                definition = EventDefinition(
                    name=name, path=path, event_id=identifier, signals=signals
                )
            else:
                definition = MethodDefinition(
                    name=name,
                    path=path,
                    method_id=identifier,
                    input_signals=signals,
                    fire_and_forget=True,
                )
        else:
            if is_event or len(targets) != 1 or not directions <= {"input", "output"}:
                raise WireTypeError("Classic RPC 部署方向或操作目标冲突")
            operation = bindings[0].target
            inputs, outputs = [], []
            for argument in _descendants(operation, "ARGUMENT-DATA-PROTOTYPE"):
                direction = (_first_text(argument, "DIRECTION") or "").upper()
                if direction not in {"IN", "OUT", "INOUT"}:
                    raise WireTypeError("Classic 操作参数缺少明确方向")
                signal = _signal_from_prototype(argument)
                if direction in {"IN", "INOUT"}:
                    inputs.append(signal)
                if direction in {"OUT", "INOUT"}:
                    outputs.append(signal.model_copy(deep=True))
            definition = MethodDefinition(
                name=_short_name(operation),
                path=next(iter(targets)),
                method_id=identifier,
                input_signals=inputs,
                output_signals=outputs,
            )
        collection = service.events if is_event else service.methods
        existing = next(
            (
                item
                for item in collection
                if (item.event_id if is_event else item.method_id) == identifier
            ),
            None,
        )
        if existing is not None and existing.path != definition.path:
            raise WireTypeError("Classic 同一 Header ID 引用不同业务目标，不能保留第一个")
        if existing is None:
            existing = definition
            collection.append(existing)
        for binding in bindings:
            if binding.source not in existing.classic_bindings:
                existing.classic_bindings.append(binding.source)

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
                    if direction not in {"IN", "OUT", "INOUT"}:
                        raise ArxmlParseError(
                            f"参数 {_element_path(argument)} 的方向非法: {direction}"
                        )
                    signal = _signal_from_prototype(argument)
                    if direction in {"IN", "INOUT"}:
                        inputs.append(signal)
                    if direction in {"OUT", "INOUT"}:
                        outputs.append(signal.model_copy(deep=True))
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
            service_name = _reference_name(_first_text(instance, "PROVIDED-SERVICE-INSTANCE-REF"))
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
    ) -> dict[str, ServiceDefinition]:
        deployed: dict[str, ServiceDefinition] = {}
        referenced: set[str] = set()
        for deployment in _descendants(root, "SOMEIP-SERVICE-INTERFACE-DEPLOYMENT"):
            service_ref = _first_text(deployment, "SERVICE-INTERFACE-REF")
            template = services.get(service_ref or "")
            deployment_path = _element_path(deployment)
            if template is None:
                warnings.append(f"部署 {deployment_path} 找不到完整引用 {service_ref}")
                continue
            if deployment_path in deployed:
                raise ArxmlParseError(f"部署完整路径重复: {deployment_path}")
            # 一个接口可以有多个部署；不能后者覆盖前者的 ID/实例。
            service = template.model_copy(deep=True)
            service.deployment_path = deployment_path
            if any(
                "SERIALIZATION" in _local_name(element) or _local_name(element) == "BYTE-ORDER"
                for element in deployment.iter()
            ):
                service.deployment_errors.append(
                    f"部署 {deployment_path} 的显式序列化属性尚未完整映射，不能套用默认 scalar 布局"
                )
            referenced.add(service.path)
            deployed[deployment_path] = service
            service.service_id = _parse_int(_first_text(deployment, "SERVICE-INTERFACE-ID"))
            major = _parse_int(_first_text(deployment, "MAJOR-VERSION"))
            minor = _parse_int(_first_text(deployment, "MINOR-VERSION"))
            if major is None or minor is None:
                service.deployment_errors.append(
                    f"部署 {deployment_path} 缺少明确的 Major/Minor 版本"
                )
            service.major_version = major if major is not None else 1
            service.minor_version = minor if minor is not None else 0

            method_ids = self._deployment_id_map(
                deployment, "SOMEIP-METHOD-DEPLOYMENT", ("METHOD-REF",), "METHOD-ID"
            )
            event_ids = self._deployment_id_map(
                deployment, "SOMEIP-EVENT-DEPLOYMENT", ("EVENT-REF",), "EVENT-ID"
            )
            field_ids = self._field_deployment_map(deployment)
            eventgroups = self._eventgroup_map(deployment)
            for method in service.methods:
                method.method_id = method_ids.get(method.path)
            for event in service.events:
                event.event_id = event_ids.get(event.path)
                event.event_group_ids = eventgroups.get(event.path, [])
            for field in service.fields:
                ids = field_ids.get(field.path, {})
                field.getter_id = ids.get("getter")
                field.setter_id = ids.get("setter")
                field.notifier_id = ids.get("notifier")
                field.event_group_ids = eventgroups.get(field.path, [])
        deployed.update({path: value for path, value in services.items() if path not in referenced})
        return deployed

    @staticmethod
    def _deployment_id_map(
        deployment: etree._Element,
        deployment_tag: str,
        reference_tags: tuple[str, ...],
        id_tag: str,
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in _descendants(deployment, deployment_tag):
            name = _first_text(item, *reference_tags)
            item_id = _parse_int(_first_text(item, id_tag))
            if name and item_id is not None:
                if name in result:
                    raise ArxmlParseError(f"重复的成员部署引用: {name}")
                result[name] = item_id
        return result

    @staticmethod
    def _field_deployment_map(deployment: etree._Element) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for item in _descendants(deployment, "SOMEIP-FIELD-DEPLOYMENT"):
            name = _first_text(item, "FIELD-REF")
            if not name:
                continue
            if name in result:
                raise ArxmlParseError(f"重复的字段部署引用: {name}")
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
        deployment_refs = {
            _element_path(item): _first_text(item, "EVENT-REF", "FIELD-REF")
            for tag in ("SOMEIP-EVENT-DEPLOYMENT", "SOMEIP-FIELD-DEPLOYMENT")
            for item in _descendants(deployment, tag)
        }
        for group in _descendants(deployment, "SOMEIP-EVENT-GROUP"):
            group_id = _parse_int(_first_text(group, "EVENT-GROUP-ID"))
            if group_id is None:
                continue
            for reference_tag in (
                "EVENT-REF",
                "FIELD-REF",
                "EVENT-DEPLOYMENT-REF",
                "FIELD-DEPLOYMENT-REF",
            ):
                for reference in _descendants(group, reference_tag):
                    raw = _text(reference)
                    name = deployment_refs.get(raw or "", raw)
                    if name and group_id not in result[name]:
                        result[name].append(group_id)
        return dict(result)

    @staticmethod
    def _apply_instances(root: etree._Element, services: dict[str, ServiceDefinition]) -> None:
        by_deployment: dict[str, list[int]] = defaultdict(list)
        for tag in ("PROVIDED-SOMEIP-SERVICE-INSTANCE", "REQUIRED-SOMEIP-SERVICE-INSTANCE"):
            for instance in _descendants(root, tag):
                deployment_name = _first_text(instance, "SERVICE-INTERFACE-DEPLOYMENT-REF")
                instance_id = _parse_int(_first_text(instance, "SERVICE-INSTANCE-ID"))
                if deployment_name and instance_id is not None:
                    by_deployment[deployment_name].append(instance_id)
        for service in services.values():
            service.instance_ids = sorted(set(by_deployment.get(service.deployment_path or "", [])))

    @staticmethod
    def _resolve_wire_types(root: etree._Element, model: ArxmlModel) -> None:
        from .transformation import TransformationResolver
        from .wire_types import WireTypeError, WireTypeResolver

        type_tags = {
            "SW-BASE-TYPE",
            "IMPLEMENTATION-DATA-TYPE",
            "APPLICATION-PRIMITIVE-DATA-TYPE",
            "APPLICATION-RECORD-DATA-TYPE",
            "APPLICATION-ARRAY-DATA-TYPE",
            "AP-SOMEIP-TRANSFORMATION-PROPS",
        }
        index: dict[str, etree._Element] = {}
        for element in root.iter():
            if _local_name(element) not in type_tags:
                continue
            path = _element_path(element)
            if path in index:
                raise ArxmlParseError(f"数据类型完整路径重复: {path}")
            index[path] = element
        mappings: dict[str, set[str]] = {}
        for mapping in _descendants(root, "DATA-TYPE-MAP"):
            sources = {
                value
                for item in _descendants(mapping, "APPLICATION-DATA-TYPE-REF")
                if (value := _text(item)) is not None
            }
            targets = {
                value
                for item in _descendants(mapping, "IMPLEMENTATION-DATA-TYPE-REF")
                if (value := _text(item)) is not None
            }
            for source in sources:
                assert source is not None
                mappings.setdefault(source, set()).update(
                    targets if len(sources) == 1 and targets else {""}
                )
        resolver = WireTypeResolver(index, mappings)
        transformation = TransformationResolver(root, index)
        for service in model.services:
            signals = [
                (signal, method.path)
                for method in service.methods
                for signal in [*method.input_signals, *method.output_signals]
            ]
            signals += [
                (signal, event.path) for event in service.events for signal in event.signals
            ]
            signals += [
                (field.signal, field.path) for field in service.fields if field.signal is not None
            ]
            for signal, element_path in signals:
                try:
                    schema = resolver.resolve(signal.type_ref)
                    signal.wire_schema = transformation.apply(schema, element_path)
                    signal.data_type = SignalDataType(signal.wire_schema["type"])
                    if "byte_order" in signal.wire_schema:
                        signal.byte_order = signal.wire_schema["byte_order"]
                except WireTypeError as exc:
                    signal.wire_schema = None
                    # 浏览投影保留旧类型提示，原生配置必须检查 wire_schema，禁止猜测发包。
                    signal.wire_error = str(exc)
                    warning = f"信号 {signal.path} 不能自动初始化原生类型: {exc}"
                    if warning not in model.warnings:
                        model.warnings.append(warning)
                        logger.warning(
                            "ARXML 类型未解析，禁止自动原生初始化",
                            extra={"operation": "arxml.type.resolve", "signal_path": signal.path},
                            exc_info=True,
                        )
