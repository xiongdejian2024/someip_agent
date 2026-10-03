"""产品自动场景入口；只执行声明式操作，复用工程及原生发送门禁。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from someip_agent.api.dependencies import get_state
from someip_agent.state import ApplicationState
from someip_agent.workbench.projects import ProjectNotFound
from someip_agent.workbench.results import ResultConflict
from someip_agent.workbench.run_repository import RunNotFound
from someip_agent.workbench.scenario_models import (
    RunSummary,
    RunView,
    ScenarioDefinition,
    ScenarioRunRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scenarios/runs", tags=["scenarios"])
T = TypeVar("T")


async def operation(work: Callable[[], Awaitable[T]]) -> T:
    try:
        return await work()
    except Exception as exc:
        logger.exception("产品场景接口失败", extra={"operation": "scenario.api"})
        if isinstance(exc, (RunNotFound, ProjectNotFound)):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, ResultConflict):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.post("/validate", response_model=ScenarioDefinition, response_model_exclude_unset=True)
async def validate(definition: ScenarioDefinition) -> ScenarioDefinition:
    return definition


@router.post("", response_model=RunView, status_code=202)
async def start(
    body: ScenarioRunRequest, request: Request, state: ApplicationState = Depends(get_state)
):
    return await operation(lambda: state.scenarios.start(body, request.state.request_id))


@router.get("", response_model=list[RunSummary])
async def list_runs(
    limit: int = Query(default=100, ge=1, le=500), state: ApplicationState = Depends(get_state)
):
    return await operation(lambda: asyncio.to_thread(state.runs.list, limit))


@router.get("/{identifier}", response_model=RunView)
async def get(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(lambda: state.scenarios.get(identifier))


@router.post("/{identifier}/cancel", response_model=RunView)
async def cancel(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(lambda: state.scenarios.cancel(identifier))


@router.get("/{identifier}/junit")
async def junit(identifier: UUID, state: ApplicationState = Depends(get_state)):
    body = await operation(lambda: asyncio.to_thread(state.runs.junit, identifier))
    return Response(
        body,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="scenario-{identifier}.xml"',
        },
    )


@router.get("/{identifier}/report", response_class=HTMLResponse)
async def report(identifier: UUID, state: ApplicationState = Depends(get_state)):
    body = await operation(lambda: asyncio.to_thread(state.runs.report, identifier))
    return HTMLResponse(body, headers={"Content-Security-Policy": "default-src 'none'"})


@router.get("/{identifier}/inputs")
async def inputs(identifier: UUID, state: ApplicationState = Depends(get_state)):
    return await operation(lambda: asyncio.to_thread(state.runs.inputs, identifier))


@router.get("/{identifier}/compare/{baseline}")
async def compare(identifier: UUID, baseline: UUID, state: ApplicationState = Depends(get_state)):
    result = await operation(lambda: asyncio.to_thread(state.results.compare, identifier, baseline))
    state.audit.add(
        action="scenario.compare", target=str(identifier), detail={"baseline_id": str(baseline)}
    )
    return result


@router.get("/{identifier}/evidence")
async def evidence(identifier: UUID, state: ApplicationState = Depends(get_state)):
    from .artifact_files import export_file

    state.audit.add(
        action="scenario.evidence.request", target=str(identifier), detail={"phase": "requested"}
    )
    return await operation(
        lambda: export_file(
            lambda: state.results.export(identifier), f"scenario-evidence-{identifier}.zip"
        )
    )
