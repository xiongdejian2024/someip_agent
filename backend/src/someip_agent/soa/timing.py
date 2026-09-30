"""按请求关联的 SAT 方法超时审计；耗时使用单调时钟，不把它当网卡时延。"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

from .operator import NativeRuntimeError

logger = logging.getLogger(__name__)


@dataclass
class RequestTiming:
    member: str
    function: str
    started: float
    limit: float
    recorded: bool = False


class MethodTimingAudit:
    def __init__(self, capacity: int = 4096, record_capacity: int = 1000) -> None:
        if capacity < 1 or record_capacity < 1:
            raise ValueError("方法耗时审计容量必须为正数")
        self.capacity = capacity
        self.record_capacity = record_capacity
        self.records: list[tuple[str, float, float]] = []
        self.dropped_records = 0
        self._pending: dict[str, RequestTiming] = {}
        self._lock = threading.Lock()

    def begin(self, correlation: str, member: str, function: str, limit: float) -> None:
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("方法耗时阈值必须为正有限数")
        with self._lock:
            if len(self._pending) >= self.capacity:
                raise NativeRuntimeError("同步请求耗时审计容量已满，拒绝继续发送")
            if correlation in self._pending:
                raise ValueError("同步请求关联 ID 重复")
            self._pending[correlation] = RequestTiming(member, function, time.monotonic(), limit)

    def _record(self, request: RequestTiming, elapsed: float, reason: str) -> None:
        if request.recorded:
            return
        request.recorded = True
        if len(self.records) >= self.record_capacity:
            self.records.pop(0)
            self.dropped_records += 1
        self.records.append((request.function, time.time(), elapsed))
        logger.error(
            "同步方法超时: %s，耗时 %.6fs，阈值 %.6fs，原因 %s",
            request.function,
            elapsed,
            request.limit,
            reason,
            extra={"operation": "soa.method.timeout", "member": request.member},
        )

    def complete(self, correlation: str, failtype: str = "FAILTYPE_SUCCESS") -> None:
        with self._lock:
            request = self._pending.pop(correlation, None)
            if request is None:
                return
            elapsed = time.monotonic() - request.started
            if failtype == "FAILTYPE_TIMEOUT" or elapsed > request.limit:
                self._record(
                    request,
                    elapsed,
                    "原生请求超时" if failtype == "FAILTYPE_TIMEOUT" else "响应超过阈值",
                )

    def abandon(self, correlation: str) -> None:
        with self._lock:
            self._pending.pop(correlation, None)

    def cancel_member(self, member: str) -> None:
        with self._lock:
            self._pending = {
                key: request for key, request in self._pending.items() if request.member != member
            }

    def reset_records(self, records: list[tuple[str, float, float]]) -> None:
        with self._lock:
            self.records[:] = records[-self.record_capacity :]
            self.dropped_records = max(0, len(records) - self.record_capacity)
            for request in self._pending.values():
                request.recorded = False

    def check(self) -> None:
        with self._lock:
            now = time.monotonic()
            for request in self._pending.values():
                elapsed = now - request.started
                if elapsed > request.limit:
                    self._record(request, elapsed, "检查时仍未收到响应")
            records, dropped = list(self.records), self.dropped_records
        if records or dropped:
            raise AssertionError(f"接口调用存在超时: {records}；更早记录溢出数={dropped}")
