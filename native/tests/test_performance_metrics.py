"""统计口径回归；这些合成样本不是虚拟网性能证据。"""

import math

import pytest
from performance_metrics import (
    distribution,
    intervals,
    kernel_capture_drops,
    sequence_accounting,
)


def test_nearest_rank_percentiles_do_not_interpolate_or_invent_samples():
    result = distribution(list(range(1, 101)))
    assert (result["p50"], result["p95"], result["p99"], result["max"]) == (
        50,
        95,
        99,
        100,
    )
    assert distribution([7])["p99"] == 7
    assert distribution([])["p99"] is None


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_measurements_fail(value):
    with pytest.raises(ValueError, match="有限"):
        distribution([value])


def test_missing_duplicates_and_reordering_are_different_observations():
    result = sequence_accounting([1, 2, 3, 4], [1, 3, 2, 2, 8])
    assert (
        result["missing"],
        result["duplicates"],
        result["unexpected"],
        result["reordered"],
    ) == (1, 1, 1, 2)
    assert result["missing_fraction"] == 0.25
    assert result["missing_samples"] == [4]
    assert sequence_accounting([], [1])["missing_fraction"] is None


def test_timestamp_regression_and_coalesced_messages_are_not_hidden_by_sorting():
    result = intervals([0, 1_000_000, 1_000_000, 500_000, 4_000_000], 1_000_000)
    assert result["clock_regressions"] == 1
    assert result["zero_interval_count"] == 1
    assert result["large_gap_count"] == 1
    assert result["interval_ms"]["min"] == -0.5
    assert result["absolute_period_error_ms"]["max"] == 2.5


def test_duplicate_expected_sequences_cannot_be_used_as_delivery_evidence():
    with pytest.raises(ValueError, match="预期序号必须唯一"):
        sequence_accounting([1, 1], [1, 1])


@pytest.mark.parametrize(
    "log", ["", "0 packets dropped by kernel\n1 packets dropped by kernel"]
)
def test_capture_without_unique_drop_count_is_not_assumed_complete(log):
    with pytest.raises(ValueError, match="唯一"):
        kernel_capture_drops(log)


def test_capture_loss_is_retained_not_converted_to_protocol_loss_or_zero():
    assert kernel_capture_drops("0 packets dropped by kernel") == 0
    assert kernel_capture_drops("2362 packets dropped by kernel") == 2362
