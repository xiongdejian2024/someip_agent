"""版本化工程配置存储；工程不是启动脚本，也不携带主机授权或密钥。"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from someip_agent.domain.models import (
    ArxmlModel,
    ListenerConfig,
    SignalGeneratorConfig,
    SimulationConfig,
)
from someip_agent.runtime.service_models import ServiceCycleCommand
from someip_agent.soa.catalog import NativeCatalogRequest

logger = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GeneratorDraft(SignalGeneratorConfig):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SimulationDraft(SimulationConfig):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    generator: GeneratorDraft = Field(default_factory=GeneratorDraft)


class ListenerDraft(ListenerConfig):
    model_config = ConfigDict(extra="forbid")


class CycleDraft(StrictModel):
    service_profile: str = Field(min_length=1, max_length=128)
    command: ServiceCycleCommand


class WaveSelection(StrictModel):
    service_id: int = Field(ge=0, le=65535)
    method_id: int = Field(ge=0, le=65535)
    signal_name: str = Field(min_length=1, max_length=512)


class WorkspaceSelection(StrictModel):
    page: Literal["dashboard", "services", "simulation", "monitor", "pcap", "settings"] = (
        "dashboard"
    )
    service_paths: list[str] = Field(default_factory=list, max_length=128)
    waves: list[WaveSelection] = Field(default_factory=list, max_length=128)


class ProjectDocument(StrictModel):
    format: Literal["someip-agent-project"] = "someip-agent-project"
    format_version: Literal[2] = 2
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    model: ArxmlModel | None = None
    services: dict[str, NativeCatalogRequest] = Field(default_factory=dict, max_length=16)
    listeners: list[ListenerDraft] = Field(default_factory=list, max_length=16)
    simulations: list[SimulationDraft] = Field(default_factory=list, max_length=128)
    cycles: list[CycleDraft] = Field(default_factory=list, max_length=128)
    workspace: WorkspaceSelection = Field(default_factory=WorkspaceSelection)

    @model_validator(mode="before")
    @classmethod
    def migrate(cls, value: Any) -> Any:
        forbidden = {
            "api_key",
            "llm_api_key",
            "private_key",
            "private_key_path",
            "token",
            "password",
            "install_root",
            "data_dir",
            "native_binary",
            "runtime_path",
            "network_send_enabled",
            "allowed_destinations",
        }

        def check(node: Any, depth: int = 0) -> None:
            if depth > 64:
                raise ValueError("工程嵌套超过 64 层")
            if isinstance(node, dict):
                if forbidden.intersection(node):
                    raise ValueError("工程不接受密钥、主机路径或发送授权字段")
                for child in node.values():
                    check(child, depth + 1)
            elif isinstance(node, list):
                for child in node:
                    check(child, depth + 1)

        check(value)
        if (
            isinstance(value, dict)
            and "format_version" in value
            and type(value["format_version"]) is not int
        ):
            raise ValueError("工程格式版本必须是整数")
        # v1 只含工作集，没有周期任务；迁移不改变激励或自动启动任何配置。
        if isinstance(value, dict) and value.get("format_version") == 1:
            value = {**value, "format_version": 2}
            value.setdefault("cycles", [])
        return value

    @model_validator(mode="after")
    def bounded(self) -> ProjectDocument:
        if any(not key or len(key) > 128 for key in self.services):
            raise ValueError("服务草案名称必须为 1–128 字符")
        if any(cycle.service_profile not in self.services for cycle in self.cycles):
            raise ValueError("周期草案必须引用工程内的服务配置")
        if self.model and ("/" in self.model.source_name or "\\" in self.model.source_name):
            raise ValueError("工程模型只保存源文件名，不接受本地文件路径")
        if len(self.model_dump_json().encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError("工程配置超过 8 MiB 上限")
        return self


class ProjectSave(StrictModel):
    document: ProjectDocument
    expected_revision: int | None = Field(default=None, ge=1, strict=True)


class ProjectView(StrictModel):
    id: UUID
    revision: int
    updated_at: datetime
    document: ProjectDocument


class ProjectConflict(ValueError):
    pass


class ProjectNotFound(LookupError):
    pass


class ProjectRepository:
    """SQLite 事务保证并发保存与备份一致；每工程最多保留 20 个旧版本。"""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL, document TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS project_backups (
                id TEXT NOT NULL, revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(id, revision))""")
            db.execute("""CREATE TABLE IF NOT EXISTS project_selection (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _view(row: sqlite3.Row | None) -> ProjectView:
        if row is None:
            raise ProjectNotFound("工程或备份不存在")
        return ProjectView(
            id=row["id"],
            revision=row["revision"],
            updated_at=row["updated_at"],
            document=ProjectDocument.model_validate_json(row["document"]),
        )

    def list(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "revision": row["revision"],
                "updated_at": row["updated_at"],
                "name": json.loads(row["document"])["name"],
            }
            for row in rows
        ]

    def get(self, identifier: UUID) -> ProjectView:
        with closing(self._connect()) as db:
            return self._view(
                db.execute("SELECT * FROM projects WHERE id=?", (str(identifier),)).fetchone()
            )

    def save(self, request: ProjectSave, identifier: UUID | None = None) -> ProjectView:
        identifier = identifier or uuid4()
        updated = datetime.now(timezone.utc)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM projects WHERE id=?", (str(identifier),)
            ).fetchone()
            if previous is None and request.expected_revision is not None:
                raise ProjectNotFound("工程不存在")
            if previous is not None:
                if request.expected_revision != previous["revision"]:
                    raise ProjectConflict("工程已被更新，请重新读取后再保存，不能覆盖他人的配置")
                db.execute("INSERT INTO project_backups VALUES(?,?,?,?)", tuple(previous))
            elif db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 100:
                raise ProjectConflict("工程数量达到 100 个上限")
            revision = previous["revision"] + 1 if previous else 1
            db.execute(
                "INSERT OR REPLACE INTO projects VALUES(?,?,?,?)",
                (
                    str(identifier),
                    revision,
                    updated.isoformat(),
                    request.document.model_dump_json(),
                ),
            )
            db.execute(
                """DELETE FROM project_backups WHERE id=? AND revision NOT IN (
                SELECT revision FROM project_backups WHERE id=? ORDER BY revision DESC LIMIT 20)""",
                (str(identifier), str(identifier)),
            )
        logger.info(
            "工程配置已保存",
            extra={
                "operation": "project.save",
                "project_id": str(identifier),
                "revision": revision,
            },
        )
        return ProjectView(
            id=identifier, revision=revision, updated_at=updated, document=request.document
        )

    def backups(self, identifier: UUID) -> list[dict[str, Any]]:
        self.get(identifier)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT revision,updated_at FROM project_backups WHERE id=? ORDER BY revision DESC",
                (str(identifier),),
            ).fetchall()
        return [dict(row) for row in rows]

    def restore(self, identifier: UUID, revision: int, expected_revision: int) -> ProjectView:
        with closing(self._connect()) as db:
            view = self._view(
                db.execute(
                    "SELECT * FROM project_backups WHERE id=? AND revision=?",
                    (str(identifier), revision),
                ).fetchone()
            )
        return self.save(
            ProjectSave(document=view.document, expected_revision=expected_revision), identifier
        )

    def select(self, identifier: UUID) -> None:
        self.get(identifier)
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO project_selection VALUES(1,?)", (str(identifier),))

    def current(self) -> ProjectView | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT projects.* FROM projects JOIN project_selection USING(id)"
            ).fetchone()
        return self._view(row) if row else None
