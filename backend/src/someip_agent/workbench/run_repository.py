"""持久产品运行历史及标准报告，和仓库开发 pytest 输出分开。"""

import hashlib
import html
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID
from xml.etree.ElementTree import Element, SubElement, tostring

from .scenario_models import RunSummary, RunView


class RunNotFound(LookupError):
    pass


class RunRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS scenario_runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, status TEXT NOT NULL,
                result_json TEXT NOT NULL, input_json TEXT NOT NULL,
                result_sha256 TEXT NOT NULL)""")
            rows = db.execute(
                "SELECT id,result_json FROM scenario_runs WHERE status='running'"
            ).fetchall()
            for row in rows:
                view = RunView.model_validate_json(row["result_json"])
                view.status = "interrupted"
                view.error = "上次进程中断；不声称清理完成或自动重新执行"
                body = view.model_dump_json()
                db.execute(
                    "UPDATE scenario_runs SET status=?,result_json=?,result_sha256=? WHERE id=?",
                    (view.status, body, hashlib.sha256(body.encode()).hexdigest(), row["id"]),
                )

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def save(self, view: RunView, inputs: dict[str, Any] | None = None) -> None:
        body = view.model_dump_json()
        if len(body.encode()) > 8 * 1024 * 1024:
            raise ValueError("运行结果超过 8 MiB，不能持久化为完整结果")
        with closing(self._connect()) as db, db:
            if inputs is not None:
                if db.execute("SELECT COUNT(*) FROM scenario_runs").fetchone()[0] >= 500:
                    raise ValueError("产品测试历史达到 500 次，请先按运维要求归档")
                db.execute(
                    "INSERT INTO scenario_runs VALUES(?,?,?,?,?,?)",
                    (
                        str(view.id),
                        view.started_at.isoformat(),
                        view.status,
                        body,
                        json.dumps(inputs, ensure_ascii=False, allow_nan=False),
                        hashlib.sha256(body.encode()).hexdigest(),
                    ),
                )
            else:
                db.execute(
                    "UPDATE scenario_runs SET status=?,result_json=?,result_sha256=? WHERE id=?",
                    (view.status, body, hashlib.sha256(body.encode()).hexdigest(), str(view.id)),
                )

    def get(self, identifier: UUID) -> RunView:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT result_json,result_sha256 FROM scenario_runs WHERE id=?", (str(identifier),)
            ).fetchone()
        if row is None:
            raise RunNotFound("产品测试运行不存在")
        if hashlib.sha256(row["result_json"].encode()).hexdigest() != row["result_sha256"]:
            raise ValueError("运行结果完整性校验不符")
        return RunView.model_validate_json(row["result_json"])

    def inputs(self, identifier: UUID) -> dict[str, Any]:
        self.get(identifier)
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT input_json FROM scenario_runs WHERE id=?", (str(identifier),)
            ).fetchone()
        return json.loads(row["input_json"])

    def list(self, limit: int = 100) -> list[RunSummary]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT id FROM scenario_runs ORDER BY started_at DESC LIMIT ?",
                (max(1, min(500, limit)),),
            ).fetchall()
        result = []
        for row in rows:
            view = self.get(UUID(row["id"]))
            values = view.model_dump(include=set(RunSummary.model_fields) - {"step_count"})
            result.append(RunSummary(**values, step_count=len(view.steps)))
        return result

    def junit(self, identifier: UUID) -> bytes:
        view = self.get(identifier)
        suite = Element(
            "testsuite",
            name=view.name,
            tests=str(len(view.steps) + 1),
            failures=str(
                sum(step.status == "failed" for step in view.steps) + (view.status != "passed")
            ),
        )
        for step in view.steps:
            case = SubElement(
                suite,
                "testcase",
                name=f"{step.case}:{step.index}:{step.name or step.kind}",
                time=str(step.duration_ms / 1000),
            )
            if step.status == "failed":
                SubElement(case, "failure", message=step.error or "步骤失败").text = step.error
        final = SubElement(suite, "testcase", name="运行完整性与资源清理")
        if view.status != "passed":
            SubElement(final, "failure", message=view.error or view.status).text = "\n".join(
                view.cleanup_errors
            )
        SubElement(suite, "system-out").text = json.dumps(
            {
                "run_id": str(view.id),
                "request_id": view.request_id,
                "recording_id": str(view.recording_id),
                "source_sha256": view.model_source_sha256,
            },
            ensure_ascii=False,
        )
        return tostring(suite, encoding="utf-8", xml_declaration=True)

    def report(self, identifier: UUID) -> str:
        view = self.get(identifier)
        escaped = html.escape(view.model_dump_json(indent=2))
        return (
            f'<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            f"<title>{html.escape(view.name)}</title><body><h1>{html.escape(view.name)}</h1>"
            f"<p>状态：{view.status} · 运行：{view.id} · 清理完成：{view.cleanup_complete}</p>"
            f"<p>版本：{view.application_version} · 原始记录：{view.recording_id}</p>"
            f"<pre>{escaped}</pre></body></html>"
        )
