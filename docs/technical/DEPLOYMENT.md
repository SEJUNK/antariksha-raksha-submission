# Deployment — ANTARIKSHA-RAKSHA

This guide covers a hosted deployment of the research/demo prototype for jury validation. Nothing here makes the
prototype an operational service: it remains a decision-support prototype on **public CelesTrak data** with a
**simplified analytic collision-probability indicator, NOT an operational covariance-based Pc**.

## 1. Verdict: can it run end-to-end on Vercel alone?

**No.** The frontend can; the backend cannot without changing its persistence and runtime architecture, which this
prototype deliberately does not do.

| Requirement | Why Vercel Functions cannot hold it as built |
|---|---|
| Users, sessions, RBAC | Stored in SQLite. Function filesystems are ephemeral and per-instance; accounts and sessions would vanish or diverge between instances. |
| Governance audit, decision history | Append-only SQLite tables (triggers). Same problem; audit must never silently disappear. |
| Protected-asset registry, app settings (screening horizon) | SQLite tables, operator/administrator-managed. |
| Screening history, provenance, refresh attempts | SQLite tables written by every run. |
| CelesTrak TLE/OMM cache | Files on disk used for offline fallback and last-known-good protection. |
| Long-running refresh | A refresh takes seconds to over a minute (network-dependent) and can run in a background thread; functions end with the response and are duration-limited. |
| Scheduler safeguards | Overlap guard and the internal 2-hour scheduler are in-process objects that need one long-lived process. |
| Single source of truth | One backend process owns the database; parallel function instances would not. |

Making the backend serverless would require a managed database migration, DB-backed locking and a job queue — none
of which is done (and none is needed for jury validation).

**Minimum additional infrastructure:** one long-running Python process with a persistent disk, reachable over
HTTPS. The production path is **Railway** (one service + one volume, §7 Option B). The demo laptop behind a Cloudflare
quick tunnel (§7 Option A) remains an optional local/testing fallback and is not required for production.

## 2. Modes

### Local mode (reference environment)

```
Browser ──▶ local frontend (Vite, :5173) ──▶ local FastAPI (:8000) ──▶ local SQLite (data/antariksha.db)
                                                    ├──▶ CelesTrak (public GP data)
                                                    └──▶ local Ollama llama3.2:3b (127.0.0.1:11434, never exposed)
```

### Hosted mode (production path: Vercel + Railway)

```
Browser ──HTTPS──▶ Vercel (frontend/: static Vite build)
                     └── /api/*  ──▶ Function api/proxy.js (same-origin proxy; cookie stays first-party)
                                        │  BACKEND_URL = https://<service>.up.railway.app (server-side only)
                                        ▼
                   Railway service: Dockerfile, ONE FastAPI process (--workers 1, 1 replica)
                     ├── Railway volume mounted at /data
                     │     ├── SQLite      /data/antariksha.db   (ANTARIKSHA_DB_PATH)
                     │     └── TLE cache   /data/tle_cache       (ANTARIKSHA_TLE_CACHE_DIR)
                     ├── internal scheduler (ANTARIKSHA_SCHEDULER_MODE=internal, every 2 h, UTC)
                     ├──▶ CelesTrak (public GP/TLE) → SGP4 → screening / TCA / Pc indicator / risk
                     └── AI: ANTARIKSHA_AI_MODE=disabled → deterministic template briefs
```

No laptop, tunnel, localhost or Ollama is involved in this path.

If Ollama is not installed on the backend host, set `ANTARIKSHA_AI_MODE=disabled`. The UI then shows
**AI FALLBACK ACTIVE — DETERMINISTIC ASSESSMENT AVAILABLE**, briefs use the labelled deterministic template, and all
orbital/risk calculations run normally. No external AI provider is used.

## 3. Frontend on Vercel

What is deployed (Vercel project **Root Directory = `frontend`**):

| Item | File | Notes |
|---|---|---|
| Static app | `frontend/vercel.json` → `npm run build` → `dist/` | SPA rewrite for every non-`/api` path; NASA Blue Marble texture is served from `dist/textures/` (no runtime remote assets). |
| Same-origin API proxy | `frontend/api/proxy.js` | `vercel.json` rewrites `/api/<path>` → `/api/proxy?__ar_path=<path>` (Vercel Functions outside Next.js have no `[...catchAll]` filenames). |
| Optional cron relay | `frontend/api/cron/refresh.js` | Inert unless a Vercel Cron job is added (Pro plan, §5). Fails closed without `CRON_SECRET`. |

