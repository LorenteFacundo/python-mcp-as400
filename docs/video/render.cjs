/*
 * Renderiza escenas.html cuadro a cuadro con Chromium (Playwright) y arma el MP4 con ffmpeg.
 *
 * Requisitos: haber corrido build_audio.py, Node 18+, `npm i playwright` y ffmpeg con libx264.
 *
 *   node render.cjs                         -> build/mcp-as400.mp4
 *   node render.cjs --stills 5,62.5,120     -> build/still-5.png, ... (para revisar el diseño)
 *
 * Variables de entorno: FPS (30), WORKERS (3), FFMPEG (ffmpeg), OUT (build/mcp-as400.mp4)
 */
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const HERE = __dirname;
const BUILD = path.join(HERE, "build");
const FPS = Number(process.env.FPS || 30);
const WORKERS = Number(process.env.WORKERS || 3);
const FFMPEG = process.env.FFMPEG || "ffmpeg";
const OUT = process.env.OUT || path.join(BUILD, "mcp-as400.mp4");

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ["pipe", "ignore", "inherit"], ...opts });
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} salio con codigo ${code}`))));
    if (opts.onSpawn) opts.onSpawn(p);
  });
}

async function openPage(browser) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on("pageerror", (err) => { console.error("Error en la pagina:", err.message); process.exit(1); });
  await page.goto("file://" + path.join(HERE, "escenas.html"));
  const total = await page.evaluate(() => window.ready);
  return { page, total };
}

async function stills(browser, times) {
  const { page } = await openPage(browser);
  for (const t of times) {
    await page.evaluate((t) => window.renderAt(t), t);
    const file = path.join(BUILD, `still-${t}.png`);
    await page.screenshot({ path: file });
    console.log(file);
  }
}

async function renderPart(browser, idx, from, to) {
  const { page } = await openPage(browser);
  const file = path.join(BUILD, `part-${idx}.mp4`);
  let ff;
  const done = run(FFMPEG, [
    "-y", "-loglevel", "error",
    "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
    "-r", String(FPS), file,
  ], { onSpawn: (p) => (ff = p) });
  for (let f = from; f < to; f++) {
    await page.evaluate((t) => window.renderAt(t), f / FPS);
    const jpg = await page.screenshot({ type: "jpeg", quality: 92 });
    if (!ff.stdin.write(jpg)) await new Promise((r) => ff.stdin.once("drain", r));
    if ((f - from) % 300 === 0) console.log(`[parte ${idx}] ${f - from}/${to - from}`);
  }
  ff.stdin.end();
  await done;
  await page.close();
  return file;
}

(async () => {
  fs.mkdirSync(BUILD, { recursive: true });
  const browser = await chromium.launch();
  try {
    const i = process.argv.indexOf("--stills");
    if (i !== -1) {
      await stills(browser, process.argv[i + 1].split(",").map(Number));
      return;
    }

    const tl = JSON.parse(fs.readFileSync(path.join(BUILD, "timeline.json"), "utf8"));
    const frames = Math.ceil(tl.total * FPS);
    const per = Math.ceil(frames / WORKERS);
    console.log(`Renderizando ${frames} cuadros (${tl.total.toFixed(1)} s) con ${WORKERS} procesos...`);
    const t0 = Date.now();
    const parts = await Promise.all(
      Array.from({ length: WORKERS }, (_, k) => renderPart(browser, k, k * per, Math.min(frames, (k + 1) * per)))
    );

    const list = path.join(BUILD, "parts.txt");
    fs.writeFileSync(list, parts.map((p) => `file '${p}'`).join("\n"));
    await run(FFMPEG, [
      "-y", "-loglevel", "error",
      "-f", "concat", "-safe", "0", "-i", list,
      "-i", path.join(BUILD, "narration.wav"),
      "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
      "-movflags", "+faststart", "-shortest", OUT,
    ]);
    parts.forEach((p) => fs.unlinkSync(p));
    fs.unlinkSync(list);
    console.log(`Listo: ${OUT} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  } finally {
    await browser.close();
  }
})().catch((err) => { console.error(err); process.exit(1); });
