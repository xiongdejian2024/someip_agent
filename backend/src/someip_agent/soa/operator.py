from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from .ipc import recv_data_from, send_data_to

logger = logging.getLogger(__name__)


class NativeRuntimeError(RuntimeError):
    pass


class NativeOperationError(NativeRuntimeError):
    """进程已响应但拒绝控制操作，不等同于进程缺失或断开。"""


class SOAOperator:
    """SOAOperator 的控制接口与 SAT 一致，增加原生配置/远程主机参数。"""

    def __init__(
        self,
        name: str | None = None,
        operator_port: int = 16789,
        *,
        binary: str | Path | None = None,
        catalog: str | Path | None = None,
        config: str | Path | None = None,
        host: str = "127.0.0.1",
        log_path: str | Path | None = None,
        mode: Literal["services", "network"] = "services",
    ) -> None:
        self.name = name or "soa_partner"
        self.operator_port = operator_port
        self.binary = str(binary or os.environ.get("SOMEIP_AGENT_NATIVE_BINARY", "soa_partner"))
        self.catalog = catalog or os.environ.get("SOMEIP_AGENT_NATIVE_CATALOG")
        self.config = config or os.environ.get("SOMEIP_AGENT_NATIVE_CONFIG")
        self.host = host
        self.log_path = Path(log_path) if log_path else None
        self.mode = mode
        self.process: subprocess.Popen[bytes] | None = None
        self.pid: int | None = None
        self.tcp_socket: socket.socket | None = None
        self._lock = Lock()
        self._stop_lock = Lock()
        self._log: Any = None

    def run_operator(self, domin: str = "acu") -> None:
        if self.process is not None and self.process.poll() is None:
            return
        executable = shutil.which(self.binary)
        if executable is None:
            raise NativeRuntimeError(f"未找到 vsomeip 二进制 {self.binary}，请先构建 native")
        if self.mode == "services" and (not self.catalog or not self.config):
            raise NativeRuntimeError("必须配置原生服务目录和 vsomeip 配置文件")
        command = [
            executable,
            "run",
            "-p",
            str(self.operator_port),
            "--name",
            self.name,
            "-d",
            domin,
        ]
        if self.mode == "network":
            command.append("--network")
        else:
            command.extend(["--catalog", str(self.catalog), "--config", str(self.config)])
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("ab")
        try:
            if self.operator_port == 0 and self.log_path is None:
                raise NativeRuntimeError("动态控制端口需要 log_path 读取 native.ready")
            self.process = subprocess.Popen(command, stdout=self._log, stderr=self._log)
            self.pid = self.process.pid
            logger.info(
                "vsomeip 原生进程已启动", extra={"operation": "native.start", "pid": self.pid}
            )
        except Exception:
            logger.exception("vsomeip 原生进程启动失败", extra={"operation": "native.start"})
            if self._log:
                self._log.close()
            raise

    def create_socket(self, timeout: float = 10) -> None:
        if self.tcp_socket is not None:
            self.tcp_socket.close()
            self.tcp_socket = None
        deadline = time.monotonic() + timeout
        while True:
            if self.process is not None and self.process.poll() is not None:
                raise NativeRuntimeError(f"vsomeip 进程已退出，返回码 {self.process.returncode}")
            try:
                if self.operator_port == 0:
                    assert self.log_path is not None
                    ready_port = self._read_ready_port()
                    if ready_port is not None:
                        self.operator_port = ready_port
                    if self.operator_port == 0:
                        if time.monotonic() >= deadline:
                            raise NativeRuntimeError("原生动态端口未在时限内就绪")
                        time.sleep(0.05)
                        continue
                conn = socket.create_connection((self.host, self.operator_port), timeout=1)
                conn.settimeout(timeout)
                self.tcp_socket = conn
                logger.info("vsomeip 控制 socket 已连接", extra={"operation": "native.connect"})
                return
            except (ConnectionRefusedError, TimeoutError):
                if time.monotonic() >= deadline:
                    logger.exception(
                        "连接 vsomeip 控制服务超时", extra={"operation": "native.connect"}
                    )
                    raise
                time.sleep(0.05)

    def _read_ready_port(self) -> int | None:
        """只消费完整就绪记录；兼容旧二进制在 JSON 后拼接协议栈日志的情况。"""
        assert self.log_path is not None
        decoder = json.JSONDecoder()
        try:
            for line in self.log_path.read_text(errors="replace").splitlines(keepends=True):
                if not line.startswith("{") or not line.endswith("\n"):
                    continue
                record, end = decoder.raw_decode(line)
                if not isinstance(record, dict) or record.get("operation") != "native.ready":
                    continue
                port = record.get("port")
                if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
                    raise NativeRuntimeError("原生就绪记录的控制端口非法")
                if line[end:].strip():
                    logger.warning(
                        "原生就绪 JSON 后拼接了其他日志，按 JSON 文档边界读取控制端口",
                        extra={"operation": "native.ready.mixed_log", "port": port},
                    )
                return port
            return None
        except Exception:
            logger.exception("读取原生就绪记录失败", extra={"operation": "native.ready.read"})
            raise

    def send_request(self, function: str, args: Any = None, print_result: bool = True) -> Any:
        if self.tcp_socket is None:
            raise NativeRuntimeError("控制 socket 尚未连接")
        try:
            with self._lock:
                send_data_to(
                    self.tcp_socket, {"action": "request", "function": function, "args": args}
                )
                response = json.loads(recv_data_from(self.tcp_socket))
            if response.get("failtype") != "FAILTYPE_SUCCESS":
                raise NativeOperationError(response.get("error", f"原生操作失败: {function}"))
            result = json.loads(response["result"])
            if print_result:
                logger.info("原生控制操作完成", extra={"operation": f"native.{function}"})
            return result
        except Exception:
            logger.exception("原生控制操作失败", extra={"operation": f"native.{function}"})
            raise

    def stop_operator(self) -> None:
        # 监控故障和生命周期停止可能同时发生，清理必须幂等且串行。
        with self._stop_lock:
            self._stop_operator_locked()

    def _stop_operator_locked(self) -> None:
        if self.tcp_socket:
            self.tcp_socket.close()
            self.tcp_socket = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.exception("原生进程退出超时", extra={"operation": "native.stop"})
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log:
            self._log.close()
            self._log = None
        logger.info("原生控制连接已关闭", extra={"operation": "native.stop"})
