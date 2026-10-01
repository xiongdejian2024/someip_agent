from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from someip_agent.api.dependencies import get_state
from someip_agent.domain.models import PcapImportResult
from someip_agent.pcap.importer import PcapImporter, PcapImportError
from someip_agent.soa.operator import NativeRuntimeError
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pcap"])


@router.post("/pcap/import", response_model=PcapImportResult)
async def import_pcap(
    file: UploadFile = File(...),
    state: ApplicationState = Depends(get_state),
) -> PcapImportResult:
    content = await file.read(state.settings.max_upload_bytes + 1)
    if len(content) > state.settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "抓包文件超过大小限制")
    source_name = file.filename or "capture.pcap"
    try:
        result, messages = await asyncio.to_thread(
            PcapImporter(state.settings).parse, content, source_name
        )
        await state.monitor.publish_many(state.enrich_message(message) for message in messages)
        state.audit.add(
            action="pcap.import",
            target=source_name,
            detail=result.model_dump(mode="json"),
        )
        logger.info("PCAP 导入完成", extra={"operation": "pcap.import"})
        return result
    except PcapImportError as exc:
        logger.exception("PCAP 导入失败", extra={"operation": "pcap.import"})
        state.audit.add(
            action="pcap.import",
            target=source_name,
            success=False,
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except NativeRuntimeError as exc:
        logger.exception("原生 PCAP 运行时不可用", extra={"operation": "pcap.import.native"})
        state.audit.add(
            action="pcap.import",
            target=source_name,
            success=False,
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
