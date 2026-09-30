from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx


class StreamProtocolError(ValueError):
    """上游流式响应不完整或格式无效。"""


async def read_openai_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """逐个读取 SSE 数据事件；网络分块和 UTF-8 解码交由 httpx 处理。"""
    data: list[str] = []
    finished = False
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
            if sum(map(len, data)) > 1_048_576:
                raise StreamProtocolError("模型网关单条流式事件超过大小限制")
        elif line == "" and data:
            raw = "\n".join(data)
            data.clear()
            if raw == "[DONE]":
                return
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise StreamProtocolError("模型网关返回无效的流式 JSON") from exc
            if not isinstance(event, dict):
                raise StreamProtocolError("模型网关流式事件必须是 JSON 对象")
            if event.get("error"):
                raise StreamProtocolError("模型网关流式返回错误，请检查模型配置或网关日志")
            choices = event.get("choices")
            if not isinstance(choices, list):
                raise StreamProtocolError("模型网关流式事件缺少 choices")
            # 某些兼容网关只通过 finish_reason 结束，而不输出 [DONE]。
            if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason"):
                finished = True
            yield event
    if data or not finished:
        raise StreamProtocolError("模型网关连接提前中断，已收到的内容已保留，请重试")


@dataclass
class StreamedAssistantMessage:
    content: str = ""
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    finish_reason: str | None = None

    def append(self, event: dict[str, Any]) -> str:
        choices = event.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            raise StreamProtocolError("模型网关 choices 格式无效")
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            raise StreamProtocolError("模型网关 delta 格式无效")
        self.finish_reason = choice.get("finish_reason") or self.finish_reason
        content = delta.get("content") or ""
        if not isinstance(content, str):
            raise StreamProtocolError("模型网关文本增量格式无效")
        self.content += content
        for part in delta.get("tool_calls") or []:
            if not isinstance(part, dict) or not isinstance(part.get("index"), int):
                raise StreamProtocolError("模型网关工具调用缺少有效 index")
            call = self.tool_calls.setdefault(
                part["index"],
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if part.get("id"):
                call["id"] += part["id"]
            function = part.get("function") or {}
            if not isinstance(function, dict):
                raise StreamProtocolError("模型网关工具调用格式无效")
            for key in ("name", "arguments"):
                fragment = function.get(key) or ""
                if not isinstance(fragment, str):
                    raise StreamProtocolError("模型网关工具调用片段必须是字符串")
                call["function"][key] += fragment
        return content

    def message(self) -> dict[str, Any]:
        if self.finish_reason in {"length", "content_filter"}:
            raise StreamProtocolError("模型输出未正常完成（长度限制或内容过滤），请缩小问题后重试")
        calls = [self.tool_calls[index] for index in sorted(self.tool_calls)]
        for call in calls:
            if not call["id"] or not call["function"]["name"]:
                raise StreamProtocolError("模型网关工具调用缺少 ID 或名称")
        return {
            "role": "assistant",
            "content": self.content or None,
            **({"tool_calls": calls} if calls else {}),
        }
