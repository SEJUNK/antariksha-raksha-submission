# Jury Presentation — Narration and Speaker Notes

Final jury deck: [`ANTARIKSHA-RAKSHA_Jury_Presentation.pptx`](ANTARIKSHA-RAKSHA_Jury_Presentation.pptx) (15 slides; PDF copy alongside). Built on the supplied jury deck design with targeted changes: two new slides (4 and 13), a Telegram notification-only note on slide 10, renumbering, and speaker notes on every slide.

**Audio.** Each slide carries its own embedded narration clip (MP3), set to start automatically with the slide and to stop when the slide changes; the small speaker icon at the bottom right gives play/pause, mute and volume control in Slide Show. Voice: neural en-IN (Neerja) via the free `edge-tts` tool, the same voice as the jury video; no paid API or key. Total narration: 9.4 minutes.

The speaker notes in the deck follow the structure A. What is shown · B. What to say · C. Jury takeaway · D. Caveat. Section B is the narration text below.

## 01 · Title  (25 s)

Imagine two objects crossing the same orbital highway. It looks dangerous — but crossing paths alone does not mean a collision. The real question is whether they arrive at the same place at the same time. ANTARIKSHA-RAKSHA is built around answering that question — with physics first, AI second, and a human making the final decision.

- **What is shown:** Title: ANTARIKSHA-RAKSHA, a Space Domain Awareness and conjunction-assessment decision-support prototype; Physics first / AI second / Human decision last.
- **Jury takeaway:** A defence-relevant Space Domain Awareness (SDA) and conjunction-assessment decision-support prototype, built on one principle: physics first, AI second, human decision last.
- **Caveat:** Research/demo decision-support prototype; the hosted deployment is a jury validation environment, not an operational service.

## 02 · The thought experiment  (31 s)

Orbit is a three-dimensional highway with no visible lanes. Satellites and debris move at several kilometres per second, and their paths cross all the time. Crossing paths alone is not a collision. If the two objects reach the crossing at different times, they pass safely. Only when they arrive at the same place at about the same time do we have a conjunction — and that is what must be predicted, measured and explained.

- **What is shown:** Thought experiment: two crossing orbits; different time means a safe pass, same place and same time means a conjunction.
- **Jury takeaway:** The core problem is timing in three dimensions, not just intersecting paths.
- **Caveat:** The orbit illustration is schematic and not to scale.

## 03 · The problem  (31 s)

Space is becoming increasingly active. More objects share busy orbital regimes, and every new orbital update can shift a prediction. Tracking objects is necessary, but it is not enough. Analysts need to know which close approaches matter and why, and they need a record of what was decided. The challenge is turning changing orbital information into a prioritised, explainable and auditable workflow.

- **What is shown:** The problem: more objects, changing information, analysts need focus.
- **Jury takeaway:** The gap addressed is decision support: prioritisation, explanation and auditability on top of tracking.
- **Caveat:** No claim is made that existing systems lack these functions; this is a prototype exploring the workflow.

## 04 · Why India, why now? (new)  (37 s)

Why India, and why now? India's space ecosystem is expanding, with more missions and more operators. Orbital congestion and space-traffic complexity are increasing, and space assets increasingly matter to both development and national security. India already has the foundations of space situational awareness. ANTARIKSHA-RAKSHA explores how an explainable, human-gated decision-support layer could help scale the analyst experience as India's space ecosystem and orbital complexity grow.