Proxy behaviour (`api/proxy.js`):
- Forwards method, path, query and body to `BACKEND_URL`; only allow-listed request headers (`cookie`, `content-type`,
  `x-antariksha-client`, `x-api-key`, `origin`, `user-agent`, `accept`, `accept-language`, `x-forwarded-*`). The
  browser's `Authorization` header is never forwarded.
- Returns every `Set-Cookie` individually, so the HttpOnly `SameSite=Lax` session cookie is first-party on the Vercel
  domain (no third-party cookies).
- `/api/internal/*` (including encoded/case variants) → `404`; the scheduler endpoint is never reachable from browsers.
- `BACKEND_URL` unset → `503 backend_not_configured`; timeout 55 s → `504`; network failure → `502`.

No backend secret, scheduler secret, Ollama URL or backend address is bundled into the browser build when
`VITE_API_BASE_URL` is empty (verified by scanning a production build).

## 4. Backend host options

The backend needs: Python 3.11+, outbound HTTPS to celestrak.org, a persistent disk, one always-running process, and
inbound HTTPS.

| Option | Account needed | Cost | Persistent disk | Always running (2-h scheduler) | Notes |
|---|---|---|---|---|---|
| **A. Demo laptop + Cloudflare quick tunnel** | None | Free | Yes (laptop disk) | Only while the laptop and tunnel run | Fastest path for a validation window; local Ollama keeps working. Testing-only service, no uptime guarantee, random `*.trycloudflare.com` URL that changes on every restart (update `BACKEND_URL` and redeploy). |
| **B1. Railway (Hobby) — selected production host** | Railway | ~US$5/month (includes US$5 usage credit) + volume ~US$0.15/GB-month | Yes (volume) | Yes | Repository ships `Dockerfile`, `.dockerignore` and `railway.json` (1 replica, health check `/api/health`). |
| **B2. Fly.io** | Fly.io (card) | Pay-as-you-go, ~US$2/month for a 256 MB shared VM + US$0.15/GB-month volume | Yes (volume) | Yes (disable auto-stop) | No free allowance for new accounts. 256 MB may be tight; 512 MB is safer. |
| Render free web service | Render | Free | **No** (disks are paid) | **No** (sleeps after 15 min idle) | Not suitable: data loss on restart, scheduler stops. |
| Vercel Functions | Vercel | — | **No** | **No** | Not suitable (§1). |

Facts checked on 2026-10-04 (verify current terms before signing up): Render free tier sleeps after 15 minutes and
cannot attach a disk; Railway Hobby is US$5/month with volumes at US$0.15/GB-month; Fly.io has no free allowance for
new accounts and volumes cost US$0.15/GB-month; Cloudflare quick tunnels need no account and are for testing only.

The backend is started with one command (§6). For Railway the repository root contains a `Dockerfile` (backend only,
`python:3.13-slim`, binds `0.0.0.0:$PORT`, `--workers 1`), a `.dockerignore` (never ships databases, `.env` files or
the venv) and `railway.json` (Dockerfile builder, `numReplicas: 1`, health check `/api/health`, restart on failure).

## 5. Automatic refresh in hosted mode

Use the **backend's internal scheduler** (`ANTARIKSHA_SCHEDULER_MODE=internal`): every 2 hours at even UTC hours
(`0 */2 * * *`), with the existing safeguards (skip if a successful refresh finished < 110 min ago, skip within 60 min
of a DEMO run, no overlapping refreshes, last-known-good data preserved on failure). Manual refresh always remains
available to OPERATOR and above. This needs no Vercel Cron and no paid Vercel plan.

`frontend/vercel.json` therefore contains **no `crons` entry**: Vercel's Hobby plan only allows once-per-day cron jobs
and rejects more frequent expressions at deploy time. If you later move to a plan that allows a 2-hour cron and prefer
it over the internal scheduler, add to `frontend/vercel.json`:

```json
"crons": [{ "path": "/api/cron/refresh", "schedule": "0 */2 * * *" }]
```

set `CRON_SECRET` and `SCHEDULER_SECRET` on Vercel, `SCHEDULER_SECRET` (same value) on the backend, and
`ANTARIKSHA_SCHEDULER_MODE=external` on the backend (so only one scheduler runs).

## 6. Environment variables (placeholders only — never commit real values)

### On the backend host

