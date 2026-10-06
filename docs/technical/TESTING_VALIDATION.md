# Testing & Validation — ANTARIKSHA-RAKSHA

This document describes what the automated tests cover, which resilience behaviours the code actually implements,
and what has *not* been validated. It is a research/demo prototype, so nothing here amounts to operational
verification or independent scientific validation.

---

## 1. Automated tests

**Runner:**

```powershell
backend\venv\Scripts\python.exe -m pytest backend\tests -v
```

Frontend: `npm test` (Node's built-in test runner) and `npm run build`, run in `frontend/`.

**Recorded validation result for this submission snapshot (2026-10-06):** 652 backend tests passed, 1 backend test
skipped; 163/163 frontend tests passed; frontend production build passed. Total automated tests passing: **815**, with
1 skipped. The skipped test (`test_ingest_resolution.py`) requires a local TLE cache, which is not shipped in this
repository.

All suites use seeded or synthetic data (synthetic but valid TLEs, synthetic position tracks). None of them needs
network access or Ollama.

### Coverage by area

| Area | Suite(s) | What is checked |
|---|---|---|
| Propagation | `test_propagate` | SGP4 output shape on the shared time grid; LEO altitudes in a plausible range |
| Screening | `test_conjunction` | Close approaches are detected and distant objects are not; scope is limited to protected-asset pairs (no debris-to-debris) |
| TCA refinement and TCA/Pc relationship | `test_conjunction`, `test_risk_score` | TCA is refined between grid samples; miss distance, relative velocity and the state used for Pc (positions and relative-velocity vector) are all taken at the **same refined TCA instant** (regression test for the earlier midpoint-sampling bug) |
| Pc and risk | `test_risk_score` | Analytic encounter-plane Pc matches the non-central chi-square CDF (rel. 1e-8) and a 2-million-sample projected Monte Carlo reference (within 4 standard errors); **every tier (Critical / High / Medium / Low) is reached with continuous non-zero values** under the unchanged thresholds; repeated scoring is bit-identical; the along-velocity component does not change Pc; zero relative velocity uses the labelled 3D fallback; before/after check: a High-band geometry that 5,000-sample Monte Carlo reports as 0 is resolved; tier thresholds; criticality changes priority but not tier |
| Proximity watch | `test_threat` | A sustained dwell (≥ 10 consecutive samples within 25 km; dwell reported as elapsed time (N − 1) × 60 s) is flagged; a brief fly-through is not; approach geometry is classified |
| Provenance | `test_provenance` | The provenance payload separates deterministic, probabilistic, AI-generated and human-decided parts; demo events are labelled |
| Demo integrity | `test_demo_seed` | Collision and proximity scenarios go through the real pipeline; the demo is stored as a separate override; **real catalog rows are not modified**; a later normal run does not report the demo event as real; events are flagged `is_demo` |
| AI fallback and review status | `test_brief_agent` | Deterministic fallback when Ollama is unreachable (`generated_by = fallback_template`); `review_status` values (`consistent` / `flagged` / `skipped` / `not_applicable`); the deterministic fact check flags wrong numbers, wrong risk tier, invented recommendations and intent language, and passes valid briefs with rounding and the required closing statement; verdict parsing handles `**APPROVED**`-style formatting; unparseable reviewer output is never treated as approved |
| Brief-pending UI state | `frontend/src/briefState.test.js` | `generated_by = null` shows "BRIEF PENDING" (never "LLM unavailable"); polling only while pending; real fallback still labelled deterministic |
| Trust and transparency blocks | `frontend/src/eventTrust.test.js` | Per-event AI DID / DID NOT and AI status (available / unavailable / check failed / not completed / pending); assessment basis from stored values with no confidence score; deterministic analyst summary; chronological timeline |
| Protected-asset registry and new objects | `test_catalog_view`, `frontend/src/catalogView.test.js` | Registry lists only configured Group A entries, kept separate from the catalog summary; first population is the baseline; later objects are new to the local catalog, stay listed when inactive (never "retired"); review is an audit record that changes nothing else and needs the `review_objects` permission (OPERATOR+); the reviewer's account and role are recorded |
| Event evolution and refresh delta | `test_event_history`, `frontend/src/eventEvolution.test.js`, `screeningDelta.test.js` | Observations persist across refreshes; prototype correlation (pair + class + nearest TCA, 20 min / 6 h, one-to-one, deterministic tie-breaks, ambiguity flag); demo and real runs never correlate; first run reports no previous run; new / increased / decreased / unchanged / not-present buckets; trends from stored values only |
| Risk drivers and evidence trace | `test_event_history` (provenance risk block), `frontend/src/riskDrivers.test.js` | Collision indicator, asset criticality (priority ordering only) and priority score shown from backend values; proximity states Pc not computed; ten-step evidence chain |
| Native OMM | `test_native_omm`, `test_omm` | 6-digit catalog IDs preserved; SGP4 initialised from OMM directly (no generated TLE); TLE vs OMM agree < 1e-5 km; TLE path bit-identical to the previous constructor; invalid OMM rejected with a reason; source format in provenance |
| Named-object resolution and ingest safety | `test_ingest_resolution` | 0 / 1 / ambiguous / exact-match cases, never an arbitrary pick; unresolved entries keep their previous object; refresh refused (409) below 50% Group A resolution or above 50% deactivation, catalog unchanged |
| Prototype RBAC | `test_rbac`, `test_auth`, `frontend/src/auth.test.js`, `api.test.js`, `governance.test.js`, `i18n/authI18n.test.js` | No default credentials; PBKDF2 hashes never returned; stored hashes below the PBKDF2 iteration minimum or with malformed base64 are rejected; login / logout / `me`; every protected route refuses a missing session (401) and a missing permission (403) for each of VIEWER / OPERATOR / ASSET_MANAGER / ADMINISTRATOR; CSRF header required on state-changing requests; deactivation and role changes apply on the next request; the last active ADMINISTRATOR cannot be demoted or deactivated; one-time env bootstrap and CLI lifecycle (audited as `cli`); decisions and object reviews record actor user and role, legacy rows labelled; `governance_audit` refuses UPDATE / DELETE; optional API-key gate still works; frontend sends the CSRF header and gates controls by permission; auth/admin strings present in all 11 languages |
| Protected-asset registry management | `test_protected_assets`, `frontend/src/governance.test.js` | Seeded once from `working_set.json` Group A (not reseeded after retirement); legacy DB migrates without data loss; add / edit (criticality, note, exact match) / suspend / resume / retire with validation and case-insensitive duplicate check; retired is terminal; refusal to leave zero active assets; non-ASSET_MANAGER roles get 403; every change audited; changes never touch existing events or observations; ingest reads only active entries, so changes apply at the next refresh; a missing/corrupt INITIAL seed source (or one without a valid entry) is refused and recorded as unseeded, while an already-seeded registry ignores the source |
| Protected-asset governance (Group A) | `test_protected_asset_governance`, `test_conjunction`, `test_threat`, `test_dwell`, `frontend/src/catalogView.test.js` | Group A / protected status comes only from active registry entries as resolved by the latest successful ingest (real ingest path, network stubbed): ordinary satellite-typed objects, debris and foreign satellites are not protected; suspended / retired entries leave Group A at the next successful refresh and a reactivated entry returns; a registry edit alone or a failed refresh does not change the current scope; unresolved entries keep their previous object; registry edits do not rewrite existing events; `new_object_review`, `/api/objects` `protected` and the proximity watch use the same source; review acknowledgement changes nothing |
| Jury attack cases | `test_jury_attack_cases` | One end-to-end test per likely review question, on the real code paths: (A) a satellite-typed object is never protected by type, and suspension applies only at the next successful refresh; (B) a failed refresh changes neither the catalogue nor protected scope; (C) stale (> 30 days) and unparseable TLEs are excluded from screening and display; (D) AI disabled (no network call), unreachable or sensational twice gives the deterministic template; (E) wrong numbers in AI text are flagged, and stored TCA / miss distance / Pc / tier are computed before and unchanged by a hostile AI brief; (F) decisions need an authenticated principal with `decide`, record actor and role from the session, and no route is a command / uplink / manoeuvre path; (G) the demo asset is always a registry-resolved protected asset (never an ordinary satellite, never a suspended or retired one after refresh; a clear 409 error when none exists) and demo seeding changes neither catalogue nor governance |
| Screening-horizon provenance | `test_screening_horizon`, `frontend/src/horizonToneGuard.test.js` | Status payload reports the window and step persisted with the latest screening run (72 h / 60 s in the current configuration); each event's assessment basis uses its own run's horizon and step; legacy events without stored values fall back to configuration and are labelled as such |
| AI tone guard | `test_tone_guard`, `frontend/src/horizonToneGuard.test.js` | Whole-word detection of unsupported sensational certainty (catastrophic, disastrous, devastating, inevitable, guaranteed, unavoidable, certain, imminent and variants); "uncertain" / "uncertainty" and legitimate tier words never match; one redraft; a second failure yields the deterministic template with `review_status = tone_guard_fallback`, shown as "DETERMINISTIC TEMPLATE (AI draft failed the tone guard)" |
| Configurable screening horizon | `test_settings_horizon` | Default 72 h; only 24/48/72/96/120 (integers) accepted; ADMINISTRATOR-only (others 403); every change audited; a change applies to the next screening run and each run keeps the horizon it used |
| Automatic refresh scheduler | `test_scheduler`, `test_settings_horizon`, `test_ai_and_deploy_config` | Modes internal/external/off; scheduled-refresh endpoint secret (401 wrong, 503 unset, 202 accepted / 200 skipped); too-soon (< 110 min), demo-active (< 60 min) and overlap guards; last-known-good preserved on failure; status fields |
| Globe configuration | `frontend/src/globeConfig.test.js` | India default view at regional altitude; locally served NASA Blue Marble texture path (no remote URL); presentation-only helpers |
| Vercel same-origin API proxy | `frontend/src/deploy.test.js` | `api/proxy.js` (reached through the `/api/*` rewrite) forwards method, path, query, body and only allowlisted headers (cookie, content-type, x-antariksha-client, x-api-key, origin, user-agent); the browser `Authorization` header is never forwarded; every `Set-Cookie` header is returned individually; `/api/internal/*` (incl. encoded, case and double-slash variants) → 404 without contacting the backend; missing `BACKEND_URL` → 503 `backend_not_configured`; upstream timeout → 504, unreachable → 502 |
| Vercel Cron relay | `frontend/src/deploy.test.js` | `api/cron/refresh.js` rejects a missing or wrong `CRON_SECRET` bearer (401, constant-time compare) without calling the backend; forwards to `/api/internal/scheduled-refresh` with `Authorization: Bearer <SCHEDULER_SECRET>` and relays `202 accepted` / `200 skipped`; backend 401 → 502 `backend_rejected_scheduler_secret` with no secret in the body; fails closed (500/503) when `CRON_SECRET`, `SCHEDULER_SECRET` or `BACKEND_URL` is unset |
| Deployment configuration | `frontend/src/deploy.test.js` | `frontend/vercel.json` is valid JSON with known keys only: framework `vite`, `npm run build`, output `dist`, function `maxDuration`, no `crons` entry (Hobby-safe; the backend's internal scheduler provides the 2-hour cadence), the `/api/*` → `api/proxy.js` rewrite, an SPA rewrite that does not capture `/api/*`, and no secrets; `.env.example` templates hold only empty values or obvious placeholders for secret-like keys and document the deployment variables |
| Status, freshness, decision audit | db / API / status suites, where present | Mode labelling (LIVE / CACHED / STALE / DEMO / NOT SCREENED); `screening_runs` provenance; `decision_log` keeps event ID, `is_demo`, screening run ID and TCA across screening runs |

---

## 2. Resilience behaviours implemented in code

### 2.1 Ollama unavailable

- `backend/brief_agent.py` calls Ollama with a 30 s timeout (`OLLAMA_TIMEOUT_SECONDS`).
- If the call fails or times out, the brief comes from a deterministic template, with
  `generated_by = fallback_template`.
- The UI labels this brief as deterministic, and `/api/health` reports `ollama_reachable: false`.
- The rest of the pipeline is unaffected, because the AI does not compute any number.

### 2.2 CelesTrak unreachable

- `backend/ingest.py::_fetch_with_cache` writes every successful response to `data/tle_cache/`.
- If a fetch fails, it loads the cached copy for that query and marks the run as cache-based.
- If no cache exists, it raises an explicit error that asks for one online run.
- The mode chip then shows **CACHED DATA**.

### 2.3 Stale TLEs

- `backend/propagate.py` excludes any object whose TLE epoch is more than 30 days old (`MAX_TLE_AGE_DAYS`) and logs
  the exclusion. This applies to real and demo screening alike.
- In the event detail, a data-confidence indicator shows **HIGH** when both objects' TLE ages are ≤ 3 days and
  **REDUCED** otherwise. This is a display heuristic, not a validated accuracy metric.

### 2.4 Concurrent screening runs

- `backend/pipeline.py` serialises screening runs with `_PIPELINE_LOCK`, a process-local `threading.Lock`.
- If a second `/api/refresh` arrives during a run, it waits for the first to finish.
- The lock does not coordinate across separate processes.

### 2.5 Database

- SQLite is used with default settings. **WAL mode is not enabled** in `backend/db.py`.
- Concurrent writers from separate processes are not a supported configuration.
- `conjunction_events` and `mission_briefs` are rebuilt on every screening run.
- `decision_log`, `screening_runs`, `ingest_runs`, `refresh_attempts`, `event_observations` (event history),
  `object_reviews`, `app_settings`, `users`, `sessions`, `governance_audit` and `protected_assets` are kept across runs.
- `governance_audit` is append-only at the database level (triggers), but there is no tamper protection against
  someone with file access to the SQLite database.

---

## 3. Historical end-to-end validation — 2026-10-02

Historical observed validation (not the latest automated test result). Run on 2026-10-02 through the real HTTP API (`uvicorn backend.main:app`), with live CelesTrak access, against
the developer's existing `data/antariksha.db`. The numbers below are what one machine produced in one run. They are
not benchmarks.

| Step | Observed result |
|---|---|
| Status before refresh | `not_screened` / **NOT SCREENED**. Warnings: no screening run recorded, no CelesTrak refresh recorded, and all 103 stored objects excluded because their TLE epochs were older than 30 days. The UI did **not** claim LIVE. |
| `POST /api/refresh` (live) | 103 objects ingested (11 Indian assets, 80 debris, 12 other active satellites), `using_cache: false`, `elapsed_seconds` 14.06 (mostly network fetch). Propagation 0.18 s, screening 0.16 s. |
| Repeated live refreshes | Full refresh took 11–35 s across 6 refreshes on 2026-10-02, observed on the demo laptop, network-dependent (`elapsed_seconds`). Not a benchmark. |
| Real screening result | 1,012 asset/object pairs screened, 534 candidate close-approach episodes, all rejected at the interpolation stage. **0 collision-risk events, 0 proximity events.** No real event was fabricated. |
| Status after refresh | `live` / **LIVE DATA**, TLE epoch age 0.2–12.27 d (avg 0.63 d), 0 objects excluded, no warnings. |
| Collision demo | Crossing geometry (1.5° plane tilt, encounter +6.5 h). Screened TCA 2026-10-02T18:59:59.995Z. Miss distance 19.77 m and relative velocity 0.1998 km/s at TCA. Pc at the time was 6.0e-4 from the former 5,000-sample Monte Carlo, tier **Critical**. After the switch to the analytic indicator, a rerun the same day (TCA 21:00:00.014Z, miss 20.79 m, 0.1998 km/s, σ 0.1135 km) gave **Pc 7.67e-3** (deterministic, no sampling), tier **Critical**. An immediate second rerun gave 7.673e-3, which differs only because σ grows with time-to-TCA. `is_demo = 1`. `real_catalog_modified: false`. Provenance mode `DEMO`, with the adjusted object marked `demo_adjusted` and derived from NORAD 44804. Status `demo` / **DEMO MODE**. |
| AI brief | AI brief during this historical local validation run: generated by the local Ollama `llama3.2:3b` model. In this run, the second-pass consistency review flagged the brief: the reviewer was comparing rounded figures against raw floats and lacked the drafter's criticality input. This was fixed afterwards: the reviewer now receives exactly the drafter's data block. The review result is shown to the operator either way. |
| Human decision | Dismiss with reason → `status: dismissed`. `GET /api/events/{id}/decisions` returned the entry with `is_demo = 1` and `event_id`. It also appears in the CSV export. |
| Proximity demo | `proximity_watch` event, minimum separation 9.975 km, tier High, `is_demo = 1`. The same co-orbital pair also yields one `collision_risk` encounter at 9.975 km, below the 10 km screening threshold. With the analytic indicator its Pc is a continuous 6.1e-177 (formerly a quantised 0 from Monte Carlo), tier Low. |
| Invalid demo mode | `POST /api/demo/seed?mode=bogus` → HTTP 400. |
| Return to live | `POST /api/refresh` → `using_cache: false`, 0 events, status back to `live` / **LIVE DATA**, demo override cleared. |
| Health | `ollama_reachable: true`, `object_count: 103`, `using_cache: false`. |

This run used a local Ollama model. The hosted Railway validation environment operates with AI disabled
(`ANTARIKSHA_AI_MODE=disabled`), so it always shows deterministic template briefs.

---

## 4. Known limits (acknowledged)

1. **The Pc model is simplified:**
   - It assumes an isotropic Gaussian position error with `sigma_km = 0.1 + 0.05 × (hours to TCA / 24)`. It does not
     use the ellipsoidal, along-track-dominated covariance found in real conjunction assessment.
   - It uses the short-encounter (straight-line) assumption, which is weak for slow or co-orbital encounters.
   - The bias relative to a covariance-based Pc has **not been quantified**.
2. **Pc is an indicator, not an operational Pc:**
   - The deterministic analytic encounter-plane calculation is mathematically consistent with its stated simplified
     uncertainty model and was cross-checked against the corresponding non-central chi-square CDF and projected Monte
     Carlo reference.
   - The uncertainty model is simplified and is not an operational covariance model; its bias against a
     covariance-based operational Pc has not been quantified.
   - The model itself (assumed, isotropic sigma) is the limitation. The earlier Monte Carlo resolution problem
     (0 or >= 2e-4 only) no longer applies.
3. **The Δv figure is illustrative:**
   - It is about 0.046 m/s (1 km over a 6 h lead time), the same for every event, and ignores geometry, relative
     velocity, burn location, mass, thrust, fuel and dynamics.
   - Its error against a real maneuver solution has **not been quantified**. It is not a maneuver recommendation.
4. **TLE/SGP4 accuracy:**
   - Public TLEs carry no covariance, and their error grows with propagation time.
   - Their accuracy has not been validated against precise ephemerides here.
5. **No independent cross-check:** predictions have not been compared against an independent service such as
   CelesTrak SOCRATES or against CDMs.
6. **The AI review is not independent:** the consistency review uses the same local model as the drafter, backed by a deterministic fact check that cannot judge vague qualitative wording.
7. **No autonomous execution:** every decision is a recorded human decision, and no command path exists. This is
   intentional.
8. **Access control is prototype RBAC:** local accounts, backend-enforced roles, session cookie and CSRF header.
   There is no MFA, SSO or enterprise identity integration, and no account lockout or login rate limiting.
   Production deployment would additionally require enterprise identity integration, MFA, stronger secret
   management and hardened identity/audit infrastructure.
9. **Hosted deployment is split, and only partly verifiable offline:** the frontend and two small functions are
   designed for Vercel; the backend must run as one process on a host with a persistent disk (SQLite). The proxy and
   cron relay are unit-tested with a mocked `fetch`; an actual Vercel deployment, Vercel Cron delivery and the plan
   limit (Hobby: once per day, per Vercel documentation) have **not** been exercised in these tests. See
   `docs/technical/DEPLOYMENT.md`.

---

## 5. Requirements beyond the current prototype (none of these are implemented at operational level)

- [ ] Authoritative orbital data, precise ephemerides, covariance, and CDM ingestion
- [ ] Higher-fidelity propagation, and validated Pc (for example 2D encounter-plane) with covariance realism
- [ ] Independent validation against reference cases and independent services
- [ ] Maneuver planning and Δv optimisation integrated with flight dynamics, plus execution controls
- [ ] Enterprise identity integration (SSO) and MFA on top of the prototype RBAC; stronger secrets management, encryption, network segmentation
- [ ] Tamper-evident, access-controlled audit storage, with backup and retention
- [ ] Multi-user database (for example PostgreSQL) and cross-process job coordination
- [ ] High availability, monitoring and metrics, error tracking, and alerting with escalation
- [ ] Defined organisational approval workflows and responsibilities (the prototype has four technical roles only)
- [ ] Formal test and verification plan, and regulatory and mission-operations integration

---

## 6. Continuous integration

**Not yet set up.** The repository has no CI workflow (no `.github/` directory) and no pre-commit hook. Tests are run
manually with the command in §1.

---

## 7. Demo reliability note

Real close approaches inside the 10 km threshold are uncommon in any 72-hour window for this small working set. The
controlled demo (`POST /api/demo/seed?mode=collision|proximity`, or `scripts/seed_demo_event.py`) works like this:

- It starts from ingested real objects. Collision mode builds a crossing encounter: a 1.5° plane tilt, about 20 m
  designed miss distance, about 0.2 km/s relative velocity, 6–7 h ahead. A test checks that screening recovers
  the designed TCA, miss distance and relative velocity. Proximity mode uses about 10 km along-track co-orbit.
- The aligned elements are stored as a **separate override**. Real catalog rows are never modified.
- The unmodified pipeline runs with unchanged thresholds and no injected Pc. For the same inputs and TCA, the
  analytic Pc calculation is deterministic. Reruns can differ if the propagated TCA or time-to-TCA changes, because
  the simplified sigma model depends on time-to-TCA.
- Events are flagged `is_demo` and labelled **DEMO SCENARIO** / **DEMO MODE**, and screening runs record the demo
  scenario in `screening_runs`.

Any demo must be narrated as a controlled demonstration. It is not a replay of a historical event.

## Historical performance observations (local validation run, 3 Oct 2026)

Single observations with Ollama `llama3.2:3b` running locally and live CelesTrak access; network-dependent, not a
benchmark or guarantee. Observed on the demo laptop under the tested configuration; not a production SLA.

| Operation | Observed |
|---|---|
| Live refresh (ingest + screening + run record, 0 real events) | 6.7–7.1 s (three refreshes) |
| Propagation + screening inside a refresh | 0.27 s + 0.28 s |
| Demo seed (one event incl. local LLM brief + consistency check) | 2.9–3.5 s; 9.9 s on the first call after a backend restart |
| Open an event (`/api/events/{id}`) | < 0.01 s API; drawer content visible immediately |
| Event Evolution load | < 0.01 s |
| Screening delta load | < 0.01 s |

The demo response waits for the brief: if Ollama is slow or unavailable, the brief falls back to the labelled
deterministic template after the configured timeout. Run one refresh before presenting so the model is loaded.

