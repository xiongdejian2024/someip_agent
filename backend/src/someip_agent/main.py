from __future__ import annotations

import logging
import sys
import threading
import uuid
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from someip_agent.api import api_router
from someip_agent.config import Settings, settings
from someip_agent.logging_config import configure_logging
from someip_agent.state import ApplicationState
from someip_agent.version import __version__

logger = logging.getLogger(__name__)


def _resolve_static_dir(app_settings: Settings) -> str | None:
    candidates: list[Path] = []
    if app_settings.static_dir:
        candidates.append(app_settings.static_dir)
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "web")
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "index.html").is_file():
            return str(resolved)
    return None


def create_app(app_settings: Settings | None = None) -> FastAPI:
    current_settings = app_settings or settings
    configure_logging(current_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = ApplicationState(current_settings)
        app.state.container = container
        logger.info("SOME/IP Agent 后端启动", extra={"operation": "application.start"})
        try:
            yield
        finally:
            await container.shutdown()
            logger.info("SOME/IP Agent 后端停止", extra={"operation": "application.stop"})

    app = FastAPI(
        title="SOME/IP Agent Enterprise API",
        description="ARXML 驱动的 SOME/IP/SD 仿真、监控、PCAP 与智能体平台",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=current_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "未处理的请求异常",
                extra={"request_id": request_id, "operation": "http.request"},
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "内部服务错误", "request_id": request_id},
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app.include_router(api_router)
    static_dir = _resolve_static_dir(current_settings)
    if static_dir:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web-console")
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {
                "name": "SOME/IP Agent Enterprise",
                "version": __version__,
                "docs": "/docs",
            }

    return app


app = create_app()


def run() -> None:
    if settings.open_browser:
        browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
        timer = threading.Timer(
            1.5,
            lambda: webbrowser.open(f"http://{browser_host}:{settings.port}"),
        )
        timer.daemon = True
        timer.start()
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.host, port=settings.port, log_config=None)
    )

    def request_shutdown() -> None:
        logger.info("升级准备完成，停止主程序", extra={"operation": "update.shutdown"})
        server.should_exit = True

    app.state.request_shutdown = request_shutdown
    server.run()


if __name__ == "__main__":
    run()
