"""声明式产品场景，不执行动态代码、命令行或任意 URL。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, JsonValue, StrictInt, field_validator, model_validator

from .projects import StrictModel, check_portable_data


class MessageMatch(StrictModel):
    service_id: int | None = Field(default=None, ge=0, le=65535)
    method_id: int | None = Field(default=None, ge=0, le=65535)
    member: str | None = Field(default=None, max_length=512)
    direction: Literal["rx", "tx", "sim", "pcap"] | None = None
    payload_hex: str | None = Field(
        default=None, max_length=131072, pattern=r"^(?:[0-9a-fA-F]{2})*$"
    )


class ScenarioStep(StrictModel):
    kind: Literal[
        "start_service",
        "stop_service",
        "start_listener",
        "stop_listener",
        "wait_ready",
        "call",
        "notify",
        "respond_next",
        "cycle_start",
        "cycle_stop",
        "wait_message",
        "delay",
        "assert",
        "parallel",
    ]
    name: str = Field(default="", max_length=128)
    profile: str | None = Field(default=None, min_length=1, max_length=128)
    session: str | None = Field(default=None, min_length=1, max_length=128)
    listener: str | None = Field(default=None, min_length=1, max_length=128)
    command: dict[str, JsonValue] | None = None
    match: MessageMatch = Field(default_factory=MessageMatch)
    save_as: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    timeout: float = Field(default=5, gt=0, le=30)
    seconds: float = Field(default=0, ge=0, le=30)
    path: list[str | StrictInt] = Field(default_factory=list, max_length=32)
    comparison: Literal["eq", "approx", "exists"] = "eq"
    expected: JsonValue = None
    tolerance: float = Field(default=0.000001, gt=0, le=1e9)
    children: list[ScenarioStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_step(self) -> ScenarioStep:
        common = {"kind", "name"}
        fields = {
            "start_service": {"profile"},
            "stop_service": {"session"},
            "start_listener": {"profile"},
            "stop_listener": {"listener"},
            "wait_ready": {"session", "timeout"},
            "call": {"session", "command", "save_as"},
            "notify": {"session", "command", "save_as"},
            "respond_next": {"session", "command", "timeout", "save_as"},
            "cycle_start": {"session", "command", "save_as"},
            "cycle_stop": {"session", "command", "save_as"},
            "wait_message": {"session", "listener", "match", "save_as", "timeout"},
            "delay": {"seconds"},
            "assert": {"path", "comparison", "expected", "tolerance"},
            "parallel": {"children"},
        }[self.kind]
        if self.model_fields_set - common - fields:
            raise ValueError("场景步骤包含不属于该操作的字段")
        if self.kind.startswith("start_") and self.profile is None:
            raise ValueError("启动步骤必须引用工程配置名称")
        if (
            self.kind
            in {
                "stop_service",
                "wait_ready",
                "call",
                "notify",
                "respond_next",
                "cycle_start",
                "cycle_stop",
            }
            and self.session is None
        ):
            raise ValueError("服务操作必须引用本场景创建的会话")
        if self.kind == "stop_listener" and self.listener is None:
            raise ValueError("停止监听必须引用本场景创建的监听器")
        if (
            self.kind in {"call", "notify", "respond_next", "cycle_start", "cycle_stop"}
            and self.command is None
        ):
            raise ValueError("接口操作缺少 command")
        if self.kind == "wait_message" and (self.session is None) == (self.listener is None):
            raise ValueError("等待报文必须明确选择本场景的一个会话或监听器")
        if self.kind == "assert" and (not self.path or type(self.path[0]) is not str):
            raise ValueError("断言 path 必须从已保存的结果名称开始")
        if any(type(part) is int and part < 0 for part in self.path):
            raise ValueError("断言数组下标不能为负")
        if self.kind == "parallel" and (
            not self.children
            or any(
                child.kind
                in {"parallel", "start_service", "start_listener", "stop_service", "stop_listener"}
                for child in self.children
            )
        ):
            raise ValueError("并行组仅允许 1–8 个非生命周期步骤，不能嵌套并行组")
        return self


def default_cases() -> list[dict[str, JsonValue]]:
    return [{}]


class ScenarioDefinition(StrictModel):
    format: Literal["someip-agent-scenario"] = "someip-agent-scenario"
    format_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=128)
    model_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cases: list[dict[str, JsonValue]] = Field(
        default_factory=default_cases, min_length=1, max_length=20
    )
    steps: list[ScenarioStep] = Field(min_length=1, max_length=200)
    cleanup: list[ScenarioStep] = Field(default_factory=list, max_length=32)
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    stop_on_failure: bool = False

    @model_validator(mode="before")
    @classmethod
    def portable(cls, value: Any) -> Any:
        check_portable_data(value)
        return value

    @field_validator("format_version", mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("场景格式版本必须为整数 1")
        return value

    @model_validator(mode="after")
    def bounded(self) -> ScenarioDefinition:
        flattened = [
            child
            for step in self.steps
            for child in (step.children if step.kind == "parallel" else [step])
        ]
        names = [step.save_as for step in flattened if step.save_as]
        if len(names) != len(set(names)):
            raise ValueError("同一参数用例中的 save_as 不能重复")
        if len(flattened) > 200:
            raise ValueError("包括并行子步骤在内最多 200 个步骤")
        if any(step.kind not in {"call", "notify", "cycle_stop", "delay"} for step in self.cleanup):
            raise ValueError("清理步骤只允许调用、通知、停止周期或等待；资源统一由执行器释放")
        if len(self.model_dump_json().encode()) > 2 * 1024 * 1024:
            raise ValueError("场景定义超过 2 MiB 上限")
        return self


class ScenarioRunRequest(StrictModel):
    project_id: UUID
    definition: ScenarioDefinition


class StepResult(StrictModel):
    case: int
    index: str
    name: str
    kind: str
    status: Literal["passed", "failed"]
    duration_ms: float
    result: Any = None
    error: str | None = None


class RunView(StrictModel):
    id: UUID
    request_id: str
    name: str
    project_id: UUID
    project_revision: int
    definition_sha256: str
    model_source_sha256: str | None
    application_version: str
    runtime_identity: dict[str, str] = Field(default_factory=dict)
    status: Literal["running", "passed", "failed", "cancelled", "interrupted"] = "running"
    started_at: datetime
    finished_at: datetime | None = None
    recording_id: UUID | None = None
    steps: list[StepResult] = Field(default_factory=list)
    cleanup_errors: list[str] = Field(default_factory=list)
    error: str | None = None
    cleanup_complete: bool = False


class RunSummary(StrictModel):
    id: UUID
    name: str
    project_id: UUID
    project_revision: int
    status: Literal["running", "passed", "failed", "cancelled", "interrupted"]
    started_at: datetime
    finished_at: datetime | None
    recording_id: UUID | None
    request_id: str
    cleanup_complete: bool
    step_count: int


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def parameters(value: Any, case: dict[str, JsonValue]) -> Any:
    """只替换完整 {$param:name} 节点，不解析表达式或做字符串求值。"""
    if isinstance(value, dict):
        if set(value) == {"$param"}:
            name = value["$param"]
            if not isinstance(name, str) or name not in case:
                raise ValueError("场景参数不存在")
            return case[name]
        return {key: parameters(child, case) for key, child in value.items()}
    if isinstance(value, list):
        return [parameters(child, case) for child in value]
    return value
