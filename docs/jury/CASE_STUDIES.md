# Visuals & Case Studies — ANTARIKSHA-RAKSHA

**Visuals:** put UI screenshots in `demo/screenshots/`. Suggested captures are listed in §4.

> **Important:** the scenarios below are **controlled demonstrations inspired by real-world event classes**. They are
> **not replays** of the historical events described. Each scenario starts from ingested real objects (public
> CelesTrak GP data) and gives one object a controlled orbit relative to a protected asset (a crossing orbit for the
> collision scenario, an along-track co-orbit for proximity), stored as a separate demo override. The real
> pipeline then runs with unchanged thresholds. Resulting events are labelled **DEMO SCENARIO**. Historical replay
> is not implemented, because authoritative historical conjunction and covariance data is not currently integrated.

---

## Case Study 1 — Fengyun-1C-class debris vs an Indian Earth-observation satellite

**Real-world basis.** The 2007 Fengyun-1C anti-satellite test created more than 3,000 catalogued fragments. It
remains one of the largest debris clouds in orbit, and much of it is in the sun-synchronous altitude regime that
Earth-observation satellites use. Close approaches between operational satellites and debris are a routine concern in
SSA operations.

**In ANTARIKSHA-RAKSHA (controlled demo).** The collision demo scenario gives one real Fengyun-1C debris object a
controlled crossing orbit relative to CARTOSAT-3: a 1.5° plane tilt, a designed miss distance of about 20 m, and a
relative velocity of about 0.2 km/s, 6–7 h ahead. Then the normal pipeline runs:

1. The pair is screened and the TCA is refined.
2. Miss distance, relative velocity and a **simplified analytic encounter-plane Pc indicator** are evaluated at that TCA.
3. The risk tier is assigned from Pc. Asset criticality affects only the priority ranking.
4. A brief is drafted, with a second-pass LLM consistency review.
5. An **illustrative Δv estimate** is shown. It is not a maneuver recommendation.
6. If Telegram alerts and demo alerts are both explicitly enabled, an alert headed DEMO — CONTROLLED SIMULATION is
   sent (off by default).

The event is labelled DEMO SCENARIO throughout.

**Why it matters.** The scenario shows how catalog data can become a prioritised, explained item that a human reviews.
It also shows the limits plainly: TLE accuracy, an assumed (non-covariance) uncertainty, and a Pc that is a simplified
indicator, not an operational covariance-based Pc.

## Case Study 2 — Sustained proximity near a protected satellite

**Real-world basis.** Rendezvous-and-proximity operations near other spacecraft have been publicly reported in recent
years, including in GEO. The pattern that matters is not a single close pass but *sustained dwell*: staying within
tens of kilometres for an extended period.

**In ANTARIKSHA-RAKSHA (controlled demo).** The proximity demo scenario aligns one real active satellite, selected by
orbital-regime similarity, to RISAT-2BR1's orbit with a ~10 km along-track offset. The dwell detector flags it as a
`proximity_watch` event when the satellite stays within the 25 km watch radius for at least 10 consecutive 1-minute
grid samples (reported dwell = elapsed time between the first and last sample). It also classifies the approach geometry (closing / station-keeping / receding), and the separation-profile
chart shows the near-constant-distance signature. A brief fly-through is not flagged, which is covered by an
automated test. No Pc is computed for proximity events.

**Why it matters, and the ethics.** The brief language is intent-neutral. It reports measured distances, durations
and geometry, and recommends human analyst review. These are proximity patterns near protected assets, not
accusations. The objects were chosen for their orbits, not their nationality.

## Case Study 3 — Lessons from the 2009 Iridium 33 / Cosmos 2251 collision

**Real-world basis.** The 2009 collision between Iridium 33 and the defunct Cosmos 2251 was the first accidental
collision between two intact satellites, and it produced on the order of 2,000 catalogued fragments. Public reporting
afterwards indicated that the pair had not been flagged as a high-priority warning beforehand. This case is widely
cited as a lesson in conjunction screening and prioritisation.

**In ANTARIKSHA-RAKSHA.** The prototype explores design responses to that general class of problem:

- a feed sorted by priority
- plain-language briefs next to the raw numbers
- clear data-freshness and provenance labelling
- a mandatory, recorded human decision in a persistent audit trail

The prototype does not show that it would have predicted or prevented this or any other real event. It is not a
replay of it.

---

## 4. Suggested screenshot set for `demo/screenshots/`

1. `console_overview.png`: the full UI, with the globe, event feed, asset panel and **mode chip** (LIVE DATA).
2. `live_data_panel.png`: the LIVE DATA status panel, showing source, ingest time, TLE age and object counts.
3. `event_drawer.png`: an open event with the provenance panel, the AI brief and its provenance chip, and the decision controls.
4. `separation_chart.png`: separation profile and data-confidence indicator.
5. `rejection_reason.png`: the dismiss flow with the reason textbox.
6. `analytics_panel.png`: decision analytics and audit history.
7. `demo_scenario.png`: a controlled demo event clearly labelled DEMO SCENARIO / DEMO MODE.
8. `degraded_mode.png` (optional): Ollama stopped, showing the deterministic template fallback.
