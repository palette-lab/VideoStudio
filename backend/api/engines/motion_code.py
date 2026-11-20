"""Motion graphics with NO video model: the LLM looks at a scene's keyframe and the Motion Graphics Director's motion
prompt (its 6-second timeline and SFX) and plans the motion - a camera move over the whole frame, the picture's
layers entering one by one (title, subject, objects, accents), their idle micro-motion and their sound effects - and
code renders it frame by frame (the vendored Lemo-Opuscar headless-Chrome renderer, tools/opuscar), the way the VOX
Documentary animates its illustrations.

    plan_scene   one vision call: camera + 2-6 layer boxes with entry, start time, idle and SFX (safe default on failure)
    page         a deterministic canvas page: plate -> layers enter -> full keyframe locks in -> idle, all under the
                 camera move for the whole scene
    animate      keyframe -> fitted to the aspect -> plate -> page -> rendered mp4 -> SFX mixed in (no loudness boost)
                 -> one clip of the scene's length; if the page cannot render, a plain ffmpeg camera move of the still
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from router_core import llm

from . import opuscar_engine as oc
from . import vox_doc_engine as vox

log = logging.getLogger("palette_video.motion_code")

FPS = 24
CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "drift", "static")
ENTRIES = ("pop", "slide", "drop", "wipe", "scale", "fade")
IDLES = ("none", "float", "pulse", "rotate", "flicker")
SFX_GAIN = 0.55  # effects only, no voice: kept well under the narration that is mixed in later
RENDERS = asyncio.Semaphore(max(1, int(os.getenv("MOTION_CODE_PARALLEL", "2"))))  # headless Chrome is heavy

PLAN = """You are the motion designer of a 3D motion-graphics explainer. There is NO video model: the attached keyframe
is animated in code by revealing RECTANGULAR REGIONS of this exact picture over its own empty background, under one
camera move, so the composition stays exactly as painted. Scene {N}, {DUR} seconds. Narration: "{LINE}".
The director's motion prompt for this scene (follow its order, timing and SFX as closely as the method allows):
{MOTION}

Plan it:
- camera: one of {CAMERAS} for the whole scene, strength 0.02-0.12 (how far it pushes or pans; 0.06 is typical).
- layers: 2-6 visual layers of the picture in the order they should appear (big shape / background element ->
  title words -> main subject -> supporting objects -> accents), NOT the plain background. For each: the tightest box
  around it as fractions of the picture (x0, y0, x1, y1 from the top-left; boxes may overlap), entry from {ENTRIES}
  (with "from" left/right/top/bottom for slide, drop and wipe), start "t" in seconds (the first at 0-0.3, all
  layers in by about 60% of the scene), idle after it lands from {IDLES} (mostly "none" or "float"), and "sfx" from
  {SFX} or "" (a sound on its entry; 2-4 sounds per scene at most).
