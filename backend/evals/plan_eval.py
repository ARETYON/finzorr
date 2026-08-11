"""Plan-quality eval: the planner's decision machinery, measured.

Offline (CI-gated 100%): synthetic supervisor decisions driven through the
REAL validation + advance/replan/parallel machinery — step budgets, route
restrictions, failure→replan→early-exit flow, per-step reset discipline.
--live: the real supervisor over judge-scored prompts (rubric: single step
when simple, sensible decomposition, parallel only when independent) —
reported, not gated (needs an LLM).

Usage: uv run python -m evals.plan_eval [--live]
"""

import argparse
import asyncio
import json
import sys
from typing import Any


def run_offline() -> int:
    from app.orchestration.supervisor import PARALLELIZABLE, validate_plan
    from app.specialists.advance import after_step
    from app.specialists.parallel import PARALLEL_ROUTES

    failures: list[str] = []
    checks_run = 0

    def check(name: str, condition: bool) -> None:
        nonlocal checks_run
        checks_run += 1
        if not condition:
            failures.append(name)

    # --- validate_plan mechanics
    check("cap-3", len(validate_plan([{"route": "rag", "task": "t"}] * 5, "m")) <= 3)
    check("bad-route-dropped", validate_plan([{"route": "nope", "task": "t"}], "m") == [])
    fallback = validate_plan([{"route": "rag", "task": ""}], "m")
    check("empty-task-falls-back", fallback[0]["task"] == "m")
    check("garbage-empty", validate_plan({"not": "a list"}, "m") == [])

    # --- parallel restrictions consistent between supervisor and runner
    check("parallel-sets-match", PARALLELIZABLE == PARALLEL_ROUTES)
    check("tools-not-parallel", "tools" not in PARALLELIZABLE)
    check("research-not-parallel", "research" not in PARALLELIZABLE)
    check("memory-not-parallel", "memory" not in PARALLELIZABLE)

    # --- parallel dependency guard (lexical; anchored markers only)
    from app.orchestration.supervisor import _steps_look_dependent

    dependent = [
        {"route": "web_search", "task": "find RIL news"},
        {"route": "general_chat", "task": "summarise the result in one line"},
    ]
    referential = [
        {"route": "nl2sql", "task": "screen low-PE banks"},
        {"route": "general_chat", "task": "explain the above for a beginner"},
    ]
    screener = [
        {"route": "nl2sql", "task": "stocks with market cap above 500cr"},
        {"route": "web_search", "task": "RIL previous close and day change"},
    ]
    check("dependent-demoted", _steps_look_dependent(dependent) is True)
    check("referential-demoted", _steps_look_dependent(referential) is True)
    check("screener-not-demoted", _steps_look_dependent(screener) is False)
    check("first-step-exempt", _steps_look_dependent([dependent[1]]) is False)

    # --- after_step decision table
    from app.orchestration.state import AssistantState

    steps2 = [{"route": "web_search", "task": "a"}, {"route": "tools", "task": "b"}]
    mid: AssistantState = {"plan_steps": steps2, "plan_index": 1, "step_outputs": [{}]}
    done: AssistantState = {"plan_steps": steps2, "plan_index": 2, "step_outputs": [{}, {}]}
    single: AssistantState = {"plan_steps": steps2[:1], "plan_index": 1, "step_outputs": [{}]}
    replanning: AssistantState = {"needs_replan": True, **mid}
    check("mid-plan-routes-next", after_step(mid) == "tools")
    check("done-multi-composes", after_step(done) == "compose")
    check("done-single-persists", after_step(single) == "persist")
    check("replan-wins", after_step(replanning) == "replan")

    # --- failure flow through the real advance node
    async def failure_flow() -> tuple[Any, Any]:
        from app.specialists.advance import advance_node

        base: AssistantState = {
            "plan_steps": steps2,
            "plan_index": 0,
            "step_outputs": [],
            "final_text": "degraded",
            "step_error": True,
            "session_id": "eval",
        }
        first = await advance_node({**base, "replan_count": 0})
        second = await advance_node({**base, "replan_count": 1})
        return first, second

    first, second = asyncio.run(failure_flow())
    check("failure-replans-once", first.get("needs_replan") is True)
    check("failure-clears-flag", first.get("step_error") is False)
    check("budget-spent-early-exits", second.get("plan_index") == len(steps2))

    # --- per-step reset discipline (arming a next step must clean the slate)
    async def arm() -> Any:
        from app.specialists.advance import advance_node

        armed_input: AssistantState = {
            "plan_steps": steps2,
            "plan_index": 0,
            "step_outputs": [],
            "final_text": "x",
            "citations": [{"m": 1}],
            "chart": {"symbol": "TCS"},
            "session_id": "eval",
        }
        return await advance_node(armed_input)

    armed = asyncio.run(arm())
    for key in ("final_text", "citations", "chart", "sources", "step_error"):
        check(f"reset-{key}", not armed.get(key))
    check("step-recorded-chart", armed["step_outputs"][0]["chart"] == {"symbol": "TCS"})

    # computed, not hardcoded — a new check must never silently skew the count
    total = checks_run
    print(f"plan mechanics: {total - len(failures)}/{total} checks passed")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


