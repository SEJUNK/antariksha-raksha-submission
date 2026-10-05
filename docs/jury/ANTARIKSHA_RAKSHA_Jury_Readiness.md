# ANTARIKSHA-RAKSHA: Jury Readiness Q&A

Research/demo Space Domain Awareness decision-support prototype focused on transparent conjunction analysis, local AI explanation, human decision support and persistent provenance (defence-relevant Space Domain Awareness and conjunction-assessment decision support; not a weapon or defence system). It is **not** an operational collision-warning service, it is **not** an ISRO/IS4OM/NETRA/DRDO system, and it has **no command path to any spacecraft**. Every answer below describes the code as it is in the repository (`backend/*.py`, `frontend/src`, `README.md`, `docs/technical/TESTING_VALIDATION.md`).

## Presentation decision

| Role | File | Why |
|---|---|---|
| **Canonical jury deck** | `submission/supporting/ANTARIKSHA_RAKSHA_Pitch.pptx` (12 slides) | Fits an 8–10 min slot (~45 s per slide). Follows the standard jury arc: problem → gap → solution → proof (live console screenshot) → differentiation → impact → theme → roadmap → collaborations → ask. Consistent visual system with slide numbering, speaker notes and cited sources. Has an explicit "Ask" close. |
| **Backup / preparation deck** | Preparation deck (20 slides; not included in this repository) | Deeper material for Q&A: architecture diagram, event lifecycle, engineering patterns, responsible-AI guardrails. Too long for the slot (~27 s per slide). It repeats some content (the metrics on slides 15/16; one-sentence pitch vs. how-it-works) and has no dedicated "ask" slide. |

## Key numbers (dated; single machine, not benchmarks)

| Item | Value | Source |
|---|---|---|
| Objects ingested (live CelesTrak, 2 Oct 2026) | 103 = 11 Indian assets + 80 debris + 12 other active satellites | E2E run, `docs/technical/TESTING_VALIDATION.md` §3 |
| Pairs screened / candidate episodes / real events | 1,012 / 534 / **0** (no real event fabricated) | same |
| Propagate + screen | < 1 s (≈ 0.18 s + 0.16 s), single measured runs on the demo laptop | `pipeline.py` timing (`propagation_seconds`, `screening_seconds`) |
| Full refresh (`POST /api/refresh`) | 11–35 s across 6 refreshes on 2 Oct 2026, network-dependent | `main.py::refresh` (`elapsed_seconds`) |
| Window / step / thresholds | 72 h, 60 s (4,320 samples per object); 10 km screen; 25 km / 10 min proximity dwell; TLEs > 30 d excluded | `config.py`, `propagate.py::MAX_TLE_AGE_DAYS` |
| Pc model | Analytic encounter-plane indicator, σ = 0.1 km + 0.05 km/day of lead time (isotropic, assumed), 20 m hard-body radius | `risk_score.py`, `config.py` |
| Tiers | Critical > 1e-4, High > 1e-5, Medium > 1e-6, else Low | `risk_score.py::risk_tier_from_pc` |
| Illustrative Δv | ≈ 0.046 m/s (1 km over 6 h), same for every event | `maneuver.py::estimate_delta_v` |
| LLM | Ollama `llama3.2:3b`, local, 30 s timeout | `config.py` |
| Automated tests | 613 backend (pytest, 1 skipped) + 162 frontend (node --test) = 775 passing, 5 Oct 2026 | `backend/tests`, `frontend/src/**/*.test.js` |

---

## A. Problem and context

**1. What problem does this solve?**
Indian satellites (NavIC, GSAT, Cartosat/RISAT/EOS) share orbits with debris and other active spacecraft. An operator needs to know which close approaches matter, why, and what was decided about each. ANTARIKSHA-RAKSHA is a locally-run prototype that screens a working set of protected Indian assets for close approaches and sustained proximity, estimates a simplified collision-probability indicator, ranks events by asset criticality, drafts a plain-language brief with a local LLM, and records a human decision in a persistent audit trail. It is decision support only. It does not issue operational warnings and does not command anything.

**2. Why is SSA important?**
About 40,000 objects are tracked (ESA Space Environment Report 2025), and a single collision can create debris that threatens whole orbital regimes. Debris from the 2007 Fengyun-1C test and the 2009 Iridium-Cosmos collision is still in orbit, and the prototype screens against samples of both (`data/working_set.json`). SSA turns orbital data into actionable awareness: what will come close, when, and how likely a collision is. Without it, operators cannot protect assets or keep LEO usable.

