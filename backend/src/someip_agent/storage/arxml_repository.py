from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from someip_agent.domain.models import ArxmlModel

_STORAGE_VERSION = 2


class ArxmlModelRepository:
    """持久化当前激活的 ARXML 投影模型。"""

    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir
        self._current_path = storage_dir / "current.json"

    @property
    def current_path(self) -> Path:
        return self._current_path

    def load(self) -> ArxmlModel | None:
        if not self._current_path.is_file():
            return None
        document: Any = json.loads(self._current_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("storage_version") != _STORAGE_VERSION:
            return None
        return ArxmlModel.model_validate(document.get("model"))

    def save(self, model: ArxmlModel) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._current_path.with_suffix(".json.tmp")
        document = {
            "storage_version": _STORAGE_VERSION,
            "model": model.model_dump(mode="json"),
        }
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._current_path)
