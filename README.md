# ANTARIKSHA-RAKSHA

**Space Domain Awareness (SDA) and conjunction-assessment decision-support prototype**

*Author: Sejal Khimani* · *Status: working research/demo prototype* · *Public demo: <https://antariksha-raksha.vercel.app/>*

*Sanskrit: "Antariksha" (space) + "Raksha" (protection).*

> **Key facts for reviewers**
>
> | Question | Short answer |
> |---|---|
> | Is this real data? | Yes. Public CelesTrak GP/TLE element sets, fetched live or loaded from a local cache. The UI mode chip says which. Controlled demo scenarios are always labelled **DEMO SCENARIO**. |
> | Is it an operational collision warning service? | **No.** It is a decision-support prototype. TLE accuracy and a simplified probability model make it unsuitable for operational warnings. |
> | Is the collision probability operational? | **No.** Simplified analytic collision-probability indicator — NOT an operational covariance-based Pc. |
> | Does the AI compute anything orbital? | **No.** The AI only drafts brief text from numbers the backend has already computed. |
> | Can it command a satellite? | **No.** No command, uplink or maneuver-execution path exists anywhere in the code. |
> | Is it integrated with ISRO / IS4OM / NETRA or any organisation? | **No.** There is no integration or partnership with any organisation. |

---

## Contents

