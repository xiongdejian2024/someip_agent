"""有界后台证据导出与请求取消清理，生成任务不会因 HTTP 断开而失控。"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)
_exports = asyncio.Semaphore(2)


async def finish(path: Path) -> None:
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception:
        logger.exception("证据下载临时文件清理失败", extra={"operation": "evidence.cleanup"})
    finally:
        _exports.release()


async def export_file(generate: Callable[[], Path], filename: str) -> FileResponse:
    await _exports.acquire()
    job = asyncio.create_task(asyncio.to_thread(generate))
    try:
        path = await asyncio.shield(job)
    except asyncio.CancelledError:
        logger.exception(
            "证据下载请求取消，等待生成任务后清理", extra={"operation": "evidence.cancel"}
        )
        try:
            path = await job
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except Exception:
            logger.exception(
                "证据取消后的生成或清理失败", extra={"operation": "evidence.cancel.cleanup"}
            )
        finally:
            _exports.release()
        raise
    except BaseException:
        _exports.release()
        raise
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(finish, path),
    )
