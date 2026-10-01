"""SAT 成员配置与连接状态；对象构造不启动进程、不连接网络。"""

from __future__ import annotations

import queue
import socket as socket_module
import threading
from typing import Any

from .naming import members_config
from .operator import NativeRuntimeError


class PartnerStartConfig:
    def __init__(self, service: str, role: str, instance: str, heartbeat: int) -> None:
        config = members_config([(service, role, instance, heartbeat)])
        self.start_service, definition = next(iter(config.items()))
        self.role = definition["role"]
        self.name = instance
        self.status = True
        self.heartbeat = heartbeat

    def args(self) -> dict[str, Any]:
        return {
            self.start_service: {
                "role": self.role,
                "name": self.name,
                "status": self.status,
                "heartbeat": self.heartbeat,
            }
        }


class PartnerKeyInfo:
    """保留 SAT 四参数构造，同时接受原生适配器已有的 socket/ip_port 扩展。"""

    def __init__(
        self,
        service: str | socket_module.socket | None = None,
        role: str | tuple[str, int] | None = None,
        instance: str | None = None,
        heartbeat: int = 600,
        *,
        socket: socket_module.socket | None = None,
        ip_port: tuple[str, int] | None = None,
    ) -> None:
        self.start_config: PartnerStartConfig | None = None
        if isinstance(service, socket_module.socket):
            if (
                socket is not None
                or ip_port is not None
                or not isinstance(role, tuple)
                or instance is not None
            ):
                raise ValueError(
                    "socket 位置构造需要 socket 与地址两个参数，不能混用服务或关键字连接"
                )
            socket, ip_port = service, role
        elif isinstance(service, str):
            if not isinstance(role, str):
                raise ValueError("SAT 成员构造必须指定 client/server 角色")
            self.start_config = PartnerStartConfig(service, role, instance or service, heartbeat)
        elif service is not None or role is not None or instance is not None or socket is None:
            raise ValueError("成员构造需要 SAT 服务配置或显式 socket 连接")
        self.socket = socket
        self.ip_port = ip_port or ("127.0.0.1", 0)
        self.start_args = self.start_config.args() if self.start_config else {}
        self.running = True
        self.instance: str | None = None
        self.application_name: str | None = None
        self.application_id: int | None = None
        self.service_status = "OFFLINE"
        self.req_queue: queue.Queue[dict[str, Any]] = queue.LifoQueue(1000)
        self.resp_queue: queue.Queue[dict[str, Any]] = queue.LifoQueue(1000)
        self.event_queue: queue.Queue[dict[str, Any]] = queue.LifoQueue(1000)
        self.callback: list[Any] = []
        self.method_timeout_ck_callback: dict[str, tuple[float, float | None]] = {}
        self.no_return_methods: set[str] = set()
        self.subscriptions: set[str] | None = None
        self.send_lock = threading.Lock()
        self.changed = threading.Condition()
        self.thread: threading.Thread | None = None
        self.error: BaseException | None = None
        self.response_waiters: dict[str, queue.Queue[dict[str, Any]]] = {}
        self.waiter_lock = threading.Lock()
        self.cycle_time = 1.0
        self.cycle_config: dict[str, Any] | None = None
        self.start_event = True
        self.thread_obj: None = None  # SAT 属性保留，周期发送实际由原生定时器执行。

    def require_socket(self) -> socket_module.socket:
        if self.socket is None:
            raise NativeRuntimeError("SOA 成员尚未建立 socket 连接")
        return self.socket
