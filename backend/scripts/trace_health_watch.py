"""Live-traffic trace-health watch — genuinely different from
drift_watch.py (which is eval-based, re-running deterministic checks
against fixture data). This queries LangSmith for the RATE of `degraded`
and `guard:suspicious` tags over REAL traffic in a trailing window.

Only the ALERT + exit-1 + file-cadence SHAPE is copied from drift_watch.py
— NEVER its credential-zeroing lines. This script's entire function
depends on live LangSmith credentials being present; unlike an eval run,
there is nothing to protect against here (we WANT it to reach LangSmith).

`Client.list_runs` is confirmed present but DEPRECATED in the installed
langsmith (0.10.16) in favor of `Client.runs.query` (removal after Jan
2027) — still used here since it's functional today; revisit if upgrading.

Usage: uv run python scripts/trace_health_watch.py [--hours 24]
Skips gracefully (exit 0) when tracing is off/unconfigured — never a hard
requirement. Documented cron line: PROJECT_PLAN.md §16, DEPLOYMENT_PLAN.md.
"""

import argparse
import datetime
import json
import pathlib
import sys

DEGRADED_RATE_THRESHOLD = 0.15  # alert if >15% of turns degrade
SUSPICIOUS_RATE_THRESHOLD = 0.05  # alert if >5% of turns flag suspicious


def compute_rates(tag_lists: list[list[str]]) -> dict[str, float]:
    """Pure function: [] of each run's tags -> {tag_type: rate}. Testable
    without a live LangSmith connection."""
    total = len(tag_lists)
    if total == 0:
        return {"degraded": 0.0, "suspicious": 0.0, "total_runs": 0.0}
    degraded = sum(1 for tags in tag_lists if any(t.startswith("degraded") for t in tags))
    suspicious = sum(
        1 for tags in tag_lists if any(t.startswith("guard:suspicious") for t in tags)
    )
    return {
        "degraded": degraded / total,
        "suspicious": suspicious / total,
        "total_runs": float(total),
    }


def check_thresholds(rates: dict[str, float]) -> list[str]:
    """Pure function: rates -> alert lines. Testable independently."""
    alerts: list[str] = []
    if rates.get("total_runs", 0) == 0:
        return alerts
    if rates.get("degraded", 0) > DEGRADED_RATE_THRESHOLD:
        alerts.append(
            f"ALERT degraded rate {rates['degraded']:.1%} exceeds "
            f"{DEGRADED_RATE_THRESHOLD:.0%} threshold"
        )
    if rates.get("suspicious", 0) > SUSPICIOUS_RATE_THRESHOLD:
        alerts.append(
            f"ALERT guard:suspicious rate {rates['suspicious']:.1%} exceeds "
            f"{SUSPICIOUS_RATE_THRESHOLD:.0%} threshold"
        )
    return alerts


def main(hours: int) -> int:
    # invoked as `python scripts/trace_health_watch.py` — sys.path[0] is
    # scripts/, not the backend/ root, so the app package needs adding.
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from app.core.config import settings

    if not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY):
        print("trace_health_watch: LangSmith tracing not configured — skipping")
        return 0

    from langsmith import Client

    client = Client()
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)
    # list_runs is deprecated in favor of Client.runs.query but functional
    # today (langsmith 0.10.16); revisit on upgrade.
    runs = list(
        client.list_runs(
            project_name=settings.LANGSMITH_PROJECT,
            start_time=since,
            is_root=True,
            limit=100,  # LangSmith API's per-request cap; a sample suffices for a rate
        )
    )
    tag_lists = [list(r.tags or []) for r in runs]
    rates = compute_rates(tag_lists)
    alerts = check_thresholds(rates)

    watch_dir = pathlib.Path("drift")
    watch_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H%M")
    (watch_dir / f"trace-health-{stamp}.json").write_text(json.dumps(rates, indent=2))

    print(f"trace health ({hours}h window): {rates}")
    for alert in alerts:
        print(alert)
    return 1 if alerts else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    sys.exit(main(args.hours))
