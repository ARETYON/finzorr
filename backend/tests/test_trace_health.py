import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from trace_health_watch import (  # noqa: E402 — path insert must precede this import
    DEGRADED_RATE_THRESHOLD,
    SUSPICIOUS_RATE_THRESHOLD,
    check_thresholds,
    compute_rates,
)

pytestmark = pytest.mark.sanity


class TestComputeRates:
    def test_empty_run_list_is_zero(self) -> None:
        rates = compute_rates([])
        assert rates == {"degraded": 0.0, "suspicious": 0.0, "total_runs": 0.0}

    def test_no_flagged_tags_is_zero(self) -> None:
        rates = compute_rates([["ok"], ["citation:invalid_ignored"], []])
        assert rates["degraded"] == 0.0
        assert rates["suspicious"] == 0.0
        assert rates["total_runs"] == 3.0

    def test_degraded_tag_counted(self) -> None:
        rates = compute_rates([["degraded"], ["degraded:timeout"], ["ok"], ["ok"]])
        assert rates["degraded"] == 0.5

    def test_suspicious_tag_counted(self) -> None:
        rates = compute_rates([["guard:suspicious"], ["ok"], ["ok"], ["ok"]])
        assert rates["suspicious"] == 0.25

    def test_run_with_both_tags_counts_in_both_buckets(self) -> None:
        rates = compute_rates([["degraded", "guard:suspicious"]])
        assert rates["degraded"] == 1.0
        assert rates["suspicious"] == 1.0


class TestCheckThresholds:
    def test_zero_runs_never_alerts(self) -> None:
        assert check_thresholds({"degraded": 1.0, "suspicious": 1.0, "total_runs": 0.0}) == []

    def test_below_threshold_is_clean(self) -> None:
        rates = {
            "degraded": DEGRADED_RATE_THRESHOLD - 0.01,
            "suspicious": SUSPICIOUS_RATE_THRESHOLD - 0.01,
            "total_runs": 100.0,
        }
        assert check_thresholds(rates) == []

    def test_degraded_above_threshold_alerts(self) -> None:
        rates = {"degraded": 0.5, "suspicious": 0.0, "total_runs": 10.0}
        alerts = check_thresholds(rates)
        assert len(alerts) == 1
        assert "degraded" in alerts[0]

    def test_suspicious_above_threshold_alerts(self) -> None:
        rates = {"degraded": 0.0, "suspicious": 0.5, "total_runs": 10.0}
        alerts = check_thresholds(rates)
        assert len(alerts) == 1
        assert "guard:suspicious" in alerts[0]

    def test_both_above_threshold_alerts_twice(self) -> None:
        rates = {"degraded": 0.5, "suspicious": 0.5, "total_runs": 10.0}
        assert len(check_thresholds(rates)) == 2
