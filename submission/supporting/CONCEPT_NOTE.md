# Concept Note — ANTARIKSHA-RAKSHA

**Space Situational Awareness (SSA) Decision-Support Prototype**
Track 03 — Defense & Strategic Technologies · Sovereign Technology for India
Repository: https://github.com/SEJUNK/antariksha-raksha-submission (public submission repository) · Status: **working prototype (research/demo)**

*The name means "Space Protection / Space Defense" in Sanskrit. The system is a research/demo Space Domain Awareness decision-support prototype focused on transparent conjunction analysis, local AI explanation, human decision support and persistent provenance — defence-relevant Space Domain Awareness and conjunction-assessment decision support. It is not an operational warning service, not a weapon or defence system, and it has no command capability.*

---

## 1. Background & Need

India operates a substantial fleet of satellites, including NavIC navigation, GSAT communications and the
Cartosat/RISAT/EOS Earth-observation fleet. These are important national infrastructure. The orbital environment
around them is increasingly congested. Tens of thousands of objects larger than about 10 cm are tracked, and debris
from the 2007 Fengyun-1C anti-satellite test and the 2009 Iridium-Cosmos collision still occupies orbital regimes
used by Indian Earth-observation satellites. Rendezvous-and-proximity operations near other spacecraft are also
publicly reported worldwide.

India already has SSA capability and institutions working on it, including ISRO's IS4OM and the NETRA programme.
ANTARIKSHA-RAKSHA does not replace them and is not integrated with them. It explores **a complementary
decision-support layer**: an affordable, transparent, operator-facing screening and explanation console that
**could** consume indigenous or authoritative data sources in a future deployment. Today it runs only on CelesTrak
public GP data.

## 2. Proposed Solution

ANTARIKSHA-RAKSHA is an end-to-end decision-support prototype that runs on a single laptop:

1. **Perceive.** The system ingests public orbital data (CelesTrak GP/TLE) for a small working set of Indian assets,
   debris fragments and other active satellites. It labels data freshness (LIVE / CACHED / STALE) and keeps an offline
   cache. It is **offline-capable after the first TLE download** (the globe uses a locally served NASA Blue Marble texture, with imagery bundled with Cesium as fallback; when online the
   UI loads its web fonts from Google Fonts, and offline it falls back to system fonts). All orbits are propagated with SGP4 (skyfield)
   over 72 h at a 60 s step.
2. **Reason.**
   - Each Indian asset is screened against all other objects in the working set. A **10 km prototype screening
     threshold** generates candidates; it is not a universal collision criterion. The TCA is refined by bounded
     minimisation on re-evaluated SGP4 states. Miss distance, relative velocity and Pc are all evaluated at that
     refined TCA.
   - A **simplified analytic encounter-plane collision-probability indicator** is computed (isotropic assumed uncertainty, not
     covariance-based).
   - **Proximity patterns near protected assets** are detected by dwell time (another active satellite within 25 km
     for at least 10 consecutive 1-minute samples).
   - Events are ranked by criticality-weighted priority.
   - A **local open-weight LLM** (Ollama, llama3.2:3b) drafts a plain-language brief from the computed numbers. A
     **consistency check** follows: a deterministic fact check of numbers, risk tier, recommendation and intent language,
     plus a second pass by the same model. This is not independent validation. If the LLM is unavailable, a deterministic template takes over.
3. **Decide (human-gated).**
   - The operator sees the brief, the numbers, a separation-profile chart, a data-confidence indicator and an
     **illustrative Δv estimate**. The Δv estimate is a fixed approximation and not a maneuver recommendation.
   - The operator then approves the assessment, acknowledges it, or dismisses it with a stated reason. This only
     records a decision. No spacecraft command path exists.
   - Decisions go into a **persistent operator decision audit trail** (SQLite, CSV-exportable, no tamper protection)
     alongside a persistent screening-run log. Each decision records the acting account and role.
   - Optional Telegram alerts (off unless enabled) notify an operator of Critical/High events once per event track
     and tier escalation; they are notification-only and cannot approve, command or execute anything. Demo events
     are not alerted unless explicitly enabled, and are then labelled DEMO — CONTROLLED SIMULATION.

**Operator feedback:** the most recent rejection reasons (up to 3) are inserted into later brief prompts. This is
*in-context feedback from previous operator decisions*. No model weights are modified, and there is no training,
fine-tuning or reinforcement learning. Using the decision log for model adaptation is a possible future option, and
it would need data governance and validation.

**Governance:** Human-in-the-loop governance is enforced through role-based access in the prototype. The operator
remains responsible for the final decision. The backend enforces four roles on local accounts (VIEWER, OPERATOR,
ASSET_MANAGER, ADMINISTRATOR): only an OPERATOR or higher can record a decision, only an ASSET_MANAGER or higher can
change the protected-asset registry, and only an ADMINISTRATOR can manage accounts. Logins, account changes and
registry changes go into an append-only governance audit (not tamper-proof). The AI cannot change protected status,
roles or any decision. There is no MFA or SSO; production deployment would additionally require enterprise identity
integration, MFA, stronger secret management and hardened identity/audit infrastructure.

