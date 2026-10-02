from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pi_gateway import completion, gateway

from someip_agent.agent.service import (
    AgentService,
    AgentTool,
    LlmConfigurationService,
    LlmGatewayError,
    OpenAiCompatibleClient,
)
from someip_agent.agent.streaming import StreamedAssistantMessage, StreamProtocolError
from someip_agent.config import Settings
from someip_agent.domain.models import AgentChatRequest
from someip_agent.main import create_app
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.simulator import SimulationManager


def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
    data = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
    return f"data: {json.dumps(data, ensure_ascii=False)}\r\n\r\n".encode()


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for value in self.chunks:
            yield value

    async def aclose(self) -> None:
        self.closed = True


def mock_gateway(monkeypatch, responses: list[ChunkStream]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-stream-key"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=responses.pop(0)
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return payloads


def configuration() -> LlmConfigurationService:
    return LlmConfigurationService(Settings(_env_file=None, llm_api_key="test-stream-key"))


@pytest.mark.asyncio
async def test_stream_preserves_utf8_and_incremental_events(monkeypatch) -> None:
    first = chunk({"content": "你好，"})
    second = chunk({"content": "**SOME/IP**"}, "stop")
    # 在 UTF-8 字符内部和 SSE 帧内部断开网络分块。
    upstream = ChunkStream([first[:19], first[19:42], first[42:], second, b"data: [DONE]\n\n"])
    payloads = mock_gateway(monkeypatch, [upstream])
    client = OpenAiCompatibleClient(configuration())
    stream = client.stream([{"role": "user", "content": "测试"}])
    first_event = await anext(stream)
    assert first_event["choices"][0]["delta"]["content"] == "你好，"
    assert not upstream.closed  # 第一段内容在整个响应结束前即可交给前端。
    remaining = [event async for event in stream]
    assert remaining[0]["choices"][0]["delta"]["content"] == "**SOME/IP**"
    assert payloads[0]["stream"] is True
    assert upstream.closed


@pytest.mark.asyncio
async def test_stream_reports_truncated_response_and_closes_upstream(monkeypatch) -> None:
    upstream = ChunkStream([chunk({"content": "已经收到的文本"})])
    mock_gateway(monkeypatch, [upstream])
    stream = OpenAiCompatibleClient(configuration()).stream([])
    assert (await anext(stream))["choices"][0]["delta"]["content"] == "已经收到的文本"
    with pytest.raises(LlmGatewayError, match="提前中断"):
        await anext(stream)
    assert upstream.closed


@pytest.mark.asyncio
async def test_cancel_closes_active_gateway_response(monkeypatch) -> None:
    started_waiting = asyncio.Event()

    class WaitingStream(ChunkStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield chunk({"content": "第一段"})
            started_waiting.set()
            await asyncio.Event().wait()

    upstream = WaitingStream([])
    mock_gateway(monkeypatch, [upstream])
    stream = OpenAiCompatibleClient(configuration()).stream([])
    await anext(stream)
    waiting = asyncio.create_task(anext(stream))
    await asyncio.wait_for(started_waiting.wait(), timeout=1)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert upstream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_mutation", [False, True])
async def test_fragmented_tool_calls_followup_and_mutation_guard(
    monkeypatch,
    tmp_path,
    allow_mutation: bool,
) -> None:
    first = ChunkStream(
        [
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "stop_simulation", "arguments": '{"simulation_'},
                        }
                    ]
                }
            ),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": 'id":"demo-1"}'},
                        }
                    ]
                },
                "tool_calls",
            ),
            b"data: [DONE]\n\n",
        ]
    )
    second = ChunkStream(
        [
            chunk({"content": "## 诊断结果\n"}),
            chunk({"content": "已读取工具结果。"}, "stop"),
            b"data: [DONE]\n\n",
        ]
    )
    with gateway([b"".join(first.chunks), b"".join(second.chunks)]) as (url, payloads, _):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        monitor = MonitorStore(100)
        agent = AgentService(
            LlmConfigurationService(
                Settings(_env_file=None, llm_base_url=url, llm_api_key="test-stream-key")
            ),
            monitor,
            SimulationManager(monitor, settings),
            lambda: [],
        )
        executions: list[dict[str, Any]] = []

        async def stop_handler(arguments: dict[str, Any], _allow: bool) -> Any:
            executions.append(arguments)
            return {"stopped": arguments["simulation_id"]}

        agent._tools["stop_simulation"] = AgentTool(
            "stop_simulation",
            "停止仿真",
            agent._tools["stop_simulation"].parameters,
            True,
            stop_handler,
        )
        events = [
            event
            async for event in agent.chat_stream(
                AgentChatRequest(message="停止演示仿真", allow_mutation=allow_mutation)
            )
        ]
    assert len(payloads) == 2
    assert payloads[1]["messages"][2]["tool_calls"][0]["function"] == {
        "name": "stop_simulation",
        "arguments": '{"simulation_id":"demo-1"}',
    }
    assert payloads[1]["messages"][3]["tool_call_id"] == "call_1"
    assert len(executions) == int(allow_mutation)
    tool_result = next(
        event["data"]["result"]
        for event in events
        if event["event"] == "tool" and event["data"]["phase"] == "result"
    )
    assert ("error" in tool_result) is not allow_mutation
    assert "".join(event["data"]["text"] for event in events if event["event"] == "delta") == (
        "## 诊断结果\n已读取工具结果。"
    )
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["status"] == "complete"


