from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import UpdateInfo
from someip_agent.state import ApplicationState
from someip_agent.update.service import UpdateError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["updates"])


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
