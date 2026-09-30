from fastapi import APIRouter, Depends, Query

from someip_agent.api.dependencies import get_state
from someip_agent.state import ApplicationState

router = APIRouter(tags=["audit"])


@router.get("/audit/events")
async def audit_events(
    limit: int = Query(default=200, ge=1, le=2000),
    state: ApplicationState = Depends(get_state),
) -> list[dict[str, object]]:
    return state.audit.list(limit)
