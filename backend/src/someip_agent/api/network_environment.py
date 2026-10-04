"""主机网卡配置专用入口；加载工程不调用此处，写入必须明确确认。"""

import asyncio
import logging
import subprocess
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from someip_agent.api.dependencies import get_state
from someip_agent.runtime.network_environment import (
    EnvironmentConflict,
    EnvironmentUnavailable,
    NetworkProfile,
)
from someip_agent.runtime.network_gate import NetworkTaskConflict
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/network/environment", tags=["network-environment"])


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: NetworkProfile
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("必须明确 confirm=true，不能使用数值或字符串代替")
        return value


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("必须明确 confirm=true，不能使用数值或字符串代替")
        return value


class BindRequest(ConfirmRequest):
    managed_id: UUID | None


async def operation(state: ApplicationState, action: str, work: Callable[[], Any]) -> Any:
    try:
        result = await asyncio.to_thread(work)
        state.audit.add(action=action, target="network-environment")
        return result
    except Exception as exc:
        logger.exception("网卡环境操作失败", extra={"operation": action})
        state.audit.add(
            action=action,
            target="network-environment",
            success=False,
            detail={"error_type": type(exc).__name__},
        )
        if isinstance(exc, PermissionError):
            raise HTTPException(403, str(exc)) from exc
        if isinstance(exc, (EnvironmentConflict, NetworkTaskConflict)):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, (EnvironmentUnavailable, OSError, subprocess.SubprocessError)):
            raise HTTPException(503, "网络环境工具不可用或权限不足；请检查后端完整日志") from exc
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.get("")
async def status(state: ApplicationState = Depends(get_state)):
    return await operation(state, "network.environment.status", state.network_environment.status)


@router.post("/plan")
async def plan(profile: NetworkProfile, state: ApplicationState = Depends(get_state)):
    return await operation(
        state, "network.environment.plan", lambda: state.network_environment.plan(profile)
    )


@router.post("/apply")
async def apply(request: ApplyRequest, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "network.environment.apply",
        lambda: state.network_environment.apply(request.profile, request.token),
    )


@router.post("/{identifier}/remove")
async def remove(
    identifier: UUID, request: ConfirmRequest, state: ApplicationState = Depends(get_state)
):
    return await operation(
        state, "network.environment.remove", lambda: state.network_environment.remove(identifier)
    )


@router.post("/binding")
async def bind(request: BindRequest, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "network.environment.bind",
        lambda: state.network_environment.bind(request.managed_id),
    )
