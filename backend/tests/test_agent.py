from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pi_gateway import completion, gateway

from someip_agent.agent.service import LlmConfigurationError, LlmConfigurationService
from someip_agent.config import Settings
from someip_agent.main import create_app


def test_pi_uses_configured_gateway_and_sampling(tmp_path):
    with gateway([completion("OK")]) as (url, payloads, headers):
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=tmp_path,
                llm_base_url=url,
                llm_api_key="test-only-key",
                llm_model="deepseek-v4-flash",
                llm_temperature=0.3,
            )
        )
        with TestClient(app) as client:
            response = client.post("/api/v1/agent/chat", json={"message": "ping"})
            assert response.json()["answer"] == "OK"
            assert response.json()["runtime"] == "pi-agent-core"
        assert headers[0]["authorization"] == "Bearer test-only-key"
        assert payloads[0]["model"] == "deepseek-v4-flash"
        assert payloads[0]["temperature"] == 0.3
        assert payloads[0]["stream"] is True


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://user:password@example.com/v1",
        "https://example.com/v1?secret=1",
        "https://example.com/v1#secret",
    ],
)
def test_initial_gateway_config_does_not_bypass_validation(url):
    with pytest.raises(LlmConfigurationError):
        LlmConfigurationService(Settings(_env_file=None, llm_base_url=url))
