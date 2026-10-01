"""按完整引用解析类型图；复合类型仍须由明确的序列化部署补齐布局。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lxml import etree


class WireTypeError(ValueError):
    """类型缺失、歧义或尚未实现的序列化布局。"""


def _value(element: etree._Element, name: str, *, direct: bool = False) -> str | None:
    axis = "./*" if direct else ".//*"
    values = element.xpath(f"{axis}[local-name()='{name}']/text()")
    unique = {str(value).strip() for value in values if str(value).strip()}
    if len(unique) > 1:
        raise WireTypeError(f"{name} 有多个不同值，不能选择第一个")
    return next(iter(unique), None)


class WireTypeResolver:
    def __init__(
        self, index: dict[str, etree._Element], mappings: dict[str, set[str]] | None = None
    ) -> None:
        self._index = index
        self._mappings = mappings or {}
        self._cache: dict[str, dict[str, Any]] = {}

    def resolve(self, reference: str | None, chain: tuple[str, ...] = ()) -> dict[str, Any]:
        if not reference or reference not in self._index:
            raise WireTypeError(f"缺少数据类型完整引用 {reference}")
        if reference in chain or len(chain) >= 64:
            raise WireTypeError(f"数据类型循环或深度超限: {' -> '.join((*chain, reference))}")
        if reference in self._cache:
            return deepcopy(self._cache[reference])
        element = self._index[reference]
        tag = str(etree.QName(element).localname)
        if tag == "SW-BASE-TYPE":
            schema = self._base_type(element)
        elif tag == "IMPLEMENTATION-DATA-TYPE":
            schema = self._implementation(element, (*chain, reference))
        elif tag.startswith("APPLICATION-"):
            targets = self._mappings.get(reference, set())
            if len(targets) != 1:
                raise WireTypeError(
                    f"应用类型 {reference} 缺少唯一 DATA-TYPE-MAP: {sorted(targets)}"
                )
            target = next(iter(targets))
            if (
                target not in self._index
                or etree.QName(self._index[target]).localname != "IMPLEMENTATION-DATA-TYPE"
            ):
                raise WireTypeError(f"DATA-TYPE-MAP 目标不是完整 implementation 类型: {target}")
            schema = self.resolve(target, (*chain, reference))
            profile = _value(element, "DYNAMIC-ARRAY-SIZE-PROFILE", direct=True)
            if profile is not None and profile != schema.get("vsa", {}).get("profile"):
                raise WireTypeError("Application 与 Implementation 动态数组 profile 不一致")
        else:
            raise WireTypeError(f"应用类型 {reference} 需要明确 DATA-TYPE-MAP 和部署布局")
        self._check_size(schema)
        self._cache[reference] = deepcopy(schema)
        return deepcopy(schema)

    def _implementation(self, element: etree._Element, chain: tuple[str, ...]) -> dict[str, Any]:
        categories = element.xpath("./*[local-name()='CATEGORY']/text()")
        if len(categories) != 1:
            raise WireTypeError("implementation 类型缺少唯一 CATEGORY，不能选择变体")
        category = str(categories[0]).strip()
        profile = _value(element, "DYNAMIC-ARRAY-SIZE-PROFILE", direct=True)
        if profile is not None:
            if category != "STRUCTURE" or profile != "VSA_LINEAR":
                raise WireTypeError(f"动态数组 profile {profile} / {category} 尚未接通")
            return self._linear_vsa(element, chain)
        if category in {"VALUE", "TYPE_REFERENCE"}:
            if element.xpath("./*[local-name()='SUB-ELEMENTS']"):
                raise WireTypeError("基础类型/别名不得混入 SUB-ELEMENTS")
            target = _value(
                element, "BASE-TYPE-REF" if category == "VALUE" else "IMPLEMENTATION-DATA-TYPE-REF"
            )
            return self.resolve(target, chain)
        children = element.xpath(
            "./*[local-name()='SUB-ELEMENTS']/*[local-name()='IMPLEMENTATION-DATA-TYPE-ELEMENT']"
        )
        if category == "STRUCTURE":
            if not children or len(children) > 256:
                raise WireTypeError("复合类型 STRUCTURE 缺少有界的显式字段列表")
            fields, names = [], set()
            for child in children:
                values = child.xpath("./*[local-name()='SHORT-NAME']/text()")
                name = str(values[0]).strip() if len(values) == 1 else ""
                if not name or name in names:
                    raise WireTypeError("复合类型字段名称缺失或重复")
                names.add(name)
                fields.append({"name": name, **self._implementation(child, chain)})
                self._check_size({"type": "struct", "fields": fields})
            return {"type": "struct", "fields": fields}
        if category == "ARRAY":
            if len(children) != 1:
                raise WireTypeError("复合类型 ARRAY 必须有唯一元素类型")
            child = children[0]
            # 每一维只读自己的元数据，不能把内层数组的不同长度视为外层歧义。
            size_text = _value(child, "ARRAY-SIZE", direct=True)
            try:
                size = int(size_text or "", 10)
            except ValueError as exc:
                raise WireTypeError(f"ARRAY-SIZE 非法: {size_text}") from exc
            # 声明仅形成类型图，不按上界展开/分配元素。实际值由原生 Codec、
            # IPC 帧和传输各自限制；不能以单个 UDP 报文大小拒绝 TCP 类型声明。
            if not 1 <= size <= 0xFFFFFFFF:
                raise WireTypeError("ARRAY-SIZE 必须在 uint32 正整数范围内")
            semantics = _value(child, "ARRAY-SIZE-SEMANTICS", direct=True)
            if semantics not in {"FIXED-SIZE", "VARIABLE-SIZE"}:
                raise WireTypeError("数组缺少明确 ARRAY-SIZE-SEMANTICS")
            return {
                "type": "array",
                "length" if semantics == "FIXED-SIZE" else "max_length": size,
                "element": self._implementation(child, chain),
            }
        raise WireTypeError(f"复合类型 {category} 尚未实现明确部署布局")

    def _linear_vsa(self, element: etree._Element, chain: tuple[str, ...]) -> dict[str, Any]:
        """new-world VSA：保留字典字段名，wire 上只有一个以字节计的长度字段。"""
        children = element.xpath(
            "./*[local-name()='SUB-ELEMENTS']/*[local-name()='IMPLEMENTATION-DATA-TYPE-ELEMENT']"
        )
        if len(children) != 2:
            raise WireTypeError("VSA_LINEAR 必须依次包含 size indicator 与 payload 两个元素")
        indicator, payload = children
        names = [_value(child, "SHORT-NAME", direct=True) for child in children]
        if any(not name for name in names) or names[0] == names[1]:
            raise WireTypeError("VSA_LINEAR 字段名缺失或重复")
        size = self._implementation(indicator, chain)
        if size["type"] not in {"uint8", "uint16", "uint32"}:
            raise WireTypeError("VSA_LINEAR size indicator 必须为 uint8/16/32")
        forbidden = {"ARRAY-SIZE", "ARRAY-SIZE-HANDLING", "ARRAY-SIZE-SEMANTICS"}
        for child in children:
            if any(_value(child, tag, direct=True) is not None for tag in forbidden):
                raise WireTypeError("VSA_LINEAR size/payload 外层不得声明数组维度元数据")
        if _value(payload, "CATEGORY", direct=True) != "ARRAY":
            raise WireTypeError("VSA_LINEAR 第二个元素必须为 ARRAY payload")
        elements = payload.xpath(
            "./*[local-name()='SUB-ELEMENTS']/*[local-name()='IMPLEMENTATION-DATA-TYPE-ELEMENT']"
        )
        if (
            len(elements) != 1
            or _value(elements[0], "ARRAY-SIZE-SEMANTICS", direct=True) != "VARIABLE-SIZE"
        ):
            raise WireTypeError("VSA_LINEAR payload 必须包含唯一变长维度")
        if _value(elements[0], "ARRAY-SIZE-HANDLING", direct=True) not in {
            None,
            "ALL-INDICES-SAME-ARRAY-SIZE",
        }:
            raise WireTypeError("VSA_LINEAR ARRAY-SIZE-HANDLING 不支持")
        result = self._implementation(payload, chain)
        if result["element"]["type"] == "array":
            raise WireTypeError("VSA_LINEAR 不支持多维数组，不能静默改用其他 profile")
        if result["max_length"] >= 1 << int(size["type"][4:]):
            raise WireTypeError("VSA_LINEAR size indicator 容量不足以表示最大元素数量")
        result["vsa"] = {
            "profile": "VSA_LINEAR",
            "size_name": names[0],
            "payload_name": names[1],
            "size_type": size["type"],
        }
        return result

    @staticmethod
    def _check_size(schema: dict[str, Any]) -> None:
        pending = [(schema, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > 4096 or depth >= 64:
                raise WireTypeError("复合类型图展开节点数或深度超限")
            pending.extend((field, depth + 1) for field in node.get("fields", []))
            if "element" in node:
                pending.append((node["element"], depth + 1))

    @staticmethod
    def _base_type(element: etree._Element) -> dict[str, Any]:
        size_text = _value(element, "BASE-TYPE-SIZE")
        try:
            size = int(size_text or "")
        except ValueError as exc:
            raise WireTypeError(f"BASE-TYPE-SIZE 非法: {size_text}") from exc
        encoding = _value(element, "BASE-TYPE-ENCODING")
        if encoding in {"NONE", "2C"} and size in {8, 16, 32, 64}:
            return {"type": f"{'uint' if encoding == 'NONE' else 'int'}{size}"}
        if encoding == "IEEE754" and size in {32, 64}:
            return {"type": f"float{size}"}
        if encoding == "BOOLEAN" and size == 8:
            return {"type": "boolean"}
        raise WireTypeError(f"原生基础类型不支持编码 {encoding} / {size} bit")
