#!/usr/bin/env node
/**
 * record-walkthrough.mjs — silent screencast recorder for the /kb-video skill.
 *
 * Records a Playwright-driven walkthrough of one or more URLs/scenarios on a
 * running localhost (or any) URL. Outputs per-scene .webm/.mp4 plus a
 * concatenated master.mp4. No audio — narration is added later by kb_pipeline.py.
 *
 * For KB videos, record the FINAL take with --no-overlay so the timer badge does
 * not leak into the delivered clip. Keep the cursor (it guides the viewer's eye).
 *
 * Usage:
 *   node record-walkthrough.mjs --base-url=http://localhost:4200 \
 *                               --out=tmp/kb-video/qa-12 \
 *                               --config=tmp/kb-video/qa-12/scenes.json \
 *                               --no-overlay
 *
 *   node record-walkthrough.mjs --base-url=http://localhost:4200 \
 *                               --out=tmp/kb-video/qa-12 \
 *                               --route=/settings --no-overlay
 *
 * scenes.json schema:
 *   [
 *     { "id": "S1", "route": "/dashboard", "duration": 6 },
 *     { "id": "S2", "route": "/settings",  "duration": 5,
 *       "actions": [
 *         { "type": "click",  "selector": "button.primary", "after": 2 },
 *         { "type": "fill",   "selector": "input[name=q]", "text": "hello", "after": 1 },
 *         { "type": "wait",   "ms": 1500 }
 *       ]
 *     }
 *   ]
 *
 * Requires: `npx playwright install chromium` (or `npm install -D playwright`)
 * Plus: `ffmpeg` on PATH for conversion/concatenation.
 */
import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync, existsSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { resolve, join } from "node:path";

const VIEWPORT = { width: 1440, height: 900 };
const DPR = 2;

function parseArgs(argv) {
  const args = { routes: [] };
  for (const a of argv.slice(2)) {
    if (a.startsWith("--base-url=")) args.baseUrl = a.slice(11);
    else if (a.startsWith("--out=")) args.out = a.slice(6);
    else if (a.startsWith("--config=")) args.config = a.slice(9);
    else if (a.startsWith("--route=")) args.routes.push(a.slice(8));
    else if (a === "--no-cursor") args.noCursor = true;
    else if (a === "--no-overlay") args.noOverlay = true;
  }
  if (!args.baseUrl) throw new Error("Required: --base-url=http://...");
  if (!args.out) throw new Error("Required: --out=/path/to/out-dir");
  return args;
}

function loadScenes(args) {
  if (args.config) {
    const data = JSON.parse(readFileSync(resolve(args.config), "utf8"));
    return data.map((s, i) => ({ id: s.id || `S${i + 1}`, ...s }));
  }
  return args.routes.map((r, i) => ({ id: `S${i + 1}`, route: r, duration: 6 }));
}

const cursorScript = `
(() => {
  if (window.__cursor) return;
  const c = document.createElement("div");
  c.style.cssText = "position:fixed;top:0;left:0;width:24px;height:24px;pointer-events:none;z-index:99999;transition:transform 600ms cubic-bezier(.4,0,.2,1);transform:translate(100px,100px);";
  c.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M2 2 L2 18 L7 14 L10 22 L13 21 L10 13 L18 13 Z" fill="white" stroke="black" stroke-width="1"/></svg>';
  document.body.appendChild(c);
  window.__cursor = c;
  window.__moveCursor = (x, y) => { c.style.transform = "translate(" + x + "px," + y + "px)"; };
  window.__clickRipple = (x, y) => {
    const r = document.createElement("div");
    r.style.cssText = "position:fixed;left:" + (x - 20) + "px;top:" + (y - 20) + "px;width:40px;height:40px;border-radius:50%;border:2px solid rgba(255,255,255,.8);pointer-events:none;z-index:99998;animation:ripple 700ms ease-out forwards;";
    document.body.appendChild(r);
    setTimeout(() => r.remove(), 700);
  };
  const sty = document.createElement("style");
  sty.textContent = "@keyframes ripple { from { transform:scale(.3); opacity:1 } to { transform:scale(1.6); opacity:0 } }";
  document.head.appendChild(sty);
})();
`;

function overlayScript(sceneId, startMs) {
  return `
(() => {
  if (window.__overlay) window.__overlay.remove();
  const o = document.createElement("div");
  o.style.cssText = "position:fixed;right:16px;bottom:16px;background:rgba(0,0,0,.55);color:#fff;padding:6px 10px;font:12px/1.2 ui-monospace,Menlo,monospace;border-radius:6px;z-index:99999;pointer-events:none";
  o.id = "__overlay";
  document.body.appendChild(o);
  window.__overlay = o;
  const start = ${startMs};
  const tick = () => {
    const e = (Date.now() - start) / 1000;
    const m = Math.floor(e / 60);
    const s = Math.floor(e % 60);
    o.textContent = "${sceneId} · " + m.toString().padStart(2, "0") + ":" + s.toString().padStart(2, "0");
    requestAnimationFrame(tick);
  };
  tick();
})();
`;
}

