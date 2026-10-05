# Screen / Source Mapping

All screenshots were captured from the deployed application https://antariksha-raksha.vercel.app/ (backend on
Railway) on **5 Oct 2026, 17:38:14–17:41:04 UTC**, signed in as the
project owner's ADMINISTRATOR account, with a fresh browser profile at 1920 × 1080. No UI, value or label was edited.
The numeric values shown were cross-checked against the backend API responses recorded at the same time
(`../video/source/capture_values.json`).

| Screenshot | Header clock (UTC) | View | Values visible | Evidence |
|---|---|---|---|---|
| `demo/screenshots/live_01_dashboard.jpg` | 17:39:20 | Main console after "Exit demo — refresh live data" | LIVE DATA; source CelesTrak public GP/TLE; last successful refresh 17:39:07 UTC; automatic refresh enabled, every 2 hours, next 18:00 UTC; 103 objects (11 Indian sat · 80 debris · 12 other active sat); TLE epoch age 0.41–15.49 d; 72 h · step 60 s; 1 012 candidate pairs; 0 collision-risk / 0 proximity events | GET /api/status → capture_log.json step live_status |
| `demo/screenshots/live_03_registry.jpg` | ≈17:39:3x | Registry & catalog panel | Protected-asset registry (Tier 1–3) | UI only |
| `demo/screenshots/demo_01_top_summary.jpg` | 17:39:53 | Collision demo event drawer (top) | DEMO SCENARIO banner; evidence trace; analyst summary (TCA 2026-10-05 23:59:59 UTC, 0.020 km, 0.20 km/s, Pc 7.71e-3 → Critical, priority 15422.616) | GET /api/events → step demo_event |
| `demo/screenshots/demo_02_summary_risk.jpg` | 17:39:56 | Event summary + risk assessment | Event summary values; Pc 7.71e-3 ≈ 1 in 130; method; σ 0.1132 km per object; hard-body radius 20 m; simplified-indicator warning | GET /api/events/98/provenance → probability |
| `demo/screenshots/demo_03_separation.jpg` | 17:39:59 | Separation profile, risk drivers, deterministic brief | Separation profile ±45 min (min 0.02 km at TCA); risk drivers; DETERMINISTIC TEMPLATE (LLM unavailable) | provenance → risk, ai |
| `demo/screenshots/demo_04_provenance.jpg` | ≈17:40:0x | Data provenance section | Object sources, TLE ages, demo-adjusted flag | provenance → data |
| `demo/screenshots/demo_06_basis_limitations.jpg` | ≈17:40:1x | Assessment basis & limitations | SGP4 (Skyfield); 72 h / 60 s from screening run #103; data coverage; limitations list | provenance |
| `demo/screenshots/demo_07_evolution.jpg` | ≈17:40:1x | Event evolution | Single DEMO observation | UI only |
| `demo/screenshots/demo_08_decision_panel.jpg` | 17:40:22 | Human decision panel (before) | APPROVE ASSESSMENT / DISMISS; "No decisions recorded for this event yet" | UI only |
| `demo/screenshots/demo_09_decision_recorded.jpg` | 17:40:28 | Human decision panel (after) | ASSESSMENT APPROVED · LOGGED 17:40:24 UTC; audit history APPROVED · ADMINISTRATOR · sejal · DEMO; AI DID / AI DID NOT | GET /api/events/98/decisions → step decisions |
| `demo/screenshots/demo_10_analytics.jpg` | 17:40:34 | Operator decision audit (Analytics) | 14 decisions · 7 accepted · 7 rejected; recent rejection reasons | GET /api/decisions/stats |
| `demo/screenshots/live_04_returned_live.jpg` | ≈17:40:5x | After exiting the demo | Console returned to real data, labelled CACHED DATA (refresh used the server's TLE cache) | step final_mode |

Video usage: `live_01_dashboard` (0:40–1:47 background and live section), `demo_01_top_summary`,
`demo_02_summary_risk`, `demo_03_separation`, `demo_08_decision_panel` and `demo_09_decision_recorded` (1:47–3:37).
The remaining screenshots are supporting evidence.
