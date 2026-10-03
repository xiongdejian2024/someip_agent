"""声明式自动场景控制面；复用原生服务、网络门禁和独立记录，不编解码 SOME/IP。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from someip_agent.domain.models import ArxmlModel, ListenerConfig, MonitorMessage
from someip_agent.runtime.service_models import (
    ServiceCommand,
    ServiceCycleCommand,
    ServiceCycleStop,
    ServiceResponse,
)
from someip_agent.soa.catalog import NativeCatalogRequest
from someip_agent.version import __version__

from .projects import ProjectView
from .recordings import RecordingRequest
from .scenario_models import (
    RunView,
    ScenarioRunRequest,
    ScenarioStep,
    StepResult,
    canonical,
    parameters,
)

if TYPE_CHECKING:
    from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)


@dataclass
class Run:
    view: RunView
    request: ScenarioRunRequest
    project: ProjectView
    queue: asyncio.Queue[MonitorMessage]
    sessions: dict[str, str] = field(default_factory=dict)
    listeners: dict[str, str] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    messages: deque[MonitorMessage] = field(default_factory=lambda: deque(maxlen=1000))
    task: asyncio.Task[None] | None = None
    queue_discards: int = 0
    result_bytes: int = 0
    failed_cases: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False


class ScenarioManager:
    def __init__(self, state: ApplicationState) -> None:
        self.state = state
        self._runs: dict[UUID, Run] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: ScenarioRunRequest, request_id: str) -> RunView:
        async with self._lock:
            if len(self._runs) >= 4:
                raise ValueError("最多同时运行 4 个产品场景")
            project = await asyncio.to_thread(self.state.projects.get, request.project_id)
            model = project.document.model
            if model is None:
                raise ValueError("场景工程缺少 ARXML 模型")
            if (
                request.definition.model_source_sha256
                and model.source_sha256 != request.definition.model_source_sha256
            ):
                raise ValueError("场景 ARXML SHA-256 前置条件不满足")
            view = RunView(
                id=uuid4(),
                request_id=request_id[:128],
                name=request.definition.name,
                project_id=project.id,
                project_revision=project.revision,
                definition_sha256=hashlib.sha256(
                    canonical(
                        request.definition.model_dump(mode="json", exclude_unset=True)
                    ).encode()
                ).hexdigest(),
                model_source_sha256=model.source_sha256,
                application_version=__version__,
                started_at=datetime.now(timezone.utc),
            )
            await asyncio.to_thread(
                self.state.runs.save,
                view,
                {
                    "project": project.model_dump(mode="json"),
                    "definition": request.definition.model_dump(mode="json", exclude_unset=True),
                },
            )
            queue = await self.state.monitor.subscribe(4096)
            run = Run(view=view, request=request, project=project, queue=queue)
            self._runs[view.id] = run
            run.task = asyncio.create_task(self._run(run, model.model_copy(deep=True)))
            # 不把尚未进入 try/finally 的后台任务交给取消入口。
            await run.started.wait()
            self.state.audit.add(
                action="scenario.start",
                target=str(view.id),
                detail={"request_id": view.request_id, "definition_sha256": view.definition_sha256},
            )
            logger.info(
                "产品自动场景已启动", extra={"operation": "scenario.start", "run_id": str(view.id)}
            )
            return view.model_copy(deep=True)

    async def cancel(self, identifier: UUID) -> RunView:
        run = self._runs.get(identifier)
        if run and run.task:
            if not run.task.done() and not run.cancel_requested and run.view.status == "running":
                run.cancel_requested = True
                run.task.cancel()
            await asyncio.shield(run.task)
        return await asyncio.to_thread(self.state.runs.get, identifier)

    async def wait(self, identifier: UUID) -> RunView:
        run = self._runs.get(identifier)
        if run and run.task:
            await asyncio.shield(run.task)
        return await asyncio.to_thread(self.state.runs.get, identifier)

    async def get(self, identifier: UUID) -> RunView:
        run = self._runs.get(identifier)
        if run:
            view = run.view.model_copy(deep=True)
            # 最终状态只有在清理、封存和持久化全部结束后才能交付。
            view.status = "running"
            return view
        return await asyncio.to_thread(self.state.runs.get, identifier)

    async def _run(self, run: Run, model: ArxmlModel) -> None:
        try:
            run.started.set()
            creation = asyncio.create_task(
                self.state.recordings.start(
                    RecordingRequest(
                        name=f"场景 {run.view.id}",
                        quota_bytes=16 * 1024 * 1024,
                        service_session_ids=[],
                        listener_ids=[],
                        queue_capacity=4096,
                    )
                )
            )
            try:
                recording = await asyncio.shield(creation)
            except asyncio.CancelledError:
                recording = await creation
                run.view.recording_id = recording.id
                raise
            run.view.recording_id = recording.id
            await asyncio.wait_for(self._cases(run, model), run.request.definition.timeout_seconds)
            run.view.status = (
                "failed"
                if run.failed_cases or any(step.status == "failed" for step in run.view.steps)
                else "passed"
            )
        except asyncio.CancelledError:
            logger.exception(
                "产品场景取消，开始清理自有资源",
                extra={"operation": "scenario.cancel", "run_id": str(run.view.id)},
            )
            run.view.status = "cancelled"
            run.view.error = "用户取消了运行"
        except Exception as exc:
            logger.exception(
                "产品场景执行失败", extra={"operation": "scenario.run", "run_id": str(run.view.id)}
            )
            run.view.status = "failed"
            run.view.error = f"{type(exc).__name__}: {exc}"
        finally:
            # 清理自己登记的资源。不能使用 stop-all，也不能关闭用户此前运行的会话。
            await self._release(run)
            counters = await self.state.monitor.detach(run.queue)
            run.queue_discards = int(counters["current_subscriber_discarded"] or 0)
            if run.view.recording_id:
                try:
                    record = await self.state.recordings.stop(run.view.recording_id)
                    if record.state != "stopped" or record.queue_discarded or run.queue_discards:
                        run.view.cleanup_errors.append(
                            "监控证据不完整：记录状态、记录队列或断言订阅发生丢弃"
                        )
                except Exception as exc:
                    logger.exception(
                        "场景证据封存失败", extra={"operation": "scenario.recording.stop"}
                    )
                    run.view.cleanup_errors.append(f"证据封存失败：{type(exc).__name__}: {exc}")
            run.view.cleanup_complete = not run.sessions and not run.listeners
            if run.view.cleanup_errors:
                run.view.status = "failed"
            run.view.finished_at = datetime.now(timezone.utc)
            try:
                await asyncio.to_thread(self.state.runs.save, run.view)
                self.state.audit.add(
                    action="scenario.finish",
                    target=str(run.view.id),
                    success=run.view.status == "passed",
                    detail={
                        "status": run.view.status,
                        "request_id": run.view.request_id,
                        "recording_id": str(run.view.recording_id),
                        "cleanup_complete": run.view.cleanup_complete,
                    },
                )
                logger.info(
                    "产品场景结果已持久化",
                    extra={
                        "operation": "scenario.finish",
                        "run_id": str(run.view.id),
                        "status": run.view.status,
                    },
                )
            finally:
                self._runs.pop(run.view.id, None)

    async def _cases(self, run: Run, model: ArxmlModel) -> None:
        for case_index, case in enumerate(run.request.definition.cases):
            run.values = {"parameters": case}
            run.messages.clear()
            failed = False
            try:
                for index, template in enumerate(run.request.definition.steps):
                    step = ScenarioStep.model_validate(
                        parameters(template.model_dump(mode="json", exclude_unset=True), case)
                    )
                    await self._execute(run, model, step, case_index, str(index))
            except Exception as exc:
                logger.exception(
                    "参数化用例失败，仍执行清理",
                    extra={
                        "operation": "scenario.case",
                        "run_id": str(run.view.id),
                        "case": case_index,
                    },
                )
                failed = True
                run.failed_cases += 1
                run.view.error = f"用例 {case_index}：{type(exc).__name__}: {exc}"
            finally:
                try:
                    await asyncio.wait_for(self._business_cleanup(run, model, case_index, case), 30)
                except Exception as exc:
                    logger.exception(
                        "场景业务清理超时或失败", extra={"operation": "scenario.cleanup"}
                    )
                    run.view.cleanup_errors.append(f"业务清理：{type(exc).__name__}: {exc}")
                await self._release(run)
            if failed and run.request.definition.stop_on_failure:
                break

    async def _business_cleanup(
        self, run: Run, model: ArxmlModel, case_index: int, case: dict
    ) -> None:
        for index, template in enumerate(run.request.definition.cleanup):
            try:
                step = ScenarioStep.model_validate(
                    parameters(template.model_dump(mode="json", exclude_unset=True), case)
                )
                await self._execute(run, model, step, case_index, f"cleanup.{index}")
            except Exception as exc:
                logger.exception("场景业务清理失败", extra={"operation": "scenario.cleanup"})
                run.view.cleanup_errors.append(
                    f"用例 {case_index} 业务清理：{type(exc).__name__}: {exc}"
                )

    async def _release(self, run: Run) -> None:
        for profile, identifier in tuple(run.sessions.items()):
            try:
                await self.state.services.stop(identifier)
                run.sessions.pop(profile)
            except Exception as exc:
                logger.exception(
                    "释放场景自有会话失败", extra={"operation": "scenario.release.service"}
                )
                run.view.cleanup_errors.append(
                    f"会话 {profile} 释放失败：{type(exc).__name__}: {exc}"
                )
        for profile, identifier in tuple(run.listeners.items()):
            try:
                await self.state.network.stop(identifier)
                run.listeners.pop(profile)
            except Exception as exc:
                logger.exception(
                    "释放场景自有监听失败", extra={"operation": "scenario.release.listener"}
                )
                run.view.cleanup_errors.append(
                    f"监听 {profile} 释放失败：{type(exc).__name__}: {exc}"
                )

    async def _execute(
        self, run: Run, model: ArxmlModel, step: ScenarioStep, case: int, index: str
    ) -> Any:
        started = time.monotonic()
        try:
            result = await self._dispatch(run, model, step, case, index)
            size = len(canonical(result).encode())
            if size > 512 * 1024 or run.result_bytes + size > 6 * 1024 * 1024:
                raise ValueError(
                    "场景结果超过单步骤/运行证据预算；原始记录单独封存，不能声称报告完整"
                )
            run.result_bytes += size
            if step.save_as:
                run.values[step.save_as] = result
            report = StepResult(
                case=case,
                index=index,
                name=step.name,
                kind=step.kind,
                status="passed",
                duration_ms=(time.monotonic() - started) * 1000,
                result=result,
            )
        except Exception as exc:
            logger.exception(
                "产品场景步骤失败",
                extra={
                    "operation": "scenario.step",
                    "run_id": str(run.view.id),
                    "case": case,
                    "step": index,
                },
            )
            run.view.steps.append(
                StepResult(
                    case=case,
                    index=index,
                    name=step.name,
                    kind=step.kind,
                    status="failed",
                    duration_ms=(time.monotonic() - started) * 1000,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            self.state.audit.add(
                action="scenario.step",
                target=str(run.view.id),
                success=False,
                detail={
                    "case": case,
                    "step": index,
                    "kind": step.kind,
                    "request_id": run.view.request_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        run.view.steps.append(report)
        self.state.audit.add(
            action="scenario.step",
            target=str(run.view.id),
            detail={
                "case": case,
                "step": index,
                "kind": step.kind,
                "request_id": run.view.request_id,
            },
        )
        return result

    async def _dispatch(
        self, run: Run, model: ArxmlModel, step: ScenarioStep, case: int, index: str
    ) -> Any:
        if step.kind == "start_service":
            if step.profile in run.sessions:
                raise ValueError("场景会话已经启动，不能覆盖资源归属")
            profile = run.project.document.services[step.profile]
            creation = asyncio.create_task(
                self.state.services.start(
                    model,
                    NativeCatalogRequest.model_validate(
                        parameters(profile.model_dump(mode="json"), run.values["parameters"])
                    ),
                )
            )
            cancelled = False
            try:
                created = await asyncio.shield(creation)
            except asyncio.CancelledError:
                # 底层可能已在 to_thread 中创建进程；先登记归属，再交给 finally 释放。
                created = await creation
                cancelled = True
            run.sessions[step.profile] = created.id
            if cancelled:
                raise asyncio.CancelledError
            self.state.recordings.permit_source(run.view.recording_id, UUID(created.id))
            return created.model_dump(mode="json")
        if step.kind == "start_listener":
            if step.profile in run.listeners:
                raise ValueError("场景监听已经启动")
            profile = int(step.profile)
            if profile < 0 or profile >= len(run.project.document.listeners):
                raise ValueError("工程监听草案下标越界")
            config = run.project.document.listeners[profile]
            creation = asyncio.create_task(
                self.state.network.start(
                    ListenerConfig.model_validate(config.model_dump(mode="json"))
                )
            )
            cancelled = False
            try:
                created = await asyncio.shield(creation)
            except asyncio.CancelledError:
                created = await creation
                cancelled = True
            run.listeners[step.profile] = created.id
            if cancelled:
                raise asyncio.CancelledError
            self.state.recordings.permit_source(
                run.view.recording_id, UUID(created.id), listener=True
            )
            return created.model_dump(mode="json")
        if step.kind == "stop_service":
            identifier = run.sessions[step.session]
            result = await self.state.services.stop(identifier)
            run.sessions.pop(step.session)
            return result.model_dump(mode="json")
        if step.kind == "stop_listener":
            result = await self.state.network.stop(run.listeners[step.listener])
            run.listeners.pop(step.listener)
            return [item.model_dump(mode="json") for item in result]
        if step.kind == "delay":
            await asyncio.sleep(step.seconds)
            return {"waited_seconds": step.seconds}
        if step.kind == "assert":
            actual: Any = run.values
            for part in step.path:
                actual = actual[part]
            if step.comparison == "eq":
                matched = canonical(actual) == canonical(step.expected)
            elif step.comparison == "approx":
                matched = (
                    type(actual) in {int, float}
                    and type(step.expected) in {int, float}
                    and math.isclose(actual, step.expected, abs_tol=step.tolerance, rel_tol=0)
                )
            else:
                matched = True
            if not matched:
                raise AssertionError(
                    f"确定性断言失败：path={step.path}，actual={canonical(actual)}，expected={canonical(step.expected)}"
                )
            return {
                "path": step.path,
                "actual": actual,
                "expected": step.expected,
                "comparison": step.comparison,
            }
        if step.kind == "parallel":
            tasks = [
                asyncio.create_task(self._execute(run, model, child, case, f"{index}.{i}"))
                for i, child in enumerate(step.children)
            ]
            try:
                return await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(
                            "并行子步骤失败",
                            exc_info=(type(result), result, result.__traceback__),
                            extra={"operation": "scenario.parallel"},
                        )
        if step.kind == "wait_message":
            return await asyncio.wait_for(self._message(run, step), step.timeout)
        identifier = run.sessions[step.session]
        if step.kind == "wait_ready":

            async def ready():
                while True:
                    status = next(
                        item for item in self.state.services.statuses() if item.id == identifier
                    )
                    if not status.running:
                        raise ValueError("场景会话失去运行连接")
                    if all(
                        member.state == "START" and member.connected for member in status.members
                    ):
                        return status.model_dump(mode="json")
                    await asyncio.sleep(0.02)

            return await asyncio.wait_for(ready(), step.timeout)
        if step.kind == "respond_next":
            command = ServiceCommand.model_validate(step.command)

            async def respond():
                while True:
                    found = next(
                        (
                            item
                            for item in self.state.services.requests(identifier)
                            if item.member == command.member
                            and item.function == command.function
                            and item.reply_allowed
                        ),
                        None,
                    )
                    if found:
                        response = ServiceResponse(
                            **command.model_dump(), request_id=found.request_id
                        )
                        return (await self.state.services.respond(identifier, response)).model_dump(
                            mode="json"
                        )
                    await asyncio.sleep(0.01)

            return await asyncio.wait_for(respond(), step.timeout)
        if step.kind == "cycle_start":
            return (
                await self.state.services.configure_cycle(
                    identifier, ServiceCycleCommand.model_validate(step.command)
                )
            ).model_dump(mode="json")
        if step.kind == "cycle_stop":
            return (
                await self.state.services.stop_cycle(
                    identifier, ServiceCycleStop.model_validate(step.command)
                )
            ).model_dump(mode="json")
        command = ServiceCommand.model_validate(step.command)
        if step.kind == "call":
            return (await self.state.services.call(identifier, command)).model_dump(mode="json")
        return (await self.state.services.notify(identifier, command)).model_dump(mode="json")

    async def _message(self, run: Run, step: ScenarioStep) -> dict[str, Any]:
        source = run.sessions[step.session] if step.session else run.listeners[step.listener]
        source_key = "service_session_id" if step.session else "listener_id"

        def matches(message: MonitorMessage) -> bool:
            return message.metadata.get(source_key) == source and all(
                getattr(message, key) == value
                if key != "member"
                else message.metadata.get("member") == value
                for key, value in step.match.model_dump(exclude_none=True).items()
            )

        while True:
            for message in tuple(run.messages):
                if matches(message):
                    run.messages.remove(message)
                    return message.model_dump(mode="json")
            run.messages.append(await run.queue.get())

    async def shutdown(self) -> None:
        for identifier in tuple(self._runs):
            await self.cancel(identifier)
