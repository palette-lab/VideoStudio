// VideoStudio 3D Render engine: one JSON scene spec -> a real Three.js scene, animated deterministically.
//
// Every frame is a pure function of its frame number: the actors' bones are driven by an AnimationMixer whose
// keyframe clips are baked from the event timeline before READY, props and the camera are evaluated from the same
// clock, and every control pass (RGB, depth, normal, character / object masks) is rendered from the identical scene
// state and camera. Nothing reads a wall clock or an unseeded random number.
//
// The camera-move vocabulary (dolly, reverse dolly, orbit, arc, crane, crane-arc, handheld follow, parallax pan,
// tilt, vertigo zoom) follows CamSketch (github.com/phantomfjh/camsketch, MIT, (c) 2026 Sorcara), generalised here
// to follow a moving subject instead of a fixed origin. See ../NOTICE.md.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';

const fail = (msg) => { window.R3D_ERROR = String(msg); console.error('[r3d] ' + msg); };

// ------------------------------------------------------------------------------------------------ utils
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const lerp = (a, b, t) => a + (b - a) * t;
const DEG = Math.PI / 180;
const EASE = {
  linear: (t) => t,
  inOut: (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2),
  cubic: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  out: (t) => 1 - (1 - t) * (1 - t),
  in: (t) => t * t,
};
const angWrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));
const angLerp = (a, b, t) => a + angWrap(b - a) * t;
function hashStr(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(seed) { let s = (seed >>> 0) || 1; return () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; }; }
const col = (c, d = '#999999') => new THREE.Color(typeof c === 'string' && /^#?[0-9a-f]{6}$/i.test(c) ? (c[0] === '#' ? c : '#' + c) : d);
const num = (v, d) => (Number.isFinite(+v) ? +v : d);
// trapezoid speed profile: constant speed with short ramps (a real walk, not a slide that eases the whole way)
function travel(u, ramp = 0.18) {
  u = clamp01(u); const r = Math.min(ramp, 0.5); const vmax = 1 / (1 - r);
  if (u < r) return 0.5 * vmax * u * u / r;
  if (u > 1 - r) { const w = 1 - u; return 1 - 0.5 * vmax * w * w / r; }
  return 0.5 * vmax * r + vmax * (u - r);
}

// ------------------------------------------------------------------------------------------------ spec
const spec = await (await fetch('/film/spec.json')).json();
const W = spec.width | 0, H = spec.height | 0, FPS = spec.fps | 0, DUR = +spec.duration;
const NF = Math.max(1, Math.round(DUR * FPS));
const ENV = spec.environment || {};

// ------------------------------------------------------------------------------------------------ renderer
const canvas = document.getElementById('c');
canvas.width = W; canvas.height = H;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(1);
renderer.setSize(W, H, false);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, W / H, 0.1, 600);

// ------------------------------------------------------------------------------------------------ materials
// Plausible matte surfaces with a little procedural texture, so the video model reads wood as wood and grass as
// grass. No glass or chrome unless the spec asks for it (VideoStudio's realistic-look rule).
const SURF = {
  wood: { rough: 0.7, metal: 0, pattern: 'grain', amp: 0.16 }, bark: { rough: 0.95, metal: 0, pattern: 'grain', amp: 0.25 },
  fabric: { rough: 0.95, metal: 0, pattern: 'weave', amp: 0.08 }, leather: { rough: 0.6, metal: 0, pattern: 'speckle', amp: 0.06 },
  stone: { rough: 0.9, metal: 0, pattern: 'speckle', amp: 0.2 }, concrete: { rough: 0.92, metal: 0, pattern: 'speckle', amp: 0.1 },
  brick: { rough: 0.9, metal: 0, pattern: 'brick', amp: 0.15 }, asphalt: { rough: 0.95, metal: 0, pattern: 'speckle', amp: 0.12 },
  grass: { rough: 1, metal: 0, pattern: 'speckle', amp: 0.22 }, foliage: { rough: 0.9, metal: 0, pattern: 'speckle', amp: 0.25 },
  soil: { rough: 1, metal: 0, pattern: 'speckle', amp: 0.2 }, sand: { rough: 1, metal: 0, pattern: 'speckle', amp: 0.1 },
  snow: { rough: 0.85, metal: 0, pattern: 'speckle', amp: 0.04 }, water: { rough: 0.15, metal: 0.1, pattern: 'ripple', amp: 0.12 },
  skin: { rough: 0.6, metal: 0, pattern: 'none', amp: 0 }, fur: { rough: 1, metal: 0, pattern: 'speckle', amp: 0.12 },
  plastic: { rough: 0.45, metal: 0, pattern: 'none', amp: 0 }, paint: { rough: 0.55, metal: 0, pattern: 'none', amp: 0 },
  metal: { rough: 0.38, metal: 0.85, pattern: 'none', amp: 0 }, ceramic: { rough: 0.3, metal: 0, pattern: 'none', amp: 0 },
  rubber: { rough: 0.9, metal: 0, pattern: 'none', amp: 0 }, paper: { rough: 0.9, metal: 0, pattern: 'speckle', amp: 0.04 },
  glass: { rough: 0.05, metal: 0, pattern: 'none', amp: 0, glass: true }, chrome: { rough: 0.08, metal: 1, pattern: 'none', amp: 0 },
  emissive: { rough: 0.5, metal: 0, pattern: 'none', amp: 0, glow: true }, shell: { rough: 0.7, metal: 0, pattern: 'scutes', amp: 0.2 },
};
const texCache = new Map();
function surfaceTexture(kind, base) {
  const s = SURF[kind]; if (!s || s.pattern === 'none') return null;
  const key = kind + base.getHexString(); if (texCache.has(key)) return texCache.get(key);
  const N = 256, cv = document.createElement('canvas'); cv.width = cv.height = N;
  const g = cv.getContext('2d'), r = rng(hashStr(key)), im = g.createImageData(N, N);
  const b = [base.r, base.g, base.b].map((v) => Math.pow(v, 1 / 2.2) * 255);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    let v = 0;
    if (s.pattern === 'grain') v = Math.sin((y + Math.sin(x * 0.05) * 6) * 0.35) * 0.6 + (r() - 0.5) * 0.5;
    else if (s.pattern === 'weave') v = ((x >> 1) + (y >> 1)) % 2 ? 0.5 : -0.5;
    else if (s.pattern === 'brick') { const row = Math.floor(y / 16), off = row % 2 ? 16 : 0; v = (y % 16 < 2 || (x + off) % 32 < 2) ? -1.4 : (r() - 0.5); }
    else if (s.pattern === 'ripple') v = Math.sin(x * 0.12 + Math.sin(y * 0.07) * 2) * 0.5 + (r() - 0.5) * 0.3;
    else if (s.pattern === 'scutes') { const cx = (x % 64) - 32, cy = (y % 64) - 32; v = (Math.abs(cx) + Math.abs(cy) > 40 ? -1.2 : 0.2) + (r() - 0.5) * 0.4; }
    else v = (r() - 0.5) * 2;
    const k = 1 + v * s.amp, i = (y * N + x) * 4;
    im.data[i] = Math.min(255, b[0] * k); im.data[i + 1] = Math.min(255, b[1] * k); im.data[i + 2] = Math.min(255, b[2] * k); im.data[i + 3] = 255;
  }
  g.putImageData(im, 0, 0);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping;
  texCache.set(key, t); return t;
}
const matCache = new Map();
function mat(color, kind = 'paint', repeat = 1) {
  const c = col(color), k = SURF[kind] ? kind : 'paint', key = `${k}|${c.getHexString()}|${repeat}`;
  if (matCache.has(key)) return matCache.get(key);
  const s = SURF[k];
  const opts = { color: s.pattern === 'none' ? c : 0xffffff, roughness: s.rough, metalness: s.metal };
  const tex = surfaceTexture(k, c);
  if (tex) { const t = tex.clone(); t.needsUpdate = true; t.repeat.set(repeat, repeat); opts.map = t; }
  let m;
  if (s.glass) m = new THREE.MeshPhysicalMaterial({ ...opts, transmission: 0.9, transparent: true, opacity: 0.35, thickness: 0.05 });
  else m = new THREE.MeshStandardMaterial(opts);
  if (s.glow) { m.emissive = c.clone(); m.emissiveIntensity = 1.4; }
  matCache.set(key, m); return m;
}

// Mask bookkeeping: every mesh knows its owner (an actor or a prop) for the segmentation passes.
const MASK_PALETTE = ['#ff3b30', '#34c759', '#007aff', '#ffcc00', '#af52de', '#ff9500', '#5ac8fa', '#ff2d55', '#a2845e', '#30d158', '#64d2ff', '#bf5af2'];
const maskColors = { characters: {}, objects: {} };
function tagOwner(root, kind, id) {
  root.traverse((o) => { if (o.isMesh) { o.userData.owner = { kind, id }; o.castShadow = o.userData.noShadow ? false : true; o.receiveShadow = true; } });
}

function mesh(geo, material, parent, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(geo, material); m.position.set(x, y, z); if (parent) parent.add(m); return m;
}
const capsule = (r, len) => new THREE.CapsuleGeometry(r, Math.max(0.001, len), 6, 12);

// ------------------------------------------------------------------------------------------------ environment
const SKY = { day: ['#7fb2e5', '#dfeaf2'], golden: ['#f2a65a', '#fde2b8'], dusk: ['#3d4b7a', '#e59a7a'],
              night: ['#05070f', '#1a2338'], overcast: ['#a9b3bd', '#d9dde0'], studio: ['#2a2d33', '#4a4e57'], indoor: ['#c9c3b8', '#e9e4da'] };
