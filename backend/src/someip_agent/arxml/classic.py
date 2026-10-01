"""复用 lxml 解析 Classic 引用图，不按 SHORT-NAME 前缀推断部署。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lxml import etree

from someip_agent.domain.models import ClassicSignalBinding

from .wire_types import WireTypeError, _value


@dataclass
class ResolvedClassicBinding:
    source: ClassicSignalBinding
    target: etree._Element


class ClassicReferenceResolver:
    def __init__(self, root: etree._Element, element_path: Callable[[etree._Element], str]) -> None:
        self._path = element_path
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
        self._targets: dict[str, set[tuple[str, str, str]]] = {}
        for mapping in root.xpath(
            "//*[local-name()='SENDER-RECEIVER-TO-SIGNAL-MAPPING' "
            "or local-name()='CLIENT-SERVER-TO-SIGNAL-MAPPING']"
        ):
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

    def resolve(self, triggering_path: str) -> list[ResolvedClassicBinding]:
        triggering = self._resolve(triggering_path, "PDU-TRIGGERING")
        pdu_path = _value(triggering, "I-PDU-REF")
        pdu = self._resolve(pdu_path, "I-SIGNAL-I-PDU")
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
            system_path = _value(signal, "SYSTEM-SIGNAL-REF")
            self._resolve(system_path, "SYSTEM-SIGNAL")
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
                    ),
                    target=target,
                )
            )
        return result