**37. How does this relate to India's space ecosystem?**
India already has SSA institutions and capability, including ISRO's IS4OM and the NETRA programme. This prototype is a **complementary, operator-facing decision-support layer**. It is not a replacement and it is not integrated with them. Today it uses only public CelesTrak GP data. A future deployment *could* consume authoritative or indigenous data (IS4OM/NETRA, commercial Indian SSA providers) if access were granted. All partnerships on the collaborations slide are marked *proposed*, not agreed.

**38. How does it contribute to the broader Viksit Bharat 2047 vision?**
Officially stated goals give context. ISRO's Debris-Free Space Missions (DFSM) initiative aims for "debris-free space missions by all Indian space actors, governmental and non-governmental by 2030", with IS4OM as the nodal point ([ISRO, DFSM](https://www.isro.gov.in/Debris_Free_Space_Missions.html)). ISRO also describes Space Vision 2047 as including the Bharatiya Antariksha Station by 2035 and Indians landing on the Moon by 2040 ([ISRO, Union Cabinet approval note](https://www.isro.gov.in/UnionCabinetApprovesIndiasMission.html)). As India's orbital presence grows, collision avoidance and orbital sustainability matter more. This prototype **demonstrates** that transparent, human-controlled SSA decision support can run locally on commodity hardware with open-source components, and it **can contribute** to that direction as a research and training reference. It is not endorsed by, or part of, any government programme.

**39. Is this an official ISRO / IS4OM / DRDO system?**
No. It is an independent research/demo prototype built for the Sovereign Technology for India challenge (Track 03). It has no integration, agreement or data-sharing with ISRO, IS4OM, NETRA, DRDO, IN-SPACe or any other organization (README "Key facts for reviewers").

## B. How it works technically

**3. What happens technically when an event is detected?**
`pipeline.py::run_screening_and_briefs` runs in this order: SGP4 propagation (`propagate.propagate_all`) → screening with refined TCA (`conjunction.find_close_approaches`) plus proximity dwell detection (`threat.detect_proximity_operations`) → Pc indicator and tier (`risk_score.score_collision_event`) → criticality-weighted priority (`risk_score.priority_score`) → illustrative Δv text (`maneuver.maneuver_for_event`) → LLM brief plus consistency review (`brief_agent.generate_and_review_brief`) → stored in SQLite (`conjunction_events`, `mission_briefs`), with a `screening_runs` row recording provenance. For Critical/High events, a best-effort Telegram alert is sent if one is configured (`notify.notify_event`). The event then appears in the UI feed, where an operator approves (or acknowledges, for proximity) or dismisses it with a reason. That decision goes to the persistent `decision_log` (`main.py::_log_decision`).

**4. How does SGP4 work in this project?**
SGP4 is the standard analytical propagator for TLE/GP element sets. The project uses it through skyfield's `EarthSatellite` (`propagate.py`). Every object is propagated on one shared time grid (72 h, 60 s step, GCRS frame in km), so positions are directly comparable. Objects with TLE epochs older than 30 days are skipped and logged. For sub-grid refinement, `propagate.make_state_functions` returns per-object functions that re-run SGP4 at an arbitrary instant and return position and velocity. Skyfield's built-in timescale (`load.timescale(builtin=True)`) means propagation needs no network.

**5. How is TCA calculated?**
There are three stages (`conjunction.py::find_close_approaches`). (1) Coarse: for each Indian asset vs. every other object, the grid samples with separation below `10 km + |v_rel|·30 s + 1 km` form "close-approach episodes", and the minimum sample of each episode becomes a candidate (`_encounter_candidates`). (2) Prefilter: `refine_tca` runs on a 3-point quadratic interpolation of the grid, and only candidates with an interpolated miss below 10.5 km continue. (3) Fine: `refine_tca` runs a bounded scalar minimisation (`scipy minimize_scalar`, ±1 grid step, tolerance about 60 µs) of |r_a(t) − r_b(t)|, re-evaluating SGP4 for both objects at each trial time. The grid point is also evaluated, so the refined miss can never exceed the grid miss.