const tod = ENV.time_of_day in SKY ? ENV.time_of_day : 'day';
function gradientTexture(top, bottom) {
  const cv = document.createElement('canvas'); cv.width = 4; cv.height = 256; const g = cv.getContext('2d');
  const gr = g.createLinearGradient(0, 0, 0, 256); gr.addColorStop(0, top); gr.addColorStop(1, bottom);
  g.fillStyle = gr; g.fillRect(0, 0, 4, 256); const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const skyCols = ENV.sky && ENV.sky.top ? [ENV.sky.top, ENV.sky.bottom || ENV.sky.top] : SKY[ENV.indoor ? 'indoor' : (ENV.studio ? 'studio' : tod)];
const skyTexture = gradientTexture(skyCols[0], skyCols[1]);
const fog = ENV.fog > 0 ? new THREE.FogExp2(col(skyCols[1]), Math.min(0.08, +ENV.fog)) : null;
scene.background = skyTexture; scene.fog = fog;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = num(ENV.exposure, tod === 'night' ? 0.9 : 1.0);

const night = tod === 'night' && !ENV.studio;  // a studio is always lit
const hemi = new THREE.HemisphereLight(col(skyCols[0]), col(ENV.ground_color || '#6b6b5a'), num(ENV.ambient, night ? 0.25 : 0.9));
scene.add(hemi);
const sun = new THREE.DirectionalLight(night ? 0x9fb4ff : (tod === 'golden' ? 0xffc88a : 0xfff4e5), num(ENV.sun_intensity, night ? 0.5 : 2.6));
const az = num(ENV.sun_azimuth, 35) * DEG, el = num(ENV.sun_elevation, tod === 'golden' ? 14 : 48) * DEG;
sun.position.set(Math.sin(az) * Math.cos(el) * 60, Math.sin(el) * 60, Math.cos(az) * Math.cos(el) * 60);
sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.02;
const SC = num(ENV.extent, 30);
Object.assign(sun.shadow.camera, { left: -SC, right: SC, top: SC, bottom: -SC, near: 1, far: 160 });
scene.add(sun); scene.add(sun.target);
const sunOffset = sun.position.clone();
if (ENV.indoor || ENV.studio) {  // a soft key + fill, like a lit set
  const key = new THREE.PointLight(0xfff1dc, 18, 20, 2); key.position.set(2, 2.6, 2); scene.add(key);
  const fill = new THREE.PointLight(0xdfe8ff, 8, 20, 2); fill.position.set(-3, 2.4, -1); scene.add(fill);
}

const GROUND = { grass: ['#5d7d3a', 'grass'], dirt: ['#7a6248', 'soil'], soil: ['#7a6248', 'soil'], sand: ['#d8c39a', 'sand'],
  snow: ['#f1f3f5', 'snow'], asphalt: ['#3b3d40', 'asphalt'], concrete: ['#9a9893', 'concrete'], wood: ['#8a6440', 'wood'],
  stone: ['#7d7a73', 'stone'], tiles: ['#cfcac0', 'ceramic'], carpet: ['#7b6a5d', 'fabric'], studio: ['#3a3d44', 'paint'], forest: ['#4c5b30', 'soil'] };
const gkind = GROUND[ENV.ground] ? ENV.ground : (ENV.indoor ? 'wood' : ENV.studio ? 'studio' : 'grass');
const ground = mesh(new THREE.PlaneGeometry(400, 400), mat(ENV.ground_color || GROUND[gkind][0], GROUND[gkind][1], 120), scene);
ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; ground.userData.owner = { kind: 'env', id: 'ground' };
if (ENV.studio) {  // an infinite cyclorama
  const cyc = mesh(new THREE.CylinderGeometry(40, 40, 30, 48, 1, true, Math.PI * 0.5, Math.PI), mat(ENV.ground_color || '#3a3d44', 'paint'), scene, 0, 15, 0);
  cyc.material = cyc.material.clone(); cyc.material.side = THREE.BackSide;
}

// ------------------------------------------------------------------------------------------------ props
const props = {};          // id -> {spec, root, inner, parts, hinge, anchors, kind}
const occluders = [];      // meshes the camera must not pass through (walls, buildings)
function partList(g) { const p = []; g.traverse((o) => { if (o.isMesh) p.push(o); }); return p; }

function buildProp(p) {
  const root = new THREE.Group(), inner = new THREE.Group(); root.add(inner);
  const w = num(p.w, 1), h = num(p.h, 1), d = num(p.d, 1), c = p.color || '#8a8a8a', m = p.material;
  const P = { spec: p, root, inner, parts: [], hinge: null, anchors: {}, kind: p.type };
  const add = (geo, material, x, y, z, explode) => { const o = mesh(geo, material, inner, x, y, z); if (explode) { o.userData.explode = new THREE.Vector3(...explode); o.userData.home = o.position.clone(); P.parts.push(o); } return o; };
  switch (p.type) {
    case 'box': case 'crate': case 'gift': case 'cube': add(new THREE.BoxGeometry(w, h, d), mat(c, m || 'wood'), 0, h / 2, 0); break;
    case 'sphere': case 'ball': { const r = num(p.r, w / 2); add(new THREE.SphereGeometry(r, 32, 20), mat(c, m || 'rubber'), 0, r, 0); break; }
    case 'cylinder': case 'pillar': case 'barrel': add(new THREE.CylinderGeometry(w / 2, w / 2, h, 32), mat(c, m || 'paint'), 0, h / 2, 0); break;
    case 'cone': add(new THREE.ConeGeometry(w / 2, h, 32), mat(c, m || 'paint'), 0, h / 2, 0); break;
    case 'torus': { const o = add(new THREE.TorusGeometry(w / 2, Math.max(0.02, w / 8), 20, 48), mat(c, m || 'paint'), 0, w / 2 + w / 8, 0); break; }
    case 'capsule': add(capsule(w / 2, Math.max(0.01, h - w)), mat(c, m || 'paint'), 0, h / 2, 0); break;
    case 'plane': case 'rug': case 'mat': { const o = add(new THREE.BoxGeometry(w, 0.02, d), mat(c, m || 'fabric'), 0, 0.011, 0); o.castShadow = false; break; }
    case 'platform': case 'pedestal': case 'stage': add(new THREE.CylinderGeometry(w / 2, w / 2 * 1.04, h || 0.2, 48), mat(c, m || 'concrete'), 0, (h || 0.2) / 2, 0); break;
    case 'door': {
      const fh = h || 2.1, fw = w || 0.95;
      add(new THREE.BoxGeometry(0.08, fh, 0.14), mat('#e8e2d6', 'paint'), -fw / 2 - 0.04, fh / 2, 0);
      add(new THREE.BoxGeometry(0.08, fh, 0.14), mat('#e8e2d6', 'paint'), fw / 2 + 0.04, fh / 2, 0);
      add(new THREE.BoxGeometry(fw + 0.16, 0.08, 0.14), mat('#e8e2d6', 'paint'), 0, fh + 0.04, 0);
      const hinge = new THREE.Group(); hinge.position.set(-fw / 2, 0, 0); inner.add(hinge);
      mesh(new THREE.BoxGeometry(fw, fh - 0.01, 0.045), mat(c === '#8a8a8a' ? '#7a5434' : c, m || 'wood'), hinge, fw / 2, fh / 2, 0);
      mesh(new THREE.SphereGeometry(0.035, 12, 8), mat('#c9b27a', 'metal'), hinge, fw - 0.09, 1.0, 0.05);
      mesh(new THREE.SphereGeometry(0.035, 12, 8), mat('#c9b27a', 'metal'), hinge, fw - 0.09, 1.0, -0.05);
      P.hinge = hinge; P.openSign = p.opens === 'out' ? -1 : 1;  // swings into the room unless told otherwise
      P.anchors.approach = new THREE.Vector3(0.25, 0, 0.75); P.anchors.through = new THREE.Vector3(0, 0, 0); P.anchors.beyond = new THREE.Vector3(0, 0, -0.9);
      break; }
    case 'chair': case 'stool': {
      const sh = 0.46, sw = w || 0.48, sd = d || 0.48, wood = mat(c === '#8a8a8a' ? '#6e4b2e' : c, m || 'wood');
      add(new THREE.BoxGeometry(sw, 0.05, sd), wood, 0, sh, 0);
      for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) add(new THREE.BoxGeometry(0.04, sh, 0.04), wood, x * (sw / 2 - 0.03), sh / 2, z * (sd / 2 - 0.03));
      if (p.type === 'chair') add(new THREE.BoxGeometry(sw, 0.5, 0.04), wood, 0, sh + 0.27, -sd / 2 + 0.02);
      P.anchors.seat = new THREE.Vector3(0, sh, 0.02); P.anchors.approach = new THREE.Vector3(0, 0, sd / 2 + 0.32); break; }
    case 'sofa': case 'couch': case 'bench': {
      const sw = w || 2.0, sd = d || 0.9, sh = 0.44, f = mat(c === '#8a8a8a' ? '#5d6b7a' : c, m || (p.type === 'bench' ? 'wood' : 'fabric'));
      add(new THREE.BoxGeometry(sw, sh, sd), f, 0, sh / 2, 0);
      if (p.type !== 'bench') { add(new THREE.BoxGeometry(sw, 0.45, 0.2), f, 0, sh + 0.22, -sd / 2 + 0.1); add(new THREE.BoxGeometry(0.2, 0.25, sd), f, sw / 2 - 0.1, sh + 0.12, 0); add(new THREE.BoxGeometry(0.2, 0.25, sd), f, -sw / 2 + 0.1, sh + 0.12, 0); }
      P.anchors.seat = new THREE.Vector3(0, sh, 0.05); P.anchors.approach = new THREE.Vector3(0, 0, sd / 2 + 0.35); break; }
    case 'table': case 'desk': case 'counter': {
      const th = h || (p.type === 'counter' ? 0.92 : 0.75), tw = w || 1.4, td = d || 0.8, t = mat(c === '#8a8a8a' ? '#8a6440' : c, m || 'wood');
      add(new THREE.BoxGeometry(tw, 0.05, td), t, 0, th, 0);
      if (p.type === 'counter') add(new THREE.BoxGeometry(tw, th, td), t, 0, th / 2, 0);
      else for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) add(new THREE.BoxGeometry(0.05, th, 0.05), t, x * (tw / 2 - 0.05), th / 2, z * (td / 2 - 0.05));
      P.anchors.top = new THREE.Vector3(0, th + 0.025, 0); P.anchors.approach = new THREE.Vector3(0, 0, td / 2 + 0.4); break; }
    case 'bed': { const bw = w || 1.6, bd = d || 2.1;
      add(new THREE.BoxGeometry(bw, 0.35, bd), mat('#6e4b2e', 'wood'), 0, 0.175, 0); add(new THREE.BoxGeometry(bw - 0.06, 0.2, bd - 0.06), mat(c === '#8a8a8a' ? '#e9e6df' : c, 'fabric'), 0, 0.45, 0);
      add(new THREE.BoxGeometry(bw, 0.9, 0.08), mat('#6e4b2e', 'wood'), 0, 0.45, -bd / 2);
      P.anchors.seat = new THREE.Vector3(0, 0.55, bd / 2 - 0.35); P.anchors.approach = new THREE.Vector3(0, 0, bd / 2 + 0.35); break; }
    case 'shelf': case 'bookshelf': case 'cabinet': case 'fridge': case 'wardrobe': {
      const sw = w || 1.0, sh = h || 1.9, sd = d || 0.4; add(new THREE.BoxGeometry(sw, sh, sd), mat(c === '#8a8a8a' ? '#7a5a3c' : c, m || (p.type === 'fridge' ? 'paint' : 'wood')), 0, sh / 2, 0);
      if (p.type === 'fridge' || p.type === 'wardrobe' || p.type === 'cabinet') {
        const hinge = new THREE.Group(); hinge.position.set(-sw / 2, 0, sd / 2); inner.add(hinge);
        mesh(new THREE.BoxGeometry(sw, sh - 0.02, 0.03), mat(c === '#8a8a8a' ? '#dcdcd8' : c, m || 'paint'), hinge, sw / 2, sh / 2, 0.016);
        P.hinge = hinge; P.openSign = 1;
      }
      P.anchors.approach = new THREE.Vector3(0.2, 0, sd / 2 + 0.55); break; }
    case 'tv': case 'screen': case 'monitor': add(new THREE.BoxGeometry(w || 1.2, h || 0.7, 0.05), mat('#111214', 'plastic'), 0, num(p.y_mount, 1.2), 0); break;
    case 'lamp': case 'streetlight': case 'lamppost': {
      const ph = h || (p.type === 'lamp' ? 1.6 : 4.2);
      add(new THREE.CylinderGeometry(0.03, 0.05, ph, 12), mat('#2b2b2b', 'metal'), 0, ph / 2, 0);
      const bulb = add(new THREE.SphereGeometry(p.type === 'lamp' ? 0.18 : 0.22, 20, 14), mat(c === '#8a8a8a' ? '#fff1c9' : c, 'emissive'), 0, ph, 0); bulb.userData.noShadow = true;
      const L = new THREE.PointLight(0xffe2b0, night ? 30 : 6, 14, 2); L.position.set(0, ph - 0.25, 0); inner.add(L); P.light = L; break; }
    case 'tree': case 'pine': case 'palm': {
      const r = rng(hashStr(p.id)), th = h || (p.type === 'pine' || p.variant === 'pine' ? 6 : 5);
      const trunkH = th * (p.type === 'palm' ? 0.85 : 0.45);
      add(new THREE.CylinderGeometry(0.12 * th / 5, 0.2 * th / 5, trunkH, 10), mat('#5a4330', 'bark'), 0, trunkH / 2, 0);
      const leaf = mat(c === '#8a8a8a' ? (p.type === 'palm' ? '#4f7d2c' : '#3f6b2a') : c, 'foliage');
      if (p.type === 'pine' || p.variant === 'pine') for (let k = 0; k < 4; k++) add(new THREE.ConeGeometry(th * (0.32 - k * 0.06), th * 0.32, 10), leaf, 0, trunkH * 0.6 + k * th * 0.15 + th * 0.16, 0);
      else if (p.type === 'palm') for (let k = 0; k < 7; k++) { const a = k / 7 * Math.PI * 2, f = add(new THREE.BoxGeometry(0.25, 0.03, th * 0.32), leaf, Math.sin(a) * th * 0.14, trunkH, Math.cos(a) * th * 0.14); f.rotation.set(-0.45, a, 0); }
      else for (let k = 0; k < 5; k++) add(new THREE.IcosahedronGeometry(th * (0.2 + r() * 0.08), 1), leaf, (r() - 0.5) * th * 0.3, trunkH + th * (0.12 + r() * 0.25), (r() - 0.5) * th * 0.3);
      break; }
    case 'bush': case 'plant': case 'flower': {
      if (p.type === 'plant') add(new THREE.CylinderGeometry(0.16, 0.12, 0.3, 16), mat('#a0583a', 'ceramic'), 0, 0.15, 0);
      const r = rng(hashStr(p.id)), s = w || (p.type === 'flower' ? 0.25 : 0.9), base = p.type === 'plant' ? 0.3 : 0;
      for (let k = 0; k < 4; k++) add(new THREE.IcosahedronGeometry(s * (0.35 + r() * 0.15), 1), mat(c === '#8a8a8a' ? '#3e6a2c' : c, 'foliage'), (r() - 0.5) * s * 0.5, base + s * (0.3 + r() * 0.2), (r() - 0.5) * s * 0.5);
      break; }
    case 'rock': case 'boulder': { const g = new THREE.DodecahedronGeometry(w / 2 || 0.5, 1), r = rng(hashStr(p.id)), pos = g.attributes.position;
      for (let i = 0; i < pos.count; i++) pos.setXYZ(i, pos.getX(i) * (0.8 + r() * 0.35), pos.getY(i) * (0.6 + r() * 0.3), pos.getZ(i) * (0.8 + r() * 0.35));
      g.computeVertexNormals(); add(g, mat(c === '#8a8a8a' ? '#7f7a71' : c, 'stone'), 0, (w / 2 || 0.5) * 0.55, 0); break; }
    case 'mountain': case 'hill': { const g = new THREE.ConeGeometry(w / 2 || 20, h || 12, 24, 6), r = rng(hashStr(p.id)), pos = g.attributes.position;
      for (let i = 0; i < pos.count; i++) if (pos.getY(i) < (h || 12) / 2 - 0.1) { const k = 0.85 + r() * 0.3; pos.setX(i, pos.getX(i) * k); pos.setZ(i, pos.getZ(i) * k); }
      g.computeVertexNormals(); add(g, mat(c === '#8a8a8a' ? (p.type === 'hill' ? '#5f7a3c' : '#77736b') : c, p.type === 'hill' ? 'grass' : 'stone', 6), 0, (h || 12) / 2, 0); break; }
    case 'river': case 'water': case 'lake': case 'pond': case 'pool': {
      const ww = w || 3, ll = d || 30; const o = add(p.type === 'pond' || p.type === 'lake' ? new THREE.CircleGeometry(ww / 2, 48) : new THREE.PlaneGeometry(ww, ll, 1, 1), mat(c === '#8a8a8a' ? '#3a6f86' : c, 'water', 4), 0, 0.03, 0);
      o.rotation.x = -Math.PI / 2; o.castShadow = false; o.material = o.material.clone(); o.material.map = o.material.map && o.material.map.clone(); if (o.material.map) o.material.map.needsUpdate = true; P.water = o;
      if (p.type === 'river') for (const s of [-1, 1]) { const bank = add(new THREE.BoxGeometry(0.5, 0.03, ll), mat('#6d5a43', 'soil'), s * (ww / 2 + 0.2), 0.015, 0); bank.castShadow = false; }  // flush banks: feet never sink into them
      P.span = { w: ww, l: ll }; break; }
    case 'bridge': add(new THREE.BoxGeometry(w || 1.6, 0.15, d || 5), mat(c === '#8a8a8a' ? '#7b5b3a' : c, 'wood'), 0, num(p.y_deck, 0.35), 0); break;
    case 'fence': { const fl = w || 4, wood = mat(c === '#8a8a8a' ? '#8b7355' : c, 'wood'); for (let x = -fl / 2; x <= fl / 2 + 1e-6; x += 0.5) add(new THREE.BoxGeometry(0.08, 1.0, 0.08), wood, x, 0.5, 0);
      add(new THREE.BoxGeometry(fl, 0.08, 0.04), wood, 0, 0.75, 0); add(new THREE.BoxGeometry(fl, 0.08, 0.04), wood, 0, 0.4, 0); break; }
    case 'wall': { const o = add(new THREE.BoxGeometry(w || 4, h || 2.8, d || 0.15), mat(c === '#8a8a8a' ? '#e6e0d4' : c, m || 'paint'), 0, (h || 2.8) / 2, 0); occluders.push(o); break; }
    case 'room': {  // four walls and a floor, with a doorway on one side (the door itself is its own prop, placed in it)
      const rw = w || 5, rd = d || 5, rh = h || 2.8, gap = num(p.door_width, 1.1), wall = mat(c === '#8a8a8a' ? '#e8e2d6' : c, m || 'paint');
      const floor = add(new THREE.BoxGeometry(rw, 0.02, rd), mat(p.floor_color || '#8a6440', p.floor || 'wood', 3), 0, 0.011, 0); floor.castShadow = false;
      const side = p.door_side || 'front';
      const walls = { front: [0, rd / 2, rw, 0], back: [0, -rd / 2, rw, 0], left: [-rw / 2, 0, rd, 1], right: [rw / 2, 0, rd, 1] };
      for (const [name, [x, z, len, rot]] of Object.entries(walls)) {
        const segs = name === side ? [[-len / 2, -gap / 2], [gap / 2, len / 2]] : [[-len / 2, len / 2]];
        for (const [a, b] of segs) { const L = b - a, mid = (a + b) / 2; const o = add(new THREE.BoxGeometry(L, rh, 0.14), wall, rot ? x : x + mid, rh / 2, rot ? z + mid : z); if (rot) o.rotation.y = Math.PI / 2; occluders.push(o); }
        if (name === side) { const o = add(new THREE.BoxGeometry(gap, rh - 2.15, 0.14), wall, rot ? x : x, 2.15 + (rh - 2.15) / 2, rot ? z : z); if (rot) o.rotation.y = Math.PI / 2; occluders.push(o); }
      }
      const doorLocal = { front: [0, rd / 2, 0], back: [0, -rd / 2, Math.PI], left: [-rw / 2, 0, -Math.PI / 2], right: [rw / 2, 0, Math.PI / 2] }[side];
      P.anchors.doorway = new THREE.Vector3(doorLocal[0], 0, doorLocal[1]); P.doorRot = doorLocal[2];
      P.anchors.inside = new THREE.Vector3(0, 0, 0); P.isRoom = true;
      for (const [x, z] of rw * rd > 20 ? [[-rw / 4, 0], [rw / 4, 0]] : [[0, 0]]) { const L = new THREE.PointLight(0xffe9cc, num(p.light, 9) * (night ? 1.6 : 1), Math.max(rw, rd) * 1.6, 1.6); L.position.set(x, rh - 0.35, z); L.castShadow = false; inner.add(L); }
      break; }
    case 'house': { const hw = w || 6, hd = d || 6, hh = h || 3; add(new THREE.BoxGeometry(hw, hh, hd), mat(c === '#8a8a8a' ? '#d9cbb4' : c, m || 'paint'), 0, hh / 2, 0);
      const roof = add(new THREE.ConeGeometry(Math.max(hw, hd) * 0.75, hh * 0.7, 4), mat('#7a3b2e', 'brick'), 0, hh + hh * 0.35, 0); roof.rotation.y = Math.PI / 4;
      add(new THREE.BoxGeometry(1.0, 2.1, 0.05), mat('#5a3b24', 'wood'), 0, 1.05, hd / 2 + 0.03);
      for (const x of [-hw / 3, hw / 3]) add(new THREE.BoxGeometry(1.0, 0.9, 0.05), mat('#9fb6c8', 'ceramic'), x, hh * 0.6, hd / 2 + 0.03);
      partList(inner).forEach((o) => occluders.push(o)); P.anchors.approach = new THREE.Vector3(0, 0, hd / 2 + 1.0); break; }
    case 'building': case 'skyscraper': { const bw = w || 10, bd = d || 10, bh = h || 20;
      const cv = document.createElement('canvas'); cv.width = 64; cv.height = 128; const g = cv.getContext('2d'); g.fillStyle = c === '#8a8a8a' ? '#8d8f93' : c; g.fillRect(0, 0, 64, 128);
      for (let y = 6; y < 128; y += 16) for (let x = 4; x < 64; x += 15) { g.fillStyle = night && ((x * 7 + y * 3) % 5 < 2) ? '#ffd98a' : '#3b4a5c'; g.fillRect(x, y, 9, 10); }
      const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(Math.max(1, bw / 6), Math.max(1, bh / 12));
      const o = add(new THREE.BoxGeometry(bw, bh, bd), new THREE.MeshStandardMaterial({ map: t, roughness: 0.8 }), 0, bh / 2, 0); occluders.push(o); break; }
    case 'road': case 'street': case 'path': case 'sidewalk': {
      const rw = w || (p.type === 'path' ? 1.5 : p.type === 'sidewalk' ? 2 : 7), rl = d || 60;
      const o = add(new THREE.BoxGeometry(rw, 0.02, rl), mat(c === '#8a8a8a' ? (p.type === 'path' ? '#8a7558' : p.type === 'sidewalk' ? '#a19e97' : '#37393c') : c, p.type === 'path' ? 'soil' : p.type === 'sidewalk' ? 'concrete' : 'asphalt', 8), 0, 0.012, 0); o.castShadow = false;
      if (p.type === 'road' || p.type === 'street') for (let z = -rl / 2 + 1; z < rl / 2; z += 4) { const l = add(new THREE.BoxGeometry(0.15, 0.022, 2), mat('#e9e4d0', 'paint'), 0, 0.014, z); l.castShadow = false; }
      break; }
    case 'car': case 'vehicle': case 'truck': case 'bus': case 'van': {
      const L = d || (p.type === 'bus' ? 11 : p.type === 'truck' ? 6.5 : 4.4), Wd = w || (p.type === 'bus' ? 2.5 : 1.85), Hh = h || (p.type === 'bus' ? 3.0 : 1.45);
      const body = mat(c === '#8a8a8a' ? '#9b1c24' : c, 'paint');
      add(new THREE.BoxGeometry(Wd, Hh * 0.45, L), body, 0, Hh * 0.38, 0);
      add(new THREE.BoxGeometry(Wd * 0.92, Hh * 0.42, L * (p.type === 'bus' ? 0.95 : 0.5)), body, 0, Hh * 0.8, p.type === 'bus' ? 0 : -L * 0.05);
      add(new THREE.BoxGeometry(Wd * 0.94, Hh * 0.3, L * (p.type === 'bus' ? 0.9 : 0.46)), mat('#1d2633', 'plastic'), 0, Hh * 0.82, p.type === 'bus' ? 0 : -L * 0.05);
      P.wheels = [];
      for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) { const wh = new THREE.Group(); wh.position.set(x * Wd / 2, Hh * 0.2, z * L * 0.32); inner.add(wh);
        const t = mesh(new THREE.CylinderGeometry(Hh * 0.22, Hh * 0.22, 0.25, 20), mat('#1b1b1b', 'rubber'), wh); t.rotation.z = Math.PI / 2;
        mesh(new THREE.BoxGeometry(0.27, Hh * 0.08, Hh * 0.3), mat('#b9bcc0', 'metal'), wh); P.wheels.push(wh); P.wheelR = Hh * 0.22; }
      for (const x of [-1, 1]) { const hl = add(new THREE.BoxGeometry(0.3, 0.12, 0.05), mat('#fff6d8', 'emissive'), x * Wd * 0.33, Hh * 0.45, L / 2 + 0.01); hl.userData.noShadow = true; }
      break; }
    case 'bicycle': case 'bike': { const fr = mat(c === '#8a8a8a' ? '#2f6db3' : c, 'paint'); P.wheels = [];
      for (const z of [-0.55, 0.55]) { const wh = new THREE.Group(); wh.position.set(0, 0.34, z); inner.add(wh); const t = mesh(new THREE.TorusGeometry(0.33, 0.03, 8, 32), mat('#1b1b1b', 'rubber'), wh); t.rotation.y = Math.PI / 2; P.wheels.push(wh); P.wheelR = 0.34; }
      add(new THREE.BoxGeometry(0.04, 0.04, 1.0), fr, 0, 0.6, 0); add(new THREE.BoxGeometry(0.04, 0.5, 0.04), fr, 0, 0.6, -0.2); break; }
    case 'cup': case 'mug': add(new THREE.CylinderGeometry(0.045, 0.04, 0.1, 20), mat(c === '#8a8a8a' ? '#f2f0ea' : c, 'ceramic'), 0, 0.05, 0); break;
    case 'book': add(new THREE.BoxGeometry(0.16, 0.035, 0.23), mat(c === '#8a8a8a' ? '#7c2d2d' : c, 'paper'), 0, 0.018, 0); break;
    case 'bag': case 'suitcase': add(new THREE.BoxGeometry(w || 0.4, h || 0.5, d || 0.2), mat(c === '#8a8a8a' ? '#3c2f25' : c, 'leather'), 0, (h || 0.5) / 2, 0); break;
    case 'bottle': add(new THREE.CylinderGeometry(0.035, 0.035, 0.22, 20), mat(c === '#8a8a8a' ? '#2f5d3a' : c, m || 'plastic'), 0, 0.11, 0); add(new THREE.CylinderGeometry(0.014, 0.03, 0.06, 16), mat(c === '#8a8a8a' ? '#2f5d3a' : c, m || 'plastic'), 0, 0.25, 0); break;
    case 'phone': add(new THREE.BoxGeometry(0.075, 0.155, 0.009), mat(c === '#8a8a8a' ? '#1c1c1e' : c, 'plastic'), 0, 0.08, 0); break;
    case 'key': case 'coin': add(new THREE.CylinderGeometry(0.012, 0.012, 0.003, 16), mat('#d4af37', 'metal'), 0, 0.01, 0); break;
    case 'sign': case 'board': add(new THREE.BoxGeometry(0.06, h || 1.6, 0.06), mat('#5a4330', 'wood'), 0, (h || 1.6) / 2, 0); add(new THREE.BoxGeometry(w || 1.0, 0.6, 0.04), mat(c === '#8a8a8a' ? '#e9e2cf' : c, 'wood'), 0, (h || 1.6) - 0.1, 0); break;
    case 'cloud': { const r = rng(hashStr(p.id)), s = w || 4; for (let k = 0; k < 6; k++) { const o = add(new THREE.SphereGeometry(s * (0.18 + r() * 0.1), 16, 12), mat('#ffffff', 'snow'), (k - 2.5) * s * 0.15, (r() - 0.5) * s * 0.08, (r() - 0.5) * s * 0.15); o.userData.noShadow = true; } root.position.y = num(p.y, 14); break; }
    case 'product': buildProduct(P, p, add); break;
    case 'text3d': case 'shape': default: {
      if (p.asset) break;  // a model file, loaded below
      add(new THREE.BoxGeometry(w, h, d), mat(c, m || 'paint'), 0, h / 2, 0);
    }
  }
  if (p.material && !['product', 'room', 'house', 'building', 'tree', 'car'].includes(p.type) && !p.asset) {
    partList(inner).forEach((o) => { if (!o.material.emissive || o.material.emissiveIntensity === 0) o.material = mat(c, p.material); });
  }
  root.position.set(num(p.x, 0), num(p.y, p.type === 'cloud' ? 14 : 0), num(p.z, 0));
  root.rotation.y = num(p.rot, 0) * DEG;
  if (p.scale) inner.scale.setScalar(num(p.scale, 1));
  root.userData.id = p.id;
  scene.add(root);
  if (['room', 'river', 'water', 'lake', 'pond', 'pool', 'road', 'street', 'path', 'sidewalk', 'plane', 'rug', 'mat', 'mountain', 'hill', 'cloud'].includes(p.type)) P.environmental = true;
  tagOwner(root, 'object', p.id);
  props[p.id] = P;
  return P;
}

