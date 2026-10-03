from __future__ import annotations

import json
import logging
import math
import queue
import socket
import threading
import time
from copy import deepcopy
from decimal import Decimal
from enum import Enum, auto
from pathlib import Path
from typing import Any
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.domain.csv_stimulus import compile_csv_stimulus
from someip_agent.domain.models import ArxmlModel

from .catalog import NativeCatalogRequest, build_native_bundle
from .identity import prepare_identities, verify_identities
from .info import PartnerKeyInfo as PartnerKeyInfo
from .info import PartnerStartConfig as PartnerStartConfig
from .ipc import configure_ipc_socket, member_messages
from .naming import PartnerRegistry, member_key, members_config
from .observations import EventObserver, cache_message
from .operator import NativeOperationError, NativeRuntimeError, SOAOperator
from .supervision import NativeSupervisor
from .timing import MethodTimingAudit
from .wti import WTIAssertions

logger = logging.getLogger(__name__)


class FailType(Enum):
    FAILTYPE_SUCCESS = 0
    FAILTYPE_TIMEOUT = -1
    FAILTYPE_SERVICE_BUSY = -2
    FAILTYPE_SERVICE_UNAVAILIABLE = -3
    FAILTYPE_TIME_OUT = 600
    FAILTYPE_NO_MEMORY = auto()
    FAILTYPE_OBJECT_NOT_EXIST = auto()
    FAILTYPE_NO_PERMISSION = auto()
    FAILTYPE_INITIALIZE = auto()
    FAILTYPE_NO_IMPLEMENT = auto()
    FAILTYPE_BAD_TYPECODE = auto()
    FAILTYPE_BAD_OPERATION = auto()
    FAILTYPE_NO_RESPONSE = auto()
    FAILTYPE_BAD_PARAM = auto()
    FAILTYPE_FREE_MEM = auto()
    FAILTYPE_SERIALIZATION_FAILURE = auto()
    FAILTYPE_DESERIALIZATION_FAILURE = auto()
    FAILTYPE_SEND_DATA_FAILURE = auto()
    FAILTYPE_RECEIVE_DATA_FAILURE = auto()
    FAILTYPE_WRONG_DATA_TYPE = auto()
    FAILTYPE_OTHER_ERROR = auto()


class ServiceState(Enum):
    START = 0
    STOP = auto()
    RESTART = auto()
    UPTATE = auto()  # 保留 SAT 的历史拼写。
    BUSY = auto()
    ONLINE = auto()
    OFFLINE = auto()
    ERROR = 100


