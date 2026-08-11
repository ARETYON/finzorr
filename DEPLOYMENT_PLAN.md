# finzorr.ai — Production Deployment Plan (OVH VM + Cloudflare)

**Status: PLAN ONLY — nothing in this document has been implemented yet.**
This is the complete blueprint for taking finzorr live at https://finzorr.ai
using the OVH VM and the Cloudflare account (domain already configured).
When you decide to go live, this document is the script we follow.

---

## 1. Architecture overview — what runs where

There are three places where things run, and it is important to keep them
straight:

**A. Your OVH server** (a dedicated server — bare metal, not a Public
Cloud VPS; same role in this architecture either way) — runs the backend
as six Docker containers, plus a lightweight GitHub Actions self-hosted
runner (a systemd service, not a container) that lets pushes to `main`
deploy themselves — see §4.5:

| Container | What it does | Memory |
|---|---|---|
| api | The finzorr backend (FastAPI + LangGraph) | ~1.5 GB |
| postgres | The database (pgvector/pgvector:pg16 — chat history, users, long-term memory) | ~0.7 GB |
| redis | Cache, rate limits, turn locks | ~0.4 GB |
| qdrant | Vector search for document RAG | ~0.5 GB |
| ollama | TWO SMALL local models only: the embedder (nomic-embed-text, ~275 MB) and an optional 3B emergency chat model (~2 GB) | ~3 GB cap |
| cloudflared | The secure outbound connection to Cloudflare (the "tunnel") | ~0.1 GB |

Total: roughly 3–4 GB steady state → a 6–8 GB VM is comfortable.

**B. External cloud services** (nothing to install — your API just calls
them over the internet with a key, exactly like it already calls Yahoo
Finance):

| Service | Runs | You need |
|---|---|---|
| Groq (console.groq.com) | llama-3.3-70b-versatile — the PRIMARY chat model. A 70B model needs ~40 GB of GPU memory; it physically cannot run on the VM. Groq hosts it on their hardware, free tier. | A free API key |
| Google Gemini (aistudio.google.com) | gemini-2.0-flash — the automatic FALLBACK when Groq errors or hits quota. Also powers image understanding. | A free API key |
| Yahoo Finance / DuckDuckGo | Stock prices / web search — already used today, keyless. | Nothing |

Hugging Face is NOT used anywhere in this plan.

**C. Cloudflare** (already has your domain):

- **Pages** builds and hosts the frontend at `https://finzorr.ai` — it
  connects to your GitHub repo and rebuilds on every push. No server
  involved.
- **Tunnel** connects `https://api.finzorr.ai` to the api container on your
  VM. The tunnel is OUTBOUND-ONLY from the VM: **zero ports are opened to
  the internet on the server.** Nobody can reach Postgres, Redis, Qdrant, or
  even the API directly — Cloudflare is the only door, and it terminates
  HTTPS for you (no certificates to manage).

Request path: `Browser → finzorr.ai (Cloudflare Pages) → api.finzorr.ai
(Cloudflare edge) → tunnel → api container → Groq/Gemini clouds + local
containers`.

### Are Groq and Gemini really free?

Yes — both have genuine free tiers, no credit card required:

| | Groq | Gemini (AI Studio) |
|---|---|---|
| Cost / card | $0, no card | $0, no card |
| Approx. daily limit | ~1,000 requests on the 70B model | ~1,500 requests on Flash |