// A product: named parts that an exploded view moves apart along their own directions.
function buildProduct(P, p, add) {
  const c = p.color || '#d9d4cc', acc = p.accent || '#1f1f1f', v = (p.variant || 'generic').toLowerCase(), s = num(p.size, 0.3);
  const body = mat(c, p.material || 'plastic'), trim = mat(acc, p.accent_material || 'metal');
  if (/bottle|perfume|can|tube|jar/.test(v)) {
    const r = s * (v === 'perfume' ? 0.32 : 0.22), hh = s * (v === 'can' ? 0.8 : 1.0);
    add(new THREE.CylinderGeometry(r, r, hh, 40), body, 0, hh / 2, 0, [0, 0, 0]);
    add(new THREE.CylinderGeometry(r * 0.45, r * 0.6, hh * 0.18, 32), trim, 0, hh + hh * 0.09, 0, [0, 0.6, 0]);
    add(new THREE.CylinderGeometry(r * 1.02, r * 1.02, hh * 0.25, 40), mat(p.label_color || acc, 'paper'), 0, hh * 0.45, 0, [0, 0, 0.5]);
    add(new THREE.CylinderGeometry(r * 0.98, r * 0.98, hh * 0.04, 40), trim, 0, hh * 0.02, 0, [0, -0.5, 0]);
  } else if (/phone|tablet|remote/.test(v)) {
    const ww = s * 0.5, hh = s, dd = s * 0.05;
    add(new THREE.BoxGeometry(ww, hh, dd * 0.4), mat('#0e0f12', 'glass'), 0, hh / 2, dd * 0.35, [0, 0, 1.0]);
    add(new THREE.BoxGeometry(ww * 0.96, hh * 0.96, dd * 0.4), mat('#2a2d33', 'plastic'), 0, hh / 2, 0, [0, 0, 0.45]);
    add(new THREE.BoxGeometry(ww, hh, dd * 0.4), body, 0, hh / 2, -dd * 0.35, [0, 0, -0.6]);
    add(new THREE.CylinderGeometry(ww * 0.12, ww * 0.12, dd * 0.3, 24), trim, ww * 0.25, hh * 0.85, -dd * 0.6, [0.3, 0.3, -1.0]).rotation.x = Math.PI / 2;
  } else if (/watch/.test(v)) {
    const r = s * 0.22;
    add(new THREE.CylinderGeometry(r, r, s * 0.06, 48), trim, 0, s * 0.5, 0, [0, 0, 0.6]).rotation.x = Math.PI / 2;
    add(new THREE.CylinderGeometry(r * 0.9, r * 0.9, s * 0.01, 48), mat('#f5f2ea', 'ceramic'), 0, s * 0.5, s * 0.035, [0, 0, 1.1]).rotation.x = Math.PI / 2;
    add(new THREE.BoxGeometry(r * 0.9, s * 0.5, s * 0.02), body, 0, s * 0.5 + r + s * 0.2, -0.0, [0, 0.8, 0]);
    add(new THREE.BoxGeometry(r * 0.9, s * 0.5, s * 0.02), body, 0, s * 0.5 - r - s * 0.2, 0, [0, -0.8, 0]);
  } else if (/headphone/.test(v)) {
    const band = add(new THREE.TorusGeometry(s * 0.4, s * 0.03, 12, 40, Math.PI), body, 0, s * 0.45, 0, [0, 0.7, 0]);
    for (const x of [-1, 1]) add(new THREE.CylinderGeometry(s * 0.14, s * 0.14, s * 0.1, 32), trim, x * s * 0.4, s * 0.45, 0, [x * 0.8, 0, 0]).rotation.z = Math.PI / 2;
  } else if (/shoe|sneaker|boot/.test(v)) {
    add(new THREE.BoxGeometry(s * 0.38, s * 0.07, s), mat('#f2f0ea', 'rubber'), 0, s * 0.035, 0, [0, -0.5, 0]);
    add(new THREE.BoxGeometry(s * 0.36, s * 0.22, s * 0.85), body, 0, s * 0.18, -s * 0.05, [0, 0.4, 0]);
    add(new THREE.BoxGeometry(s * 0.3, s * 0.06, s * 0.4), trim, 0, s * 0.3, s * 0.1, [0, 0.9, 0.3]);
  } else if (/laptop|camera|speaker|console|box/.test(v)) {
    add(new THREE.BoxGeometry(s, s * 0.5, s * 0.6), body, 0, s * 0.25, 0, [0, 0, 0]);
    add(new THREE.CylinderGeometry(s * 0.18, s * 0.2, s * 0.2, 32), trim, 0, s * 0.25, s * 0.38, [0, 0, 0.9]).rotation.x = Math.PI / 2;
    add(new THREE.BoxGeometry(s * 0.3, s * 0.05, s * 0.2), trim, s * 0.25, s * 0.53, 0, [0.4, 0.7, 0]);
  } else {
    add(new THREE.BoxGeometry(s, s * 0.6, s * 0.6), body, 0, s * 0.3, 0, [0, 0, 0]);
    add(new THREE.BoxGeometry(s * 1.02, s * 0.08, s * 0.62), trim, 0, s * 0.62, 0, [0, 0.8, 0]);
    add(new THREE.SphereGeometry(s * 0.12, 24, 16), trim, 0, s * 0.3, s * 0.32, [0, 0, 0.9]);
  }
}

// ------------------------------------------------------------------------------------------------ asset files
async function loadAsset(P) {
  const url = '/film/' + String(P.spec.asset).replace(/^\/+/, '');
  const ext = url.split('.').pop().toLowerCase();
  let obj;
  if (ext === 'glb' || ext === 'gltf') obj = (await new GLTFLoader().loadAsync(url)).scene;
  else if (ext === 'fbx') obj = await new FBXLoader().loadAsync(url);
  else if (ext === 'obj') obj = await new OBJLoader().loadAsync(url);
  else throw new Error('unsupported model format ' + ext);
  const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3());
  const want = Math.max(num(P.spec.h, 1), 0.01), k = want / Math.max(size.y, 1e-6);
  obj.scale.setScalar(k); obj.position.set(-(box.min.x + size.x / 2) * k, -box.min.y * k, -(box.min.z + size.z / 2) * k);
  P.inner.add(obj); tagOwner(P.root, 'object', P.spec.id);
}

// ------------------------------------------------------------------------------------------------ actors
const actors = {};  // id -> {spec, root, bones, kind, dims, ...}
const HUMAN_BONES = ['hips', 'spine', 'chest', 'neck', 'head', 'upperArmL', 'forearmL', 'handL', 'upperArmR', 'forearmR', 'handR', 'thighL', 'shinL', 'footL', 'thighR', 'shinR', 'footR'];

function bone(name, parent, x = 0, y = 0, z = 0) { const b = new THREE.Bone(); b.name = name; b.position.set(x, y, z); parent.add(b); return b; }

