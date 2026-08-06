"""Routing-accuracy eval: the supervisor's decision quality, measured.

Usage (from backend/):
    uv run python -m evals.routing_eval          # deterministic keyword floor
    uv run python -m evals.routing_eval --live   # full LLM supervisor (needs a provider)

The labelled dataset is the ground truth for "which specialist should handle
this message". Offline mode scores the regex keyword floor (what carries the
product through a total LLM outage); --live scores the real supervisor.
Feedback rows (thumbs-down + preceding query) are the intended source of new
cases — add misroutes here as they're found.
"""

import argparse
import asyncio
import sys
from collections import defaultdict

# (message, expected_route)
DATASET: list[tuple[str, str]] = [
    # --- general_chat ---
    ("Hi there!", "general_chat"),
    ("Explain compound interest simply", "general_chat"),
    ("Write me a haiku about monsoon season", "general_chat"),
    ("What's the capital of Australia?", "general_chat"),
    ("Draft a polite email declining a meeting", "general_chat"),
    ("Tell me a joke about accountants", "general_chat"),
    ("How do I stay motivated to save money?", "general_chat"),
    ("Translate 'good morning' to Hindi", "general_chat"),
    ("Summarize the concept of diversification", "general_chat"),
    ("What is inflation?", "general_chat"),
    # --- tools (live market data / actions) ---
    ("What is the current price of TCS?", "tools"),
    ("Get me Reliance stock quote", "tools"),
    ("Show me the HDFC Bank chart for 6 months", "tools"),
    ("Infosys 52 week high?", "tools"),
    ("How is my portfolio doing?", "tools"),
    ("Analyze my holdings CSV", "tools"),
    ("Run python code to compute 17% CAGR on 50000 over 8 years", "tools"),
    ("Do deep research on the Indian EV battery sector", "tools"),
    ("read https://example.com/article and summarize it", "tools"),
    ("What's Wipro trading at right now?", "tools"),
    ("Current volume on Tata Motors?", "tools"),
    ("Fetch the latest quote for ITC", "tools"),
    # --- nl2sql (screening over fundamentals) ---
    ("NSE stocks with P/E under 20 and dividend yield above 2%", "nl2sql"),
    ("Screen for IT companies with market cap above 1 lakh crore", "nl2sql"),
    ("Which stocks have ROE greater than 25%?", "nl2sql"),
    ("List banks sorted by pe ratio ascending", "nl2sql"),
    ("Top 10 companies by market cap in the pharma sector", "nl2sql"),
    ("Find undervalued stocks with pb ratio below 1.5", "nl2sql"),
    ("show me all stocks with eps above 100", "nl2sql"),
    # --- web_search (current events / fresh info) ---
    ("What's the latest news on the RBI rate decision?", "web_search"),
    ("Who won the match yesterday?", "web_search"),
    ("Latest headlines about Adani group", "web_search"),
    ("What happened in the budget announcement today?", "web_search"),
    ("Recent news about SEBI regulations", "web_search"),
    ("What are analysts saying about the rupee this week?", "web_search"),
    # --- rag (documents / glossary) ---
    ("What does my uploaded annual report say about capex?", "rag"),
    ("According to the PDF I uploaded, what were the risks?", "rag"),
    ("Search my documents for the dividend policy", "rag"),
    ("What is the definition of book value in the glossary?", "rag"),
    ("From my uploaded file, summarize the management discussion", "rag"),
    # --- memory (watchlist / alerts / prefs) ---
    ("Add TCS to my watchlist", "memory"),
    ("Remove Infosys from my watchlist", "memory"),
    ("What's on my watchlist?", "memory"),
    ("Alert me when TCS goes above 5000", "memory"),
    ("Set a price alert for Reliance below 2200", "memory"),
    ("Remind me every Friday to review my portfolio", "memory"),
    ("Remember that I prefer short answers", "memory"),
    ("Every day at 9am summarize my watchlist", "memory"),
]


def _score(predict: dict[str, str]) -> float:
    per_route: dict[str, list[bool]] = defaultdict(list)
    misroutes: list[tuple[str, str, str]] = []
    for message, expected in DATASET:
        got = predict[message]
        per_route[expected].append(got == expected)
        if got != expected:
            misroutes.append((message, expected, got))
    total = sum(len(v) for v in per_route.values())
    correct = sum(sum(v) for v in per_route.values())
    print(f"\noverall accuracy: {correct}/{total} = {correct / total:.1%}\n")
    for route in sorted(per_route):
        hits = per_route[route]
        print(f"  {route:<14} {sum(hits)}/{len(hits)}")
    if misroutes:
        print("\nmisroutes:")
        for message, expected, got in misroutes:
            print(f"  [{expected} -> {got}] {message[:60]}")
    return correct / total


def run_offline() -> float:
    from app.graph.supervisor import keyword_route

    print("== deterministic keyword floor ==")
    return _score({m: keyword_route(m) for m, _ in DATASET})


async def run_live() -> float:
    from app.graph.supervisor import plan_and_route

    print("== live LLM supervisor ==")
    predictions: dict[str, str] = {}
    for message, _expected in DATASET:
        out = await plan_and_route({"user_msg": message, "session_id": "eval"})
        predictions[message] = out.get("route", "")
        sys.stdout.write(".")
        sys.stdout.flush()
    print()
    return _score(predictions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="score the LLM supervisor")
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.0,
        help="exit non-zero below this accuracy (CI gate)",
    )
    args = parser.parse_args()
    accuracy = asyncio.run(run_live()) if args.live else run_offline()
    if accuracy < args.min_accuracy:
        print(f"\nFAIL: accuracy {accuracy:.1%} < required {args.min_accuracy:.1%}")
        sys.exit(1)
