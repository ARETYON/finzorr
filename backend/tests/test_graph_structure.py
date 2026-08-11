"""Sanity: graph topology + prompt registry — structural checks, zero live deps."""

import pytest

from app.core.prompt_registry import AGENT_PROMPTS, render_agent_prompt
from app.orchestration.graph import BRANCHES, build_graph
from app.orchestration.supervisor import ROUTES

pytestmark = pytest.mark.sanity

EXPECTED_NODES = {
    "supervisor",
    "general_chat",
    "memory",
    "rag",
    "web_search",
    "nl2sql",
    "tools_plan",  # the agent loop is two checkpointed nodes, not one
    "tools_exec",
    "research_plan",  # deep research is four checkpointed stages
    "research_search",
    "research_read",
    "research_synthesize",
    "advance",  # the plan walker
    "replan",  # one revision attempt on step failure
    "spec_runner",  # Send fan-out branch runner
    "join",  # fan-out barrier
    "compose",  # multi-step synthesis
    "persist",
}

EXPECTED_PROMPTS = {
    "general_chat_system",
    "title_generator",
    "tools_system",
    "nl2sql_generator",
    "nl2sql_answer",
    "rag_system",
    "web_synthesis",
    "memory_system",
    "supervisor_planner",
}


def test_graph_contains_all_nodes() -> None:
    builder = build_graph()
    assert set(builder.nodes) >= EXPECTED_NODES


def test_branches_cover_every_route() -> None:
    assert set(BRANCHES) == set(ROUTES)


def test_all_prompts_registered() -> None:
    assert set(AGENT_PROMPTS) >= EXPECTED_PROMPTS


def test_every_prompt_has_version() -> None:
    for prompt in AGENT_PROMPTS.values():
        assert prompt.version, f"{prompt.name} has no version"


def test_nl2sql_generator_is_v2_postgres_dialect() -> None:
    prompt = AGENT_PROMPTS["nl2sql_generator"]
    assert prompt.version == "2"
    assert "strftime" in prompt.template  # the explicit anti-SQLite warning


def test_prompts_render() -> None:
    assert "Dev" in render_agent_prompt("general_chat_system", user_name="Dev")
    assert "fundamentals" in render_agent_prompt(
        "nl2sql_generator", schema="fundamentals(symbol text)", error_hint=""
    )
    assert "[1]" in render_agent_prompt("rag_system", excerpts="<<excerpt [1]>>x<<end excerpt>>")


def test_unknown_prompt_raises() -> None:
    with pytest.raises(KeyError):
        render_agent_prompt("nonexistent_prompt")
