from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pi_gateway import PausedResponse, completion, gateway
from starlette.requests import Request

from someip_agent.agent.service import (
    AgentService,
    AgentTool,
    LlmConfigurationService,
    LlmGatewayError,
)
from someip_agent.api.agent import agent_chat_stream
from someip_agent.config import Settings
from someip_agent.domain.models import AgentChatRequest
from someip_agent.main import create_app
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.simulator import SimulationManager
from someip_agent.state import ApplicationState


def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
    data = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
    return f"data: {json.dumps(data, ensure_ascii=False)}\r\n\r\n".encode()


def agent(url, tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="test-stream-key"
    )
    monitor = MonitorStore(100)
    return AgentService(
        LlmConfigurationService(settings),
        monitor,
        SimulationManager(monitor, settings),
        lambda: [],
        settings=settings,
    )


@pytest.mark.asyncio
async def test_pi_preserves_split_utf8_and_incremental_delivery(tmp_path):
    first = chunk({"content": "你好，"})
    boundary = first.index("你".encode()) + 1
    upstream = PausedResponse([first[:boundary], first[boundary:], completion("**SOME/IP**")])
    with gateway([upstream]) as (url, payloads, _):
        subject = agent(url, tmp_path)
        stream = subject.chat_stream(AgentChatRequest(message="测试"))
        assert (await anext(stream))["event"] == "status"
        assert (await anext(stream))["data"]["text"] == "你好，"
        assert not upstream.completed.is_set()
        upstream.release.set()
        events = [item async for item in stream]
        assert "".join(e["data"]["text"] for e in events if e["event"] == "delta") == "**SOME/IP**"
        assert payloads[0]["stream"] is True
        assert events[-1]["data"]["runtime"] == "pi-agent-core"
        assert subject.runtime_view()["active_sessions"] == 0


@pytest.mark.asyncio
async def test_cancel_while_pi_gateway_is_stalled_cleans_process(tmp_path):
    upstream = PausedResponse([chunk({"content": "第一段"}), completion("尾段")])
    with gateway([upstream]) as (url, _, _):
        subject = agent(url, tmp_path)
        stream = subject.chat_stream(AgentChatRequest(message="测试"))
        await anext(stream)
        assert (await anext(stream))["data"]["text"] == "第一段"
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        assert subject.runtime_view()["active_sessions"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_http_disconnect_cleans_pi_under_starlette_cancel_scope(tmp_path, spec_version):
    upstream = PausedResponse([chunk({"content": "断连前文本"}), completion("不应等待的尾段")])
    with gateway([upstream]) as (url, _, _):
        state = ApplicationState(
            Settings(_env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="test-key")
        )
        disconnect = asyncio.Event()
        delivered = []

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            body = message.get("body", b"")
            if "断连前文本".encode() in body:
                delivered.append(body)
                disconnect.set()

        scope = {"type": "http", "asgi": {"spec_version": spec_version}}
        response = await agent_chat_stream(
            AgentChatRequest(message="断连测试"), Request(scope, receive=receive), state
        )
        try:
            await asyncio.wait_for(response(scope, receive, send), 5)
            assert delivered
            assert not upstream.completed.is_set()
            assert state.agent.runtime_view()["active_sessions"] == 0
        finally:
            await state.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_mutation", [False, True])
async def test_fragmented_tool_calls_followup_and_mutation_guard(tmp_path, allow_mutation):
    first = [
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
            {"tool_calls": [{"index": 0, "function": {"arguments": 'id":"demo-1"}'}}]}, "tool_calls"
        ),
        b"data: [DONE]\n\n",
    ]
    with gateway([first, completion("已读取工具结果。")]) as (url, payloads, _):
        subject = agent(url, tmp_path)
        executions = []

        async def stop_handler(arguments, _allow):
            executions.append(arguments)
            return {"stopped": arguments["simulation_id"]}

        subject._tools["stop_simulation"] = AgentTool(
            "stop_simulation",
            "停止仿真",
            subject._tools["stop_simulation"].parameters,
            True,
            stop_handler,
        )
        events = [
            event
            async for event in subject.chat_stream(
                AgentChatRequest(message="停止演示仿真", allow_mutation=allow_mutation)
            )
        ]
        assert len(payloads) == 2
        tool_result = next(m for m in payloads[1]["messages"] if m["role"] == "tool")
        assert tool_result["tool_call_id"] == "call_1"
        assert ("error" in json.loads(tool_result["content"])) is not allow_mutation
        assert len(executions) == int(allow_mutation)
        assert events[-1]["data"]["status"] == "complete"