function buildHumanoid(a) {
  const ap = a.appearance || {}, Hh = num(a.height, 1.75), k = Hh / 1.75;
  const build = ap.build === 'heavy' ? 1.25 : ap.build === 'slim' ? 0.88 : 1.0;
  const skin = mat(ap.skin || '#c69c7b', 'skin'), hair = mat(ap.hair || '#2b211b', 'fur'), top = mat(ap.top || a.color || '#3f5f8a', ap.top_material || 'fabric');
  const bottom = mat(ap.bottom || '#2e3440', ap.bottom_material || 'fabric'), shoes = mat(ap.shoes || '#1d1d1f', 'leather');
  const root = new THREE.Group(), B = {};
  const leg = 0.88 * k, thigh = 0.45 * k, shin = 0.43 * k, torso = 0.52 * k, neckL = 0.08 * k, headR = 0.115 * k * (ap.child ? 1.15 : 1);
  const shoulder = 0.19 * k * build, hipW = 0.095 * k * build, upper = 0.29 * k, fore = 0.26 * k;
  B.hips = bone('hips', root, 0, leg, 0); B.spine = bone('spine', B.hips, 0, 0.1 * k, 0); B.chest = bone('chest', B.spine, 0, torso * 0.55, 0);
  B.neck = bone('neck', B.chest, 0, torso * 0.42, 0); B.head = bone('head', B.neck, 0, neckL, 0);
  for (const [s, sx] of [['L', 1], ['R', -1]]) {
    B['upperArm' + s] = bone('upperArm' + s, B.chest, sx * shoulder, torso * 0.36, 0); B['forearm' + s] = bone('forearm' + s, B['upperArm' + s], 0, -upper, 0);
    B['hand' + s] = bone('hand' + s, B['forearm' + s], 0, -fore, 0);
    B['thigh' + s] = bone('thigh' + s, B.hips, sx * hipW, -0.02 * k, 0); B['shin' + s] = bone('shin' + s, B['thigh' + s], 0, -thigh, 0); B['foot' + s] = bone('foot' + s, B['shin' + s], 0, -shin, 0);
    // flesh on the bones
    mesh(capsule(0.05 * k * build, upper - 0.08 * k), top, B['upperArm' + s], 0, -upper / 2, 0);
    mesh(capsule(0.043 * k * build, fore - 0.08 * k), ap.sleeves === 'short' ? skin : top, B['forearm' + s], 0, -fore / 2, 0);
    mesh(new THREE.SphereGeometry(0.045 * k, 12, 10), skin, B['hand' + s], 0, -0.04 * k, 0).scale.set(0.8, 1.25, 0.6);
    mesh(capsule(0.072 * k * build, thigh - 0.1 * k), bottom, B['thigh' + s], 0, -thigh / 2, 0);
    mesh(capsule(0.056 * k * build, shin - 0.1 * k), ap.shorts ? skin : bottom, B['shin' + s], 0, -shin / 2, 0);
    mesh(new THREE.BoxGeometry(0.1 * k, 0.07 * k, 0.26 * k), shoes, B['foot' + s], 0, -0.02 * k, 0.06 * k);
  }
  mesh(new THREE.BoxGeometry(hipW * 2 + 0.1 * k, 0.16 * k, 0.2 * k * build), bottom, B.hips, 0, 0.02 * k, 0);
  mesh(capsule(0.14 * k * build, 0.16 * k), top, B.spine, 0, 0.14 * k, 0).scale.set(1.15, 1, 0.75);
  mesh(capsule(0.16 * k * build, 0.12 * k), top, B.chest, 0, torso * 0.2, 0).scale.set(1.2, 1, 0.72);
  if (ap.dress) mesh(new THREE.ConeGeometry(0.3 * k * build, 0.6 * k, 24, 1, true), top, B.hips, 0, -0.22 * k, 0);
  mesh(capsule(0.045 * k, 0.05 * k), skin, B.neck, 0, 0.03 * k, 0);
  const head = mesh(new THREE.SphereGeometry(headR, 28, 20), skin, B.head, 0, headR * 0.95, 0); head.scale.set(0.9, 1.08, 1.0);
  mesh(new THREE.SphereGeometry(headR * 1.04, 24, 16, 0, Math.PI * 2, 0, Math.PI * (ap.long_hair ? 0.62 : 0.48)), hair, B.head, 0, headR * 1.02, -headR * 0.06);
  if (ap.long_hair) mesh(new THREE.BoxGeometry(headR * 1.8, headR * 2.4, headR * 0.5), hair, B.head, 0, headR * 0.2, -headR * 0.75);
  mesh(new THREE.SphereGeometry(headR * 0.16, 10, 8), skin, B.head, 0, headR * 0.9, headR * 0.98);  // nose: the face reads as a face
  for (const sx of [-1, 1]) mesh(new THREE.SphereGeometry(headR * 0.1, 10, 8), mat('#1a1410', 'plastic'), B.head, sx * headR * 0.36, headR * 1.12, headR * 0.84);
  return { root, B, dims: { leg, thigh, shin, height: Hh, headR, upper, fore }, kind: 'humanoid' };
}

const SPECIES = {  // body length, shoulder height, leg ratio, neck, head, tail, ears, gait, default colour, surface
  rabbit: { L: 0.36, Ht: 0.2, neck: 0.06, head: 0.075, tail: 0.04, ears: 0.13, gait: 'hop', color: '#b9a58c', surf: 'fur' },
  hare: { L: 0.45, Ht: 0.28, neck: 0.07, head: 0.085, tail: 0.05, ears: 0.15, gait: 'hop', color: '#a8916f', surf: 'fur' },
  frog: { L: 0.14, Ht: 0.06, neck: 0.0, head: 0.05, tail: 0, ears: 0, gait: 'hop', color: '#5f8f3a', surf: 'skin' },
  tortoise: { L: 0.42, Ht: 0.14, neck: 0.1, head: 0.06, tail: 0.04, ears: 0, gait: 'crawl', color: '#6e6a4a', surf: 'skin', shell: true },
  turtle: { L: 0.42, Ht: 0.12, neck: 0.1, head: 0.06, tail: 0.04, ears: 0, gait: 'crawl', color: '#55704a', surf: 'skin', shell: true },
  cat: { L: 0.46, Ht: 0.25, neck: 0.08, head: 0.07, tail: 0.32, ears: 0.05, gait: 'walk', color: '#c98a4b', surf: 'fur' },
  dog: { L: 0.75, Ht: 0.5, neck: 0.16, head: 0.11, tail: 0.3, ears: 0.08, gait: 'walk', color: '#a87b4f', surf: 'fur' },
  fox: { L: 0.62, Ht: 0.38, neck: 0.12, head: 0.09, tail: 0.42, ears: 0.08, gait: 'walk', color: '#c4622d', surf: 'fur' },
  wolf: { L: 1.0, Ht: 0.75, neck: 0.18, head: 0.13, tail: 0.42, ears: 0.09, gait: 'walk', color: '#7d7a74', surf: 'fur' },
  horse: { L: 1.8, Ht: 1.5, neck: 0.7, head: 0.24, tail: 0.6, ears: 0.1, gait: 'walk', color: '#6b4a2f', surf: 'fur' },
  deer: { L: 1.3, Ht: 1.05, neck: 0.45, head: 0.15, tail: 0.12, ears: 0.12, gait: 'walk', color: '#9c6b3e', surf: 'fur' },
  cow: { L: 2.0, Ht: 1.35, neck: 0.35, head: 0.24, tail: 0.6, ears: 0.1, gait: 'walk', color: '#e9e4da', surf: 'fur' },
  bear: { L: 1.6, Ht: 1.0, neck: 0.2, head: 0.22, tail: 0.08, ears: 0.07, gait: 'walk', color: '#4a3526', surf: 'fur' },
  lion: { L: 1.7, Ht: 1.1, neck: 0.25, head: 0.24, tail: 0.8, ears: 0.06, gait: 'walk', color: '#c19a5b', surf: 'fur' },
  tiger: { L: 1.8, Ht: 1.0, neck: 0.25, head: 0.22, tail: 0.85, ears: 0.06, gait: 'walk', color: '#d27a2c', surf: 'fur' },
  elephant: { L: 3.0, Ht: 2.8, neck: 0.3, head: 0.6, tail: 0.8, ears: 0.6, gait: 'walk', color: '#8f8a84', surf: 'skin' },
  pig: { L: 1.1, Ht: 0.7, neck: 0.1, head: 0.18, tail: 0.1, ears: 0.08, gait: 'walk', color: '#e8b4a6', surf: 'skin' },
  sheep: { L: 1.1, Ht: 0.75, neck: 0.2, head: 0.14, tail: 0.1, ears: 0.07, gait: 'walk', color: '#efebe2', surf: 'fur' },
  mouse: { L: 0.1, Ht: 0.04, neck: 0.02, head: 0.025, tail: 0.1, ears: 0.02, gait: 'walk', color: '#8d8478', surf: 'fur' },
  squirrel: { L: 0.22, Ht: 0.1, neck: 0.03, head: 0.04, tail: 0.22, ears: 0.025, gait: 'hop', color: '#9a5b2e', surf: 'fur' },
};
function buildQuadruped(a) {
  const sp = SPECIES[a.species] || SPECIES[Object.keys(SPECIES).find((k) => (a.species || '').includes(k))] || SPECIES.dog;
  const k = a.height ? num(a.height, sp.Ht) / sp.Ht : 1, L = sp.L * k, Ht = sp.Ht * k;
  const fur = mat(a.color || sp.color, sp.surf), dark = mat(a.accent || '#2a211b', sp.surf);
  const root = new THREE.Group(), B = {};
  const legLen = sp.shell ? Ht * 0.6 : Ht * 0.62, bodyR = sp.shell ? L * 0.2 : Math.max(0.03, Ht * 0.32);
  B.hips = bone('hips', root, 0, legLen + bodyR * 0.5, 0);
  const torso = mesh(sp.shell ? new THREE.SphereGeometry(L * 0.55, 28, 16, 0, Math.PI * 2, 0, Math.PI / 2) : capsule(bodyR, L * 0.6), sp.shell ? mat(a.shell_color || '#5d4f33', 'shell') : fur, B.hips, 0, sp.shell ? -bodyR * 0.3 : 0, 0);
  if (sp.shell) torso.scale.set(0.82, 0.6, 1); else torso.rotation.x = Math.PI / 2;
  if (sp.shell) mesh(new THREE.CylinderGeometry(L * 0.44, L * 0.44, 0.02 * k, 28), fur, B.hips, 0, -bodyR * 0.3, 0).scale.set(1, 1, 1.2);
  B.neck = bone('neck', B.hips, 0, bodyR * 0.3, L * 0.42); B.neck.rotation.x = -0.6;
  mesh(capsule(Math.max(0.015, bodyR * 0.45), Math.max(0.01, sp.neck * k)), fur, B.neck, 0, sp.neck * k / 2, 0);
  B.head = bone('head', B.neck, 0, sp.neck * k, 0); B.head.rotation.x = 0.6;
  const hr = sp.head * k; const head = mesh(new THREE.SphereGeometry(hr, 20, 14), fur, B.head, 0, hr * 0.4, hr * 0.5); head.scale.set(0.9, 0.9, 1.25);
  mesh(new THREE.SphereGeometry(hr * 0.16, 8, 6), dark, B.head, 0, hr * 0.45, hr * 1.7);
  for (const sx of [-1, 1]) {
    mesh(new THREE.SphereGeometry(hr * 0.13, 8, 6), mat('#111', 'plastic'), B.head, sx * hr * 0.5, hr * 0.75, hr * 1.05);
    if (sp.ears) { const e = mesh(sp.ears > hr ? capsule(hr * 0.18, sp.ears * k) : new THREE.ConeGeometry(hr * 0.3, sp.ears * k, 8), fur, B.head, sx * hr * 0.45, hr * 1.0 + sp.ears * k * 0.45, hr * 0.1); e.rotation.z = -sx * 0.18; if (sp.ears > hr) e.scale.set(1, 1, 0.45); }
  }
  B.tail = bone('tail', B.hips, 0, bodyR * 0.2, -L * 0.42); B.tail.rotation.x = -2.2;
  if (sp.tail) mesh(sp.tail < 0.06 ? new THREE.SphereGeometry(sp.tail * k, 10, 8) : capsule(Math.max(0.01, bodyR * 0.2), sp.tail * k), a.species === 'rabbit' ? mat('#f4f1ea', 'fur') : fur, B.tail, 0, sp.tail * k / 2, 0);
  const legR = Math.max(0.012, bodyR * (sp.shell ? 0.35 : 0.3));
  for (const [s, sx, sz] of [['FL', 1, 1], ['FR', -1, 1], ['BL', 1, -1], ['BR', -1, -1]]) {
    const up = bone('up' + s, B.hips, sx * (sp.shell ? L * 0.3 : bodyR * 0.65), 0, sz * L * (sp.shell ? 0.3 : 0.3)); const lo = bone('lo' + s, up, 0, -legLen * 0.5, 0); B['up' + s] = up; B['lo' + s] = lo;
    const rear = sz < 0 && (sp.gait === 'hop'); const ur = rear ? legR * 1.6 : legR;
    mesh(capsule(ur, legLen * 0.5 - ur), fur, up, 0, -legLen * 0.25, 0); mesh(capsule(legR * 0.85, legLen * 0.5 - legR), fur, lo, 0, -legLen * 0.25, rear ? legR : 0);
    if (rear) mesh(new THREE.BoxGeometry(legR * 2, legR, legLen * 0.45), fur, lo, 0, -legLen * 0.5, legLen * 0.15);
  }
  return { root, B, dims: { leg: legLen, height: legLen + bodyR * 1.5 + hr, L, hipY: legLen + bodyR * 0.5, gait: sp.gait, stride: sp.gait === 'hop' ? L * 1.4 : L * 0.9 }, kind: 'quadruped' };
}

function buildBird(a) {
  const k = num(a.height, 0.3) / 0.3, c = mat(a.color || '#5b6e8c', 'fur'), root = new THREE.Group(), B = {};
  B.hips = bone('hips', root, 0, 0.12 * k, 0);
  mesh(new THREE.SphereGeometry(0.09 * k, 16, 12), c, B.hips, 0, 0.06 * k, 0).scale.set(0.8, 0.8, 1.3);
  B.head = bone('head', B.hips, 0, 0.14 * k, 0.08 * k); mesh(new THREE.SphereGeometry(0.05 * k, 14, 10), c, B.head);
  mesh(new THREE.ConeGeometry(0.015 * k, 0.05 * k, 8), mat('#e0a030', 'plastic'), B.head, 0, 0, 0.06 * k).rotation.x = Math.PI / 2;
  for (const [s, sx] of [['L', 1], ['R', -1]]) { B['wing' + s] = bone('wing' + s, B.hips, sx * 0.06 * k, 0.08 * k, 0); const wm = mesh(new THREE.BoxGeometry(0.22 * k, 0.01 * k, 0.12 * k), c, B['wing' + s], sx * 0.11 * k, 0, 0); }
  for (const sx of [1, -1]) mesh(new THREE.CylinderGeometry(0.005 * k, 0.005 * k, 0.1 * k), mat('#c98a2e', 'plastic'), B.hips, sx * 0.025 * k, -0.06 * k, 0);
  return { root, B, dims: { leg: 0.12 * k, height: 0.3 * k, stride: 0.1 * k }, kind: 'bird' };
}

function buildActor(a) {
  const kind = a.kind === 'quadruped' || a.kind === 'animal' ? 'quadruped' : a.kind === 'bird' ? 'bird' : 'humanoid';
  const A = kind === 'quadruped' ? buildQuadruped(a) : kind === 'bird' ? buildBird(a) : buildHumanoid(a);
  A.spec = a; A.id = a.id;
  A.bones = Object.values(A.B);
  A.skeleton = new THREE.Skeleton(A.bones);
  A.root.userData.id = a.id;
  scene.add(A.root);
  tagOwner(A.root, 'character', a.id);
  actors[a.id] = A;
  return A;
}

// ------------------------------------------------------------------------------------------------ timeline
// Locomotion / posture events are sequential per subject; gestures and hand actions overlay them.
const LOCO = new Set(['walk', 'run', 'move', 'approach', 'enter', 'exit', 'jump', 'hop', 'sit', 'stand_up', 'lie_down', 'turn', 'fly', 'land', 'swim', 'crawl', 'drive', 'follow', 'chase', 'flee', 'climb', 'fall']);
const GEST = new Set(['wave', 'talk', 'look', 'point', 'nod', 'clap', 'eat', 'drink', 'hug', 'shake_hands', 'meet', 'greet', 'idle', 'celebrate', 'think', 'type', 'read', 'dance', 'push', 'pull', 'throw', 'catch', 'open', 'close', 'pick_up', 'drop', 'place', 'give', 'take']);
const PROPFX = new Set(['rotate', 'spin', 'explode', 'assemble', 'move', 'scale', 'float', 'bounce', 'appear', 'disappear', 'color', 'morph', 'open', 'close', 'light_on', 'light_off', 'fall', 'drive', 'orbit']);
const events = (spec.events || []).map((e, i) => ({ ...e, i, t0: num(e.t0, 0), t1: Math.max(num(e.t1, 0), num(e.t0, 0) + 0.05) })).sort((a, b) => a.t0 - b.t0 || a.i - b.i);

