from __future__ import annotations

from typing import Any

import httpx
import pytest

from someip_agent.agent.service import LlmConfigurationService, OpenAiCompatibleClient
from someip_agent.config import Settings


@pytest.mark.asyncio
async def test_openai_compatible_client_uses_configured_gateway(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_options"] = kwargs

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            captured.update(url=url, headers=headers, payload=json)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    configuration = LlmConfigurationService(
        Settings(
            _env_file=None,
            llm_base_url="https://voyahgpt-gateway.voyah.cn/api/gateway/v1/",
            llm_api_key="test-only-key",
            llm_model="deepseek-v4-flash",
            llm_temperature=0.3,
        )
    )

    message = await OpenAiCompatibleClient(configuration).complete(
        [{"role": "user", "content": "ping"}]
    )

    assert message["content"] == "OK"
    assert captured["url"] == (
        "https://voyahgpt-gateway.voyah.cn/api/gateway/v1/chat/completions"
    )
    assert captured["headers"]["Authorization"] == "Bearer test-only-key"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["temperature"] == 0.3
    assert captured["payload"]["stream"] is False
