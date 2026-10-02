from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class MessageType(IntEnum):
    REQUEST = 0x00
    REQUEST_NO_RETURN = 0x01
    NOTIFICATION = 0x02
    REQUEST_ACK = 0x40
    REQUEST_NO_RETURN_ACK = 0x41
    NOTIFICATION_ACK = 0x42
    RESPONSE = 0x80
    ERROR = 0x81
    RESPONSE_ACK = 0xC0
    ERROR_ACK = 0xC1


class ReturnCode(IntEnum):
    E_OK = 0x00
    E_NOT_OK = 0x01
    E_UNKNOWN_SERVICE = 0x02
    E_UNKNOWN_METHOD = 0x03
    E_NOT_READY = 0x04
    E_NOT_REACHABLE = 0x05
    E_TIMEOUT = 0x06
    E_WRONG_PROTOCOL_V = 0x07
    E_WRONG_INTERFACE_V = 0x08
    E_MALFORMED_MESSAGE = 0x09
    E_WRONG_MESSAGE_TYPE = 0x0A


class SignalDataType(str, Enum):
    BOOLEAN = "boolean"
    UINT8 = "uint8"
    UINT16 = "uint16"
    UINT32 = "uint32"
    UINT64 = "uint64"
    INT8 = "int8"
    INT16 = "int16"
    INT32 = "int32"
    INT64 = "int64"
    FLOAT32 = "float32"
    FLOAT64 = "float64"
    STRING = "string"
    BYTES = "bytes"
    STRUCT = "struct"
    ARRAY = "array"


class SignalDefinition(BaseModel):
    name: str
    path: str = ""
    data_type: SignalDataType = SignalDataType.UINT32
    byte_order: Literal["big", "little"] = "big"
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    factor: float = 1.0
    offset: float = 0.0
    type_ref: str | None = None
    wire_schema: dict[str, Any] | None = None
    wire_error: str | None = None


class ClassicHeaderProperties(BaseModel):
    """保留 transformer 原始属性，不把模型枚举映射为线上报文类型。"""

    transformer_path: str
    signal_props_present: bool
    description_count: int
    protocol_raw: str | None = None
    transformer_version_raw: str | None = None
    header_length_bits_raw: str | None = None
    message_type_raw: str | None = None
    session_handling_sr_raw: str | None = None
    signal_interface_version_raw: str | None = None
    description_interface_version_raw: str | None = None


class ClassicSignalBinding(BaseModel):
    """Classic 部署的完整源引用；不是已验证的 payload 布局。"""

    triggering_path: str
    pdu_path: str
    mapping_path: str
    signal_path: str
    system_signal_path: str
    target_path: str
    direction: Literal["input", "output", "data"]
    start_position: int
    transformation_paths: list[str]
    transformer_paths: list[str]
    header_properties: list[ClassicHeaderProperties] = Field(default_factory=list)


class MethodDefinition(BaseModel):
    name: str
    path: str = ""
    method_id: int | None = None
    input_signals: list[SignalDefinition] = Field(default_factory=list)
    output_signals: list[SignalDefinition] = Field(default_factory=list)
    fire_and_forget: bool = False
    classic_bindings: list[ClassicSignalBinding] = Field(default_factory=list)


class EventDefinition(BaseModel):
    name: str
    path: str = ""
    event_id: int | None = None
    event_group_ids: list[int] = Field(default_factory=list)
    signals: list[SignalDefinition] = Field(default_factory=list)
    classic_bindings: list[ClassicSignalBinding] = Field(default_factory=list)


class FieldDefinition(BaseModel):
    name: str
    path: str = ""
    getter_id: int | None = None
    setter_id: int | None = None
    notifier_id: int | None = None
    event_group_ids: list[int] = Field(default_factory=list)
    signal: SignalDefinition | None = None


class ServiceDefinition(BaseModel):
    name: str
    path: str = ""
    deployment_path: str | None = None
    deployment_errors: list[str] = Field(default_factory=list)
    service_id: int | None = None
    instance_ids: list[int] = Field(default_factory=list)
    major_version: int = 1
    minor_version: int = 0
    methods: list[MethodDefinition] = Field(default_factory=list)
    events: list[EventDefinition] = Field(default_factory=list)
    fields: list[FieldDefinition] = Field(default_factory=list)


class ArxmlModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_name: str
    source_sha256: str | None = None
    imported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    autosar_version: str | None = None
    services: list[ServiceDefinition] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SomeIpHeaderModel(BaseModel):
    service_id: int
    method_id: int
    length: int
    client_id: int
    session_id: int
    protocol_version: int
    interface_version: int
    message_type: int
    return_code: int


class MonitorMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    direction: Literal["rx", "tx", "sim", "pcap"] = "rx"
    transport: Literal["udp", "tcp", "internal"] = "udp"
    source: str = ""
    destination: str = ""
    service_id: int
    method_id: int
    client_id: int = 0
    session_id: int = 0
    interface_version: int = 1
    message_type: int = MessageType.NOTIFICATION
    return_code: int = ReturnCode.E_OK
    payload_hex: str = ""
    payload_size: int = 0
    is_sd: bool = False
    sd_summary: str | None = None
    signal_values: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneratorKind(str, Enum):
    CONSTANT = "constant"
    SINE = "sine"
    RAMP = "ramp"
    RANDOM = "random"
    SEQUENCE = "sequence"


class SignalGeneratorConfig(BaseModel):
    signal_name: str = "value"
    kind: GeneratorKind = GeneratorKind.SINE
    data_type: SignalDataType = SignalDataType.FLOAT32
    minimum: float = 0.0
    maximum: float = 100.0
    initial: float = 0.0
    period_seconds: float = Field(default=5.0, gt=0)
    sequence: list[float] = Field(default_factory=list)

    @field_validator("data_type")
    @classmethod
    def generator_requires_scalar(cls, value: SignalDataType) -> SignalDataType:
        if value in {SignalDataType.STRUCT, SignalDataType.ARRAY}:
            raise ValueError("标量信号发生器不接受复合类型，请使用服务方法或事件 API")
        return value

    @field_validator("maximum")
    @classmethod
    def maximum_must_be_valid(cls, value: float, info: Any) -> float:
        minimum = info.data.get("minimum")
        if minimum is not None and value < minimum:
            raise ValueError("maximum 必须大于或等于 minimum")
        return value


class SimulationConfig(BaseModel):
    name: str = "signal-simulation"
    service_id: int = Field(ge=0, le=0xFFFF)
    instance_id: int = Field(default=1, ge=0, le=0xFFFF)
    method_id: int = Field(ge=0, le=0xFFFF)
    interface_version: int = Field(default=1, ge=0, le=0xFF)
    interval_ms: int = Field(default=100, ge=10, le=60_000)
    transport: Literal["internal", "udp"] = "internal"
    destination_host: str = "127.0.0.1"
    destination_port: int = Field(default=30501, ge=1, le=65535)
    enable_sd: bool = True
    sd_multicast_group: str = "239.192.255.251"
    sd_port: int = Field(default=30490, ge=1, le=65535)
    sd_ttl: int = Field(default=3, ge=1, le=0xFFFFFF)
    sd_offer_cycle_ms: int = Field(default=1000, ge=100, le=60_000)
    generator: SignalGeneratorConfig = Field(default_factory=SignalGeneratorConfig)


class SimulationStatus(BaseModel):
    id: str
    config: SimulationConfig
    running: bool
    started_at: datetime
    emitted_count: int = 0
    last_value: float | None = None
    last_error: str | None = None


class ListenerConfig(BaseModel):
    name: str = "someip-listener"
    mode: Literal["socket", "pcap"] = "socket"
    capture_interface: str | None = Field(default=None, min_length=1, max_length=256)
    capture_filter: str = Field(
        default="udp port 30490 or udp port 30500 or tcp port 30500",
        min_length=1,
        max_length=4096,
    )
    promiscuous: bool = False
    transport: Literal["udp", "tcp"] = "udp"
    bind_host: str = "0.0.0.0"
    port: int = Field(default=30490, ge=1, le=65535)
    multicast_group: str | None = None
    interface_ip: str = "0.0.0.0"

    @model_validator(mode="after")
    def validate_capture_interface(self) -> ListenerConfig:
        if self.mode == "pcap" and not self.capture_interface:
            raise ValueError("被动抓包必须明确选择网卡")
        return self


