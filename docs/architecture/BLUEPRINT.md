> **Historical design blueprint (pre-implementation planning document).** Where it differs from README.md's capability matrix, README.md and the code are authoritative. Figures, claims, and roadmap items here are planning statements, not verified capabilities. In particular: the system is a research/demo prototype, not an operational collision-warning service; its Pc is a simplified analytic encounter-plane indicator (not an operational covariance-based Pc; the Monte Carlo design below was replaced); its Δv figure is illustrative, not a maneuver recommendation; the AI "reviewer" is a second-pass consistency check by the same local LLM, not independent validation; and there is no integration with ISRO, IS4OM, NETRA or any other organization. The AI does not learn (rejection reasons are prompt context only, with no training); the audit trail is a local SQLite log with no tamper protection; the system is offline-capable only after the first TLE download; and the repository carries no open-source licence.

# ANTARIKSHA-RAKSHA — Enhanced End-to-End Technical Blueprint (v2, Space + Defense Edition)
### Build specification for AI-assisted implementation (any coding agent)

**How to use this document:** Place this file as `BLUEPRINT.md` in an empty repo root and instruct the coding agent: *"Implement this blueprint end-to-end, phase by phase, following Section 12 build order. Do not skip acceptance criteria in Section 13."* It is self-contained — every module, schema, endpoint, algorithm, and prompt needed is specified. Where a judgment call was needed, one has already been made so the agent does not need to stop and ask.

---

## 0. Project Summary

**Name:** ANTARIKSHA-RAKSHA (Sanskrit: "Space Defense")
**One-line description:** An indigenous, AI-powered Space Situational Awareness (SSA) and space-defense decision-support system that tracks satellites and orbital debris using public data, screens Indian space assets for close approaches and proximity patterns, estimates a simplified analytic collision-probability indicator, weights priority by asset criticality, and generates explainable, human-reviewed watch briefs with an illustrative Δv estimate (not a maneuver recommendation) — running on local infrastructure with an offline open-weight LLM.

