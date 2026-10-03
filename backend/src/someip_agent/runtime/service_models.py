"""服务会话控制面的请求、状态与结果，不承担线上编解码。"""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from someip_agent.domain.csv_stimulus import compile_csv_stimulus
from someip_agent.domain.models import GeneratorKind
from someip_agent.domain.timed_states import TimedSignalState, validate_timed_states


class NativeMemberView(BaseModel):
    key: str
    application_name: str | None = None
    application_id: int | None = None
    role: Literal["client", "server"]
    service_path: str
    deployment_path: str | None
    service_id: int
    instance_id: int
    transport: Literal["internal", "udp", "tcp"]
    state: str
    connected: bool
    methods: list[str]
    no_return_methods: list[str]
    events: list[str]
    last_error: str | None = None


class ServiceSessionView(BaseModel):
    id: str
    runtime: Literal["vsomeip"] = "vsomeip"
    model_id: str
    source_sha256: str | None
    application_name: str
    application_id: int
    started_at: datetime
    active: bool
    running: bool
    pid: int | None
    members: list[NativeMemberView]
    last_error: str | None = None


class ServiceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member: str = Field(min_length=1, max_length=512)
    function: str = Field(min_length=1, max_length=256)
    args: Any = Field(default_factory=dict)
    timeout: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)


class ServiceResponse(ServiceCommand):
    request_id: int = Field(ge=0, le=0xFFFFFFFF)
    return_code: int = Field(default=0, ge=0, le=255)
    is_error: bool = False


class EventGeneratorConfig(BaseModel):
    """类型从冻结事件路径推导；不让调用者另造 data_type 或布局。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: GeneratorKind = GeneratorKind.CONSTANT
    initial: StrictInt | StrictFloat | StrictBool | StrictStr = 0
    minimum: StrictInt | StrictFloat = 0
    maximum: StrictInt | StrictFloat = 100
    period_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    sequence: list[StrictInt | StrictFloat | StrictBool | StrictStr] = Field(
        default_factory=list, max_length=8192
    )
    seed: StrictInt = Field(default=0, ge=0, le=18446744073709551615)
    step_at_ms: StrictInt | None = Field(default=None, ge=0, le=18446744073709551615)
    step_value: StrictInt | StrictFloat | StrictBool | StrictStr | None = None
    initial_state: StrictStr | None = Field(default=None, max_length=64)
    states: list[TimedSignalState] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def step_shape(self) -> "EventGeneratorConfig":
        validate_timed_states(self.kind, self.initial_state, self.states)
        if self.kind == GeneratorKind.STEP:
            if self.step_at_ms is None or self.step_value is None:
                raise ValueError("阶跃源必须提供 step_at_ms 与 step_value")
        elif self.step_at_ms is not None or self.step_value is not None:
            raise ValueError("非阶跃源不能包含阶跃参数")
        return self


class EventSignalSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=512)
    generator: EventGeneratorConfig


class ServiceCycleCommand(ServiceCommand):
    interval_ms: int = Field(default=100, ge=1, le=60_000, strict=True)
    sources: list[EventSignalSource] = Field(default_factory=list, max_length=128)
    csv_text: StrictStr | None = Field(default=None, max_length=1048576)

    @model_validator(mode="after")
    def csv_is_portable_and_valid(self) -> "ServiceCycleCommand":
        bindings = compile_csv_stimulus(self.csv_text)
        if len(bindings) + len(self.sources) > 128:
            raise ValueError("CSV 与其他激励合计最多 128 个路径")
        paths = {source.path for source in self.sources}
        if any(binding["path"] in paths for binding in bindings):
            raise ValueError("CSV 与其他激励路径不能重复绑定")
        return self


class ServiceCycleStop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member: str = Field(min_length=1, max_length=512)


class ServiceCycleStatus(BaseModel):
    member: str
    function: str | None = None
    interval_ms: int | None = None
    running: bool
    emitted_count: int = Field(ge=0)
    source_count: int = Field(default=0, ge=0, le=128)
    active_states: dict[str, str] = Field(default_factory=dict)
    logical_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    observation: Literal["native_schedule"] = "native_schedule"
    wire_verified: Literal[False] = False
    synchronized: bool = False


class ServiceSyncCommand(BaseModel):
    """同一原生会话内的多事件公共时钟；不允许任意脚本或隐式停止旧任务。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    events: list[ServiceCycleCommand] = Field(min_length=1, max_length=16)
    paused: StrictBool = True
    speed: StrictInt | StrictFloat = 1

    @model_validator(mode="after")
    def group_shape(self) -> "ServiceSyncCommand":
        if self.speed not in (0.25, 0.5, 1, 2, 4):
            raise ValueError("同步倍率仅支持 0.25、0.5、1、2、4")
        keys = [(event.member, event.function) for event in self.events]
        if len(keys) != len(set(keys)):
            raise ValueError("同步组不能重复绑定同一成员事件")
        if (
            len(json.dumps(self.model_dump(mode="json"), ensure_ascii=True).encode())
            > 4 * 1024 * 1024
        ):
            raise ValueError("同步配置超过 4 MiB 控制消息预算")
        return self


class ServiceSyncControl(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    action: Literal["pause", "resume", "step", "stop", "speed"]
    speed: StrictInt | StrictFloat | None = None

    @model_validator(mode="after")
    def control_shape(self) -> "ServiceSyncControl":
        if self.action == "speed":
            if self.speed not in (0.25, 0.5, 1, 2, 4):
                raise ValueError("同步倍率仅支持 0.25、0.5、1、2、4")
        elif self.speed is not None:
            raise ValueError("只有 speed 操作接受倍率")
        return self


class SyncEventStatus(BaseModel):
    member: str
    function: str
    interval_ms: int = Field(ge=1, le=60000)
    emitted_count: int = Field(ge=0)
    last_logical_ms: int | None = Field(default=None, ge=0, le=18446744073709551615)
    source_count: int = Field(default=0, ge=0, le=128)
    active_states: dict[str, str] = Field(default_factory=dict)


class ServiceSyncStatus(BaseModel):
    active: bool
    paused: bool
    group_id: str | None
    logical_ms: int = Field(ge=0, le=18446744073709551615)
    frame_index: int = Field(ge=0, le=18446744073709551615)
    interval_ms: int | None = Field(default=None, ge=1, le=60000)
    speed: float = Field(allow_inf_nan=False)
    events: list[SyncEventStatus] = Field(default_factory=list, max_length=16)
    last_error: str | None = None
    observation: Literal["native_schedule"] = "native_schedule"
    wire_verified: Literal[False] = False


class ServiceCommandResult(BaseModel):
    status: Literal["responded", "submitted"]
    result: Any = None
    observation: Literal["vsomeip_response", "native_submission"]
    wire_verified: Literal[False] = False


class ServiceIncomingRequest(BaseModel):
    member: str
    function: str
    request_id: int
    args: Any
    payload_hex: str
    received_at: float
    reply_allowed: bool
