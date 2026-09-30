from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import ListenerConfig, ListenerStatus
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["network"])


class StopListenerRequest(BaseModel):
    listener_id: str | None = None


@router.get("/network/listeners", response_model=list[ListenerStatus])
async def list_listeners(
    state: ApplicationState = Depends(get_state),
) -> list[ListenerStatus]:
    return state.network.list()


@router.post("/network/listeners/start", response_model=ListenerStatus, status_code=201)
async def start_listener(
    config: ListenerConfig,
    state: ApplicationState = Depends(get_state),
) -> ListenerStatus:
    try:
        listener = await state.network.start(config)
    except (OSError, ValueError) as exc:
        logger.exception("网络监听启动失败", extra={"operation": "network.listener.start"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    state.audit.add(
        action="network.listener.start",
        target=listener.id,
        detail=config.model_dump(mode="json"),
    )
    return listener


@router.post("/network/listeners/stop", response_model=list[ListenerStatus])
async def stop_listener(
    request: StopListenerRequest | None = None,
    state: ApplicationState = Depends(get_state),
) -> list[ListenerStatus]:
    listener_id = request.listener_id if request else None
    listeners = await state.network.stop(listener_id)
    state.audit.add(
        action="network.listener.stop",
        target=listener_id or "all",
        detail={"count": len(listeners)},
    )
    return listeners