1. [What ANTARIKSHA-RAKSHA is](#1-what-antariksha-raksha-is)
2. [The problem](#2-the-problem)
3. [Seven-stage workflow](#3-seven-stage-workflow)
4. [Architecture](#4-architecture)
5. [Technology stack](#5-technology-stack)
6. [Data source](#6-data-source)
7. [Orbital propagation](#7-orbital-propagation)
8. [Conjunction detection](#8-conjunction-detection)
9. [TCA refinement](#9-tca-refinement)
10. [Collision-probability indicator](#10-collision-probability-indicator)
11. [Risk prioritisation](#11-risk-prioritisation)
12. [AI explanation boundary](#12-ai-explanation-boundary)
13. [Human-in-the-loop governance](#13-human-in-the-loop-governance)
14. [Auditability](#14-auditability)
15. [Deployment architecture](#15-deployment-architecture)
16. [Known limitations](#16-known-limitations)
17. [Future evolution](#17-future-evolution)
18. [Running locally](#running-locally) · [Running the tests](#running-the-tests) · [Repository layout](#repository-layout) · [Demo video](#demo-video) · [Documentation](#documentation)

---

## 1. What ANTARIKSHA-RAKSHA is

ANTARIKSHA-RAKSHA is a **decision-support prototype for Space Domain Awareness and conjunction assessment**. It
ingests public orbital data for a working set of Indian satellites, selected debris fragments and selected
non-Indian active satellites; propagates them with SGP4; screens each protected Indian asset for close approaches
and sustained proximity patterns; refines each close approach; estimates a simplified collision-probability
indicator; ranks events by asset criticality; and drafts a plain-language operator brief with a local AI model.
A human operator then records a decision, which is kept in a persistent audit trail.

The innovation is not any single algorithm. It is the **integration** of:

- **deterministic orbital evidence** (SGP4 propagation, episode-based screening, refined TCA, miss distance,
  relative velocity),
- **risk prioritisation** (risk tiers plus criticality-weighted priority),
- **explainable AI assistance** (a local model that drafts text only, with fact checks, provenance labels and a
  deterministic fallback),
- **human governance** (role-based access, explicit human decisions, no command path), and
- **auditability** (decision log, screening-run log, append-only governance audit, per-event provenance).

For India, it is intended as a **complementary, modular and potentially sovereign software layer**: it runs on
commodity hardware with a local open-weight model and no cloud AI service, and it is designed so that whoever
deploys it can run and inspect it locally. It does not replace, and is not integrated with, national SDA/SSA
systems such as ISRO's IS4OM or the NETRA programme.

**What it is not:** it is not an operational collision-warning service, not a weapon, missile-defence, guidance or
electronic-warfare system, and it takes no autonomous action. It does not plan or execute collision-avoidance
manoeuvres.

## 2. The problem

India's satellites (navigation, communications and Earth observation) operate in an increasingly congested orbital
environment that also contains long-lived debris, for example fragments from the 2007 Fengyun-1C anti-satellite
test and the 2009 Iridium-33 / Cosmos-2251 collision. Close-approach screening produces raw numbers — times, miss
distances, probabilities — that need expert interpretation before an operator can act on them.

The gap this prototype explores is the step **from raw conjunction numbers to a prioritised, explained, auditable
human decision**: which events matter most for which assets, why, how much the numbers can be trusted, what the
operator decided, and who decided it.

## 3. Seven-stage workflow

**SEE → PREDICT → DETECT → UNDERSTAND → PRIORITISE → EXPLAIN → DECIDE**

| Stage | What the code does | Main code |
|---|---|---|
| **SEE** | Ingests public CelesTrak GP data (TLE by default, OMM optional) for the working set; validates element sets; excludes objects whose element-set epoch is more than 30 days old; labels data freshness (LIVE / CACHED / STALE / DEMO / NOT SCREENED); falls back to a local cache when offline. | `backend/ingest.py`, `backend/orbital_formats.py`, `backend/propagate.py` |
| **PREDICT** | Propagates every object with SGP4 (skyfield) on one shared time grid: 72 h at a 60 s step by default. | `backend/propagate.py` |
| **DETECT** | Screens each protected asset against every other object using close-approach *episodes* and a 10 km candidate threshold; separately flags sustained proximity (another active satellite within 25 km for at least 10 consecutive 1-minute samples). | `backend/conjunction.py`, `backend/threat.py` |
| **UNDERSTAND** | Refines the time of closest approach (TCA) with SGP4 re-evaluation, then evaluates miss distance, relative velocity and the collision-probability indicator at that same refined instant; records per-event provenance. | `backend/conjunction.py`, `backend/risk_score.py`, `backend/provenance.py` |
| **PRIORITISE** | Assigns a risk tier from the probability indicator and a priority score weighted by the asset's criticality tier. | `backend/risk_score.py`, `backend/config.py` |
| **EXPLAIN** | A local LLM drafts a plain-language brief from the computed values; a deterministic fact check, a tone guard and a second-pass consistency review check it; a deterministic template is used whenever the AI is unavailable, disabled or fails the checks. | `backend/brief_agent.py` |
| **DECIDE** | An authorised human operator approves (acknowledges, for proximity events) or dismisses (with a stated reason). The decision is recorded with the acting user and role. Nothing is sent to any spacecraft. | `backend/main.py`, `backend/auth.py`, `backend/db.py`, `frontend/src/components/ApprovalPanel.jsx` |

## 4. Architecture

A linear pipeline on one backend process, not a distributed system.

```
 ┌──────────────────────────────────────┐
 │ Public orbital data: CelesTrak GP    │  ingest.py (offline cache fallback: data/tle_cache/)
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ Validation & freshness labelling     │  epoch age > 30 d excluded; LIVE/CACHED/STALE/DEMO
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ SGP4 propagation (skyfield)          │  72 h window, 60 s step (default)
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ Screening: protected asset vs all    │  episode-based, 10 km candidate threshold
 │ + proximity watch (25 km dwell)      │
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ TCA refinement, miss distance,       │  bounded minimisation on re-evaluated SGP4 states
 │ relative velocity                    │
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ Simplified Pc indicator, risk tier,  │  2D encounter plane, isotropic assumed sigma,
 │ criticality-weighted priority        │  20 m hard-body radius
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ AI operator brief (local Ollama)     │  text only; fact check + tone guard + review;
 │                                      │  deterministic template fallback
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ Human decision (RBAC-gated)          │  approve / acknowledge / dismiss; no command path
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ Persistent audit (SQLite)            │  decision_log, screening_runs, governance_audit
 └──────────────────────────────────────┘

 Frontend (React + CesiumJS): globe, event feed, event detail, provenance, decisions,
 analytics, admin. Visualisation only — the backend is the source of truth.
```

## 5. Technology stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn, SQLite |
| Orbital mechanics | skyfield, sgp4, NumPy, SciPy |
| Frontend | React 18, Vite, CesiumJS (via resium), locally served NASA Blue Marble texture (Natural Earth II bundled with Cesium as fallback; no map API key or Cesium Ion token) |
| AI (optional) | Ollama running `llama3.2:3b` locally (open-weight model under Meta's Llama licence) |
| Report export | `docx` (Word incident report, built in the browser) and browser print to PDF |
| Localisation | UI in 11 languages; optional local IndicTrans2 translation of briefs (off by default, no external API) |
| Notifications (optional) | Telegram bot alerts, off unless explicitly enabled |
| Hosting | Vercel (static frontend + serverless proxy), Railway (Docker backend with persistent volume) |
| Tests | pytest (backend), Node's built-in test runner (frontend) |

No cloud AI provider is used. There is no software licence cost for the prototype; that is not a statement about
total operational cost.

## 6. Data source

- **Source:** CelesTrak public GP data via `https://celestrak.org/NORAD/elements/gp.php`, as TLE (default) or native
  OMM (`ANTARIKSHA_CELESTRAK_FORMAT=omm`, which also preserves catalogue numbers above 99999).
- **Working set** (`data/working_set.json`, resolved by name/group at ingest time):
  - **Group A, protected assets:** 11 Indian satellites, each with a criticality tier (Tier 1 strategic, Tier 2
    observation, Tier 3 civil). After first start these seed the operator-managed protected-asset registry in the
    database.
  - **Group B, debris:** subsampled CelesTrak groups — Fengyun-1C debris (up to 50), Cosmos-2251 debris (up to 20),
    Iridium-33 debris (up to 10).
  - **Group C, other active satellites:** 12 non-Indian active satellites chosen by orbital-regime similarity to
    Group A. Nationality is not a selection criterion and nothing is implied about intent.
- The working set holds up to roughly a hundred objects. This is the current prototype set, not the full public
  catalogue.
- **Ingest safety:** a refresh is refused (catalogue untouched) if too few Group A entries resolve or if
  reconciliation would deactivate too large a fraction of the active catalogue.
- **Offline:** after the first successful download the pipeline can run from `data/tle_cache/`; the mode chip then
  shows **CACHED DATA**.

## 7. Orbital propagation

- **SGP4 via skyfield** (`backend/propagate.py`), every object on one shared time grid.
- **Default horizon 72 h at a 60 s step** (4,321 samples per object, inclusive of both ends).
- **Horizon options:** 24, 48, 72, 96 or 120 h. Only an ADMINISTRATOR can change it; the change is written to the
  governance audit and applies from the next screening run. The 60 s step is fixed. Each screening run records the
  horizon it actually used.
- A longer horizon covers more time with larger orbital uncertainty. It is a screening window, not an accuracy
  guarantee: TLE/SGP4 errors grow with propagation time.

## 8. Conjunction detection

- Each protected asset is screened against every other object in the working set (`backend/conjunction.py`).
- A 60 s grid can step past a fast crossing (at ~10 km/s objects move ~600 km between samples), so candidates are
  **close-approach episodes**, not single grid samples: an episode is a run of samples whose separation is below
  `10 km + |v_rel| × 30 s + 1 km margin`, and the minimum of each episode is carried forward.
- The **10 km candidate threshold** is applied to the refined miss distance. It generates candidates for review in
  this prototype; it is **not** a collision criterion. A pair can produce more than one event if it has several
  separate encounters in the window.
- **Proximity watch** (`backend/threat.py`): another active satellite within 25 km for at least 10 consecutive
  1-minute samples is recorded as a `proximity_watch` event. No probability is computed for these, and the wording
  is deliberately intent-neutral: a pattern for human review, not an accusation.

## 9. TCA refinement

1. Each candidate episode is first refined cheaply on a quadratic interpolation of the grid.
2. Candidates whose interpolated miss distance is under 10.5 km are then refined by **bounded minimisation of the
   separation within ±1 grid step**, re-running SGP4 for both objects at each evaluated instant.
3. **Miss distance, relative velocity and the positions used for the probability indicator are all evaluated at
   the same refined TCA instant.** This is covered by tests.

## 10. Collision-probability indicator

**Simplified analytic collision-probability indicator — NOT an operational covariance-based Pc.**

Implemented in `backend/risk_score.py::analytic_collision_probability`:

1. Take relative position **r** and relative velocity **v** at the refined TCA.
2. The encounter plane is perpendicular to **v** (short-encounter assumption). Project **r** into it:
   `r_perp = r − (r·v̂) v̂`, `d = |r_perp|`.
3. Each object has an **assumed isotropic** position sigma, which grows linearly with lead time:

   ```
   sigma_km = 0.1 + 0.05 × (hours_to_TCA / 24)
   ```

   The relative uncertainty in the plane is `N(0, s² I₂)` with `s = √2 · sigma`.
4. `Pc = P(|X| < R)` for `X ~ N(r_perp, s² I₂)`, with **hard-body radius R = 20 m**. This is the Rician CDF,
   evaluated by adaptive quadrature in an overflow-safe form (scaled Bessel function `i0e`).
5. If **v** is effectively zero (below 1 mm/s) there is no encounter plane, and a labelled 3D fallback is used.

Properties and boundaries:

- **Deterministic and continuous:** the same inputs always give the same value; no sampling.
- **No covariance from the data.** TLEs carry none and CDMs are not ingested; the uncertainty is assumed, not
  measured.
- **Monte Carlo is used only as a test reference:** the tests cross-check the analytic value against the
  non-central chi-square CDF and a projected Monte Carlo simulation. Monte Carlo is not used for production scoring.
- Absolute values are indicative only and are not validated against an authoritative Pc.

## 11. Risk prioritisation

- **Risk tier** (from the indicator): Critical if Pc > 1e-4, High if Pc > 1e-5, Medium if Pc > 1e-6, otherwise Low.
- **Priority** (sort order): `Pc × criticality multiplier × 10⁶`, with multipliers Tier 1 = 3, Tier 2 = 2,
  Tier 3 = 1.
- **Criticality affects priority only.** It never changes the risk tier.
- Proximity-watch events sit in a fixed priority band below Medium collision events unless their dwell time is
  extreme. Their tier is High when elapsed dwell exceeds 60 minutes with minimum separation under 10 km, otherwise
  Medium.

## 12. AI explanation boundary

| The AI does | The AI does **not** |
|---|---|
| Draft a plain-language operator brief from values the backend has already computed (local Ollama, `llama3.2:3b`) | Compute orbits, TCA, miss distance, relative velocity, Pc, risk tier or priority |
| Get checked by a **deterministic fact check** (numbers, risk tier, recommendation and intent language), a **deterministic tone guard** against sensational certainty, and a **second-pass consistency review by the same local model** | Provide independent scientific validation — the review is another pass of the same model, not verification |
| Receive in-context feedback: up to the 3 most recent operator rejection reasons are added to later prompts | Learn in the training sense — no weights change; no training, fine-tuning or reinforcement learning |
| Carry a provenance label on every brief (AI-drafted with review status, or deterministic template) | Take any action, change any configuration or protected-asset status, or command anything |

- **Deterministic template fallback:** if Ollama is unreachable, disabled, or a draft fails the tone guard twice,
  a deterministic template brief is used and clearly labelled. Screening and scoring never depend on the AI.
- **AI modes:** `ANTARIKSHA_AI_MODE=ollama` (default, local only) or `disabled` (no network call; template briefs
  immediately). The console shows **AI FALLBACK ACTIVE** when the AI is unavailable or disabled.
- **Hosted deployment:** on the hosted backend AI mode is **disabled**, so all briefs there are the deterministic
  template. Ollama stays on a local machine and must never be exposed publicly.

## 13. Human-in-the-loop governance

- Every event requires an explicit human decision: **approve** (shown as **acknowledge** for proximity events) or
  **dismiss** with a stated reason. Decisions are idempotent and record the acting user and role.
- **No command is sent to any spacecraft.** There is no command, uplink or manoeuvre-execution path in the code.
  The Δv figure shown in the UI is a fixed illustrative estimate (≈ 0.046 m/s, the same for every event), not a
  manoeuvre recommendation.
- **Prototype role-based access control**, enforced by the backend (`backend/auth.py::ROLE_PERMISSIONS`):

  | Role | Permissions |
  |---|---|
  | `VIEWER` | View events, provenance, decisions and audit trails |
  | `OPERATOR` | VIEWER + refresh, run the controlled demo, record decisions, acknowledge new objects |
  | `ASSET_MANAGER` | OPERATOR + manage the protected-asset registry |
  | `ADMINISTRATOR` | ASSET_MANAGER + manage users and roles, system settings, read the governance audit |

- Local accounts only: PBKDF2-HMAC-SHA256 password hashes, **no default credentials**, HttpOnly session cookie,
  CSRF header on every state-changing request. No MFA or SSO (see limitations).
- The AI cannot change protected-asset status, roles or configuration.

## 14. Auditability

All records live in SQLite (`backend/db.py`):

- **`decision_log`** — persistent operator decision trail (event, decision, reason, UTC time, acting user and role,
  screening run, demo flag). Exportable to CSV.
- **`screening_runs`** — one row per screening run: mode (live / cached / demo), source, ingest time, cache use,
  object counts, TLE age, horizon, step, threshold, candidate pairs, event counts.
- **`governance_audit`** — **append-only** (SQLite triggers block UPDATE and DELETE): logins, user administration,
  protected-asset changes, horizon changes.
- Per-event **provenance** (`GET /api/events/{id}/provenance`) separates deterministic, probabilistic, AI-generated
  and human-decided elements, and marks demo events.
- Per-event incident report export (Word, or PDF via browser print).

This is an accountability aid for a prototype. It is **not tamper-proof**: anyone with file access to the SQLite
database can alter or replace it.

## 15. Deployment architecture

```
Browser ─▶ Vercel (static SPA + /api same-origin proxy function) ─▶ Railway (FastAPI, Docker, 1 process)
                                                                       ├─▶ volume /data (SQLite + TLE cache)
                                                                       └─▶ CelesTrak ─▶ SGP4 ─▶ screening / TCA / Pc indicator / risk
Backend internal scheduler (every 2 h, even UTC hours) ─▶ same refresh pipeline as manual refresh
ANTARIKSHA_AI_MODE=disabled on the hosted backend ─▶ deterministic template briefs
```

- **Frontend → Vercel** (`frontend/vercel.json`, Root Directory `frontend`): static Vite build. `/api/*` is
  rewritten to `frontend/api/proxy.js`, a same-origin proxy to the backend, so the session cookie is first-party;
  `/api/internal/*` is blocked. `frontend/api/cron/refresh.js` is an optional cron relay, inert unless a cron job is
  added on a plan that allows it.
- **Backend → Railway** (`Dockerfile`, `railway.json`): one replica, one Uvicorn worker (the scheduler and refresh
  locks are in-process), health check `GET /api/health`, SQLite database and TLE cache on a mounted volume.
- **Automatic refresh:** the backend's internal scheduler refreshes public data every 2 hours. This is periodic
  public-data refresh, not real-time sensor tracking. Manual refresh is always available to authorised roles.
- Configuration uses environment variables only; templates with placeholders are in [`.env.example`](.env.example)
  and [`frontend/.env.example`](frontend/.env.example). Real `.env` files are git-ignored.
- Full guide: [docs/technical/DEPLOYMENT.md](docs/technical/DEPLOYMENT.md).

## 16. Known limitations

- **TLE/SGP4 accuracy is inherently limited** (errors from hundreds of metres to kilometres, growing with
  propagation time). Precise ephemerides and covariance are not used.
- **The Pc indicator is simplified:** assumed isotropic sigma, no covariance, short-encounter assumption (weak for
  slow or co-orbital encounters). It is not validated against authoritative Pc values or CDMs.
- **The Δv figure is illustrative** and identical for every event; there is no manoeuvre planning.
- **Small working set** (up to roughly a hundred objects); only protected assets are screened against the rest.
- **The AI review uses the same model** as the drafter; it is not independent validation.
- **The audit trail is a local SQLite file** and is not tamper-proof.
- **Prototype access control:** local accounts only; no MFA, SSO, account lockout or login rate limiting.
- **Minimal security hardening;** this is a research/demo prototype, not a production or classified system.
- **Single-process backend** on a host with a persistent disk; no high availability.
- **Event correlation across runs** is a prototype matching rule, not an authoritative event identity.
- **Controlled demo scenarios** are synthetic geometries built from real objects as a separate override; they never
  modify real catalogue rows, but they are not replays of historical events.
- No independent cross-check against an external conjunction service has been performed.

## 17. Future evolution

*Everything in this section is future work. None of it exists in the prototype today.*

- Ingest standard Conjunction Data Messages and realistic covariance; validated Pc using that covariance in the
  existing encounter-plane formulation.
- Consume authoritative and indigenous data sources and multi-sensor fusion, subject to access — as a
  complementary decision-support layer on top of such sources.
- Scale toward the full public catalogue with vectorised propagation and spatial indexing.
- Independent cross-checks against public conjunction services and reference cases.
- Enterprise identity (SSO, MFA), stronger secret management, tamper-evident audit.
- Historical replay once authoritative historical data can be integrated.

---

## Running locally

**Prerequisites:** Python 3.11+, Node.js LTS with npm. Optional: [Ollama](https://ollama.com) with
`ollama pull llama3.2:3b` (without it, briefs use the deterministic template).

### Backend (from the repository root)

```bash
python -m venv backend/venv
# Windows: backend\venv\Scripts\activate      macOS/Linux: source backend/venv/bin/activate
pip install -r backend/requirements.txt

python -m backend.ingest                       # first run needs network; caches element sets in data/tle_cache/
python -m backend.users create --username admin1 --role ADMINISTRATOR      # password prompted
python -m backend.users create --username operator1 --role OPERATOR
python -m uvicorn backend.main:app --port 8000
```

There are **no default credentials**: create at least one account before the first login. Other user commands:
`list`, `set-password`, `set-role`, `activate`, `deactivate`.

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173` and sign in. Without `VITE_API_BASE_URL` the frontend talks to
`http://localhost:8000`.

### Controlled demo scenarios

From the UI use the **Collision** / **Proximity** demo controls in the event feed, or from the command line:

```bash
python -m scripts.seed_demo_event --asset CARTOSAT-3 --debris FENGYUN
python -m scripts.seed_demo_event --asset RISAT-2BR1 --proximity --foreign SENTINEL-2A
```

Demo events are flagged `is_demo`, labelled **DEMO SCENARIO**, and never modify real catalogue data. A normal
refresh returns to live data.

## Running the tests

```bash
# Backend (from the repository root, with the backend venv)
python -m pytest backend/tests -q

# Frontend
cd frontend
npm ci
npm test
npm run build
```

The tests use seeded or synthetic data and need neither network access nor Ollama. They cover propagation,
screening and TCA refinement, the analytic Pc indicator (including the Monte Carlo and chi-square cross-checks),
proximity dwell detection, provenance, demo integrity, AI fallback and fact checking, RBAC, the audit trails,
the protected-asset registry, screening-horizon provenance, i18n parity and the deployment configuration
(Dockerfile, `railway.json`, `.dockerignore`, `vercel.json`, the Vercel proxy and cron relay, and placeholder-only
`.env.example` files). Current results are recorded in
[docs/technical/TESTING_VALIDATION.md](docs/technical/TESTING_VALIDATION.md).

## Repository layout

```
backend/                 FastAPI app, orbital pipeline, AI brief agent, auth/RBAC, SQLite persistence
  tests/                 pytest suites (+ fixtures)
  requirements.txt
frontend/                React + Vite + CesiumJS console
  api/                   Vercel serverless functions: same-origin proxy, optional cron relay
  public/textures/       Locally served Earth texture (+ ATTRIBUTION.md)
  src/                   UI, view models, i18n (11 languages), frontend tests
  vercel.json
data/working_set.json    Working-set definition (protected assets, debris groups, other satellites)
scripts/                 Demo-scenario CLI and deploy-database preparation script
Dockerfile, railway.json, .dockerignore   Backend container and Railway configuration
.env.example, frontend/.env.example       Placeholder-only environment templates
docs/
  architecture/          Original design blueprint (historical planning document)
  technical/             Deployment guide, testing and validation record
  jury/                  Jury readiness notes, case studies
demo/                    Jury demo material (storyboard, screenshots, narration, video)
submission/
  application/           Application material (added separately)
  supporting/            Concept note, pitch deck
```

Runtime files (`data/antariksha.db`, `data/tle_cache/`) are created locally and are not part of the repository.

## Demo video

The 4:49 jury demonstration video, [`demo/video/ANTARIKSHA_RAKSHA_Jury_Demo.mp4`](demo/video/ANTARIKSHA_RAKSHA_Jury_Demo.mp4)
(1920 × 1080), uses unedited captures of the deployed application taken on 5 Oct 2026. The collision event shown is
a clearly labelled **controlled DEMO scenario**, not an operational warning.

| Folder | Contents |
|---|---|
| `demo/video/` | Final MP4, production notes (`README.md`), claim-by-claim `FACTUAL_VALIDATION.md`, and the capture/render sources |
| `demo/narration/` | Narration script with timecodes, subtitles (`.srt`) and the script source |
| `demo/storyboard/` | Shot-by-shot storyboard and the screen-to-source mapping |
| `demo/screenshots/` | The real application screenshots used as evidence |

A separate static **Jury Companion** site accompanies the submission; its code is not part of this repository.

## Documentation

| Document | Purpose |
|---|---|
| [docs/technical/DEPLOYMENT.md](docs/technical/DEPLOYMENT.md) | Hosting guide (Vercel + Railway), environment variables, optional Telegram alerts |
| [docs/technical/TESTING_VALIDATION.md](docs/technical/TESTING_VALIDATION.md) | Test coverage, results and end-to-end validation record |
| [docs/jury/ANTARIKSHA_RAKSHA_Jury_Readiness.md](docs/jury/ANTARIKSHA_RAKSHA_Jury_Readiness.md) | Jury question-and-answer preparation |
| [docs/jury/CASE_STUDIES.md](docs/jury/CASE_STUDIES.md) | Worked case studies |
| [docs/architecture/BLUEPRINT.md](docs/architecture/BLUEPRINT.md) | Original pre-implementation blueprint (historical; this README and the code are authoritative) |
| [submission/supporting/CONCEPT_NOTE.md](submission/supporting/CONCEPT_NOTE.md) | Concept note |
| [submission/supporting/ANTARIKSHA_RAKSHA_Pitch.pptx](submission/supporting/ANTARIKSHA_RAKSHA_Pitch.pptx) | Pitch deck |

Earth imagery: NASA Blue Marble (public domain); see `frontend/public/textures/ATTRIBUTION.md`. Orbital data:
CelesTrak public GP data.
