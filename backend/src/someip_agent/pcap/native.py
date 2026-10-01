"""独立原生离线任务的本地控制通道；不读取报文头或创建车辆网络端点。"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.soa.ipc import recv_data_from
from someip_agent.soa.operator import NativeOperationError, NativeRuntimeError, SOAOperator

logger = logging.getLogger(__name__)


class PcapImportError(ValueError):
    """离线文件、解码结果或资源边界不满足导入契约。"""


def records(
    content: bytes, settings: Settings, timeout: float
) -> Generator[dict[str, Any], None, None]:
    if len(content) > settings.max_upload_bytes:
        raise PcapImportError("抓包文件超过大小限制")
    directory = settings.data_dir / "native-pcap"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-", dir=directory) as temporary:
        path = Path(temporary) / "capture.pcap"
        path.write_bytes(content)
        operator = SOAOperator(
            "pcap",
            operator_port=0,
            binary=settings.native_binary,
            mode="network",
            log_path=directory / f"{uuid4()}.log",
        )
        try:
            operator.run_operator()
            operator.create_socket()
            operator.send_request("pcap_import", {"path": str(path.resolve())})
            if operator.tcp_socket is None:
                raise NativeRuntimeError("原生 PCAP 控制 socket 未连接")
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PcapImportError("原生 PCAP 导入超过时间限制")
                operator.tcp_socket.settimeout(min(10, remaining))
                record = json.loads(recv_data_from(operator.tcp_socket))
                if not isinstance(record, dict) or record.get("action") not in {
                    "pcap_frame",
                    "pcap_done",
                }:
                    raise PcapImportError("原生 PCAP 返回了非法记录类型")
                if record.get("error"):
                    raise PcapImportError(str(record["error"]))
                yield record
                if record["action"] == "pcap_done":
                    return
        except NativeOperationError as exc:
            logger.exception("原生 PCAP 拒绝文件", extra={"operation": "pcap.native.open"})
            raise PcapImportError(str(exc)) from exc
        except (EOFError, OSError) as exc:
            logger.exception("原生 PCAP 通道断开", extra={"operation": "pcap.native.read"})
            raise NativeRuntimeError("原生 PCAP 导入未完整结束") from exc
        except (ValueError, TypeError) as exc:
            logger.exception("原生 PCAP 数据非法", extra={"operation": "pcap.native.read"})
            if isinstance(exc, PcapImportError):
                raise
            raise PcapImportError("原生 PCAP 记录不能解码") from exc
        finally:
            operator.stop_operator()
