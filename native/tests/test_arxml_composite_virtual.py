"""ARXML 嵌套类型与部署驱动的真实 UDP/TCP；目录不是手写 Codec schema。"""

import json
import logging
import os
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest
from soa_partner.src.base_partner import S2sBaseClass
from soa_partner.src.Operator import SOAOperator
from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.soa.catalog import NativeCatalogRequest, build_native_bundle

logger = logging.getLogger(__name__)
WORKSPACE = Path(__file__).resolve().parents[2]
EVIDENCE = WORKSPACE / "build/virtual-evidence"
VALUE = {
    "tag": 7,
    "samples": [0x1234, 0xABCD],
    "bytes": [1, 2],
    "nested": {"temperature": -2},
}


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("struct_width", [0, 2])
def test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth(
    transport, byte_order, struct_width
):
    port = (
        30530
        + (transport == "tcp")
        + 2 * (byte_order == "little")
        + 4 * (struct_width == 2)
    )
    content = (
        WORKSPACE / "backend/tests/fixtures/composite_service.arxml"
    ).read_bytes()
    if byte_order == "little":
        content = content.replace(
            b"MOST-SIGNIFICANT-BYTE-FIRST", b"MOST-SIGNIFICANT-BYTE-LAST"
        )
    content = content.replace(
        b"<SIZE-OF-STRUCT-LENGTH-FIELD>2",
        f"<SIZE-OF-STRUCT-LENGTH-FIELD>{struct_width}".encode(),
    )
    model = ArxmlParser().parse(content, "composite_service.arxml")
    assert not model.warnings
    processes, handles, partners = [], [], []
    try:
        for role, address, peer, app_id in (
            ("server", "10.77.0.1", "10.77.0.2", 0x7711),
            ("client", "10.77.0.2", "10.77.0.1", 0x7722),
        ):
            name = "composite_" + role
            settings = Settings(
                _env_file=None,
                native_unicast=address,
                network_send_enabled=True,
                allowed_destinations=[peer, "239.255.77.1"],
            )
            request = NativeCatalogRequest.model_validate(
                {
                    "application_name": name,
                    "application_id": app_id,
                    "members": {
                        "EnvelopeService": {
                            "role": role,
                            "transport": transport,
                            "peer_host": peer,
                            "port": port,
                        }
                    },
                    "sd_multicast_group": "239.255.77.1",
                }
            )
            bundle = build_native_bundle(model, request, settings)
            directory = (
                EVIDENCE
                / f"composite-{transport}-{byte_order}-{struct_width}-{role}-{time.time_ns()}"
            )
            catalog, config = bundle.write(directory)
            (directory / "source.arxml").write_bytes(content)
            handle = (directory / "native.log").open("ab")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    [
                        "ip",
                        "netns",
                        "exec",
                        "soa-" + role,
                        os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                        "run",
                        "--name",
                        name,
                        "--bind",
                        address,
                        "-p",
                        "16789",
                        "--catalog",
                        str(catalog),
                        "--config",
                        str(config),
                    ],
                    stdout=handle,
                    stderr=handle,
                )
            )
            partners.append(
                S2sBaseClass(
                    bundle.members,
                    operator=SOAOperator(name, host=address),
                    attach=True,
                )
            )
        server, client = partners
        key = "EnvelopeService_client"
        assert client.wait_for_service_reconnect(key, timeout=10)
        calls = []

        def reply(server_key, message):
            if message["action"] == "request":
                args = json.loads(message["args"])
                calls.append((message["function"], args))
                server.send_method_response(
                    server_key,
                    message["function"],
                    args,
                    request_id=message["request_id"],
                )

        server.register_callback("EnvelopeService_server", reply)
        assert client.send_request_and_return_resp(
            key, "Transform", {"payload": VALUE}, timeout=3
        ) == {"out": {"payload": VALUE}}
        assert client.send_request_and_return_resp(
            key, "MappedScalar", {"value": 0x1234}, timeout=3
        ) == {"out": {"value": 0x1234}}
        # 不把 START 当 SD 订阅完成；真实周期通知必须交付后停止，不能用历史默认值通过。
        server.send_event_notify_thread_start(
            "EnvelopeService_server", "EnvelopeChanged", VALUE, 0.05
        )
        assert client.chk_notify(
            key, "EnvelopeChanged", VALUE, timeout=5, fuzz_match=False
        )
        server.send_event_notify_thread_stop("EnvelopeService_server")
        assert calls == [
            ("Transform", {"payload": VALUE}),
            ("MappedScalar", {"value": 0x1234}),
        ]
        # 非法固定长度/上界必须在原生编码阶段拒绝，不能产生伪成功或默认重放。
        bad = deepcopy(VALUE)
        bad["samples"] = [1]
        client.send_method_request(key, "Transform", {"payload": bad})
        response = client.partner_infos[key].resp_queue.get(timeout=3)
        while response["failtype"] == "FAILTYPE_SUCCESS":
            response = client.partner_infos[key].resp_queue.get(timeout=3)
        assert response["failtype"] != "FAILTYPE_SUCCESS"
        assert calls == [
            ("Transform", {"payload": VALUE}),
            ("MappedScalar", {"value": 0x1234}),
        ]
    finally:
        for partner in partners:
            partner.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.exception("复合 ARXML 验收进程退出超时")
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()
