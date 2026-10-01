"""本地持久原生解码会话；复用Codec及控制socket，不构建车辆网络端点。"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Sequence
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.domain.models import SignalDefinition
from someip_agent.soa.catalog import parameter_schema
from someip_agent.soa.operator import NativeOperationError, NativeRuntimeError, SOAOperator

logger = logging.getLogger(__name__)


class NativePayloadError(ValueError):
    pass


class NativeSignalDecoder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._operator: SOAOperator | None = None
        self._lock = RLock()
        self._closed = False

    def _runtime(self) -> SOAOperator:
        if self._closed:
            raise NativeRuntimeError("原生信号解码会话已关闭")
        if self._operator and self._operator.process and self._operator.process.poll() is None:
            return self._operator
        if self._operator:
            self._operator.stop_operator()
        operator = SOAOperator(
            "payload-decoder",
            operator_port=0,
            binary=self.settings.native_binary,
            mode="network",
            log_path=self.settings.data_dir / "native-payload" / f"{uuid4()}.log",
        )
        self._operator = operator
        try:
            operator.run_operator()
            operator.create_socket()
        except Exception:
            logger.exception("原生信号解码会话启动失败", extra={"operation": "payload.start"})
            operator.stop_operator()
            self._operator = None
            raise
        return operator

    def decode_many(
        self, payloads: Sequence[str], signals: Sequence[SignalDefinition]
    ) -> list[dict[str, Any]]:
        if not payloads:
            return []
        if len(signals) > 100:
            raise NativePayloadError("观测信号数量超过100个")
        schema = parameter_schema(list(signals))
        results: list[dict[str, Any]] = []
        with self._lock:
            if self._closed:
                raise NativeRuntimeError("原生信号解码会话已关闭")
            pending = deque(payloads)
            while pending:
                batch: list[str] = []
                size = 0
                while pending and len(batch) < 32:
                    payload = pending[0]
                    if len(payload) > 131072:
                        raise NativePayloadError("单个观测payload超过64KiB")
                    if batch and size + len(payload) > 524288:
                        break
                    batch.append(pending.popleft())
                    size += len(payload)
                try:
                    response = self._runtime().send_request(
                        "payload_decode", {"schema": schema, "payloads": batch}, print_result=False
                    )
                except NativeOperationError as exc:
                    raise NativePayloadError(str(exc)) from exc
                except (OSError, EOFError, NativeRuntimeError) as exc:
                    logger.exception("原生信号解码通道失败", extra={"operation": "payload.channel"})
                    if self._operator:
                        self._operator.stop_operator()
                        self._operator = None
                    raise NativeRuntimeError("原生信号解码不可用，不使用Python回退") from exc
                if (
                    not isinstance(response, dict)
                    or type(response.get("schema_version")) is not int
                    or response["schema_version"] != 1
                    or response.get("decoder") != "someip-agent-native-codec"
                    or not isinstance(response.get("records"), list)
                    or len(response["records"]) != len(batch)
                ):
                    raise NativePayloadError("原生信号解码结果契约不匹配")
                for record in response["records"]:
                    if not isinstance(record, dict) or set(record) not in ({"values"}, {"error"}):
                        raise NativePayloadError("原生信号记录结构非法")
                    if "error" in record:
                        if (
                            not isinstance(record["error"], str)
                            or not 0 < len(record["error"]) <= 2048
                        ):
                            raise NativePayloadError("原生信号错误记录非法")
                    else:
                        values = record["values"]
                        if not isinstance(values, dict) or set(values) != {s.name for s in signals}:
                            raise NativePayloadError("原生信号解码参数名称不一致")
                        pending_values = list(values.values())
                        while pending_values:
                            value = pending_values.pop()
                            if value is None or (
                                isinstance(value, float) and not math.isfinite(value)
                            ):
                                raise NativePayloadError("原生信号返回非有限值或null")
                            if isinstance(value, dict):
                                pending_values.extend(value.values())
                            elif isinstance(value, list):
                                pending_values.extend(value)
                            elif type(value) not in (str, int, float, bool):
                                raise NativePayloadError("原生信号返回非JSON值")
                        for signal in signals:
                            value = values[signal.name]
                            if (
                                isinstance(value, (int, float))
                                and not isinstance(value, bool)
                                and (signal.factor != 1 or signal.offset != 0)
                            ):
                                value = value * signal.factor + signal.offset
                                if not math.isfinite(value):
                                    raise NativePayloadError("物理值比例换算结果不是有限值")
                                values[signal.name] = value
                    results.append(record)
        return results

    def decode(self, payload: str, signals: Sequence[SignalDefinition]) -> dict[str, Any]:
        result = self.decode_many([payload], signals)[0]
        if "error" in result:
            raise NativePayloadError(result["error"])
        return cast(dict[str, Any], result["values"])

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._operator:
                self._operator.stop_operator()
                self._operator = None
