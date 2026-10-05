# Factual Validation Report — Jury Demonstration Video

Every factual statement in the narration and every value on screen was checked against (a) the deployed
application's own API responses recorded during the capture session on 5 Oct 2026, (b) the unedited screenshots, and
(c) the project source and README. **Result: 19 / 19 claims verified; no fabricated values,
UI or telemetry.**

| # | Claim in the narration / on screen | Evidence | Status |
|---|---|---|---|
| 1 | Uses public orbital data from CelesTrak | status.source = "CelesTrak public GP/TLE data (celestrak.org)" | ✓ verified |
| 2 | Propagates objects with SGP4 | Assessment basis: "ENGINE SGP4 (via Skyfield)"; backend/propagate.py | ✓ verified |
| 3 | 103 objects; 72 h horizon; 60 s step; 1 012 candidate pairs | status: object_count 103, window 72 h, step 60 s, candidate_pairs 1012 | ✓ verified |
| 4 | Automatic refresh every two hours | scheduler.cadence_label = "every 2 hours", enabled = True | ✓ verified |
| 5 | Tracks are SGP4-propagated predictions, not sensor telemetry | UI label "TIME-LAPSE 60× · SGP4-PROPAGATED TRACKS"; assessment basis: radar/optical observations "not connected" | ✓ verified |
| 6 | Controlled geometry derived from ingested orbital data; not an operational warning | UI banner "DEMO SCENARIO — Controlled geometry created from orbital data … not an operational collision warning"; event is_demo = 1; FENGYUN 1C demo_adjusted = true | ✓ verified |
| 7 | CARTOSAT-3 and a Fengyun-1C debris fragment | provenance.data object_a CARTOSAT-3 (satellite), object_b FENGYUN 1C (debris) | ✓ verified |
| 8 | Miss distance 20 m at TCA; relative velocity 0.2 km/s | miss_distance_km 0.0201; rel_velocity_km_s 0.1991; TCA 2026-10-05T23:59:59.986434+00:00 | ✓ verified |
| 9 | Deterministic analytic encounter-plane indicator | provenance.probability.deterministic = True; method: "Simplified analytic encounter-plane indicator: the relative …" | ✓ verified |
| 10 | Simplified analytic indicator — NOT an operational covariance-based Pc | provenance.probability.label = "Simplified analytic collision-probability indicator -- NOT an operational covariance-based Pc" | ✓ verified |
| 11 | Simplified isotropic uncertainty; 20 m hard-body radius | sigma_km 0.1132 per object; hard_body_radius_km 0.02; uncertainty_model: "Simplified prototype estimate: isotropic Gaussian position sigma per o…" | ✓ verified |
| 12 | Monte Carlo is not the production calculation; kept only as a test reference | provenance.probability.samples = None; validation_reference = "Monte Carlo sampling is kept only as a test reference for the analytic formula" | ✓ verified |
| 13 | Critical tier; criticality raises priority for ordering only | risk.tier Critical; criticality Tier2 ×2; priority_basis pc_x_criticality; priority 15422.616 | ✓ verified |
| 14 | AI does not calculate orbit, TCA, miss distance, Pc; does not command a spacecraft | provenance.ai.involved_in_numerical_calculation = False; UI "AI DID NOT" list; no command endpoint exists (README, backend/main.py) | ✓ verified |
| 15 | On the hosted deployment the AI layer is unavailable; deterministic template shown | provenance.ai.used = False, generated_by = fallback_template; header "AI FALLBACK ACTIVE"; /api/health ai.status AI_FALLBACK_ACTIVE | ✓ verified |
| 16 | Decision recorded with account and role; no command sent | decision approved at 2026-10-05T17:40:24.554345+00:00 by ADMINISTRATOR; UI "Records an operator decision in the audit trail. No command is sent to any spacecraft." | ✓ verified |
| 17 | Weaker for slow / co-orbital encounters | provenance.probability.limitations: "Short-encounter assumption … weak for slow or co-orbital encounters" | ✓ verified |
| 18 | No authoritative operational validation; future work (CDM, covariance, validation) | README §16–17; assessment basis: "Covariance / CDM — not available" | ✓ verified |
| 19 | Not a replacement for ISRO / IS4OM / NETRA; no partnerships | README key facts; concept note §1 | ✓ verified |

## Wording decisions

- The brief suggested "Monte Carlo was used only as a validation/reference technique during development". An earlier
  version of the prototype did use a 5 000-sample Monte Carlo as its production estimator (see README "Before / after"),
  so that sentence would have been inaccurate. The narration instead says Monte Carlo "is not the production
  calculation; it is kept only as a test reference", which matches the provenance field quoted above.
- The tracks are described as "SGP4-propagated predictions … not continuous sensor-level live telemetry".
- The collision event is explicitly a controlled DEMO scenario. The corner label "CONTROLLED DEMO SCENARIO — NOT AN
  OPERATIONAL WARNING" stays on screen throughout 1:47–3:37.
- The AI layer is shown honestly as unavailable on the hosted deployment (deterministic template).
- Future work is visually labelled "not yet built".

## Production side effects (disclosed)

Capturing the demonstration required these real actions on the deployed system, all approved by the project owner:
"Exit demo — refresh live data" (17:39 UTC), one controlled collision demo (screening run #103, event 98), one
**Approve** decision on that DEMO event (17:40:24 UTC, recorded in the audit trail), and a final "Exit demo". The final
refresh fell back to the server's TLE cache, so the console showed **CACHED DATA** afterwards (an honest label; the
next successful scheduled ingest restores LIVE DATA).

## Illustrative content

Scenes 1–3 and 10–12 are explanatory graphics and slides, not application output. The thought-experiment orbits are
schematic and not to scale.
