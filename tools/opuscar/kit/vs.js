// VideoStudio's drawing kit for Programmatic Film pages (VideoStudio's own code, not Lemo-Opuscar's).
// Import from the page:  import { ... } from '/kit/vs.js';
// Every function is deterministic (no Math.random): the same t always draws the same frame.
import { clamp, seg, ss, eo, mulberry, hash } from '/core/lib.js';
export { clamp, seg, ss, eo, mulberry, hash };

// The beat of the timeline at time t, with p = 0..1 progress through it ({beat, i, p} or null before the first).
export function beatAt(timeline, t) {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const b = timeline[i];
    if (t >= b.t0) return { beat: b, i, p: clamp((t - b.t0) / Math.max(0.001, b.t1 - b.t0)) };
  }
  return null;
}

// A hand-drawn stroke through pts=[[x,y],...], drawn on up to `progress` (0..1) of its length.
// opts: {width, color, jitter (px), seed, passes (chalk/marker texture), alpha, cap}
export function rough(g, pts, o = {}) {
  const { width = 4, color = '#fff', jitter = 1.5, seed = 1, passes = 2, alpha = 1, progress = 1, cap = 'round' } = o;
  if (pts.length < 2 || progress <= 0) return;
  const segs = []; let total = 0;
  for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); segs.push(d); total += d; }
  let left = total * clamp(progress);
  g.save(); g.lineCap = cap; g.lineJoin = 'round'; g.strokeStyle = color;
  for (let k = 0; k < passes; k++) {
    const r = mulberry(seed * 97 + k * 13); let budget = left;
    g.globalAlpha = alpha * (k === 0 ? 1 : 0.45); g.lineWidth = width * (k === 0 ? 1 : 0.6);
    g.beginPath(); g.moveTo(pts[0][0] + (r() - .5) * jitter, pts[0][1] + (r() - .5) * jitter);
    for (let i = 1; i < pts.length && budget > 0; i++) {
      const f = Math.min(1, budget / Math.max(1e-6, segs[i - 1])); budget -= segs[i - 1];
      const x = pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, y = pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f;
      g.lineTo(x + (r() - .5) * jitter, y + (r() - .5) * jitter);
    }
    g.stroke();
  }
  g.restore();
}

// Points of a circle / ellipse / box / arrow, to hand to rough().
export const circlePts = (cx, cy, rx, ry = rx, n = 48, start = -Math.PI / 2) =>
  Array.from({ length: n + 1 }, (_, i) => { const a = start + i / n * Math.PI * 2; return [cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]; });
export const boxPts = (x, y, w, h) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]];
export function arrow(g, x0, y0, x1, y1, o = {}) {
  const p = clamp(o.progress ?? 1), x = x0 + (x1 - x0) * p, y = y0 + (y1 - y0) * p, a = Math.atan2(y1 - y0, x1 - x0), h = o.head ?? 22;
  rough(g, [[x0, y0], [x, y]], { ...o, progress: 1 });
  if (p > 0.85) rough(g, [[x - Math.cos(a - .45) * h, y - Math.sin(a - .45) * h], [x, y], [x - Math.cos(a + .45) * h, y - Math.sin(a + .45) * h]], { ...o, progress: 1 });
}

// The largest font size (<= max) at which `text` fits `maxW` on one line.
export function fitFont(g, text, maxW, family, weight = 600, max = 120, min = 18) {
  let s = max; g.font = `${weight} ${s}px ${family}`;
  while (s > min && g.measureText(text).width > maxW) { s -= 2; g.font = `${weight} ${s}px ${family}`; }
  return s;
}

// Lines of `text` wrapped to maxW with the current g.font.
export function wrap(g, text, maxW) {
  const words = String(text).split(/\s+/), lines = []; let line = '';
  for (const w of words) { const t = line ? line + ' ' + w : w; if (g.measureText(t).width > maxW && line) { lines.push(line); line = w; } else line = t; }
  if (line) lines.push(line);
  return lines;
}

// Text revealed left to right (a written-on look) up to `progress`; returns its box {x0,y0,x1,y1} for window.TEXTS.
// The FULL text is what TEXTS must report, from its first frame (the reveal is only how it is drawn).
export function textOn(g, text, x, y, o = {}) {
  const { font = '600 64px Fredoka', color = '#fff', align = 'left', progress = 1, baseline = 'alphabetic' } = o;
  g.save(); g.font = font; g.fillStyle = color; g.textAlign = 'left'; g.textBaseline = baseline;
  const m = g.measureText(text), w = m.width, asc = m.actualBoundingBoxAscent || 0, desc = m.actualBoundingBoxDescent || 0;
  const x0 = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
  g.beginPath(); g.rect(x0 - 4, y - asc - 8, (w + 8) * clamp(progress), asc + desc + 16); g.clip();
  g.fillText(text, x0, y); g.restore();
  return { x0: Math.floor(x0), y0: Math.floor(y - asc), x1: Math.ceil(x0 + w), y1: Math.ceil(y + desc) };
}

// A paper / board / screen surface: a flat colour with deterministic speckle and a soft vignette.
export function surface(g, W, H, color, o = {}) {
  const { speckle = 0.06, dark = 'rgba(0,0,0,', light = 'rgba(255,255,255,', vignette = 0.35, seed = 3 } = o;
  g.fillStyle = color; g.fillRect(0, 0, W, H);
  const r = mulberry(seed), n = Math.round(W * H / 900 * speckle);
  for (let i = 0; i < n; i++) { g.fillStyle = (r() < .5 ? dark : light) + (r() * .08).toFixed(3) + ')'; g.fillRect(r() * W, r() * H, 1 + r() * 2, 1 + r() * 2); }
  if (vignette > 0) {
    const v = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * .35, W / 2, H / 2, Math.hypot(W, H) * .6);
    v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, `rgba(0,0,0,${vignette})`); g.fillStyle = v; g.fillRect(0, 0, W, H);
  }
}

// A cached background (draw once, reuse every frame): cache(key, W, H, draw) -> canvas; then g.drawImage(it, 0, 0).
// draw(g2, W, H) gets the offscreen canvas's 2D CONTEXT (g2.canvas is the canvas). A callback written for a canvas
// (calling .getContext('2d') on its argument) also works: the context answers getContext with itself.
const _cache = new Map();
export function cache(key, W, H, draw) {
  if (!_cache.has(key)) {
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g2 = c.getContext('2d'); if (!g2.getContext) g2.getContext = () => g2;
    draw(g2, W, H); _cache.set(key, c);
  }
  return _cache.get(key);
}

// Pixel-art helper: draw a sprite from rows of characters, each mapped to a colour by `pal` ('.' = transparent).
export function sprite(g, rows, x, y, px, pal) {
  for (let j = 0; j < rows.length; j++) for (let i = 0; i < rows[j].length; i++) {
    const c = pal[rows[j][i]]; if (!c) continue; g.fillStyle = c; g.fillRect(Math.round(x + i * px), Math.round(y + j * px), px, px);
  }
}

// Ease between keyframes: keys = [[t, value], ...] (numbers), smooth in-out between them.
export function keys(t, ks) {
  if (t <= ks[0][0]) return ks[0][1];
  for (let i = 1; i < ks.length; i++) if (t <= ks[i][0]) { const [a, va] = ks[i - 1], [b, vb] = ks[i]; return va + (vb - va) * ss((t - a) / (b - a)); }
  return ks[ks.length - 1][1];
}