| Variable | Hosted value | Purpose |
|---|---|---|
| `ANTARIKSHA_DB_PATH` | `/data/antariksha.db` (on the persistent disk) | SQLite file |
| `ANTARIKSHA_TLE_CACHE_DIR` | `/data/tle_cache` | TLE/OMM cache |
| `ANTARIKSHA_CORS_ORIGINS` | `https://<your-project>.vercel.app` | The proxy forwards the browser `Origin`; login refuses other origins |
| `ANTARIKSHA_COOKIE_SECURE` | `true` | Session cookie only over HTTPS |
| `ANTARIKSHA_SESSION_HOURS` | `12` | Session lifetime |
| `ANTARIKSHA_SCHEDULER_MODE` | `internal` | 2-hour refresh by the backend itself |
| `ANTARIKSHA_AI_MODE` | `disabled` (no Ollama on the host) or `ollama` (Option A laptop) | AI explanation layer |
| `ANTARIKSHA_OLLAMA_URL` / `ANTARIKSHA_OLLAMA_MODEL` | only with `ollama`: `http://127.0.0.1:11434/api/generate` / `llama3.2:3b` | Local Ollama, never exposed |
| `ANTARIKSHA_BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | set once if the host has no shell, then remove | One-time first administrator |
| `SCHEDULER_SECRET` | only for the optional Vercel Cron path | Scheduled-refresh endpoint secret |
| `TELEGRAM_ENABLED` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | optional (§10); off unless `TELEGRAM_ENABLED=true` | Telegram alerts (backend secrets) |
| `TELEGRAM_MIN_RISK` / `TELEGRAM_NOTIFY_DEMO` / `ANTARIKSHA_PUBLIC_APP_URL` | optional: `High` / `false` / `https://<your-project>.vercel.app` | Alert policy and review link |
| `ANTARIKSHA_API_KEY` | optional | Extra deployment gate on writes |