function subjectPos(id, t) {  // ground position of an actor or prop at time t (for targets and the camera)
  if (actors[id]) { const s = actorState(actors[id], t); return new THREE.Vector3(s.x, s.y, s.z); }
  if (props[id]) { const s = propState(props[id], t); return s.pos.clone(); }
  return null;
}
function resolvePoint(v, fallback, ownerFacing = 0) {  // {x,z} | "P3" | {prop:"P3", anchor:"approach"} -> Vector3
  if (v == null) return fallback ? fallback.clone() : null;
  if (Array.isArray(v)) return new THREE.Vector3(num(v[0], 0), 0, num(v[v.length > 2 ? 2 : 1], 0));
  if (typeof v === 'string') v = { prop: v };
  if (v.prop || v.actor) {
    const id = v.prop || v.actor;
    if (props[id]) { const P = props[id], anc = P.anchors[v.anchor || 'approach'] || P.anchors.approach; const p = anc ? P.root.localToWorld(anc.clone()) : P.root.position.clone(); p.y = 0;
      if (!anc && P.spec) { const off = Math.max(num(P.spec.d, 1), num(P.spec.w, 1)) / 2 + 0.6; p.add(new THREE.Vector3(Math.sin(P.root.rotation.y), 0, Math.cos(P.root.rotation.y)).multiplyScalar(off)); } return p; }
    if (actors[id]) { const s = actors[id].plan ? actors[id].plan[0] : (actors[id].spec.start || {}); return new THREE.Vector3(num(s.x, 0), 0, num(s.z, 0)); }
  }
  return new THREE.Vector3(num(v.x, fallback ? fallback.x : 0), 0, num(v.z, fallback ? fallback.z : 0));
}
const facingOf = (from, to, d = 0) => { const dx = to.x - from.x, dz = to.z - from.z; return dx * dx + dz * dz < 1e-6 ? d : Math.atan2(dx, dz); };

// Per actor: the state at the start of each locomotion event, simulated once, in order.
function planActor(A) {
  const s0 = A.spec.start || {};
  let st = { x: num(s0.x, 0), y: 0, z: num(s0.z, 0), facing: num(s0.facing, 0) * DEG, seated: 0, lying: 0, seatY: 0, flying: num(s0.altitude, 0) };
  A.plan = [{ ...st }]; A.loco = [];
  for (const e of events) {
    if (e.actor !== A.id || !LOCO.has(e.action)) continue;
    const from = { ...st }; const seg = { e, from };
    const here = new THREE.Vector3(st.x, 0, st.z);
    if (['walk', 'run', 'move', 'approach', 'enter', 'exit', 'jump', 'hop', 'fly', 'swim', 'crawl', 'follow', 'chase', 'flee', 'climb', 'land', 'drive'].includes(e.action)) {
      let to;
      if ((e.action === 'follow' || e.action === 'chase') && e.target && actors[e.target] && actors[e.target].plan) {
        const ts = actorState(actors[e.target], e.t1); to = new THREE.Vector3(ts.x - Math.sin(ts.facing) * 0.9, 0, ts.z - Math.cos(ts.facing) * 0.9);
      } else {
        const dest = e.to ?? (e.target && e.action !== 'flee' ? e.target : null);
        const aid = typeof dest === 'string' && actors[dest] ? dest : dest && dest.actor;
        if (aid && actors[aid]) {  // meet someone: stop short of them, facing them
          const other = actors[aid].plan ? actorState(actors[aid], e.t1) : null;
          const op = other ? new THREE.Vector3(other.x, 0, other.z) : resolvePoint({ actor: aid }, here);
          const gap = num(e.gap, (A.dims.L || 0.4) / 2 + (actors[aid].dims.L || 0.4) / 2 + 0.45);
          const dir = here.clone().sub(op); if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1); dir.normalize();
          to = op.clone().addScaledVector(dir, gap);
        } else to = resolvePoint(typeof dest === 'string' ? { prop: dest } : dest, here);
      }
      if (e.action === 'enter' && e.target && props[e.target] && props[e.target].isRoom && !e.to) to = resolvePoint({ prop: e.target, anchor: 'inside' }, here);
      const pts = [here];
      for (const v of e.via || []) pts.push(resolvePoint(v, here));
      if ((e.action === 'enter' || e.action === 'exit') && e.target && props[e.target] && props[e.target].isRoom && !(e.via || []).length) pts.push(resolvePoint({ prop: e.target, anchor: 'doorway' }, here));
      pts.push(to);
      seg.pts = avoid(pts, [e.target, ...(e.via || []).map((v) => (typeof v === 'string' ? v : v && v.prop))], A); seg.len = pts.slice(1).reduce((s, p, i) => s + p.distanceTo(pts[i]), 0);
      st = { ...st, x: to.x, z: to.z, seated: 0, lying: 0 };
      if (seg.len > 0.02) st.facing = facingOf(pts[pts.length - 2], to, st.facing);
      if (e.face != null) st.facing = num(e.face, 0) * DEG;
      if (e.action === 'fly') st.flying = num(e.altitude, Math.max(2, st.flying));
      if (e.action === 'land') st.flying = 0;
    } else if (e.action === 'sit' || e.action === 'lie_down') {
      const P = props[e.target];
      if (P && P.anchors.seat) { const sw = P.root.localToWorld(P.anchors.seat.clone()); st = { ...st, x: sw.x, z: sw.z, seatY: sw.y, facing: P.root.rotation.y }; }
      else st = { ...st, seatY: e.action === 'lie_down' ? 0.05 : 0.0 };
      if (e.action === 'sit') st.seated = 1; else st.lying = 1;
    } else if (e.action === 'stand_up') {
      st = { ...st, seated: 0, lying: 0 };
      const P = props[seg.from && A.lastSeat];
      if (P && P.anchors.approach) { const p = P.root.localToWorld(P.anchors.approach.clone()); st.x = p.x; st.z = p.z; }
    } else if (e.action === 'turn') {
      st = { ...st, facing: e.degrees != null ? st.facing + num(e.degrees, 0) * DEG : e.target ? facingOf(here, subjectPosStatic(e.target), st.facing) : e.face != null ? num(e.face, 0) * DEG : st.facing + Math.PI };
    } else if (e.action === 'fall') st = { ...st, lying: 1, seatY: 0.05 };
    if (e.action === 'sit' || e.action === 'lie_down') A.lastSeat = e.target;
    seg.to = { ...st }; A.loco.push(seg); A.plan.push({ ...st });
  }
}
// Walk around solid furniture instead of through it: a segment that passes too close to one gets a waypoint that
// rounds it on the nearer side (one pass; the planner still places things sensibly).
const SOLID = new Set(['chair', 'stool', 'sofa', 'couch', 'bench', 'table', 'desk', 'counter', 'bed', 'shelf', 'bookshelf', 'cabinet', 'fridge', 'wardrobe', 'tree', 'pine', 'palm', 'rock', 'boulder', 'car', 'vehicle', 'truck', 'bus', 'van', 'box', 'crate', 'lamp', 'streetlight', 'lamppost', 'platform', 'pedestal', 'sign', 'plant', 'bush']);
function avoid(pts, exclude, A) {
  const body = A.kind === 'humanoid' ? 0.3 : Math.max(0.15, (A.dims.L || 0.4) * 0.5);
  const obst = Object.values(props).filter((P) => SOLID.has(P.kind) && !exclude.includes(P.spec.id) && !P.scatter).map((P) => ({ c: new THREE.Vector3(P.root.position.x, 0, P.root.position.z), r: Math.max(num(P.spec.w, 0.5), num(P.spec.d, 0.5)) * 0.55 * num(P.spec.scale, 1) + body }));
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const a = out[out.length - 1], b = pts[i], ab = b.clone().sub(a), L2 = ab.lengthSq();
    let worst = null;
    for (const o of obst) {
      if (o.c.distanceTo(a) < o.r || o.c.distanceTo(b) < o.r) continue;  // starts or ends at it: that is the point
      const u = L2 > 1e-9 ? clamp01(o.c.clone().sub(a).dot(ab) / L2) : 0, q = a.clone().addScaledVector(ab, u), d = q.distanceTo(o.c);
      if (d < o.r && (!worst || u < worst.u)) worst = { o, u, q, d };
    }
    if (worst) { const away = worst.q.clone().sub(worst.o.c); if (away.lengthSq() < 1e-6) away.set(-ab.z, 0, ab.x); away.normalize(); out.push(worst.o.c.clone().addScaledVector(away, worst.o.r + 0.15)); }
    out.push(b);
  }
  return out;
}
function subjectPosStatic(id) { if (props[id]) return props[id].root.position.clone(); if (actors[id] && actors[id].plan) { const s = actors[id].plan[0]; return new THREE.Vector3(s.x, 0, s.z); } return new THREE.Vector3(); }

function pathAt(pts, len, s) {  // point and heading at arc length s along a polyline
  let acc = 0;
  for (let i = 1; i < pts.length; i++) { const L = pts[i].distanceTo(pts[i - 1]); if (acc + L >= s || i === pts.length - 1) { const u = L > 1e-6 ? clamp01((s - acc) / L) : 1; return { p: pts[i - 1].clone().lerp(pts[i], u), h: facingOf(pts[i - 1], pts[i], 0), moving: L > 1e-6 }; } acc += L; }
  return { p: pts[pts.length - 1].clone(), h: 0, moving: false };
}

// The actor's whole state at time t: position, facing, posture and gait.
function actorState(A, t) {
  let st = { ...A.plan[0], gait: 0, gaitAmt: 0, run: 0, sit: 0, lie: 0, jump: 0, crouch: 0, fly: A.plan[0].flying || 0, moving: false, speed: 0, action: 'idle' };
  for (const seg of A.loco) {
    const e = seg.e;
    if (t < e.t0) break;
    const u = clamp01((t - e.t0) / (e.t1 - e.t0)), done = t >= e.t1;
    const to = seg.to;
    st = { ...st, ...to, sit: to.seated, lie: to.lying, fly: to.flying || 0, action: done ? 'idle' : e.action, gaitAmt: 0, run: 0, moving: false, crouch: 0, jump: 0 };
    if (done) continue;
    const f = seg.from;
    if (seg.pts) {
      const isJump = e.action === 'jump' || e.action === 'hop';
      const s = (isJump ? EASE.inOut(u) : travel(u, e.action === 'run' || e.action === 'chase' || e.action === 'flee' ? 0.1 : 0.18)) * seg.len;
      const q = pathAt(seg.pts, seg.len, s);
      st.x = q.p.x; st.z = q.p.z;
      const turnIn = clamp01((t - e.t0) / 0.35);
      st.facing = q.moving && seg.len > 0.05 ? angLerp(f.facing, q.h, EASE.inOut(turnIn)) : f.facing;
      if (e.face != null && u > 0.85) st.facing = angLerp(st.facing, num(e.face, 0) * DEG, (u - 0.85) / 0.15);
      st.sit = f.seated * (1 - clamp01(u / 0.2)); st.lie = 0;
      st.moving = seg.len > 0.05; st.speed = seg.len / (e.t1 - e.t0);
      st.run = e.action === 'run' || e.action === 'chase' || e.action === 'flee' ? 1 : 0;
      st.gaitAmt = st.moving && !isJump ? Math.min(1, Math.min(u, 1 - u) * 8 + 0.3) : 0;
      st.dist = s + (A.distBefore ? A.distBefore(seg) : 0);
      if (isJump) { const hgt = num(e.height, A.kind === 'humanoid' ? 0.55 : A.dims.height * 1.6); const air = clamp01((u - 0.18) / 0.64); st.jump = Math.sin(air * Math.PI) * hgt; st.crouch = u < 0.18 ? u / 0.18 : u > 0.82 ? (1 - u) / 0.18 : 0; st.air = air > 0 && air < 1 ? air : 0; }
      if (e.action === 'fly' || e.action === 'land') st.fly = lerp(f.flying || 0, to.flying || 0, EASE.inOut(u));
      if (e.action === 'climb') st.fly = lerp(f.flying || 0, num(e.height, 1), EASE.inOut(u));
    } else if (e.action === 'sit' || e.action === 'lie_down') {
      // turn away from the seat first, then back onto it while lowering (never upright on the seat)
      const a = EASE.inOut(clamp01(u / 0.35)), m = EASE.inOut(clamp01((u - 0.2) / 0.6)), b = EASE.inOut(clamp01((u - 0.22) / 0.62));
      st.x = lerp(f.x, to.x, m); st.z = lerp(f.z, to.z, m); st.facing = angLerp(f.facing, to.facing, a);
      if (e.action === 'sit') { st.sit = b; st.lie = 0; } else { st.lie = b; st.sit = 0; } st.seatY = to.seatY;
    } else if (e.action === 'stand_up') {
      const a = EASE.inOut(clamp01(u / 0.6)), b = EASE.inOut(clamp01((u - 0.45) / 0.55));
      st.sit = f.seated * (1 - a); st.lie = f.lying * (1 - a); st.seatY = f.seatY; st.x = lerp(f.x, to.x, b); st.z = lerp(f.z, to.z, b); st.facing = f.facing;
    } else if (e.action === 'turn') {
      st.facing = angLerp(f.facing, to.facing, EASE.inOut(u)); st.x = f.x; st.z = f.z; st.gaitAmt = 0.35; st.moving = true;
    } else if (e.action === 'fall') { st.lie = EASE.in(u); st.seatY = 0.05; st.x = f.x; st.z = f.z; }
  }
  st.y = (st.jump || 0) + (st.fly || 0);
  return st;
}

// Gesture / hand events active at t (several may overlap a locomotion event).
function gestures(A, t) {
  const out = [];
  for (const e of events) if (e.actor === A.id && GEST.has(e.action) && t >= e.t0 - 0.0001 && t <= e.t1 + 0.0001) out.push({ e, u: clamp01((t - e.t0) / (e.t1 - e.t0)) });
  return out;
}

// Which prop an actor holds at time t (pick_up attaches at the event's middle, drop / place / give releases).
const holdChanges = [];
for (const e of events) {
  if (e.action === 'pick_up' || e.action === 'take' || e.action === 'catch') holdChanges.push({ t: (e.t0 + e.t1) / 2, actor: e.actor, prop: e.target, held: true, e });
  if (e.action === 'drop' || e.action === 'place' || e.action === 'give' || e.action === 'throw') holdChanges.push({ t: (e.t0 + e.t1) / 2, actor: e.actor, prop: e.target, held: false, e, to: e.to, receiver: e.action === 'give' ? e.receiver : null });
}
holdChanges.sort((a, b) => a.t - b.t);
function holdingAt(propId, t) { let h = null; for (const c of holdChanges) { if (c.t > t) break; if (c.prop !== propId) continue; h = c.held ? { actor: c.actor, since: c.t } : (c.receiver ? { actor: c.receiver, since: c.t } : { released: c, at: c.t }); } return h; }

