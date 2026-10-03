"""干净发行包的工程、原生公共时钟与冻结 CLI：宿主仅发送夹具并核验输出。"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import zipfile
from pathlib import Path
from xml.etree.ElementTree import fromstring

from check_linux_payload_package import PackageClient

logger = logging.getLogger("干净发行包工作台验收")
BINARY = "/opt/someip-agent/someip-agent"
OUTPUT = "/tmp/someip-clean-workbench"
SEED = 18446744073709551615


def command() -> dict:
    return {
        "paused": True,
        "speed": 0.5,
        "events": [
            {
                "member": "Provider_server",
                "function": "UpdateSpeedChangedEvent",
                "args": 0,
                "interval_ms": 20,
                "csv_text": "time_ms,\n0,42.5\n29,24.5\n",
            },
            {
                "member": "Provider_server",
                "function": "UpdateIgnitionStateEvent",
                "args": 0,
                "interval_ms": 30,
                "sources": [
                    {
                        "path": "",
                        "generator": {
                            "kind": "state_machine",
                            "seed": SEED,
                            "initial_state": "cold",
                            "states": [
                                {
                                    "name": "cold",
                                    "value": 9,
                                    "duration_ms": 29,
                                    "next": "warm",
                                },
                                {"name": "warm", "value": 10},
                            ],
                        },
                    }
                ],
            },
        ],
    }


def definition() -> dict:
    steps = [
        {"kind": "start_service", "profile": "pair"},
        {"kind": "wait_ready", "session": "pair"},
        {
            "kind": "sync_start",
            "session": "pair",
            "profile": "pair",
            "save_as": "prepared",
        },
        {"kind": "assert", "path": ["prepared", "frame_index"], "expected": 0},
        {"kind": "assert", "path": ["prepared", "paused"], "expected": True},
    ]
    steps.extend(
        {"kind": "sync_control", "session": "pair", "command": {"action": "step"}}
        for _ in range(7)
    )
    steps.extend(
        [
            {"kind": "sync_status", "session": "pair", "save_as": "clock"},
            {"kind": "assert", "path": ["clock", "logical_ms"], "expected": 70},
            {
                "kind": "assert",
                "path": ["clock", "events", 0, "emitted_count"],
                "expected": 4,
            },
            {
                "kind": "assert",
                "path": ["clock", "events", 1, "emitted_count"],
                "expected": 3,
            },
            {
                "kind": "assert",
                "path": ["clock", "events", 1, "active_states", ""],
                "expected": "warm",
            },
            {
                "kind": "wait_message",
                "session": "pair",
                "match": {"member": "Consumer_client", "payload_hex": "41c40000"},
            },
            {
                "kind": "wait_message",
                "session": "pair",
                "match": {"member": "Consumer_client", "payload_hex": "0a"},
            },
            {
                "kind": "sync_control",
                "session": "pair",
                "command": {"action": "speed", "speed": {"$param": "speed"}},
                "save_as": "rated",
            },
            {
                "kind": "assert",
                "path": ["rated", "speed"],
                "expected": {"$param": "speed"},
            },
        ]
    )
    return {
        "name": "冻结发行包 CSV/FSM 原生接收",
        "cases": [{"speed": 0.5}, {"speed": 4.0}],
        "steps": steps,
        "cleanup": [{"kind": "sync_stop", "session": "pair"}],
    }


def verify(container: str, root: Path, fixtures: Path) -> None:
    root.mkdir(mode=0o700, exist_ok=False)
    client = PackageClient(container, root)

    def run(
        args: list[str], name: str, expected: int = 0
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["docker", "exec", container, *args],
            capture_output=True,
            timeout=120,
            check=False,
        )
        (root / f"{name}.stdout").write_bytes(result.stdout)
        (root / f"{name}.stderr").write_bytes(result.stderr)
        if result.returncode != expected:
            raise RuntimeError(
                f"发行包命令退出码错误：{name}，实际 {result.returncode}，期望 {expected}；完整输出已保留"
            )
        return result

    def copy_into(path: Path) -> str:
        target = f"{OUTPUT}/{path.name}"
        subprocess.run(
            ["docker", "cp", str(path), f"{container}:{target}"], check=True, timeout=30
        )
        return target

    logger.info("步骤 1/4：HTTP 导入现有 ARXML，保存包含最大种子的同步工程，保存不运行")
    client.request(
        "/api/v1/arxml/import",
        "vehicle_service.arxml",
        (fixtures / "vehicle_service.arxml").read_bytes(),
    )
    model = client.request("/api/v1/model", "model")
    saved = client.request(
        "/api/v1/projects",
        "project",
        json_body={
            "document": {
                "name": "冻结 CLI 独立验收",
                "model": model,
                "services": {
                    "pair": {
                        "application_name": "frozen_pair",
                        "application_id": 0x4C01,
                        "members": {
                            "Provider": {"service": "VehicleStatus", "role": "server"},
                            "Consumer": {"service": "VehicleStatus", "role": "client"},
                        },
                    }
                },
                "sync_groups": [{"service_profile": "pair", "command": command()}],
            }
        },
    )
    assert client.request("/api/v1/services/sessions", "saved-sessions") == []
    assert (
        saved["document"]["sync_groups"][0]["command"]["events"][1]["sources"][0][
            "generator"
        ]["seed"]
        == SEED
    )
    run(["mkdir", "-m", "700", OUTPUT], "create-output")

    def execute(name: str, document: dict, expected: int) -> dict:
        path = root / f"{name}.definition.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        source = copy_into(path)
        args = [
            BINARY,
            "scenario",
            "--server",
            "http://127.0.0.1:8765",
            "--project",
            saved["id"],
            "--definition",
            source,
            "--timeout",
            "60",
        ]
        for flag, suffix in (
            ("result", "json"),
            ("junit", "xml"),
            ("html", "html"),
            ("evidence", "zip"),
        ):
            args.extend([f"--{flag}", f"{OUTPUT}/{name}.{suffix}"])
        run(args, f"{name}-cli", expected)
        for suffix in ("json", "xml", "html", "zip"):
            filename = f"{name}.{suffix}"
            mode = run(
                ["stat", "-c", "%a", f"{OUTPUT}/{filename}"], f"{name}-{suffix}-mode"
            )
            assert mode.stdout.strip() == b"600", "CLI 证据不得依赖宽松的默认 umask"
            subprocess.run(
                [
                    "docker",
                    "cp",
                    f"{container}:{OUTPUT}/{filename}",
                    str(root / filename),
                ],
                check=True,
                timeout=30,
            )
        run(
            [BINARY, "evidence-verify", f"{OUTPUT}/{name}.zip"],
            f"{name}-offline-verify",
        )
        result = json.loads((root / f"{name}.json").read_text())
        assert result["cleanup_complete"]
        assert (
            client.request("/api/v1/health", f"{name}-health")[
                "active_service_sessions"
            ]
            == 0
        )
        assert b"<html" in (root / f"{name}.html").read_bytes()
        return result

    logger.info("步骤 2/4：运行冻结主程序的成功场景，核验公共帧、消费者载荷与完整证据")
    passed = execute("passed", definition(), 0)
    assert passed["status"] == "passed"
    assert len(fromstring((root / "passed.xml").read_bytes()).findall("testcase")) >= 1
    inputs = client.request(
        f"/api/v1/scenarios/runs/{passed['id']}/inputs", "frozen-inputs"
    )
    assert inputs["project"]["revision"] == 1
    assert (
        inputs["project"]["document"]["sync_groups"] == saved["document"]["sync_groups"]
    )
    frames = client.request(
        f"/api/v1/recordings/{passed['recording_id']}/frames", "passed-frames"
    )["frames"]
    received = {
        frame["message"]["payload_hex"]
        for frame in frames
        if frame["message"]["metadata"].get("member") == "Consumer_client"
    }
    assert {"422a0000", "41c40000", "09", "0a"} <= received

    logger.info("步骤 3/4：实际业务失败必须退出 1，同时释放会话并保留完整失败证据")
    failed = execute(
        "failed",
        {
            "name": "冻结 CLI 明确失败",
            "cases": [{"value": 2}],
            "steps": [
                {"kind": "start_service", "profile": "pair"},
                {"kind": "sync_start", "session": "pair", "profile": "pair"},
                {"kind": "assert", "path": ["parameters", "value"], "expected": 1},
            ],
        },
        1,
    )
    assert failed["status"] == "failed" and fromstring(
        (root / "failed.xml").read_bytes()
    ).findall("testcase/failure")

    logger.info("追加取消门禁：冻结客户端等待超时后，已知运行必须取消并释放同步会话")
    timeout_path = root / "timeout.definition.json"
    timeout_path.write_text(
        json.dumps(
            {
                "name": "冻结客户端超时清理",
                "steps": [
                    {"kind": "start_service", "profile": "pair"},
                    {"kind": "wait_ready", "session": "pair"},
                    {"kind": "sync_start", "session": "pair", "profile": "pair"},
                    {"kind": "delay", "seconds": 30},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = copy_into(timeout_path)
    run(
        [
            BINARY,
            "scenario",
            "--server",
            "http://127.0.0.1:8765",
            "--project",
            saved["id"],
            "--definition",
            source,
            "--timeout",
            "2",
        ],
        "timeout-cli",
        2,
    )
    summaries = client.request("/api/v1/scenarios/runs", "timeout-history")
    cancelled = [item for item in summaries if item["name"] == "冻结客户端超时清理"]
    assert (
        len(cancelled) == 1
        and cancelled[0]["status"] == "cancelled"
        and cancelled[0]["cleanup_complete"]
    )
    details = client.request(
        f"/api/v1/scenarios/runs/{cancelled[0]['id']}", "timeout-result"
    )
    assert any(
        step["kind"] == "sync_start" and step["status"] == "passed"
        for step in details["steps"]
    )
    assert (
        client.request("/api/v1/health", "timeout-health")["active_service_sessions"]
        == 0
    )

    logger.info("步骤 4/4：改动 ZIP 附件后冻结离线校验器必须拒绝，且不启动服务")
    tampered = root / "tampered.zip"
    with (
        zipfile.ZipFile(root / "passed.zip") as source,
        zipfile.ZipFile(tampered, "x") as target,
    ):
        names = source.namelist()
        victim = next(
            name for name in names if name.endswith(".json") and "manifest" not in name
        )
        for name in names:
            data = source.read(name)
            target.writestr(name, data + b" " if name == victim else data)
    target = copy_into(tampered)
    run([BINARY, "evidence-verify", target], "tampered-offline-verify", 2)
    health = client.request("/api/v1/health", "final-health")
    assert (
        health["active_service_sessions"]
        == health["active_simulations"]
        == health["active_listeners"]
        == 0
    )
    (root / "result.json").write_text(
        json.dumps(
            {
                "status": "verified",
                "system_python": False,
                "network": "none",
                "passed_run": passed["id"],
                "failed_run": failed["id"],
                "cancelled_run": cancelled[0]["id"],
                "frozen_cli": True,
                "native_consumer_receipts": True,
                "exact_seed": SEED,
                "complete_evidence": True,
                "tampering_rejected": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.info("冻结 CLI 成功／失败退出码、原生同步接收、清理与完整证据均通过")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="核验干净发行包的工程、公共时钟及冻结 CLI"
    )
    parser.add_argument("container")
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        verify(
            args.container,
            args.evidence,
            Path(__file__).resolve().parents[2] / "backend/tests/fixtures",
        )
    except Exception:
        logger.exception("干净发行包工作台／冻结 CLI 验收失败")
        raise
