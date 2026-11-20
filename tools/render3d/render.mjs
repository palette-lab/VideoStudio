#!/usr/bin/env node
// Render a 3D Render film folder (spec.json) frame by frame in headless Chrome.
//
//   node render.mjs <film dir> --out <dir> [--passes rgb,depth,normal,mask_characters,mask_objects]
//                   [--stills 0.5,2,4.1] [--meta-only] [--from F --to F]
//
// Every pass gets its own MP4 with the same size, frame rate and frame count; one metadata.json records the camera,
// each actor's position, pose keypoints and screen box, and the active events for every frame. --stills writes RGB
// JPEGs at the given seconds (for the preview and the vision check) without encoding a video.

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const film = path.resolve(argv[0] || '.');
const opt = (k, d) => { const i = argv.indexOf('--' + k); return i >= 0 ? argv[i + 1] : d; };
const flag = (k) => argv.includes('--' + k);
const out = path.resolve(opt('out', path.join(film, 'out')));
const passes = opt('passes', 'rgb,depth,normal,mask_characters,mask_objects').split(',').filter(Boolean);
const stills = opt('stills', '') ? opt('stills').split(',').map(Number).filter((x) => Number.isFinite(x)) : null;
const CHROME = process.env.RENDER3D_CHROME || process.env.PLAYWRIGHT_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FFMPEG = process.env.FFMPEG || 'ffmpeg';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream', '.fbx': 'application/octet-stream', '.obj': 'text/plain', '.png': 'image/png', '.jpg': 'image/jpeg' };

// A local server: /film/* is the film folder, everything else the runtime (the page, three.js). Nothing else.
function serve() {
  return new Promise((res) => {
    const srv = http.createServer((req, rsp) => {
      const u = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      const base = u.startsWith('/film/') ? film : ROOT, rel = u.startsWith('/film/') ? u.slice(6) : u.slice(1);
      const file = path.resolve(base, rel);
      if (!file.startsWith(base + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { rsp.writeHead(404); rsp.end(); return; }
      if (base === ROOT && !(rel.startsWith('scene/') || rel.startsWith('node_modules/three/'))) { rsp.writeHead(403); rsp.end(); return; }
      rsp.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' }); fs.createReadStream(file).pipe(rsp);
    });
    srv.listen(0, '127.0.0.1', () => res(srv));
  });
}

function encoder(file, fps, kind) {
  const args = ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', kind === 'png' ? 'png' : 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', kind === 'png' ? '10' : '16', '-pix_fmt', 'yuv420p', '-r', String(fps), '-movflags', '+faststart', file];
  const p = spawn(FFMPEG, args, { stdio: ['pipe', 'ignore', 'pipe'] });
  let err = ''; p.stderr.on('data', (d) => { err += d; });
  const done = new Promise((res, rej) => p.on('close', (code) => (code === 0 ? res() : rej(new Error(`ffmpeg ${path.basename(file)}: ${err.slice(-400)}`)))));
  const write = (buf) => new Promise((res) => (p.stdin.write(buf) ? res() : p.stdin.once('drain', res)));
  return { write, end: () => { p.stdin.end(); return done; } };
}

async function launch() {
  const tries = [[], ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']];  // hardware first, software if WebGL fails
  let last;
  for (const extra of tries) {
    const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--ignore-gpu-blocklist', '--enable-webgl', '--hide-scrollbars', ...extra] });
    const ok = await (async () => { const pg = await browser.newPage(); const r = await pg.evaluate(() => !!document.createElement('canvas').getContext('webgl2')); await pg.close(); return r; })();
    if (ok) return { browser, gl: extra.length ? 'swiftshader' : 'hardware' };
    last = 'no WebGL2'; await browser.close();
  }
  throw new Error('Chrome has no WebGL2 here: ' + last);
}

const t0 = Date.now();
const srv = await serve();
const { browser, gl } = await launch();
let exitCode = 0;
try {
  const spec = JSON.parse(fs.readFileSync(path.join(film, 'spec.json'), 'utf8'));
  const page = await browser.newPage({ viewport: { width: spec.width, height: spec.height } });
  const problems = [];
  page.on('pageerror', (e) => problems.push('[pageerror] ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|status of 404/.test(m.text())) problems.push('[console] ' + m.text()); });
  await page.goto(`http://127.0.0.1:${srv.address().port}/scene/index.html`);
  await page.waitForFunction(() => window.R3D_READY || window.R3D_ERROR, null, { timeout: 120000 }).catch(() => problems.push('the scene never became ready'));
  const err = await page.evaluate(() => window.R3D_ERROR || null);
  if (err || problems.some((p) => p.startsWith('[pageerror]') || p.includes('never became ready'))) throw new Error([err, ...problems].filter(Boolean).join(' | '));
  const info = await page.evaluate(() => window.R3D.info());
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(out, 'info.json'), JSON.stringify({ ...info, gl }, null, 1));
  const N = info.frames, fps = info.fps;

  if (stills) {
    for (const s of stills) {
      const f = Math.max(0, Math.min(N - 1, Math.round(s * fps)));
      const r = await page.evaluate(([f]) => window.R3D.frame(f, ['rgb'], 0.9), [f]);
      fs.writeFileSync(path.join(out, `still_${s.toFixed(2)}.jpg`), Buffer.from(r.images.rgb.split(',')[1], 'base64'));
    }
    console.log(JSON.stringify({ ok: true, stills: stills.length, gl, ms: Date.now() - t0 }));
  } else {
    const from = +opt('from', 0), to = Math.min(N, +opt('to', N));
    const meta = [];
    const enc = flag('meta-only') ? {} : Object.fromEntries(passes.map((p) => [p, encoder(path.join(out, `${p}.mp4`), fps, p.startsWith('mask') ? 'png' : 'jpg')]));
    for (let f = from; f < to; f++) {
      const r = await page.evaluate(([f, ps]) => window.R3D.frame(f, ps, 0.92), [f, flag('meta-only') ? [] : passes]);
      meta.push(r.meta);
      for (const p of Object.keys(enc)) await enc[p].write(Buffer.from(r.images[p].split(',')[1], 'base64'));
      if (f % Math.max(1, Math.round(fps * 2)) === 0) console.log(`frame ${f}/${to}`);
    }
    await Promise.all(Object.values(enc).map((e) => e.end()));
    fs.writeFileSync(path.join(out, 'metadata.json'), JSON.stringify({ fps, frames: N, width: info.width, height: info.height, passes, gl, frames_meta: meta }));
    if (problems.length) fs.writeFileSync(path.join(out, 'warnings.txt'), problems.join('\n'));
    console.log(JSON.stringify({ ok: true, frames: to - from, passes, gl, ms: Date.now() - t0 }));
  }
} catch (e) {
  console.error('RENDER3D_ERROR ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  await browser.close();
  srv.close();
}
process.exit(exitCode);