Return ONLY JSON {"camera": "...", "strength": 0.06, "layers": [{"what": "...", "box": [x0, y0, x1, y1],
"entry": "...", "from": "left", "t": 0.2, "idle": "none", "sfx": "pop"}]}"""


def default_plan(dur: float) -> dict:
    """No usable plan: the whole picture appears at once and the camera pushes in slowly."""
    return {"camera": "push_in", "strength": 0.06, "layers": [
        {"what": "the picture", "box": [0.0, 0.0, 1.0, 1.0], "entry": "fade", "from": "left", "t": 0.0,
         "idle": "none", "sfx": "whoosh"}]}


def clean_plan(data: dict, dur: float) -> dict:
    """Whatever the model returned, a plan the page can always play."""
    layers = []
    for l in (data.get("layers") or [])[:6] if isinstance(data, dict) else []:
        if not isinstance(l, dict):
            continue
        try:
            x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in l.get("box")]
            t = float(l.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            continue
        layers.append({"what": str(l.get("what") or "")[:80],
                       "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                       "entry": l.get("entry") if l.get("entry") in ENTRIES else "pop",
                       "from": l.get("from") if l.get("from") in ("left", "right", "top", "bottom") else "bottom",
                       "t": round(min(max(0.0, t), max(0.0, dur * 0.7)), 3),
                       "idle": l.get("idle") if l.get("idle") in IDLES else "none",
                       "sfx": l.get("sfx") if l.get("sfx") in oc.SFX else ""})
    if not layers:
        return default_plan(dur)
    layers.sort(key=lambda l: l["t"])
    try:
        strength = min(0.12, max(0.0, float(data.get("strength") or 0.06)))
    except (TypeError, ValueError):
        strength = 0.06
    camera = data.get("camera") if data.get("camera") in CAMERAS else "push_in"
    return {"camera": camera, "strength": strength, "layers": layers}


async def plan_scene(img: Path, scene: dict, dur: float, n: int) -> dict:
    """The LLM plans the scene's motion from its keyframe and the director's motion prompt."""
    text = (PLAN.replace("{N}", str(n)).replace("{DUR}", f"{dur:g}")
            .replace("{LINE}", str(scene.get("script") or scene.get("line") or "")[:300])
            .replace("{MOTION}", str(scene.get("motion_prompt") or scene.get("action") or "")[:2500])
            .replace("{CAMERAS}", ", ".join(CAMERAS)).replace("{ENTRIES}", ", ".join(ENTRIES))
            .replace("{IDLES}", ", ".join(IDLES)).replace("{SFX}", ", ".join(oc.SFX)))
    try:
        data = await vox._json([{"role": "system", "content": text},
                                {"role": "user", "content": [{"type": "text", "text": "The keyframe:"},
                                                             vox._part(img)]}],
                               "engine.motion_code.plan", 3000, "low")
    except (llm.LLMError, ValueError) as e:
        log.warning("motion plan for scene %s failed: %s", n, e)
        data = {}
    return clean_plan(data, dur)


# Deterministic: render(t) draws frame t from the plan (no clocks, no randomness), as the renderer needs.
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;overflow:hidden;background:#000}canvas{display:block}</style></head><body>
<canvas id="film"></canvas>
<script type="module">
import { loadImages, cover, clamp, seg, ss } from '/kit/illustrated.js';
const P = __PLAN__, D = __DUR__;
window.DUR = D;
const cv = document.getElementById('film'), W = innerWidth, H = innerHeight; cv.width = W; cv.height = H;
const g = cv.getContext('2d');
const off = document.createElement('canvas'); off.width = W; off.height = H; const o = off.getContext('2d');
const [plate, img] = await loadImages([__PLATE__, __IMG__]);
const ENTER = 0.55;                                 // one layer's entry (s)
const last = Math.max(...P.layers.map(L => L.t)) + ENTER, LOCK = Math.min(D - 0.2, last + 0.25);
const back = x => { const c = 1.15, k = c + 1; return 1 + k * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
function fitOf(im) {
  const s = Math.max(W / im.width, H / im.height), dw = im.width * s, dh = im.height * s;
  return { ox: (W - dw) / 2, oy: (H - dh) / 2, dw, dh };
}
function region(c, box, alpha, dx, dy, sc, rot, clipFrac, dir) {
  const f = fitOf(img), [x0, y0, x1, y1] = box;
  const X = f.ox + x0 * f.dw, Y = f.oy + y0 * f.dh, Wd = (x1 - x0) * f.dw, Hd = (y1 - y0) * f.dh;
  c.save(); c.globalAlpha = Math.max(0, Math.min(1, alpha));
  const cx = X + Wd / 2, cy = Y + Hd / 2;
  c.translate(cx + dx, cy + dy); c.rotate(rot); c.scale(sc, sc); c.translate(-cx, -cy);
  c.beginPath();
  if (clipFrac < 1) {
    const p = Math.max(0, clipFrac);
    if (dir === 'right') c.rect(X + Wd * (1 - p), Y, Wd * p, Hd);
    else if (dir === 'top') c.rect(X, Y, Wd, Hd * p);
    else if (dir === 'bottom') c.rect(X, Y + Hd * (1 - p), Wd, Hd * p);
    else c.rect(X, Y, Wd * p, Hd);
  } else c.rect(X, Y, Wd, Hd);
  c.clip(); c.drawImage(img, f.ox, f.oy, f.dw, f.dh); c.restore();
}
function layer(c, L, k, t) {
  const p = clamp((t - L.t) / ENTER, 0, 1);
  if (p <= 0) return;
  const e = ss(p), ob = back(p), d = L.from;
  const sx = d === 'left' ? -1 : d === 'right' ? 1 : 0, sy = d === 'top' ? -1 : d === 'bottom' ? 1 : 0;
  let dx = 0, dy = 0, sc = 1, rot = 0, a = 1;            // the idle, once it has landed
  if (p >= 1) {
    const w = Math.sin((t - L.t) * 2.1 + k * 1.7);
    if (L.idle === 'float') dy = 3 * w;
    else if (L.idle === 'pulse') sc = 1 + 0.012 * w;
    else if (L.idle === 'rotate') rot = 0.015 * w;
    else if (L.idle === 'flicker') a = 0.88 + 0.12 * (0.5 + 0.5 * Math.sin((t - L.t) * 9 + k));
  }
  switch (L.entry) {
    case 'slide': region(c, L.box, Math.min(1, p * 3) * a, dx + sx * (1 - e) * W * 0.08, dy + sy * (1 - e) * H * 0.08, sc, rot, 1); break;
    case 'drop': region(c, L.box, Math.min(1, p * 3) * a, dx, dy - (1 - ob) * H * 0.07, sc, rot, 1); break;
    case 'wipe': region(c, L.box, a, dx, dy, sc, rot, e, d); break;
    case 'scale': region(c, L.box, e * a, dx, dy, sc * (0.85 + 0.15 * e), rot, 1); break;
    case 'fade': region(c, L.box, e * a, dx, dy, sc, rot, 1); break;
    default: region(c, L.box, Math.min(1, p * 2.5) * a, dx, dy, sc * (0.9 + 0.1 * ob), rot, 1);   // pop
  }
}
function camera(t) {                                  // the move over the whole frame, for the whole scene
  const p = ss(clamp(t / D, 0, 1)), k = P.strength;
  switch (P.camera) {
    case 'push_in': return { s: 1 + k * p, x: 0, y: 0 };
    case 'pull_out': return { s: 1 + k * (1 - p), x: 0, y: 0 };
    case 'pan_left': return { s: 1 + k, x: (0.5 - p) * k * W, y: 0 };
    case 'pan_right': return { s: 1 + k, x: (p - 0.5) * k * W, y: 0 };
    case 'drift': return { s: 1 + k * 0.6 + k * 0.4 * p, x: (p - 0.5) * k * W * 0.5, y: (0.5 - p) * k * H * 0.3 };
    default: return { s: 1, x: 0, y: 0 };
  }
}
window.render = t => {
  cover(o, plate, W, H);
  P.layers.forEach((L, k) => layer(o, L, k, t));
  if (t >= LOCK) {                                    // the exact keyframe locks in under the moving layers
    o.save(); o.globalAlpha = ss(seg(t, LOCK, LOCK + 0.35)); cover(o, img, W, H); o.restore();
    P.layers.forEach((L, k) => { if (L.idle !== 'none') layer(o, L, k, t); });
  }
  const c = camera(t);
  g.save(); g.fillStyle = '#000'; g.fillRect(0, 0, W, H);
  g.translate(W / 2 - c.x, H / 2 - c.y); g.scale(c.s, c.s); g.translate(-W / 2, -H / 2);
  g.drawImage(off, 0, 0); g.restore();
};
window.TEXTS = t => { window.render(t); return []; };
window.EV = P.layers.filter(L => L.sfx).map(L => ({ t: +(L.t + 0.03).toFixed(3), type: L.sfx }));
window.READY = true;
</script></body></html>"""


