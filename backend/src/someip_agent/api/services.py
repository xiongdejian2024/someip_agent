"""模型绑定的原生服务控制 API；启停与调用均通过 SAT socket 适配器。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException

from someip_agent.api.dependencies import get_state
from someip_agent.runtime.native_config import SimulationPermissionError
from someip_agent.runtime.network_gate import NetworkTaskConflict
from someip_agent.runtime.service_models import (
    ServiceCommand,
    ServiceCommandResult,
    ServiceCycleCommand,
    ServiceCycleStatus,
    ServiceCycleStop,
    ServiceIncomingRequest,
    ServiceResponse,
    ServiceSessionView,
    ServiceSyncCommand,
    ServiceSyncControl,
    ServiceSyncStatus,
)
from someip_agent.runtime.services import ServiceSessionConflict, ServiceSessionNotFound
from someip_agent.soa.catalog import NativeCatalogRequest
from someip_agent.soa.operator import NativeOperationError, NativeRuntimeError
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/services/sessions", tags=["native-services"])
T = TypeVar("T")


async def _operation(
    state: ApplicationState, action: str, target: str, callback: Callable[[], Awaitable[T]]
) -> T:
    try:
        result = await callback()
        state.audit.add(action=action, target=target, detail={"runtime": "vsomeip"})
        logger.info("原生服务操作已完成", extra={"operation": action, "session_id": target})
        return result
    except Exception as exc:
        logger.exception("原生服务操作失败", extra={"operation": action, "session_id": target})
        state.audit.add(
            action=action,
            target=target,
            success=False,
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        if isinstance(exc, SimulationPermissionError):
            code = 403
        elif isinstance(exc, ServiceSessionNotFound):
            code = 404
        elif isinstance(exc, (ServiceSessionConflict, NetworkTaskConflict)):
            code = 409
        elif isinstance(exc, (NativeOperationError, ValueError)):
            code = 422
        elif isinstance(exc, TimeoutError):
            code = 504
        elif isinstance(exc, (NativeRuntimeError, OSError)):
            code = 503
        else:
            raise
        raise HTTPException(code, str(exc)) from exc


@router.get("", response_model=list[ServiceSessionView])
async def sessions(state: ApplicationState = Depends(get_state)) -> list[ServiceSessionView]:
    return state.services.statuses()


@router.post("", response_model=ServiceSessionView)
async def start(
    request: NativeCatalogRequest, state: ApplicationState = Depends(get_state)
) -> ServiceSessionView:
    model = await state.get_arxml_model()
    if model is None:
        raise HTTPException(409, "请先导入 ARXML 服务模型")
    return await _operation(
        state,
        "services.start",
        request.application_name,
        lambda: state.services.start(model, request),
    )


@router.post("/{identifier}/stop", response_model=ServiceSessionView)
async def stop(identifier: str, state: ApplicationState = Depends(get_state)) -> ServiceSessionView:
    return await _operation(
        state, "services.stop", identifier, lambda: state.services.stop(identifier)
    )


@router.post("/{identifier}/call", response_model=ServiceCommandResult)
async def call(
    identifier: str, command: ServiceCommand, state: ApplicationState = Depends(get_state)
) -> ServiceCommandResult:
    return await _operation(
        state, "services.call", identifier, lambda: state.services.call(identifier, command)
    )


@router.post("/{identifier}/notify", response_model=ServiceCommandResult)
async def notify(
    identifier: str, command: ServiceCommand, state: ApplicationState = Depends(get_state)
) -> ServiceCommandResult:
    return await _operation(
        state, "services.notify", identifier, lambda: state.services.notify(identifier, command)
    )


@router.get("/{identifier}/cycles", response_model=list[ServiceCycleStatus])
async def cycles(
    identifier: str, state: ApplicationState = Depends(get_state)
) -> list[ServiceCycleStatus]:
    # 状态轮询复用错误映射；不把查询变成写操作。
    try:
        return await state.services.cycles(identifier)
    except Exception as exc:
        logger.exception("读取周期状态失败", extra={"operation": "services.cycles"})
        if isinstance(exc, ServiceSessionNotFound):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, (NativeRuntimeError, OSError)):
            raise HTTPException(503, str(exc)) from exc
        raise


@router.post("/{identifier}/cycles/start", response_model=ServiceCycleStatus)
async def start_cycle(
    identifier: str, command: ServiceCycleCommand, state: ApplicationState = Depends(get_state)
) -> ServiceCycleStatus:
    return await _operation(
        state,
        "services.cycle.start",
        identifier,
        lambda: state.services.configure_cycle(identifier, command),
    )


@router.post("/{identifier}/cycles/update", response_model=ServiceCycleStatus)
async def update_cycle(
    identifier: str, command: ServiceCycleCommand, state: ApplicationState = Depends(get_state)
) -> ServiceCycleStatus:
    return await _operation(
        state,
        "services.cycle.update",
        identifier,
        lambda: state.services.configure_cycle(identifier, command, update=True),
    )


@router.post("/{identifier}/cycles/stop", response_model=ServiceCycleStatus)
async def stop_cycle(
    identifier: str, command: ServiceCycleStop, state: ApplicationState = Depends(get_state)
) -> ServiceCycleStatus:
    return await _operation(
        state,
        "services.cycle.stop",
        identifier,
        lambda: state.services.stop_cycle(identifier, command),
    )


@router.get("/{identifier}/sync", response_model=ServiceSyncStatus)
async def sync_status(
    identifier: str, state: ApplicationState = Depends(get_state)
) -> ServiceSyncStatus:
    try:
        return await state.services.sync_status(identifier)
    except Exception as exc:
        logger.exception("读取同步状态失败", extra={"operation": "services.sync.status"})
        if isinstance(exc, ServiceSessionNotFound):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, (NativeRuntimeError, OSError)):
            raise HTTPException(503, str(exc)) from exc
        raise


@router.post("/{identifier}/sync/start", response_model=ServiceSyncStatus)
async def start_sync(
    identifier: str, command: ServiceSyncCommand, state: ApplicationState = Depends(get_state)
) -> ServiceSyncStatus:
    return await _operation(
        state,
        "services.sync.start",
        identifier,
        lambda: state.services.start_sync(identifier, command),
    )


@router.post("/{identifier}/sync/control", response_model=ServiceSyncStatus)
async def control_sync(
    identifier: str, command: ServiceSyncControl, state: ApplicationState = Depends(get_state)
) -> ServiceSyncStatus:
    return await _operation(
        state,
        "services.sync." + command.action,
        identifier,
        lambda: state.services.control_sync(identifier, command),
    )


@router.get("/{identifier}/requests", response_model=list[ServiceIncomingRequest])
async def requests(
    identifier: str, state: ApplicationState = Depends(get_state)
) -> list[ServiceIncomingRequest]:
    async def snapshot() -> list[ServiceIncomingRequest]:
        return state.services.requests(identifier)

    # 状态轮询不写审计记录；异常仍保留堆栈和明确的 HTTP 状态。
    try:
        return await snapshot()
    except ServiceSessionNotFound as exc:
        logger.exception("请求快照会话不存在", extra={"operation": "services.requests"})
        raise HTTPException(404, str(exc)) from exc
    except NativeRuntimeError as exc:
        logger.exception("请求快照原生运行时不可用", extra={"operation": "services.requests"})
        raise HTTPException(503, str(exc)) from exc


@router.post("/{identifier}/respond", response_model=ServiceCommandResult)
async def respond(
    identifier: str, command: ServiceResponse, state: ApplicationState = Depends(get_state)
) -> ServiceCommandResult:
    return await _operation(
        state, "services.respond", identifier, lambda: state.services.respond(identifier, command)
    )
