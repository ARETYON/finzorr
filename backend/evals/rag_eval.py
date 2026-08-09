"""RAG answer-quality eval — golden dataset over the REAL local pipeline.

Two axes:
- retrieval hit-rate (DETERMINISTIC): after ingesting golden fixture docs
  into a throwaway tenant, does `search()` surface the chunk containing the
  expected fact? Gate with `--min-hit-rate 0.9` (LOCAL release gate — CI has
  no Qdrant/Ollama service, so this cannot and does not run there).
- faithfulness (LLM judge, `--judge`): is every claim in a generated answer
  supported by the retrieved text? Reported; gate locally via `--min-score`.

Usage (local stack up):
    uv run python -m evals.rag_eval --min-hit-rate 0.9
    uv run python -m evals.rag_eval --judge --min-score 7
"""

import argparse
import asyncio
import os
import sys
import uuid

# evals run OUTSIDE pytest's conftest — pin tracing off ourselves or a daily
# drift run posts ingest/search trace trees to LangSmith (changelog-62 lesson)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_API_KEY"] = ""

# (question, doc filename, doc text, expected substring in retrieved chunk)
GOLDEN: list[tuple[str, str, str, str]] = [
    ("What was Meridian's total revenue?", "meridian-q2.txt",
     "Meridian Industries quarterly report. Total revenue was 918 crore rupees. "
     "Operating margin improved to 14 percent.", "918 crore"),
    ("What is Meridian's operating margin?", "meridian-q2.txt",
     "", "14 percent"),  # empty text = reuse previous doc
    ("Who is the CEO of Kavya Textiles?", "kavya-notes.md",
     "# Kavya Textiles\nFounded 1987. CEO is Priya Raghavan. "
     "Exports account for 60% of sales.", "Priya Raghavan"),
    ("What share of Kavya's sales are exports?", "kavya-notes.md", "", "60%"),
    ("What is the dividend policy?", "policy.txt",
     "Dividend policy: the board targets a payout ratio of 35 percent of "
     "net profit, reviewed annually in April.", "35 percent"),
    ("When is the dividend policy reviewed?", "policy.txt", "", "April"),
    ("What was the headcount at year end?", "hr-summary.csv",
     "metric,value\nheadcount_year_end,4820\nattrition_pct,11.5\n", "4820"),
    ("What was the attrition percentage?", "hr-summary.csv", "", "11.5"),
    ("What did the risk section flag?", "risk.txt",
     "Risk review: currency exposure to USD receivables was flagged as the "
     "primary concern; hedging covers only 40 percent of positions.",
     "currency exposure"),
    ("How much of the positions are hedged?", "risk.txt", "", "40 percent"),
    ("What is the warranty period?", "product-faq.md",
     "## Product FAQ\nWarranty period: 24 months from purchase. "
     "Extended cover available for 12 more months.", "24 months"),
    ("Can warranty be extended?", "product-faq.md", "", "12 more months"),
    ("What city hosts the new plant?", "expansion.txt",
     "Expansion note: the new manufacturing plant will be built in Coimbatore "
     "with production starting next fiscal year.", "Coimbatore"),
    ("When does production start?", "expansion.txt", "", "next fiscal year"),
    ("What is the customer satisfaction score?", "nps.txt",
     "Customer survey results: NPS of 62, satisfaction score 4.4 out of 5.",
     "4.4"),
]


async def run(min_hit_rate: float | None, judge: bool, min_score: float | None) -> int:
    from app.documents.ingest import ingest_document
    from app.rag.embeddings import embed_query
    from app.rag.vector_store import delete_tenant, search

    tenant_uuid = uuid.uuid4()  # ingest signature wants a UUID user id
    print(f"rag eval: tenant {tenant_uuid} ({len(GOLDEN)} golden items)")

    ingested: dict[str, bool] = {}
    try:
        for _q, filename, text, _expect in GOLDEN:
            if text and filename not in ingested:
                await ingest_document(tenant_uuid, uuid.uuid4(), filename, text.encode())
                ingested[filename] = True

        hits = 0
        judge_scores: list[int] = []
        for question, filename, _text, expect in GOLDEN:
            vector = await embed_query(question)
            results = await search(vector, tenants=[str(tenant_uuid)], top_k=6)
            found = any(expect.lower() in h.text.lower() for h in results)
            hits += found
            mark = "PASS" if found else "MISS"
            print(f"  {mark}  {question[:52]:<52} ({filename})")

            if judge and found:
                from app.ai.base import SystemMessage, UserMessage
                from app.ai.completion import complete

                context = "\n".join(h.text for h in results[:3])
                answer = await complete(
                    [SystemMessage(content=f"Answer ONLY from this context:\n{context}"),
                     UserMessage(content=question)],
                    temperature=0.0, max_tokens=150,
                )
                verdict = await complete(
                    [SystemMessage(content=(
                        "Score 0-10: is every claim in the ANSWER supported by "
                        "the CONTEXT? Reply ONLY the integer."
                    )),
                     UserMessage(content=f"CONTEXT:\n{context}\n\nANSWER:\n{answer}")],
                    temperature=0.0, max_tokens=4,
                )
                try:
                    judge_scores.append(max(0, min(10, int(verdict.strip()[:2]))))
                except ValueError:
                    judge_scores.append(0)

        rate = hits / len(GOLDEN)
        print(f"retrieval hit-rate: {hits}/{len(GOLDEN)} = {rate:.0%}")
        exit_code = 0
        if min_hit_rate is not None and rate < min_hit_rate:
            print(f"FAIL: hit-rate {rate:.0%} below --min-hit-rate {min_hit_rate:.0%}")
            exit_code = 1
        if judge and judge_scores:
            mean = sum(judge_scores) / len(judge_scores)
            print(f"faithfulness judge mean: {mean:.1f}/10 over {len(judge_scores)} answers")
            if min_score is not None and mean < min_score:
                print(f"FAIL: judge mean {mean:.1f} below --min-score {min_score}")
                exit_code = 1
        return exit_code
    finally:
        try:
            await delete_tenant(str(tenant_uuid))
            print("cleanup: tenant deleted")
        except Exception as exc:  # noqa: BLE001
            print(f"cleanup failed (non-fatal): {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-hit-rate", type=float, default=None)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.min_hit_rate, args.judge, args.min_score)))
