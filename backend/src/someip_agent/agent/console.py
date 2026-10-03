"""控制台内置操作：复用业务管理器，不依赖插件，不通过任意 URL 回调。"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from someip_agent.agent.service import AgentTool
from someip_agent.domain.models import ListenerConfig
from someip_agent.runtime.service_models import (
    ServiceCommand,
    ServiceCycleCommand,
    ServiceCycleStop,
    ServiceResponse,
)
from someip_agent.soa.catalog import NativeCatalogRequest

if TYPE_CHECKING:
    from someip_agent.state import ApplicationState


class Identifier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=128)


class SessionCommand(Identifier):
    command: ServiceCommand


class SessionResponse(Identifier):
    command: ServiceResponse


class SessionCycleCommand(Identifier):
    command: ServiceCycleCommand


class SessionCycleStop(Identifier):
    command: ServiceCycleStop


class Navigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: Literal["dashboard", "services", "simulation", "monitor", "pcap", "settings"]


class ConsoleControls:
    """当前本地工作台的有界页面命令，不执行 URL、JavaScript 或 DOM selector。"""

    def __init__(self) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=100)
        self._sequence = 0

    def navigate(self, page: str) -> dict[str, Any]:
        self._sequence += 1
        event = {"id": self._sequence, "action": "navigate", "page": page}
        self._events.append(event)
        return {"status": "queued", "command": event}

    def events(self, after: int | None) -> dict[str, Any]:
        return {
            "last_id": self._sequence,
            "commands": [item for item in self._events if after is not None and item["id"] > after],
        }


def console_tools(state: ApplicationState) -> list[AgentTool]:
    empty = {"type": "object", "properties": {}, "additionalProperties": False}

    async def navigate(args: dict[str, Any], _allow: bool) -> Any:
        request = Navigation.model_validate(args)
        return state.console.navigate(request.page)

    async def clear(args: dict[str, Any], _allow: bool) -> Any:
        if args:
            raise ValueError("clear_monitor 不接受参数")
        await state.monitor.clear()
        return {"cleared": True}

    async def sessions(args: dict[str, Any], _allow: bool) -> Any:
        if args:
            raise ValueError("list_service_sessions 不接受参数")
        return [item.model_dump(mode="json") for item in state.services.statuses()]

    async def start(args: dict[str, Any], _allow: bool) -> Any:
        request = NativeCatalogRequest.model_validate(args)
        model = await state.get_arxml_model()
        if model is None:
            raise ValueError("请先导入 ARXML 服务模型")
        return (await state.services.start(model, request)).model_dump(mode="json")

    async def stop(args: dict[str, Any], _allow: bool) -> Any:
        identifier = Identifier.model_validate(args).identifier
        return (await state.services.stop(identifier)).model_dump(mode="json")

    async def requests(args: dict[str, Any], _allow: bool) -> Any:
        identifier = Identifier.model_validate(args).identifier
        return [item.model_dump(mode="json") for item in state.services.requests(identifier)]

    def command_handler(action: str):
        async def handler(args: dict[str, Any], _allow: bool) -> Any:
            model = SessionResponse if action == "respond" else SessionCommand
            request = model.model_validate(args)
            callback = getattr(state.services, action)
            return (await callback(request.identifier, request.command)).model_dump(mode="json")

        return handler

    async def cycle_status(args: dict[str, Any], _allow: bool) -> Any:
        request = Identifier.model_validate(args)
        return [
            item.model_dump(mode="json") for item in await state.services.cycles(request.identifier)
        ]

    def cycle_handler(action: str):
        async def handler(args: dict[str, Any], _allow: bool) -> Any:
            if action == "stop":
                stop_request = SessionCycleStop.model_validate(args)
                result = await state.services.stop_cycle(
                    stop_request.identifier, stop_request.command
                )
            else:
                request = SessionCycleCommand.model_validate(args)
                result = await state.services.configure_cycle(
                    request.identifier, request.command, update=action == "update"
                )
            return result.model_dump(mode="json")

        return handler

    async def interfaces(args: dict[str, Any], _allow: bool) -> Any:
        if args:
            raise ValueError("list_network_interfaces 不接受参数")
        return await state.network.interfaces()

    async def listeners(args: dict[str, Any], _allow: bool) -> Any:
        if args:
            raise ValueError("list_network_listeners 不接受参数")
        return [item.model_dump(mode="json") for item in state.network.list()]

    async def start_listener(args: dict[str, Any], _allow: bool) -> Any:
        config = ListenerConfig.model_validate(args)
        if set(args) - set(ListenerConfig.model_fields):
            raise ValueError("监听配置包含未知字段")
        return (await state.network.start(config)).model_dump(mode="json")

    async def stop_listener(args: dict[str, Any], _allow: bool) -> Any:
        identifier = Identifier.model_validate(args).identifier
        if not any(item.id == identifier for item in state.network.list()):
            raise ValueError("指定监听不存在，禁止停止全部监听")
        return [item.model_dump(mode="json") for item in await state.network.stop(identifier)]

    return [
        AgentTool(
            "navigate_console",
            "切换已连接工作台页面；返回 queued 不代表浏览器已应用命令",
            Navigation.model_json_schema(),
            False,
            navigate,
        ),
        AgentTool("clear_monitor", "清空监控缓存；属于写操作", empty, True, clear),
        AgentTool("list_service_sessions", "读取原生服务会话状态", empty, False, sessions),
        AgentTool(
            "get_service_cycles",
            "读取真实服务的原生周期通知状态与计数",
            Identifier.model_json_schema(),
            False,
            cycle_status,
        ),
        *[
            AgentTool(
                f"service_cycle_{action}",
                f"{description}指定真实服务的完整事件周期通知；属于写操作，不猜测 ARXML 布局",
                (SessionCycleStop if action == "stop" else SessionCycleCommand).model_json_schema(),
                True,
                cycle_handler(action),
            )
            for action, description in (("start", "启动"), ("update", "更新"), ("stop", "停止"))
        ],
        AgentTool(
            "start_service_session",
            "按当前 ARXML 启动原生服务会话；网络发送仍需目的地址白名单和部署授权",
            NativeCatalogRequest.model_json_schema(),
            True,
            start,
        ),
        AgentTool(
            "stop_service_session",
            "仅停止指定会话；属于写操作",
            Identifier.model_json_schema(),
            True,
            stop,
        ),
        AgentTool(
            "get_service_requests",
            "读取指定会话收到的请求",
            Identifier.model_json_schema(),
            False,
            requests,
        ),
        *[
            AgentTool(
                f"service_{action}",
                description,
                (SessionResponse if action == "respond" else SessionCommand).model_json_schema(),
                True,
                command_handler(action),
            )
            for action, description in (
                ("call", "调用指定会话的 RPC/字段接口；属于写操作"),
                ("notify", "发送指定会话的事件通知；属于写操作"),
                ("respond", "回复指定会话收到的请求；属于写操作"),
            )
        ],
        AgentTool("list_network_interfaces", "读取可捕获网卡", empty, False, interfaces),
        AgentTool("list_network_listeners", "读取网络监听状态", empty, False, listeners),
        AgentTool(
            "start_network_listener",
            "启动指定监听；属于写操作",
            ListenerConfig.model_json_schema(),
            True,
            start_listener,
        ),
        AgentTool(
            "stop_network_listener",
            "仅停止指定监听；禁止全停",
            Identifier.model_json_schema(),
            True,
            stop_listener,
        ),
    ]