The app is already built for the limits: Groq exhausted → automatic retry on
Gemini → both exhausted → the tiny 3B model on the VM answers slowly instead
of erroring. The app's own daily token-budget counters shift traffic down
this chain BEFORE providers start rejecting. For a small launch this is
thousands of chat turns per day at $0; paying (Groq's paid tier is cheap)
only becomes a question if the site outgrows that.

---

## 2. Prerequisites — do these before deployment day

### 2.1 API keys and OAuth client

| Item | Status | Steps |
|---|---|---|
| Groq API key | required | console.groq.com → Sign up (easiest: "Continue with Google") → left sidebar **API Keys** → **Create API Key** → name it `finzorr-prod` → Create → copy the key (starts `gsk_...`, shown ONCE — save it now; lost keys are no problem, just create a new one) |
| Gemini API key | required | aistudio.google.com → sign in with your Google account → **Get API key** → **Create API key** → if asked for a project, pick "Create API key in new project" → copy the key (starts `AIza...`) |
| OpenRouter API key | optional (extra free fallback beyond Groq/Gemini) | openrouter.ai → Sign up → **Keys** → **Create Key** → copy — only `:free`-tagged models are needed, no billing setup required |
| Google Client ID (OAuth, for "Sign in with Google") | required — **the SAME client used in local dev works in prod**, this is not a separate credential | Google Client IDs aren't environment-scoped, only their *Authorized JavaScript origins* list is. Go to console.cloud.google.com/apis/credentials → your existing OAuth client (the one set up for local dev — see `PROJECT_PLAN.md` §1 if none exists yet) → add `https://finzorr.ai` (and `https://uat.finzorr.ai` if using UAT) to Authorized JavaScript origins, alongside `http://localhost:5173` → use that same Client ID value in prod env |
| Grok (xAI) | not supported | Not a wired provider in this codebase (only `ollama \| groq \| gemini \| openrouter \| huggingface` are); xAI has no meaningful free tier, so adding it would break the $0-cost-policy default — skip unless you explicitly want a paid exception |

### 2.4 Server specs and SSH
1. Log in to https://manager.ca.ovhcloud.com → **Dedicated Servers**
   (not Public Cloud/VPS) → click your server → the overview shows
   **RAM / vCPU / OS** (need: ≥6 GB RAM, Ubuntu or Debian). Note these
   down.
2. Confirm you can SSH in from your terminal: `ssh <user>@<server-ip>`.
   OVH emailed the credentials when the server was provisioned; add your
   SSH key if you haven't. (This SSH access is for you, one-time setup
   only — the ongoing deploy pipeline in §4.5 does NOT use SSH at all.)

### 2.5 Cloudflare tunnel token — created DURING deployment
Not needed in advance. On deployment day: Cloudflare dashboard → Zero Trust
→ Networks → Tunnels → **Create a tunnel** → name `finzorr-prod` → choose
Docker → copy the long token (starts `eyJ...`). Keep the tab open; you'll
also map the hostname there (step 4.3).

### 2.6 Optional integrations & storage (not required to deploy — add when ready)

| Item | Status | Steps |
|---|---|---|
| Tavily API key | optional (web-search upgrade) | tavily.com → Sign up → Dashboard → copy the API key → set `TAVILY_API_KEY` in prod env. Without it, DuckDuckGo/SearXNG already handle web search — nothing breaks if skipped. |
| GitHub token | optional (only for the GitHub MCP tool) | github.com → Settings → Developer settings → Personal access tokens → generate a token scoped to the repos you want the assistant to read → set `GITHUB_TOKEN` in prod env. Not needed unless you want the assistant to use the GitHub MCP integration. |
| Cloudflare R2 buckets | **not code-wired yet — creating buckets alone won't do anything** | `app/documents/storage.py::get_storage()` currently hard-returns `LocalDiskStorage` with no branch for R2 at all (verified in code — there's no `R2_*` setting to set). `PROJECT_PLAN.md` §13 calls the `DocumentStorage` interface "swappable," meaning the SEAM exists (one new class implementing it would plug in), not that R2 support is built. Until that class is written, uploads/backups stay on the VM's own disk — fine at small scale, just ask if you want the R2 backend actually implemented before launch. |

### 2.7 Monitoring (free — set up any time before or shortly after launch)

| Item | Status | Steps |
|---|---|---|
| UptimeRobot | optional, recommended, no code needed | uptimerobot.com → free account → **Add New Monitor** → HTTP(s) → URL `https://api.finzorr.ai/healthz` → sets up email alerts if the API goes down. Purely external — the `/healthz` endpoint already exists, nothing to change in the app. |
| Sentry | **not code-wired yet** | `sentry-sdk` is not imported or initialized anywhere in the backend or frontend today — this is a `PROJECT_PLAN.md` §15/16 stated intention, not shipped code. Creating a sentry.io project and copying a DSN won't do anything until the SDK is actually added (`sentry_sdk.init(dsn=..., environment=...)` at backend startup + the frontend's React SDK). Ask if you want this implemented before launch — it's a small addition, just not done yet. |

