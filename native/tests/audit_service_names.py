"""只读核对 ARXML 与指定 comm 矩阵名称；不生成服务、布局或车辆端点。"""

import argparse
import hashlib
import json
import logging
from collections import Counter
from pathlib import Path

from lxml import etree
from someip_agent.arxml.parser import ArxmlParser, _element_path
from someip_agent.arxml.wire_types import WireTypeError, WireTypeResolver

logger = logging.getLogger(__name__)


def audit_vsa_declarations(content):
    """只核对明确标注 profile 的 Implementation 类型图，不猜测 STRING/部署。"""
    root = etree.fromstring(
        content,
        etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
        ),
    )
    if root.getroottree().docinfo.doctype:
        raise ValueError("类型审计禁止 DTD")
    index = {}
    for node in root.iter():
        if not isinstance(node.tag, str) or etree.QName(node).localname not in {
            "SW-BASE-TYPE",
            "IMPLEMENTATION-DATA-TYPE",
        }:
            continue
        path = _element_path(node)
        if path in index:
            raise ValueError(f"类型审计完整路径重复：{path}")
        index[path] = node
    types = WireTypeResolver(index)
    declarations, errors = [], []
    for path, node in index.items():
        if not node.xpath("./*[local-name()='DYNAMIC-ARRAY-SIZE-PROFILE']"):
            continue
        try:
            schema = types.resolve(path)
            declarations.append(
                {
                    "type_path": path,
                    "max_elements": schema["max_length"],
                    "indicator_type": schema["vsa"]["size_type"],
                    "profile": schema["vsa"]["profile"],
                }
            )
        except WireTypeError as exc:
            logger.exception("源 VSA 类型图审计失败：%s", path)
            errors.append({"type_path": path, "error": str(exc)})
    return {
        "scope": "仅完整引用类型图，不证明部署布局或最大报文可发送",
        "declarations": declarations,
        "errors": errors,
        "declared_count": len(declarations) + len(errors),
        "resolved_count": len(declarations),
        "error_count": len(errors),
        "runtime_verified": False,
    }


def name_index(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("服务名称矩阵必须为非空数组")
    names = {}
    for row in rows:
        value = row["service_id"]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("矩阵 Service ID 类型非法")
        identifier = int(value, 0) if isinstance(value, str) else value
        name = row["service_name"]
        if (
            not 0 <= identifier <= 0xFFFE
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise ValueError("矩阵服务名称或 ID 非法")
        if identifier in names and names[identifier] != name:
            raise ValueError(f"矩阵同一 Service ID 存在不同名称：0x{identifier:04x}")
        names[identifier] = name
    return names


def audit_names(model, definitions, communications):
    if not isinstance(communications, list):
        raise TypeError("通信矩阵必须为 ECU 数组")
    rows = [
        service
        for ecu in communications
        for vlan in ecu["vlan_list"]
        for service in vlan["service_list"]
    ]
    defined, deployed = name_index(definitions), name_index(rows)
    if not model.services:
        raise ValueError("ARXML 不包含服务，不能以空集合判定一致")
    matches = []
    for service in model.services:
        identifier = service.service_id
        if identifier is None:
            raise ValueError(f"ARXML 服务缺少部署 ID：{service.path}")
        if (
            defined.get(identifier) != service.name
            or deployed.get(identifier) != service.name
        ):
            raise ValueError(f"ARXML 与两份 comm 矩阵的名称/ID 不一致：{service.name}")
        matches.append(
            {"name": service.name, "service_id": identifier, "path": service.path}
        )
    signals = [
        signal
        for service in model.services
        for signal in (
            [
                s
                for method in service.methods
                for s in method.input_signals + method.output_signals
            ]
            + [s for event in service.events for s in event.signals]
            + [field.signal for field in service.fields if field.signal is not None]
        )
    ]
    # 同一 I-SIGNAL 可能因多个 ECU 引用重复出现；属性统计按信号/transformer/变体去重。
    header_records = {
        (binding.signal_path, index, props.model_dump_json()): props
        for service in model.services
        for member in [*service.methods, *service.events]
        for binding in member.classic_bindings
        for index, props in enumerate(binding.header_properties)
    }
    return {
        "scope": "仅源服务名称与 Service ID；不证明序列化、端点、方法或线上功能可用",
        "service_count": len(matches),
        "matrix_service_count": len(defined),
        "communication_row_count": len(rows),
        "matches": matches,
        "wire_schema_error_count": sum(
            signal.wire_error is not None for signal in signals
        ),
        "wire_schema_error_groups": dict(
            Counter(
                signal.wire_error for signal in signals if signal.wire_error is not None
            )
        ),
        "wire_schema_errors": [
            {
                "signal_path": signal.path,
                "type_ref": signal.type_ref,
                "error": signal.wire_error,
            }
            for signal in signals
            if signal.wire_error is not None
        ],
        "classic_bound_member_count": sum(
            bool(member.classic_bindings)
            for service in model.services
            for member in [*service.methods, *service.events]
        ),
        "classic_reference_binding_count": sum(
            len(member.classic_bindings)
            for service in model.services
            for member in [*service.methods, *service.events]
        ),
        "classic_header_metadata": {
            "scope": "只保留源原始属性，不确认 OEM 枚举映射或线上 header/session 语义",
            "unique_signal_variant_count": len(header_records),
            "message_type_raw_counts": dict(
                Counter(
                    props.message_type_raw or "<missing>"
                    for props in header_records.values()
                )
            ),
            "session_handling_sr_raw_counts": dict(
                Counter(
                    props.session_handling_sr_raw or "<missing>"
                    for props in header_records.values()
                )
            ),
            "runtime_verified": False,
        },
        "deployment_error_count": sum(
            len(service.deployment_errors) for service in model.services
        ),
        "classic_bindings": [
            {
                "service": service.name,
                "member": member.name,
                "member_path": member.path,
                "bindings": [
                    binding.model_dump() for binding in member.classic_bindings
                ],
            }
            for service in model.services
            for member in [*service.methods, *service.events]
            if member.classic_bindings
        ],
        "deployment_errors": [
            {"service": service.name, "errors": list(service.deployment_errors)}
            for service in model.services
            if service.deployment_errors
        ],
        "verified": True,
        "runtime_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arxml", type=Path, required=True)
    parser.add_argument("--service-define", type=Path, required=True)
    parser.add_argument("--communication-define", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("拒绝覆盖既有名称审计证据")
    # 每条解析异常的完整堆栈保留在新日志，不把大量布局错误隐藏为名称成功。
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(args.log_file, mode="x", encoding="utf-8")],
    )
    try:
        sources = {
            "arxml": args.arxml.read_bytes(),
            "service_define": args.service_define.read_bytes(),
            "communication_define": args.communication_define.read_bytes(),
        }
        model = ArxmlParser().parse(sources["arxml"], args.arxml.name)
        result = audit_names(
            model,
            json.loads(sources["service_define"]),
            json.loads(sources["communication_define"]),
        )
        result["vsa_type_graph"] = audit_vsa_declarations(sources["arxml"])
        result["source_sha256"] = {
            key: hashlib.sha256(data).hexdigest() for key, data in sources.items()
        }
        result["sources"] = {key: str(getattr(args, key)) for key in sources}
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
        logger.info(
            "源服务名称审计通过：%s 个；不代表业务布局就绪", result["service_count"]
        )
        print(
            f"源名称核对通过：{result['service_count']} 个；布局错误：{result['wire_schema_error_count']}，详见日志"
        )
    except Exception:
        logger.exception("源服务名称审计失败")
        raise


if __name__ == "__main__":
    main()
