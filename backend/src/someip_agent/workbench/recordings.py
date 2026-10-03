"""有界监控流记录与只读回放；不向任何网络重放记录数据。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from someip_agent.domain.models import MonitorMessage
from someip_agent.runtime.monitor import MonitorStore

from .projects import StrictModel

logger = logging.getLogger(__name__)


class RecordingRequest(StrictModel):
    name: str = Field(default="监控流记录", min_length=1, max_length=128)
    segment_bytes: int = Field(
        default=8 * 1024 * 1024, ge=64 * 1024, le=16 * 1024 * 1024, strict=True
    )
    quota_bytes: int = Field(
        default=256 * 1024 * 1024, ge=64 * 1024, le=512 * 1024 * 1024, strict=True
    )
    queue_capacity: int = Field(default=512, ge=16, le=4096, strict=True)
    service_id: int | None = Field(default=None, ge=0, le=65535)
    method_id: int | None = Field(default=None, ge=0, le=65535)
    service_session_ids: list[UUID] | None = Field(default=None, max_length=512)
    listener_ids: list[UUID] | None = Field(default=None, max_length=512)


class RecordingSegment(StrictModel):
    number: int = Field(ge=0, le=8191)
    first_index: int = Field(ge=0)
    frame_count: int = Field(ge=1)
    bytes: int = Field(ge=1, le=24 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecordingView(StrictModel):
    id: UUID
    format_version: Literal[1] = 1
    config: RecordingRequest
    started_at: datetime
    finished_at: datetime | None = None
    state: Literal["recording", "stopped", "quota", "failed", "interrupted"] = "recording"
    scope: Literal["monitor_publish"] = "monitor_publish"
    backend_epoch: str
    frame_count: int = 0
    bytes: int = 0
    duration_ms: float = 0
    queue_discarded: int = 0
    skipped_by_filter: int = 0
    unsealed_frames: int = 0
    buffered_not_recorded: int = 0
    last_error: str | None = None
    segments: list[RecordingSegment] = Field(default_factory=list)


class RecordingNotFound(LookupError):
    pass


class RecordingConflict(ValueError):
    pass


@dataclass
class Writer:
    view: RecordingView
    queue: asyncio.Queue[MonitorMessage]
    stop: asyncio.Event
    task: asyncio.Task[None] | None = None


class SegmentWriter:
    def __init__(self, directory: Path, view: RecordingView) -> None:
        self.directory, self.view = directory, view
        self.stream: Any = None
        self.digest = hashlib.sha256()
        self.size = self.count = 0
        self.started_ns = time.monotonic_ns()

    def close(self) -> None:
        if self.stream is None:
            return
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        self.view.segments.append(
            RecordingSegment(
                number=len(self.view.segments),
                first_index=self.view.frame_count - self.count,
                frame_count=self.count,
                bytes=self.size,
                sha256=self.digest.hexdigest(),
            )
        )
        self.stream = None

    def append(self, batch: list[tuple[int, str]]) -> bool:
        for arrived_ns, message in batch:
            offset_ms = max(0, (arrived_ns - self.started_ns) / 1_000_000)
            line = (
                '{"index":'
                + str(self.view.frame_count)
                + ',"offset_ms":'
                + str(offset_ms)
                + ',"message":'
                + message
                + "}\n"
            ).encode()
            if len(line) > 8 * 1024 * 1024:
                raise ValueError("单条监控记录超过 8 MiB，记录失败，不截断原始证据")
            if self.view.bytes + len(line) > self.view.config.quota_bytes:
                self.view.state = "quota"
                return False
            if self.stream is not None and self.size + len(line) > self.view.config.segment_bytes:
                self.close()
            if self.stream is None:
                self.stream = (
                    self.directory / f"segment-{len(self.view.segments):05d}.jsonl"
                ).open("xb")
                self.digest, self.size, self.count = hashlib.sha256(), 0, 0
            self.stream.write(line)
            self.digest.update(line)
            self.size += len(line)
            self.count += 1
            self.view.bytes += len(line)
            self.view.frame_count += 1
            self.view.duration_ms = offset_ms
        return True


class RecordingManager:
    def __init__(self, directory: Path, monitor: MonitorStore) -> None:
        self._root = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.monitor = monitor
        self._writers: dict[UUID, Writer] = {}
        self._lock = asyncio.Lock()

    def _directory(self, identifier: UUID) -> Path:
        directory = self._root / str(identifier)
        if directory.is_symlink():
            raise ValueError("记录目录不能是符号链接")
        return directory

    def _persist(self, view: RecordingView) -> None:
        path = self._directory(view.id) / "manifest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(view.model_dump_json(), encoding="utf-8")
        temporary.replace(path)

    def get(self, identifier: UUID) -> RecordingView:
        if identifier in self._writers:
            return self._writers[identifier].view.model_copy(deep=True)
        path = self._directory(identifier) / "manifest.json"
        if not path.is_file():
            raise RecordingNotFound("记录不存在")
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("记录清单非法")
        view = RecordingView.model_validate_json(path.read_text(encoding="utf-8"))
        if view.id != identifier:
            raise ValueError("记录清单 ID 不匹配")
        if view.state == "recording":
            sealed_count = sum(segment.frame_count for segment in view.segments)
            view.unsealed_frames = max(0, view.frame_count - sealed_count)
            view.frame_count = sealed_count
            view.bytes = sum(segment.bytes for segment in view.segments)
            view.state = "interrupted"
            view.last_error = "进程非正常中断；只允许读取已封存且校验通过的分段，不能声称记录完整"
        return view

    def list(self) -> list[RecordingView]:
        identifiers = sorted((p.name for p in self._root.iterdir() if p.is_dir()), reverse=True)
        views = []
        for name in identifiers[:128]:
            try:
                views.append(self.get(UUID(name)))
            except Exception:
                logger.exception("读取记录清单失败", extra={"operation": "recording.list"})
        return sorted(views, key=lambda item: item.started_at, reverse=True)

    async def start(self, request: RecordingRequest) -> RecordingView:
        async with self._lock:
            if len(self._writers) >= 4:
                raise RecordingConflict("最多同时记录 4 路监控流")
            views = await asyncio.to_thread(self.list)
            if sum(1 for path in self._root.iterdir() if path.is_dir()) >= 128:
                raise RecordingConflict("记录数量达到 128，请先导出归档")
            # 保留本次额度；应用记录目录总额不超过 2 GiB，不影响其他数据目录。
            actual_bytes = sum(
                path.stat().st_size
                for path in self._root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            reserved = actual_bytes + sum(
                max(0, v.config.quota_bytes - v.bytes) for v in views if v.state == "recording"
            )
            if reserved + request.quota_bytes > 2 * 1024 * 1024 * 1024:
                raise RecordingConflict("应用记录总额度超过 2 GiB，请先导出归档")
            identifier = uuid4()
            self._directory(identifier).mkdir(exist_ok=False)
            statistics = await self.monitor.statistics()
            view = RecordingView(
                id=identifier,
                config=request,
                started_at=datetime.now(timezone.utc),
                backend_epoch=str(statistics["backend_epoch"]),
            )
            await asyncio.to_thread(self._persist, view)
            queue = await self.monitor.subscribe(request.queue_capacity)
            writer = Writer(view=view, queue=queue, stop=asyncio.Event())
            self._writers[identifier] = writer
            writer.task = asyncio.create_task(self._record(writer))
            logger.info(
                "连续记录已启动，不改变监听或发送配置",
                extra={"operation": "recording.start", "recording_id": str(identifier)},
            )
            return view.model_copy(deep=True)

    async def _record(self, writer: Writer) -> None:
        output = SegmentWriter(self._directory(writer.view.id), writer.view)
        queue = writer.queue
        try:
            while not writer.stop.is_set() or not queue.empty():
                batch = []
                if queue.empty():
                    waiting = asyncio.create_task(queue.get())
                    stopping = asyncio.create_task(writer.stop.wait())
                    done, pending = await asyncio.wait(
                        {waiting, stopping}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    if waiting in done:
                        messages = [waiting.result()]
                    else:
                        continue
                else:
                    messages = [queue.get_nowait()]
                while not queue.empty() and len(messages) < 128:
                    messages.append(queue.get_nowait())
                for message in messages:
                    config = writer.view.config
                    if config.service_session_ids is not None or config.listener_ids is not None:
                        sessions = {str(value) for value in config.service_session_ids or []}
                        listeners = {str(value) for value in config.listener_ids or []}
                        if (
                            message.metadata.get("service_session_id") not in sessions
                            and message.metadata.get("listener_id") not in listeners
                        ):
                            writer.view.skipped_by_filter += 1
                            continue
                    if (
                        config.service_id is not None and message.service_id != config.service_id
                    ) or (config.method_id is not None and message.method_id != config.method_id):
                        writer.view.skipped_by_filter += 1
                        continue
                    batch.append(
                        (int(message.metadata["monitor_arrival_ns"]), message.model_dump_json())
                    )
                previous_count = writer.view.frame_count
                if not await asyncio.to_thread(output.append, batch):
                    writer.view.buffered_not_recorded = (
                        len(batch) - (writer.view.frame_count - previous_count) + queue.qsize()
                    )
                    break
                # 封存分段后及时持久化校验清单；当前段在异常中断时不冒充完整证据。
                await asyncio.to_thread(self._persist, writer.view)
        except BaseException as exc:
            logger.exception(
                "连续记录失败",
                extra={"operation": "recording.write", "recording_id": str(writer.view.id)},
            )
            writer.view.state = "failed"
            writer.view.last_error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            statistics = await self.monitor.detach(queue)
            writer.view.queue_discarded = max(
                writer.view.queue_discarded, int(statistics["current_subscriber_discarded"] or 0)
            )
            try:
                await asyncio.to_thread(output.close)
                if writer.view.state == "recording":
                    writer.view.state = "stopped"
                writer.view.finished_at = datetime.now(timezone.utc)
                await asyncio.to_thread(self._persist, writer.view)
            except Exception as exc:
                logger.exception("封存连续记录失败", extra={"operation": "recording.close"})
                writer.view.state = "failed"
                writer.view.last_error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                self._writers.pop(writer.view.id, None)

    async def stop(self, identifier: UUID) -> RecordingView:
        async with self._lock:
            writer = self._writers.get(identifier)
            if writer is None:
                return await asyncio.to_thread(self.get, identifier)
            # 切断新流量，排空此前队列。保留连接计数，退订由 writer 的 finally 负责。
            writer.stop.set()
            counters = await self.monitor.detach(writer.queue)
            writer.view.queue_discarded = max(
                writer.view.queue_discarded, int(counters["current_subscriber_discarded"] or 0)
            )
            if writer.task:
                await writer.task
            logger.info(
                "连续记录已停止并封存",
                extra={"operation": "recording.stop", "recording_id": str(identifier)},
            )
            return await asyncio.to_thread(self.get, identifier)

    def segment(self, identifier: UUID, number: int) -> bytes:
        view = self.get(identifier)
        if view.state == "recording":
            raise RecordingConflict("请先停止并封存记录，再导出或回放")
        segment = next((item for item in view.segments if item.number == number), None)
        if segment is None:
            raise RecordingNotFound("记录分段不存在或未封存")
        path = self._directory(identifier) / f"segment-{number:05d}.jsonl"
        if path.is_symlink() or path.stat().st_size != segment.bytes:
            raise ValueError("记录分段大小不符或路径非法")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != segment.sha256:
            raise ValueError("记录分段 SHA-256 不符，拒绝回放或导出")
        return body

    def frames(
        self,
        identifier: UUID,
        *,
        offset: int = 0,
        limit: int = 200,
        service_id: int | None = None,
        method_id: int | None = None,
        direction: str | None = None,
        start_ms: float = 0,
    ) -> dict[str, Any]:
        view = self.get(identifier)
        if view.state == "recording":
            raise RecordingConflict("请先停止记录，离线回放不消费实时发送面")
        result, cursor = [], offset
        for segment in view.segments:
            if segment.first_index + segment.frame_count <= offset:
                continue
            for line in self.segment(identifier, segment.number).splitlines():
                frame = json.loads(line)
                if frame["index"] < offset:
                    continue
                cursor = frame["index"] + 1
                message = MonitorMessage.model_validate(frame["message"])
                if (
                    (service_id is not None and message.service_id != service_id)
                    or (method_id is not None and message.method_id != method_id)
                    or (direction is not None and message.direction != direction)
                    or frame["offset_ms"] < start_ms
                ):
                    continue
                result.append(frame)
                if len(result) >= max(1, min(limit, 1000)):
                    return {
                        "frames": result,
                        "next_offset": cursor,
                        "complete": cursor >= view.frame_count,
                        "wire_replay": False,
                    }
        return {"frames": result, "next_offset": cursor, "complete": True, "wire_replay": False}

    def export(self, identifier: UUID) -> Path:
        view = self.get(identifier)
        if view.state == "recording":
            raise RecordingConflict("请先停止并封存记录")
        handle = tempfile.NamedTemporaryFile(
            prefix="someip-recording-", suffix=".zip", delete=False
        )
        path = Path(handle.name)
        handle.close()
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", view.model_dump_json())
                for segment in view.segments:
                    archive.writestr(
                        f"segment-{segment.number:05d}.jsonl",
                        self.segment(identifier, segment.number),
                    )
            logger.info(
                "连续记录已导出并校验分段完整性",
                extra={"operation": "recording.export", "recording_id": str(identifier)},
            )
            return path
        except BaseException:
            logger.exception("连续记录导出失败", extra={"operation": "recording.export"})
            path.unlink(missing_ok=True)
            raise

    async def shutdown(self) -> None:
        for identifier in tuple(self._writers):
            await self.stop(identifier)

    def permit_source(self, identifier: UUID, source_id: UUID, *, listener: bool = False) -> None:
        """场景内部登记它刚创建的资源；不开放任意路径或网络发送。"""
        writer = self._writers[identifier]
        values = (
            writer.view.config.listener_ids if listener else writer.view.config.service_session_ids
        )
        if values is None:
            raise ValueError("该记录没有启用资源范围过滤")
        if source_id not in values:
            if len(values) >= 512:
                raise RecordingConflict("记录资源范围最多 512 个来源")
            values.append(source_id)