def ck_data(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and ck_data(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        if not expected:
            return bool(actual == [])
        return isinstance(actual, list) and all(
            any(ck_data(a, e) for a in actual) for e in expected
        )
    if isinstance(actual, float) and isinstance(expected, (int, float)):
        decimal_places = min(len(str(float(expected)).split(".")[1]), 4)
        return round(actual, decimal_places) == round(expected, decimal_places)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.lower() == expected.lower()
    return bool(actual == expected)


class S2sBaseClass(WTIAssertions):
    """保持 SAT 核心测试 API；字典初始化和原生目录显式定义服务的 on-wire 类型。"""

    def __init__(
        self,
        partner_members: Any = None,
        logger_flag: bool = True,
        domin: str = "acu",
        auto_start: bool = True,
        X86: str | None = None,
        idl: str | None = None,
        *,
        operator: SOAOperator | None = None,
        attach: bool = False,
        auto_restart: bool = True,
        monitor_interval: float = 5,
        restart_limit: int = 3,
        liveness_timeout: float | None = 1,
        liveness_failures: int = 3,
        **native_options: Any,
    ) -> None:
        if X86 or idl:
            raise ValueError("vsomeip 使用 catalog/config，不支持厂商 X86/idl 二进制部署参数")
        self.sim_operator = operator or SOAOperator(**native_options)
        self.partner_infos = PartnerRegistry()
        self.auto_response: dict[str, set[str]] = {}
        self._echo_responses: dict[str, set[str]] = {}
        self.logger_flag = logger_flag
        self.domin = domin
        self.attach = attach
        self._closed = False
        self._lifecycle = threading.RLock()
        self._auto_restart = auto_restart and not attach
        if liveness_timeout is not None and (
            isinstance(liveness_timeout, bool)
            or not math.isfinite(liveness_timeout)
            or not 0 < liveness_timeout <= 3
        ):
            raise ValueError("原生活性探测时限必须为 0 至 3 秒内有限正数")
        self._supervisor = NativeSupervisor(
            self.sim_operator,
            self._restart_owned_runtime,
            monitor_interval,
            restart_limit,
            probe=(
                (lambda: self.sim_operator.probe_liveness(liveness_timeout))
                if liveness_timeout is not None
                else None
            ),
            liveness_failures=liveness_failures,
        )
        self.method_default_timeout = 5.1
        self._method_timing = MethodTimingAudit()
        self._member_configs: dict[str, dict[str, Any]] = {}
        self._native_keys: dict[str, str] = {}
        self._sync_config: dict[str, Any] | None = None
        if auto_start:
            try:
                self.start_soa(partner_members or {})
            except Exception:
                logger.exception("SOA 初始化失败", extra={"operation": "soa.start"})
                self.close()
                raise

    @property
    def method_is_timeout(self) -> list[tuple[str, float, float]]:
        return self._method_timing.records

    @method_is_timeout.setter
    def method_is_timeout(self, value: list[tuple[str, float, float]]) -> None:
        self._method_timing.reset_records(value)

    def ck_method_timeout(self) -> None:
        """沿用 SAT 测试结束审计；在途同步调用也不能因没有响应而漏报。"""
        self._method_timing.check()

    def __enter__(self) -> S2sBaseClass:
        return self

    @classmethod
    def from_arxml(
        cls,
        model: ArxmlModel,
        partner_members: Any,
        *,
        directory: Path,
        settings: Settings,
        application_name: str | None = None,
        application_id: int = 0x1101,
        sd_multicast_group: str = "239.192.255.251",
        sd_port: int = 30490,
        **options: Any,
    ) -> S2sBaseClass:
        """配置并初始化原生 client/server；后续方法和事件 API 保持 SAT 写法。"""
        name = application_name or "arxml_" + uuid4().hex
        request = NativeCatalogRequest.model_validate(
            {
                "application_name": name,
                "application_id": application_id,
                "members": cls._members_config(partner_members),
                "sd_multicast_group": sd_multicast_group,
                "sd_port": sd_port,
            }
        )
        try:
            bundle = build_native_bundle(model, request, settings)
            catalog, config = bundle.write(directory)
            return cls(
                bundle.members,
                name=name,
                binary=settings.native_binary,
                catalog=catalog,
                config=config,
                log_path=directory / "native.log",
                **options,
            )
        except Exception:
            logger.exception("ARXML 服务初始化失败", extra={"operation": "soa.arxml.start"})
            raise

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _members_config(members: Any) -> dict[str, Any]:
        return members_config(members)

    def start_soa(self, partner_members: Any) -> None:
        with self._lifecycle:
            if self._closed:
                raise NativeRuntimeError("SOA 实例已关闭，不能再启动服务")
            self._start_soa_locked(partner_members)
            if self._auto_restart:
                self._supervisor.start()

    def _start_soa_locked(self, partner_members: Any) -> None:
        if not self.attach:
            self.sim_operator.run_operator(self.domin)
        self.sim_operator.create_socket()
        configs = deepcopy(self._members_config(partner_members))
        expected = prepare_identities(self.sim_operator, configs)
        addresses = self.sim_operator.send_request("start_config", json.dumps(configs))
        verify_identities(self.sim_operator, expected, addresses)
        for key in list(self.partner_infos):
            if self._native_keys[key] not in addresses:
                self._disconnect(key)
        self._member_configs = {f"{alias}_{cfg['role']}": cfg for alias, cfg in configs.items()}
        for key, address in addresses.items():
            self._connect(key, address)
        if self._sync_config is not None and not self.event_sync_status()["active"]:
            self._sync_config = None

    def _connect(self, key: str, address: list[Any]) -> None:
        native_key = key
        config = self._member_configs[native_key]
        alias = native_key.rsplit("_", 1)[0]
        key = member_key(alias, config)
        callbacks = []
        if key in self.partner_infos:
            previous = self.partner_infos[key]
            if previous.running and previous.ip_port == tuple(address):
                return
            callbacks = list(previous.callback)
            self._disconnect(key)
        conn = socket.create_connection(tuple(address), timeout=10)
        try:
            configure_ipc_socket(conn)
            conn.settimeout(None)
        except Exception:
            conn.close()
            raise
        info = PartnerKeyInfo(
            alias,
            config["role"],
            config.get("name", alias),
            config.get("heartbeat", 600),
            socket=conn,
            ip_port=tuple(address),
        )
        info.start_args = {alias: deepcopy(config)}
        info.callback = callbacks
        self.partner_infos[key] = info
        self._native_keys[key] = native_key
        self.partner_infos.add_alias(native_key, key)
        info.thread = threading.Thread(
            target=self._reader, args=(key, info), daemon=True, name=f"soa-{key}"
        )
        info.thread.start()

    def _reader(self, key: str, info: PartnerKeyInfo) -> None:
        try:
            for message in member_messages(info.require_socket()):
                message["timestamp"] = time.time()
                message["monotonic_timestamp"] = time.monotonic()
                action = str(message.get("action", ""))
                if action == "event" and message.get("function") == "ServiceStatus":
                    with info.changed:
                        status = json.loads(message["args"])
                        info.service_status = status["state"]
                        info.instance = status.get("instance")
                        info.application_name = status.get("application_name")
                        info.application_id = status.get("application_id")
                        info.no_return_methods = set(status.get("no_return_methods", []))
                        if "subscriptions" in status:
                            info.subscriptions = set(status["subscriptions"])
                        info.changed.notify_all()
                target = {
                    "request": info.req_queue,
                    "response": info.resp_queue,
                    "event": info.event_queue,
                }.get(action)
                if action == "response":
                    if message.get("failtype") == "FAILTYPE_SUCCESS" and isinstance(
                        message.get("subscriptions"), list
                    ):
                        with info.changed:
                            info.subscriptions = set(message["subscriptions"])
                            info.changed.notify_all()
                    self._method_timing.complete(
                        message.get("correlation_id", ""),
                        message.get("failtype", "FAILTYPE_SUCCESS"),
                    )
                    with info.waiter_lock:
                        waiter = info.response_waiters.get(message.get("correlation_id", ""))
                        if waiter is not None:
                            waiter.put_nowait(message)
                            target = None
                if target is not None:
                    cache_message(target, message)
                with info.changed:
                    info.changed.notify_all()
                if action == "request" and message["function"] in self.auto_response.get(
                    key, set()
                ):
                    self._auto_reply(key, message)
                if action == "error":
                    info.error = NativeRuntimeError(str(message.get("error", "原生成员执行失败")))
                    logger.error(
                        "原生成员异常，堆栈见原生日志: %s",
                        info.error,
                        extra={"operation": "soa.native.error", "member": key},
                    )
                for callback in tuple(info.callback):
                    try:
                        callback(key, message)
                    except Exception:
                        logger.exception(
                            "SOA 回调执行失败", extra={"operation": "soa.callback", "member": key}
                        )
        except Exception as exc:
            if info.running:
                info.error = exc
                logger.exception(
                    "SOA 成员 socket 接收失败", extra={"operation": "soa.receive", "member": key}
                )
        finally:
            self._method_timing.cancel_member(key)
            with info.waiter_lock:
                for correlation, waiter in info.response_waiters.items():
                    waiter.put_nowait(
                        {
                            "action": "response",
                            "correlation_id": correlation,
                            "failtype": "FAILTYPE_SERVICE_UNAVAILIABLE",
                            "error": "成员 socket 已断开",
                        }
                    )
            with info.changed:
                info.service_status = "OFFLINE"
                info.running = False
                info.changed.notify_all()

    def _restart_owned_runtime(self) -> None:
        with self._lifecycle:
            if self._closed:
                return
            configs = {
                key.rsplit("_", 1)[0]: deepcopy(self._member_configs[key])
                for key in self._native_keys.values()
            }
            for key, info in self.partner_infos.items():
                native_key = self._native_keys[key]
                if (
                    info.subscriptions is not None
                    and self._member_configs[native_key]["role"] == "client"
                ):
                    configs[native_key.rsplit("_", 1)[0]]["subscriptions"] = sorted(
                        info.subscriptions
                    )
            cycles = {
                key: deepcopy(info.cycle_config)
                for key, info in self.partner_infos.items()
                if info.cycle_config is not None and not info.start_event
            }
            sync = deepcopy(self._sync_config)
            self._sync_config = None
            self.sim_operator.stop_operator()
            # 旧读线程必须结束，否则它可能在新请求开始后清除同名成员的关联状态。
            for info in self.partner_infos.values():
                if info.thread and info.thread is not threading.current_thread():
                    info.thread.join(timeout=2)
                    if info.thread.is_alive():
                        raise NativeRuntimeError("旧 SOA 回调线程尚未结束，拒绝重建同名成员")
            self._start_soa_locked(configs)
            for key, cycle in cycles.items():
                assert cycle is not None
                self.send_event_notify_thread_start(
                    key,
                    cycle["event_name"],
                    cycle["args"],
                    cycle["cycle_time"],
                    sources=cycle.get("sources", []),
                    **(
                        {"csv_text": cycle["csv_text"]} if cycle.get("csv_text") is not None else {}
                    ),
                )
            if sync is not None:
                # 异常恢复不假装公共时间连续；从 0 重新预编译并保持暂停，需明确恢复。
                self.start_event_sync(sync["events"], paused=True, speed=sync["speed"])
                logger.warning(
                    "原生同步组异常恢复后已重新准备并暂停",
                    extra={"operation": "soa.event_sync.recover"},
                )

    def _send(self, key: str, message: dict[str, Any]) -> None:
        info = self.partner_infos[key]
        if not info.running:
            raise NativeRuntimeError(f"SOA 成员 {key} 已断开") from info.error
        with info.send_lock:
            info.require_socket().sendall(json.dumps(message, ensure_ascii=True).encode())

    def _auto_reply(self, key: str, message: dict[str, Any]) -> None:
        try:
            canonical = self.partner_infos.canonical(key)
            # SAT 的命名包含实例后缀；不同实例不能误调用同一个基础服务处理器。
            service = canonical.replace("_server", "")
            handler_name = f"send_method_response_{service}_{message['function']}"
            handler = getattr(self, handler_name, None)
            if handler is not None:
                handler()
            elif message["function"] in self._echo_responses.get(canonical, set()):
                self.send_method_response(
                    key,
                    message["function"],
                    json.loads(message["args"]),
                    request_id=message.get("request_id"),
                )
            else:
                logger.warning(
                    "未定义 SAT 自动响应处理器，不发送默认业务响应: %s",
                    handler_name,
                    extra={"operation": "soa.auto_response.missing", "member": canonical},
                )
        except Exception:
            logger.exception(
                "SOA 自动响应执行失败，成员接收线程保持运行",
                extra={"operation": "soa.auto_response", "member": key},
            )

    def wait_for_service_reconnect(self, partner_key: str, timeout: float = 30) -> bool:
        info = self.partner_infos[partner_key]
        with info.changed:
            if not info.changed.wait_for(
                lambda: info.service_status == "START" or not info.running, timeout
            ):
                raise TimeoutError(f"{partner_key} 未在 {timeout}s 内上线")
            if not info.running:
                raise NativeRuntimeError(f"{partner_key} socket 已断开") from info.error
        return True

    def send_method_request(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        is_async: bool = False,
        timeout: float | None = None,
        *,
        _correlation: str | None = None,
    ) -> str:
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("请求超时必须为正有限值")
        self.wait_for_service_reconnect(partner_key, timeout if timeout is not None else 5)
        correlation = _correlation or str(uuid4())
        info = self.partner_infos[partner_key]
        audited = not is_async and method_name not in info.no_return_methods
        function = method_name + ("Async" if is_async else "")
        if audited:
            self._method_timing.begin(
                correlation,
                self.partner_infos.canonical(partner_key),
                function,
                timeout if timeout is not None else self.method_default_timeout,
            )
            info.method_timeout_ck_callback[function] = (time.time(), timeout)
        try:
            self._send(
                partner_key,
                {
                    "action": "request",
                    "function": function,
                    "args": json.dumps(args),
                    "correlation_id": correlation,
                    "timeout_ms": max(1, int((timeout if timeout is not None else 5) * 1000)),
                },
            )
        except Exception:
            self._method_timing.abandon(correlation)
            logger.exception(
                "SOA 方法请求发送失败",
                extra={"operation": "soa.method.send", "member": partner_key},
            )
            raise
        return correlation

    def send_event_notify(self, partner_key: str, event_name: str, args: Any) -> None:
        function = (
            event_name
            if event_name.startswith("Update") and event_name.endswith("Event")
            else f"Update{event_name}Event"
        )
        self._send(partner_key, {"action": "event", "function": function, "args": json.dumps(args)})

    def submit_member_command(
        self, partner_key: str, command: dict[str, Any], timeout: float = 5
    ) -> None:
        """扩展回执接口：原生完成编码/提交后返回；不冒充远端接收或线上抓包。"""
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("本地提交回执超时必须为 0 至 30 秒内有限正数")
        if command.get("action") not in {"event", "response", "request"}:
            raise ValueError("不支持的成员提交动作")
        info = self.partner_infos[partner_key]
        correlation = str(uuid4())
        waiter: queue.Queue[dict[str, Any]] = queue.Queue()
        with info.waiter_lock:
            info.response_waiters[correlation] = waiter
        try:
            self._send(partner_key, {**command, "acknowledge": True, "correlation_id": correlation})
            try:
                response = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                logger.exception("原生成员提交回执超时", extra={"operation": "soa.submit.wait"})
                raise TimeoutError("未收到原生成员提交回执") from exc
            if response["failtype"] != "FAILTYPE_SUCCESS":
                if response["failtype"] == "FAILTYPE_BAD_PARAM":
                    raise NativeOperationError(response.get("error", response["failtype"]))
                raise NativeRuntimeError(response.get("error", response["failtype"]))
        except Exception:
            logger.exception(
                "原生成员提交失败", extra={"operation": "soa.submit", "member": partner_key}
            )
            raise
        finally:
            with info.waiter_lock:
                info.response_waiters.pop(correlation, None)

    @staticmethod
    def _event_name(name: str) -> str:
        return (
            name if name.startswith("Update") and name.endswith("Event") else f"Update{name}Event"
        )

    def _native_member(self, key: str) -> str:
        return self._native_keys[self.partner_infos.canonical(key)]

    @staticmethod
    def _cycle_interval(seconds: float) -> int:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or not 0.001 <= seconds <= 60:
            raise ValueError("周期通知间隔必须为 0.001 至 60 秒")
        # 十进制毫秒不因二进制浮点的 29.000000000000004 被额外 ceil 成 30。
        return math.ceil(Decimal(str(seconds)) * 1000)

    def send_event_notify_thread_start(
        self,
        partner_key: str,
        event_name: str,
        args: Any,
        cycle_time: float = 1,
        *,
        sources: list[dict[str, Any]] | None = None,
        csv_text: str | None = None,
    ) -> None:
        with self._lifecycle:
            self._cycle_start_locked(partner_key, event_name, args, cycle_time, sources, csv_text)

    @staticmethod
    def _cycle_sources(
        sources: list[dict[str, Any]] | None, csv_text: str | None
    ) -> list[dict[str, Any]]:
        bindings = deepcopy([] if sources is None else sources)
        if not isinstance(bindings, list):
            raise ValueError("事件激励绑定必须为数组")
        bindings.extend(compile_csv_stimulus(csv_text))
        if len(bindings) > 128:
            raise ValueError("CSV 与其他激励合计最多 128 个路径")
        paths: set[str] = set()
        for binding in bindings:
            if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
                raise ValueError("每个事件激励必须提供字符串路径")
            if binding["path"] in paths:
                raise ValueError("CSV 与其他激励路径不能重复绑定")
            paths.add(binding["path"])
        return bindings

    def _cycle_start_locked(
        self,
        partner_key: str,
        event_name: str,
        args: Any,
        cycle_time: float,
        sources: list[dict[str, Any]] | None = None,
        csv_text: str | None = None,
    ) -> None:
        interval = self._cycle_interval(cycle_time)
        bindings = self._cycle_sources(sources, csv_text)
        self.sim_operator.send_request(
            "event_cycle_start",
            {
                "member": self._native_member(partner_key),
                "function": self._event_name(event_name),
                "args": args,
                "interval_ms": interval,
                "sources": bindings,
            },
        )
        info = self.partner_infos[partner_key]
        info.cycle_time = interval / 1000
        info.start_event = False
        info.cycle_config = {
            "event_name": event_name,
            "args": deepcopy(args),
            "cycle_time": interval / 1000,
            "sources": deepcopy([] if sources is None else sources),
            **({"csv_text": csv_text} if csv_text is not None else {}),
        }
        logger.info(
            "原生周期事件已启动",
            extra={"operation": "soa.event_cycle.start", "member": partner_key},
        )

    def send_event_notify_thread_stop(self, partner_key: str) -> None:
        with self._lifecycle:
            self._cycle_stop_locked(partner_key)

    def _cycle_stop_locked(self, partner_key: str) -> None:
        self.sim_operator.send_request(
            "event_cycle_stop", {"member": self._native_member(partner_key)}
        )
        self.partner_infos[partner_key].start_event = True
        self.partner_infos[partner_key].cycle_config = None
        logger.info(
            "原生周期事件已停止", extra={"operation": "soa.event_cycle.stop", "member": partner_key}
        )

    def send_event_notify_thread_update(
        self,
        partner_key: str,
        event_name: str,
        args: Any,
        cycle_time: float | None = None,
        *,
        sources: list[dict[str, Any]] | None = None,
        csv_text: str | None = None,
    ) -> None:
        with self._lifecycle:
            self._cycle_update_locked(partner_key, event_name, args, cycle_time, sources, csv_text)

    def event_cycle_status(self, partner_key: str) -> dict[str, Any]:
        """读取实际原生周期计数；这是调度/提交状态，不代表线上抓包或远端交付。"""
        with self._lifecycle:
            info = self.partner_infos[partner_key]
            state = self.sim_operator.send_request("running_service", print_result=False)
            native = (
                state.get(self._native_member(partner_key)) if isinstance(state, dict) else None
            )
            if not isinstance(native, dict):
                raise NativeRuntimeError("原生运行时缺少所选成员的周期事件状态")
            running = native.get("event_cycle_running")
            count = native.get("event_cycle_count")
            if type(running) is not bool or type(count) is not int or count < 0:
                raise NativeRuntimeError("原生运行时缺少可靠的周期事件状态")
            config = info.cycle_config
            return {
                "member": self.partner_infos.canonical(partner_key),
                "function": self._event_name(config["event_name"]) if config else None,
                "interval_ms": self._cycle_interval(config["cycle_time"]) if config else None,
                "running": running,
                "emitted_count": count,
                "source_count": native.get("event_source_count", 0),
                "active_states": native.get("event_active_states", {}),
                "logical_seconds": native.get("event_logical_seconds", 0),
                "synchronized": native.get("synchronized", False),
            }

    def start_event_sync(
        self, events: list[dict[str, Any]], *, paused: bool = True, speed: float = 1
    ) -> dict[str, Any]:
        """一次控制提交全部预编译事件；原生公共时钟采样，不使用 Python 逐周期调用。"""
        with self._lifecycle:
            commands = []
            for event in events:
                commands.append(
                    {
                        "member": self._native_member(event["member"]),
                        "function": self._event_name(event["function"]),
                        "args": deepcopy(event["args"]),
                        "interval_ms": event["interval_ms"],
                        "sources": self._cycle_sources(event.get("sources"), event.get("csv_text")),
                    }
                )
            result = self.sim_operator.send_request(
                "event_sync_start",
                {
                    "group_id": str(uuid4()),
                    "events": commands,
                    "paused": paused,
                    "speed": speed,
                },
            )
            if not isinstance(result, dict) or not result.get("active"):
                raise NativeRuntimeError("原生同步组未能完成准备")
            self._sync_config = {"events": deepcopy(events), "speed": speed}
            logger.info(
                "原生同步组已准备",
                extra={"operation": "soa.event_sync.start", "event_count": len(events)},
            )
            return self._canonical_sync_status(result)

    def _canonical_sync_status(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise NativeRuntimeError("原生同步状态不是字典")
        result = deepcopy(result)
        names = {native: canonical for canonical, native in self._native_keys.items()}
        for event in result.get("events", []):
            event["member"] = names.get(event["member"], event["member"])
        return result

    def event_sync_status(self) -> dict[str, Any]:
        with self._lifecycle:
            result = self._canonical_sync_status(
                self.sim_operator.send_request("event_sync_status", print_result=False)
            )
            if not result["active"]:
                self._sync_config = None
            return result

    def control_event_sync(self, action: str, *, speed: float | None = None) -> dict[str, Any]:
        if action not in ("pause", "resume", "step", "stop", "speed"):
            raise ValueError("未知同步操作")
        with self._lifecycle:
            result = self._canonical_sync_status(
                self.sim_operator.send_request(
                    "event_sync_" + action,
                    {"speed": speed} if action == "speed" else {},
                )
            )
            if self._sync_config is not None:
                self._sync_config["speed"] = result["speed"]
                if not result["active"]:
                    self._sync_config = None
            logger.info("原生同步组操作完成", extra={"operation": "soa.event_sync." + action})
            return result

    def _cycle_update_locked(
        self,
        partner_key: str,
        event_name: str,
        args: Any,
        cycle_time: float | None,
        sources: list[dict[str, Any]] | None = None,
        csv_text: str | None = None,
    ) -> None:
        info = self.partner_infos[partner_key]
        if info.start_event:
            raise AttributeError(f"{partner_key} 周期任务已停止，无法使用 update")
        interval = self._cycle_interval(info.cycle_time if cycle_time is None else cycle_time)
        bindings = self._cycle_sources(sources, csv_text)
        self.sim_operator.send_request(
            "event_cycle_update",
            {
                "member": self._native_member(partner_key),
                "function": self._event_name(event_name),
                "args": args,
                "interval_ms": interval,
                "sources": bindings,
            },
        )
        info.cycle_time = interval / 1000
        info.cycle_config = {
            "event_name": event_name,
            "args": deepcopy(args),
            "cycle_time": interval / 1000,
            "sources": deepcopy([] if sources is None else sources),
            **({"csv_text": csv_text} if csv_text is not None else {}),
        }
        logger.info(
            "原生周期事件已更新",
            extra={"operation": "soa.event_cycle.update", "member": partner_key},
        )

    def send_method_response(
        self,
        partner_key: str,
        method_name: str,
        args: Any = None,
        *,
        request_id: int | None = None,
        return_code: int = 0,
        is_error: bool = False,
    ) -> None:
        message = {
            "action": "response",
            "function": method_name,
            "result": json.dumps({"out": {} if args is None else args}),
            "return_code": return_code,
        }
        if is_error:
            message["message_type"] = 0x81
        if args is None and (is_error or return_code):
            message["payload_hex"] = ""
        if request_id is not None:
            message["request_id"] = request_id
        self._send(partner_key, message)

    @staticmethod
    def _take(
        target: queue.Queue[dict[str, Any]], predicate: Any, timeout: float
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        saved = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待 SOA 消息超时")
                try:
                    message = target.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError("等待 SOA 消息超时") from exc
                if predicate(message):
                    return message
                saved.append(message)
        finally:
            for message in saved:
                if not target.full():
                    target.put_nowait(message)

    def send_request_and_return_resp(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        timeout: float = 1,
        is_async: bool = False,
    ) -> Any:
        response = self._request_response(partner_key, method_name, args, timeout, is_async)
        if response["failtype"] != "FAILTYPE_SUCCESS":
            if response["failtype"] == "FAILTYPE_TIMEOUT":
                raise TimeoutError(f"{method_name} 请求超时")
            if response["failtype"] == "FAILTYPE_BAD_PARAM":
                raise NativeOperationError(response.get("error", response["failtype"]))
            raise NativeRuntimeError(response.get("error", response["failtype"]))
        return json.loads(response["result"])

    def _request_response(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        timeout: float | None,
        is_async: bool,
    ) -> dict[str, Any]:
        info = self.partner_infos[partner_key]
        correlation = str(uuid4())
        waiter: queue.Queue[dict[str, Any]] = queue.Queue()
        with info.waiter_lock:
            info.response_waiters[correlation] = waiter
        try:
            self.send_method_request(
                partner_key, method_name, args, is_async, timeout, _correlation=correlation
            )
            try:
                return waiter.get(timeout=(timeout if timeout is not None else 6) + 0.2)
            except queue.Empty as exc:
                self._method_timing.complete(correlation, "FAILTYPE_TIMEOUT")
                logger.exception(
                    "SOA 方法未收到原生响应或超时回执",
                    extra={"operation": "soa.method.wait", "member": partner_key},
                )
                raise TimeoutError(f"{method_name} 等待响应超时") from exc
        finally:
            with info.waiter_lock:
                info.response_waiters.pop(correlation, None)

    def send_request_and_ck_failtype(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        failtype: FailType | str,
        timeout: float = 6,
        is_async: bool = False,
    ) -> bool:
        response = self._request_response(partner_key, method_name, args, timeout, is_async)
        expected = failtype if isinstance(failtype, str) else failtype.name
        assert response["failtype"] == expected, f"错误码不匹配: {response}"
        return True

    def send_request_and_ck_resp(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        ck_info: dict[str, Any],
        timeout: float = 1,
        cycle_time: float = 0.2,
        is_async: bool = False,
        fuzz_match: bool = True,
        failtype: FailType | str = FailType.FAILTYPE_SUCCESS,
    ) -> bool:
        if (
            not math.isfinite(timeout)
            or timeout <= 0
            or not math.isfinite(cycle_time)
            or cycle_time < 0
        ):
            raise ValueError("响应断言要求正超时和非负重试间隔")
        expected_failtype = failtype if isinstance(failtype, str) else failtype.name
        deadline = time.monotonic() + timeout
        last: Any = None
        while (remaining := deadline - time.monotonic()) > 0:
            response = self._request_response(partner_key, method_name, args, remaining, is_async)
            assert response["failtype"] == expected_failtype, f"响应错误码不匹配: {response}"
            last = json.loads(response["result"])
            if ck_data(last, ck_info) if fuzz_match else last == ck_info:
                return True
            logger.info(
                "响应尚未满足预期，将在超时窗口内重试",
                extra={
                    "operation": "soa.response.retry",
                    "member": partner_key,
                    "method": method_name,
                },
            )
            time.sleep(max(0, min(cycle_time, deadline - time.monotonic())))
        raise AssertionError(f"{timeout}s 内响应未满足预期: 实际={last} 预期={ck_info}")

    def _send_request_and_return_resp_atom(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        timeout: float | None = None,
        is_async: bool = False,
    ) -> dict[str, Any]:
        return self._request_response(partner_key, method_name, args, timeout, is_async)

    def ck_s2s_req(
        self, partner_key: str, interface_name: str, ck_info: Any = None, timeout: float = 1
    ) -> bool:
        message = self._take(
            self.partner_infos[partner_key].req_queue,
            lambda msg: (
                msg["function"] == interface_name
                and (ck_info is None or ck_data(self._request_data(msg), ck_info))
            ),
            timeout,
        )
        return bool(message)

    @staticmethod
    def _request_data(message: dict[str, Any]) -> Any:
        data = json.loads(message["args"])
        return data.get("info", data) if isinstance(data, dict) else data

    def ck_s2s_event(
        self,
        partner_key: str,
        interface_name: str,
        ck_info: Any,
        timeout: float = 3,
        fuzz_match: bool = True,
    ) -> Any:
        message = EventObserver(self.partner_infos[partner_key], ck_data).wait(
            self._event_name(interface_name), ck_info, timeout, fuzzy=fuzz_match
        )
        return json.loads(message["args"])

    def return_latest_event(
        self, partner_key: str, interface_name: str, pop_event: bool = True
    ) -> Any:
        return EventObserver(self.partner_infos[partner_key], ck_data).latest(
            self._event_name(interface_name), pop_event
        )

    def chk_notify(
        self,
        partner_key: str,
        method_name: str,
        ck_info: Any = None,
        timeout: float = 1,
        fuzz_match: bool = True,
    ) -> bool:
        EventObserver(self.partner_infos[partner_key], ck_data).wait(
            self._event_name(method_name), ck_info, timeout, fuzzy=fuzz_match, consume=False
        )
        return True

    def ck_coming_event(
        self,
        partner_key: str,
        interface_name: str,
        ck_info: Any,
        timeout: float = 0.3,
        deviation: float = 0,
        fuzz_match: bool = True,
    ) -> Any:
        if (
            not math.isfinite(timeout)
            or not math.isfinite(deviation)
            or not 0 <= deviation < timeout
        ):
            raise ValueError("事件时序断言要求 0 <= deviation < timeout")
        started = time.monotonic()
        message = EventObserver(self.partner_infos[partner_key], ck_data).wait(
            self._event_name(interface_name),
            ck_info,
            timeout + deviation,
            fuzzy=fuzz_match,
            after=started,
        )
        if deviation:
            elapsed = message["monotonic_timestamp"] - started
            assert timeout - deviation < elapsed < timeout + deviation, (
                f"事件时间偏差不符合预期: {elapsed}s"
            )
        return json.loads(message["args"])

    @staticmethod
    def _getter(event_name: str) -> str:
        return event_name.replace("Notify", "Get") if "Notify" in event_name else f"Get{event_name}"

    def ck_event_and_resp(
        self,
        partner_key: str,
        event_name: str,
        event_info: dict[str, Any],
        method_name: str | None = None,
        method_args: dict[str, Any] | None = None,
        resp_info: Any = None,
        timeout: float = 3,
        fuzz_match: bool = True,
    ) -> bool:
        self.ck_s2s_event(partner_key, event_name, event_info, timeout, fuzz_match)
        expected = {"out": next(iter(event_info.values()))} if resp_info is None else resp_info
        return self.send_request_and_ck_resp(
            partner_key,
            method_name or self._getter(event_name),
            method_args or {},
            expected,
            timeout=timeout,
            fuzz_match=fuzz_match,
        )

    def ck_coming_event_and_resp(
        self,
        partner_key: str,
        event_name: str,
        event_info: dict[str, Any],
        method_name: str | None = None,
        method_args: dict[str, Any] | None = None,
        resp_info: Any = None,
        timeout: float = 3,
        fuzz_match: bool = True,
        deviation: float = 0,
    ) -> bool:
        self.ck_coming_event(partner_key, event_name, event_info, timeout, deviation, fuzz_match)
        expected = {"out": next(iter(event_info.values()))} if resp_info is None else resp_info
        return self.send_request_and_ck_resp(
            partner_key,
            method_name or self._getter(event_name),
            method_args or {},
            expected,
            timeout=timeout,
            fuzz_match=fuzz_match,
        )

    def ck_no_event_and_ck_resp(
        self,
        partner_key: str,
        event_name: str,
        resp_info: Any,
        method_name: str | None = None,
        method_args: dict[str, Any] | None = None,
        timeout: float = 1,
        fuzz_match: bool = True,
    ) -> bool:
        self.ck_no_event(partner_key, event_name, timeout)
        return self.send_request_and_ck_resp(
            partner_key,
            method_name or self._getter(event_name),
            method_args or {},
            resp_info,
            timeout=timeout,
            fuzz_match=fuzz_match,
        )

    def ck_field(
        self,
        partner_key: str,
        field_name: str,
        field_info: dict[str, Any],
        timeout: float = 1,
        deviation: float = 0,
        fuzz_match: bool = True,
    ) -> bool:
        if deviation:
            self.ck_coming_event(
                partner_key, field_name, field_info, timeout, deviation, fuzz_match
            )
        else:
            self.ck_s2s_event(partner_key, field_name, field_info, timeout, fuzz_match)
        expected = {"out": next(iter(field_info.values()))}
        actual = self.send_request_and_return_resp(
            partner_key, f"Get{field_name}", {}, timeout=2, is_async=True
        )
        return ck_data(actual, expected) if fuzz_match else actual == expected

    def ck_s2s_req_v20(
        self,
        partner_key: str,
        interface_name_list: list[str],
        ck_info_list: list[Any] | None = None,
        timeout: float = 1,
    ) -> dict[str, Any]:
        expected = ck_info_list if ck_info_list is not None else [None] * len(interface_name_list)
        if not interface_name_list or len(expected) != len(interface_name_list):
            raise ValueError("请求名称与校验列表必须非空且一一对应")
        remaining = list(zip(interface_name_list, expected, strict=True))
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while remaining:

            def matches(message: dict[str, Any]) -> bool:
                return any(
                    message.get("function") == name
                    and (data is None or data == "" or ck_data(self._request_data(message), data))
                    for name, data in remaining
                )

            last = self._take(
                self.partner_infos[partner_key].req_queue,
                matches,
                max(0, deadline - time.monotonic()),
            )
            for index, (name, data) in enumerate(remaining):
                if last["function"] == name and (
                    data is None or data == "" or ck_data(self._request_data(last), data)
                ):
                    remaining.pop(index)
                    break
        return last

    def ck_no_specific_event(
        self, partner_key: str, interface_name: str, hint: str, timeout: float = 1
    ) -> bool:
        def contains_hint(data: Any) -> bool:
            return isinstance(data, dict) and any(
                item.get("name") == hint for item in data.get("list", [])
            )

        return EventObserver(self.partner_infos[partner_key], ck_data).assert_absent(
            self._event_name(interface_name), timeout, contains_hint
        )

    def register_event(self, partner_key: str, event_names: list[Any] | None = None) -> None:
        self._change_subscriptions(partner_key, event_names or [{"all": 1}], "RegistEvent")

    def _change_subscriptions(self, partner_key: str, names: list[Any], function: str) -> None:
        """等待原生应用后的成员状态；不能把仅发送控制命令当作订阅已经成功。"""
        info = self.partner_infos[partner_key]
        correlation = str(uuid4())
        message = {
            "action": "request",
            "function": function,
            "args": json.dumps({"event_list": names}),
            "correlation_id": correlation,
        }
        if info.thread is threading.current_thread():
            # 回调不能等待自身线程的回执，也不能抢占正在等待回执的生命周期锁。
            self._send(partner_key, message)
            logger.info(
                "订阅控制已从回调发送，状态等待原生回执",
                extra={"operation": "soa.subscriptions.callback", "member": partner_key},
            )
            return
        waiter: queue.Queue[dict[str, Any]] = queue.Queue()
        try:
            with self._lifecycle:
                info = self.partner_infos[partner_key]
                with info.waiter_lock:
                    info.response_waiters[correlation] = waiter
                self._send(partner_key, message)
            response = waiter.get(timeout=3)
            cache_message(info.resp_queue, response)
            if response["failtype"] != "FAILTYPE_SUCCESS":
                raise NativeRuntimeError(response.get("error", response["failtype"]))
            if not isinstance(response.get("subscriptions"), list):
                raise NativeRuntimeError("原生订阅回执缺少状态，不能确认订阅或用于恢复")
            # 只有读线程按实际接收顺序更新状态；等待者不能覆盖更新的回执。
            logger.info(
                "原生订阅配置已确认",
                extra={"operation": "soa.subscriptions", "member": partner_key},
            )
        except Exception:
            logger.exception(
                "原生订阅配置失败", extra={"operation": "soa.subscriptions", "member": partner_key}
            )
            raise
        finally:
            with info.waiter_lock:
                info.response_waiters.pop(correlation, None)

    def ck_no_event(self, partner_key: str, interface_name: str, timeout: float = 1) -> bool:
        return EventObserver(self.partner_infos[partner_key], ck_data).assert_absent(
            self._event_name(interface_name), timeout
        )

    def ck_no_req(
        self, partner_key: str, interface_name: str, timeout: float = 1, ck_info: Any = None
    ) -> bool:
        try:
            message = self._take(
                self.partner_infos[partner_key].req_queue,
                lambda item: (
                    item.get("function") == interface_name
                    and (ck_info is None or ck_data(self._request_data(item), ck_info))
                ),
                timeout,
            )
        except TimeoutError:
            return True
        raise AssertionError(f"观测到不应出现的请求: {message}")

    def unregister_event(self, partner_key: str, event_names: list[Any] | None = None) -> None:
        self._change_subscriptions(partner_key, event_names or ["all"], "UnRegistEvent")

    def register_callback(self, partner_key: str, func: Any) -> None:
        self.partner_infos[partner_key].callback.append(func)

    def unregister_callback(self, partner_key: str, func: Any) -> None:
        self.partner_infos[partner_key].callback.remove(func)

    def register_auto_response(
        self, partner_key: str, interface_name: str, *, echo: bool = False
    ) -> None:
        """默认遵循 SAT 自定义处理器语义；echo 仅为显式启用的协议互通测试扩展。"""
        canonical = self.partner_infos.canonical(partner_key)
        self.auto_response.setdefault(canonical, set()).add(interface_name)
        echoes = self._echo_responses.setdefault(canonical, set())
        if echo:
            echoes.add(interface_name)
        else:
            echoes.discard(interface_name)

    def unregister_auto_response(self, partner_key: str, interface_name: str) -> None:
        canonical = self.partner_infos.canonical(partner_key)
        self.auto_response.get(canonical, set()).discard(interface_name)
        self._echo_responses.get(canonical, set()).discard(interface_name)

    def start_single_partner(
        self, service: str, role: str, instance: str | None = None, heartbeat: int = 600
    ) -> None:
        with self._lifecycle:
            if self._closed:
                raise NativeRuntimeError("SOA 实例已关闭，不能再启动服务")
            self._start_single_partner_locked(service, role, instance, heartbeat)

    def _start_single_partner_locked(
        self, service: str, role: str, instance: str | None, heartbeat: int
    ) -> None:
        cfg = self._members_config([(service, role, instance or service, heartbeat)])
        for alias, config in cfg.items():
            native_key = alias + "_" + config["role"]
            if member_key(alias, config) in self.partner_infos:
                return
            previous = self._member_configs.get(native_key)
            if previous is None:
                previous = next(
                    (
                        item
                        for key, item in self._member_configs.items()
                        if item.get("service", key.rsplit("_", 1)[0]) == service
                        and item.get("role") == config["role"]
                    ),
                    None,
                )
            if previous:
                cfg[alias] = {**deepcopy(previous), **config}
        expected = prepare_identities(self.sim_operator, cfg)
        result = self.sim_operator.send_request("start_config_get_args", json.dumps(cfg))
        verify_identities(self.sim_operator, expected, result)
        self._member_configs.update(
            {f"{alias}_{config['role']}": deepcopy(config) for alias, config in cfg.items()}
        )
        for key, values in result.items():
            self._connect(key, values[0])

    def stop_single_partner(self, partner_key: str) -> None:
        with self._lifecycle:
            self._stop_single_partner_locked(partner_key)

    def _stop_single_partner_locked(self, partner_key: str) -> None:
        alias, role = self._native_member(partner_key).rsplit("_", 1)
        self.sim_operator.send_request(
            "start_config_get_args", {alias: {"role": role, "enable": "disable"}}
        )
        if self._sync_config is not None and not self.event_sync_status()["active"]:
            self._sync_config = None
        self._disconnect(partner_key)

    def _disconnect(self, partner_key: str) -> None:
        key = self.partner_infos.canonical(partner_key)
        info = self.partner_infos[key]
        info.running = False
        if info.socket is not None:
            try:
                info.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                logger.debug("SOA 成员 socket 已停止", exc_info=True)
            info.socket.close()
        if info.thread and info.thread is not threading.current_thread():
            info.thread.join(timeout=2)
        del self.partner_infos[key]
        self._native_keys.pop(key, None)

    def empty_all(self, wait_time: float = 0) -> None:
        if wait_time:
            time.sleep(wait_time)
        self.empty_event_list()
        self.empty_req_list()
        self.empty_resp_list()

    def _empty_channel(self, attribute: str, partner_key: str | None) -> None:
        if partner_key is not None and partner_key not in self.partner_infos:
            raise ValueError(f"SOA 成员尚未实例化: {partner_key}")
        infos = (
            [self.partner_infos[partner_key]] if partner_key else list(self.partner_infos.values())
        )
        for info in infos:
            target = getattr(info, attribute)
            while True:
                try:
                    target.get_nowait()
                except queue.Empty:
                    break

    def empty_event_list(self, partner_key: str | None = None) -> None:
        self._empty_channel("event_queue", partner_key)

    def empty_req_list(self, partner_key: str | None = None) -> None:
        self._empty_channel("req_queue", partner_key)

    def empty_resp_list(self, partner_key: str | None = None) -> None:
        self._empty_channel("resp_queue", partner_key)

    def stop_operators(self) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        self._supervisor.stop()
        with self._lifecycle:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._sync_config is not None:
            try:
                self.control_event_sync("stop")
            except Exception:
                logger.exception("关闭 SOA 时同步组已不可用", extra={"operation": "soa.close"})
                self._sync_config = None
        for key in list(self.partner_infos):
            if not self.partner_infos[key].start_event:
                try:
                    self.send_event_notify_thread_stop(key)
                except Exception:
                    logger.exception(
                        "关闭 SOA 时原生周期任务已不可用", extra={"operation": "soa.close"}
                    )
            self._disconnect(key)
        self.sim_operator.stop_operator()
