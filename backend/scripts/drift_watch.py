"""Daily drift watch — re-runs every deterministic eval and alerts on ANY
gate regression versus yesterday.

Runs the evals via SUBPROCESS with the exact release-gate commands (importing
them would drag evals/ into mypy --strict; subprocess also preserves their
exit-code semantics). Scores land in drift/YYYY-MM-DD.json; a regression
prints ALERT lines and exits 1 so cron mail / a heartbeat monitor can flag it.

Install (dev Mac, crontab -e):
    30 7 * * * cd <repo>/backend && uv run python scripts/drift_watch.py >> drift/watch.log 2>&1
Prod cron is documented in DEPLOYMENT_PLAN.md.
"""

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

# never let a drift run post trace trees (the changelog-62 lesson)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_API_KEY"] = ""

EVALS: list[tuple[str, list[str], str]] = [
    # (name, command, regex capturing "passed/total" or "pct")
    (
        "routing",
        ["uv", "run", "python", "-m", "evals.routing_eval"],
        r"overall accuracy: (\d+)/(\d+)",
    ),
    (
        "injection",
        ["uv", "run", "python", "-m", "evals.injection_eval"],
        r"injection resistance: (\d+)/(\d+)",
    ),
    (
        "plan",
        ["uv", "run", "python", "-m", "evals.plan_eval"],
        r"plan mechanics: (\d+)/(\d+)",
    ),
    (
        "rag_retrieval",
        ["uv", "run", "python", "-m", "evals.rag_eval"],
        r"retrieval hit-rate: (\d+)/(\d+)",
    ),
]


def run_evals() -> dict[str, float]:
    scores: dict[str, float] = {}
    for name, cmd, pattern in EVALS:
        try:
            proc = subprocess.run(  # noqa: S603 — fixed command list, no user input
                cmd, capture_output=True, text=True, timeout=900
            )
            match = re.search(pattern, proc.stdout)
            if match:
                passed, total = int(match.group(1)), int(match.group(2))
                scores[name] = round(passed / total, 4) if total else 0.0
            else:
                scores[name] = -1.0  # eval ran but output unparseable
        except Exception:
            scores[name] = -1.0  # eval crashed/timed out — itself an alert
    return scores


def compare(yesterday: dict[str, float], today: dict[str, float]) -> list[str]:
    """Pure comparison — every regression (or broken eval) is an alert line."""
    alerts: list[str] = []
    for name, score in today.items():
        if score < 0:
            alerts.append(f"ALERT {name}: eval failed to run")
            continue
        previous = yesterday.get(name)
        if previous is not None and previous >= 0 and score < previous:
            alerts.append(f"ALERT {name}: {previous:.2%} -> {score:.2%}")
    return alerts


def main() -> int:
    drift_dir = pathlib.Path("drift")
    drift_dir.mkdir(exist_ok=True)
    today = datetime.date.today()
    scores = run_evals()
    (drift_dir / f"{today:%Y-%m-%d}.json").write_text(json.dumps(scores, indent=2))

    previous_files = sorted(drift_dir.glob("*.json"))
    yesterday: dict[str, float] = {}
    if len(previous_files) >= 2:
        yesterday = json.loads(previous_files[-2].read_text())

    alerts = compare(yesterday, scores)
    print(f"drift watch {today}: {scores}")
    for alert in alerts:
        print(alert)
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
