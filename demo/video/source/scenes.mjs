// Scene list for the jury video. All screen content comes from real captures of the deployed app.
import fs from "node:fs";
import path from "node:path";
import { VIDEO_DIR } from "./cdp.mjs";

const TL = JSON.parse(fs.readFileSync(path.join(VIDEO_DIR, "timeline.json"), "utf8"));
const sec = (id) => TL.sections.find((s) => s.id === id);
const cue = (id, i) => sec(id).sentences[i].start;
const end = (id) => sec(id).end;
const startOf = (id) => sec(id).start;
const f = (p) => "file:///" + path.join(VIDEO_DIR, "shots", p).replace(/\\/g, "/");

const images = {
  live: f("live_01_dashboard.png"),
  d01: f("demo_01_top_summary.png"),
  d02: f("demo_02_summary_risk.png"),
  d03: f("demo_03_separation.png"),
  d08: f("demo_08_decision_panel.png"),
  d09: f("demo_09_decision_recorded.png"),
};
const LIVE_CHIP = "DEPLOYED APPLICATION · LIVE PUBLIC DATA · CAPTURED 5 OCT 2026, 17:39 UTC";
const DEMO_CHIP = "CONTROLLED DEMO SCENARIO — NOT AN OPERATIONAL WARNING";

const scenes = [];

// 1. Thought experiment (animation)
scenes.push({ type: "anim", start: 0, end: end("s01_thought"), cues: sec("s01_thought").sentences.map((s) => s.start), brand: false });

// 2. Problem (slide over a dimmed real globe capture)
{
  const q = (i) => cue("s02_problem", i);
  scenes.push({ type: "slide", start: startOf("s02_problem"), end: end("s02_problem"), html: `
    <div class="bg" style="background-image:url('${images.live}'); background-position: 50% 52%;"></div>
    <div class="slide">
      <div class="kicker" data-at="${q(0)}">THE PROBLEM</div>
      <div class="big" data-at="${q(0) + 0.2}">Space is becoming<br>increasingly crowded.</div>
      <div style="height:44px"></div>
      <div class="mid" data-at="${q(1)}">Not simply <span class="soft">tracking objects</span> —</div>
      <div class="mid" data-at="${q(1) + 2.2}">turning changing orbital information into a<br><span class="accent">prioritised · explainable · auditable</span> decision-support workflow.</div>
      <div style="height:54px"></div>
      <div class="row" data-at="${q(2)}">
        <div class="stage-chip">3-D HIGHWAY</div>
        <div class="stage-chip">SEVERAL KM/S</div>
        <div class="stage-chip">NO VISIBLE LANES</div>
      </div>
    </div>` });
}

// 3. Seven-stage workflow
{
  const q0 = cue("s03_solution", 0), q1 = cue("s03_solution", 1);
  const d0 = sec("s03_solution").sentences[0], d1 = sec("s03_solution").sentences[1];
  const stages = ["SEE", "PREDICT", "DETECT", "UNDERSTAND", "PRIORITISE", "EXPLAIN", "DECIDE"];
  const span0 = d0.end - d0.start;
  const chips = stages.map((s, i) => `<div class="stage-chip" data-at="${q0 + span0 * (0.38 + i * 0.087)}">${s}</div>${i < 6 ? `<div class="arrow" data-at="${q0 + span0 * (0.38 + i * 0.087)}">→</div>` : ""}`).join("");
  const lines = [["DATA", "t-data", "Data comes in."], ["PHYSICS", "t-phys", "Physics processes it."], ["RISK", "t-risk", "Risk prioritises it."],
    ["AI", "t-ai", "AI explains it."], ["HUMAN", "t-human", "A human decides."], ["AUDIT", "t-audit", "The audit trail records what happened."]];
  const span1 = d1.end - d1.start;
  const rows = lines.map((l, i) => `<div class="flowline" data-at="${q1 + span1 * (i / 6)}"><span class="tag ${l[1]}">${l[0]}</span>${l[2]}</div>`).join("");
  scenes.push({ type: "slide", start: startOf("s03_solution"), end: end("s03_solution"), html: `
    <div class="slide" style="padding-top:120px">
      <div class="kicker" data-at="${q0}">THE SOLUTION · SEVEN STAGES</div>
      <div class="row stages" style="gap:10px;flex-wrap:nowrap">${chips}</div>
      <div style="height:60px"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;column-gap:60px">${rows}</div>
    </div>` });
}

