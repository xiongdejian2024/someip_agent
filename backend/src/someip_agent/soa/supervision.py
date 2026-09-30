"""仅监督本实例拥有的原生进程；不重放业务请求，不管理远端附着进程。"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable

from .operator import NativeRuntimeError, SOAOperator

logger = logging.getLogger(__name__)


class NativeSupervisor:
    def __init__(
        self,
        operator: SOAOperator,
        recover: Callable[[], None],
        interval: float = 5,
        restart_limit: int = 3,
        restart_window: float = 60,
    ) -> None:
        if (
            not math.isfinite(interval)
            or interval <= 0
            or restart_limit < 1
            or not math.isfinite(restart_window)
            or restart_window <= 0
        ):
            raise ValueError("进程监督周期、重启次数和窗口必须为正数")
        self.operator = operator
        self.recover = recover
        self.interval = interval
        self.restart_limit = restart_limit
        self.restart_window = restart_window
        self.restart_count = 0
        self.state = "idle"
        self.last_error: str | None = None
        self._attempts: deque[float] = deque()
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.state = "watching"
        self.thread = threading.Thread(target=self._run, daemon=True, name="soa-native-supervisor")
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                process = self.operator.process
                if process is None:
                    raise NativeRuntimeError("监督对象没有本实例拥有的原生进程")
                code = process.poll()
                if code is None:
                    continue
                # 沿用 SAT：正常退出、重复启动错误及显式 kill 不自动恢复。
                if code in {0, 1, -9}:
                    self.state = "stopped"
                    logger.info(
                        "原生进程退出，不自动重启",
                        extra={"operation": "soa.supervisor.stop", "exit_code": code},
                    )
                    return
                now = time.monotonic()
                while self._attempts and now - self._attempts[0] >= self.restart_window:
                    self._attempts.popleft()
                if len(self._attempts) >= self.restart_limit:
                    raise NativeRuntimeError("原生进程反复异常，重启次数超过窗口限制")
                self._attempts.append(now)
                self.state = "recovering"
                logger.error(
                    "原生进程异常退出，恢复活动成员配置",
                    extra={"operation": "soa.supervisor.recover", "exit_code": code},
                )
                self.recover()
                if not self._stop.is_set():
                    self.restart_count += 1
                    self.state = "watching"
                    logger.info(
                        "原生进程与活动成员已恢复",
                        extra={
                            "operation": "soa.supervisor.ready",
                            "restart_count": self.restart_count,
                        },
                    )
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                logger.exception(
                    "原生监督恢复失败，停止自动恢复", extra={"operation": "soa.supervisor.failed"}
                )
                self.state = "failed"
                return
        self.state = "stopped"