def test_stream_endpoint_reports_error_after_partial_content(tmp_path, monkeypatch) -> None:
    async def failing_stream(self, request) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "delta", "data": {"text": "部分回答"}}
        raise LlmGatewayError("模型网关连接提前中断")

    monkeypatch.setattr(AgentService, "chat_stream", failing_stream)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key="test-stream-key"))
    with TestClient(app) as client:
        response = client.post("/api/v1/agent/chat/stream", json={"message": "测试"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert 'event: delta\ndata: {"text": "部分回答"}' in response.text
    assert "event: error" in response.text
    assert response.text.endswith('event: done\ndata: {"status":"error"}\n\n')


def test_stream_local_evidence_is_explicit_and_has_completion(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key=""))
    with TestClient(app) as client:
        response = client.post("/api/v1/agent/chat/stream", json={"message": "检查监控"})
    assert response.status_code == 200
    assert '"phase": "local"' in response.text
    assert '"model": "local-evidence-engine"' in response.text
    assert '"status": "complete"' in response.text
    assert "event: error" not in response.text


def test_interleaved_tool_arguments_are_aggregated_by_index() -> None:
    message = StreamedAssistantMessage()
    message.append(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "b",
                                "function": {
                                    "name": "stop_simulation",
                                    "arguments": '{"simulation_id":',
                                },
                            },
                            {
                                "index": 0,
                                "id": "a",
                                "function": {"name": "list_services", "arguments": "{"},
                            },
                        ]
                    }
                }
            ]
        }
    )
    message.append(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": "}"}},
                            {"index": 1, "function": {"arguments": '"one"}'}},
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    calls = message.message()["tool_calls"]
    assert [call["id"] for call in calls] == ["a", "b"]
    assert calls[1]["function"]["arguments"] == '{"simulation_id":"one"}'


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_incomplete_completion_is_not_marked_successful(finish_reason: str) -> None:
    message = StreamedAssistantMessage(content="部分文本", finish_reason=finish_reason)
    with pytest.raises(StreamProtocolError, match="未正常完成"):
        message.message()


@pytest.mark.asyncio
async def test_closing_agent_stream_closes_gateway(monkeypatch, tmp_path) -> None:
    with gateway([completion("第一段")]) as (url, _, _):
        monitor = MonitorStore(100)
        agent = AgentService(
            LlmConfigurationService(
                Settings(_env_file=None, llm_base_url=url, llm_api_key="test-stream-key")
            ),
            monitor,
            SimulationManager(monitor, Settings(_env_file=None, data_dir=tmp_path)),
            lambda: [],
        )
        stream = agent.chat_stream(AgentChatRequest(message="检查"))
        assert (await anext(stream))["event"] == "status"
        assert (await anext(stream))["data"]["text"] == "第一段"
        await stream.aclose()
        assert agent._runtime.view()["active_sessions"] == 0