// ------------------------------------------------------------------------------------------------ posing
function setRot(b, x = 0, y = 0, z = 0) { if (b) b.rotation.set(x, y, z); }
function poseHumanoid(A, st, gs, t) {
  const B = A.B, d = A.dims;
  for (const b of A.bones) b.rotation.set(0, 0, 0);
  B.hips.position.set(0, d.leg, 0);
  const phase = (st.dist || 0) / (st.run ? 1.6 : 1.25) * Math.PI * 2;
  const g = st.gaitAmt || 0, R = st.run;
  const swing = (st.run ? 0.85 : 0.5) * g;
  // legs
  const sL = Math.sin(phase), sR = Math.sin(phase + Math.PI);
  setRot(B.thighL, -sL * swing); setRot(B.thighR, -sR * swing);
  setRot(B.shinL, Math.max(0, Math.sin(phase - 1.2)) * (0.6 + R * 0.7) * g); setRot(B.shinR, Math.max(0, Math.sin(phase + Math.PI - 1.2)) * (0.6 + R * 0.7) * g);
  setRot(B.upperArmL, sL * swing * 0.8 * (R ? 1.2 : 1), 0, 0.06); setRot(B.upperArmR, sR * swing * 0.8 * (R ? 1.2 : 1), 0, -0.06);
  setRot(B.forearmL, -0.15 - R * 1.2 * g); setRot(B.forearmR, -0.15 - R * 1.2 * g);
  B.hips.position.y = d.leg - Math.abs(Math.sin(phase)) * 0.03 * g + (g ? 0.012 : 0);
  B.spine.rotation.x = R * 0.25 * g;
  // breathing when still
  const br = Math.sin(t * 1.7) * 0.012; B.chest.rotation.x += br; B.head.rotation.x += -br;
  // crouch / jump
  const c = st.crouch || 0;
  if (c) { B.hips.position.y -= c * 0.25 * d.leg; B.thighL.rotation.x -= c * 1.0; B.thighR.rotation.x -= c * 1.0; B.shinL.rotation.x += c * 1.7; B.shinR.rotation.x += c * 1.7; B.spine.rotation.x += c * 0.45; B.upperArmL.rotation.x += c * 0.6; B.upperArmR.rotation.x += c * 0.6; }
  if (st.air) { const tuck = Math.sin(st.air * Math.PI); B.thighL.rotation.x -= tuck * 0.8; B.thighR.rotation.x -= tuck * 0.5; B.shinL.rotation.x += tuck * 1.2; B.shinR.rotation.x += tuck * 0.9; B.upperArmL.rotation.x -= tuck * 1.8; B.upperArmR.rotation.x -= tuck * 1.8; }
  // sit / lie
  const s = st.sit || 0;
  if (s) { const seatH = st.seatY || 0.45; B.hips.position.y = lerp(B.hips.position.y, seatH + 0.09, s); B.hips.position.z = -0.08 * s;
    B.thighL.rotation.x = lerp(B.thighL.rotation.x, -Math.PI / 2, s); B.thighR.rotation.x = lerp(B.thighR.rotation.x, -Math.PI / 2, s);
    B.shinL.rotation.x = lerp(B.shinL.rotation.x, Math.PI / 2, s); B.shinR.rotation.x = lerp(B.shinR.rotation.x, Math.PI / 2, s);
    B.upperArmL.rotation.x = lerp(B.upperArmL.rotation.x, -0.35, s); B.upperArmR.rotation.x = lerp(B.upperArmR.rotation.x, -0.35, s); B.forearmL.rotation.x = lerp(B.forearmL.rotation.x, -0.9, s); B.forearmR.rotation.x = lerp(B.forearmR.rotation.x, -0.9, s);
    B.spine.rotation.x = lerp(B.spine.rotation.x, -0.08, s); }
  const l = st.lie || 0;
  if (l) { B.hips.position.y = lerp(B.hips.position.y, (st.seatY || 0.05) + 0.12, l); B.hips.rotation.x = lerp(0, -Math.PI / 2, l); B.thighL.rotation.x *= 1 - l; B.thighR.rotation.x *= 1 - l; B.shinL.rotation.x *= 1 - l; B.shinR.rotation.x *= 1 - l; }
  // gestures
  for (const { e, u } of gs) {
    const env = Math.sin(clamp01(u) * Math.PI);  // in and out
    const a = e.action, wob = Math.sin((t - e.t0) * 9);
    if (a === 'wave' || a === 'greet' || a === 'celebrate') { B.upperArmR.rotation.z = -2.6 * env; B.upperArmR.rotation.x = -0.2 * env; B.forearmR.rotation.z = -0.4 * env + wob * 0.35 * env; if (a === 'celebrate') { B.upperArmL.rotation.z = 2.6 * env; } }
    else if (a === 'point') { B.upperArmR.rotation.x = -1.5 * env; B.forearmR.rotation.x = -0.05 * env; }
    else if (a === 'talk' || a === 'meet' || a === 'think') { B.head.rotation.x += Math.sin((t - e.t0) * 5) * 0.05 * env; B.upperArmR.rotation.x -= (0.45 + Math.sin((t - e.t0) * 3) * 0.15) * env; B.forearmR.rotation.x -= 0.9 * env; B.upperArmL.rotation.x -= 0.25 * env; B.forearmL.rotation.x -= 0.6 * env; }
    else if (a === 'nod') B.head.rotation.x += Math.max(0, Math.sin((t - e.t0) * 8)) * 0.25 * env;
    else if (a === 'clap') { const cl = Math.abs(Math.sin((t - e.t0) * 10)) * env; B.upperArmL.rotation.x -= 1.2 * env; B.upperArmR.rotation.x -= 1.2 * env; B.upperArmL.rotation.z = 0.5 * cl; B.upperArmR.rotation.z = -0.5 * cl; B.forearmL.rotation.x -= 0.6 * env; B.forearmR.rotation.x -= 0.6 * env; }
    else if (a === 'hug' || a === 'shake_hands') { B.upperArmR.rotation.x -= 1.3 * env; B.forearmR.rotation.x -= 0.3 * env; if (a === 'hug') { B.upperArmL.rotation.x -= 1.3 * env; B.upperArmL.rotation.z = -0.5 * env; B.upperArmR.rotation.z = 0.5 * env; } }
    else if (a === 'eat' || a === 'drink') { B.upperArmR.rotation.x -= 0.6 * env; B.forearmR.rotation.x -= 2.0 * env; B.head.rotation.x += 0.1 * env; }
    else if (a === 'look') { const tgt = e.target ? subjectPos(e.target, t) : null; if (tgt) { const yaw = angWrap(Math.atan2(tgt.x - st.x, tgt.z - st.z) - st.facing); B.neck.rotation.y = Math.max(-1.1, Math.min(1.1, yaw)) * env; } }
    else if (a === 'pick_up' || a === 'take' || a === 'place' || a === 'drop') {  // reach down to the thing, come back up
      const P = props[e.target], low = P ? (P.root.position.y + 0.1 < 0.6) : true;
      const r = Math.sin(clamp01(u) * Math.PI);
      if (low) { B.spine.rotation.x += 0.75 * r; B.hips.position.y -= 0.22 * r * d.leg; B.thighL.rotation.x -= 0.6 * r; B.thighR.rotation.x -= 0.6 * r; B.shinL.rotation.x += 1.0 * r; B.shinR.rotation.x += 1.0 * r; }
      B.upperArmR.rotation.x -= (low ? 0.9 : 1.2) * r; B.forearmR.rotation.x -= 0.2 * r;
    }
    else if (a === 'open' || a === 'close' || a === 'push' || a === 'pull' || a === 'give') { B.upperArmR.rotation.x -= 1.25 * env; B.forearmR.rotation.x -= 0.25 * env; if (a === 'push') { B.upperArmL.rotation.x -= 1.25 * env; B.spine.rotation.x += 0.2 * env; } }
    else if (a === 'throw') { const ph = clamp01(u); B.upperArmR.rotation.x = ph < 0.5 ? lerp(0, 1.2, ph * 2) : lerp(1.2, -1.8, (ph - 0.5) * 2); }
    else if (a === 'catch') { B.upperArmR.rotation.x -= 1.3 * env; B.upperArmL.rotation.x -= 1.3 * env; }
    else if (a === 'type' || a === 'read') { B.upperArmR.rotation.x -= 0.7 * env; B.upperArmL.rotation.x -= 0.7 * env; B.forearmR.rotation.x -= 0.9 * env; B.forearmL.rotation.x -= 0.9 * env; B.head.rotation.x += 0.3 * env; if (a === 'type') { B.forearmR.rotation.x += Math.sin((t - e.t0) * 14) * 0.08 * env; B.forearmL.rotation.x += Math.sin((t - e.t0) * 13 + 1) * 0.08 * env; } }
    else if (a === 'dance') { const dd = Math.sin((t - e.t0) * 6) * env; B.hips.rotation.y = dd * 0.3; B.upperArmL.rotation.z = (1.4 + dd) * env; B.upperArmR.rotation.z = -(1.4 - dd) * env; B.hips.position.y -= Math.abs(dd) * 0.05; }
  }
  // carrying something: the right forearm bends to hold it
  if (A.holding) { B.upperArmR.rotation.x = Math.min(B.upperArmR.rotation.x, -0.35); B.forearmR.rotation.x = Math.min(B.forearmR.rotation.x, -1.0); }
}

function poseQuadruped(A, st, gs, t) {
  const B = A.B, d = A.dims;
  for (const b of A.bones) if (!['neck', 'head', 'tail'].includes(b.name)) b.rotation.set(0, 0, 0);
  B.neck.rotation.set(-0.6, 0, 0); B.head.rotation.set(0.6, 0, 0); B.tail.rotation.set(-2.2, 0, 0);
  B.hips.position.set(0, d.hipY, 0); B.hips.rotation.set(0, 0, 0);
  const g = st.gaitAmt || 0, ph = (st.dist || 0) / Math.max(0.05, d.stride) * Math.PI * 2, amp = (st.run ? 0.75 : 0.45) * g;
  if (d.gait === 'hop' && st.moving) {  // hops: each stride is a small jump, rear legs push, front legs reach
    const hp = (ph / (Math.PI * 2)) % 1, air = Math.sin(hp * Math.PI);
    B.hips.position.y += air * d.height * 0.32 * g; B.hips.rotation.x = Math.sin(hp * Math.PI * 2) * 0.25 * g;
    for (const s of ['BL', 'BR']) { B['up' + s].rotation.x = (hp < 0.3 ? -0.2 : 0.9) * g; B['lo' + s].rotation.x = (hp < 0.3 ? -0.9 : -0.2) * g; }
    for (const s of ['FL', 'FR']) { B['up' + s].rotation.x = (hp > 0.6 ? 0.3 : -0.7) * g; }
  } else {
    const off = { FL: 0, BR: 0, FR: Math.PI, BL: Math.PI };
    for (const s of ['FL', 'FR', 'BL', 'BR']) { const x = Math.sin(ph + off[s]); B['up' + s].rotation.x = -x * amp; B['lo' + s].rotation.x = (s[0] === 'F' ? 1 : -1) * Math.max(0, Math.sin(ph + off[s] - 1.0)) * amp * 1.2; }
    B.hips.position.y += Math.abs(Math.sin(ph)) * 0.02 * g * d.height;
  }
  B.tail.rotation.z = Math.sin(t * 3 + (st.dist || 0)) * 0.25;
  const c = st.crouch || 0;
  if (c) { B.hips.position.y -= c * 0.3 * d.leg; for (const s of ['FL', 'FR', 'BL', 'BR']) { B['up' + s].rotation.x += (s[0] === 'F' ? -0.5 : 0.6) * c; B['lo' + s].rotation.x += (s[0] === 'F' ? 0.9 : -0.9) * c; } }
  if (st.air) { const a = Math.sin(st.air * Math.PI); for (const s of ['FL', 'FR']) B['up' + s].rotation.x -= 0.9 * a; for (const s of ['BL', 'BR']) B['up' + s].rotation.x += 0.9 * a; B.hips.rotation.x = (0.5 - st.air) * 0.6; }
  if (st.sit) { B.hips.rotation.x = -0.5 * st.sit; for (const s of ['BL', 'BR']) { B['up' + s].rotation.x = -1.2 * st.sit; B['lo' + s].rotation.x = 1.6 * st.sit; } B.hips.position.y -= d.leg * 0.35 * st.sit; }
  if (st.lie) { B.hips.position.y = lerp(B.hips.position.y, d.hipY * 0.45, st.lie); for (const s of ['FL', 'FR', 'BL', 'BR']) B['up' + s].rotation.x = lerp(B['up' + s].rotation.x, s[0] === 'F' ? -1.3 : 1.3, st.lie); }
  for (const { e, u } of gs) {
    const env = Math.sin(clamp01(u) * Math.PI);
    if (e.action === 'eat' || e.action === 'drink') { B.neck.rotation.x -= -1.0 * env; B.head.rotation.x += 0.4 * env; }
    else if (e.action === 'look' || e.action === 'meet' || e.action === 'greet' || e.action === 'talk') { const tgt = e.target ? subjectPos(e.target, t) : null; if (tgt) { const yaw = angWrap(Math.atan2(tgt.x - st.x, tgt.z - st.z) - st.facing); B.neck.rotation.y = Math.max(-1, Math.min(1, yaw)) * env; } B.head.rotation.x += Math.sin((t - e.t0) * 6) * 0.08 * env; B.tail.rotation.z += Math.sin(t * 12) * 0.4 * env; }
  }
}

function poseBird(A, st, gs, t) {
  const B = A.B, f = st.fly > 0.05 || st.air;
  const flap = f ? Math.sin(t * 18) * 0.9 : -0.1;
  B.wingL.rotation.z = flap; B.wingR.rotation.z = -flap;
  B.hips.rotation.x = f ? -0.25 : 0; B.head.rotation.x = !f && (st.gaitAmt || 0) ? Math.sin((st.dist || 0) * 20) * 0.2 : 0;
}

// ------------------------------------------------------------------------------------------------ props over time
function propState(P, t) {
  const p = P.spec, base = new THREE.Vector3(num(p.x, 0), num(p.y, p.type === 'cloud' ? 14 : 0), num(p.z, 0));
  const st = { pos: base.clone(), rotY: num(p.rot, 0) * DEG, spin: 0, spinAxis: 'y', scale: num(p.scale, 1), explode: 0, open: 0, color: null, visible: true, held: null, fx: [] };
  if (p.hidden_until != null && t < num(p.hidden_until, 0)) st.visible = false;
  for (const e of events) {
    if (e.actor !== p.id && !(e.target === p.id && ['open', 'close', 'light_on', 'light_off'].includes(e.action))) continue;
    if (t < e.t0) continue;
    const u = clamp01((t - e.t0) / (e.t1 - e.t0)), k = (e.ease && EASE[e.ease] ? EASE[e.ease] : EASE.inOut)(u);
    switch (e.action) {
      case 'rotate': case 'spin': st.spin += num(e.degrees, 360) * DEG * (e.action === 'spin' ? u : k); st.spinAxis = e.axis || 'y'; break;
      case 'orbit': { const r = num(e.radius, 2), a0 = num(e.from_angle, 0) * DEG, a = a0 + num(e.degrees, 360) * DEG * k, c = resolvePoint(e.center || { x: 0, z: 0 }); st.pos.set(c.x + Math.sin(a) * r, st.pos.y, c.z + Math.cos(a) * r); break; }
      case 'explode': st.explode = lerp(st.explode, num(e.amount, 1), k); break;
      case 'assemble': st.explode = lerp(st.explode, 0, k); break;
      case 'move': case 'drive': case 'fall': {
        const to = resolvePoint(e.to, st.pos), y1 = e.action === 'fall' ? 0 : num(e.to && e.to.y, st.pos.y);
        if (e.action === 'drive' || p.type === 'car' || p.type === 'bike' || p.type === 'bicycle') { const h = facingOf(st.pos, to, st.rotY); st.rotY = angLerp(st.rotY, h, clamp01(u * 4)); st.travel = (st.travel || 0) + st.pos.distanceTo(to) * travel(u); }
        const arc = num(e.arc, 0);
        st.pos.set(lerp(st.pos.x, to.x, e.action === 'drive' ? travel(u) : k), lerp(st.pos.y, y1, e.action === 'fall' ? EASE.in(u) : k) + Math.sin(u * Math.PI) * arc, lerp(st.pos.z, to.z, e.action === 'drive' ? travel(u) : k));
        if (u >= 1) st.pos.y = y1; break; }
      case 'scale': st.scale = lerp(st.scale, num(e.to_scale ?? e.amount, 1.5), k); break;
      case 'float': st.pos.y += num(e.height, 0.15) * Math.sin((t - e.t0) * 2.2) * (u < 1 ? 1 : 0); break;
      case 'bounce': st.pos.y += Math.abs(Math.sin((t - e.t0) * 6)) * num(e.height, 0.3) * (1 - u); break;
      case 'appear': st.visible = true; st.scale *= u < 1 ? EASE.out(u) : 1; break;
      case 'disappear': st.scale *= 1 - k; if (u >= 1) st.visible = false; break;
      case 'color': st.color = { to: e.color, k }; break;
      case 'morph': st.scale *= 1 - k; if (u >= 1) st.visible = false; break;
      case 'open': st.open = lerp(st.open, 1, EASE.inOut(clamp01((u - 0.25) / 0.75))); break;
      case 'close': st.open = lerp(st.open, 0, EASE.inOut(clamp01((u - 0.2) / 0.8))); break;
      case 'light_on': st.light = u; break; case 'light_off': st.light = 1 - u; break;
    }
  }
  // a morph target grows where its source shrinks
  for (const e of events) if (e.action === 'morph' && e.into === p.id && t >= e.t0) { const k = EASE.inOut(clamp01((t - e.t0) / (e.t1 - e.t0))); st.visible = k > 0; st.scale *= k; }
  if (events.some((e) => e.action === 'morph' && e.into === p.id) && !events.some((e) => e.action === 'morph' && e.into === p.id && t >= e.t0)) st.visible = false;
  st.held = holdingAt(p.id, t);
  return st;
}

