import argparse
import json
from uuid import uuid4

import httpx
import pytest

from someip_agent.scenario_cli import api_root, execute, write_artifact


def test_evidence_subcommand_dispatch_does_not_start_backend(monkeypatch):
    from someip_agent.main import run

    monkeypatch.setattr("sys.argv", ["someip-agent", "evidence-verify", "--help"])
    with pytest.raises(SystemExit) as result:
        run()
    assert result.value.code == 0


def arguments(tmp_path):
    definition = tmp_path / "scenario.json"
    definition.write_text(
        json.dumps(
            {
                "name": "命令行测试",
                "steps": [
                    {
                        "kind": "assert",
                        "path": ["parameters", "large"],
                        "expected": 18446744073709551615,
                    }
                ],
                "cases": [{"large": 18446744073709551615}],
            }
        )
    )
    return argparse.Namespace(
        server="http://localhost:8765",
        project=uuid4(),
        definition=definition,
        timeout=0.01,
        result=tmp_path / "result.json",
        junit=tmp_path / "junit.xml",
        html=tmp_path / "report.html",
    )


@pytest.mark.parametrize(
    "status,cleanup,expected",
    [("passed", True, 0), ("failed", True, 1), ("passed", False, 1), ("cancelled", True, 1)],
)
def test_headless_exit_reports_and_exact_integer(tmp_path, status, cleanup, expected):
    args = arguments(tmp_path)
    identifier = str(uuid4())

    def handler(request):
        if request.method == "POST":
            sent = json.loads(request.content)
            assert sent["definition"]["steps"][0]["expected"] == 18446744073709551615
            assert "X-Request-ID" in request.headers
            return httpx.Response(202, json={"id": identifier})
        if request.url.path.endswith("junit"):
            return httpx.Response(200, content=b"<testsuite />")
        if request.url.path.endswith("report"):
            return httpx.Response(200, text="<html>报告</html>")
        return httpx.Response(
            200, json={"id": identifier, "status": status, "cleanup_complete": cleanup}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert execute(args, client) == expected
    assert json.loads(args.result.read_text())["status"] == status
    assert args.junit.read_bytes() == b"<testsuite />"
    assert "报告" in args.html.read_text()
    assert args.result.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_artifact(args.result, b"overwrite")
    assert json.loads(args.result.read_text())["status"] == status


def test_poll_failure_cancels_only_known_run(tmp_path):
    args = arguments(tmp_path)
    identifier = str(uuid4())
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("cancel"):
            return httpx.Response(200, json={"status": "cancelled"})
        if request.method == "POST":
            return httpx.Response(202, json={"id": identifier})
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            execute(args, client)
    assert calls[-1] == f"/api/v1/scenarios/runs/{identifier}/cancel"
    assert not args.result.exists()


@pytest.mark.parametrize(
    "server",
    [
        "file:///tmp/test",
        "http://user:password@localhost",
        "https://example.com?key=value",
        "http://localhost/#fragment",
    ],
)
def test_server_does_not_accept_embedded_credentials(server):
    with pytest.raises(ValueError):
        api_root(server)


def test_main_and_frozen_launcher_share_scenario_dispatch(monkeypatch):
    from someip_agent.main import run

    received = []
    monkeypatch.setattr("sys.argv", ["someip-agent", "scenario", "--help"])
    monkeypatch.setattr("someip_agent.scenario_cli.main", lambda argv: received.append(argv) or 0)
    with pytest.raises(SystemExit) as result:
        run()
    assert result.value.code == 0 and received == [["--help"]]