// 4. Live data (real deployed app, LIVE)
{
  const q = (i) => cue("s04_live", i);
  scenes.push({ type: "shot", start: startOf("s04_live"), end: end("s04_live"), chip: LIVE_CHIP,
    keys: [{ at: startOf("s04_live"), img: "live", rect: [0, 0, 1920] },
           { at: q(1) + 0.4, img: "live", rect: [1335, 66, 585] },
           { at: q(1) + 3.2, img: "live", rect: [1335, 318, 585] },
           { at: q(2), img: "live", rect: [380, 340, 1290] }],
    hl: [{ at: q(0) + 0.6, until: q(1) + 0.4, box: [735, 12, 102, 24], cls: "green" },
         { at: q(1) + 1.6, until: q(2), box: [1638, 100, 252, 36] },
         { at: q(1) + 2.4, until: q(2), box: [1638, 380, 252, 40] },
         { at: q(1) + 3.6, until: q(2), box: [1638, 462, 252, 36] },
         { at: q(1) + 5.0, until: q(2), box: [1638, 208, 252, 128] },
         { at: q(2) + 1.3, until: end("s04_live"), box: [736, 1034, 448, 26] }] });
}

// 5. Conjunction assessment (controlled demo)
{
  const q = (i) => cue("s05_conjunction", i);
  scenes.push({ type: "shot", start: startOf("s05_conjunction"), end: end("s05_conjunction"), chip: DEMO_CHIP, chipClass: "amber",
    keys: [{ at: startOf("s05_conjunction"), img: "d01", rect: [0, 0, 1920] },
           { at: q(1), img: "d01", rect: [372, 382, 1240] },
           { at: q(3), img: "d02", rect: [372, 470, 1000] }],
    hl: [{ at: q(0) + 0.8, until: q(1), box: [661, 12, 105, 24], cls: "amber", img: "d01" },
         { at: q(0) + 1.6, until: q(1), box: [1641, 544, 246, 116], cls: "amber", img: "d01" },
         { at: q(1) + 0.9, until: q(2), box: [393, 509, 1194, 35], cls: "amber", img: "d01" },
         { at: q(2) + 0.2, until: q(3), box: [393, 486, 420, 18], img: "d01" },
         { at: q(2) + 1.2, until: q(3), box: [400, 638, 420, 40], img: "d01" },
         { at: q(3) + 1.2, until: q(4), box: [393, 626, 200, 26], img: "d02" },
         { at: q(4) + 0.2, until: end("s05_conjunction"), box: [393, 664, 175, 26], img: "d02" },
         { at: q(4) + 1.4, until: end("s05_conjunction"), box: [393, 703, 175, 26], img: "d02" },
         { at: q(4) + 2.6, until: end("s05_conjunction"), box: [393, 742, 300, 122], img: "d02" }] });
}

// 6. Collision-probability indicator
{
  const q = (i) => cue("s06_pc", i);
  scenes.push({ type: "shot", start: startOf("s06_pc"), end: end("s06_pc"), chip: DEMO_CHIP, chipClass: "amber",
    keys: [{ at: startOf("s06_pc"), img: "d02", rect: [985, 495, 620] }],
    hl: [{ at: q(0) + 0.3, until: q(1), box: [1008, 536, 110, 44] },
         { at: q(0) + 1.4, until: q(1), box: [1008, 585, 568, 86] },
         { at: q(1) + 0.3, until: q(2), box: [1008, 786, 572, 26], cls: "amber" },
         { at: q(2) + 0.2, until: q(3), box: [1008, 743, 568, 34] },
         { at: q(2) + 1.8, until: q(3), box: [1008, 726, 150, 16] },
         { at: q(3) + 0.2, until: end("s06_pc"), box: [1008, 675, 280, 16] }] });
}

