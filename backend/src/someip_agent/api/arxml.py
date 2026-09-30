from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from someip_agent.api.dependencies import get_state
from someip_agent.arxml.parser import ArxmlParseError, ArxmlParser
from someip_agent.domain.models import ArxmlModel, ServiceDefinition
from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["arxml"])


@router.post("/arxml/import", response_model=ArxmlModel)
async def import_arxml(
    file: UploadFile = File(...),
    state: ApplicationState = Depends(get_state),
) -> ArxmlModel:
    content = await file.read(state.settings.max_upload_bytes + 1)
    if len(content) > state.settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ARXML 文件超过大小限制")
    source_name = file.filename or "import.arxml"
    try:
        model = ArxmlParser().parse(content, source_name)
        if not model.services:
            detail = model.warnings[-1] if model.warnings else "未解析到可用的 SOME/IP 服务"
            raise ArxmlParseError(detail)
        digest = hashlib.sha256(content).hexdigest()
        target = state.imported_file_path(digest, source_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        await state.set_arxml_model(model)
        state.audit.add(
            action="arxml.import",
            target=source_name,
            detail={
                "sha256": digest,
                "services": len(model.services),
                "warnings": model.warnings,
            },
        )
        logger.info("ARXML 导入完成", extra={"operation": "arxml.import"})
        return model
    except ArxmlParseError as exc:
        logger.exception("ARXML 导入失败", extra={"operation": "arxml.import"})
        state.audit.add(
            action="arxml.import",
            target=source_name,
            success=False,
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/model", response_model=ArxmlModel | None)
async def get_model(state: ApplicationState = Depends(get_state)) -> ArxmlModel | None:
    return await state.get_arxml_model()


@router.get("/model/services", response_model=list[ServiceDefinition])
async def get_services(state: ApplicationState = Depends(get_state)) -> list[ServiceDefinition]:
    model = await state.get_arxml_model()
    return model.services if model else []
