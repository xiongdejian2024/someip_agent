"""连续记录与只读分页离线回放接口，没有网络重放入口。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from someip_agent.api.dependencies import get_state
from someip_agent.state import ApplicationState
from someip_agent.workbench.recordings import (
    RecordingConflict,
    RecordingNotFound,
    RecordingRequest,
    RecordingView,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recordings", tags=["recordings"])
_exports = asyncio.Semaphore(2)
T = TypeVar("T")


async def operation(
    state: ApplicationState,
    action: str,
    target: str,
    work: Callable[[], Awaitable[T]],
    *,
    audit: bool = False,
) -> T:
    try:
        value = await work()
        if audit:
            state.audit.add(action=action, target=target)
        return value
    except Exception as exc:
        logger.exception(
            "记录或只读回放操作失败", extra={"operation": action, "recording_id": target}
        )
        if audit:
            state.audit.add(
                action=action,
                target=target,
                success=False,
                detail={"error_type": type(exc).__name__},
            )
        if isinstance(exc, RecordingNotFound):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, RecordingConflict):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, (ValueError, OSError)):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.get("", response_model=list[RecordingView])
async def list_recordings(state: ApplicationState = Depends(get_state)):
    return await asyncio.to_thread(state.recordings.list)


@router.post("", response_model=RecordingView)
async def start(request: RecordingRequest, state: ApplicationState = Depends(get_state)):
    return await operation(
        state, "recording.start", "new", lambda: state.recordings.start(request), audit=True
    )


@router.get("/{identifier}", response_model=RecordingView)
async def get_recording(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "recording.read",
        str(identifier),
        lambda: asyncio.to_thread(state.recordings.get, identifier),
    )


@router.post("/{identifier}/stop", response_model=RecordingView)
async def stop(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "recording.stop",
        str(identifier),
        lambda: state.recordings.stop(identifier),
        audit=True,
    )


@router.get("/{identifier}/segments/{number}")
async def segment(identifier: UUID, number: int, state: ApplicationState = Depends(get_state)):
    body = await operation(
        state,
        "recording.export",
        str(identifier),
        lambda: asyncio.to_thread(state.recordings.segment, identifier, number),
        audit=True,
    )
    return Response(
        body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{identifier}-{number:05d}.jsonl"'},
    )


async def finish_export(path) -> None:
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception:
        logger.exception(
            "清理临时记录导出文件失败", extra={"operation": "recording.export.cleanup"}
        )
    finally:
        _exports.release()


@router.get("/{identifier}/export")
async def export(identifier: UUID, state: ApplicationState = Depends(get_state)):
    await _exports.acquire()
    job = asyncio.create_task(
        operation(
            state,
            "recording.export",
            str(identifier),
            lambda: asyncio.to_thread(state.recordings.export, identifier),
            audit=True,
        )
    )
    try:
        path = await asyncio.shield(job)
    except asyncio.CancelledError:
        logger.exception(
            "记录导出请求取消，等待文件生成后清理", extra={"operation": "recording.export.cancel"}
        )
        try:
            path = await job
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except Exception:
            logger.exception(
                "取消导出后的清理失败", extra={"operation": "recording.export.cancel.cleanup"}
            )
        finally:
            _exports.release()
        raise
    except BaseException:
        _exports.release()
        raise
    return FileResponse(
        path,
        filename=f"recording-{identifier}.zip",
        media_type="application/zip",
        background=BackgroundTask(finish_export, path),
    )


@router.get("/{identifier}/frames")
async def frames(
    identifier: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    service_id: int | None = Query(default=None, ge=0, le=65535),
    method_id: int | None = Query(default=None, ge=0, le=65535),
    direction: Literal["rx", "tx", "sim", "pcap"] | None = None,
    start_ms: float = Query(default=0, ge=0, allow_inf_nan=False),
    state: ApplicationState = Depends(get_state),
):
    return await operation(
        state,
        "recording.replay.read",
        str(identifier),
        lambda: asyncio.to_thread(
            state.recordings.frames,
            identifier,
            offset=offset,
            limit=limit,
            service_id=service_id,
            method_id=method_id,
            direction=direction,
            start_ms=start_ms,
        ),
    )
