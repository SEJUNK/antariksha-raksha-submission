// Minimal Chrome DevTools Protocol driver for capturing the real deployed application.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const VIDEO_DIR = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
export const PROFILE = path.join(VIDEO_DIR, "chrome-profile");
export const SHOTS = path.join(VIDEO_DIR, "shots");
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";

export async function launch({ width = 1920, height = 1080, headless = true } = {}) {
  const port = 9600 + Math.floor(Math.random() * 300);
  fs.mkdirSync(PROFILE, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const args = [
    `--remote-debugging-port=${port}`, `--user-data-dir=${PROFILE}`, "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--mute-audio", `--window-size=${width},${height}`, "--hide-scrollbars",
    "--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--ignore-gpu-blocklist", "--lang=en-US", "about:blank"
  ];
  if (headless) args.unshift("--headless=new");
  const proc = spawn(CHROME, args, { stdio: "ignore" });
  let targets;
  for (let i = 0; i < 60; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (targets.length) break; } catch {}
    await sleep(250);
  }
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 1; const pending = new Map(); const listeners = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); return; }
    listeners.forEach((fn) => fn(m));
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = id++; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  const api = {
    send, on: (fn) => listeners.push(fn),
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true, userGesture: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async waitFor(expr, timeout = 20000) {
      const t0 = Date.now();
      while (Date.now() - t0 < timeout) { try { if (await api.eval(`Boolean(${expr})`)) return true; } catch {} await sleep(250); }
      throw new Error("timeout: " + expr);
    },
    async goto(url) { await send("Page.navigate", { url }); await sleep(1500); },
    async shot(name, clip) {
      const r = await send("Page.captureScreenshot", { format: "png", ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
      const f = path.join(SHOTS, name.endsWith(".png") ? name : name + ".png");
      fs.writeFileSync(f, Buffer.from(r.data, "base64"));
      return f;
    },
    // Visible clickable texts, for exploration.
    async buttons() {
      return api.eval(`[...document.querySelectorAll('button,a,[role=button],[role=tab]')].filter(b=>b.offsetParent).map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ')).filter(Boolean)`);
    },
    async clickText(text, exact = false) {
      return api.eval(`(() => { const els=[...document.querySelectorAll('button,a,[role=button],[role=tab],tr,li,div')].filter(b=>b.offsetParent);
        const t=${JSON.stringify(text)}; const el=els.find(b=>{const s=(b.innerText||'').trim().replace(/\\s+/g,' '); return ${exact} ? s===t : ((b.tagName!=='DIV' || b.getAttribute('role')) && s.includes(t));});
        if(!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true; })()`);
    },
    async close() { try { ws.close(); } catch {} proc.kill(); await sleep(400); }
  };
  return api;
}

// Logs in through the real login screen. Credentials come only from the environment.
export async function login(b, base) {
  const user = process.env.AR_USER, pass = process.env.AR_PASS;
  if (!user || !pass) throw new Error("AR_USER / AR_PASS not set");
  await b.goto(base);
  await sleep(2500);
  const needLogin = await b.eval(`!!document.querySelector('input[type=password]')`);
  if (!needLogin) return "already-signed-in";
  await b.eval(`(() => {
    const set = (el, v) => { const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value'); proto.set.call(el, v); el.dispatchEvent(new Event('input', {bubbles:true})); };
    const inputs=[...document.querySelectorAll('input')]; const pw=inputs.find(i=>i.type==='password'); const us=inputs.find(i=>i!==pw && (i.type==='text'||i.type==='email'||!i.type));
    set(us, ${JSON.stringify(user)}); set(pw, ${JSON.stringify(pass)});
    const f=pw.closest('form'); if (f) f.requestSubmit(); else [...document.querySelectorAll('button')].find(b=>/log ?in|sign ?in/i.test(b.innerText)).click();
  })()`);
  await b.waitFor(`!document.querySelector('input[type=password]')`, 20000);
  return "signed-in";
}
