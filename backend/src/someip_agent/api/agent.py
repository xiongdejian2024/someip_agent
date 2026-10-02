from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from someip_agent.agent.service import LlmConfigurationError, LlmGatewayError
from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import AgentChatRequest, AgentChatResponse
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent"])


@router.post("/agent/chat/stream")
async def agent_chat_stream(
    request: AgentChatRequest,
    http_request: Request,
    state: ApplicationState = Depends(get_state),
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        stream = state.agent.chat_stream(request)
        pending: asyncio.Task | None = None
        try:
            while True:
                pending = asyncio.create_task(anext(stream))
                # 即使网关不再发送事件，仍监听 HTTP 断连；不能只在下一帧时清理 Pi。
                while not pending.done():
                    await asyncio.wait({pending}, timeout=0.2)
                    if await http_request.is_disconnected():
                        logger.info(
                            "客户端断连，取消 Pi 会话", extra={"operation": "agent.pi.disconnect"}
                        )
                        return
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                if event["event"] == "done":
                    state.audit.add(
                        action="agent.chat.stream",
                        target=event["data"]["model"],
                        detail={
                            "allow_mutation": request.allow_mutation,
                            "tools": [trace["tool"] for trace in event["data"]["traces"]],
                            "degraded": event["data"]["degraded"],
                        },
                    )
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event['event']}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            logger.info("智能体流式请求已取消", extra={"operation": "agent.chat.stream.cancel"})
            raise
        except Exception as exc:
            logger.exception("智能体流式调用失败", extra={"operation": "agent.chat.stream"})
            detail = (
                str(exc)
                if isinstance(exc, (LlmConfigurationError, LlmGatewayError))
                else ("智能体处理失败，已收到的内容已保留，请检查后端日志后重试")
            )
            yield f"event: error\ndata: {json.dumps({'message': detail}, ensure_ascii=False)}\n\n"
            yield 'event: done\ndata: {"status":"error"}\n\n'
        finally:
            # Starlette 的取消作用域会在每次 await 再次取消；保护有界的进程清理，
            # 否则 SIGTERM 已发出，但 wait/kill 和运行时会话登记可能无法完成。
            with anyio.CancelScope(shield=True):
                if pending is not None:
                    pending.cancel()
                    results = await asyncio.gather(pending, return_exceptions=True)
                    for result in results:
                        if isinstance(result, Exception) and not isinstance(
                            result, StopAsyncIteration
                        ):
                            logger.error(
                                "取消智能体事件读取时异常",
                                exc_info=(type(result), result, result.__traceback__),
                            )
                await stream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/chat", response_model=AgentChatResponse)
async def agent_chat(
    request: AgentChatRequest,
    state: ApplicationState = Depends(get_state),
) -> AgentChatResponse:
    try:
        response = await state.agent.chat(request)
        state.audit.add(
            action="agent.chat",
            target=response.model,
            detail={
                "allow_mutation": request.allow_mutation,
                "tools": [trace.tool for trace in response.traces],
                "degraded": response.degraded,
            },
        )
        return response
    except (LlmConfigurationError, LlmGatewayError) as exc:
        logger.exception("智能体调用失败", extra={"operation": "agent.chat"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
