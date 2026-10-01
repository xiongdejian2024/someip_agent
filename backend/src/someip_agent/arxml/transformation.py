"""AP 服务元素的 SOME/IP 序列化属性；未知布局拒绝，不能由 UI 默认值覆盖。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lxml import etree

from .wire_types import WireTypeError, _value


def apply_layout(
    schema: dict[str, Any],
    order: str | None,
    alignment: str | None,
    struct_width: str | None,
    array_width: str | None,
    *,
    classic_cp44: bool = False,
) -> dict[str, Any]:
    """共享显式布局：长度以字节计，对齐属性以 bit 计，不覆盖源类型图。"""
    orders = {"MOST-SIGNIFICANT-BYTE-FIRST": "big", "MOST-SIGNIFICANT-BYTE-LAST": "little"}
    if order not in orders:
        raise WireTypeError("序列化属性缺少明确 BYTE-ORDER")
    try:
        bits = int(alignment if alignment is not None else "8")
    except ValueError as exc:
        raise WireTypeError("ALIGNMENT 非法") from exc
    if not 8 <= bits <= 2048 or bits % 8:
        raise WireTypeError("ALIGNMENT 必须是 8-2048 bit 的整字节对齐")
    result = deepcopy(schema)
    pending = [result]
    while pending:
        node = pending.pop()
        node["byte_order"] = orders[order]
        if bits != 8:
            node["alignment_bytes"] = bits // 8
        kind = node["type"]
        if kind in {"array", "struct"}:
            value = array_width if kind == "array" else struct_width
            if classic_cp44:
                if kind == "array" and "vsa" in node:
                    # CP 4.4.0 的动态长度类型来自 size indicator，不套用后续版本默认32位。
                    indicator_width = int(node["vsa"]["size_type"][4:]) // 8
                    if value is not None and value != str(indicator_width):
                        raise WireTypeError(
                            "VSA_LINEAR 长度字段与 CP 4.4.0 size indicator 类型冲突"
                        )
                    value = str(indicator_width)
                elif value is None and (kind == "struct" or "length" in node):
                    # 未配置可选长度字段：固定结构/数组按类型深度优先连续序列化。
                    value = "0"
            if value not in {"0", "1", "2", "4"}:
                raise WireTypeError(f"复合类型缺少或非法 {kind} LENGTH-FIELD: {value}")
            node["length_bytes"] = int(value)
            if kind == "array" and "length" not in node and value == "0":
                raise WireTypeError("变长数组不能使用零长度字段")
        pending.extend(node.get("fields", []))
        if "element" in node:
            pending.append(node["element"])
    return result


class TransformationResolver:
    def __init__(self, root: etree._Element, paths: dict[str, etree._Element]) -> None:
        self._paths = paths
        self._references: dict[str, set[str]] = {}
        self._unsupported: set[str] = set()
        for mapping in root.xpath(
            "//*[local-name()='TRANSFORMATION-PROPS-TO-SERVICE-INTERFACE-ELEMENT-MAPPING']"
        ):
            references = {
                str(value).strip()
                for value in mapping.xpath(".//*[local-name()='TRANSFORMATION-PROPS-REF']/text()")
            } or {""}
            targets = mapping.xpath(
                ".//*[local-name()='METHOD-REF' or local-name()='EVENT-REF' "
                "or local-name()='FIELD-REF']/text()"
            )
            for target in targets:
                key = str(target).strip()
                self._references.setdefault(key, set()).update(references)
                if mapping.xpath(
                    ".//*[contains(local-name(), 'TLV') or contains(local-name(), 'TLY')]"
                ):
                    self._unsupported.add(key)
        # 细粒度覆盖含上下文引用；未支持前保守拒绝全模型自动初始化，不能忽略覆盖。
        self._fine_grained = bool(
            root.xpath("//*[local-name()='SOMEIP-DATA-PROTOTYPE-TRANSFORMATION-PROPS']")
        )

    def apply(self, schema: dict[str, Any], element_path: str) -> dict[str, Any]:
        if self._fine_grained:
            raise WireTypeError("细粒度 SOMEIP-DATA-PROTOTYPE 序列化覆盖尚未支持，不能忽略")
        references = self._references.get(element_path, set())
        if not references:
            if schema["type"] in {"struct", "array"}:
                raise WireTypeError("复合类型缺少明确的 SOME/IP transformation 部署")
            return deepcopy(schema)
        if len(references) != 1 or element_path in self._unsupported:
            raise WireTypeError("序列化映射有歧义或包含未支持的 TLV 部署")
        reference = next(iter(references))
        props = self._paths.get(reference)
        if props is None or etree.QName(props).localname != "AP-SOMEIP-TRANSFORMATION-PROPS":
            raise WireTypeError(f"缺少明确 AP-SOMEIP-TRANSFORMATION-PROPS 完整引用 {reference}")
        supported = {
            "SHORT-NAME",
            "BYTE-ORDER",
            "ALIGNMENT",
            "SIZE-OF-ARRAY-LENGTH-FIELD",
            "SIZE-OF-STRUCT-LENGTH-FIELD",
            "SESSION-HANDLING",
        }
        for child in props:
            if isinstance(child.tag, str) and etree.QName(child).localname not in supported:
                raise WireTypeError(f"未支持序列化属性 {etree.QName(child).localname}")
        if _value(props, "SESSION-HANDLING") not in {None, "SESSION-HANDLING-INACTIVE"}:
            raise WireTypeError("额外 Transformer session 序列化尚未支持")
        return apply_layout(
            schema,
            _value(props, "BYTE-ORDER"),
            _value(props, "ALIGNMENT"),
            _value(props, "SIZE-OF-STRUCT-LENGTH-FIELD"),
            _value(props, "SIZE-OF-ARRAY-LENGTH-FIELD"),
        )