// 7. Risk prioritisation
{
  const q = (i) => cue("s07_priority", i);
  scenes.push({ type: "shot", start: startOf("s07_priority"), end: end("s07_priority"), chip: DEMO_CHIP, chipClass: "amber",
    keys: [{ at: startOf("s07_priority"), img: "d01", rect: [380, 520, 980] },
           { at: q(1), img: "d03", rect: [990, 560, 620] }],
    hl: [{ at: q(0) + 0.3, until: q(1), box: [400, 686, 560, 32], img: "d01" },
         { at: q(0) + 2.4, until: q(1), box: [1008, 840, 420, 18], img: "d01" },
         { at: q(1) + 0.6, until: end("s07_priority"), box: [1004, 732, 320, 46], img: "d03" }] });
}

// 8. AI explanation boundary
{
  const q = (i) => cue("s08_ai", i);
  scenes.push({ type: "shot", start: startOf("s08_ai"), end: end("s08_ai"), chip: DEMO_CHIP, chipClass: "amber",
    keys: [{ at: startOf("s08_ai"), img: "d09", rect: [395, 495, 920] },
           { at: q(2), img: "d03", rect: [380, 388, 1230] }],
    hl: [{ at: q(0) + 0.4, until: q(1), box: [992, 566, 300, 128], img: "d09" },
         { at: q(1) + 0.2, until: q(2), box: [402, 543, 456, 16], img: "d09" },
         { at: q(2) + 0.6, until: end("s08_ai"), box: [393, 898, 318, 24], cls: "amber", img: "d03" },
         { at: q(2) + 1.6, until: end("s08_ai"), box: [393, 928, 410, 16], cls: "amber", img: "d03" },
         { at: q(2) + 3.2, until: end("s08_ai"), box: [393, 975, 1190, 76], img: "d03" }] });
}

// 9. Human decision and audit
{
  const q = (i) => cue("s09_decision", i);
  scenes.push({ type: "shot", start: startOf("s09_decision"), end: end("s09_decision"), chip: DEMO_CHIP, chipClass: "amber",
    keys: [{ at: startOf("s09_decision"), img: "d08", rect: [380, 383, 1240] },
           { at: q(1), img: "d09", rect: [390, 411, 1190] }],
    hl: [{ at: q(0) + 0.3, until: q(1), box: [406, 482, 574, 40], cls: "green", img: "d08" },
         { at: q(0) + 1.8, until: q(1), box: [406, 533, 574, 40], img: "d08" },
         { at: q(1) + 0.3, until: end("s09_decision"), box: [404, 746, 345, 22], cls: "green", img: "d09" },
         { at: q(1) + 1.6, until: end("s09_decision"), box: [1000, 767, 470, 26], img: "d09" },
         { at: q(2) + 0.1, until: end("s09_decision"), box: [404, 773, 420, 16], cls: "amber", img: "d09" }] });
}

