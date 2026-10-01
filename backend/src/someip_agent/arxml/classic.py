"""复用 lxml 解析 Classic 引用图，不按 SHORT-NAME 前缀推断部署。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from lxml import etree

from someip_agent.domain.models import ClassicHeaderProperties, ClassicSignalBinding

from .wire_types import WireTypeError, _value


@dataclass
class ResolvedClassicBinding:
    source: ClassicSignalBinding
    target: etree._Element


class ClassicReferenceResolver:
    def __init__(self, root: etree._Element, element_path: Callable[[etree._Element], str]) -> None:
        self._path = element_path
        schema = root.get("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation", "").split()
        self._cp44 = (
            len(schema) == 2
            and schema[0] == "http://autosar.org/schema/r4.0"
            and schema[1].rsplit("/", 1)[-1] == "AUTOSAR_00046.xsd"
        )
        self._fine_grained = bool(
            root.xpath("//*[local-name()='SOMEIP-DATA-PROTOTYPE-TRANSFORMATION-PROPS']")
        )
        self._index: dict[str, etree._Element] = {}
        self._ambiguous: set[str] = set()
        tags = {
            "PDU-TRIGGERING",
            "I-SIGNAL-I-PDU",
            "I-SIGNAL",
            "SYSTEM-SIGNAL",
            "VARIABLE-DATA-PROTOTYPE",
            "CLIENT-SERVER-OPERATION",
            "DATA-TRANSFORMATION",
            "TRANSFORMATION-TECHNOLOGY",
        }
        for element in root.iter():
            if not isinstance(element.tag, str) or etree.QName(element).localname not in tags:
                continue
            path = self._path(element)
            if path in self._index:
                self._ambiguous.add(path)
            else:
                self._index[path] = element
        self._targets: dict[str, set[tuple[str, str, Literal["input", "output", "data"]]]] = {}
        for mapping in root.xpath(
            "//*[local-name()='SENDER-RECEIVER-TO-SIGNAL-MAPPING' "
            "or local-name()='CLIENT-SERVER-TO-SIGNAL-MAPPING']"
        ):
            relations: list[tuple[str, str, Literal["input", "output", "data"]]]
            if etree.QName(mapping).localname == "SENDER-RECEIVER-TO-SIGNAL-MAPPING":
                relations = [("SYSTEM-SIGNAL-REF", "TARGET-DATA-PROTOTYPE-REF", "data")]
            else:
                relations = [
                    ("CALL-SIGNAL-REF", "TARGET-OPERATION-REF", "input"),
                    ("RETURN-SIGNAL-REF", "TARGET-OPERATION-REF", "output"),
                ]
            for signal_tag, target_tag, direction in relations:
                signals = self._references(mapping, signal_tag)
                targets = self._references(mapping, target_tag) or [""]
                for signal in signals:
                    for target in targets:
                        self._targets.setdefault(signal, set()).add((target, target_tag, direction))

    @staticmethod
    def _references(element: etree._Element, name: str) -> list[str]:
        return [
            str(value).strip()
            for value in element.xpath(f".//*[local-name()='{name}']/text()")
            if str(value).strip()
        ]

    def _resolve(self, reference: str | None, tag: str) -> etree._Element:
        if reference in self._ambiguous:
            raise WireTypeError(f"Classic 部署完整路径重复，不能选择第一个: {reference}")
        element = self._index.get(reference or "")
        if element is None or etree.QName(element).localname != tag:
            raise WireTypeError(f"Classic 缺少明确 {tag} 完整引用: {reference}")
        return element

    def apply_layout(
        self, schema: dict[str, Any], bindings: list[ClassicSignalBinding]
    ) -> dict[str, Any]:
        """读取已绑定 I-SIGNAL 的显式 payload 布局；不解释 header/session 枚举。"""
        from .transformation import apply_layout

        if self._fine_grained:
            raise WireTypeError("Classic 细粒度 SOMEIP-DATA-PROTOTYPE 覆盖尚未支持")
        layouts = []
        for binding in bindings:
            if binding.start_position != 0:
                raise WireTypeError("Classic transformer 非零 START-POSITION 尚未接通")
            if len(binding.transformer_paths) != 1:
                raise WireTypeError(
                    "Classic payload 需要唯一 SOMEIP serializer，不忽略其他 transformer"
                )
            technology = self._resolve(binding.transformer_paths[0], "TRANSFORMATION-TECHNOLOGY")
            if (
                _value(technology, "PROTOCOL", direct=True) != "SOMEIP"
                or _value(technology, "VERSION", direct=True) != "1.0.0"
                or _value(technology, "TRANSFORMER-CLASS", direct=True) != "SERIALIZER"
                or _value(technology, "HEADER-LENGTH") != "64"
            ):
                raise WireTypeError("Classic 需要明确 SOMEIP 1.0.0 SERIALIZER / 64-bit header")
            descriptions = technology.xpath(
                "./*[local-name()='TRANSFORMATION-DESCRIPTIONS']"
                "/*[local-name()='SOMEIP-TRANSFORMATION-DESCRIPTION']"
            )
            if len(descriptions) != 1:
                raise WireTypeError("Classic 缺少唯一 SOMEIP-TRANSFORMATION-DESCRIPTION")
            description = descriptions[0]
            if _value(description, "ALIGNMENT") is None:
                raise WireTypeError("Classic description 缺少明确 ALIGNMENT，不能套用 AP 默认值")
            supported_description = {"ALIGNMENT", "BYTE-ORDER", "INTERFACE-VERSION"}
            if any(
                isinstance(child.tag, str)
                and etree.QName(child).localname not in supported_description
                for child in description
            ):
                raise WireTypeError("Classic description 包含未支持属性")
            signal = self._resolve(binding.signal_path, "I-SIGNAL")
            variants = signal.xpath(
                ".//*[local-name()='SOMEIP-TRANSFORMATION-I-SIGNAL-PROPS-CONDITIONAL']"
            )
            if len(variants) != 1 or self._references(variants[0], "TRANSFORMER-REF") != [
                binding.transformer_paths[0]
            ]:
                raise WireTypeError("Classic 缺少唯一且同 chain 的 I-SIGNAL props")
            props = variants[0]
            supported_props = {
                "TRANSFORMER-REF",
                "MESSAGE-TYPE",
                "SESSION-HANDLING-SR",
                "INTERFACE-VERSION",
                "SIZE-OF-STRUCT-LENGTH-FIELDS",
                "SIZE-OF-ARRAY-LENGTH-FIELDS",
            }
            if any(
                isinstance(child.tag, str) and etree.QName(child).localname not in supported_props
                for child in props
            ):
                raise WireTypeError("Classic I-SIGNAL props 包含未支持属性或 TLV 覆盖")
            layouts.append(
                apply_layout(
                    schema,
                    _value(description, "BYTE-ORDER"),
                    _value(description, "ALIGNMENT"),
                    _value(props, "SIZE-OF-STRUCT-LENGTH-FIELDS"),
                    _value(props, "SIZE-OF-ARRAY-LENGTH-FIELDS"),
                    classic_cp44=self._cp44,
                )
            )
        if not layouts or any(layout != layouts[0] for layout in layouts[1:]):
            raise WireTypeError("Classic 同一参数存在冲突或缺失的 payload 布局")
        return layouts[0]

    def _header_properties(
        self, signal: etree._Element, transformer_paths: list[str]
    ) -> list[ClassicHeaderProperties]:
        """按完整引用保存所有原始变体；缺失字段显式为空，不选择第一个变体。"""
        variants = signal.xpath(
            ".//*[local-name()='SOMEIP-TRANSFORMATION-I-SIGNAL-PROPS-CONDITIONAL']"
        )
        records = []
        for path in dict.fromkeys(transformer_paths):
            technology = self._resolve(path, "TRANSFORMATION-TECHNOLOGY")
            descriptions = technology.xpath(
                "./*[local-name()='TRANSFORMATION-DESCRIPTIONS']"
                "/*[local-name()='SOMEIP-TRANSFORMATION-DESCRIPTION']"
            )
            # 非唯一 description 不投影它的值，避免把同值多变体当成已确定部署。
            description = descriptions[0] if len(descriptions) == 1 else None
            matched = [
                variant
                for variant in variants
                if self._references(variant, "TRANSFORMER-REF") == [path]
            ]
            for props in matched or [None]:
                records.append(
                    ClassicHeaderProperties(
                        transformer_path=path,
                        signal_props_present=props is not None,
                        description_count=len(descriptions),
                        protocol_raw=_value(technology, "PROTOCOL", direct=True),
                        transformer_version_raw=_value(technology, "VERSION", direct=True),
                        header_length_bits_raw=_value(technology, "HEADER-LENGTH"),
                        message_type_raw=(
                            _value(props, "MESSAGE-TYPE", direct=True)
                            if props is not None
                            else None
                        ),
                        session_handling_sr_raw=(
                            _value(props, "SESSION-HANDLING-SR", direct=True)
                            if props is not None
                            else None
                        ),
                        signal_interface_version_raw=(
                            _value(props, "INTERFACE-VERSION", direct=True)
                            if props is not None
                            else None
                        ),
                        description_interface_version_raw=(
                            _value(description, "INTERFACE-VERSION", direct=True)
                            if description is not None
                            else None
                        ),
                    )
                )
        return records

    def resolve(self, triggering_path: str) -> list[ResolvedClassicBinding]:
        triggering = self._resolve(triggering_path, "PDU-TRIGGERING")
        pdu_path = _value(triggering, "I-PDU-REF")
        pdu = self._resolve(pdu_path, "I-SIGNAL-I-PDU")
        assert pdu_path is not None  # _resolve 已拒绝缺失引用。
        mappings = pdu.xpath(
            "./*[local-name()='I-SIGNAL-TO-PDU-MAPPINGS']"
            "/*[local-name()='I-SIGNAL-TO-I-PDU-MAPPING']"
        )
        if not 1 <= len(mappings) <= 256:
            raise WireTypeError("Classic I-PDU 缺少有界的 I-SIGNAL 映射")
        result = []
        for mapping in mappings:
            signal_path = _value(mapping, "I-SIGNAL-REF")
            signal = self._resolve(signal_path, "I-SIGNAL")
            assert signal_path is not None
            system_path = _value(signal, "SYSTEM-SIGNAL-REF")
            self._resolve(system_path, "SYSTEM-SIGNAL")
            assert system_path is not None
            targets = self._targets.get(system_path or "", set())
            # 相同目标的多 ECU 上下文引用可去重，不同业务目标不能任取第一个。
            if len(targets) != 1:
                raise WireTypeError(f"Classic SYSTEM-SIGNAL 缺少唯一业务映射: {system_path}")
            target_path, target_tag, direction = next(iter(targets))
            target = self._resolve(
                target_path,
                "VARIABLE-DATA-PROTOTYPE"
                if target_tag == "TARGET-DATA-PROTOTYPE-REF"
                else "CLIENT-SERVER-OPERATION",
            )
            position = _value(mapping, "START-POSITION", direct=True)
            try:
                start_position = int(position or "", 10)
            except ValueError as exc:
                raise WireTypeError(f"Classic START-POSITION 非法: {position}") from exc
            if start_position < 0:
                raise WireTypeError("Classic START-POSITION 不得为负数")
            transformations = self._references(signal, "DATA-TRANSFORMATION-REF")
            if not transformations:
                raise WireTypeError(
                    f"Classic I-SIGNAL 缺少 DATA-TRANSFORMATION 引用: {signal_path}"
                )
            transformers = []
            for reference in transformations:
                transformation = self._resolve(reference, "DATA-TRANSFORMATION")
                chain = self._references(transformation, "TRANSFORMER-CHAIN-REF")
                if not chain:
                    raise WireTypeError(
                        f"Classic transformation 缺少 transformer chain: {reference}"
                    )
                for transformer in chain:
                    self._resolve(transformer, "TRANSFORMATION-TECHNOLOGY")
                    transformers.append(transformer)
            for transformer in self._references(signal, "TRANSFORMER-REF"):
                self._resolve(transformer, "TRANSFORMATION-TECHNOLOGY")
                if transformer not in transformers:
                    raise WireTypeError(
                        "Classic I-SIGNAL props 引用的 transformer 不属于声明的 chain"
                    )
            result.append(
                ResolvedClassicBinding(
                    source=ClassicSignalBinding(
                        triggering_path=triggering_path,
                        pdu_path=pdu_path,
                        mapping_path=self._path(mapping),
                        signal_path=signal_path,
                        system_signal_path=system_path,
                        target_path=target_path,
                        direction=direction,
                        start_position=start_position,
                        transformation_paths=transformations,
                        transformer_paths=transformers,
                        header_properties=self._header_properties(signal, transformers),
                    ),
                    target=target,
                )
            )
        return result
