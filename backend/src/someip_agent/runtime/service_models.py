"""服务会话控制面的请求、状态与结果，不承担线上编解码。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
