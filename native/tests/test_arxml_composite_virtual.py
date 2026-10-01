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
    "matrix": [[1, 2, 3], [4, 5, 6]],
    "nested": {"temperature": -2},
}
WIDTHS = (0, 1, 2, 4)


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("struct_width", WIDTHS)
@pytest.mark.parametrize("array_width", WIDTHS)
def test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth(
    transport, byte_order, struct_width, array_width
):
    port = (
        30530
        + (transport == "tcp")
        + 2 * (byte_order == "little")
        + 4 * WIDTHS.index(struct_width)
        + 16 * WIDTHS.index(array_width)
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
    content = content.replace(
        b"<SIZE-OF-ARRAY-LENGTH-FIELD>2",
        f"<SIZE-OF-ARRAY-LENGTH-FIELD>{array_width}".encode(),
    )
    if array_width == 0:
        # 零前缀只用于固定数组，不把非法变长数组宽度 0 当支持场景。
        content = content.replace(
            b"<ARRAY-SIZE>3</ARRAY-SIZE><ARRAY-SIZE-SEMANTICS>VARIABLE-SIZE",
            b"<ARRAY-SIZE>2</ARRAY-SIZE><ARRAY-SIZE-SEMANTICS>FIXED-SIZE",
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
                / f"composite-{transport}-{byte_order}-{struct_width}-{array_width}-{role}-{time.time_ns()}"
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
        state = deepcopy(VALUE)
        changed = {**deepcopy(VALUE), "tag": 8}

        def reply(server_key, message):
            if message["action"] == "request":
                args = json.loads(message["args"])
                calls.append((message["function"], args))
                if message["function"] == "GetEnvelopeState":
                    out = deepcopy(state)
                elif message["function"] == "SetEnvelopeState":
                    assert args == {"EnvelopeState": changed}
                    state.update(deepcopy(args["EnvelopeState"]))
                    out = deepcopy(state)
                    server.send_event_notify(server_key, "EnvelopeState", out)
                else:
                    out = args
                server.send_method_response(
                    server_key,
                    message["function"],
                    out,
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
        # 先以真实字段通知证明订阅就绪，再对一次性 Setter 更新做断言，不重试业务。
        server.send_event_notify_thread_start(
            "EnvelopeService_server", "EnvelopeState", VALUE, 0.05
        )
        assert client.chk_notify(
            key, "EnvelopeState", VALUE, timeout=5, fuzz_match=False
        )
        server.send_event_notify_thread_stop("EnvelopeService_server")
        assert client.send_request_and_return_resp(
            key, "GetEnvelopeState", {}, timeout=3
        ) == {"out": VALUE}
        assert client.send_request_and_return_resp(
            key, "SetEnvelopeState", {"EnvelopeState": changed}, timeout=3
        ) == {"out": changed}
        assert client.chk_notify(
            key, "EnvelopeState", changed, timeout=5, fuzz_match=False
        )
        assert client.send_request_and_return_resp(
            key, "GetEnvelopeState", {}, timeout=3
        ) == {"out": changed}
        expected_calls = [
            ("Transform", {"payload": VALUE}),
            ("MappedScalar", {"value": 0x1234}),
            ("GetEnvelopeState", {}),
            ("SetEnvelopeState", {"EnvelopeState": changed}),
            ("GetEnvelopeState", {}),
        ]
        assert calls == expected_calls
        # 非法固定长度/上界必须在原生编码阶段拒绝，不能产生伪成功或默认重放。
        for field, invalid in (
            ("samples", [1]),
            ("bytes", [1, 2, 3, 4]),
            ("matrix", [[1, 2], [4, 5, 6]]),
        ):
            bad = {**deepcopy(VALUE), "tag": 99, field: invalid}
            client.send_method_request(key, "Transform", {"payload": bad})
            deadline = time.monotonic() + 3
            while True:
                remaining = deadline - time.monotonic()
                assert remaining > 0, "非法数组请求未在限定时限内收到失败回执"
                response = client.partner_infos[key].resp_queue.get(timeout=remaining)
                if response["failtype"] != "FAILTYPE_SUCCESS":
                    break
            assert calls == expected_calls
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
