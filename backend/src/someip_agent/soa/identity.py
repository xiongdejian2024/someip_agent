"""具名 application 的启动契约；声明用于预检，实际身份必须由运行时另行回报。"""

from __future__ import annotations

import logging
from typing import Any

from .operator import NativeOperationError, SOAOperator

logger = logging.getLogger(__name__)
CAPABILITY = "member_application_identity_v1"


def prepare_identities(
    operator: SOAOperator, configs: dict[str, Any]
) -> dict[str, tuple[str, int]]:
    """只在显式选择身份时握手；不缓存，重启/更换二进制后必须重新确认。"""
    selected = {
        alias: cfg
        for alias, cfg in configs.items()
        if cfg.get("enable", "enable") != "disable"
        and ("application_name" in cfg or "application_id" in cfg)
    }
    if not selected:
        return {}
    try:
        result = operator.send_request("ping", print_result=False)
        if (
            not isinstance(result, dict)
            or result.get("runtime") != "vsomeip"
            or type(result.get("protocol")) is not int
            or result["protocol"] != 1
            or not isinstance(result.get("capabilities"), list)
            or not all(isinstance(item, str) for item in result["capabilities"])
            or CAPABILITY not in result["capabilities"]
        ):
            raise NativeOperationError("二进制不支持独立 application 身份契约，请升级原生运行时")
        declared = result.get("configured_applications")
        if (
            not isinstance(declared, dict)
            or not declared
            or not all(
                isinstance(name, str)
                and name
                and type(identifier) is int
                and 0 < identifier < 0xFFFF
                for name, identifier in declared.items()
            )
            or len(set(declared.values())) != len(declared)
        ):
            raise NativeOperationError("原生 application 声明缺失、非法或 Client ID 重复")
        expected = {}
        for alias, cfg in selected.items():
            name = cfg.get("application_name")
            if not isinstance(name, str) or name not in declared:
                raise NativeOperationError(f"application 未在原生配置中声明: {name!r}")
            identifier = cfg.get("application_id", declared[name])
            if type(identifier) is not int or identifier != declared[name]:
                raise NativeOperationError(f"application {name} 的 Client ID 与原生声明不一致")
            expected[f"{alias}_{cfg['role']}"] = (name, identifier)
        logger.info(
            "已确认原生独立 application 能力和声明",
            extra={"operation": "soa.identity.prepare", "members": list(expected)},
        )
        return expected
    except Exception:
        logger.exception(
            "原生 application 身份预检失败", extra={"operation": "soa.identity.prepare"}
        )
        raise


def verify_identities(
    operator: SOAOperator, expected: dict[str, tuple[str, int]], addresses: dict[str, Any]
) -> None:
    """业务 socket 建链前核对实际 get_name/get_client，不用请求值填充观测值。"""
    if not expected:
        return
    try:
        running = operator.send_request("running_service", print_result=False)
        if not isinstance(running, dict) or not isinstance(addresses, dict):
            raise NativeOperationError("原生成员身份回执格式非法")
        for key, (name, identifier) in expected.items():
            actual = running.get(key)
            if (
                key not in addresses
                or not isinstance(actual, dict)
                or actual.get("application_name") != name
                or type(actual.get("application_id")) is not int
                or actual["application_id"] != identifier
            ):
                raise NativeOperationError(f"成员 {key} 的实际 application 身份与声明不一致")
        logger.info(
            "已核对原生成员实际 application 身份",
            extra={"operation": "soa.identity.verify", "members": list(expected)},
        )
    except Exception:
        logger.exception("原生成员实际身份核对失败", extra={"operation": "soa.identity.verify"})
        raise