**6. Why is TCA refined rather than using the coarse screening result?**
At LEO crossing speeds (up to ~15 km/s), objects move hundreds of km between 60 s samples. The true closest approach usually falls between samples, and the grid minimum can overstate the miss by a large margin. An earlier version kept a pair only if a raw grid sample fell inside 10 km, so most fast crossings were silently missed. The same version also sampled Pc at the window midpoint, not at TCA. The fix produces **one authoritative refined TCA**: the 10 km threshold is applied to the refined miss, and miss distance, relative velocity and the Pc state all refer to that same instant. Regression tests in `test_conjunction` / `test_risk_score` cover this.

**7. How is miss distance calculated?**
Miss distance is the Euclidean norm |r_a − r_b| of the two SGP4 GCRS position vectors at the refined TCA (`conjunction.py::refine_tca`). It is reported in km. The grid miss is also stored (`grid_miss_distance_km`) for transparency. Accuracy is limited by TLE/SGP4 error, typically hundreds of metres to kilometres and growing with propagation time (README Limitations).

**8. How is relative velocity calculated?**
At the refined TCA, SGP4 returns each object's velocity vector. The relative velocity is v_a − v_b; its magnitude is reported in km/s, and the vector is kept to define the encounter plane for Pc (`refine_tca`, field `rel_velocity_vec_km_s`). During coarse screening only, relative speed is estimated by finite differences on the grid (`np.gradient`) to size the candidate padding.

## C. Risk and uncertainty

**9. What exactly is Pc here?**
It is a **simplified analytic encounter-plane collision-probability indicator** (`risk_score.py::analytic_collision_probability`). The relative position at the refined TCA is projected onto the plane perpendicular to the relative velocity. Each object gets an assumed isotropic position sigma, so the relative uncertainty is N(0, 2σ² I₂). Pc = P(|projected position| < 20 m hard-body radius), the Rician CDF evaluated by adaptive quadrature in an overflow-safe form with `i0e`. It is deterministic: the same inputs always give the same value. It is cross-checked in tests against the non-central chi-square CDF (relative tolerance 1e-8) and a 2-million-sample projected Monte Carlo.

**10. Why is this NOT an operational covariance-based Pc?**
Operational Pc uses realistic, usually along-track-elongated covariance from orbit determination or CDMs. Public TLEs carry no covariance, and CDMs are not integrated. The sigma here is **assumed** (`sigma_km_for`), isotropic, and grows linearly with lead time. Its bias relative to a covariance-based Pc has not been quantified, and it has not been validated against any authoritative Pc. The formula is exact for its stated model; the model is the limitation. It is therefore labelled an indicator everywhere: UI, provenance panel, briefs and decks.

**11. Why an analytic indicator instead of Monte Carlo?**
The earlier production estimator was a 5,000-sample Monte Carlo. Its resolution was 1/5,000, so it could only report 0 or ≥ 2e-4: every non-zero result was Critical, and High/Medium were unreachable. The analytic formula is continuous and deterministic (e.g. 5e-5 High or 5e-6 Medium are resolved, and reruns are bit-identical) and costs negligible time. Monte Carlo is kept only as a test reference (`compute_collision_probability`), where a 2M-sample projected run agrees with the analytic value within 4 standard errors.

**12. What uncertainty assumptions are made?**
(a) Each object's position error is isotropic Gaussian with σ = 0.1 km + 0.05 km × (hours to TCA / 24) (`config.py`: `PC_SIGMA_KM_BASE`, `PC_SIGMA_KM_GROWTH_PER_DAY`). (b) The two objects' errors are independent, so the relative sigma is √2·σ. (c) Short-encounter, straight-line relative motion, so uncertainty along v_rel integrates out. (d) Combined hard-body radius of 20 m. (e) No covariance comes from the data. All of this is shown in the provenance panel (`provenance.py`).

**13. What happens when relative velocity is extremely low?**
Below 1 mm/s (`ENCOUNTER_PLANE_MIN_REL_SPEED_KM_S = 1e-6` km/s) no encounter plane is defined. The code then switches to a labelled 3D fallback: P(|X| < R) with X ~ N(r, 2σ² I₃), the non-central chi-square CDF with 3 degrees of freedom (`_sphere_probability_3d`). More generally, the short-encounter assumption is weak for slow or co-orbital encounters, and the README says so. Screening groups a slow drift into one long episode rather than hundreds of noise minima, and sustained co-orbital presence is handled separately by the 25 km / 10-minute **proximity watch** (`threat.py`), which computes no Pc.

