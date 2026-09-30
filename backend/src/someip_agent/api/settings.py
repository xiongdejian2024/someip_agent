from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from someip_agent.agent.service import LlmConfigurationError, LlmGatewayError
from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import LlmSettingsUpdate, LlmSettingsView
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])


@router.get("/settings/llm", response_model=LlmSettingsView)
async def get_llm_settings(
    state: ApplicationState = Depends(get_state),
) -> LlmSettingsView:
    return state.llm_configuration.view()


@router.put("/settings/llm", response_model=LlmSettingsView)
async def put_llm_settings(
    update: LlmSettingsUpdate,
    state: ApplicationState = Depends(get_state),
) -> LlmSettingsView:
    try:
        view = state.llm_configuration.update(update)
    except LlmConfigurationError as exc:
        logger.exception("模型配置校验失败", extra={"operation": "llm.settings.update"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    state.audit.add(
        action="llm.settings.update",
        target=view.model,
        detail={"base_url": view.base_url, "api_key_updated": bool(update.api_key)},
    )
    return view


@router.post("/settings/llm/test")
async def test_llm_settings(state: ApplicationState = Depends(get_state)) -> dict[str, str]:
    try:
        reply = await state.agent.test_connection()
        return {"status": "ok", "reply": reply}
    except (LlmConfigurationError, LlmGatewayError) as exc:
        logger.exception("模型连接测试失败", extra={"operation": "llm.settings.test"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
