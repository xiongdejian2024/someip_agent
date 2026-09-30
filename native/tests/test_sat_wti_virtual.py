"""六个 SAT WTI 辅助接口的真实 UDP/TCP 互通；显式测试布局不是 OEM 序列化认证。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from soa_partner.src.base_partner import S2sBaseClass
from soa_partner.src.Operator import SOAOperator

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent
EVIDENCE = ROOT.parents[1] / "build" / "virtual-evidence"
SERVICES = {"WTIService": 0x2345, "WTIAutoDriveService": 0x2346}


def array_schema(field):
    return {
        "type": "array",
        "element": {
            "type": "struct",
            "fields": [
                {"name": "name", "type": "string"},
                {"name": field, "type": "string"},
            ],
        },
    }


@pytest.fixture(params=["udp", "tcp"])
def wti_partners(request):
    transport = request.param
    processes, handles, objects = [], [], []
    catalog = {}
    for name, identifier in SERVICES.items():
        methods, events = {}, {}
        for suffix, field, method_id, group in (
            ("WarningMsgList", "info", 1, 1),
            ("TelltaleList", "state", 2, 2),
        ):
            methods[f"Get{suffix}"] = {
                "id": method_id,
                "input": {"type": "struct", "fields": []},
                "output": array_schema(field),
            }
            events[f"Update{suffix}Event"] = {
                "id": 0x8000 + method_id,
                "eventgroups": [group],
                "schema": {
                    "type": "struct",
                    "fields": [{"name": "list", **array_schema(field)}],
                },
            }
        catalog[name] = {
            "service_id": identifier,
            "instance_id": 1,
            "major_version": 1,
            "methods": methods,
            "events": events,
        }
    catalog_path = EVIDENCE / f"wti-{transport}-catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    try:
        for node, address in (("server", "10.77.0.1"), ("client", "10.77.0.2")):
            name = f"wti_{node}"
            config = json.loads((ROOT / f"{node}.json").read_text())
            config.update(
                {
                    "network": f"soa-wti-{node}",
                    "applications": [
                        {"name": name, "id": "0x4411" if node == "server" else "0x4422"}
                    ],
                    "routing": name,
                }
            )
            config.pop("services", None)
            if node == "server":
                config["services"] = [
                    {
                        "service": hex(identifier),
                        "instance": "0x0001",
                        "reliable" if transport == "tcp" else "unreliable": "30511"
                        if transport == "tcp"
                        else "30510",
                        "events": [
                            {
                                "event": hex(0x8000 + index),
                                "is_field": "false",
                                "is_reliable": transport == "tcp",
                            }
                            for index in (1, 2)
                        ],
                        "eventgroups": [
                            {"eventgroup": hex(index), "events": [hex(0x8000 + index)]}
                            for index in (1, 2)
                        ],
                    }
                    for identifier in SERVICES.values()
                ]
            config_path = EVIDENCE / f"wti-{node}-{transport}-config.json"
            config_path.write_text(json.dumps(config))
            log = (EVIDENCE / f"wti-{node}-{transport}.log").open("ab")
            handles.append(log)
            process = subprocess.Popen(
                [
                    "ip",
                    "netns",
                    "exec",
                    f"soa-{node}",
                    os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    "run",
                    "--name",
                    name,
                    "-p",
                    "16789",
                    "--bind",
                    address,
                    "--catalog",
                    str(catalog_path),
                    "--config",
                    str(config_path),
                ],
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            objects.append(
                S2sBaseClass(
                    {
                        service: {"role": node, "name": service, "transport": transport}
                        for service in SERVICES
                    },
                    operator=SOAOperator(name, host=address),
                    attach=True,
                )
            )
        server, client = objects
        for name in SERVICES:
            assert client.wait_for_service_reconnect(f"{name}_client", timeout=10)
        time.sleep(0.3)
        yield server, client, transport
    finally:
        for partner in objects:
            partner.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.exception(
                    "WTI 验收原生进程未及时退出",
                    extra={"operation": "test.wti.cleanup"},
                )
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()


@pytest.mark.parametrize("auto", [False, True])
def test_sat_wti_six_helpers_over_veth(wti_partners, auto):
    server, client, _ = wti_partners
    name = "WTIAutoDriveService" if auto else "WTIService"
    server_key, client_key = f"{name}_server", f"{name}_client"
    current = {
        "WarningMsgList": [{"name": "LowVoltage", "info": "9"}],
        "TelltaleList": [{"name": "Brake", "state": "1"}],
    }

    def reply(key, message):
        if message["action"] == "request":
            assert json.loads(message["args"]) == {}
            server.send_method_response(
                key,
                message["function"],
                current[message["function"][3:]],
                request_id=message["request_id"],
            )

    server.register_callback(server_key, reply)
    # 普通断言允许历史事件；Getter 的数组输出与事件的 list 字段一致。
    for suffix, value in current.items():
        server.send_event_notify(server_key, suffix, {"list": value})
    assert client.ck_wti_warning_and_resp("LowVoltage", 9, timeout=1, wti_auto=auto)
    assert client.ck_wti_telltale_and_resp("Brake", 1, timeout=1, wti_auto=auto)
    # 新事件断言必须等待观测开始后的线上消息，不能用同值历史通过。
    for suffix, hint, value, helper in (
        ("WarningMsgList", "LowVoltage", 9, client.ck_wti_coming_warning_and_resp),
        ("TelltaleList", "Brake", 1, client.ck_wti_coming_telltale_and_resp),
    ):
        server.send_event_notify(server_key, suffix, {"list": current[suffix]})
        client.chk_notify(client_key, suffix, {"list": current[suffix]}, timeout=1)
        timer = threading.Timer(
            0.05,
            server.send_event_notify,
            args=(server_key, suffix, {"list": current[suffix]}),
        )
        timer.start()
        try:
            assert helper(hint, value, timeout=0.5, deviation=0, wti_auto=auto)
        finally:
            timer.join(timeout=2)
    # 无特定提示只检查目标，不消费或禁止其他提示；Getter 仍可读到当前状态。
    client.empty_event_list(client_key)
    for suffix, field in (("WarningMsgList", "info"), ("TelltaleList", "state")):
        server.send_event_notify(
            server_key, suffix, {"list": [{"name": "Other", field: "0"}]}
        )
        client.chk_notify(client_key, suffix, {"list": [{"name": "Other"}]}, timeout=1)
    assert client.ck_wti_no_warning_and_ck_resp(
        "LowVoltage", 9, timeout=0.03, wti_auto=auto
    )
    assert client.ck_wti_no_telltale_and_ck_resp(
        "Brake", 1, timeout=0.03, wti_auto=auto
    )
    assert (
        client.return_latest_event(client_key, "WarningMsgList", False)["list"][0][
            "name"
        ]
        == "Other"
    )
    server.send_event_notify(
        server_key, "WarningMsgList", {"list": current["WarningMsgList"]}
    )
    client.chk_notify(
        client_key, "WarningMsgList", {"list": current["WarningMsgList"]}, timeout=1
    )
    with pytest.raises(AssertionError, match="事件"):
        client.ck_wti_no_warning_and_ck_resp(
            "LowVoltage", 9, timeout=0.03, wti_auto=auto
        )
    client.ck_method_timeout()