**14. How are risk levels determined?**
Collision events: Critical if Pc > 1e-4, High if > 1e-5, Medium if > 1e-6, otherwise Low (`risk_tier_from_pc`). Priority (sort order only) = max(Pc, 1e-9) × criticality multiplier (Tier 1 = 3, Tier 2 = 2, Tier 3 = 1) × 10⁶. Criticality never changes the tier. Proximity events: High if dwell > 60 min **and** minimum distance < 10 km, else Medium (`threat.risk_tier_for_proximity`). Their priority sits in a fixed band below Medium collision events unless dwell exceeds 180 min.

## D. The AI component

**15. What does the LLM do?**
A local Ollama model (`llama3.2:3b`) drafts a 3–4 sentence plain-language brief from numbers the backend has already computed: object names, criticality, TCA, miss distance, relative velocity, Pc with odds, tier and illustrative Δv (`brief_agent.py::build_prompt`). A deterministic fact check and a second pass of the same model then check the draft for consistency with those facts (Q18). Prompts include up to the 3 most recent operator rejection reasons as in-context feedback.

**16. What does the LLM NOT do?**
It does not compute orbits, TCA, miss distance, relative velocity, Pc, tier, priority or Δv. It does not make or execute decisions, and it cannot command anything. The maneuver/Δv text shown with each brief always comes from `maneuver.py`, never from the LLM. Prompts forbid inventing numbers, proposing burns, and speculating about intent (proximity prompts also forbid accusatory language). The provenance panel states that AI plays no part in the calculations.

**17. Why an LLM instead of showing raw output?**
Raw output (TCA, km, km/s, 7.67e-3) needs expert interpretation. A short brief puts the numbers in context and makes triage faster, especially for non-specialist reviewers. The raw numbers are still shown next to the brief, computed by deterministic code, so the brief is an explanation layer, not a source of truth. Each brief carries a provenance chip (AI-drafted with review status, or deterministic template), and the human makes the decision.

**18. How does the consistency reviewer work?**
Every LLM draft is checked in two layers (`brief_agent.py::check_brief`). **(1) Deterministic fact check** (`fact_check_brief`, no AI): every number in the brief must match a computed fact (with rounding and unit conversions, e.g. 0.0198 km ≈ 20 m, Pc as % or "1 in N"), times must match the TCA, a stated risk tier must equal the computed tier, and invented recommendations ("a maneuver is likely necessary", "no immediate action is required", burn directions/timing) or intent language (hostile, suspicious, deliberate …) are flagged. **(2) LLM consistency review** (`review_brief`): the same local model gets the drafter's exact facts block, the expected structure and the required closing human-decision statement (which must not be flagged), and is told that rounding, formatting and omissions are not contradictions. Its verdict parser tolerates markdown such as `**APPROVED**`; an unparseable reply is never treated as approved. **Combination:** only the deterministic fact check can flag a brief (it is regenerated once, then shown `flagged` with the issues); if the fact check passes, an LLM approval gives `consistent`, and an LLM flag is kept as an *advisory* note because the 3B model produces false flags. A real example from 2 Oct 2026: a draft called a Critical event "high risk" — the fact check flagged it even though wording looked plausible. Limits: vague qualitative phrases ("relatively low probability") are not checked deterministically, and the LLM pass is the same model — **not independent validation**.

**19. What if the LLM fails?**
If the Ollama call fails or times out (30 s), `generate_brief` returns a deterministic template brief built from the same numbers, with `generated_by = fallback_template`, and the review status is `not_applicable`. The UI labels the brief deterministic, and `/api/health` reports `ollama_reachable: false`. Nothing else in the pipeline is affected, because the AI computes no numbers. This path is covered in `test_brief_agent`.

**20. What if the reviewer fails?**
If the LLM review call fails, the deterministic fact check still runs: if it finds issues the brief is `flagged`; otherwise `review_status = skipped` with the note "Consistency review skipped: local LLM unreachable during review." The brief is never marked as checked without the review. An unparseable LLM reply becomes an advisory note (fact check clean) or accompanies a fact-check flag; it is never treated as approval. If a retry draft falls back to the template, status becomes `not_applicable`. The status is always shown to the operator.

## E. Human control and feedback

