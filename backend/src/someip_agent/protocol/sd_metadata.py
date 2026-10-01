"""校验并展示固定 vsomeip 的结构化 SD 结果；不解析或回退解码原始字节。"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Byte = Annotated[int, Field(ge=0, le=255)]
Word = Annotated[int, Field(ge=0, le=65535)]
Dword = Annotated[int, Field(ge=0, le=0xFFFFFFFF)]
OptionRun = Annotated[list[Byte], Field(max_length=15)]


class NativeSdEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    entry_type: Literal[0, 1, 2, 4, 5, 6, 7]
    service_id: Word
    instance_id: Word
    major_version: Byte
    ttl: Annotated[int, Field(ge=0, le=0xFFFFFF)]
    minor_version: Dword | None
    eventgroup_id: Word | None
    counter: Annotated[int, Field(ge=0, le=15)] | None
    option_indices: Annotated[list[OptionRun], Field(min_length=2, max_length=2)]

    @field_validator("entry_type", mode="before")
    @classmethod
    def validate_type(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("SD Entry 类型必须为整数")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> NativeSdEntry:
        if self.entry_type in (0, 1, 2):
            if (
                self.minor_version is None
                or self.eventgroup_id is not None
                or self.counter is not None
            ):
                raise ValueError("SD Service Entry 的结构化字段不一致")
        elif self.minor_version is not None or self.eventgroup_id is None or self.counter is None:
            raise ValueError("SD Eventgroup Entry 的结构化字段不一致")
        for indices in self.option_indices:
            if indices and indices != list(range(indices[0], indices[0] + len(indices))):
                raise ValueError("SD Option run 必须连续且不能回绕")
        return self

    @property
    def name(self) -> str:
        return {
            0: "FindService",
            1: "OfferService" if self.ttl else "StopOfferService",
            6: "SubscribeEventgroup" if self.ttl else "StopSubscribeEventgroup",
            7: "SubscribeEventgroupAck" if self.ttl else "SubscribeEventgroupNack",
        }.get(self.entry_type, f"Unknown(0x{self.entry_type:02X})")

    def summary(self) -> str:
        suffix = f" eventgroup=0x{self.eventgroup_id:04X}" if self.eventgroup_id is not None else ""
        return (
            f"{self.name} service=0x{self.service_id:04X} instance=0x{self.instance_id:04X}"
            f" ttl={self.ttl}{suffix}"
        )


class NativeSdOption(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    option_type: Byte
    length: Annotated[int, Field(ge=1, le=65535)]


class NativeSdPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    decoder: Literal["vsomeip-3.5.10"]
    flags: Byte
    entries: Annotated[list[NativeSdEntry], Field(max_length=4096)]
    options: Annotated[list[NativeSdOption], Field(max_length=4096)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("SD 结构化版本必须为整数")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> NativeSdPayload:
        for entry in self.entries:
            for indices in entry.option_indices:
                if any(index >= len(self.options) for index in indices):
                    raise ValueError("SD Entry 引用不存在的 Option")
        return self

    def summary(self) -> str:
        return "; ".join(entry.summary() for entry in self.entries) or "SOME/IP-SD（无 Entry）"


def read_sd(metadata: dict[str, Any]) -> NativeSdPayload:
    """元数据缺失或原生报错均显式失败，绝不以 payload_hex 重新解码。"""
    if "sd_error" in metadata:
        error = metadata["sd_error"]
        if not isinstance(error, str) or not error or len(error) > 2048:
            raise ValueError("原生 SD 错误记录不符合契约")
        raise ValueError(f"原生 SD 解码失败: {error}")
    if "sd" not in metadata:
        raise ValueError("缺少原生 vsomeip SD 结构化结果；不使用 Python 解码回退")
    return NativeSdPayload.model_validate(metadata["sd"])


def project_sd(packet: dict[str, Any], logger: logging.Logger) -> tuple[str, dict[str, Any]]:
    try:
        payload = read_sd(packet)
        return payload.summary(), {"sd": payload.model_dump(mode="json")}
    except ValueError as exc:
        logger.exception("原生 SD 观测结果不可用", extra={"operation": "sd.metadata.project"})
        error = str(exc)[:2048]
        return f"SOME/IP-SD 解析失败: {error}", {"sd_error": error}
