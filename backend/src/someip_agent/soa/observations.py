"""线程安全的事件缓存观测；不从线上发包，也不吞掉非匹配历史消息。"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .operator import NativeRuntimeError


class ObservationSource(Protocol):
    event_queue: queue.Queue[dict[str, Any]]
    changed: threading.Condition
    running: bool


def cache_message(target: queue.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
    """有界 SAT 历史缓存：保留 LIFO 读取，满时淘汰最旧记录而不是最新记录。"""
    try:
        target.put_nowait(message)
    except queue.Full:
        with target.mutex:
            if len(target.queue) >= target.maxsize:
                target.queue.remove(target.queue[0])
            target._put(message)
            target.unfinished_tasks += 1
            target.not_empty.notify()


class EventObserver:
    def __init__(self, source: ObservationSource, match: Callable[[Any, Any], bool]) -> None:
        self.source = source
        self.match = match

    def _select(
        self, name: str, expected: Any, fuzzy: bool, consume: bool, after: float | None
    ) -> dict[str, Any] | None:
        target = self.source.event_queue
        with target.mutex:
            candidates: list[dict[str, Any]] = [
                message
                for message in target.queue
                if message.get("function") == name
                and (after is None or message.get("monotonic_timestamp", float("-inf")) >= after)
                and (
                    expected is None
                    or (
                        self.match(json.loads(message["args"]), expected)
                        if fuzzy
                        else json.loads(message["args"]) == expected
                    )
                )
            ]
            if not candidates:
                return None
            latest = max(
                candidates,
                key=lambda item: item.get("monotonic_timestamp", item.get("timestamp", 0)),
            )
            if consume:
                target.queue.remove(latest)
                target.not_full.notify()
            return latest

    def wait(
        self,
        name: str,
        expected: Any,
        timeout: float,
        *,
        fuzzy: bool = True,
        consume: bool = True,
        after: float | None = None,
    ) -> dict[str, Any]:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("事件观测超时必须为非负有限值")
        deadline = time.monotonic() + timeout
        with self.source.changed:
            while True:
                message = self._select(name, expected, fuzzy, consume, after)
                if message is not None:
                    return message
                if not self.source.running:
                    raise NativeRuntimeError("事件观测期间 SOA 成员已断开")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"等待 {name} 事件超时")
                self.source.changed.wait(remaining)

    def latest(self, name: str, consume: bool) -> Any:
        message = self._select(name, None, True, consume, None)
        if message is None:
            raise AssertionError(f"当前未接收到过 {name} 事件")
        return json.loads(message["args"])

    def assert_absent(
        self, name: str, timeout: float, predicate: Callable[[Any], bool] | None = None
    ) -> bool:
        """断言观测窗口内无匹配事件；断开不是无事件，历史记录也不能静默清空。"""
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("无事件观测超时必须为非负有限值")
        deadline = time.monotonic() + timeout
        with self.source.changed:
            while True:
                with self.source.event_queue.mutex:
                    for message in self.source.event_queue.queue:
                        if message.get("function") == name and (
                            predicate is None or predicate(json.loads(message["args"]))
                        ):
                            raise AssertionError(f"观测到不应出现的事件: {message}")
                if not self.source.running:
                    raise NativeRuntimeError("无事件观测期间 SOA 成员已断开，不能证明未发生事件")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                self.source.changed.wait(remaining)
