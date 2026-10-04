"""跨线程网卡写入与异步原生生命周期互斥；不跨 await 持有线程锁。"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class NetworkTaskConflict(ValueError):
    pass


class NetworkTaskGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._operations = 0
        self._configuring = False

    @contextmanager
    def task_operation(self) -> Iterator[None]:
        with self._lock:
            if self._configuring:
                logger.warning("网卡环境正在变更，拒绝原生生命周期操作")
                raise NetworkTaskConflict("网卡环境正在变更，请等待完成后再启动或停止任务")
            self._operations += 1
        try:
            yield
        finally:
            with self._lock:
                self._operations -= 1

    @contextmanager
    def configuration(self) -> Iterator[None]:
        with self._lock:
            if self._configuring or self._operations:
                logger.warning("原生生命周期操作尚未结束，拒绝网卡变更")
                raise NetworkTaskConflict("服务、仿真或监听正在启停，请等待资源完全释放")
            self._configuring = True
        try:
            yield
        finally:
            with self._lock:
                self._configuring = False

    def status(self) -> dict[str, int | bool]:
        with self._lock:
            return {"lifecycle_operations": self._operations, "configuring": self._configuring}