## 3. Innovation & Differentiation

- **Transparent and locally run:** the prototype is built mostly on open-source software (Python, FastAPI, SQLite,
  skyfield, React, CesiumJS). The AI is a local open-weight model (Llama 3.2, under Meta's license), so operation
  needs no cloud AI service and no paid API key. There is no software license cost for the current prototype.
  External dependencies remain: CelesTrak data, optional Telegram, Google Fonts (UI typography, when online) and
  package registries. The submission repository is public for evaluation and carries no open-source license.
- **Careful AI posture:**
  - local inference only
  - provenance labels on every AI output
  - a deterministic fact check plus a same-model second-pass review (not independent validation)
  - prompts that forbid invented numbers and speculation about intent
  - a hard human gate (the system cannot act autonomously)
  - every number shown is computed by deterministic code, not by the AI
- **Beyond close approaches:** dwell-time detection of proximity patterns near protected assets, with deliberately
  intent-neutral reporting language.
- **Accessible:** the console runs on commodity hardware, which suits training, research and evaluation settings.

## 4. Current Status

The end-to-end prototype includes:

- a public-data ingest with freshness labelling
- SGP4 propagation and screening of the current prototype working set (up to roughly a hundred objects; the actual
  count is shown at runtime)
- TCA refinement and a simplified Pc indicator
- proximity watch
- AI briefs with a deterministic fallback
- the human decision workflow with a persistent audit trail and screening-run log
- decision analytics and CSV export, plus per-event incident reports as Word (.docx) or PDF
- a clearly labelled controlled demo mode
- event evolution across screening runs and a refresh-change summary (prototype correlation rule, not an
  authoritative event identity)
- optional native CCSDS OMM ingestion, which keeps 6-digit catalog numbers
- ingest safety: a refresh is refused, leaving the catalog unchanged, if fewer than 50% of protected assets resolve
  or more than 50% of the active catalog would be deactivated
- a local 11-language UI (machine-assisted translations, not yet reviewed by native speakers) and a text-size control
- prototype role-based access enforced by the backend: local accounts, login sessions, four roles, identity-aware
  decision and review records, an append-only governance audit, and protected-asset registry management by an
  ASSET_MANAGER (no MFA or enterprise identity integration)
- a deterministic tone guard that rejects unsupported sensational certainty in AI drafts (redraft once, then a
  labelled deterministic template)
- the screening horizon (default 72 hours; an ADMINISTRATOR can choose 24/48/72/96/120 h, audited, applied to the
  next run; not an accuracy guarantee) reported from each screening run
- a periodic public orbital-data refresh every 2 hours (in-process scheduler or an external cron; not real-time
  sensor tracking), with too-soon, demo-active and overlap guards; manual refresh remains available
- a deployment path: static frontend on Vercel with a same-origin API proxy and a cron relay, and the backend on a
  persistent host with its SQLite database on a mounted volume (`docs/technical/DEPLOYMENT.md`); the backend itself is not
  serverless

Automated tests are in `backend/tests` (see `docs/technical/TESTING_VALIDATION.md` for current results). No measured
performance benchmarks are claimed here.

## 5. Roadmap (planning, not commitments)

| Phase | Horizon | Scope |
|---|---|---|
| 0 — Prototype | **Current** | End-to-end system as described above |
| 1 — Hardening | Future | Larger catalog, CDM ingestion, realistic covariance, validated Pc (encounter-plane methods), enterprise identity integration and MFA beyond the prototype RBAC, tamper-evident audit |
| 2 — Authoritative data & AI | Future | Consumption of authoritative/indigenous data and multi-sensor fusion (subject to access and partnership), maneuver planning with flight-dynamics integration, governed evaluation of model adaptation |
| 3 — Deployment evaluation | Future | Pilot evaluation with relevant organizations, independent validation, operational security |

## 6. Impact (qualitative)

Better-prioritised, explained conjunction and proximity information can help operators focus attention and protect
high-value satellites and the services they provide. Avoiding debris-generating collisions also helps keep LEO
usable. Asset values and avoided losses are not quantified here: any such figure would be illustrative only. The
project could also serve as an inspectable platform for SSA education (public submission repository, available for evaluation)
and a reference design for human-controlled decision-support AI.

## 7. Team Ask

Mentorship and expert review. Guidance toward a pilot evaluation with relevant organizations. Access paths to
authoritative data (ephemerides, covariance, CDMs) for validation.

---
*Demo integrity note: real close approaches are rare in any 72-hour window for a small working set, so live demos
can use a **controlled demonstration scenario**:*

- *It starts from ingested real objects and gives one debris object a controlled crossing orbit (or aligns one
  other active satellite for the proximity case) relative to a protected asset, as a separate override. Real catalog rows are never modified.*
- *It runs the unmodified pipeline with unchanged thresholds and no injected Pc.*
- *It is labelled DEMO SCENARIO / DEMO MODE throughout and must be narrated as a controlled demonstration.*