// ------------------------------------------------------------------------------------------------ build all
for (const p of spec.props || []) { try { buildProp(p); } catch (e) { fail('prop ' + p.id + ': ' + e.message); } }
// a door that belongs in a room's doorway is placed there
for (const P of Object.values(props)) {
  if (P.kind === 'door' && P.spec.room && props[P.spec.room] && props[P.spec.room].isRoom) {
    const R = props[P.spec.room], wp = R.root.localToWorld(R.anchors.doorway.clone());
    P.root.position.set(wp.x, 0, wp.z); P.root.rotation.y = R.root.rotation.y + R.doorRot; P.spec.x = wp.x; P.spec.z = wp.z; P.spec.rot = P.root.rotation.y / DEG;
  }
}
// a small thing placed on a pedestal, platform or table stands on its top (never sunk inside it)
const SUPPORTS = new Set(['pedestal', 'platform', 'stage', 'table', 'desk', 'counter', 'shelf', 'box', 'crate']);
for (const P of Object.values(props)) {
  if (P.spec.y != null || SUPPORTS.has(P.kind) && P.kind !== 'box' || P.environmental) continue;
  for (const Q of Object.values(props)) {
    if (Q === P || !SUPPORTS.has(Q.kind)) continue;
    const local = Q.root.worldToLocal(P.root.position.clone()), hw = num(Q.spec.w, 1) / 2 + 0.05, hd = num(Q.spec.d, Q.kind === 'pedestal' || Q.kind === 'platform' || Q.kind === 'stage' ? num(Q.spec.w, 1) : 1) / 2 + 0.05;
    if (Math.abs(local.x) > hw || Math.abs(local.z) > hd) continue;
    const box = new THREE.Box3().setFromObject(Q.root); if (box.isEmpty()) continue;
    P.spec.y = box.max.y; P.root.position.y = box.max.y; break;
  }
}
for (const P of Object.values(props)) if (P.spec.asset) { try { await loadAsset(P); } catch (e) { fail('asset ' + P.spec.asset + ': ' + e.message); } }
scene.updateMatrixWorld(true);
for (const a of spec.actors || []) { try { buildActor(a); } catch (e) { fail('actor ' + a.id + ': ' + e.message); } }
for (const A of Object.values(actors)) planActor(A);
for (const A of Object.values(actors)) {  // gait distance accumulated over earlier moves (the stride never resets)
  const cum = new Map(); let acc = 0; for (const seg of A.loco) { cum.set(seg, acc); acc += seg.len || 0; }
  A.distBefore = (seg) => cum.get(seg) || 0;
}

// scattered set dressing (trees, rocks...), never on anyone's path or on a placed prop
const pathPts = [];
for (const A of Object.values(actors)) for (const seg of A.loco) if (seg.pts) for (let i = 1; i < seg.pts.length; i++) for (let k = 0; k <= 10; k++) pathPts.push(seg.pts[i - 1].clone().lerp(seg.pts[i], k / 10));
for (const A of Object.values(actors)) pathPts.push(new THREE.Vector3(A.plan[0].x, 0, A.plan[0].z));
// hills and mountains are backdrop: never on anyone's path (pushed back along the line from the path)
for (const P of Object.values(props)) {
  if (P.kind !== 'hill' && P.kind !== 'mountain' || !pathPts.length) continue;
  const r = num(P.spec.w, 40) / 2 + 2, c = new THREE.Vector3(P.root.position.x, 0, P.root.position.z);
  let near = pathPts[0]; for (const q of pathPts) if (q.distanceTo(c) < near.distanceTo(c)) near = q;
  const d = near.distanceTo(c);
  if (d >= r) continue;
  const dir = c.clone().sub(near); if (dir.lengthSq() < 1e-6) dir.set(0, 0, -1); dir.normalize();
  const moved = near.clone().addScaledVector(dir, r);
  P.root.position.x = moved.x; P.root.position.z = moved.z; P.spec.x = moved.x; P.spec.z = moved.z;
}
for (const [n, sc] of (ENV.scatter || []).entries()) {
  const r = rng(hashStr('scatter' + n + (sc.type || ''))), area = sc.area || [-SC, -SC, SC, SC];
  let placed = 0;
  for (let tries = 0; tries < num(sc.count, 10) * 8 && placed < Math.min(120, num(sc.count, 10)); tries++) {
    const x = lerp(area[0], area[2], r()), z = lerp(area[1], area[3], r()), here = new THREE.Vector3(x, 0, z);
    if (pathPts.some((q) => q.distanceTo(here) < num(sc.clearance, 2.2))) continue;
    if (Object.values(props).some((P) => !P.environmental && P.root.position.distanceTo(here) < 2)) continue;
    if (Object.values(props).some((P) => (P.kind === 'river' || P.kind === 'road' || P.kind === 'street') && Math.abs(P.root.worldToLocal(here.clone()).x) < (P.span ? P.span.w : 7) / 2 + 1)) continue;
    const s = num(sc.size, 1) * (0.75 + r() * 0.5);
    const id = `S${n}_${placed}`;
    const P = buildProp({ id, type: sc.type || 'tree', x, z, rot: r() * 360, color: sc.color, h: sc.type === 'rock' ? undefined : (sc.type === 'bush' ? undefined : 5 * s), w: sc.type === 'rock' ? 0.8 * s : sc.type === 'bush' ? 1.0 * s : undefined });
    P.scatter = true; placed++;
  }
}

// ------------------------------------------------------------------------------------------------ bake actor clips
// Each actor's bones are sampled from the event timeline at every frame and baked into an AnimationClip that an
// AnimationMixer plays: the same clip (and the same time) drives every pass.
const mixers = [];
const timelineJSON = { fps: FPS, frames: NF, duration: DUR, clips: {} };
function applyActor(A, t) {
  const st = actorState(A, t), gs = gestures(A, t);
  A.root.position.set(st.x, st.y, st.z); A.root.rotation.set(0, st.facing, 0);
  A.holding = Object.values(props).some((P) => { const h = holdingAt(P.spec.id, t); return h && h.actor === A.id; });
  if (A.kind === 'humanoid') poseHumanoid(A, st, gs, t); else if (A.kind === 'quadruped') poseQuadruped(A, st, gs, t); else poseBird(A, st, gs, t);
  return st;
}
for (const A of Object.values(actors)) {
  const times = [], tracks = {}, rootPos = [], rootQuat = [];
  for (const b of A.bones) tracks[b.name] = { q: [], p: [] };
  for (let f = 0; f < NF; f++) {
    const t = f / FPS; times.push(t); applyActor(A, t);
    rootPos.push(A.root.position.x, A.root.position.y, A.root.position.z); rootQuat.push(...A.root.quaternion.toArray());
    for (const b of A.bones) { tracks[b.name].q.push(...b.quaternion.toArray()); tracks[b.name].p.push(b.position.x, b.position.y, b.position.z); }
  }
  A.root.name = 'root_' + A.id;
  const kf = [new THREE.VectorKeyframeTrack(A.root.name + '.position', times, rootPos), new THREE.QuaternionKeyframeTrack(A.root.name + '.quaternion', times, rootQuat)];
  for (const b of A.bones) { b.name = A.id + '_' + b.name; kf.push(new THREE.QuaternionKeyframeTrack(b.name + '.quaternion', times, tracks[b.name.slice(A.id.length + 1)].q)); kf.push(new THREE.VectorKeyframeTrack(b.name + '.position', times, tracks[b.name.slice(A.id.length + 1)].p)); }
  const clip = new THREE.AnimationClip('timeline_' + A.id, DUR, kf);
  const holder = new THREE.Group(); scene.add(holder); holder.add(A.root);  // the mixer root finds the actor by name
  const mixer = new THREE.AnimationMixer(holder); const act = mixer.clipAction(clip); act.setLoop(THREE.LoopOnce, 1); act.clampWhenFinished = true; act.play();
  mixers.push(mixer); A.mixer = mixer;
  timelineJSON.clips[A.id] = { tracks: kf.length, bones: A.bones.length, keyframes: times.length };
}

// ------------------------------------------------------------------------------------------------ apply a frame
const tmpV = new THREE.Vector3();
function applyProps(t) {
  for (const P of Object.values(props)) {
    if (P.scatter) continue;
    const st = propState(P, t);
    P.root.visible = st.visible;
    P.root.position.copy(st.pos); P.root.rotation.set(0, st.rotY, 0);
    P.inner.rotation.set(0, 0, 0);
    if (st.spin) P.inner.rotation[st.spinAxis === 'x' ? 'x' : st.spinAxis === 'z' ? 'z' : 'y'] = st.spin;
    P.inner.scale.setScalar(Math.max(1e-4, st.scale));
    for (const part of P.parts) part.position.copy(part.userData.home).addScaledVector(part.userData.explode, st.explode * num(P.spec.size, 0.3));
    if (P.hinge) P.hinge.rotation.y = (P.openSign || -1) * st.open * num(P.spec.open_degrees, 95) * DEG;
    if (P.light) P.light.intensity = (st.light ?? 1) * (night ? 30 : 6);
    if (P.wheels && st.travel) for (const w of P.wheels) w.rotation.x = st.travel / P.wheelR;
    if (P.water && P.water.material.map) P.water.material.map.offset.set(0, -t * 0.08);
    if (st.color) { const target = col(st.color.to); P.inner.traverse((o) => { if (o.isMesh && o.material.color) { if (!o.userData.baseColor) { o.material = o.material.clone(); o.userData.baseColor = o.material.color.clone(); } o.material.color.copy(o.userData.baseColor).lerp(target, st.color.k); } }); }
    // held: follows the holder's hand; released: falls to the ground where it was let go (or goes where placed)
    if (st.held && st.held.actor && actors[st.held.actor]) {
      const hand = actors[st.held.actor].B.handR || actors[st.held.actor].B.head; hand.updateWorldMatrix(true, false);
      hand.getWorldPosition(tmpV); P.root.position.set(tmpV.x, tmpV.y - 0.12, tmpV.z); P.root.rotation.y = actors[st.held.actor].root.rotation.y;
    } else if (st.held && st.held.released) {
      const c = st.held.released, rel = releasePos(c), dt = t - c.t;
      const dest = c.to ? resolvePoint(c.to, rel) : new THREE.Vector3(rel.x, 0, rel.z);
      if (c.to && c.to.y != null) dest.y = num(c.to.y, 0); else if (c.to && typeof c.to === 'string' && props[c.to] && props[c.to].anchors.top) dest.y = props[c.to].root.localToWorld(props[c.to].anchors.top.clone()).y;
      const fallT = c.e.action === 'throw' ? 0.6 : 0.35, k = clamp01(dt / fallT);
      P.root.position.set(lerp(rel.x, dest.x, k), lerp(rel.y, dest.y, c.e.action === 'place' ? EASE.inOut(k) : k * k) + (c.e.action === 'throw' ? Math.sin(k * Math.PI) * 1.2 : 0), lerp(rel.z, dest.z, k));
    }
  }
}
const releaseCache = new Map();
function releasePos(c) {  // where the holder's hand was at the moment of release (computed once)
  if (releaseCache.has(c)) return releaseCache.get(c);
  const A = actors[c.actor]; let p = new THREE.Vector3();
  if (A) { A.mixer.setTime(Math.min(c.t, DUR - 1e-4)); A.root.updateMatrixWorld(true); (A.B.handR || A.B.head).getWorldPosition(p); p.y -= 0.12; }
  releaseCache.set(c, p.clone()); return p;
}
for (const c of holdChanges) if (!c.held) releasePos(c);  // once, before any frame is drawn
for (const A of Object.values(actors)) A.mixer.setTime(0);

