// Renders the video: steps stage.html frame by frame (30 fps) and pipes JPEG frames to ffmpeg,
// then muxes the narration. Usage: node render.mjs [--preview t1,t2,...] [--from s --to s]
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { launch, VIDEO_DIR, sleep } from "./cdp.mjs";
import { CONFIG } from "./scenes.mjs";

const FPS = 30;
const args = process.argv.slice(2);
const preview = args.includes("--preview") ? args[args.indexOf("--preview") + 1].split(",").map(Number) : null;
const OUT = path.join(VIDEO_DIR, "out"); fs.mkdirSync(OUT, { recursive: true });

const b = await launch({ headless: true });
try {
  await b.goto("file:///" + path.join(VIDEO_DIR, "stage.html").replace(/\\/g, "/"));
  await b.waitFor("typeof window.setup === 'function'");
  console.log("images loaded:", await b.eval(`setup(${JSON.stringify(CONFIG)})`));
  const frame = async (t, quality = 90) => {
    await b.eval(`renderAt(${t})`);
    const r = await b.send("Page.captureScreenshot", { format: "jpeg", quality, captureBeyondViewport: false });
    return Buffer.from(r.data, "base64");
  };
  if (preview) {
    for (const t of preview) fs.writeFileSync(path.join(OUT, `preview_${String(t).padStart(6, "0")}.jpg`), await frame(t, 92));
    console.log("previews written");
  } else {
    const total = CONFIG.total, n = Math.ceil(total * FPS);
    const video = path.join(OUT, "video_only.mp4");
    const ff = spawn("ffmpeg", ["-y", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
      "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS), video], { stdio: ["pipe", "ignore", "inherit"] });
    const t0 = Date.now();
    for (let i = 0; i < n; i++) {
      const buf = await frame(i / FPS);
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
      if (i % 600 === 0) console.log(`frame ${i}/${n} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
    }
    ff.stdin.end();
    await new Promise((r) => ff.on("close", r));
    console.log("video rendered in", ((Date.now() - t0) / 1000).toFixed(0), "s");
  }
} finally { await b.close(); }