**21. What does human-in-the-loop mean here?**
Every event ends at a human decision in the UI: **APPROVE ASSESSMENT** (collision), **ACKNOWLEDGE — CONTINUE TRACKING** (proximity), or **DISMISS** with a mandatory reason (`frontend/src/components/ApprovalPanel.jsx`). The API records it (`POST /api/events/{id}/approve` or `/dismiss`) in the persistent `decision_log`, with event ID, `is_demo`, screening-run ID, TCA and the acting user and role, and it is exportable as CSV (`/api/decisions/export`); each event can also be exported as an incident report in Word (.docx) or PDF (browser print, built locally in `frontend/src/reportExport.js`). The decision only records an assessment. The gate is not configurable, and nothing happens automatically after it.

**22. Can it autonomously maneuver a spacecraft?**
No. There is no command, uplink or maneuver-execution path anywhere in the code. `main.py` exposes only read endpoints, decision recording, refresh, demo seeding and optional local brief translation (`translation_api.py`, `POST /api/translation/brief`). "Approve" changes a status field and writes an audit row; nothing else happens.

**23. What does the Δv number mean?**
It is an **illustrative order-of-magnitude figure**: 1 km of extra separation spread over a 6-hour lead time, ≈ 0.046 m/s, and it is **the same for every collision event** (`maneuver.py::estimate_delta_v`). It deliberately ignores geometry, relative velocity, burn location, mass, thrust, fuel and constraints. It is labelled "Illustrative Δv estimate — not a maneuver plan". It is not a recommendation, and proximity events get no Δv.

**24. How is feedback handled?**
When an operator dismisses an event, the reason is stored in `decision_log.rejection_reason`. `db.py::recent_rejection_reasons(limit=3)` fetches the 3 most recent non-empty reasons, and `brief_agent.py::build_prompt` appends them to later drafter prompts, with an instruction to address the concerns in tone and framing but never invent or alter data. The Analytics panel shows accept/reject counts and recent reasons.

**25. Does feedback retrain the model?**
No. It is in-context feedback only: text added to future prompts. No model weights change, and there is no training, fine-tuning or RL. The code, UI and decks all say this explicitly (`FEEDBACK_MECHANISM_DESCRIPTION`). Governed model adaptation is listed only as possible future work.

**25a. Who can approve a decision?**
Only a signed-in account whose role has the `decide` permission: OPERATOR, ASSET_MANAGER or ADMINISTRATOR. A VIEWER can read events, provenance and audit trails but cannot decide; the backend refuses the request (403) even if the UI were bypassed (`backend/auth.py::ROLE_PERMISSIONS`, `require_permission("decide")`, tested in `test_rbac`). This is **prototype role-based access** with local accounts created by an administrator (`python -m backend.users create ...`); there are no default credentials. Human-in-the-loop governance is enforced through role-based access in the prototype. The operator remains responsible for the final decision.

**25b. How is identity recorded?**
Each decision and each new-object review stores the acting username and role from the login session; the decision CSV export includes `actor_username` and `actor_role`. Rows from before login existed are shown as "Legacy / pre-authentication record". Logins, account administration and protected-asset registry changes go to an append-only `governance_audit` table (SQLite triggers block update and delete). Limits, stated plainly: local accounts only, **no MFA, no SSO or enterprise identity provider**, no account lockout, and the audit is not tamper-proof or compliance-grade (anyone with file access to the database could alter it). Production deployment would additionally require enterprise identity integration, MFA, stronger secret management and hardened identity/audit infrastructure.

## F. Locality, data and deployment

**26. Why local Ollama?**
Briefs may concern sensitive assets, so inference stays on the operator's machine: no cloud AI, no cloud or third-party API keys (local accounts and login sessions control access; an optional local `ANTARIKSHA_API_KEY` adds a deployment gate on writes), no per-token cost. It also keeps working offline after the first TLE download, and the model choice is inspectable. `llama3.2` is open-weight under Meta's Llama license (not OSI open source), which the README states.

