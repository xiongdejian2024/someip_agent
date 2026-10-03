from __future__ import annotations

import asyncio
from collections import Counter, deque
from collections.abc import Iterable
from uuid import uuid4

from someip_agent.domain.models import MonitorMessage


class MonitorStore:
    def __init__(self, capacity: int = 20_000) -> None:
        if capacity < 1:
            raise ValueError("监控缓存容量必须为正整数")
        self._messages: deque[MonitorMessage] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue[MonitorMessage]] = set()
        self._lock = asyncio.Lock()
        self._epoch = str(uuid4())
        self._published_total = 0
        self._history_evicted_total = 0
        self._cleared_total = 0
        self._subscriber_discarded_total = 0
        self._subscriber_discards: dict[asyncio.Queue[MonitorMessage], int] = {}

    async def publish(self, message: MonitorMessage) -> None:
        async with self._lock:
            self._published_total += 1
            if len(self._messages) == self._messages.maxlen:
                self._history_evicted_total += 1
            self._messages.append(message)
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                # 此处没有 await，队列只由同一事件循环访问；淘汰操作与计数同步完成。
                queue.get_nowait()
                self._subscriber_discarded_total += 1
                self._subscriber_discards[queue] += 1
            queue.put_nowait(message)

    async def publish_many(self, messages: Iterable[MonitorMessage]) -> None:
        for message in messages:
            await self.publish(message)

    async def list(
        self,
        *,
        limit: int = 500,
        service_id: int | None = None,
        method_id: int | None = None,
        is_sd: bool | None = None,
    ) -> list[MonitorMessage]:
        async with self._lock:
            snapshot = list(self._messages)
        if service_id is not None:
            snapshot = [item for item in snapshot if item.service_id == service_id]
        if method_id is not None:
            snapshot = [item for item in snapshot if item.method_id == method_id]
        if is_sd is not None:
            snapshot = [item for item in snapshot if item.is_sd is is_sd]
        return snapshot[-max(1, min(limit, 5000)) :]

    async def clear(self) -> None:
        async with self._lock:
            self._cleared_total += len(self._messages)
            self._messages.clear()

    def _statistics(self, queue: asyncio.Queue[MonitorMessage] | None = None) -> dict[str, object]:
        return {
            "backend_epoch": self._epoch,
            "counter_scope": "backend_process_lifetime",
            "published_total": self._published_total,
            "retained_messages": len(self._messages),
            "history_evicted_total": self._history_evicted_total,
            "cleared_total": self._cleared_total,
            "subscriber_discarded_total": self._subscriber_discarded_total,
            "current_subscriber_discarded": self._subscriber_discards.get(queue)
            if queue is not None else None,
            "active_subscribers": len(self._subscribers),
        }

    async def statistics(
        self, queue: asyncio.Queue[MonitorMessage] | None = None
    ) -> dict[str, object]:
        async with self._lock:
            return self._statistics(queue)

    async def summary(self) -> dict[str, object]:
        async with self._lock:
            snapshot = list(self._messages)
            statistics = self._statistics()
        service_counts = Counter(f"0x{item.service_id:04X}" for item in snapshot)
        type_counts = Counter(f"0x{item.message_type:02X}" for item in snapshot)
        return {
            "total": len(snapshot),
            "sd_messages": sum(1 for item in snapshot if item.is_sd),
            "error_messages": sum(1 for item in snapshot if item.return_code != 0),
            "services": dict(service_counts.most_common(20)),
            "message_types": dict(type_counts),
            "stream_counters": statistics,
        }

    async def subscribe(self, queue_size: int = 1000) -> asyncio.Queue[MonitorMessage]:
        if queue_size < 1:
            raise ValueError("订阅队列容量必须为正整数，不能使用无界队列")
        queue: asyncio.Queue[MonitorMessage] = asyncio.Queue(maxsize=queue_size)
        async with self._lock:
            self._subscribers.add(queue)
            self._subscriber_discards[queue] = 0
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[MonitorMessage]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
            self._subscriber_discards.pop(queue, None)
