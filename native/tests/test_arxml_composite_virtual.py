"""ARXML 嵌套类型与部署驱动的真实 UDP/TCP；目录不是手写 Codec schema。"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree
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
    transport,
    byte_order,
    struct_width,
    array_width,
    alignment=8,
    vsa_bits=None,
    large_count=None,
    stimulus_check=None,
):
    port = (
        (30530 if alignment == 8 else 30730 + 64 * (alignment == 64))
        + (transport == "tcp")
        + 2 * (byte_order == "little")
        + 4 * WIDTHS.index(struct_width)
        + 16 * WIDTHS.index(array_width)
    )
    content = (
        WORKSPACE / "backend/tests/fixtures/composite_service.arxml"
    ).read_bytes()
    value = deepcopy(VALUE)
    if stimulus_check is not None:
        # 动态专项使用独立端口与实际源 EventGroup=7，不混入原黄金 PCAP。
        port = 31100 + (transport == "tcp") + 2 * (byte_order == "little")
        content = content.replace(
            b"<EVENT-GROUP-ID>1</EVENT-GROUP-ID>", b"<EVENT-GROUP-ID>7</EVENT-GROUP-ID>"
        )
    if vsa_bits is not None:
        port = (
            30900
            + (transport == "tcp")
            + 2 * (byte_order == "little")
            + 4 * (8, 16, 32).index(vsa_bits)
        )
        root = etree.fromstring(content)
        extra = etree.fromstring(
            (WORKSPACE / "backend/tests/fixtures/vsa_type.xml").read_bytes()
        )
        extra.xpath(".//*[local-name()='BASE-TYPE-SIZE']")[0].text = str(vsa_bits)
        if large_count is not None:
            assert transport == "tcp" and vsa_bits == 32
            port = 31000 + (byte_order == "little")
            extra.xpath(".//*[local-name()='ARRAY-SIZE']")[0].text = "1048576"
            value["samples"] = [0x1234, 0xABCD] * (large_count // 2)
        root.xpath("//*[local-name()='AR-PACKAGE']/*[local-name()='ELEMENTS']")[
            0
        ].extend(extra)
        for reference in root.xpath("//*[local-name()='IMPLEMENTATION-DATA-TYPE-REF']"):
            if reference.text == "/Composite/FixedWords":
                reference.text = "/Composite/LinearWords"
        content = etree.tostring(root)
        value["samples"] = {
            "validElements": len(value["samples"]),
            "words": value["samples"],
        }
    content = content.replace(b"<ALIGNMENT>8", f"<ALIGNMENT>{alignment}".encode())
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
    diagnostic_dir = None
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
                / f"composite-{alignment}-{transport}-{byte_order}-{struct_width}-{array_width}-{role}-{time.time_ns()}"
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
            if role == "client":
                diagnostic_dir = directory
        server, client = partners
        key = "EnvelopeService_client"
        assert client.wait_for_service_reconnect(key, timeout=10)
        calls = []
        state = deepcopy(value)
        changed = {**deepcopy(value), "tag": 8}

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
            key, "Transform", {"payload": value}, timeout=3
        ) == {"out": {"payload": value}}
        assert client.send_request_and_return_resp(
            key, "MappedScalar", {"value": 0x1234}, timeout=3
        ) == {"out": {"value": 0x1234}}
        # 不把 START 当 SD 订阅完成；真实周期通知必须交付后停止，不能用历史默认值通过。
        server.send_event_notify_thread_start(
            "EnvelopeService_server", "EnvelopeChanged", value, 0.05
        )
        assert client.chk_notify(
            key, "EnvelopeChanged", value, timeout=5, fuzz_match=False
        )
        server.send_event_notify_thread_stop("EnvelopeService_server")
        # 先以真实字段通知证明订阅就绪，再对一次性 Setter 更新做断言，不重试业务。
        server.send_event_notify_thread_start(
            "EnvelopeService_server", "EnvelopeState", value, 0.05
        )
        assert client.chk_notify(
            key, "EnvelopeState", value, timeout=5, fuzz_match=False
        )
        server.send_event_notify_thread_stop("EnvelopeService_server")
        assert client.send_request_and_return_resp(
            key, "GetEnvelopeState", {}, timeout=3
        ) == {"out": value}
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
            ("Transform", {"payload": value}),
            ("MappedScalar", {"value": 0x1234}),
            ("GetEnvelopeState", {}),
            ("SetEnvelopeState", {"EnvelopeState": changed}),
            ("GetEnvelopeState", {}),
        ]
        assert calls == expected_calls
        # 非法固定长度/上界必须在原生编码阶段拒绝，不能产生伪成功或默认重放。
        invalid_values = [
            ("samples", [1]),
            ("bytes", [1, 2, 3, 4]),
            ("matrix", [[1, 2], [4, 5, 6]]),
        ]
        if vsa_bits is not None:
            for words in ([], [1, 2, 3]):
                variant = {
                    **deepcopy(value),
                    "samples": {"validElements": len(words), "words": words},
                }
                assert client.send_request_and_return_resp(
                    key, "Transform", {"payload": variant}, timeout=3
                ) == {"out": {"payload": variant}}
                expected_calls.append(("Transform", {"payload": variant}))
            invalid_values.extend(
                ("samples", invalid)
                for invalid in (
                    {"validElements": 1, "words": [1, 2]},
                    {"validElements": -1, "words": []},
                    {
                        "validElements": 1048577 if large_count is not None else 4,
                        "words": [1, 2, 3, 4],
                    },
                    {"validElements": True, "words": [1]},
                )
            )
        for field, invalid in invalid_values:
            bad = {**deepcopy(value), "tag": 99, field: invalid}
            client.send_method_request(key, "Transform", {"payload": bad})
            deadline = time.monotonic() + 3
            while True:
                remaining = deadline - time.monotonic()
                assert remaining > 0, "非法数组请求未在限定时限内收到失败回执"
                response = client.partner_infos[key].resp_queue.get(timeout=remaining)
                if response["failtype"] != "FAILTYPE_SUCCESS":
                    break
            assert calls == expected_calls
        if stimulus_check is not None:
            stimulus_check(server, client, value, byte_order, bundle)
    except Exception:
        if large_count is not None:
            logger.exception("大数组复合业务失败")
            frames = sys._current_frames()
            stacks = {
                thread.name: traceback.format_stack(frames[thread.ident])
                for thread in threading.enumerate()
                if thread.ident in frames
            }
            logger.error("大数组失败时 Python 线程堆栈：%s", stacks)
            if diagnostic_dir is not None:
                (diagnostic_dir / "python-failure.json").write_text(
                    json.dumps(
                        {"thread_stacks": stacks},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        raise
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


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("alignment", [32, 64])
@pytest.mark.parametrize("struct_width,array_width", [(1, 1), (2, 2)])
def test_arxml_variable_alignment_with_absolute_prefix_offsets_over_veth(
    transport, byte_order, alignment, struct_width, array_width
):
    # 同时验证 RPC、事件、字段 Getter/Setter/通知；保留相同 SAT 字典初始化入口。
    test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth(
        transport, byte_order, struct_width, array_width, alignment
    )


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("bits", [8, 16, 32])
def test_arxml_vsa_source_dictionary_rpc_event_field_and_rejections_over_veth(
    transport, byte_order, bits
):
    test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth(
        transport, byte_order, 0, bits // 8, 64, bits
    )
