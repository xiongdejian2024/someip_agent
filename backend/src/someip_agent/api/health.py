from fastapi import APIRouter, Depends

from someip_agent.api.dependencies import get_state
from someip_agent.state import ApplicationState
from someip_agent.version import __version__

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(state: ApplicationState = Depends(get_state)) -> dict[str, object]:
    model = await state.get_arxml_model()
    monitor = await state.monitor.summary()
    return {
        "status": "ok",
        "version": __version__,
        "arxml_loaded": model is not None,
        "service_count": len(model.services) if model else 0,
        "monitor_count": monitor["total"],
        "active_simulations": sum(1 for item in state.simulator.list() if item.running),
        "active_listeners": sum(1 for item in state.network.list() if item.running),
        "llm_configured": state.llm_configuration.view().api_key_configured,
    }
