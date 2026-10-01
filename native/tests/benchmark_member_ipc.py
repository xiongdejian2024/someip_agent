"""同机器复核成员 JSON 分片解码热点，不冒充网络吞吐或整条仿真性能。"""

import argparse
import hashlib
import json
import logging
import subprocess
import time
import types
from pathlib import Path
from unittest.mock import patch

from someip_agent.soa import ipc

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("拒绝覆盖既有 IPC 微测证据")
    try:
        revision = subprocess.run(
            [
                "git",
                "rev-parse",
                "--verify",
                "--end-of-options",
                args.baseline_revision + "^{commit}",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        source = subprocess.run(
            [
                "git",
                "show",
                revision + ":backend/src/someip_agent/soa/ipc.py",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        # 仅执行用户仓库的确定提交，不加载远端代码，不写回产品模块。
        baseline = types.ModuleType("ipc_baseline")
        exec(compile(source, "提交基线 IPC", "exec"), baseline.__dict__)  # noqa: S102
        expected = {
            "args": json.dumps({"values": [4660, 43981] * 35000}),
            "payload_hex": "1234abcd" * 35000,
        }
        raw = json.dumps(expected).encode()
        chunks = [raw[i : i + 8192] for i in range(0, len(raw), 8192)]

        class Input:
            def __init__(self):
                self.parts = iter(chunks)

            def recv(self, _size):
                return next(self.parts, b"")

        class CountingDecoder(json.JSONDecoder):
            calls = 0

            def raw_decode(self, *positional, **keywords):
                CountingDecoder.calls += 1
                return super().raw_decode(*positional, **keywords)

        results = {"baseline": [], "current": []}
        # 交错而不是先跑完某一版本；三轮只是局部热点观测，不作显著性结论。
        with patch.object(json, "JSONDecoder", CountingDecoder):
            for _ in range(3):
                for name, function in (
                    ("baseline", baseline.member_messages),
                    ("current", ipc.member_messages),
                ):
                    CountingDecoder.calls = 0
                    begin = time.perf_counter()
                    actual = list(function(Input()))
                    elapsed = (time.perf_counter() - begin) * 1000
                    assert actual == [expected]
                    results[name].append(
                        {
                            "elapsed_ms": elapsed,
                            "json_decode_attempts": CountingDecoder.calls,
                        }
                    )
        report = {
            "scope": "内存分片热点微测，不代表线上吞吐/长稳/纯解释器开销",
            "baseline_commit": revision,
            "document_bytes": len(raw),
            "chunks": len(chunks),
            "current_module": ipc.__file__,
            "current_module_sha256": hashlib.sha256(
                Path(ipc.__file__).read_bytes()
            ).hexdigest(),
            "results": results,
        }
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
        logger.info("成员 JSON 热点微测完成：%s", report)
    except Exception:
        logger.exception("成员 JSON 热点微测失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
