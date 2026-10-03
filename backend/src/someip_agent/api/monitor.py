from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import MonitorMessage
from someip_agent.state import ApplicationState

router = APIRouter(tags=["monitor"])


@router.get("/monitor/messages", response_model=list[MonitorMessage])
async def messages(
    limit: int = Query(default=500, ge=1, le=5000),
    service_id: int | None = Query(default=None, ge=0, le=0xFFFF),
    method_id: int | None = Query(default=None, ge=0, le=0xFFFF),
    is_sd: bool | None = None,
    state: ApplicationState = Depends(get_state),
) -> list[MonitorMessage]:
    return await state.monitor.list(
        limit=limit,
        service_id=service_id,
        method_id=method_id,
        is_sd=is_sd,
    )


@router.get("/monitor/summary")
async def summary(state: ApplicationState = Depends(get_state)) -> dict[str, object]:
    return await state.monitor.summary()


@router.delete("/monitor/messages", status_code=204)
async def clear(state: ApplicationState = Depends(get_state)) -> None:
    await state.monitor.clear()
    state.audit.add(action="monitor.clear", target="message-buffer")


@router.websocket("/monitor/ws")
async def monitor_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    state: ApplicationState = websocket.app.state.container
    queue = await state.monitor.subscribe()
    try:
        initial = await state.monitor.list(limit=500)
        await websocket.send_json(
            {
                "type": "snapshot",
                "messages": [item.model_dump(mode="json") for item in initial],
                "stream_counters": await state.monitor.statistics(queue),
            }
        )
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=20)
                await websocket.send_json(
                    {
                        "type": "message",
                        "message": message.model_dump(mode="json"),
                        "stream_counters": await state.monitor.statistics(queue),
                    }
                )
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {"type": "heartbeat", "stream_counters": await state.monitor.statistics(queue)}
                )
    except WebSocketDisconnect:
        pass
    finally:
        await state.monitor.unsubscribe(queue)
