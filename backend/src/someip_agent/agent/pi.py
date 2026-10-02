"""Pi Agent 的有界子进程桥接；不加载用户插件、Skills 或编码代理工具。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

from someip_agent.config import Settings

logger = logging.getLogger(__name__)
MAX_LINE = 1024 * 1024


class PiRuntimeError(RuntimeError):
    pass


class PiRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._processes: set[asyncio.subprocess.Process] = set()
        self._slots = asyncio.Semaphore(4)

    def paths(self) -> tuple[str, Path]:
        bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[4]))
        bundled_node = bundle / "pi" / "node"
        node = str(bundled_node) if bundled_node.is_file() else self.settings.pi_node_binary
        entry = self.settings.pi_runtime_path
        if entry is None:
            packaged = bundle / "pi" / "runtime.mjs"
            entry = packaged if packaged.is_file() else bundle / "agent-runtime/dist/runtime.mjs"
        return node, entry

    def view(self) -> dict[str, Any]:
        node, entry = self.paths()
        return {
            "name": "pi-agent-core",
            "version": "1.0.0",
            "ready": entry.is_file() and shutil.which(node) is not None,
            "plugins_enabled": False,
            "skills_enabled": False,
            "active_sessions": len(self._processes),
            "max_sessions": 4,
        }

    async def shutdown(self) -> None:
        await asyncio.gather(*(self._stop(process) for process in list(self._processes)))

    async def _stop(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), 2)
            except ProcessLookupError:
                await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        self._processes.discard(process)

    @staticmethod
    async def _stderr(process: asyncio.subprocess.Process, key: str) -> None:
        assert process.stderr is not None
        # stderr 仅保留脱敏后有界文本；继续消费，防止子进程被管道背压卡死。
        total = 0
        while line := await process.stderr.readline():
            text = line.decode("utf-8", errors="replace").replace(key, "[已隐去密钥]")
            if total < 64 * 1024:
                logger.error("Pi 运行时日志: %s", text[:4096].rstrip())
            total += len(line)

    async def stream(
        self,
        payload: dict[str, Any],
        scope: Callable[[str, dict[str, Any]], dict[str, Any]],
        execute: Callable[[str, dict[str, Any]], Awaitable[Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        node, entry = self.paths()
        if not self.view()["ready"]:
            raise PiRuntimeError("Pi 内置运行时不可用；源码部署请先执行 make install-agent")
        key = payload["api_key"]
        encoded = (json.dumps({"type": "start", **payload}, ensure_ascii=False) + "\n").encode()
        if len(encoded) > MAX_LINE:
            raise PiRuntimeError("Pi 会话上下文超过大小上限，请缩小范围")
        process = None
        stderr_task = None
        acquired = False
        # 排队也受会话超时约束；每个请求独立进程，历史只由当前请求明确传入。
        try:
            deadline = asyncio.get_running_loop().time() + payload["timeout_ms"] / 1000

            async def bounded(awaitable):
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                return await asyncio.wait_for(awaitable, remaining)

            await bounded(self._slots.acquire())
            acquired = True
            # 不继承模型密钥或 Pi 插件配置。凭据只经过私有 stdin，不进 argv。
            environment = {
                name: os.environ[name]
                for name in (
                    "PATH",
                    "SystemRoot",
                    "TMPDIR",
                    "LANG",
                    "SSL_CERT_FILE",
                    "NODE_EXTRA_CA_CERTS",
                )
                if name in os.environ
            }
            process = await bounded(
                asyncio.create_subprocess_exec(
                    node,
                    str(entry),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                    limit=MAX_LINE,
                )
            )
            self._processes.add(process)
            logger.info("Pi 会话进程已启动", extra={"operation": "agent.pi.start"})
            stderr_task = asyncio.create_task(self._stderr(process, key))
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(encoded)
            await bounded(process.stdin.drain())
            completed = False
            sequence = 0
            while line := await bounded(process.stdout.readline()):
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise PiRuntimeError("Pi 桥接返回无效 JSON") from exc
                if not isinstance(event, dict):
                    raise PiRuntimeError("Pi 桥接返回无效事件")
                kind = event.get("type")
                if kind == "tool_call":
                    sequence += 1
                    name, arguments = event.get("name"), event.get("arguments")
                    if (
                        sequence > 16
                        or event.get("id") != sequence
                        or not isinstance(name, str)
                        or not isinstance(arguments, dict)
                    ):
                        raise PiRuntimeError("Pi 工具请求不符合协议")
                    arguments = scope(name, arguments)
                    data = {
                        "id": event.get("call_id"),
                        "name": name,
                        "arguments": arguments,
                    }
                    yield {"event": "tool", "data": {**data, "phase": "start"}}
                    result = await bounded(execute(name, arguments))
                    reply = (
                        json.dumps(
                            {"type": "tool_result", "id": sequence, "result": result},
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    ).encode()
                    if len(reply) > MAX_LINE:
                        raise PiRuntimeError("工具结果超过桥接大小上限")
                    process.stdin.write(reply)
                    await process.stdin.drain()
                    yield {
                        "event": "tool",
                        "data": {**data, "phase": "result", "result": result},
                    }
                elif kind == "delta" and isinstance(event.get("text"), str):
                    yield {"event": "delta", "data": {"text": event["text"]}}
                elif kind == "status":
                    yield {
                        "event": "status",
                        "data": {
                            "phase": "generating",
                            "message": "Pi Agent 正在生成回答",
                            "round": event.get("round"),
                        },
                    }
                elif kind == "tool_rejected":
                    yield {
                        "event": "tool",
                        "data": {
                            "phase": "result",
                            "id": event.get("call_id"),
                            "name": event.get("name"),
                            "arguments": {},
                            "result": event.get("result"),
                        },
                    }
                elif kind == "error":
                    raise PiRuntimeError(
                        str(event.get("message", "Pi 执行失败")).replace(key, "[已隐去密钥]")
                    )
                elif kind == "done":
                    completed = True
                    break
                else:
                    raise PiRuntimeError("Pi 桥接返回未知事件")
            if not completed:
                raise PiRuntimeError("Pi 会话提前结束，已收到内容已保留")
            # 必须等待进程正常退出；done 不是遗漏子进程退出错误的理由。
            if await bounded(process.wait()) != 0:
                raise PiRuntimeError("Pi 会话进程异常退出")
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.exception("Pi 会话超时", extra={"operation": "agent.pi.timeout"})
            raise PiRuntimeError("Pi 会话超时，已收到内容已保留，请重试") from exc
        except (OSError, ValueError) as exc:
            logger.exception("Pi 桥接失败", extra={"operation": "agent.pi.bridge"})
            raise PiRuntimeError("Pi 内置运行时启动或通信失败，请检查后端日志") from exc
        finally:
            if acquired:
                self._slots.release()
            if process is not None:
                await self._stop(process)
            if stderr_task is not None:
                results = await asyncio.gather(stderr_task, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(
                            "Pi 日志读取失败", exc_info=(type(result), result, result.__traceback__)
                        )
            logger.info("Pi 会话已清理", extra={"operation": "agent.pi.stop"})