- **What is shown:** NEW. Why India, why now: four drivers (expanding ecosystem, congestion, importance of space assets, need for capable decision support) and the positioning statement.
- **Jury takeaway:** India already has national SSA capabilities. This prototype explores a complementary decision-support layer.
- **Caveat:** Not a replacement for, and not integrated with, ISRO, IS4OM, NETRA or DRDO systems. Official context for the speaker: ISRO Indian Space Situational Assessment Reports (https://www.isro.gov.in/Indian_Space_Situational_Awareness_Report_2025.html ; https://www.isro.gov.in/ISSAR_2024.html ; https://www.isro.gov.in/Indian_Space_Situational_Assessment_Report_ISSAR2023.html), Indian Space Policy 2023 (https://new1.isro.gov.in/media_isro/pdf/IndianSpacePolicy2023.pdf) and Debris-Free Space Missions (https://new1.isro.gov.in/ISRO_EN/Debris_Free_Space_Missions.html).

## 05 · The seven-step workflow  (37 s)

The system is organised around seven questions. See: which objects matter? Predict: when will they come closest? Detect: how close will they be? Understand: what is the estimated risk? Prioritise: which event comes first? Explain: how is the evidence explained? And decide: who makes the final call? The early steps are deterministic physics, the next are risk and AI explanation, and the last always belongs to a human.

- **What is shown:** Seven-step workflow: SEE, PREDICT, DETECT, UNDERSTAND, PRIORITISE, EXPLAIN, DECIDE.
- **Jury takeaway:** SEE → PREDICT → DETECT → UNDERSTAND → PRIORITISE → EXPLAIN → DECIDE; the decision is always human.
- **Caveat:** Each step is implemented at prototype level on public data.

## 06 · How ANTARIKSHA-RAKSHA works  (43 s)

Here is the end-to-end flow. Public orbital data from CelesTrak is validated and recorded with its provenance. The physics layer propagates every object with SGP4, screens candidate pairs, refines the time of closest approach, and computes miss distance, relative velocity and a simplified collision-probability indicator. Only then are events prioritised and explained, by a local AI when enabled, with a deterministic fallback. Finally, a human decides, and every decision is audited. No command is ever sent to a spacecraft.

- **What is shown:** End-to-end flow: data, deterministic physics, risk plus AI, human decision and audit; no spacecraft commands.
- **Jury takeaway:** Numbers come first from physics; explanation comes after; the human decides last.
- **Caveat:** Telegram, where enabled, is only a notification channel after events are stored; it is not part of this computation.

## 07 · Physics first  (42 s)

Every number comes from deterministic physics. SGP4 propagates public orbital elements across a seventy-two hour horizon at sixty-second steps. Candidate pairs within ten kilometres are screened, and the time of closest approach is refined by bounded minimisation on re-evaluated SGP4 states. Miss distance and relative velocity are taken at that refined instant. The collision figure is a deterministic analytic encounter-plane indicator with simplified, isotropic uncertainty. It is not an operational covariance-based collision probability.

- **What is shown:** Physics first: SGP4, screening, refined TCA, miss distance, relative velocity, analytic collision indicator; encounter-plane schematic.
- **Jury takeaway:** Deterministic, reproducible physics: same inputs, same numbers.
- **Caveat:** Pc is a simplified analytic indicator, not an operational covariance-based collision probability. Per-object sigma is 0.1 km + 0.05 km per day to TCA (relative in-plane sigma = sqrt(2) x sigma); a 3D non-central chi-square fallback is used at very low relative velocity. Monte Carlo is only a test reference, not production scoring.

## 08 · Risk + explainability  (40 s)

Which event deserves attention first, and why? Deterministic evidence — time of closest approach, miss distance, relative velocity, the collision indicator and data provenance — places each event in a Low, Medium, High or Critical tier and a ranked queue. Asset criticality affects ordering, not the risk tier. The explanation then states the evidence and the reason for the priority. When AI is enabled, a second-pass consistency check reviews it; if AI is unavailable, a deterministic explanation is shown.

- **What is shown:** Risk and explainability: deterministic evidence, ranked event queue, human-readable assessment, deterministic fallback.
- **Jury takeaway:** Prioritisation is evidence-based and every priority comes with a reason.
- **Caveat:** Pc is a simplified analytic indicator, not an operational covariance-based collision probability. The second-pass check uses the same local model and is not independent validation.

## 09 · AI boundary  (44 s)

Where does AI stop? AI is not used to perform orbital mechanics. The deterministic physics pipeline produces the evidence; AI converts that evidence into a concise analyst-oriented explanation, with fact checking, guardrails and human approval. It reduces cognitive load and keeps briefings consistent. It does not calculate positions, closest approach, miss distance or collision probability, it cannot override backend risk, and it never commands a spacecraft. On the hosted jury environment, AI is disabled and deterministic explanations are used.

- **What is shown:** AI boundary: what AI does (explain) and does not do (compute, command).
- **Jury takeaway:** AI is intentionally downstream of deterministic physics.
- **Caveat:** Local AI is optional (Ollama, llama3.2:3b). A deterministic fact check flags wrong numbers; a tone guard replaces sensational drafts with the deterministic template. Hosted AI is disabled.

## 10 · Human governance + audit  (42 s)

Who makes the final decision? The analyst or operator reviews the evidence and the explanation, then approves, acknowledges or dismisses the assessment. Every decision is recorded with the acting account and role in a persistent audit trail, and access is controlled by four roles. Once a significant event is assessed, the system can notify the operator through Telegram. But the notification channel has no decision authority: the operator returns to the console to review the evidence and record the human decision, which is then audited. No command is sent to any spacecraft.

- **What is shown:** Human governance and audit: analyst or operator decides (approve, dismiss, acknowledge), persistent audit trail, four RBAC roles, Telegram = notification only.
- **Jury takeaway:** Telegram extends the system's reach to the operator, but it does not extend the system's authority.
- **Caveat:** Telegram is an external notification channel only. It does not participate in orbital mechanics or risk calculation and cannot approve assessments or command spacecraft. Flow: deterministic assessment → stored event → notification decision → Telegram alert → operator opens the console → human decision → audit. Critical and High notify by default, Low never; alerts are de-duplicated per event track and repeated only on escalation, at most 5 per run; every attempt is audited without credentials; DEMO alerts are off by default and otherwise headed DEMO — CONTROLLED SIMULATION. Enabled in the current hosted jury environment; delivery depends on the external Telegram service. The audit log is local SQLite with no tamper protection.

## 11 · Working prototype  (45 s)

This is not a mock-up. The prototype is deployed: a Vercel frontend, a Railway backend with a persistent database, a two-hour public-data refresh, and authenticated, role-based access. You can see the 3D orbital view, the live data and event feed, the conjunction risk assessment, the evidence-based brief and the recorded human decision. Significant events can also notify the operator through Telegram. The collision shown is a controlled demonstration scenario derived from ingested orbital data — CARTOSAT-3 and a Fengyun-1C debris fragment. It is not an operational warning.

- **What is shown:** Working prototype: real screenshots (3D orbital view, live data and event feed, conjunction risk, evidence-based brief, human decision and audit) and the deployment stack.
- **Jury takeaway:** An end-to-end working prototype on real public orbital data, validated by 815 automated tests (652 backend, 1 skipped; 163 frontend).
- **Caveat:** This is a controlled demonstration scenario derived from ingested orbital data; it is not an operational collision warning. Real catalogue rows are not modified. The recorded video shows a Pc of about 7.71e-3; later deterministic reruns can show about 7.67e-3. Telegram notification was enabled after the recorded jury video.

## 12 · Sovereign + strategic value  (38 s)

Where does this fit? India already has significant space surveillance and tracking capabilities, and international agencies and commercial providers run sophisticated conjunction assessment. ANTARIKSHA-RAKSHA is a complementary software-layer prototype. It is modular, it can use a local AI model without any cloud dependency, it is governed by human decisions with an audit trail, and it is built so that authoritative data sources could be added later. Our contribution is the integrated workflow from evidence to audit.

- **What is shown:** Sovereign and strategic value: complementary software layer; modular, optional local AI, governed, extensible.
- **Jury takeaway:** A complementary, potentially sovereign software layer: the value is the integrated, governed workflow.
- **Caveat:** No ISRO, IS4OM, NETRA or DRDO integration, endorsement or partnership exists or is implied.

## 13 · What could this enable? (new)  (39 s)

What could this enable in the future? It could help prioritise analyst attention on the conjunctions that matter most, explain complex orbital evidence as concise and traceable assessments, support analysts as object populations and event volumes grow, and keep AI assistance bounded by deterministic evidence and human approval. These are potential outcomes, not current capabilities. They would need a stronger foundation: authoritative ephemerides, covariance and CDM integration, and institutional analyst workflows.

- **What is shown:** NEW. What could this enable: prioritise, explain, scale, govern; future foundation (authoritative ephemerides, covariance/CDM, institutional workflows).
- **Jury takeaway:** The prototype shows a pattern that could scale analyst capacity if built on authoritative data.
- **Caveat:** Everything on this slide is potential future value, not implemented capability.

## 14 · Limitations + pathway  (46 s)

We are candid about scope. Today this is a deployed prototype on public CelesTrak data, so accuracy is bounded by that source. The collision figure is a simplified indicator, not an operational covariance-based probability, and the demo uses controlled geometry. It is not a replacement for ISRO, IS4OM, NETRA or DRDO systems, and it is not a command, autonomous-avoidance or weapon system. The next steps would be independent validation, covariance-based probability where validated data exists, and integration with authoritative sources. This pathway is potential, not committed.

- **What is shown:** Limitations and pathway: now (prototype), next (validation), later (integration); prototype limitations.
- **Jury takeaway:** Honest limits and a credible pathway: validation first, then authoritative data and integration.
- **Caveat:** No operational validation has been performed; the pathway is potential, not committed.

## 15 · Closing  (23 s)

Physics first. AI second. Human decision last. ANTARIKSHA-RAKSHA turns orbital data into physics, physics into risk, risk into a clear explanation, and explanation into a recorded human decision — from orbital data to explainable decision support. Thank you.

- **What is shown:** Closing: Physics first. AI second. Human decision last. Data → physics → risk → AI explanation → human decision → audit.
- **Jury takeaway:** Physics first. AI second. Human decision last.
- **Caveat:** Research/demo decision-support prototype; not an operational service.