**Positioning statement (as revised; see README):** India already has SSA capability and programmes (e.g. ISRO's IS4OM and NETRA); this project is not integrated with them and makes no claim that India lacks SSA. ANTARIKSHA-RAKSHA is positioned as a complementary decision-support layer that could consume indigenous/authoritative data sources in a future deployment; today it runs only on CelesTrak public GP/TLE data. It never auto-executes — every assessment passes through a human operator, and no spacecraft command path exists.

**Architecture pattern:** Perceive → Reason → Execute, implemented as a linear data pipeline, not a distributed system.
**Target outcome:** A runnable, demoable, laptop-only prototype — not a production system. Optimize for correctness and a working end-to-end demo over scalability, security hardening, or enterprise concerns.

---

## 1. Explicit Constraints (read before writing any code)

1. **No hardware.** All data comes from public sources over HTTP (CelesTrak). No sensors, no drones, no IoT.
2. **No cloud infrastructure, no paid services of any kind.** Runs entirely on a single laptop: SQLite (not Postgres), no Docker required (a Dockerfile is a nice-to-have only), no cloud LLM API — use a local model via Ollama (no license fee; open-weight model).
3. **No authentication/authorization.** Single-user, local-only tool.
4. **No websockets.** Simple HTTP polling from the frontend (every 5–10 seconds) is sufficient.
5. **Working data set:** ~100–150 tracked objects (real Indian satellites + selected foreign satellites + real debris including Fengyun-1C ASAT fragments), not the full 30,000+ object catalog. Full-catalog scale is documented as future work only.
6. **Human-in-the-loop is a hard requirement**, not configurable: the system must never auto-execute a maneuver. It only presents assessments; every assessment is presented for a human decision, which is recorded and commands nothing.
7. **Everything must run offline after the initial TLE download** — the LLM must be local (Ollama), so the demo works without network at presentation time (as implemented: offline-capable after caching; the globe uses Cesium's bundled Natural Earth II imagery).
8. **All software must be free/open-source** (as implemented: open-source software plus the open-weight Llama 3.2 model under Meta's license, which is not OSI open source; external dependencies on CelesTrak and package registries remain). If the agent is tempted to use anything requiring a license, API key with payment, or Cesium ion paid token — don't; use the free alternative specified.

---

## 2. Tech Stack (exact choices — do not substitute without a stated code comment reason)

| Layer | Choice | Notes |
|---|---|---|
| Backend language | Python 3.11+ | |
| Backend framework | FastAPI + `uvicorn` | |
| Orbital mechanics | `skyfield` | wraps SGP4; use `EarthSatellite` |
| Numerical | `numpy`, `scipy` | Monte Carlo simulation |
| Database | SQLite via built-in `sqlite3` | single file `data/antariksha.db` |
| LLM runtime | Ollama with `llama3.2:3b` (default) or `mistral:7b` (16GB+ RAM) | `POST http://localhost:11434/api/generate` |
| Frontend | React 18 via Vite | |
| 3D globe | CesiumJS (via `resium` or thin wrapper) | free default imagery; NO Cesium ion token |
| HTTP client (frontend) | native `fetch` | |
| Styling | plain CSS or Tailwind (agent's choice, consistent) | dark theme per Section 9 |
| Packaging | `pip` + `requirements.txt`; `npm` | no poetry/yarn |

---

## 3. Repository Structure (create exactly this layout)

```
antariksha-raksha/
├── backend/
│   ├── main.py                  # FastAPI app + routes
│   ├── ingest.py                # TLE fetch + parse + store
│   ├── propagate.py             # SGP4 propagation
│   ├── conjunction.py           # pairwise close-approach screening
│   ├── risk_score.py            # Monte Carlo Pc + criticality-weighted priority
│   ├── threat.py                # NEW: proximity-threat / anomaly classification
│   ├── maneuver.py              # simplified delta-v estimate
│   ├── brief_agent.py           # Ollama drafter + reviewer pass + fallback
│   ├── db.py                    # SQLite schema + CRUD helpers
│   ├── config.py                # constants, thresholds, model name, criticality map
│   ├── requirements.txt
│   └── tests/
│       ├── test_propagate.py
│       ├── test_conjunction.py
│       ├── test_risk_score.py
│       └── test_threat.py
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── api.js
│       └── components/
│           ├── GlobeView.jsx        # Cesium globe, live positions
│           ├── EventFeed.jsx        # sorted flagged events
│           ├── EventDetail.jsx      # risk, brief, maneuver, threat class
│           ├── ApprovalPanel.jsx    # approve / dismiss
│           └── AssetPanel.jsx       # NEW: Indian asset list w/ criticality tiers
├── data/
│   └── working_set.json
├── scripts/
│   └── seed_demo_event.py
├── README.md
└── BLUEPRINT.md
```

---

## 4. Working Set Definition (`data/working_set.json` + `config.py`)

The working set has three groups. `ingest.py` fetches TLEs by NORAD ID (or by CelesTrak group file, then filters).

**Group A — Indian assets (protected assets), with criticality tiers:**
Include real, currently-orbiting Indian satellites. Suggested set (verify NORAD IDs against CelesTrak at build time; if any is decayed, substitute a comparable Indian satellite and note it in a comment):
- Tier 1 (CRITICAL — navigation/strategic comms): NavIC/IRNSS constellation members (e.g., IRNSS-1I), GSAT military-band comms satellites (e.g., GSAT-7/7A class).
- Tier 2 (HIGH — earth observation/recon): Cartosat-2/3 series, RISAT series, EOS series.
- Tier 3 (MEDIUM — science/civil): Oceansat, Resourcesat, Astrosat.
Store tier in `objects.criticality` (see schema). Criticality multipliers for priority scoring: Tier 1 = 3.0, Tier 2 = 2.0, Tier 3 = 1.0, non-Indian/debris = n/a.

**Group B — Debris:** 60–100 objects, biased toward debris in LEO regimes overlapping Group A. MUST include a subset of **Fengyun-1C fragments** (1999-025 international designator family, from the 2007 Chinese ASAT test) — fetch via CelesTrak group `fengyun-1c-debris` (or equivalent current group name). Also include Cosmos-2251/Iridium-33 collision debris if convenient.

**Group C — Other (non-Indian) active satellites (proximity-watch set):** 10–20 active non-Indian satellites in orbits near Group A assets. These are used by `threat.py` for proximity-operation detection. Selection is orbital-mechanical (similar altitude/inclination to Indian assets), NOT nationality-based accusation — see Section 7 ethics note.

`config.py` must define: `WORKING_SET_PATH`, `SCREENING_THRESHOLD_KM = 10.0`, `PROPAGATION_WINDOW_HOURS = 72`, `PROPAGATION_STEP_SECONDS = 60`, `OLLAMA_URL`, `OLLAMA_MODEL`, `CRITICALITY_MULTIPLIERS` dict, `PROXIMITY_WATCH_KM = 25.0`, `PROXIMITY_DWELL_MIN_STEPS = 10`.

---

## 5. Database Schema (`backend/db.py`)

Create on first run if absent:

```sql
CREATE TABLE IF NOT EXISTS objects (
  norad_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  object_type TEXT NOT NULL CHECK(object_type IN ('satellite','debris','foreign_sat')),
  owner_country TEXT,
  criticality TEXT CHECK(criticality IN ('Tier1','Tier2','Tier3') OR criticality IS NULL),
  tle_line1 TEXT NOT NULL,
  tle_line2 TEXT NOT NULL,
  last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conjunction_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_a_id TEXT NOT NULL REFERENCES objects(norad_id),   -- Indian asset
  object_b_id TEXT NOT NULL REFERENCES objects(norad_id),   -- other object
  event_class TEXT NOT NULL CHECK(event_class IN ('collision_risk','proximity_watch')),
  tca_timestamp TEXT NOT NULL,           -- ISO 8601 UTC
  miss_distance_km REAL NOT NULL,
  rel_velocity_km_s REAL,
  pc_score REAL NOT NULL,                -- 0.0-1.0 (0 for proximity_watch if not computed)
  risk_tier TEXT NOT NULL CHECK(risk_tier IN ('Low','Medium','High','Critical')),
  priority_score REAL NOT NULL,          -- pc-derived score × criticality multiplier
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mission_briefs (
  event_id INTEGER PRIMARY KEY REFERENCES conjunction_events(id),
  brief_text TEXT NOT NULL,
  maneuver_text TEXT NOT NULL,
  delta_v_ms REAL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','dismissed')),
  reviewer_notes TEXT,
  generated_by TEXT NOT NULL DEFAULT 'llm' CHECK(generated_by IN ('llm','fallback_template'))
);
```

`db.py` exposes plain functions: `init_db()`, `upsert_object(...)`, `list_objects()`, `insert_event(...)`, `list_events(sorted by priority_score DESC)`, `get_event_with_brief(id)`, `insert_brief(...)`, `set_brief_status(event_id, status)`. Use parameterized queries throughout.

---

## 6. Backend Modules — Detailed Specs

### 6.1 `ingest.py`
- `fetch_tles() -> list[dict]`: download TLEs from CelesTrak (`https://celestrak.org/NORAD/elements/gp.php?...` endpoints, format=tle). Fetch: (a) group `stations`/specific NORAD IDs for Group A per `working_set.json`; (b) debris groups for Group B; (c) selected IDs for Group C.
- Parse standard 3-line TLE format (name line + line1 + line2). Validate checksum lines start with '1 ' and '2 '.
- `store_objects(tles)`: upsert into `objects` with type/criticality from `working_set.json` mapping.
- Cache raw TLE text to `data/tle_cache/` with timestamp so subsequent runs work offline. If network fetch fails AND cache exists → load cache and log a warning (this is the offline-demo path). If neither → raise a clear error telling the user to run once with network.
- Runnable directly: `python -m backend.ingest` prints object count by group.

### 6.2 `propagate.py`
- `propagate_object(tle_line1, tle_line2, times: list[datetime]) -> np.ndarray`: use `skyfield` `EarthSatellite`; return ECI (GCRS) positions in km, shape `(len(times), 3)`. Also provide `velocity_at(t)`.
- `propagate_all(window_hours, step_seconds) -> dict[norad_id, positions]`: propagate every object across the window on a shared time grid. This grid is reused by conjunction and threat modules — compute once.
- `current_positions() -> list[dict]`: position now, converted to lat/lon/alt (skyfield `subpoint()`) for the globe.
- Guard: skip (and log) objects whose TLE epoch is > 30 days old or whose propagation raises (decayed objects) rather than crashing.

### 6.3 `conjunction.py`
- `find_close_approaches(positions_grid, threshold_km) -> list[event_dict]`:
  - Only screen pairs where object A ∈ Indian assets (Group A). This reduces pairs from O(n²) to |A|×|others| — intentional scope: we protect Indian assets, we don't do global screening.
  - For each pair, compute distance at every time step (vectorized numpy over the shared grid). Find the local minimum below `threshold_km`; record TCA timestamp (interpolate parabolic between neighboring steps for a slightly better TCA — one-line refinement, don't over-engineer), miss distance, and relative velocity at TCA.
  - Deduplicate: one event per pair per window (keep the global minimum).
- If zero events found against real data, direct the user to `scripts/seed_demo_event.py` (Section 10) rather than inflating the threshold.

### 6.4 `risk_score.py`
- `compute_collision_probability(pos_a, pos_b, rel_velocity_km_s, sigma_km=0.5, hard_body_radius_km=0.02, n_samples=5000) -> float`:
  - Sample perturbed positions for A and B independently from `N(pos, sigma_km² · I₃)`; return fraction of pairs with distance < `hard_body_radius_km`.
  - `sigma_km = 0.1 + 0.05 * hours_until_tca / 24` (linear uncertainty growth — simple and defensible; comment it as a known simplification).
- `risk_tier_from_pc(pc)`: Critical if pc > 1e-4, High if > 1e-5, Medium if > 1e-6, else Low.
- `priority_score(pc, criticality) -> float`: `max(pc, 1e-9)`'s negative log inverted into a 0–100 scale is overkill — simply use `pc * CRITICALITY_MULTIPLIERS[criticality] * 1e6` and sort by it. Document the formula in a comment. Proximity-watch events get a fixed priority band below any Medium collision event unless dwell time is extreme (see 6.5).
- Tests (`test_risk_score.py`): seed RNG; assert huge miss + small sigma → pc ≈ 0; near-zero miss + larger sigma → pc measurably > 0; assert Tier1 event outranks identical-pc Tier3 event.

### 6.5 `threat.py` (NEW — the defense module)
Purpose: flag *other active satellites* (Group C) showing sustained proximity patterns near Indian assets — a rendezvous/inspection-style pattern — as `proximity_watch` events distinct from debris collision risk.
- `detect_proximity_operations(positions_grid) -> list[event_dict]`:
  - For each (Indian asset, Group C satellite) pair, count consecutive time steps where separation < `PROXIMITY_WATCH_KM` (25 km).
  - If dwell ≥ `PROXIMITY_DWELL_MIN_STEPS` (10 steps = 10 min at 60 s step), emit a `proximity_watch` event: record minimum distance, dwell duration (minutes), and approach geometry (closing / station-keeping / receding, from the sign of range-rate at the window midpoint).
  - Risk tier for proximity events: `High` if dwell > 60 min AND min distance < 10 km; else `Medium`.
- Debris and Indian-Indian pairs are excluded — this module is only about active foreign objects.
- `test_threat.py`: construct two synthetic co-orbital position tracks (no real TLEs needed) and assert a dwell event is detected; assert a fast fly-through (2 steps under threshold) is NOT flagged.
- **Ethics/framing note for README and code comment:** a proximity_watch flag is an *anomaly worth human review*, not an accusation of hostile intent. Objects are selected by orbital similarity, and the brief language must remain factual (distances, durations) — this is enforced in the prompt template (Section 8).

### 6.6 `maneuver.py`
- `estimate_delta_v(rel_velocity_km_s, desired_miss_increase_km=1.0, lead_time_hours=6.0) -> float`: `delta_v_m_s = (desired_miss_increase_km / (lead_time_hours*3600)) * 1000`. Deliberately simplified radial-separation approximation — comment it as such. Only computed for `collision_risk` events; proximity_watch events get maneuver_text = "No maneuver recommended — continue enhanced tracking." with delta_v NULL.

### 6.7 `brief_agent.py`
- `generate_brief(event, pc, risk_tier, delta_v_m_s) -> dict`: build prompt from the matching template in Section 8 (collision vs proximity), call Ollama (`{"model": OLLAMA_MODEL, "prompt": ..., "stream": false}`, 30 s timeout), return `{"brief_text", "maneuver_text", "generated_by": "llm"}`.
- `review_brief(brief_text, event, pc) -> dict`: second Ollama call with reviewer template; returns `{"approved": bool, "notes": str}`. If FLAGGED, regenerate once; if flagged again, pass through with reviewer_notes populated so the human sees the flag.
- Fallback: if Ollama unreachable/timeout at any point → deterministic f-string template brief with `generated_by='fallback_template'`. The pipeline must never hard-fail during a live demo.

### 6.8 `main.py` (FastAPI) — exact endpoints

| Method | Path | Response |
|---|---|---|
| GET | `/api/objects` | all objects with current lat/lon/alt, type, criticality |
| GET | `/api/events` | events sorted by priority_score DESC (collision + proximity interleaved by score) |
| GET | `/api/events/{id}` | event + brief (join) |
| POST | `/api/events/{id}/approve` | status → approved, return record |
| POST | `/api/events/{id}/dismiss` | status → dismissed, return record |
| POST | `/api/refresh` | full pipeline: ingest → propagate → conjunction + threat → risk → briefs; returns counts summary |
| GET | `/api/health` | `{ok, ollama_reachable, object_count, using_cache}` — for demo pre-flight |

CORS enabled for `http://localhost:5173`.

---

## 7. Ethics & Honesty Requirements (bake into code + README)

1. Human-in-the-loop always; no auto-execution path exists in the codebase.
2. Proximity flags are anomalies for review, never accusations; brief language is restricted to measured facts.
3. Any demo-seeded orbit adjustment is logged, commented, and narrated (Section 10).
4. Known simplifications (spherical covariance, simplified delta-v, TLE accuracy limits) are listed in a README "Limitations" section — judges reward honesty over inflated claims.

---

## 8. LLM Prompt Templates (use exactly; adjust only for model quirks)

**Collision brief prompt:**
```
You are a satellite operations analyst at an Indian space defense watch center. Given the following conjunction event data, write a short, plain-language Space Defense Watch Brief (3-4 sentences) for a satellite operator, followed by one sentence noting the illustrative delta-v estimate. [Implementation note: the implemented system presents an "Illustrative Δv estimate", explicitly not a maneuver recommendation.]

Event data:
- Protected asset: {object_a_name} (criticality: {criticality})
- Approaching object: {object_b_name} ({object_b_type})
- Time of closest approach: {tca_timestamp}
- Miss distance: {miss_distance_km} km
- Collision probability: {pc_formatted} (1 in {pc_odds})
- Risk tier: {risk_tier}
- Illustrative delta-v estimate: {delta_v_m_s} m/s

Do not invent numbers not given above. Refer only to the data provided. Write in a calm, professional tone appropriate for a mission control brief.
```

**Proximity-watch brief prompt:**
```
You are a satellite operations analyst at an Indian space defense watch center. Given the following proximity event data, write a short, factual Space Defense Watch Brief (3-4 sentences) describing the observed behavior, followed by one sentence recommending continued enhanced tracking and human review.

Event data:
- Protected asset: {object_a_name} (criticality: {criticality})
- Nearby active satellite: {object_b_name}
- Minimum separation: {min_distance_km} km
- Dwell time within {watch_km} km: {dwell_minutes} minutes
- Approach geometry: {geometry}

Strict rules: Do not speculate about intent. Do not use accusatory language. Describe only the measured distances, durations, and geometry given above. Recommend human analyst review.
```

**Reviewer prompt** (implemented as a second-pass LLM consistency review by the same local model — an additional LLM check, not independent validation):
```
You are reviewing a drafted satellite watch brief for factual consistency before it reaches a human operator.

Original data:
{key_value_dump_of_event_fields}

Drafted brief:
"{brief_text}"

Does the drafted brief accurately reflect the original data, without inventing numbers, claims, or intent not present in the data? Respond with exactly one word, APPROVED or FLAGGED, followed by a one-sentence explanation.
```

---

## 9. Mission Control UI Specification (follow precisely — do not substitute a generic dashboard layout)

The UI must feel like a national space-defense watch center, not an admin dashboard. Everything below is prescriptive; where an item says "if feasible," a simpler fallback is stated — never silently drop a requirement without implementing its fallback.

### 9.1 Design tokens (define once as CSS variables in `src/theme.css`, use everywhere)

```css
:root {
  --bg-space:      #0B1020;   /* app background */
  --bg-panel:      rgba(19, 41, 75, 0.72);  /* translucent navy panels over the globe */
  --bg-panel-solid:#13294B;
  --border-line:   rgba(0, 194, 255, 0.18); /* hairline cyan borders */
  --accent:        #00C2FF;   /* primary cyan */
  --accent-dim:    rgba(0, 194, 255, 0.35);
  --warn:          #F2994A;   /* High risk / flagged objects */
  --critical:      #EB5757;   /* Critical risk */
  --safe:          #27AE60;   /* Low risk / nominal */
  --medium:        #F2C94C;   /* Medium risk */
  --text-primary:  #E8EEF7;
  --text-secondary:#8FA3BF;
  --tricolor-saffron:#FF9933; --tricolor-green:#138808; /* header accent line only */
}
```

Risk tier → color mapping is fixed: Critical=`--critical`, High=`--warn`, Medium=`--medium`, Low=`--safe`. Use these tokens exclusively; no ad-hoc hex values in components.

### 9.2 Typography (free Google Fonts; load in `index.html`)

- **Data/telemetry font:** `IBM Plex Mono` — ALL numbers, timestamps, NORAD IDs, coordinates, Pc values, delta-v. Numbers in mono is the single biggest "mission control" tell; do not use a proportional font for any numeric value.
- **UI font:** `Inter` — labels, brief text, buttons.
- **Section labels / eyebrows:** Inter 11px, `text-transform: uppercase`, `letter-spacing: 0.12em`, color `--text-secondary`. Example: `THREAT FEED`, `ASSET STATUS`, `WATCH BRIEF`.
- No font sizes below 11px; brief body text 14px/1.6.

### 9.3 Layout — full-screen globe with overlay panels (NOT a boxed grid)

The Cesium globe is the full-viewport base layer. All panels float above it as translucent overlays (`--bg-panel`, `backdrop-filter: blur(8px)` with a solid `--bg-panel-solid` fallback if blur hurts performance). ASCII wireframe:

```
┌──────────────────────────────────────────────────────────────────────┐
│ HEADER BAR (48px, full width, solid bg, tricolor 2px bottom border)  │
│  ◉ ANTARIKSHA-RAKSHA · SPACE DEFENSE WATCH      12:41:07 UTC  ●SYS  │
├────────────┬─────────────────────────────────────────┬──────────────┤
│ THREAT     │                                         │ ASSET STATUS │
│ FEED       │                                         │ (right panel │
│ (left      │            FULL-SCREEN                  │  280px wide) │
│  panel,    │            CESIUM GLOBE                 │              │
│  340px)    │                                         │  TIER 1  ●●  │
│            │                                         │  TIER 2  ●●● │
│  [event]   │                                         │  TIER 3  ●●  │
│  [event]   │                                         │              │
│  [event]   │                                         ├──────────────┤
│            │                                         │ (empty until │
│            │   ┌───────────────────────────────┐     │  selection)  │
│            │   │ EVENT DETAIL DRAWER (slides   │     │              │
│            │   │ up from bottom on selection)  │     │              │
└────────────┴───┴───────────────────────────────┴─────┴──────────────┘
```

- Left panel (`EventFeed`) and right panel (`AssetPanel`) are fixed-position overlays with 16px inset from viewport edges, rounded 10px, 1px `--border-line` border.
- `EventDetail` is a bottom drawer (max-height 45vh, scrollable) that slides up (200ms ease-out) when an event is selected; a ✕ closes it. On selection, also fly the Cesium camera to the involved asset (`camera.flyTo`, 1.5 s).
- Minimum supported width 1280px (demo is a laptop screen recording; responsive mobile not required — note this in README).

### 9.4 Header bar (part of `App.jsx`)

Left: a 10px pulsing cyan dot (CSS `@keyframes` opacity pulse, 2s) + wordmark `ANTARIKSHA-RAKSHA` (Inter 700, letter-spacing 0.08em) + `· SPACE DEFENSE WATCH` in `--text-secondary`. Right: live UTC clock (IBM Plex Mono, updates every second, format `HH:MM:SS UTC`) + system status chip: `● SYSTEM NOMINAL` in `--safe` when `/api/health` is ok, `● DEGRADED — TEMPLATE BRIEFS` in `--warn` when ollama_reachable is false, `● OFFLINE CACHE` info chip when using_cache. Poll `/api/health` every 30 s. Bottom border: 2px gradient saffron → white → green (the only place the tricolor appears — restraint).

### 9.5 GlobeView

- Poll `/api/objects` every 10 s; update entity positions (do not recreate entities each poll — mutate positions to avoid flicker).
- Point styling: Indian Tier 1 = cyan, 10px, with a 16px `--accent-dim` outline glow; Tier 2/3 = cyan 7px; debris = `#5A6B85` 4px; Group C foreign sats = white 6px. Any object involved in a pending event: override to `--warn` (or `--critical` for Critical tier) with a CSS-driven or per-frame size pulse; if pulsing proves fiddly in Cesium, a static larger (12px) orange point + label is the accepted fallback.
- Labels: show name labels only for Tier 1 assets and flagged objects (IBM Plex Mono 11px, `--text-primary`, 2px offset) — labeling everything makes noise.
- For each pending collision event, draw a dashed orange polyline between the two objects' current positions (Cesium `PolylineDashMaterialProperty`). This one element makes screenshots instantly legible — do not skip it.
- Disable Cesium's default UI chrome (timeline, animation widget, base layer picker, credits container moved to a corner) for a clean look.

### 9.6 EventFeed (left panel)

- Eyebrow header `THREAT FEED` + live count chip (`3 PENDING`, mono).
- Each row: left 3px color bar in risk-tier color; line 1 = `{RISK_TIER}` chip (11px uppercase pill, tier color bg at 15% opacity, tier color text) + event-class tag `CONJUNCTION` or `PROXIMITY` (outlined chip); line 2 = `{asset name} ⟷ {object B name}` (Inter 13px); line 3 = mono 11px: `TCA 2026-07-21 04:12 UTC · MISS 0.84 KM · Pc 1:12,400`.
- Selected row: `--accent` left bar + subtle `--accent-dim` background. Hover: background lightens 4%.
- New event appearing on a poll: 300ms fade-in + brief cyan left-bar flash. Rows sorted by priority_score DESC (as delivered by the API — do not re-sort client-side).
- Empty state (must be designed, not blank): centered radar-sweep glyph (a simple rotating conic-gradient circle in CSS) + `NO ACTIVE THREATS` + secondary line `All monitored assets nominal. Next screening pass on refresh.` + a `RUN SCREENING PASS` button wired to `/api/refresh` (disabled with spinner while running).

### 9.7 EventDetail drawer

Three-column internal layout:
1. **Telemetry column** — stacked stat blocks, each: eyebrow label + big mono value. `MISS DISTANCE 0.84 km`, `COLLISION PROBABILITY 8.1e-5` with beneath it larger `1 IN 12,400`, `RELATIVE VELOCITY 14.2 km/s`, `TCA` timestamp, `ASSET CRITICALITY TIER 1`. The "1 IN N" value should count up from 0 over 600ms on open (simple rAF animation) — this is the demo's money shot; implement it.
2. **Watch brief column** — eyebrow `AI WATCH BRIEF`; brief text (Inter 14px/1.6, max 60ch); beneath, the illustrative Δv estimate in a bordered box (implemented as "Illustrative Δv estimate"; originally specced as `RECOMMENDED MANEUVER`) with the delta-v in mono. Provenance chip at top-right of the column: `⬡ AI-DRAFTED · REVIEWER-CHECKED` (cyan outline) or `⬡ TEMPLATE FALLBACK` (gray) from `generated_by`; if reviewer_notes exist, an amber `⚠ REVIEWER FLAG` chip that expands the notes on click.
3. **Decision column** (`ApprovalPanel`) — eyebrow `OPERATOR DECISION`; two large buttons: `APPROVE ASSESSMENT` (originally specced as `APPROVE MANEUVER`; it records a decision only and commands nothing) (solid `--safe` bg, black text) and `DISMISS` (outlined, `--text-secondary`). On approve: optimistic update, button row replaced by confirmation line in mono: `✓ ASSESSMENT APPROVED · LOGGED {HH:MM:SS} UTC` in `--safe`; on dismiss, equivalent gray line. Buttons disabled with reduced opacity once decided. For proximity_watch events, the approve button reads `ACKNOWLEDGE — CONTINUE TRACKING` instead.

### 9.8 AssetPanel (right)

Eyebrow `ASSET STATUS`. Grouped by `TIER 1 — STRATEGIC`, `TIER 2 — OBSERVATION`, `TIER 3 — CIVIL`. Each asset row: status dot (`--safe` if no pending event, tier-risk color if involved in one, with pulse when Critical) + name (Inter 13px) + NORAD ID (mono 11px, `--text-secondary`). Clicking an asset flies the camera to it.

### 9.9 Micro-interaction & polish checklist (the agent must verify each)

- [ ] All numeric values everywhere are IBM Plex Mono.
- [ ] Live UTC clock ticks every second.
- [ ] Header status chip reflects `/api/health` including the Ollama-down degraded state.
- [ ] Panels are translucent over the globe with blur (or solid fallback).
- [ ] Dashed conjunction polyline renders between event pairs.
- [ ] Event selection slides the drawer up AND flies the camera.
- [ ] "1 in N" count-up animation on drawer open.
- [ ] Approve/dismiss confirmation lines with logged UTC time.
- [ ] Designed empty state with refresh action.
- [ ] Focus states visible on all interactive elements (2px `--accent` outline); `prefers-reduced-motion` disables pulse/count-up/slide animations.
- [ ] No default-styled browser buttons/scrollbars in overlay panels (style thin dark scrollbars).

### 9.10 What NOT to do

- No boxed 2×2 dashboard grid with the globe as "one of the cards" — the globe IS the screen.
- No gradient-heavy "crypto dashboard" styling, no purple, no glassmorphism beyond the specified panel blur.
- No emoji in the UI (the chips use text and simple glyphs only).
- No light theme, no theme toggle — single dark theme.
- Do not pull in a component library (MUI/Ant); hand-styled components with the tokens above.

---

## 10. Demo-Reliability Script (`scripts/seed_demo_event.py`)

If real data produces no event in the window, this script perturbs one debris object's TLE mean motion or inclination by a small, disclosed amount so it crosses an Indian asset within the demo window. [Implementation note: the implemented demo aligns one ingested object to the asset's orbit with a small along-track offset and stores it as a separate demo override — real catalog rows are never modified — and flags resulting events `is_demo` / DEMO SCENARIO.] Requirements: clearly logged as a simulation adjustment; narrated honestly in the demo video ("we've adjusted one object's orbit slightly to guarantee a conjunction event for this walkthrough"); never the default path. Optionally also provide `--proximity` mode that nudges one Group C satellite to co-orbit an Indian asset to demo the threat module.

---

## 11. README Requirements

The agent must write a README containing: project positioning paragraph (Section 0), architecture diagram (ASCII acceptable), quickstart (backend, Ollama pull command, frontend), the offline-demo procedure (run once online to cache TLEs → disconnect → full demo works), Limitations section (Section 7.4 items), and Future Work (full catalog scale, indigenous sensor ingestion, covariance-based Pc from CDMs, NavIC-time integration).

---

## 12. Build Order / Milestones (implement strictly in this order)

1. `db.py` + schema — verify tables exist with a throwaway check.
2. `ingest.py` + `working_set.json` + TLE caching — verify printed counts per group.
3. `propagate.py` — print one object's positions at 3 timestamps; sanity-check altitude is plausible (LEO: 300–2000 km).
4. Minimal `main.py` with `/api/objects` + `GlobeView.jsx` — **real objects moving on the globe is the highest-value first milestone.**
5. `conjunction.py` — verify ≥1 flagged event (use seed script if needed).
6. `risk_score.py` (+ tests) — Pc directionally sane; criticality weighting works.
7. `threat.py` (+ tests) — synthetic dwell detection passes.
8. `maneuver.py` — plausible small delta-v (sub-1 to low tens of m/s).
9. `brief_agent.py` — one real Ollama brief end-to-end + fallback path tested by pointing at a wrong port.
10. Remaining endpoints + `EventFeed`, `EventDetail`, `ApprovalPanel`, `AssetPanel`.
11. `/api/refresh` full wiring + `/api/health` + styling polish per Section 9 + README per Section 11.

---

## 13. Acceptance Criteria ("done" checklist)

- [ ] `python -m backend.ingest` populates `objects` with real data across all three groups, and a second run works offline from cache.
- [ ] Frontend shows a rotating 3D globe with real object positions updating, color-coded by type/criticality.
- [ ] At least one `collision_risk` event with non-trivial Pc (not exactly 0 or 1) appears, priority-sorted.
- [ ] At least one `proximity_watch` event can be produced (via real data or `--proximity` seed) and renders with dwell time and geometry.
- [ ] Event detail shows a coherent brief referencing actual names/numbers; reviewer status visible; fallback template works when Ollama is stopped.
- [ ] Approve/dismiss visibly updates status.
- [ ] `/api/refresh` runs the entire pipeline in < 30 seconds.
- [ ] `/api/health` returns green for the demo pre-flight.
- [ ] All four test files pass with seeded RNG.
- [ ] README complete per Section 11.
- [ ] Every item in the Section 9.9 micro-interaction checklist verified — the UI matches the mission-control spec (overlay layout, mono numerals, live clock, drawer + camera fly, empty state), not a generic dashboard.

---

## 14. Notes for the Implementing Agent

- Prefer clear, well-commented code over clever abstractions — judges' technical reviewers should understand it quickly.
- If a library call signature here is slightly off (APIs drift), use the closest correct equivalent and note the deviation in a comment — do not stall on minor details.
- Verify NORAD IDs / CelesTrak group names at build time; substitute equivalents with a comment if any object has decayed.
- Stop and ask the user only if: CelesTrak's endpoint structure has changed significantly, Ollama is not installed/reachable at all, or a design decision would materially change what the demo video shows.