class ListenerStatus(BaseModel):
    id: str
    config: ListenerConfig
    running: bool = True
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    received_count: int = 0
    parse_error_count: int = 0
    last_error: str | None = None
    captured_count: int = 0
    kernel_dropped_count: int | None = None
    interface_dropped_count: int | None = None
    active_streams: int = 0
    active_fragment_datagrams: int = 0
    fragment_buffered_bytes: int = 0
    reassembled_datagrams: int = 0
    fragment_error_count: int = 0


class PcapEndpointStat(BaseModel):
    endpoint: str
    ip_version: Literal[4, 6]
    is_multicast: bool = False
    packet_count: int = 0
    sent_count: int = 0
    received_count: int = 0
    someip_count: int = 0
    transport_counts: dict[str, int] = Field(default_factory=dict)
    offered_service_ids: list[int] = Field(default_factory=list)


class PcapImportResult(BaseModel):
    source_name: str
    packet_count: int
    captured_bytes: int
    someip_count: int
    someip_packet_count: int
    sd_count: int
    sd_packet_count: int
    skipped_count: int
    duration_seconds: float
    start_time: datetime | None = None
    end_time: datetime | None = None
    endpoint_count: int
    top_endpoints: list[PcapEndpointStat] = Field(default_factory=list)
    transport_counts: dict[str, int] = Field(default_factory=dict)
    protocol_counts: dict[str, int] = Field(default_factory=dict)
    sd_entry_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    runtime: Literal["vsomeip"] = "vsomeip"
    link_type: int | None = None
    reassembled_datagrams: int = 0
    fragment_error_count: int = 0


class LlmSettingsUpdate(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = Field(default=60.0, ge=1, le=600)
    temperature: float = Field(default=0.1, ge=0, le=2)


class LlmSettingsView(BaseModel):
    base_url: str
    model: str
    supported_models: list[str]
    api_key_configured: bool
    timeout_seconds: float
    temperature: float


class AgentContext(BaseModel):
    """仅接受工作台目标引用，禁止把客户端状态当成服务端工程事实。"""

    model_config = ConfigDict(extra="forbid")
    page: Literal["dashboard", "services", "simulation", "monitor", "pcap", "settings"]
    service_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    method_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    message_id: str | None = Field(default=None, max_length=128)
    simulation_id: str | None = Field(default=None, max_length=128)
    signal_name: str | None = Field(default=None, max_length=256)
    source: Literal["live", "pcap"] | None = None
    frozen: bool | None = None


class AgentHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    allow_mutation: bool = False
    context: AgentContext | None = None
    history: list[AgentHistoryMessage] = Field(default_factory=list, max_length=12)

    @field_validator("history")
    @classmethod
    def bounded_history(cls, value: list[AgentHistoryMessage]) -> list[AgentHistoryMessage]:
        if sum(len(item.content) for item in value) > 24_000:
            raise ValueError("会话历史总长度不能超过 24000 字符")
        return value


class AgentToolTrace(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result: Any


class AgentChatResponse(BaseModel):
    answer: str
    model: str
    traces: list[AgentToolTrace] = Field(default_factory=list)
    degraded: bool = False
    runtime: Literal["pi-agent-core", "local-evidence-engine"] = "pi-agent-core"


class UpdateInfo(BaseModel):
    current_version: str
    latest_version: str | None = None
    available: bool = False
    release_notes: str | None = None
    download_url: str | None = None
    sha256: str | None = None
    signature_verified: bool = False


class UpdateInstallationStatus(BaseModel):
    installation_id: str
    version: str
    status: Literal["prepared", "complete", "failed"]
    rollback_completed: bool = False
    rollback_failed: bool = False
    restored_version: str | None = None
    error: str | None = None