Start command (one process, one worker, behind the host's HTTPS; this is the `Dockerfile` `CMD`):

```
uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
```

`PORT` is injected by Railway; do not set it yourself. Never raise `--workers` or the replica count: the internal
scheduler and the refresh/demo locks are in-process, so a second process would mean a second scheduler. Railway does
not overlap old and new deployments of a service with an attached volume, so a redeploy never runs two schedulers.

Install with `pip install -r backend/requirements.txt` (Python 3.11+). Health check: `GET /api/health` (public,
non-sensitive). Startup is non-destructive: missing tables/columns are added, existing data is kept.

### On Vercel (project with Root Directory `frontend`)

| Variable | Value | Scope |
|---|---|---|
| `BACKEND_URL` | `https://<backend-host>` (no trailing slash) | Server (functions) |
| `VITE_API_BASE_URL` | empty string | Build (browser) — `''` = same-origin `/api` via the proxy |
| `VITE_API_KEY` | only if `ANTARIKSHA_API_KEY` is set | Build (browser; not a secret) |
| `CRON_SECRET`, `SCHEDULER_SECRET` | only for the optional Vercel Cron path (§5) | Server |

### Local development

Nothing is required. Defaults: SQLite in `data/`, `ANTARIKSHA_AI_MODE=ollama`,
`ANTARIKSHA_OLLAMA_URL=http://127.0.0.1:11434/api/generate`, `ANTARIKSHA_OLLAMA_MODEL=llama3.2:3b`,
`ANTARIKSHA_SCHEDULER_MODE=internal`, frontend talks to `http://localhost:8000` (`VITE_API_BASE_URL` unset). Full
template: `.env.example` and `frontend/.env.example`.

## 7. Manual deployment steps

### Option A — demo laptop + Cloudflare quick tunnel (optional local/testing fallback, not production)

1. On the laptop, start the backend with hosted settings (PowerShell):
   ```
   $env:ANTARIKSHA_CORS_ORIGINS="http://localhost:5173,https://<your-project>.vercel.app"
   $env:ANTARIKSHA_COOKIE_SECURE="true"
   backend\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
   ```
   (Local Ollama keeps working; leave `ANTARIKSHA_AI_MODE` unset.)
2. Install `cloudflared` (`winget install --id Cloudflare.cloudflared`) and run
   `cloudflared tunnel --url http://localhost:8000`. Copy the printed `https://<random>.trycloudflare.com` URL.
   Only port 8000 is tunnelled — never tunnel Ollama's port 11434.
3. Vercel → Add New Project → import the GitHub repository → Root Directory `frontend` → Framework Vite.
4. Environment variables: `BACKEND_URL=https://<random>.trycloudflare.com`, `VITE_API_BASE_URL=` (empty) → Deploy.
5. Put the Vercel production domain into `ANTARIKSHA_CORS_ORIGINS` (step 1) if it differs, and restart the backend.
6. Open `https://<your-project>.vercel.app`, sign in with an account created by
   `python -m backend.users create ...`, and check the header (role chip, AI status) and `https://<your-project>.vercel.app/api/health`.
7. When the tunnel restarts its URL changes: update `BACKEND_URL` in Vercel and redeploy.

### Option B — Railway (production, always on)

Prerequisites: Railway CLI (`npm i -g @railway/cli`) and `railway login` (interactive; never paste tokens anywhere).

1. Create the project and an empty service, then attach the volume (from the repository root):
   ```
   railway init --name antariksha-raksha
   railway add --service antariksha-backend
   railway volume add --mount-path /data
   ```
2. Set the backend variables (placeholders shown; `PORT` is injected by Railway):
   ```
   railway variables --set ANTARIKSHA_DB_PATH=/data/antariksha.db --set ANTARIKSHA_TLE_CACHE_DIR=/data/tle_cache
   railway variables --set ANTARIKSHA_CORS_ORIGINS=https://<your-project>.vercel.app,http://localhost:5173
   railway variables --set ANTARIKSHA_COOKIE_SECURE=true --set ANTARIKSHA_SCHEDULER_MODE=internal --set ANTARIKSHA_AI_MODE=disabled
   ```
3. Migrate the existing state (optional; otherwise create the first administrator in step 6). Back up first, then
   prepare an upload copy — users, roles, password hashes, audit, settings, catalog and history are kept; all login
   sessions are removed; only row counts and username/role flags are printed:
   ```
   python scripts/prepare_deploy_db.py data/antariksha.db <scratch>/antariksha_deploy.db
   railway volume files upload <scratch>/antariksha_deploy.db /antariksha.db
   ```
   Upload **before** the first deployment (or with the service stopped) so the backend never has the file open.
   Never commit either database file.
4. Deploy the backend only (Dockerfile build; the frontend stays on Vercel): `railway up --ci`
5. Create the public HTTPS domain and check health:
   ```
   railway domain
   curl https://<service>.up.railway.app/api/health
   ```
   Expect `"ok": true`, `"ai": {"mode": "disabled", "status": "AI_FALLBACK_ACTIVE"}` and `"scheduler": {"mode": "internal"}`.
6. No migrated database? Create the first administrator in the service shell:
   `railway ssh -- python -m backend.users create --username <name> --role ADMINISTRATOR` (prompts for the password).
7. Point Vercel at Railway (from `frontend/`): `vercel env rm BACKEND_URL production`, `vercel env add BACKEND_URL
   production` (value `https://<service>.up.railway.app`), keep `VITE_API_BASE_URL` empty, then
   `vercel deploy --prod --yes`.
8. Operate: `railway restart` (restart the running deployment), `railway redeploy` (rebuild the latest), `railway logs`
   (look for `Internal scheduler started` once per start), `railway volume files list /` (persistent files). Data on
   `/data` survives restarts and redeploys.

## 8. Security notes

- Never commit `.env` files, passwords, tokens or secrets; set them in each platform's environment settings.
- Never expose Ollama (11434) or the SQLite file; only the FastAPI port is published.
- Keep HTTPS end to end and `ANTARIKSHA_COOKIE_SECURE=true` in hosted mode.
- Authentication, RBAC, audit, last-known-good protection and scheduler guards stay enabled in every mode.
- A quick tunnel makes the backend reachable by anyone who has the URL; access still requires login, but use it only
  for the validation window.
- Vercel needs read access to the repository through its GitHub integration.
- Telegram credentials (if used) are Railway variables only; the browser never receives them (§10).

## 9. Limitations

- The prototype is not an operational or production deployment; production would additionally require enterprise
  identity integration, MFA, stronger secret management and hardened identity/audit infrastructure.
- Without Ollama on the backend host (the Railway path), AI briefs use the labelled deterministic template; screening,
  TCA, miss distance, relative velocity, Pc indicator and risk never depend on an LLM.
- SQLite on one volume means exactly one backend instance; scaling out would need the managed-database migration of §1.

## 10. Optional Telegram notifications

**Feature.** The backend can push a short alert to one Telegram chat when screening produces a significant event.
**Architecture:** notification is an external alert channel connected to the backend, not to the physics engine —
it runs after a screening run's events are stored and only reads stored values.
**Operational boundary:** Telegram notifications do not authorize or execute spacecraft maneuvers; they cannot
approve or dismiss an assessment, change data or risk values, or bypass RBAC. The operator opens the console and
decides there. **Telegram is optional; the application works without it.**

```
CelesTrak → ingest → SGP4 → screening → TCA → miss / rel. velocity / analytic Pc → risk tier → stored events
    → notification decision (backend/notify.py) → Telegram Bot API (HTTPS) → operator → console → human decision → audit
Browser → Vercel → Railway FastAPI → Telegram Bot API     (never from browser JavaScript)
```

**Policy.**
- Off unless `TELEGRAM_ENABLED=true` (credentials alone never enable sending).
- Tiers: the existing risk tiers only — Critical and High notify by default; `TELEGRAM_MIN_RISK=Medium` adds Medium,
  `Critical` restricts to Critical; Low never notifies. No new thresholds.
- DEMO events are not notified by default. With `TELEGRAM_NOTIFY_DEMO=true` they are sent with the header
  "🧪 DEMO — CONTROLLED SIMULATION / This is NOT an operational conjunction warning."
- De-duplication per event track (the same encounter correlated across screening runs, as in Event Evolution): a
  track is notified when its tier is notifiable and higher than every tier already delivered for that track. First
  detection → one alert; same tier on later refreshes → none; High → Critical → one new alert; de-escalation → none.
  A track break (the encounter missing from one run, a TCA jump beyond the correlation window, a demo reset) starts a
  new track and can alert again.
- At most 5 alerts per screening run (highest priority first; the rest follow at the next run). After the first
  failed delivery in a run (bad credentials, Telegram down) the remaining alerts wait for the next run, so a broken
  channel produces one audited attempt per run and never a burst.
- Delivery attempts are recorded in the governance audit (`action = notification`, channel, status, event and track
  id, risk tier); never the token or chat id. Disabled Telegram writes nothing; enabled-but-unconfigured writes one
  `unconfigured` row per run; test sends are always audited.
- Public `/api/health` reports only `telegram_configured: true|false` (true = enabled and credentials present).

**Security.** Telegram credentials are backend deployment secrets (Railway variables). They are never in Git, Vercel
or `VITE_*` variables, API responses, audit rows or logs (errors log only the exception type/HTTP status; urllib3
debug lines are redacted). `GET /api/admin/notifications/telegram` (ADMINISTRATOR) returns only non-secret flags
(`bot_token: "********"`, `chat_id: "configured"`). `POST /api/admin/notifications/telegram/test` (ADMINISTRATOR,
CSRF header, one per 60 s, audited) sends a fixed server-built test message — client text is ignored.

**Limitation.** Telegram delivery depends on external Telegram service availability; alerts are best-effort (no
delivery guarantee, retries beyond the next run, or escalation).

**Setup (placeholders only — never paste the token into chats, tickets or documents):**
1. In Telegram, open **@BotFather** → `/newbot` → choose a name and username. BotFather replies with
   `<TELEGRAM_BOT_TOKEN>`; keep it private.
2. Create or pick the destination chat (a private chat with the bot, or a group with the bot added) and send it any
   message.
3. Find `<TELEGRAM_CHAT_ID>`: open `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates` in your own browser
   and read `"chat":{"id": ...}` (group ids are negative).
4. Set the Railway variables (from the repository root; values typed by you, never committed):
   ```
   railway variables --set TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN> --set TELEGRAM_CHAT_ID=<TELEGRAM_CHAT_ID>
   railway variables --set TELEGRAM_ENABLED=true --set ANTARIKSHA_PUBLIC_APP_URL=https://<your-project>.vercel.app
   ```
   Changing variables redeploys the service; otherwise `railway restart`.
5. Sign in as an ADMINISTRATOR → **Users** panel → **Telegram notifications** shows *Enabled* → **Send test
   notification**, and confirm the message arrives.
6. To disable: `railway variables --set TELEGRAM_ENABLED=false` (or delete the variables). Nothing else changes.

**Jury demonstration path.**
1. Start in LIVE DATA. 2. (Optional, for the demo only) set `TELEGRAM_NOTIFY_DEMO=true`. 3. Run the controlled
COLLISION demo. 4. Open the event: TCA, miss distance, relative velocity, Pc indicator, risk tier, provenance,
limitations. 5. Show the Telegram alert, clearly headed **DEMO — CONTROLLED SIMULATION**, and open the console from
its review link. 6. Explain that Telegram is only the notification channel. 7. Show Human Decision and Audit (the
delivery is in the governance audit). 8. State: *"Telegram can notify the operator, but it cannot approve an
assessment, issue a spacecraft command, or execute a maneuver."* 9. Exit the demo and set `TELEGRAM_NOTIFY_DEMO`
back to `false`.
