"""安装包验收传输的纯标准库回归；不冒充运行镜像中的真实冻结 CLI。"""

import json
from types import SimpleNamespace

import pytest
from check_linux_payload_package import PackageClient
from check_linux_workbench_package import SEED, command, definition


def test_json_transport_uses_actual_utf8_length_and_preserves_uint64(
    tmp_path, monkeypatch
):
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        response = json.dumps({"seed": SEED}, ensure_ascii=False).encode()
        return SimpleNamespace(
            stdout=b"HTTP/1.0 201 Created\r\nContent-Length: "
            + str(len(response)).encode()
            + b"\r\n\r\n"
            + response,
            stderr=b"",
            check_returncode=lambda: None,
        )

    monkeypatch.setattr("check_linux_payload_package.subprocess.run", execute)
    result = PackageClient("isolated-id", tmp_path).request(
        "/api/v1/projects", "project", json_body={"name": "工程", "seed": SEED}
    )
    args, options = calls[0]
    assert args[:4] == ["docker", "exec", "-i", "isolated-id"]
    headers, body = options["input"].split(b"\r\n\r\n", 1)
    assert f"Content-Length: {len(body)}".encode() in headers
    assert json.loads(body)["seed"] == result["seed"] == SEED
    assert options["timeout"] == 45 and options["check"] is False


def test_request_rejects_ambiguous_file_and_json(tmp_path):
    with pytest.raises(ValueError, match="同时"):
        PackageClient("isolated", tmp_path).request(
            "/import", "input", b"file", json_body={}
        )


def test_frozen_cli_fixture_requires_actual_consumer_and_uses_typed_rates():
    doc = definition()
    assert doc["cases"] == [{"speed": 0.5}, {"speed": 4.0}]
    assert all(type(case["speed"]) is float for case in doc["cases"])
    assert (
        len(
            [
                step
                for step in doc["steps"]
                if step["kind"] == "sync_control"
                and step["command"]["action"] == "step"
            ]
        )
        == 7
    )
    receipts = [
        step["match"] for step in doc["steps"] if step["kind"] == "wait_message"
    ]
    assert receipts == [
        {"member": "Consumer_client", "payload_hex": "41c40000"},
        {"member": "Consumer_client", "payload_hex": "0a"},
    ]
    assert command()["events"][1]["sources"][0]["generator"]["seed"] == SEED
    assert doc["cleanup"] == [{"kind": "sync_stop", "session": "pair"}]
