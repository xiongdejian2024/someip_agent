"""只读核对 ARXML 与指定 comm 矩阵名称；不生成服务、布局或车辆端点。"""

import argparse
import hashlib
import json
import logging
from collections import Counter
from pathlib import Path

from someip_agent.arxml.parser import ArxmlParser

logger = logging.getLogger(__name__)


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