# 15 prompts across three rubrics: single-step-when-simple (a planner that
# decomposes trivia is worse than a classifier), decomposition order (later
# steps must be able to consume earlier outputs), and parallel independence
# (fan out only when steps share nothing). Replanning is deliberately NOT a
# live rubric — this harness only calls plan_and_route, so a judge never
# sees a replan; that surface is covered by the offline mechanics above and
# the compiled-graph e2e test.
JUDGE_PROMPTS = [
    # --- single-step-when-simple
    ("What is the price of TCS?", "expect single step"),
    ("Hi!", "expect single general_chat step"),
    ("What does P/E ratio mean?", "expect single step (rag or general_chat)"),
    ("Add Infosys to my watchlist", "expect single memory step"),
    ("Show me a chart of Reliance for 6 months", "expect single tools step"),
    # --- decomposition order (later steps consume earlier outputs)
    ("Find the latest news on Infosys and then show its current price", "expect 2 ordered steps"),
    ("Do deep research on EV adoption and then alert me if Tata Motors drops below 900",
     "research then memory, ordered"),
    ("Screen banks with P/E under 12, then summarise the top result's recent news",
     "nl2sql before web_search; second step depends on the first"),
    ("Look up what 'book value' means and use it to explain whether SBI looks cheap",
     "definition first, application second, ordered"),
    ("Get HDFC Bank's fundamentals and then compare them with its latest quarterly news",
     "ordered: numbers first, news comparison second"),
    # --- parallel independence (fan out ONLY when steps share nothing)
    ("Compare the latest RBI news with a screener of low-P/E banks", "independent -> parallel ok"),
    ("Two things: latest news on Reliance, and separately explain what NIFTY 50 is",
     "independent -> parallel ok"),
    ("What's the TCS share price today, and also what IT stocks have dividend yield above 3%?",
     "independent -> parallel ok"),
    ("Find Wipro's latest results and summarise what they mean for the stock",
     "DEPENDENT steps -> must NOT be parallel"),
    ("Search for Adani Ports news and then screen ports companies based on that result",
     "second step references the first -> sequential required"),
]


async def run_live(min_score: float | None = None) -> int:
    from app.infrastructure.llm.base import SystemMessage, UserMessage
    from app.infrastructure.llm.completion import complete
    from app.orchestration.supervisor import plan_and_route

    scores: list[int] = []
    for prompt, expectation in JUDGE_PROMPTS:
        out = await plan_and_route({"user_msg": prompt, "session_id": "eval"})
        plan = out.get("plan_steps", [])
        verdict = await complete(
            [
                SystemMessage(
                    content=(
                        "You judge assistant PLANS. Score 0-10 for: minimal steps, "
                        "correct specialist per step, sensible order, parallel only "
                        "when steps are independent. Reply ONLY JSON: "
                        '{"score": <0-10>, "reason": "<short>"}'
                    )
                ),
                UserMessage(
                    content=(
                        f"Request: {prompt}\nExpectation: {expectation}\n"
                        f"Plan: {json.dumps(plan)}\n"
                        f"Parallel: {out.get('plan_parallel', False)}"
                    )
                ),
            ],
            temperature=0.0,
            max_tokens=150,
            response_format={"type": "json_object"},
        )
        try:
            score = int(json.loads(verdict).get("score", 0))
        except (json.JSONDecodeError, ValueError):
            score = 0
        scores.append(score)
        print(f"  {score}/10  {prompt[:60]}")
    mean = sum(scores) / len(scores)
    print(f"judge mean: {mean:.1f}/10 over {len(scores)} prompts")
    if min_score is not None and mean < min_score:
        print(f"FAIL: judge mean {mean:.1f} below --min-score {min_score}")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="fail (exit 1) if the judge mean falls below this — makes the "
        "live judge an enforceable gate for local/nightly runs",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run_live(args.min_score)) if args.live else run_offline())
