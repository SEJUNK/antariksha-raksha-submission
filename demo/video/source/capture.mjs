// Captures the real deployed ANTARIKSHA-RAKSHA application for the jury video.
// Writes screenshots to shots/ and the exact API values shown to capture_log.json.
import fs from "node:fs";
import path from "node:path";
import { launch, login, sleep, VIDEO_DIR } from "./cdp.mjs";

const BASE = "https://antariksha-raksha.vercel.app/";
const log = { started_utc: new Date().toISOString(), app: BASE, steps: [] };
const note = (step, data) => { log.steps.push({ t: new Date().toISOString(), step, ...data }); console.log(step, JSON.stringify(data || {}).slice(0, 300)); };
const api = (b, p, opts = "") => b.eval(`fetch(${JSON.stringify(p)},{credentials:'include'${opts}}).then(r=>r.json())`);
const drawerScroll = (b, y) => b.eval(`(() => { const d=[...document.querySelectorAll('div')].find(e=>{const s=getComputedStyle(e);const r=e.getBoundingClientRect();return (s.overflowY==='auto'||s.overflowY==='scroll')&&e.scrollHeight>e.clientHeight+40&&r.width>1000;}); if(!d) return -1; d.scrollTop=${y}; return d.scrollHeight; })()`);
const scrollToText = (b, text) => b.eval(`(() => { const d=[...document.querySelectorAll('div')].find(e=>{const s=getComputedStyle(e);const r=e.getBoundingClientRect();return (s.overflowY==='auto'||s.overflowY==='scroll')&&e.scrollHeight>e.clientHeight+40&&r.width>1000;});
  const el=[...d.querySelectorAll('*')].find(e=>e.children.length===0 && (e.innerText||'').includes(${JSON.stringify(text)}));
  if(!el) return false; d.scrollTop += el.getBoundingClientRect().top - d.getBoundingClientRect().top - 12; return true; })()`);
const mode = (b) => b.eval(`(document.querySelector('header')||document.body).innerText.match(/LIVE DATA|DEMO MODE|CACHED DATA|STALE DATA|NOT SCREENED/)?.[0]||''`);

