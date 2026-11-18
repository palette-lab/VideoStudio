// VideoStudio's illustrated-film kit (VideoStudio's own code): real images from an image model, animated in code.
// An empty surface (board, paper, map...) and, per beat, the same surface with that beat's drawing; the drawing is
// revealed onto the empty surface as if drawn, wiped away between beats, with a slow camera push and words in a
// textured hand on top. Everything is deterministic in t.
import { clamp, seg, ss, eo, mulberry } from '/core/lib.js';

export const loadImages = urls => Promise.all(urls.map(u => new Promise((ok, bad) => {
  const im = new Image(); im.onload = () => ok(im); im.onerror = () => bad(new Error('image missing: ' + u)); im.src = u;
})));

// Draw `img` to cover W x H (crop, never stretch), zoomed by z around (fx, fy) in 0..1.
export function cover(g, img, W, H, z = 1, fx = .5, fy = .5) {
  const s = Math.max(W / img.width, H / img.height) * z, w = img.width * s, h = img.height * s;
  g.drawImage(img, (W - w) * fx, (H - h) * fy, w, h);
}

// 2D value noise for the reveal order (smooth blobs + fine grain), seeded.
function noise2(seed) {
  const r = mulberry(seed), N = 64, grid = Float32Array.from({ length: N * N }, () => r());
  const at = (i, j) => grid[((j % N + N) % N) * N + ((i % N + N) % N)];
  return (x, y) => { const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j, u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    return (at(i, j) * (1 - u) + at(i + 1, j) * u) * (1 - v) + (at(i, j + 1) * (1 - u) + at(i + 1, j + 1) * u) * v; };
}

// A reveal mask: which parts appear first. dir: 'ltr' | 'rtl' | 'ttb' | 'center' | 'blobs'.
export function makeMask(W, H, seed = 1, dir = 'ltr') {
  const mw = 320, mh = Math.max(2, Math.round(320 * H / W)), order = new Float32Array(mw * mh), n = noise2(seed);
  for (let y = 0; y < mh; y++) for (let x = 0; x < mw; x++) {
    const u = x / mw, v = y / mh;
    const base = dir === 'rtl' ? 1 - u : dir === 'ttb' ? v : dir === 'center' ? Math.hypot(u - .5, v - .5) * 1.4 : dir === 'blobs' ? .5 : u;
    const blob = n(u * 5, v * 5 * mh / mw), fine = n(u * 40 + 17, v * 40 * mh / mw + 3);
    order[y * mw + x] = dir === 'blobs' ? .75 * blob + .25 * fine : .55 * base + .33 * blob + .12 * fine;
  }
  let lo = Infinity, hi = -Infinity; for (const o of order) { lo = Math.min(lo, o); hi = Math.max(hi, o); }
  for (let k = 0; k < order.length; k++) order[k] = (order[k] - lo) / (hi - lo || 1);
  const c = document.createElement('canvas'); c.width = mw; c.height = mh;
  const m = c.getContext('2d'), data = m.createImageData(mw, mh);
  return { canvas: c, set(p, soft = .06) {   // alpha = how much of the drawing shows at progress p (0..1)
    for (let k = 0; k < order.length; k++) { const a = clamp((p * (1 + soft) - order[k]) / soft); data.data[k * 4 + 3] = a * 255; }
    m.putImageData(data, 0, 0); return c; } };
}

// The empty surface with `img` shown through `mask` at progress p, into `layer` (a W x H canvas), drawn to g.
const _layers = new Map();
function layerOf(W, H, k) { const key = `${W}x${H}:${k}`; if (!_layers.has(key)) { const c = document.createElement('canvas'); c.width = W; c.height = H; _layers.set(key, c); } return _layers.get(key); }
export function reveal(g, img, mask, p, W, H, alpha = 1) {
  if (p <= 0) return;
  const L = layerOf(W, H, 'reveal'), l = L.getContext('2d');
  l.globalCompositeOperation = 'source-over'; l.clearRect(0, 0, W, H); cover(l, img, W, H);
  if (p < 1) { l.globalCompositeOperation = 'destination-in'; l.imageSmoothingEnabled = true; l.drawImage(mask.set(p), 0, 0, W, H); }
  g.save(); g.globalAlpha = alpha; g.drawImage(L, 0, 0); g.restore();
}

// Grain for textured lettering (chalk, crayon, print), cached.
let _grain = null;
function grain(W, H) {
  if (_grain && _grain.width === W) return _grain;
  const c = document.createElement('canvas'); c.width = W; c.height = H; const x = c.getContext('2d'), r = mulberry(91);
  for (let i = 0; i < W * H / 6; i++) { x.fillStyle = `rgba(0,0,0,${(.25 + r() * .75).toFixed(2)})`; x.fillRect(r() * W, r() * H, 1 + r() * 2.2, 1 + r() * 1.6); }
  return (_grain = c);
}

// Words written on, left to right, in a hand; texture eats into the letters (chalk / crayon). Returns the box for TEXTS.
export function words(g, text, x, y, o = {}) {
  const { font = '600 72px Chalkduster', color = '#f3efe2', progress = 1, texture = true, shadow = null, align = 'center' } = o;
  const W = g.canvas.width, H = g.canvas.height, L = layerOf(W, H, 'words'), l = L.getContext('2d');
  l.globalCompositeOperation = 'source-over'; l.clearRect(0, 0, W, H);
  l.font = font; l.textBaseline = 'alphabetic'; l.textAlign = 'left';
  const m = l.measureText(text), w = m.width, asc = m.actualBoundingBoxAscent || 0, desc = m.actualBoundingBoxDescent || 0;
  const x0 = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
  if (shadow) { l.shadowColor = shadow; l.shadowBlur = 18; l.shadowOffsetY = 3; }
  l.fillStyle = color; l.fillText(text, x0, y); l.shadowColor = 'transparent';
  if (texture) { l.globalCompositeOperation = 'destination-out'; l.globalAlpha = .55; l.drawImage(grain(W, H), 0, 0); l.globalAlpha = 1; }
  l.globalCompositeOperation = 'destination-in'; l.fillStyle = '#000';
  l.fillRect(x0 - 10, 0, (w + 20) * clamp(progress), H);
  g.drawImage(L, 0, 0);
  return { x0: Math.floor(x0), y0: Math.floor(y - asc), x1: Math.ceil(x0 + w), y1: Math.ceil(y + desc) };
}

// The largest font size (<= max px) at which text fits maxW.
export function fit(g, text, maxW, family, weight = 600, max = 120, min = 28) {
  let s = max; g.font = `${weight} ${s}px ${family}`;
  while (s > min && g.measureText(text).width > maxW) { s -= 2; g.font = `${weight} ${s}px ${family}`; }
  return s;
}

// Specks of dust drifting near a point (chalk dust after a wipe), deterministic.
export function dust(g, t, x, y, amount, seed = 5, color = '235,232,220') {
  if (amount <= 0) return; const r = mulberry(seed);
  for (let i = 0; i < 40; i++) {
    const a = r() * Math.PI * 2, sp = 20 + r() * 80, life = (t * (0.6 + r()) + r()) % 1;
    g.fillStyle = `rgba(${color},${(amount * (1 - life) * .5).toFixed(3)})`;
    g.fillRect(x + Math.cos(a) * sp * life * 3, y + Math.sin(a) * sp * life * 2 + life * 40, 2, 2);
  }
}
export { clamp, seg, ss, eo };
