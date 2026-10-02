from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_MODELS = (
    "qwen3.5-plus",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.2",
    "deepseek-v4.1-flash",
)

DEFAULT_LLM_BASE_URL = "https://voyahgpt-gateway.voyah.cn/api/gateway/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOMEIP_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    data_dir: Path = Path("./data")
    static_dir: Path | None = None
    open_browser: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str = ""
    llm_model: str = SUPPORTED_MODELS[0]
    llm_timeout_seconds: float = 60.0
    llm_temperature: float = 0.1
    pi_node_binary: str = "node"
    pi_runtime_path: Path | None = None

    update_manifest_url: str = ""
    update_public_key: str = ""
    update_install_root: Path | None = None
    update_helper_binary: Path | None = None
    update_max_download_bytes: int = 1024 * 1024 * 1024

    network_send_enabled: bool = False
    native_binary: str = "soa_partner"
    native_unicast: str = "127.0.0.1"
    allowed_destinations: list[str] = Field(default_factory=list)
    monitor_capacity: int = 20_000
    max_upload_bytes: int = 256 * 1024 * 1024

    @field_validator("allowed_destinations", "cors_origins", mode="before")
    @classmethod
    def parse_json_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
