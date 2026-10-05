# ANTARIKSHA-RAKSHA — Jury Demonstration: Narration Script

Final duration **4:48.8** (mm:ss). Narration voice: Microsoft neural text-to-speech, `en-IN-NeerjaNeural` (Indian English, female), rate +18 %. Timecodes are the exact sentence starts in the final video.

## Thought experiment  ·  0:01.2 – 0:40.1

- `0:01.2` Before I show you my solution, let me start with a thirty-second thought experiment.
- `0:05.9` Imagine these are two satellites. Their predicted orbital paths cross.
- `0:10.8` Do these two satellites necessarily collide?
- `0:13.4` Not necessarily. They also have to reach that location at approximately the same time.
- `0:19.2` That is one of the fundamental ideas behind conjunction assessment.
- `0:23.0` Now imagine doing this repeatedly, for a changing population of space objects.
- `0:27.8` We need to know where they will be, when they will be closest, how close they will get, how uncertain that prediction is, and which event deserves attention first.
- `0:36.4` That is the problem ANTARIKSHA-RAKSHA is designed to support.

## The problem  ·  0:40.1 – 0:59.6

- `0:40.1` Space is becoming increasingly crowded.
- `0:42.5` The challenge is not simply tracking objects. It is turning changing orbital information into a prioritised, explainable and auditable decision-support workflow.
- `0:52.2` Think of space as a three-dimensional highway: vehicles travelling at several kilometres per second, with no visible lanes.

## Seven-stage workflow  ·  0:59.6 – 1:19.3

- `0:59.6` ANTARIKSHA-RAKSHA follows seven stages: see, predict, detect, understand, prioritise, explain, and decide.
- `1:06.8` Data comes in. Physics processes it. Risk prioritises it. AI explains it. A human decides. And the audit trail records what happened.

## Live data  ·  1:19.3 – 1:47.2

- `1:19.3` This is the deployed prototype. It uses public orbital data from CelesTrak, and propagates the objects forward using SGP4.
- `1:28.0` It screens one hundred and three objects over a seventy-two-hour horizon at a sixty-second step, one thousand and twelve candidate pairs, refreshed automatically every two hours.
- `1:37.5` The displayed tracks are SGP4-propagated predictions based on the latest available orbital data. This is not continuous sensor-level live telemetry.

## Conjunction assessment  ·  1:47.2 – 2:17.8

- `1:47.2` For demonstration purposes, the prototype can create controlled geometry derived from ingested orbital data.
- `1:53.6` This is a controlled simulation for demonstrating the decision workflow. It is not an operational warning.
- `1:59.9` Here, screening flags CARTOSAT-3 and a Fengyun-1C debris fragment.
- `2:04.6` TCA means Time of Closest Approach: the predicted moment when the separation between the two objects is smallest.
- `2:11.2` The miss distance at TCA is twenty metres, at a relative velocity of zero point two kilometres per second.

## Collision-probability indicator  ·  2:17.8 – 2:44.9

- `2:17.8` The prototype calculates a deterministic collision-probability indicator using an analytic encounter-plane model.
- `2:24.1` This is a simplified analytic collision-probability indicator. It is NOT an operational covariance-based collision probability.
- `2:31.9` It uses simplified isotropic uncertainty, not authoritative operational covariance, and a twenty-metre hard-body radius.
- `2:39.2` Monte Carlo is not the production calculation; it is kept only as a test reference.

## Risk prioritisation  ·  2:44.9 – 2:58.1

- `2:44.9` The event is placed in the Critical tier, and asset criticality raises its priority, for ordering only.
- `2:50.8` The purpose is not simply to produce a number. It is to help answer: which event deserves attention first?

## AI explanation boundary  ·  2:58.1 – 3:23.9

- `2:58.1` AI does not calculate the orbit. AI does not calculate TCA, miss distance or collision probability. AI does not command a spacecraft.
- `3:08.3` AI receives the already-computed backend evidence, and helps turn it into a concise, analyst-oriented explanation.
- `3:15.0` On this hosted deployment the AI layer is unavailable, so a labelled deterministic template is shown, and the physics and risk assessment remain available.

## Human decision and audit  ·  3:23.9 – 3:37.1

- `3:23.9` The operator can approve the assessment, or dismiss it with a reason.
- `3:27.8` The final decision remains with the human operator, recorded in the audit trail with the account and role that made it.
- `3:34.1` No command is sent to any spacecraft.

## India and innovation  ·  3:37.1 – 4:03.5

- `3:37.1` For India, the opportunity is not to replace established orbital tracking or space-situational-awareness capabilities.
- `3:43.6` It is to build complementary, modular and sovereign software layers that help transform orbital evidence into explainable, prioritised and auditable decision support.
- `3:52.8` The innovation is in integrating deterministic orbital evidence, transparent risk reasoning, local AI explanation, human governance and auditability into one demonstrable workflow.

## Limitations and future evolution  ·  4:03.5 – 4:31.0

- `4:03.5` These are transparent engineering boundaries: public TLE data; simplified isotropic uncertainty instead of operational covariance; a fixed hard-body radius, weaker for slow co-orbital encounters; no operational validation; and controlled demo scenarios. It is decision support, not spacecraft control.
- `4:22.4` Next steps: covariance and CDM integration, richer catalogue feeds, higher-fidelity uncertainty propagation, and operational validation.

## Closing  ·  4:31.0 – 4:48.8

- `4:31.0` Space safety is not only about tracking what is in orbit.
- `4:34.5` It is about understanding what may happen next — and helping the right human make the right decision at the right time.
- `4:40.4` ANTARIKSHA-RAKSHA.
- `4:41.6` Physics first. AI second. Human decision last.
