"""外部程序与 Pi 共享的受控控制台 API；OpenAPI 提供完整参数定义。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from someip_agent.api.dependencies import get_state
from someip_agent.state import ApplicationState

router = APIRouter(prefix="/console", tags=["console"])


class ConsoleExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)
    allow_mutation: bool = False

    @model_validator(mode="after")
    def bounded(self) -> "ConsoleExecution":
        import json

        if len(json.dumps(self.arguments, ensure_ascii=False).encode()) > 64 * 1024:
            raise ValueError("控制台参数超过 64 KiB 上限")
        return self


@router.get("/actions")
async def actions(state: ApplicationState = Depends(get_state)) -> dict[str, Any]:
    return {"runtime": state.agent.runtime_view(), "actions": state.agent.console_tools()}


@router.get("/commands")
async def commands(
    after_id: int | None = Query(default=None, ge=0),
    state: ApplicationState = Depends(get_state),
) -> dict[str, Any]:
    return state.console.events(after_id)


@router.post("/actions/{name}")
async def execute(
    name: str,
    request: ConsoleExecution,
    state: ApplicationState = Depends(get_state),
) -> dict[str, Any]:
    tool = next((item for item in state.agent.console_tools() if item["name"] == name), None)
    result = await state.agent.execute_console(name, request.arguments, request.allow_mutation)
    if tool is None:
        raise HTTPException(404, result["error"])
    if tool["mutating"] and not request.allow_mutation:
        raise HTTPException(403, result["error"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(422, result["error"])
    return {"action": name, "result": result}
