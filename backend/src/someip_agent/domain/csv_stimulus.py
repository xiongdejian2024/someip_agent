"""有界 CSV 激励预编译；只处理用户给定文本，不读取路径或执行单元格。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
from decimal import Decimal
from typing import Any, cast

logger = logging.getLogger(__name__)
CSV_MAX_BYTES = 1024 * 1024
CSV_MAX_ROWS = 8192
CSV_MAX_COLUMNS = 128
CSV_MAX_CELL_CHARS = 65536
CSV_COMPILED_MAX_BYTES = 2 * 1024 * 1024


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value) or (value == 0 and Decimal(token) != 0):
        raise ValueError("CSV 浮点值溢出或下溢")
    return value


def _cell(text: str) -> int | float | bool | str:
    if len(text) > CSV_MAX_CELL_CHARS:
        raise ValueError("CSV 单元格超过 65536 字符")
    value = json.loads(text, parse_float=_finite_float)
    if type(value) not in (int, float, bool, str):
        raise ValueError("CSV 单元格必须为标量 JSON 字面量，不接受 null、结构或数组")
    if type(value) is int and not -(2**63) <= value <= 2**64 - 1:
        raise ValueError("CSV 整数超出 int64／uint64 范围")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("CSV 浮点值必须有限")
    return cast(int | float | bool | str, value)


def compile_csv_stimulus(text: str | None) -> list[dict[str, Any]]:
    """按时间零阶保持，生成只供原生使用的 typed CSV timeline 绑定。"""
    if text is None:
        return []
    try:
        if type(text) is not str or not text:
            raise ValueError("CSV 文本不能为空，停用 CSV 请使用 null 或省略字段")
        raw = text.encode("utf-8", errors="strict")
        if len(raw) > CSV_MAX_BYTES:
            raise ValueError("CSV 文本超过 1 MiB")
        reader = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
        header = next(reader, [])
        if not 2 <= len(header) <= CSV_MAX_COLUMNS + 1 or header[0] != "time_ms":
            raise ValueError("CSV 首列必须为 time_ms，后接 1–128 个 JSON Pointer 信号列")
        paths = header[1:]
        if len(set(paths)) != len(paths):
            raise ValueError("CSV 信号列路径不能重复")
        if any((path and not path.startswith("/")) or len(path.encode()) > 512 for path in paths):
            raise ValueError("CSV 信号列必须为最多 512 字节的 JSON Pointer，根标量用空列名")
        sources: list[dict[str, Any]] = [
            {"path": path, "generator": {"kind": "csv", "timeline": []}} for path in paths
        ]
        budget = len(json.dumps(sources).encode())
        rows = 0
        previous = -1
        for row in reader:
            rows += 1
            if rows > CSV_MAX_ROWS:
                raise ValueError("CSV 超过 8192 行数据")
            if len(row) != len(header):
                raise ValueError(f"CSV 第 {rows} 行列数与表头不一致")
            clock = _cell(row[0])
            if type(clock) is not int:
                raise ValueError(f"CSV 第 {rows} 行时间必须为 uint64 整型毫秒")
            at = cast(int, clock)
            if not 0 <= at <= 2**64 - 1:
                raise ValueError(f"CSV 第 {rows} 行时间必须为 uint64 整型毫秒")
            if at <= previous or (rows == 1 and at != 0):
                raise ValueError("CSV 时间须从 0 开始严格递增，不接受重复或倒序")
            previous = at
            for source, cell in zip(sources, row[1:], strict=True):
                point = {"at_ms": at, "value": _cell(cell)}
                # 使用成熟 JSON 序列化度量 IPC 预算；逗号空格按保守上界计入。
                budget += len(json.dumps(point).encode()) + 2
                if budget > CSV_COMPILED_MAX_BYTES:
                    raise ValueError("CSV 预编译时间轴超过 2 MiB IPC 预算")
                source["generator"]["timeline"].append(point)
        if not rows:
            raise ValueError("CSV 至少需要一行数据")
        logger.info(
            "CSV 激励已预编译，未执行发送",
            extra={
                "operation": "stimulus.csv.compile",
                "rows": rows,
                "columns": len(paths),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        return sources
    except (ValueError, csv.Error) as cause:
        logger.exception("CSV 激励预编译失败", extra={"operation": "stimulus.csv.compile"})
        if isinstance(cause, csv.Error):
            raise ValueError("CSV 格式不合法（引号、单元格或行边界错误）") from cause
        raise
