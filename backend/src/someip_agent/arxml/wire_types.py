"""解析明确的基础类型引用；不以 SHORT-NAME 推断发送端的二进制布局。"""

from __future__ import annotations

from typing import Any

from lxml import etree


class WireTypeError(ValueError):
    """类型缺失、歧义或尚未实现的序列化布局。"""


def _value(element: etree._Element, name: str) -> str | None:
    values = element.xpath(f".//*[local-name()='{name}']/text()")
    unique = {str(value).strip() for value in values if str(value).strip()}
    if len(unique) > 1:
        raise WireTypeError(f"{name} 有多个不同值，不能选择第一个")
    return next(iter(unique), None)


class WireTypeResolver:
    def __init__(self, index: dict[str, etree._Element]) -> None:
        self._index = index
        self._cache: dict[str, dict[str, Any]] = {}

    def resolve(self, reference: str | None, chain: tuple[str, ...] = ()) -> dict[str, Any]:
        if not reference or reference not in self._index:
            raise WireTypeError(f"缺少数据类型完整引用 {reference}")
        if reference in chain or len(chain) >= 64:
            raise WireTypeError(f"数据类型循环或深度超限: {' -> '.join((*chain, reference))}")
        if reference in self._cache:
            return dict(self._cache[reference])
        element = self._index[reference]
        tag = str(etree.QName(element).localname)
        if tag == "SW-BASE-TYPE":
            schema = self._base_type(element)
        elif tag == "IMPLEMENTATION-DATA-TYPE":
            categories = element.xpath("./*[local-name()='CATEGORY']/text()")
            category = str(categories[0]).strip() if categories else ""
            if category not in {"VALUE", "TYPE_REFERENCE"}:
                raise WireTypeError(f"类型 {reference} 的 {category} 需要复合类型部署布局")
            target = _value(
                element, "BASE-TYPE-REF" if category == "VALUE" else "IMPLEMENTATION-DATA-TYPE-REF"
            )
            schema = self.resolve(target, (*chain, reference))
        else:
            raise WireTypeError(f"应用类型 {reference} 需要明确 DATA-TYPE-MAP 和部署布局")
        self._cache[reference] = dict(schema)
        return dict(schema)

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
