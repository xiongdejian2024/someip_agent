from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import SimulationConfig, SimulationStatus
from someip_agent.runtime.simulator import SimulationPermissionError
from someip_agent.soa.operator import NativeRuntimeError
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["simulation"])


class StopSimulationRequest(BaseModel):
    simulation_id: str | None = None


@router.get("/simulation", response_model=list[SimulationStatus])
async def list_simulations(
    state: ApplicationState = Depends(get_state),
) -> list[SimulationStatus]:
    return state.simulator.list()


@router.post("/simulation/start", response_model=SimulationStatus, status_code=201)
async def start_simulation(
    config: SimulationConfig,
    state: ApplicationState = Depends(get_state),
) -> SimulationStatus:
    try:
        result = await state.simulator.start(config)
    except SimulationPermissionError as exc:
        logger.exception("仿真启动被安全策略拒绝", extra={"operation": "simulation.start"})
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except NativeRuntimeError as exc:
        logger.exception("原生仿真运行时不可用", extra={"operation": "simulation.start"})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        logger.exception("原生仿真配置非法", extra={"operation": "simulation.start"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    state.audit.add(
        action="simulation.start",
        target=result.id,
        detail=config.model_dump(mode="json"),
    )
    return result


@router.post("/simulation/stop", response_model=list[SimulationStatus])
async def stop_simulation(
    request: StopSimulationRequest | None = None,
    state: ApplicationState = Depends(get_state),
) -> list[SimulationStatus]:
    simulation_id = request.simulation_id if request else None
    stopped = await state.simulator.stop(simulation_id)
    state.audit.add(
        action="simulation.stop",
        target=simulation_id or "all",
        detail={"count": len(stopped)},
    )
    return stopped
