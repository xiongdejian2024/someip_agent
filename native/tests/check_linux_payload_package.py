"""宿主只用标准库，经HTTP核验无Python运行镜像内的真实发行包解码。"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("干净发行包原生信号验收")

# 用已有dpkt独立生成的RAW IPv4 PCAP；SOME/IP头及期望值是人工黄金字节，
# 不调用产品Codec生成期望，也不向干净镜像安装dpkt/Python或注入产品源码。
SCALAR_PCAP = bytes.fromhex(
    "d4c3b2a1020004000000000000000000dc05000065000000"
    "00f15365000000003000000030000000"
    "4500003000000000401166210a4d00020a4d0001a0367796001cfb99"
    "123480010000000c001100170101020042480000"
    "01f15365000000002d0000002d000000"
    "4500002d00000000401166240a4d00020a4d0001a036779600193eea"
    "12348001000000090011001701010200ff"
    "02f15365000000003000000030000000"
    "4500003000000000401166210a4d00020a4d0001a0367796001cfb95"
    "123480010000000c0011001701010200424c0000"
)
NESTED_PCAP = bytes.fromhex(
    "d4c3b2a1020004000000000000000000dc05000065000000"
    "04f15365000000004900000049000000"
    "4500004900000000401166080a4d00020a4d0001a03677960035f08e"
    "34568001000000250011001701010200"
    "001b0700041234abcd00020102000a000301020300030405060002fffe"
)
NESTED_VALUE = {
    "tag": 7,
    "samples": [0x1234, 0xABCD],
    "bytes": [1, 2],
    "matrix": [[1, 2, 3], [4, 5, 6]],
    "nested": {"temperature": -2},
}


class ResponseSocket:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def makefile(self, *_args: object) -> io.BytesIO:
        return io.BytesIO(self.raw)


class PackageClient:
    """复用标准库与 Docker stdin 访问隔离包，不向运行镜像安装客户端或源码。"""

    def __init__(self, container: str, root: Path) -> None:
        self.container = container
        self.root = root

    def request(
        self,
        path: str,
        name: str,
        content: bytes | None = None,
        *,
        json_body: object | None = None,
    ) -> object:
        if content is not None and json_body is not None:
            raise ValueError("请求不能同时为文件上传与 JSON")
        container, root = self.container, self.root
        boundary = "someip-agent-clean-package-golden"
        body = b""
        content_type = "application/json"
        if content is not None:
            assert boundary.encode() not in content
            content_type = f"multipart/form-data; boundary={boundary}"
            body = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                    f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                ).encode()
                + content
                + f"\r\n--{boundary}--\r\n".encode()
            )
            (root / name).write_bytes(content)
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        method = "GET" if content is None and json_body is None else "POST"
        headers = (
            f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n"
            f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode("ascii")
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "bash",
                "-c",
                "exec 3<>/dev/tcp/127.0.0.1/8765; cat >&3; cat <&3",
            ],
            input=headers + body,
            capture_output=True,
            timeout=45,
            check=False,
        )
        (root / f"{name}.http").write_bytes(result.stdout)
        (root / f"{name}.stderr").write_bytes(result.stderr)
        result.check_returncode()
        response = http.client.HTTPResponse(ResponseSocket(result.stdout))
        response.begin()
        data = response.read()
        if response.status not in {200, 201}:
            raise RuntimeError(f"发行包HTTP请求失败：{path} {response.status} {data!r}")
        return json.loads(data)


def verify(container: str, root: Path, fixtures: Path) -> None:
    request = PackageClient(container, root).request
    logger.info("步骤1/2：真实HTTP导入ARXML与标量黄金PCAP，核对有效帧及截断错误")
    model = request(
        "/api/v1/arxml/import",
        "vehicle_service.arxml",
        (fixtures / "vehicle_service.arxml").read_bytes(),
    )
    assert model["services"][0]["name"] == "VehicleStatus"
    imported = request("/api/v1/pcap/import", "scalar-golden.pcap", SCALAR_PCAP)
    assert imported["runtime"] == "vsomeip" and imported["someip_count"] == 3
    messages = request(
        "/api/v1/monitor/messages?service_id=4660&method_id=32769",
        "scalar-monitor",
    )
    assert len(messages) == 3
    assert [item["payload_hex"] for item in messages] == ["42480000", "ff", "424c0000"]
    assert messages[0]["signal_values"] == {"SpeedChanged": 50}
    assert messages[2]["signal_values"] == {"SpeedChanged": 51}
    for message in (messages[0], messages[2]):
        assert message["metadata"]["signal_decoder"] == "someip-agent-native-codec"
        assert message["metadata"]["signal_schema"] == "event:SpeedChanged"
        assert "signal_decode_error" not in message["metadata"]
    assert messages[1]["signal_values"] == {}
    assert "signal_decode_error" in messages[1]["metadata"]
    assert "signal_decoder" not in messages[1]["metadata"]
    assert all(item["return_code"] == 0 for item in messages)

    logger.info("步骤2/2：切换现有嵌套ARXML黄金夹具，核对结构体与多维数组")
    model = request(
        "/api/v1/arxml/import",
        "composite_service.arxml",
        (fixtures / "composite_service.arxml").read_bytes(),
    )
    assert model["services"][0]["name"] == "EnvelopeService"
    imported = request("/api/v1/pcap/import", "nested-golden.pcap", NESTED_PCAP)
    assert imported["someip_count"] == 1
    messages = request(
        "/api/v1/monitor/messages?service_id=13398&method_id=32769",
        "nested-monitor",
    )
    assert len(messages) == 1
    assert messages[0]["signal_values"] == {"EnvelopeChanged": NESTED_VALUE}
    assert messages[0]["metadata"]["signal_decoder"] == "someip-agent-native-codec"
    assert "signal_decode_error" not in messages[0]["metadata"]
    (root / "payload-result.json").write_text(
        json.dumps(
            {
                "status": "verified",
                "network": "none",
                "system_python": False,
                "decoder": "someip-agent-native-codec",
                "valid_frames": 3,
                "invalid_payload_frames": 1,
                "nested_json_preserved": True,
                "scope": "人工ARXML/PCAP黄金夹具的真实发行包HTTP调用，不证明车型业务源运行",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.info("干净发行包的原生信号解码、逐帧错误及嵌套JSON均验证通过")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="核验干净Linux发行包的原生信号解码")
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
        logger.exception("干净发行包原生信号解码验收失败")
        raise
