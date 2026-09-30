from __future__ import annotations

import asyncio
from collections import Counter, deque
from collections.abc import Iterable

from someip_agent.domain.models import MonitorMessage


class MonitorStore:
    def __init__(self, capacity: int = 20_000) -> None:
        self._messages: deque[MonitorMessage] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue[MonitorMessage]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, message: MonitorMessage) -> None:
        async with self._lock:
            self._messages.append(message)
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
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
            self._messages.clear()

    async def summary(self) -> dict[str, object]:
        async with self._lock:
            snapshot = list(self._messages)
        service_counts = Counter(f"0x{item.service_id:04X}" for item in snapshot)
        type_counts = Counter(f"0x{item.message_type:02X}" for item in snapshot)
        return {
            "total": len(snapshot),
            "sd_messages": sum(1 for item in snapshot if item.is_sd),
            "error_messages": sum(1 for item in snapshot if item.return_code != 0),
            "services": dict(service_counts.most_common(20)),
            "message_types": dict(type_counts),
        }

    async def subscribe(self, queue_size: int = 1000) -> asyncio.Queue[MonitorMessage]:
        queue: asyncio.Queue[MonitorMessage] = asyncio.Queue(maxsize=queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[MonitorMessage]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
