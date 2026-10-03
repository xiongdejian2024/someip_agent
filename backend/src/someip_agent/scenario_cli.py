"""无界面产品场景客户端；与网页共用 API，不启动第二套协议运行时。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from someip_agent.logging_config import configure_logging
from someip_agent.workbench.results import verify_evidence
from someip_agent.workbench.scenario_models import ScenarioDefinition

logger = logging.getLogger(__name__)


def api_root(server: str) -> str:
    parsed = urlsplit(server)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("服务地址必须为明确的 HTTP/HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("服务地址不接受凭据、查询参数或片段")
    return server.rstrip("/") + "/api/v1/scenarios/runs"


def write_artifact(path: Path | None, body: bytes) -> None:
    if path is None:
        return
    # 用户明确指定输出位置；独占创建，避免覆盖已有证据或跟随已有文件符号链接。
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    logger.info("产品场景报告已写入", extra={"operation": "scenario.cli.export"})


def execute(args: argparse.Namespace, client: httpx.Client) -> int:
    root = api_root(args.server)
    if args.definition.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("场景定义超过 2 MiB")
    body = args.definition.read_bytes()
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("场景定义超过 2 MiB")
    definition = ScenarioDefinition.model_validate_json(body)
    identifier: str | None = None
    try:
        # 不重试启动 POST；响应不确定时不能重复创建场景。
        response = client.post(
            root,
            json={
                "project_id": str(args.project),
                "definition": definition.model_dump(mode="json", exclude_unset=True),
            },
            headers={"X-Request-ID": str(uuid4())},
        )
        response.raise_for_status()
        identifier = str(UUID(response.json()["id"]))
        logger.info(
            "产品场景已提交，等待最终结果",
            extra={
                "operation": "scenario.cli.start",
                "run_id": identifier,
            },
        )
        deadline = time.monotonic() + args.timeout
        while True:
            response = client.get(f"{root}/{identifier}")
            response.raise_for_status()
            result = response.json()
            if result["status"] != "running":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("客户端等待超时，将取消本次运行并等待清理")
            time.sleep(0.25)
    except (Exception, KeyboardInterrupt):
        logger.exception(
            "场景客户端中断，尝试取消已知的自有运行",
            extra={
                "operation": "scenario.cli.cancel",
                "run_id": identifier,
            },
        )
        if identifier:
            cancelled = client.post(f"{root}/{identifier}/cancel", timeout=90)
            cancelled.raise_for_status()
            logger.info(
                "取消与资源清理已返回",
                extra={
                    "operation": "scenario.cli.cancelled",
                    "run_id": identifier,
                    "status": cancelled.json()["status"],
                },
            )
        raise
    write_artifact(
        args.result, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode()
    )
    for suffix, path in (("junit", args.junit), ("report", args.html)):
        if path:
            response = client.get(f"{root}/{identifier}/{suffix}")
            response.raise_for_status()
            write_artifact(path, response.content)
    evidence_path = getattr(args, "evidence", None)
    evidence_complete = True
    if evidence_path:
        response = client.get(f"{root}/{identifier}/evidence", timeout=90)
        response.raise_for_status()
        write_artifact(evidence_path, response.content)
        evidence_complete = bool(verify_evidence(evidence_path)["complete"])
        if not evidence_complete:
            logger.warning(
                "运行结果已返回，但证据包声明不完整", extra={"operation": "scenario.cli.evidence"}
            )
    logger.info(
        "产品场景已完成",
        extra={
            "operation": "scenario.cli.finish",
            "run_id": identifier,
            "status": result["status"],
        },
    )
    return (
        0
        if result["status"] == "passed" and result["cleanup_complete"] and evidence_complete
        else 1
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="无界面执行已保存工程的声明式 SOME/IP 场景")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="正在运行的工作台后端")
    parser.add_argument("--project", type=UUID, required=True, help="已保存工程 UUID")
    parser.add_argument("--definition", type=Path, required=True, help="场景定义 JSON 文件")
    parser.add_argument("--timeout", type=float, default=720, help="等待上限秒数（含清理）")
    parser.add_argument("--result", type=Path, help="最终结果 JSON（不覆盖已有文件）")
    parser.add_argument("--junit", type=Path, help="JUnit XML（不覆盖已有文件）")
    parser.add_argument("--html", type=Path, help="HTML 报告（不覆盖已有文件）")
    parser.add_argument(
        "--evidence", type=Path, help="完整证据 ZIP（不完整时退出 1，不覆盖已有文件）"
    )
    args = parser.parse_args(argv)
    configure_logging("INFO")
    if not 0 < args.timeout <= 900:
        parser.error("等待上限必须大于 0 且不超过 900 秒")
    try:
        with httpx.Client(timeout=45, trust_env=False) as client:
            return execute(args, client)
    except KeyboardInterrupt:
        logger.exception("用户终止场景客户端", extra={"operation": "scenario.cli.interrupted"})
        return 130
    except Exception:
        logger.exception("场景客户端执行失败", extra={"operation": "scenario.cli.failed"})
        return 2


def verify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线只读校验产品证据 ZIP，不执行或导入内容")
    parser.add_argument("path", type=Path, help="已下载证据 ZIP")
    args = parser.parse_args(argv)
    configure_logging("INFO")
    try:
        manifest = verify_evidence(args.path)
        logger.info(
            "证据附件与场景/模型关联校验通过",
            extra={
                "operation": "evidence.verify",
                "run_id": manifest["run_id"],
                "status": "complete" if manifest["complete"] else "incomplete",
            },
        )
        return 0 if manifest["complete"] else 1
    except Exception:
        logger.exception("证据校验失败", extra={"operation": "evidence.verify"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