// 10. India and innovation
{
  const q = (i) => cue("s10_india", i);
  scenes.push({ type: "slide", start: startOf("s10_india"), end: end("s10_india"), html: `
    <div class="bg" style="background-image:url('${images.live}'); background-position: 50% 58%;"></div>
    <div class="slide" style="padding-top:100px">
      <div class="kicker" data-at="${q(0)}">FOR INDIA</div>
      <div class="mid" data-at="${q(0) + 0.3}"><span class="soft">Not a replacement for established orbital-tracking and SSA capabilities.</span></div>
      <div style="height:26px"></div>
      <div class="big" data-at="${q(1)}" style="font-size:54px">A complementary, modular and sovereign<br><span class="accent">software layer</span> for explainable, prioritised,<br>auditable decision support.</div>
      <div style="height:56px"></div>
      <div class="kicker" data-at="${q(2)}">THE INNOVATION · INTEGRATION</div>
      <div class="eq">
        <span class="p" data-at="${q(2) + 0.6}">Deterministic orbital evidence</span><span class="op" data-at="${q(2) + 0.6}">+</span>
        <span class="p" data-at="${q(2) + 1.8}">Transparent risk reasoning</span><span class="op" data-at="${q(2) + 1.8}">+</span>
        <span class="p" data-at="${q(2) + 3.0}">Local AI explanation</span><span class="op" data-at="${q(2) + 3.0}">+</span>
        <span class="p" data-at="${q(2) + 4.1}">Human governance</span><span class="op" data-at="${q(2) + 4.1}">+</span>
        <span class="p" data-at="${q(2) + 5.0}">Auditability</span><span class="op" data-at="${q(2) + 6.0}">=</span>
        <span class="p res" data-at="${q(2) + 6.2}">One demonstrable decision-support workflow</span>
      </div>
    </div>` });
}

// 11. Limitations and future evolution
{
  const q = (i) => cue("s11_limits", i);
  const d0 = sec("s11_limits").sentences[0]; const sp = d0.end - d0.start;
  const lim = ["Public GP / TLE orbital data", "Simplified isotropic uncertainty — not operational covariance", "Simplified analytic Pc indicator, fixed 20 m hard-body radius",
    "Weaker for slow / co-orbital encounters", "No authoritative operational validation", "Controlled demo scenarios · decision support, not spacecraft control"];
  const fut = ["Covariance / CDM integration", "Richer catalogue and sensor feeds", "Higher-fidelity uncertainty propagation", "Operational validation"];
  scenes.push({ type: "slide", start: startOf("s11_limits"), end: end("s11_limits"), html: `
    <div class="slide" style="padding-top:110px">
      <div style="display:flex;gap:46px">
        <div class="col card" data-at="${q(0)}"><h3>TRANSPARENT ENGINEERING BOUNDARIES</h3>
          <ul class="b">${lim.map((s, i) => `<li data-at="${q(0) + sp * (0.1 + i * 0.14)}">${s}</li>`).join("")}</ul></div>
        <div class="card future" data-at="${q(1)}" style="width:640px"><h3>FUTURE EVOLUTION · NOT YET BUILT</h3>
          <ul class="b">${fut.map((s, i) => `<li data-at="${q(1) + 0.6 + i * 1.2}">${s}</li>`).join("")}</ul></div>
      </div>
    </div>` });
}

// 12. Closing
{
  const q = (i) => cue("s12_close", i);
  scenes.push({ type: "slide", start: startOf("s12_close"), end: TL.total, brand: false, html: `
    <div class="slide center" style="padding-top:170px">
      <div class="mid" data-at="${q(0)}" data-out="${q(2) - 0.2}">Space safety is not only about tracking what is in orbit.</div>
      <div style="height:30px"></div>
      <div class="mid" data-at="${q(1)}" data-out="${q(2) - 0.2}">It is about understanding what may happen next —<br>and helping the right human make the right decision at the right time.</div>
      <div style="position:absolute;left:0;right:0;top:330px">
        <div class="big" data-at="${q(2)}" style="font-size:104px;letter-spacing:0.06em">ANTARIKSHA-RAKSHA</div>
        <div style="height:46px"></div>
        <div class="motto"><span data-at="${q(3)}" class="accent">PHYSICS FIRST.</span> <span data-at="${q(3) + 0.9}">AI SECOND.</span> <span data-at="${q(3) + 1.8}" style="color:#f0b955">HUMAN DECISION LAST.</span></div>
        <div style="height:70px"></div>
        <div class="mid soft" data-at="${q(3) + 2.4}" style="font-size:30px">Sejal Khimani · Ideas for India 2026</div>
      </div>
    </div>` });
}

export const CONFIG = { images, scenes, total: TL.total };
