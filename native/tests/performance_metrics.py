"""性能证据的纯统计函数；不把采集缺失等同于协议栈丢包。"""

from __future__ import annotations

import math
import re
from collections import Counter
from itertools import pairwise
from statistics import mean


def kernel_capture_drops(log):
    counts = re.findall(r"(\d+) packets dropped by kernel", log)
    if len(counts) != 1:
        raise ValueError("必须有唯一的 tcpdump 内核丢包计数，不能默认零或混用多次采集")
    return int(counts[0])


def distribution(values):
    if not values:
        return {
            "count": 0,
            "min": None,
            "mean": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    ordered = sorted(values)
    if any(not math.isfinite(v) for v in ordered):
        raise ValueError("性能样本必须为有限数")
    return {
        "count": len(values),
        "min": ordered[0],
        "mean": mean(ordered),
        **{
            f"p{int(q * 100)}": ordered[math.ceil(len(values) * q) - 1]
            for q in (0.5, 0.95, 0.99)
        },
        "max": ordered[-1],
    }


def sequence_accounting(expected, observed):
    unique = set(expected)
    if len(unique) != len(expected):
        raise ValueError("预期序号必须唯一，否则不能区分循环与重复交付")
    expected = unique
    counts = Counter(observed)
    missing = expected - counts.keys()
    unexpected = counts.keys() - expected
    highest, reordered = -1, 0
    for value in observed:
        if value < highest:
            reordered += 1
        highest = max(highest, value)
    return {
        "expected": len(expected),
        "observed": len(observed),
        "unique": len(counts),
        "missing": len(missing),
        "missing_samples": sorted(missing)[:16],
        "unexpected": len(unexpected),
        "unexpected_samples": sorted(unexpected)[:16],
        "duplicates": sum(n - 1 for n in counts.values()),
        "reordered": reordered,
        "missing_fraction": len(missing) / len(expected) if expected else None,
    }


def intervals(timestamps_ns, target_ns):
    if target_ns <= 0:
        raise ValueError("目标周期必须大于零")
    deltas = [b - a for a, b in pairwise(timestamps_ns)]
    return {
        "interval_ms": distribution([n / 1_000_000 for n in deltas]),
        "absolute_period_error_ms": distribution(
            [abs(n - target_ns) / 1_000_000 for n in deltas]
        ),
        "clock_regressions": sum(n < 0 for n in deltas),
        "large_gap_count": sum(n > 1.5 * target_ns for n in deltas),
        "zero_interval_count": sum(n == 0 for n in deltas),
    }