def test_stream_endpoint_reports_error_after_partial_content(tmp_path, monkeypatch):
    async def failing_stream(self, request) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "delta", "data": {"text": "部分回答"}}
        raise LlmGatewayError("模型网关连接提前中断")

    monkeypatch.setattr(AgentService, "chat_stream", failing_stream)
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.post("/api/v1/agent/chat/stream", json={"message": "测试"})
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert 'event: delta\ndata: {"text": "部分回答"}' in response.text
    assert "event: error" in response.text
    assert response.text.endswith('event: done\ndata: {"status":"error"}\n\n')


def test_stream_local_evidence_is_explicit_and_has_completion(tmp_path, monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.post("/api/v1/agent/chat/stream", json={"message": "检查监控"})
    assert '"phase": "local"' in response.text
    assert '"runtime": "local-evidence-engine"' in response.text
    assert '"status": "complete"' in response.text
    assert "event: error" not in response.text


@pytest.mark.asyncio
async def test_interleaved_tool_arguments_are_correlated_by_id(tmp_path):
    first = [
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "b",
                        "function": {"name": "stop_simulation", "arguments": '{"simulation_id":'},
                    },
                    {
                        "index": 0,
                        "id": "a",
                        "function": {"name": "list_services", "arguments": "{"},
                    },
                ]
            }
        ),
        chunk(
            {
                "tool_calls": [
                    {"index": 0, "function": {"arguments": "}"}},
                    {"index": 1, "function": {"arguments": '"one"}'}},
                ]
            },
            "tool_calls",
        ),
        b"data: [DONE]\n\n",
    ]
    with gateway([first, completion("已关联每个工具参数。")]) as (url, payloads, _):
        subject = agent(url, tmp_path)
        await subject.chat(AgentChatRequest(message="测试交错参数"))
        calls = next(m["tool_calls"] for m in payloads[1]["messages"] if m.get("tool_calls"))
        by_id = {c["id"]: c["function"] for c in calls}
        assert by_id["a"]["name"] == "list_services"
        assert json.loads(by_id["a"]["arguments"]) == {}
        assert json.loads(by_id["b"]["arguments"]) == {"simulation_id": "one"}
        assert {m["tool_call_id"] for m in payloads[1]["messages"] if m["role"] == "tool"} == {
            "a",
            "b",
        }


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_incomplete_pi_completion_is_not_marked_successful(tmp_path, finish_reason):
    with gateway([chunk({"content": "部分文本"}, finish_reason)]) as (url, _, _):
        with TestClient(
            create_app(
                Settings(
                    _env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="test-key"
                )
            )
        ) as client:
            response = client.post("/api/v1/agent/chat/stream", json={"message": "测试"})
    assert "部分文本" in response.text
    assert '"status":"error"' in response.text


@pytest.mark.asyncio
async def test_closing_agent_stream_cleans_pi_process(tmp_path):
    with gateway([completion("第一段")]) as (url, _, _):
        subject = agent(url, tmp_path)
        stream = subject.chat_stream(AgentChatRequest(message="检查"))
        await anext(stream)
        assert (await anext(stream))["data"]["text"] == "第一段"
        await stream.aclose()
        assert subject.runtime_view()["active_sessions"] == 0