async function runAction(page, action) {
  if (action.type === "click") {
    const el = page.locator(action.selector).first();
    const box = await el.boundingBox();
    if (box) {
      const cx = Math.round(box.x + box.width / 2);
      const cy = Math.round(box.y + box.height / 2);
      await page.evaluate(([x, y]) => window.__moveCursor && window.__moveCursor(x, y), [cx, cy]);
      await page.waitForTimeout(700);
      await page.evaluate(([x, y]) => window.__clickRipple && window.__clickRipple(x, y), [cx, cy]);
      await el.click();
    }
  } else if (action.type === "fill") {
    await page.locator(action.selector).first().fill(action.text);
  } else if (action.type === "wait") {
    await page.waitForTimeout(action.ms || 1000);
  } else if (action.type === "scroll") {
    await page.mouse.wheel(0, action.dy || 400);
  }
  if (action.after) await page.waitForTimeout(action.after * 1000);
}

async function recordScene(browser, args, scene) {
  const sceneDir = join(args.out, scene.id);
  mkdirSync(sceneDir, { recursive: true });
  const ctx = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DPR,
    recordVideo: { dir: sceneDir, size: { ...VIEWPORT } },
  });
  const page = await ctx.newPage();
  const url = args.baseUrl.replace(/\/$/, "") + (scene.route || "/");
  console.log(`[${scene.id}] → ${url}`);
  await page.goto(url, { waitUntil: "networkidle" });
  if (!args.noCursor) await page.evaluate(cursorScript);
  if (!args.noOverlay) await page.evaluate(overlayScript(scene.id, Date.now()));
  await page.waitForTimeout(1000);
  if (scene.actions) for (const a of scene.actions) await runAction(page, a);
  if (scene.duration) await page.waitForTimeout(scene.duration * 1000);
  await page.close();
  await ctx.close();
  return sceneDir;
}

function findWebm(dir) {
  // readdirSync instead of shelling out to `ls` — portable and no subprocess.
  const list = readdirSync(dir).filter((f) => f.endsWith(".webm"));
  return list[0] ? join(dir, list[0]) : null;
}

function webmToMp4(webm, mp4) {
  const r = spawnSync(
    "ffmpeg",
    ["-y", "-i", webm, "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p", "-an", mp4],
    { stdio: "inherit" }
  );
  if (r.status !== 0) throw new Error(`ffmpeg conversion failed for ${webm}`);
}

function concatMaster(mp4Paths, masterPath) {
  const listPath = masterPath + ".list.txt";
  writeFileSync(listPath, mp4Paths.map((p) => `file '${resolve(p)}'`).join("\n"));
  const r = spawnSync("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", masterPath], {
    stdio: "inherit",
  });
  rmSync(listPath, { force: true });
  if (r.status !== 0) throw new Error("ffmpeg concat failed");
}

async function main() {
  const args = parseArgs(process.argv);
  const scenes = loadScenes(args);
  if (!scenes.length) throw new Error("No scenes provided. Use --route=... or --config=scenes.json");
  if (!existsSync(args.out)) mkdirSync(args.out, { recursive: true });

  console.log(`Recording ${scenes.length} scene(s) → ${args.out}`);
  const browser = await chromium.launch({ headless: true });
  const mp4Paths = [];
  try {
    for (const scene of scenes) {
      const sceneDir = await recordScene(browser, args, scene);
      const webm = findWebm(sceneDir);
      if (!webm) {
        console.warn(`[${scene.id}] no .webm produced — skipping`);
        continue;
      }
      const mp4 = join(args.out, `${scene.id}.mp4`);
      webmToMp4(webm, mp4);
      mp4Paths.push(mp4);
      console.log(`[${scene.id}] OK → ${mp4}`);
    }
  } finally {
    await browser.close();
  }

  if (mp4Paths.length > 1) {
    const master = join(args.out, "master.mp4");
    concatMaster(mp4Paths, master);
    console.log(`\nMaster: ${master}`);
  } else if (mp4Paths.length === 1) {
    // Single scene: expose it under the canonical master.mp4 name so `init` finds it.
    const master = join(args.out, "master.mp4");
    concatMaster(mp4Paths, master);
    console.log(`\nMaster (single scene): ${master}`);
  }
}

main().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});
