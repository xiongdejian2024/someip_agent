from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pi_gateway import completion, gateway, tool_call

from someip_agent.config import Settings
from someip_agent.main import create_app


@pytest.mark.parametrize("stream", [False, True])
def test_real_pi_loop_calls_console_and_continues(tmp_path, stream):
    with gateway([tool_call("get_monitor_summary", {}), completion("Pi 已核对证据。")]) as (
        url,
        payloads,
        headers,
    ):
        app = create_app(
            Settings(
                _env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="pi-test-only-key"
            )
        )
        with TestClient(app) as client:
            path = "/api/v1/agent/chat" + ("/stream" if stream else "")
            response = client.post(path, json={"message": "检查监控"})
            assert response.status_code == 200
            assert "Pi 已核对证据。" in response.text
            assert "get_monitor_summary" in response.text
            assert "pi-test-only-key" not in response.text
            runtime = client.get("/api/v1/console/actions").json()["runtime"]
            assert runtime["active_sessions"] == 0
            assert runtime["name"] == "pi-agent-core"
            assert not runtime["plugins_enabled"] and not runtime["skills_enabled"]
        assert len(payloads) == 2
        assert payloads[0]["stream"] is True
        assert headers[0]["authorization"] == "Bearer pi-test-only-key"
        tool_result = next(m for m in payloads[1]["messages"] if m["role"] == "tool")
        assert "total" in json.loads(tool_result["content"])


@pytest.mark.parametrize("allow", [False, True])
def test_pi_mutation_guard_and_each_action_audit(tmp_path, allow):
    with gateway([tool_call("clear_monitor", {}), completion("操作结果已收到。")]) as (
        url,
        _,
        _,
    ):
        app = create_app(
            Settings(_env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="pi-test-key")
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/agent/chat",
                json={
                    "message": "清空缓存",
                    "allow_mutation": allow,
                },
            )
            assert response.status_code == 200
            result = response.json()["traces"][0]["result"]
            assert ("error" in result) is not allow
            audits = client.get("/api/v1/audit/events").json()
            record = next(item for item in audits if item["action"] == "console.clear_monitor")
            assert record["success"] is allow


def test_console_api_defaults_readonly_and_no_shell(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        catalog = client.get("/api/v1/console/actions").json()
        names = {item["name"] for item in catalog["actions"]}
        assert {
            "service_call",
            "start_service_session",
            "start_network_listener",
            "stop_simulation",
            "query_messages",
        } <= names
        assert not {"bash", "shell", "read_file", "write_file"} & names
        assert client.post("/api/v1/console/actions/clear_monitor", json={}).status_code == 403
        assert client.post(
            "/api/v1/console/actions/clear_monitor",
            json={
                "allow_mutation": True,
            },
        ).json()["result"] == {"cleared": True}
        assert (
            client.post(
                "/api/v1/console/actions/shell",
                json={
                    "arguments": {"command": "id"},
                    "allow_mutation": True,
                },
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/console/actions/stop_network_listener",
                json={
                    "arguments": {},
                    "allow_mutation": True,
                },
            ).status_code
            == 422
        )


def test_console_navigation_is_typed_bounded_and_queued(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert client.get("/api/v1/console/commands").json() == {"last_id": 0, "commands": []}
        response = client.post(
            "/api/v1/console/actions/navigate_console",
            json={
                "arguments": {"page": "monitor"},
            },
        )
        assert response.json()["result"]["status"] == "queued"
        assert client.get("/api/v1/console/commands?after_id=0").json()["commands"] == [
            {"id": 1, "action": "navigate", "page": "monitor"},
        ]
        assert client.get("/api/v1/console/commands?after_id=1").json()["commands"] == []
        assert (
            client.post(
                "/api/v1/console/actions/navigate_console",
                json={
                    "arguments": {"page": "https://example.com"},
                },
            ).status_code
            == 422
        )


def test_pi_schema_rejection_never_runs_mutation(tmp_path):
    with gateway(
        [tool_call("clear_monitor", {"unknown": True}), completion("工具参数已拒绝。")]
    ) as (
        url,
        payloads,
        _,
    ):
        with TestClient(
            create_app(
                Settings(
                    _env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="test-key"
                )
            )
        ) as client:
            response = client.post(
                "/api/v1/agent/chat",
                json={
                    "message": "测试",
                    "allow_mutation": True,
                },
            )
            assert response.status_code == 200
            assert "error" in response.json()["traces"][0]["result"]
            assert "cleared" not in json.dumps(payloads[1])


def test_pi_round_limit_is_not_a_fake_success(tmp_path):
    with gateway([tool_call("get_monitor_summary", {}, f"call_{i}") for i in range(4)]) as (
        url,
        payloads,
        _,
    ):
        with TestClient(
            create_app(
                Settings(
                    _env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="test-key"
                )
            )
        ) as client:
            response = client.post("/api/v1/agent/chat/stream", json={"message": "测试"})
            assert len(payloads) == 4
            assert "安全上限" in response.text
            assert '"status":"error"' in response.text


@pytest.mark.parametrize(
    "body",
    [
        'data: {"choices":[{"delta":{"content":"部分文本"}}]}\n\n'.encode(),
        (
            'data: {"choices":[{"delta":{"content":"部分文本"},"finish_reason":"length"}]}\n\n'
        ).encode(),
    ],
)
def test_pi_incomplete_gateway_does_not_report_success(tmp_path, body):
    with gateway([body]) as (url, _, _):
        with TestClient(
            create_app(
                Settings(
                    _env_file=None, data_dir=tmp_path, llm_base_url=url, llm_api_key="test-only-key"
                )
            )
        ) as client:
            response = client.post("/api/v1/agent/chat/stream", json={"message": "测试"})
            assert "部分文本" in response.text
            assert '"status":"error"' in response.text
            assert '"status": "complete"' not in response.text