### 2.8 Local dev status (already satisfied — nothing to do here)
These were prerequisites for local development, not for the deployment
described in this document — confirmed already done, listed here only so
this checklist is complete end-to-end:
- ✅ `gh auth login` — logged in as ARETYON.
- ✅ Local dev stack (Ollama + models, Docker, uv, node) — verified ready.
Full detail (if you ever need to redo either on a fresh machine):
`PROJECT_PLAN.md` §1.4.

**Prerequisite summary: two required free keys (Groq, Gemini) + the Google
OAuth client's origins updated for prod + VM specs + working SSH — that's
the required ~10-minute path. Everything in 2.6/2.7 is optional and can be
added before, during, or after initial launch without blocking it.**

---

## 3. What gets built in the repo (the coding work, when approved)

These are the files the implementation wave will add — none exist yet:

| File | Purpose |
|---|---|
| `backend/Dockerfile` + `.dockerignore` | Packages the backend as a container image (python 3.12-slim, locked deps, non-root user, 2 uvicorn workers) |
| `deploy/docker-compose.prod.yml` | The six-container stack above — pinned image versions, memory caps, named volumes (data survives redeploys), **no published ports** |
| `deploy/prod.env.template` | Every config value pre-filled except the four you paste; becomes `/opt/finzorr/prod.env` on the VM |
| `deploy/vm-bootstrap.sh` | The ONE script you run on the server — installs Docker, lays out `/opt/finzorr`, prompts for your four values, builds the image, runs database migrations, pulls the two small local models, seeds the glossary + fundamentals, installs the backup cron, health-checks, **and installs + registers the GitHub Actions self-hosted runner as a systemd service** (using `gh` CLI to mint a fresh runner registration token — no extra manual paste needed since you're already `gh auth login`'d) |
| `deploy/deploy.sh` | Day-2 deploys: `./deploy/deploy.sh <tag>` (run from `/opt/finzorr`) — pull image, migrate, restart, health-check. Rollback = same command with the previous tag (<5 min); also what the CD workflow below runs |
| `deploy/backup.sh` | Nightly compressed Postgres dump, 14-day rotation |
| `.github/workflows/cd-prod.yml` | After first launch: every push to main builds+pushes the image to GHCR (GitHub-hosted runner), then a second job — gated by the `production` Environment's required-reviewer approval — runs on a **self-hosted runner installed ON your server** and executes `deploy/deploy.sh <tag>` locally. No SSH from GitHub to your server at any point; the runner polls GitHub outbound, same shape as `cloudflared` itself, so zero new inbound exposure. Uses only the built-in `GITHUB_TOKEN` — no SSH key or other secret lives in GitHub |
| `frontend/wrangler.toml` | Cloudflare Pages project config (project name `finzorr`, build output `dist`) so the frontend deploys via `wrangler pages deploy` directly instead of Git-integration auto-builds |
| Code fix: multi-origin | Backend accepts both `https://finzorr.ai` and `https://www.finzorr.ai` (today it allows exactly one origin) |
| Code fix: uploads volume | Uploaded PDFs/images move to a persistent volume (today they'd be lost on redeploy) |
| `frontend/public/_redirects` | One line so refreshing `/chat` or opening a share link directly doesn't 404 on Cloudflare Pages |
| `frontend/src/pages/Privacy.tsx` + `Terms.tsx` | Required: Google will not allow public OAuth login without a published privacy-policy URL |

Security defaults baked in: `CODE_INTERPRETER=false` (the Python sandbox
needs docker-inside-docker — a host-escape surface; stays off in prod),
`LANGSMITH_TRACING=false` (user prompts would leave the server; enable
deliberately if ever wanted), dev-login and debug routes automatically
disabled outside dev (already true in the code today).

---

## 4. Deployment-day runbook (your steps, in order)

### 4.1 On the VM (~15 minutes, mostly waiting)
```
git clone https://github.com/ARETYON/finzorr.git /tmp/finzorr
sudo bash /tmp/finzorr/deploy/vm-bootstrap.sh
```
The script pauses four times and asks you to paste:
1. `GROQ_API_KEY` (gsk_...)
2. `GEMINI_API_KEY` (AIza...)
3. `GOOGLE_CLIENT_ID`
4. `TUNNEL_TOKEN` (eyJ..., from step 2.5)

Everything else — Docker install, database password, session secret, model
downloads, migrations, seed data, backups cron — is automatic. It ends by
printing a health check and `docker compose ps`; send me that output.

The fundamentals-seed step doubles as the **yfinance test**: if Yahoo
blocks OVH's datacenter IPs (a known risk), this step fails visibly and we
choose an alternative market-data source before launch.

### 4.2 Cloudflare Pages via Wrangler (frontend, ~5 minutes)
Using the Wrangler CLI directly (not Git-integration auto-builds):
```
cd frontend
wrangler pages project create finzorr   # one-time
echo "VITE_API_BASE_URL=https://api.finzorr.ai" >> .env.production
echo "VITE_GOOGLE_CLIENT_ID=<your client id>" >> .env.production
npm run build
wrangler pages deploy dist --project-name=finzorr
```
- `frontend/wrangler.toml` (checked into the repo) holds the project name
  and build output directory so every future `wrangler pages deploy`
  needs no flags beyond the directory.
- Custom-domain binding stays a one-time **dashboard** step (Wrangler
  doesn't cleanly automate this part today): Workers & Pages → `finzorr`
  project → Custom domains → add `finzorr.ai` and `www.finzorr.ai`
  (Cloudflare wires the DNS itself).
- Every subsequent release: `npm run build && wrangler pages deploy dist
  --project-name=finzorr` — one command, run whenever you want to ship a
  frontend update, no GitHub push required (though nothing stops you from
  wrapping this same command in a CI step later if you want push-to-deploy
  for the frontend too).

### 4.3 Cloudflare Tunnel hostname (~2 minutes)
In the tunnel you created (Zero Trust → Networks → Tunnels →
`finzorr-prod`) → Public Hostname → Add:
- Subdomain `api`, domain `finzorr.ai`
- Service: type `HTTP`, URL `api:8000`
Then from any browser: `https://api.finzorr.ai/healthz` must return
`{"status":"ok"}`.

### 4.4 Google console (~2 minutes)
console.cloud.google.com → APIs & Services → Credentials → your OAuth
client → **Authorized JavaScript origins** → add `https://finzorr.ai`.
While the consent screen stays in "Testing" mode, only Google accounts you
add as test users can log in — that's your invite-only launch for free.
Publish the consent screen (requires the Privacy page to be live) when you
want the public in.

### 4.5 Enable auto-deploys (optional, after launch)
Two GitHub-side config steps, no secrets:
1. Settings → Environments → create `production` → add yourself as
   required reviewer.
2. After the first `cd-prod.yml` run pushes an image: the repo's Packages
   tab → `finzorr-backend` → Package settings → make it **public**. The
   self-hosted runner pulls images as plain `docker`, with no registry
   login configured (deliberately — one less credential on the server);
   a public GHCR package needs no auth to pull, which is what makes that
   work. (Private is fine too if you'd rather set up a pull token later —
   just isn't done by default here.)

The self-hosted runner was already installed and
registered by `vm-bootstrap.sh` in §4.1, so `cd-prod.yml`'s deploy job can
already reach the server (it runs locally there, polling GitHub outbound —
nothing connects in). From then on: every push to `main` builds+pushes to
GHCR automatically, then waits for your approval click on the deploy job,
which runs `deploy/deploy.sh <sha>` directly on the server. Rollback is
`workflow_dispatch` on the same workflow with a previous tag/sha as input.

**Why not SSH:** an SSH deploy key would need to live in GitHub Secrets,
and if it ever leaked, it's an interactive shell on the box — full reach
to Postgres/Redis/Qdrant, not just the API. The self-hosted-runner model
needs no such credential in GitHub at all, and matches the same
outbound-only shape the Cloudflare Tunnel already uses for ingress.

---

## 5. Launch smoke-test checklist

Run through together once everything above is done:

1. `https://api.finzorr.ai/healthz` → `{"status":"ok"}`; `/readyz` → ok
2. Open `https://finzorr.ai` → page loads over HTTPS
3. Log in with Google (as a test user)
4. Send a chat message → streamed answer (served by Groq)
5. Ask a stock question ("TCS price") → chart renders (yfinance works)
6. Upload a small PDF → ask about it → cited answer (RAG + embeddings)
7. Create a share link → open it in a private/logged-out window
8. Refresh mid-conversation → history intact
9. Send messages rapidly → rate limit responds politely
10. `www.finzorr.ai` → works (multi-origin fix)
11. On the VM: `docker compose logs api --tail 50` → clean JSON logs
12. Next morning: check `/opt/finzorr/backups/` has a dump file

---

## 6. Day-2 operations (keep this section handy)

| Task | How |
|---|---|
| Deploy a new version | Push to `main` → approve the `production` Environment gate in the GitHub Actions run (or run `cd /opt/finzorr && sudo ./deploy/deploy.sh <tag>` directly on the server) |
| Roll back | GitHub Actions → `cd-prod.yml` → **Run workflow** → enter the previous tag/sha (or the same `deploy/deploy.sh <tag>` command directly on the server) — under 5 minutes either way |
| See logs | `docker compose logs api --tail 100 -f` |
| Backup now | `sudo /opt/finzorr/backup.sh` (nightly automatic at 02:10) |
| Restore drill | `gunzip -c backups/<file>.sql.gz \| docker compose exec -T postgres psql -U finzorr finzorr` — practice once BEFORE you need it |
| Rotate a key | Edit `/opt/finzorr/prod.env`, then `docker compose up -d api`. NOTE: rotating `SESSION_SECRET` logs every user out |
| Enable LangSmith in prod | Set `LANGSMITH_TRACING=true` + the API key in prod.env, restart api — remember prompts then leave the server |
| Daily drift watch | Add to the VM's cron: `30 7 * * * cd /opt/finzorr && docker compose run --rm api python scripts/drift_watch.py >> /opt/finzorr/backups/drift.log 2>&1` — alerts if any quality eval regresses |
| Live trace-health watch | Add to the VM's cron (only meaningful once `LANGSMITH_TRACING=true` in prod.env): `0 */6 * * * cd /opt/finzorr && docker compose run --rm api python scripts/trace_health_watch.py >> /opt/finzorr/backups/trace-health.log 2>&1` — alerts on live `degraded`/`guard:suspicious` tag rate over the trailing window; skips cleanly (exit 0) if tracing is off |
| Uptime alerts | uptimerobot.com (free) → HTTP monitor on `https://api.finzorr.ai/healthz` → email alert |
| Free-tier pressure | Watch for `ai.budget.exceeded` in logs — the chain absorbs it; recurring daily = time to consider Groq's paid tier |

---

## 7. Security notes

- **Secrets live in exactly one place**: `/opt/finzorr/prod.env` on the
  server, permissions 600, owned by root. They are never in the GitHub
  repo, never in this document, never in CI, and never in GitHub Secrets
  either — the CD pipeline's deploy step runs locally on the server via a
  self-hosted GitHub Actions runner (not SSH), so there is no deploy
  credential in GitHub at all to leak. Only the built-in, auto-rotated
  `GITHUB_TOKEN` is used, solely to push the built image to GHCR.
- **Nothing listens on the internet.** The tunnel is outbound; there are no
  open ports, so there is nothing to port-scan. The self-hosted runner adds
  no new listener either — it polls GitHub outbound, the same shape as
  `cloudflared`.
- **Deliberately off in prod**: the code-interpreter sandbox
  (docker-in-docker risk) and LangSmith tracing (data leaves the box).
- **Never commit a filled .env** — the repo's .gitignore already blocks it;
  keep it that way.
- If a key ever leaks (pasted somewhere public), rotate it at its console
  and update prod.env — a 2-minute fix for Groq/Gemini.

## 8. Known risks, stated honestly

1. **Yahoo Finance vs datacenter IPs** — tested on day one by the
   fundamentals seed; fallback plan ready if blocked.
2. **Free-tier limits** — absorbed by the budget chain; only matters at
   real scale.
3. **One VM = one point of failure** — mitigated by nightly backups and the
   <5-minute rollback; a second VM/UAT comes later if the site earns it.
4. **Cloudflare Pages preview URLs cannot call the prod API** (different
   site → CORS/cookies) — by design; previews are for eyeballing UI only.
