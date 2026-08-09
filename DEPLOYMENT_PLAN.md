# finzorr.ai — Production Deployment Plan (OVH VM + Cloudflare)

**Status: PLAN ONLY — nothing in this document has been implemented yet.**
This is the complete blueprint for taking finzorr live at https://finzorr.ai
using the OVH VM and the Cloudflare account (domain already configured).
When you decide to go live, this document is the script we follow.

---

## 1. Architecture overview — what runs where

There are three places where things run, and it is important to keep them
straight:

**A. Your OVH VM** (the server you rent) — runs the backend as six Docker
containers:

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

### 2.1 Groq API key (~2 minutes)
1. Open https://console.groq.com and Sign up (easiest: "Continue with
   Google").
2. Left sidebar → **API Keys** → **Create API Key** → name it
   `finzorr-prod` → Create.
3. The key (starts `gsk_...`) is shown ONCE — copy it into your password
   manager now. (Lost keys are no problem: create a new one.)

### 2.2 Gemini API key (~2 minutes)
1. Open https://aistudio.google.com and sign in with your Google account.
2. Click **Get API key** → **Create API key** → if asked for a project,
   pick "Create API key in new project".
3. Copy the key (starts `AIza...`) into your password manager.

### 2.3 Google Client ID — you already have it
The OAuth client used for Google login in development. Nothing to create;
it's in your local `frontend/.env` and in console.cloud.google.com under
Credentials.

### 2.4 VM specs and SSH
1. Log in to https://manager.ca.ovhcloud.com → click your VPS → the
   overview shows **RAM / vCPU / OS** (need: ≥6 GB RAM, Ubuntu or Debian).
   Note these down.
2. Confirm you can SSH in from your terminal: `ssh <user>@<vm-ip>`. OVH
   emailed the credentials when the VM was created; add your SSH key if you
   haven't.

### 2.5 Cloudflare tunnel token — created DURING deployment
Not needed in advance. On deployment day: Cloudflare dashboard → Zero Trust
→ Networks → Tunnels → **Create a tunnel** → name `finzorr-prod` → choose
Docker → copy the long token (starts `eyJ...`). Keep the tab open; you'll
also map the hostname there (step 4.3).

**Prerequisite summary: two free keys + VM specs + working SSH. Total ~10
minutes.**

---

## 3. What gets built in the repo (the coding work, when approved)

These are the files the implementation wave will add — none exist yet:

| File | Purpose |
|---|---|
| `backend/Dockerfile` + `.dockerignore` | Packages the backend as a container image (python 3.12-slim, locked deps, non-root user, 2 uvicorn workers) |
| `deploy/docker-compose.prod.yml` | The six-container stack above — pinned image versions, memory caps, named volumes (data survives redeploys), **no published ports** |
| `deploy/prod.env.template` | Every config value pre-filled except the four you paste; becomes `/opt/finzorr/prod.env` on the VM |
| `deploy/vm-bootstrap.sh` | The ONE script you run on the VM — installs Docker, lays out `/opt/finzorr`, prompts for your four values, builds the image, runs database migrations, pulls the two small local models, seeds the glossary + fundamentals, installs the backup cron, health-checks |
| `deploy/deploy.sh` | Day-2 deploys: `./deploy.sh <tag>` — pull image, migrate, restart, health-check. Rollback = same command with the previous tag (<5 min) |
| `deploy/backup.sh` | Nightly compressed Postgres dump, 14-day rotation |
| `.github/workflows/cd-prod.yml` | After first launch: every push to main builds the image and deploys to the VM — behind an approval click only you can give |
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

### 4.2 Cloudflare Pages (frontend, ~5 minutes)
Dashboard → Workers & Pages → Create → Pages → **Connect to Git** → select
`ARETYON/finzorr`:
- Root directory: `frontend`
- Build command: `npm run build`
- Output directory: `dist`
- Environment variables:
  - `VITE_API_BASE_URL` = `https://api.finzorr.ai`
  - `VITE_GOOGLE_CLIENT_ID` = your client id
- After first build: Custom domains → add `finzorr.ai` and
  `www.finzorr.ai` (Cloudflare wires the DNS itself).

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
GitHub repo → Settings → Environments → create `production` → add yourself
as required reviewer. Settings → Secrets → add `SSH_HOST` (`user@vm-ip`)
and `SSH_DEPLOY_KEY` (a private key whose public half is on the VM). Also
make the GHCR package public (one click) so the VM can pull images. From
then on, every push deploys after your approval click.

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
| Deploy a new version | `cd /opt/finzorr && sudo ./deploy.sh <tag>` (or approve the GitHub action) |
| Roll back | Same command with the previous tag — under 5 minutes |
| See logs | `docker compose logs api --tail 100 -f` |
| Backup now | `sudo /opt/finzorr/backup.sh` (nightly automatic at 02:10) |
| Restore drill | `gunzip -c backups/<file>.sql.gz \| docker compose exec -T postgres psql -U finzorr finzorr` — practice once BEFORE you need it |
| Rotate a key | Edit `/opt/finzorr/prod.env`, then `docker compose up -d api`. NOTE: rotating `SESSION_SECRET` logs every user out |
| Enable LangSmith in prod | Set `LANGSMITH_TRACING=true` + the API key in prod.env, restart api — remember prompts then leave the server |
| Daily drift watch | Add to the VM's cron: `30 7 * * * cd /opt/finzorr && docker compose run --rm api python scripts/drift_watch.py >> /opt/finzorr/backups/drift.log 2>&1` — alerts if any quality eval regresses |
| Uptime alerts | uptimerobot.com (free) → HTTP monitor on `https://api.finzorr.ai/healthz` → email alert |
| Free-tier pressure | Watch for `ai.budget.exceeded` in logs — the chain absorbs it; recurring daily = time to consider Groq's paid tier |

---

## 7. Security notes

- **Secrets live in exactly one place**: `/opt/finzorr/prod.env` on the VM,
  permissions 600, owned by root. They are never in the GitHub repo, never
  in this document, never in CI. GitHub holds only the SSH deploy key.
- **Nothing listens on the internet.** The tunnel is outbound; there are no
  open ports, so there is nothing to port-scan.
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