**27. What data leaves the machine?**
(a) HTTPS requests to CelesTrak `gp.php` to fetch public element sets (query names only) during ingest/refresh. (b) Only if `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured: the alert text for Critical/High events (Q28) sent to the Telegram Bot API. LLM inference goes to `localhost:11434`. The globe uses bundled Natural Earth imagery, so no map API key or tile requests are needed. (c) When online, the browser loads the Google Fonts stylesheet (Inter, IBM Plex Mono) from fonts.googleapis.com / fonts.gstatic.com; offline it falls back to system fonts. Optional local brief translation (IndicTrans2) makes no network call. Nothing else is sent; there is no telemetry in the code. (Installing packages from npm/PyPI and pulling the model are one-time setup steps.)

**28. What happens when Telegram alerts are enabled?**
For each Critical or High event, `notify.py::notify_event` posts one message: tier, event type, the two object names, TCA, miss distance (or minimum separation) and, for collisions, the Pc as odds, plus "Open the watch console to review and decide." Demo events are prefixed "[DEMO SCENARIO — controlled simulation, NOT an operational warning]". Delivery is best-effort with a 5 s timeout and no retries or escalation. Failures are logged and never block screening. Unconfigured Telegram is a silent no-op, and `/api/health` reports `telegram_configured`.

**29. What are the data dependencies?**
Public CelesTrak GP/TLE data for a working set defined in `data/working_set.json`: 11 Indian assets by `NAME=`, Fengyun-1C / Cosmos-2251 / Iridium-33 debris by `GROUP=` (capped at 50/20/10), and 12 other active satellites chosen by orbital-regime similarity, not nationality. Responses are cached in `data/tle_cache/` for offline use (`ingest.py::_fetch_with_cache`). Data is labelled LIVE (network ingest < 24 h old), CACHED, STALE, DEMO or NOT SCREENED. There is no covariance, no CDMs, and no authoritative or indigenous data.

**35. Live vs demo data?**
Live: real CelesTrak elements, real pipeline, and whatever events it finds. On 2 Oct 2026 that was 0 real events, and none were fabricated. Demo: real close approaches inside 10 km are rare for ~100 objects in 72 h, so `POST /api/demo/seed?mode=collision|proximity` builds a controlled geometry from a real object. Collision mode creates a crossing orbit tilted 1.5°, ~20 m designed miss, ~0.2 km/s, about 6–7 h ahead. Proximity mode co-orbits ~10 km along-track. The same unchanged pipeline then runs, with no injected Pc. Demo events are flagged `is_demo`, labelled DEMO SCENARIO, and the mode chip shows DEMO MODE. It is not a replay of any historical event.

**36. How do you prove demo data doesn't alter the real catalog?**
The demo elements are written only to a separate `demo_overrides` table (`db.py::set_demo_override`); the `objects` table is never written. Only a demo screening run applies the override (`list_objects_with_demo_overrides`), and every normal run calls `clear_demo_overrides()` first (`pipeline.py`). Tests: `test_demo_seed.py::test_demo_never_modifies_real_objects_table` and `test_normal_screening_after_demo_ignores_and_clears_override`, plus `test_db.py::test_demo_override_replaces_previous_and_clears`. In the 2 Oct 2026 E2E run, the API returned `real_catalog_modified: false`, and the following live refresh returned 0 events with status back to LIVE DATA and the override cleared. This fixes an earlier bug where the demo overwrote the real TLE.

## G. Performance and validation

**33. What is the current measured refresh time?**
A full refresh took 11–35 s across 6 refreshes on 2 Oct 2026, depending on the network. Propagate + screen took < 1 s (≈ 0.18 s + 0.16 s) in single measured runs on the demo laptop (103 objects, 1,012 pairs, 72 h at 60 s). The API reports `elapsed_seconds`, and the pipeline logs `propagation_seconds` / `screening_seconds`.

**34. What is the significance of the 11–35 s measurement?**
Most of it is the CelesTrak network fetch: 26 configured queries (23 `NAME=` lookups fetched concurrently with a 16-worker thread pool, plus 3 `GROUP=` debris queries), then the local LLM briefs if any events exist. Computation itself is under a second at this scale. These are single-machine observations, not benchmarks. They show the prototype is interactive on a laptop, but they do not predict full-catalog performance.

**44. What evidence validates the implementation?**
(a) Automated tests (613 backend + 162 frontend) on synthetic data: SGP4 sanity, screening scope, TCA refinement between samples, same-instant miss/Pc state, analytic Pc vs non-central chi-square and a 2M-sample Monte Carlo, all four tiers reachable, determinism, proximity dwell vs fly-through, provenance separation, demo integrity, AI fallback and review states, status labelling, the audit trail, prototype RBAC, protected-asset registry management, screening-horizon provenance and the AI tone guard. (b) An end-to-end run through the real HTTP API with live CelesTrak data on 2 Oct 2026 (`docs/technical/TESTING_VALIDATION.md` §3): 0 real events, the collision demo recovered the designed geometry (miss ~20 m, 0.1998 km/s), and the database confirmed no real-catalog change. (c) Per-event provenance in the UI. **Not done:** independent cross-check vs CelesTrak SOCRATES or CDMs, validation against precise ephemerides, CI.

**45. How many automated tests pass?**
613 backend tests pass, 1 skipped (`python -m pytest backend/tests`; no network or Ollama needed; the skipped test needs a local TLE cache, which is not shipped), plus 162 frontend Node tests (`npm test` → `node --test`) across the briefState, eventEvolution, screeningDelta, riskDrivers, reportExport, uiScale, i18n, auth, api, governance, horizonToneGuard and authI18n test files (5 Oct 2026), 775 passing in total. There is no CI yet; tests are run manually.

## H. Limitations, scale and roadmap

**30. What are the main scientific limitations?**
(1) TLE/SGP4 accuracy: errors from hundreds of metres to kilometres, growing with time, and no covariance. (2) Pc uses an assumed isotropic sigma and the short-encounter assumption; it is not covariance-based or validated, and its bias is unquantified. (3) The Δv is a fixed illustration. (4) The working set is small (~100 objects), with only Indian assets screened against the rest, not the full catalog. (5) No independent cross-check against SOCRATES or CDMs. (6) The AI reviewer is the same model as the drafter.

**31. What is required for operational deployment?**
Per README "What would be required for production?", none of the following exist today. Authoritative data (precise ephemerides, CDMs, realistic covariance, indigenous/multi-sensor fusion); higher-fidelity propagation and a validated Pc with covariance realism; independent scientific validation; maneuver planning integrated with flight dynamics; security (enterprise identity integration and MFA beyond the prototype RBAC, stronger secrets management, encryption, segmentation); tamper-evident audit; high availability and monitoring; defined human approval roles; and regulatory and mission-operations integration with the relevant organizations.

**32. How would this scale?**
Screening is intentionally O(|assets| × |others|), not all-pairs. It is vectorised in NumPy and only promotes a few candidates to SGP4 refinement, which is why ~1,000 pairs take ~0.16 s. Scaling toward the full public catalog (30,000+ objects) would need spatial indexing/prefiltering, parallel screening workers, PostgreSQL instead of SQLite (no WAL, single-process lock today), and cross-process job coordination. These are roadmap items, **not built**, and no performance at that scale is claimed.

**40. What differentiates it technically?**
(No ranking against other systems is claimed.) It combines: one authoritative refined TCA with miss, relative velocity and Pc at the same instant; episode-based screening that does not miss fast crossings; a deterministic analytic Pc with transparent assumptions; intent-neutral proximity dwell detection alongside conjunctions; local LLM briefs with provenance chips, a structured consistency review and a deterministic fallback; honest in-context feedback; a hard human gate with no command path; per-event provenance separating deterministic, probabilistic, AI-generated and human-decided content; demo isolation that is tested; and operation on one laptop, offline after the first fetch.

**41. What would you do next with more time?**
Cross-check predictions against CelesTrak SOCRATES; ingest CDMs and use real (ellipsoidal) covariance in the existing encounter-plane formulation; scale to the full public catalog with spatial indexing; historical replay once authoritative archives are available; enterprise identity integration (SSO, MFA) on top of the prototype RBAC and tamper-evident audit; CI; a one-command installer. Longer term, and only with access and partners: authoritative/indigenous data fusion and maneuver-planning research.

**42. What was the hardest engineering challenge?**
(1) **Getting a single authoritative refined TCA.** The original code missed most fast crossings (a 60 s grid steps ~600 km at 10 km/s) and evaluated Pc at the window midpoint. The fix was velocity-padded close-approach episodes, a cheap interpolation prefilter, then SGP4-backed bounded minimisation, with everything evaluated at that one instant. (2) **Demo integrity.** An early demo overwrote a real TLE, and a later live run reported the fake event as real. The fix was a separate `demo_overrides` table that every normal run clears, plus tests. (3) **Reviewer reliability.** A small local model flagged correct briefs (rounding, the required closing sentence, markdown verdicts). The fix gives the reviewer the exact drafter facts block, the expected closing statement, explicit non-contradiction rules, and tolerant but fail-safe verdict parsing.

**43. What would you change before production?**
Replace the assumed sigma with real covariance and validate Pc against reference cases; use authoritative ephemerides; replace the fixed Δv with real flight-dynamics tooling (or remove it); replace local accounts with enterprise identity integration and MFA, harden secret management, and add a tamper-evident, access-controlled audit store; move to a multi-user database with cross-process coordination; add monitoring, alert escalation and HA; evaluate the LLM brief formally (or use an independent reviewer); set up CI and a formal V&V plan; and replace FastAPI's deprecated `on_event` startup hook.

**46. What was added in the latest iterations?**
All of these are prototype features (README sections "Event evolution and refresh changes", "Multilingual / local translation", "CelesTrak format: TLE and OMM", "Named-object resolution", "Prototype role-based access (RBAC)", "Protected-asset registry management"):
- **Event evolution:** per-run numeric observations, with miss-distance, tier and TCA trends across screening runs. Correlation uses a prototype matching rule, not an authoritative event identity.
- **Refresh-change summary:** new, tier up/down, unchanged, and "not present in the latest run". Absence is not treated as resolution.
- **Native CCSDS OMM ingestion (optional):** keeps 6-digit catalog numbers. TLE stays the default and gives bit-identical results.
- **Ingest safety:** a refresh is refused, with the catalog unchanged, if fewer than 50% of protected assets resolve or more than 50% of the catalog would be deactivated.
- **Local 11-language UI:** machine-assisted and not yet reviewed by native speakers. Evidence values stay untranslated, and optional local brief translation has no cloud fallback.
- **Text-size control.**
- **Word/PDF incident reports.**
- **Prototype role-based access (RBAC):** login screen, local accounts (PBKDF2 password hashes, no default credentials), HttpOnly session cookie, CSRF header, four backend-enforced roles (VIEWER, OPERATOR, ASSET_MANAGER, ADMINISTRATOR), Admin panel, identity-aware decisions and reviews, append-only governance audit. No MFA or SSO.
- **Protected-asset registry management:** ASSET_MANAGER+ can add, edit, suspend, resume or retire entries; every change is audited and applies at the next successful refresh. Protected status comes only from operator configuration; the AI cannot change it.
- **Screening horizon from the run itself:** current prototype screening horizon 72 hours (not an accuracy guarantee), shown from the latest screening run and, per event, from that event's own run.
- **AI tone guard:** a deterministic check rejects unsupported sensational certainty (e.g. "catastrophic", "inevitable", "imminent"); one redraft, then a labelled deterministic template.
- **Optional API key for write endpoints:** still available as an extra deployment layer.

None of these changes the physics, the Pc indicator or the human gate; RBAC only controls who may act at the gate.

---
*Pipeline figures are dated 2 Oct 2026 and test counts 3 Oct 2026; all come from one developer machine. Source of truth: `README.md` and `docs/technical/TESTING_VALIDATION.md` in the repository.*

## Known differences from the recorded video

The submission video (`submission_video/ANTARIKSHA_RAKSHA_Jury_Demo.mp4`) was recorded earlier and cannot be changed. If asked, use the figures in this document:
- **Refresh time:** section 5 narrates a full refresh of "about 7 and 35 seconds". The documented figure is **11–35 s across 6 refreshes on 2 Oct 2026**, network-dependent.
- **Capture date:** section 5 says the run was captured on 3 Oct 2026, but the on-screen catalog caption reads 2 Oct 2026 (the measurement date).
- **Roadmap numbering:** section 10 narrates Phases 1–5, with Phase 1 as today. The decks use Phase 0 (complete) to Phase 3. The content is equivalent; nothing beyond the current prototype is built.
- **"Open-source software":** section 9 says the system runs "on open-source software and an open-weight model". More precisely, it is built **mostly** on open-source components; llama3.2 is open-weight under Meta's licence (not OSI open source); and the project repository carries no open-source licence.
- **Test count:** section 8 says "more than 100 automated tests". That is still true; the current count is 613 backend + 162 frontend = 775 passing.
- **Newer features:** the video predates the features in Q46 (event evolution, refresh summary, OMM, ingest safety, multilingual UI, text size, Word/PDF export, prototype RBAC, protected-asset management, tone guard). The video shows an "incident report" without naming the format.
- **Login and RBAC:** the video predates login and role-based access. It shows the console opening directly, with no login screen, no `ROLE · name` header badge and no actor shown in the decision history. The current build requires signing in (the live demo uses an OPERATOR account), and decisions record the acting user and role.