const b = await launch();
try {
  note("login", { result: await login(b, BASE) });
  await sleep(8000);
  note("initial_mode", { mode: await mode(b) });

  // 1. Return production to LIVE data (Exit demo → refresh live data).
  if ((await mode(b)) === "DEMO MODE") {
    await b.clickText("EXIT DEMO — REFRESH LIVE DATA");
    note("exit_demo_clicked", {});
    await sleep(1500);
    await b.shot("live_00_refreshing");
    await b.waitFor(`(document.querySelector('header')||document.body).innerText.includes('LIVE DATA')`, 120000);
    await sleep(9000);
  }
  const live = await api(b, "/api/status");
  note("live_status", { mode: live.mode_label, source: live.source, last_refresh_at: live.last_refresh_at, object_count: live.object_count, objects_ingested: live.objects_ingested,
    window_h: live.screening_window_hours, step_s: live.screening_step_seconds, candidate_pairs: live.latest_run?.candidate_pairs,
    collision_events: live.latest_run?.collision_events, proximity_events: live.latest_run?.proximity_events, tle_age_days: live.tle_age_days, scheduler: live.scheduler });
  await b.shot("live_01_dashboard");
  await sleep(4000);
  await b.shot("live_02_dashboard_b");

  // Registry & catalog panel.
  if (await b.clickText("Registry & catalog")) { await sleep(2500); await b.shot("live_03_registry"); await b.clickText("Registry & catalog"); await sleep(800); }

  // 2. Controlled collision demo (labelled DEMO).
  await b.clickText("COLLISION", true);
  note("collision_demo_clicked", {});
  await b.waitFor(`(document.querySelector('header')||document.body).innerText.includes('DEMO MODE')`, 120000);
  await sleep(6000);
  // The demo event opens automatically; if not, open it.
  const drawerOpen = async () => (await drawerScroll(b, 0)) > 0;
  if (!(await drawerOpen())) { await b.clickText("CARTOSAT-3 ⇔"); await sleep(3000); }
  await sleep(5000);
  const events = await api(b, "/api/events");
  const ev = (Array.isArray(events) ? events : events.events || [])[0];
  note("demo_event", { event: ev });
  if (ev?.id) {
    note("demo_event_provenance", { provenance: await api(b, `/api/events/${ev.id}/provenance`) });
  }
  await b.shot("demo_00_globe_event");
  await drawerScroll(b, 0); await sleep(800); await b.shot("demo_01_top_summary");
  await scrollToText(b, "1 · EVENT SUMMARY"); await sleep(800); await b.shot("demo_02_summary_risk");
  await scrollToText(b, "SEPARATION PROFILE"); await sleep(1200); await b.shot("demo_03_separation");
  if (await b.clickText("3 · DATA PROVENANCE")) { await sleep(1200); await scrollToText(b, "3 · DATA PROVENANCE"); await sleep(800); await b.shot("demo_04_provenance"); }
  await scrollToText(b, "AI EXPLANATION"); await sleep(800);
  const sections = await b.eval(`[...document.querySelectorAll('*')].filter(e=>e.children.length===0 && /^\\d · /.test((e.innerText||'').trim())).map(e=>e.innerText.trim())`);
  note("drawer_sections", { sections });
  for (const s of sections) {
    if (/^(4|5) · /.test(s)) { await scrollToText(b, s); await sleep(900); await b.shot("demo_05_" + s.slice(0, 1) + "_" + s.slice(4, 30).replace(/[^A-Za-z]+/g, "_")); }
  }
  if (await b.clickText("6 · ASSESSMENT BASIS & LIMITATIONS")) { await sleep(1000); await scrollToText(b, "6 · ASSESSMENT BASIS"); await sleep(800); await b.shot("demo_06_basis_limitations"); }
  if (await b.clickText("7 · EVENT EVOLUTION")) { await sleep(1500); await scrollToText(b, "7 · EVENT EVOLUTION"); await sleep(800); await b.shot("demo_07_evolution"); }

  // 3. Human decision: one Approve on the controlled demo event.
  await scrollToText(b, "APPROVE ASSESSMENT"); await sleep(800); await b.shot("demo_08_decision_panel");
  await b.clickText("APPROVE ASSESSMENT", true);
  note("approve_clicked", {});
  await sleep(4000);
  await b.shot("demo_09_decision_recorded");
  if (ev?.id) note("decisions", { decisions: await api(b, `/api/events/${ev.id}/decisions`) });

  // 4. Analytics / audit trail.
  if (await b.clickText("ANALYTICS", true)) { await sleep(3500); await b.shot("demo_10_analytics"); note("decision_stats", { stats: await api(b, "/api/decisions/stats") }); await b.clickText("ANALYTICS", true); await sleep(800); }

  // 5. Leave production in LIVE mode.
  await b.eval(`document.querySelectorAll('button').forEach(x=>{ if((x.innerText||'').trim()==='✕') x.click(); })`);
  await sleep(800);
  await b.clickText("EXIT DEMO — REFRESH LIVE DATA");
  await b.waitFor(`(document.querySelector('header')||document.body).innerText.includes('LIVE DATA')`, 120000);
  await sleep(8000);
  await b.shot("live_04_returned_live");
  note("final_mode", { mode: await mode(b), status: (await api(b, "/api/status")).mode_label });
} catch (e) {
  note("ERROR", { message: String(e && e.message || e) });
  try { await b.shot("error_state"); } catch {}
} finally {
  log.finished_utc = new Date().toISOString();
  fs.writeFileSync(path.join(VIDEO_DIR, "capture_log.json"), JSON.stringify(log, null, 1));
  await b.close();
}
