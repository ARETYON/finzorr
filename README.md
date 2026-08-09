# finzorr.ai

A general-purpose AI assistant platform — ChatGPT-style multi-session chat, document
upload + analysis, and pluggable tool integrations — with **Indian stock markets
(NSE/BSE)** as the first fully-built vertical: live quotes, natural-language stock
screening, market news, and a personal watchlist.

Built on an enterprise reference architecture: LangGraph supervisor routing across
six specialist routes, a 5-layer-guarded NL2SQL pipeline, tenant-partitioned RAG,
an MCP client, and graceful degradation on every external dependency.
Full architecture: [`PROJECT_PLAN.md`](./PROJECT_PLAN.md).

| Route | What it handles | Example |
|---|---|---|
| `general_chat` | Anything conversational (default) | "Write a haiku about monsoon" |
| `tools` | Live quotes/fundamentals/history via function-calling agent loop | "Price of TCS?" |
| `nl2sql` | Screening many stocks (guarded SQL over a daily-refreshed table) | "Stocks with P/E under 20" |
| `rag` | Finance glossary + your uploaded documents (PDF/DOCX/PPTX/XLSX/XLS/CSV/TXT/MD), with citations | "What does my contract say about notice period?" |
| `web_search` | Fresh news (Tavily → SearXNG → DuckDuckGo) | "Why did Adani stock fall today?" |
| `memory` | Your watchlist (add/remove/show) | "Add Infosys to my watchlist" |

**Not investment advice.** Market data is delayed and comes from free/unofficial sources.

---

Security posture: see [SECURITY_REVIEW.md](SECURITY_REVIEW.md) (OWASP LLM Top-10 disposition).

## Run locally (dev)

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Node 20+, and
[Ollama](https://ollama.com) with the dev models pulled:

```bash
ollama pull qwen2.5:14b-instruct     # chat + routing (dev default)
ollama pull nomic-embed-text:v1.5    # embeddings (RAG)
```

### 1. Infrastructure (Postgres 5433 · Redis 6380 · Qdrant 6335)

```bash
cd backend
docker compose -f docker-compose.dev.yml up -d
```

### 2. Backend (FastAPI on :8000)

```bash
cd backend
cp .env.example .env          # dev defaults work as-is; DEV_FAKE_AUTH=true
uv sync
uv run alembic upgrade head   # tables + NL2SQL read-only role
uv run python -m app.rag.ingest_corpus                       # glossary → Qdrant
uv run python -m app.nl2sql.jobs.refresh_fundamentals        # screener data (daily)
uv run uvicorn app.main:app --port 8000
```

### 3. Frontend (Vite on :5173)

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** → *Continue as Dev User* → chat. Real Google
Sign-In activates automatically once `GOOGLE_CLIENT_ID` (backend `.env`) and
`VITE_GOOGLE_CLIENT_ID` (frontend env) are set.

### Verify

```bash
curl localhost:8000/healthz            # liveness
curl localhost:8000/readyz             # Postgres + Redis reachability
curl localhost:8000/api/debug/llm-ping # LLM streaming + tool-calling (dev only)
cd backend && uv run pytest -q         # 53 sanity tests, no live deps
```

---

## Configuration highlights (`backend/.env.example` documents every variable)

- **LLM provider chain (free-first):** `LLM_PROVIDER=ollama` for dev; set
  `GROQ_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` and switch
  `LLM_PROVIDER` for hosted free tiers. `LLM_FALLBACK_PROVIDER` enables one
  bounded retry; a per-provider daily token budget shifts traffic down the chain.
- **GitHub tools (MCP):** set `GITHUB_TOKEN` → read-only GitHub tools
  (search repos/code, read files, issues, PRs) appear in the `tools` route.
- **Your own microservices:** point `MICROSERVICE_TOOLS_CONFIG` at a JSON file
  (see `backend/app/tools_registry/examples/microservices.json`) — each entry
  becomes an LLM-callable tool, no code changes.
- **Web search:** works keyless via DuckDuckGo; `TAVILY_API_KEY` or
  `SEARXNG_URL` upgrade the chain.

## Repository layout

```
backend/    FastAPI + LangGraph (app/graph = supervisor + 6 route nodes),
            ai/ provider gateway, nl2sql/ guarded screener, rag/ + documents/,
            market_data/, mcp_client/, tools_registry/, alembic/, tests/
frontend/   React + Vite + Tailwind SPA (multi-session chat, watchlist, uploads)
.github/    CI: backend lint+tests, frontend build, security scans
```

## Status

Local end-to-end build is complete and verified (all six routes, document RAG,
watchlist, feedback loop, Postgres checkpointing). Deployment (Cloudflare Pages +
Tunnel, OVH VMs, UAT/PROD CD) is the next phase — see `PROJECT_PLAN.md`.
