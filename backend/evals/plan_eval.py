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
    from app.graph.nodes.advance import after_step
    from app.graph.nodes.parallel import PARALLEL_ROUTES
    from app.graph.supervisor import PARALLELIZABLE, validate_plan

    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
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

    # --- after_step decision table
    from app.graph.state import AssistantState

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
        from app.graph.nodes.advance import advance_node

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
        from app.graph.nodes.advance import advance_node

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

    total = 20
    print(f"plan mechanics: {total - len(failures)}/{total} checks passed")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


JUDGE_PROMPTS = [
    ("What is the price of TCS?", "expect single step"),
    ("Find the latest news on Infosys and then show its current price", "expect 2 ordered steps"),
    ("Compare the latest RBI news with a screener of low-P/E banks", "independent -> parallel ok"),
    ("Hi!", "expect single general_chat step"),
    ("Do deep research on EV adoption and then alert me if Tata Motors drops below 900",
     "research then memory, ordered"),
]


async def run_live() -> int:
    from app.ai.base import SystemMessage, UserMessage
    from app.ai.completion import complete
    from app.graph.supervisor import plan_and_route

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
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(run_live()) if args.live else run_offline())