// ------------------------------------------------------------------------------------------------ camera
const shots = (spec.camera || [{ t0: 0, t1: DUR, move: 'follow' }]).map((s) => ({ ...s, t0: num(s.t0, 0), t1: num(s.t1, DUR) }));
function targetOf(s, t) {
  const ids = Array.isArray(s.target) ? s.target : s.target ? [s.target] : Object.keys(actors).slice(0, 1).concat(Object.keys(actors).length ? [] : Object.keys(props).slice(0, 1));
  const pts = []; let hgt = 1.2, size = 0;
  for (const id of ids) {
    if (typeof id === 'object') { pts.push(new THREE.Vector3(num(id.x, 0), num(id.y, 1), num(id.z, 0))); continue; }
    if (actors[id]) { const st = actorState(actors[id], t); pts.push(new THREE.Vector3(st.x, st.y, st.z)); hgt = Math.min(hgt, actors[id].dims.height * (actors[id].kind === 'humanoid' ? (st.sit ? 0.5 : 0.78) : 0.6)); size = Math.max(size, actors[id].dims.height, actors[id].dims.L || 0); }
    else if (props[id]) { const st = propState(props[id], t), P = props[id]; pts.push(st.pos.clone()); hgt = Math.min(hgt, P.kind === 'product' ? num(P.spec.size, 0.3) * 0.5 : Math.min(1.2, num(P.spec.h, 1) * 0.5)); size = Math.max(size, P.kind === 'product' ? num(P.spec.size, 0.3) : Math.max(num(P.spec.h, 1), num(P.spec.w, 1))); }
  }
  if (!pts.length) pts.push(new THREE.Vector3(0, 0, 0));
  const c = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / pts.length);
  const spread = pts.reduce((m, p) => Math.max(m, p.distanceTo(c)), 0);
  c.y += hgt; return { c, spread, ids, size: size || 1.7 };
}
function smoothTarget(s, t) {  // averaged over the last 0.4 s: the camera follows like an operator, without jitter
  const acc = new THREE.Vector3(); let n = 0, sp = 0, size = 1.7;
  for (let k = 0; k < 8; k++) { const tt = Math.max(s.t0, t - k * 0.05); const r = targetOf(s, tt); acc.add(r.c); sp += r.spread; size = r.size; n++; }
  return { c: acc.multiplyScalar(1 / n), spread: sp / n, size };
}
function facingOfTarget(s, t) {
  const id = Array.isArray(s.target) ? s.target[0] : s.target;
  if (actors[id]) { let a = 0, b = 0; for (let k = 0; k < 12; k++) { const st = actorState(actors[id], Math.max(s.t0, t - k * 0.06)); a += Math.sin(st.facing); b += Math.cos(st.facing); } return Math.atan2(a, b); }
  return 0;
}
const raycaster = new THREE.Raycaster();
const blockers = [];  // walls and solid furniture: the camera stops in front of them, never inside
for (const P of Object.values(props)) if ((P.scatter ? ['tree', 'pine', 'palm', 'rock', 'boulder'].includes(P.kind) : true) && !P.environmental && ['table', 'desk', 'counter', 'sofa', 'couch', 'bed', 'shelf', 'bookshelf', 'cabinet', 'fridge', 'wardrobe', 'car', 'vehicle', 'truck', 'bus', 'van', 'wall', 'tree', 'pine', 'palm', 'rock', 'boulder'].includes(P.kind)) P.root.traverse((o) => { if (o.isMesh) blockers.push(o); });
function rawCamera(t) {
  const s = shots.find((x) => t >= x.t0 && t < x.t1) || shots[shots.length - 1];
  const u = clamp01((t - s.t0) / Math.max(1e-3, s.t1 - s.t0)), k = (EASE[s.ease] || EASE.inOut)(u);
  const { c, spread, size } = smoothTarget(s, t);
  let dist = num(s.distance, Math.max(1.0, Math.min(14, size * 2.6 + spread * 1.6))), hgt = num(s.height, size < 1 ? Math.max(0.35, size * 1.4) + 1.2 - c.y : 1.6), fov = num(s.fov, 40), ang = num(s.angle, 30) * DEG;
  const rel = ['follow', 'reverse_dolly', 'lead', 'handheld_follow', 'track_side', 'over_shoulder', 'chase'].includes(s.move);
  let base = rel ? facingOfTarget(s, t) + ang : ang, look = c.clone(), pos;
  const at = (a, d, h, from = c) => new THREE.Vector3(from.x + Math.sin(a) * d, from.y - 1.2 + h, from.z + Math.cos(a) * d);
  const start = s._start || (s._start = targetOf(s, s.t0).c.clone());
  switch (s.move) {
    case 'static': pos = at(ang, dist, hgt, s.lock ? start : start); if (s.lock) look = start.clone(); break;
    case 'dolly_in': case 'push_in': pos = at(ang, lerp(dist, dist * num(s.to, 0.55), k), hgt); break;
    case 'dolly_out': case 'pull_out': pos = at(ang, lerp(dist, dist * num(s.to, 1.7), k), hgt); break;
    case 'reverse_dolly': case 'lead': pos = at(base, dist + k * 0.8, hgt); break;
    case 'orbit': case 'arc': case 'turntable': pos = at(ang + num(s.sweep, s.move === 'turntable' ? 360 : 60) * DEG * k, dist, hgt); break;
    case 'crane_up': pos = at(ang, dist, hgt + num(s.rise, 2.5) * k); look.y -= 0.4 * k; break;
    case 'crane_down': pos = at(ang, dist, hgt + num(s.rise, 2.5) * (1 - k)); break;
    case 'crane_arc': { const e = EASE.cubic(u); pos = at(ang + num(s.sweep, 180) * DEG * e, dist - Math.max(0, e - 0.6) * dist * 0.95, hgt + num(s.rise, 1.65) * e); break; }
    case 'handheld_follow': case 'handheld': pos = at(base, dist - k * 0.6, hgt); pos.x += Math.sin(t * 2.3) * 0.05 + Math.sin(t * 5.1) * 0.02; pos.y += Math.cos(t * 1.9) * 0.03; break;
    case 'track_side': case 'truck': pos = at(facingOfTarget(s, t) + (s.side === 'left' ? -1 : 1) * Math.PI / 2, dist, hgt); break;
    case 'pan': case 'parallax_pan': { const side = new THREE.Vector3(Math.cos(ang), 0, -Math.sin(ang)); pos = at(ang, dist, hgt, start).addScaledVector(side, lerp(-num(s.span, 1), num(s.span, 1), k)); break; }
    case 'tilt_up': pos = at(ang, dist, hgt, start); look = start.clone(); look.y = lerp(start.y - 0.6, start.y + num(s.rise, 3), k); break;
    case 'tilt_down': pos = at(ang, dist, hgt, start); look = start.clone(); look.y = lerp(start.y + num(s.rise, 3), start.y - 0.6, k); break;
    case 'vertigo': case 'dolly_zoom': { const f0 = num(s.fov, 25), f1 = num(s.fov_to, 55); fov = lerp(f0, f1, k); const d1 = dist * Math.tan(f0 * DEG / 2) / Math.tan(fov * DEG / 2); pos = at(ang, d1, hgt); break; }
    case 'top_down': case 'birds_eye': pos = new THREE.Vector3(c.x + 0.01, c.y + num(s.height, 12), c.z + 0.01); break;
    case 'over_shoulder': { const ids = Array.isArray(s.target) ? s.target : [s.target]; const a = actors[ids[0]]; const st = a ? actorState(a, t) : null; const other = ids[1] ? targetOf({ target: ids[1] }, t).c : c;
      if (st) { const sh = new THREE.Vector3(st.x, a.dims.height * 0.92, st.z); const dir = other.clone().sub(sh).setY(0).normalize(); pos = sh.clone().addScaledVector(dir, -0.9).add(new THREE.Vector3(dir.z, 0, -dir.x).multiplyScalar(0.45)); look = other.clone(); } else pos = at(ang, dist, hgt); break; }
    case 'two_shot': { const ids = Array.isArray(s.target) ? s.target : [s.target]; if (ids.length > 1) { const p1 = targetOf({ target: ids[0] }, t).c, p2 = targetOf({ target: ids[1] }, t).c; const mid = p1.clone().add(p2).multiplyScalar(0.5); const perp = Math.atan2(p2.x - p1.x, p2.z - p1.z) + Math.PI / 2 + ang; pos = at(perp, Math.max(dist, p1.distanceTo(p2) * 1.3), hgt, mid); look = mid; } else pos = at(ang, dist, hgt); break; }
    case 'follow': case 'chase': default: pos = at(base, dist, hgt);
  }
  pos.y = Math.max(0.25, pos.y);
  return { pos, look, fov, shot: s, shotIndex: shots.indexOf(s) };
}
// Never through a wall, a tree or furniture - and never a snap: the share of the camera-to-subject line that is
// clear is eroded (min) then blurred (mean) over +-0.5 s inside the shot, so the camera eases in before an
// obstacle and back out after it. Deterministic (a function of time only), cached per 25 ms.
const clearCache = new Map();
function clearance(t) {
  const key = Math.round(t * 40); if (clearCache.has(key)) return clearCache.get(key);
  const cam = rawCamera(key / 40), s = cam.shot; let r = 1;
  const hitters = occluders.concat(blockers.filter((m) => !(s.target && m.userData.owner && [].concat(s.target).includes(m.userData.owner.id))));
  if (hitters.length && s.move !== 'top_down') {
    const dir = cam.pos.clone().sub(cam.look), L = dir.length(); dir.normalize();
    raycaster.set(cam.look, dir); raycaster.far = L;
    const hit = raycaster.intersectObjects(hitters, false)[0];
    if (hit && hit.distance > 0.6 && L > 1e-3) r = Math.max(0.05, (hit.distance - 0.3) / L);
  }
  clearCache.set(key, r); return r;
}
function cameraAt(t) {
  const cam = rawCamera(t), s = cam.shot;
  if (!(occluders.length || blockers.length) || s.move === 'top_down') return cam;
  const inShot = (x) => Math.min(s.t1 - 1e-3, Math.max(s.t0, x));
  const eroded = (x) => { let m = 1; for (let k = -2; k <= 2; k++) m = Math.min(m, clearance(inShot(x + k * 0.125))); return m; };
  let acc = 0; for (let k = -4; k <= 4; k++) acc += eroded(inShot(t + k * 0.125)); const r = Math.min(1, acc / 9);
  if (r < 0.999) { cam.pos = cam.look.clone().add(cam.pos.clone().sub(cam.look).multiplyScalar(r)); cam.pos.y = Math.max(0.25, cam.pos.y); }
  return cam;
}

// ------------------------------------------------------------------------------------------------ passes
const depthMat = new THREE.ShaderMaterial({
  uniforms: { near: { value: 0.5 }, far: { value: 60 } },
  vertexShader: 'varying float vD; void main(){ vec4 mv = modelViewMatrix * vec4(position, 1.0); vD = -mv.z; gl_Position = projectionMatrix * mv; }',
  fragmentShader: 'uniform float near; uniform float far; varying float vD; void main(){ float d = clamp((vD - near) / (far - near), 0.0, 1.0); float v = 1.0 - sqrt(d); gl_FragColor = vec4(vec3(v), 1.0); }',
});
const normalMat = new THREE.MeshNormalMaterial();
const black = new THREE.Color(0, 0, 0), normalBg = new THREE.Color(0.5, 0.5, 1.0);
let ci = 0, oi = 0;
for (const A of Object.values(actors)) maskColors.characters[A.id] = MASK_PALETTE[ci++ % MASK_PALETTE.length];
for (const P of Object.values(props)) if (!P.scatter && !P.environmental) maskColors.objects[P.spec.id] = MASK_PALETTE[oi++ % MASK_PALETTE.length];
const maskMats = {};
const maskMat = (hex) => (maskMats[hex] ||= new THREE.MeshBasicMaterial({ color: new THREE.Color(hex), toneMapped: false, fog: false }));
const blackMat = new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false, fog: false });
const allMeshes = []; scene.traverse((o) => { if (o.isMesh) allMeshes.push(o); });
for (const m of allMeshes) m.userData.rgbMat = m.material;
let lightsOn = [];
function setPass(pass) {
  const rgb = pass === 'rgb';
  scene.overrideMaterial = pass === 'depth' ? depthMat : pass === 'normal' ? normalMat : null;
  scene.background = rgb ? skyTexture : pass === 'normal' ? normalBg : black;
  scene.fog = rgb ? fog : null;
  renderer.toneMapping = rgb ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
  renderer.shadowMap.autoUpdate = rgb;
  for (const m of allMeshes) {
    if (pass === 'mask_characters') m.material = m.userData.owner && m.userData.owner.kind === 'character' ? maskMat(maskColors.characters[m.userData.owner.id]) : blackMat;
    else if (pass === 'mask_objects') m.material = m.userData.owner && m.userData.owner.kind === 'object' && maskColors.objects[m.userData.owner.id] ? maskMat(maskColors.objects[m.userData.owner.id]) : blackMat;
    else m.material = m.userData.colorMat || m.userData.rgbMat;
  }
}
// keep colour-animated materials (cloned during play) as the RGB material
function syncRgbMats() { for (const m of allMeshes) if (m.userData.baseColor) m.userData.colorMat = m.material; }

// ------------------------------------------------------------------------------------------------ metadata
const JOINTS = ['head', 'neck', 'chest', 'hips', 'upperArmL', 'forearmL', 'handL', 'upperArmR', 'forearmR', 'handR', 'thighL', 'shinL', 'footL', 'thighR', 'shinR', 'footR'];
function project(v) { const p = v.clone().project(camera); return [(p.x + 1) / 2, (1 - p.y) / 2, p.z]; }
function bbox2d(obj) {
  const box = new THREE.Box3().setFromObject(obj); if (box.isEmpty()) return null;
  const xs = [], ys = []; let behind = 0;
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) { const [u, v, d] = project(new THREE.Vector3(x, y, z)); if (d > 1) behind++; xs.push(u); ys.push(v); }
  if (behind === 8) return { box: null, visible: 0 };
  const b = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  const cl = [Math.max(0, b[0]), Math.max(0, b[1]), Math.min(1, b[2]), Math.min(1, b[3])];
  const area = Math.max(1e-9, (b[2] - b[0]) * (b[3] - b[1])), ca = Math.max(0, cl[2] - cl[0]) * Math.max(0, cl[3] - cl[1]);
  return { box: b.map((x) => +x.toFixed(4)), visible: +(ca / area).toFixed(3), area: +ca.toFixed(4) };
}
function metaFor(f, t, cam) {
  const m = { f, t: +t.toFixed(4), shot: cam.shotIndex, camera: { pos: cam.pos.toArray().map((x) => +x.toFixed(4)), look: cam.look.toArray().map((x) => +x.toFixed(4)), fov: +cam.fov.toFixed(3), near: camera.near, far: depthMat.uniforms.far.value }, actors: {}, props: {}, events: [] };
  for (const A of Object.values(actors)) {
    const st = actorState(A, t), bb = bbox2d(A.root), pose = {};
    const chest = new THREE.Vector3(st.x, st.y + A.dims.height * 0.6, st.z), toA = chest.clone().sub(camera.position), dA = toA.length();
    raycaster.set(camera.position, toA.normalize()); raycaster.far = dA;
    const first = raycaster.intersectObjects(allMeshes.filter((o) => o.visible), false).find((h) => h.object.material !== undefined);
    const occluded = !!(first && first.object.userData.owner && first.object.userData.owner.id !== A.id && first.distance < dA - 0.15);
    if (A.kind === 'humanoid') for (const j of JOINTS) { const b = A.B[j]; if (b) { const [u, v] = project(b.getWorldPosition(new THREE.Vector3())); pose[j] = [+u.toFixed(4), +v.toFixed(4)]; } }
    m.actors[A.id] = { occluded, occluder: occluded ? first.object.userData.owner.id : null, pos: [+st.x.toFixed(4), +st.y.toFixed(4), +st.z.toFixed(4)], facing: +(st.facing / DEG).toFixed(2), action: st.action, sit: +(st.sit || 0).toFixed(3), bbox: bb && bb.box, visible: bb ? bb.visible : 0, area: bb ? bb.area : 0, pose };
  }
  for (const P of Object.values(props)) {
    if (P.scatter || P.environmental) continue;
    const bb = P.root.visible ? bbox2d(P.root) : null;
    m.props[P.spec.id] = { pos: P.root.position.toArray().map((x) => +x.toFixed(4)), visible: bb ? bb.visible : 0, bbox: bb && bb.box, area: bb ? bb.area : 0, shown: P.root.visible };
  }
  for (const e of events) if (t >= e.t0 && t <= e.t1) m.events.push(e.id);
  return m;
}

// ------------------------------------------------------------------------------------------------ the API
let lastF = -1, lastCam = null;
function setFrame(f) {
  const t = Math.min(f / FPS, DUR - 1e-4);
  for (const mx of mixers) mx.setTime(t);
  for (const A of Object.values(actors)) A.holding = Object.values(props).some((P) => { const h = holdingAt(P.spec.id, t); return h && h.actor === A.id; });
  scene.updateMatrixWorld(true);
  applyProps(t);
  syncRgbMats();
  const cam = cameraAt(t);
  camera.position.copy(cam.pos); camera.fov = cam.fov; camera.updateProjectionMatrix(); camera.lookAt(cam.look);
  sun.target.position.copy(cam.look).setY(0); sun.position.copy(sun.target.position).add(sunOffset); sun.target.updateMatrixWorld();
  const d = cam.pos.distanceTo(cam.look); depthMat.uniforms.near.value = 0.1; depthMat.uniforms.far.value = Math.max(12, Math.min(150, d * 5));
  scene.updateMatrixWorld(true);
  lastF = f; lastCam = cam; return t;
}
window.R3D = {
  frames: NF, fps: FPS, duration: DUR, width: W, height: H,
  frame(f, passes = ['rgb'], jpegQ = 0.92) {
    const t = setFrame(f), images = {};
    for (const p of passes) { setPass(p); renderer.render(scene, camera); images[p] = canvas.toDataURL(p.startsWith('mask') ? 'image/png' : 'image/jpeg', jpegQ); }
    setPass('rgb');
    return { images, meta: metaFor(f, t, lastCam) };
  },
  meta(f) { const t = setFrame(f); return metaFor(f, t, lastCam); },
  info() {
    return { frames: NF, fps: FPS, duration: DUR, width: W, height: H, masks: maskColors, timeline: timelineJSON,
             actors: Object.fromEntries(Object.values(actors).map((A) => [A.id, { kind: A.kind, height: A.dims.height, plan: A.plan.map((s) => ({ x: +s.x.toFixed(3), z: +s.z.toFixed(3), facing: +(s.facing / DEG).toFixed(1) })) }])),
             props: Object.fromEntries(Object.values(props).filter((P) => !P.scatter).map((P) => [P.spec.id, { type: P.kind, anchors: Object.fromEntries(Object.entries(P.anchors).map(([k, v]) => [k, P.root.localToWorld(v.clone()).toArray().map((x) => +x.toFixed(3))])) }])),
             scattered: Object.values(props).filter((P) => P.scatter).length, meshes: allMeshes.length, errors: window.R3D_ERROR || null };
  },
};
setFrame(0); setPass('rgb'); renderer.render(scene, camera);
if (!window.R3D_ERROR) window.R3D_READY = true;