def page(plan: dict, dur: float, img: str = "img.png", plate: str = "plate.png") -> str:
    return (PAGE.replace("__PLAN__", json.dumps(plan, ensure_ascii=False)).replace("__DUR__", f"{dur:g}")
            .replace("__PLATE__", json.dumps(plate)).replace("__IMG__", json.dumps(img)))


def sfx_events(plan: dict) -> list[dict]:
    return [{"t": round(l["t"] + 0.03, 3), "type": l["sfx"], "gain": SFX_GAIN} for l in plan["layers"] if l["sfx"]]


def size_for(aspect: str) -> tuple[int, int]:
    return oc.SIZES.get(aspect) or oc.SIZES["16:9"]


async def _ffmpeg(args: list[str], timeout: int = 600) -> None:
    from .vendor.flow import video_merge
    proc = await asyncio.create_subprocess_exec(video_merge.find_ffmpeg() or "ffmpeg", "-loglevel", "error", "-y",
                                                *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg: " + out.decode(errors="replace")[-300:])


async def camera_move(still: Path, dur: float, aspect: str, out: Path) -> str:
    """The last resort: a slow push-in on the still with ffmpeg alone (always renders)."""
    w, h = size_for(aspect)
    frames = max(1, round(dur * FPS))
    await _ffmpeg(["-loop", "1", "-i", str(still), "-vf",
                   f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                   f"zoompan=z='1+0.06*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
                   f"s={w}x{h}:fps={FPS},format=yuv420p", "-frames:v", str(frames), "-c:v", "libx264", "-crf", "18",
                   str(out)])
    return str(out)


async def animate(still: Path, scene: dict, dur: float, aspect: str, work: Path, n: int) -> tuple[str, dict]:
    """One scene: its keyframe animated in code for `dur` seconds. Returns (mp4 path, the plan it played)."""
    from . import presenter_engine
    work.mkdir(parents=True, exist_ok=True)
    film = work / "film"
    film.mkdir(exist_ok=True)
    w, h = size_for(aspect)
    img = film / "img.png"
    await asyncio.to_thread(vox._ff, ["-i", str(still), "-frames:v", "1", str(img)])
    try:
        await asyncio.to_thread(vox.fit_aspect, img, aspect)
    except Exception as e:  # a picture the right shape already, or one ffmpeg cannot widen: drawn as it is
        log.warning("scene %s keyframe not fitted: %s", n, e)
    plan = await plan_scene(img, scene, dur, n)
    raw = work / "picture.mp4"
    try:
        await asyncio.to_thread(vox.paper_plate, img, film / "plate.png", aspect)
        (film / "index.html").write_text(page(plan, dur))
        async with RENDERS:
            code, text = await oc._node(["core/render/video.mjs", str(film), "--fps", str(FPS), "--workers", "2",
                                         "--size", f"{w}x{h}", "--out", str(raw)], int(max(600, dur * 40)))
        if code != 0 or not raw.exists():
            raise RuntimeError("; ".join(oc.page_errors(text) or [text.strip()[-300:]]))
    except Exception as e:
        log.warning("scene %s code animation failed (%s); a camera move on the still instead", n, e)
        plan = {**plan, "fallback": "camera_move"}
        await camera_move(img, dur, aspect, raw)
    events = sfx_events(plan) if "fallback" not in plan else []
    if not events:
        return str(raw), plan
    out = work / "scene.mp4"
    try:  # the sound effects only (narration is mixed over the joined film later): no loudness normalisation
        spec = work / "mix.json"
        spec.write_text(json.dumps({"dur": dur, "narration": [], "events": events, "music": None}))
        await presenter_engine.run_local([presenter_engine.LOCAL_PY, oc.RUNTIME / "kit" / "mix.py", spec,
                                          work / "sfx.wav"], "The sound effects", timeout=300)
        await _ffmpeg(["-i", str(raw), "-i", str(work / "sfx.wav"), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                       "-c:a", "aac", "-b:a", "160k", "-t", f"{dur:.3f}", str(out)])
        return str(out), plan
    except Exception as e:  # the picture is the scene; its sound effects are a finishing touch
        log.warning("scene %s sound effects failed: %s", n, e)
        return str(raw), plan
