from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import UpdateInfo, UpdateInstallationStatus
from someip_agent.state import ApplicationState
from someip_agent.update.service import UpdateError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["updates"])


@router.get("/updates/install/{installation_id}", response_model=UpdateInstallationStatus)
async def installation_status(
    installation_id: str, state: ApplicationState = Depends(get_state)
) -> UpdateInstallationStatus:
    try:
        return await asyncio.to_thread(state.update_service.installation_status, installation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "本次安装状态尚不存在") from exc
    except UpdateError as exc:
        logger.exception("在线升级状态查询失败", extra={"operation": "update.status"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/updates/install")
async def install_update(
    request: Request,
    background_tasks: BackgroundTasks,
    state: ApplicationState = Depends(get_state),
) -> dict[str, str]:
    shutdown = getattr(request.app.state, "request_shutdown", None)
    if shutdown is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "当前启动方式不支持自动重启升级")
    try:
        result = await state.update_service.install()
    except UpdateError as exc:
        logger.exception("在线升级安装失败", extra={"operation": "update.install"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    state.audit.add(action="update.install", target=result["version"])
    background_tasks.add_task(shutdown)
    return result


@router.get("/updates/check", response_model=UpdateInfo)
async def check_update(state: ApplicationState = Depends(get_state)) -> UpdateInfo:
    try:
        return await state.update_service.check()
    except UpdateError as exc:
        logger.exception("在线升级检查失败", extra={"operation": "update.check"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/updates/stage")
async def stage_update(state: ApplicationState = Depends(get_state)) -> dict[str, str]:
    try:
        path = await state.update_service.stage()
    except UpdateError as exc:
        logger.exception("在线升级暂存失败", extra={"operation": "update.stage"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    state.audit.add(action="update.stage", target=path.name, detail={"path": str(path)})
    return {"status": "staged", "path": str(path)}
