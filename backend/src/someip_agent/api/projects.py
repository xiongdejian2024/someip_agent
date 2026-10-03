"""只保存和恢复工程配置，不将导入的配置当作自动执行脚本。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field

from someip_agent.api.dependencies import get_state
from someip_agent.state import ApplicationState
from someip_agent.workbench.projects import (
    ProjectConflict,
    ProjectDocument,
    ProjectNotFound,
    ProjectSave,
    ProjectView,
    StrictModel,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])
T = TypeVar("T")


class RestoreRequest(StrictModel):
    revision: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=1, strict=True)


async def operation(
    state: ApplicationState, action: str, target: str, work: Callable[[], Awaitable[T]]
) -> T:
    try:
        value = await work()
        state.audit.add(action=action, target=target)
        logger.info("工程操作已完成", extra={"operation": action, "project_id": target})
        return value
    except Exception as exc:
        logger.exception("工程操作失败", extra={"operation": action, "project_id": target})
        state.audit.add(
            action=action, target=target, success=False, detail={"error_type": type(exc).__name__}
        )
        if isinstance(exc, ProjectNotFound):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, ProjectConflict):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.get("")
async def list_projects(state: ApplicationState = Depends(get_state)):
    return await asyncio.to_thread(state.projects.list)


@router.get("/current", response_model=ProjectView | None)
async def current_project(state: ApplicationState = Depends(get_state)):
    return await asyncio.to_thread(state.projects.current)


@router.post("", response_model=ProjectView)
async def create(request: ProjectSave, state: ApplicationState = Depends(get_state)):
    return await operation(
        state, "project.create", "new", lambda: asyncio.to_thread(state.projects.save, request)
    )


@router.post("/import", response_model=ProjectView)
async def import_project(document: ProjectDocument, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "project.import",
        "new",
        lambda: asyncio.to_thread(state.projects.save, ProjectSave(document=document)),
    )


@router.get("/{identifier}", response_model=ProjectView)
async def get_project(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "project.read",
        str(identifier),
        lambda: asyncio.to_thread(state.projects.get, identifier),
    )


@router.put("/{identifier}", response_model=ProjectView)
async def save_project(
    identifier: UUID, request: ProjectSave, state: ApplicationState = Depends(get_state)
):
    return await operation(
        state,
        "project.save",
        str(identifier),
        lambda: asyncio.to_thread(state.projects.save, request, identifier),
    )


@router.get("/{identifier}/export")
async def export(identifier: UUID, state: ApplicationState = Depends(get_state)):
    view = await operation(
        state,
        "project.export",
        str(identifier),
        lambda: asyncio.to_thread(state.projects.get, identifier),
    )
    return Response(
        view.document.model_dump_json(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="project-{identifier}.json"'},
    )


@router.get("/{identifier}/backups")
async def backups(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(
        state,
        "project.backups",
        str(identifier),
        lambda: asyncio.to_thread(state.projects.backups, identifier),
    )


@router.post("/{identifier}/restore", response_model=ProjectView)
async def restore(
    identifier: UUID, request: RestoreRequest, state: ApplicationState = Depends(get_state)
):
    return await operation(
        state,
        "project.restore",
        str(identifier),
        lambda: asyncio.to_thread(
            state.projects.restore, identifier, request.revision, request.expected_revision
        ),
    )


@router.post("/{identifier}/open", response_model=ProjectView)
async def open_project(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(
        state, "project.open", str(identifier), lambda: state.open_project(identifier)
    )
