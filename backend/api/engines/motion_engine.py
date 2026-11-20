"""Motion Studio engine: programmatic motion written by our LLM as a HyperFrames composition and rendered to MP4.

HyperFrames (HeyGen, Apache-2.0, pinned 0.9.7 in tools/hyperframes) turns an HTML page with a paused GSAP timeline
into a frame-exact video with headless Chrome + ffmpeg. The LLM writes one self-contained index.html; we lint it, give
lint errors back to the LLM (up to 2 repairs), render, and return the MP4. Used for kinetic text, titles and end cards,
charts and diagrams, product turntables/callouts from a product image, stylised exploded/assembly views, and graphics
placed on top of footage another step produced.
"""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

from router_core import config, llm

from .templates import bar_chart
from .video_intelligence import ENGINE_RULES as VI_RULES

ROOT = Path(__file__).resolve().parents[3]  # the app folder
RUNTIME = Path(os.getenv("HYPERFRAMES_DIR", ROOT / "tools" / "hyperframes"))
CLI = RUNTIME / "node_modules" / ".bin" / "hyperframes"
GSAP = RUNTIME / "node_modules" / "gsap" / "dist" / "gsap.min.js"
CHROME = os.getenv("HYPERFRAMES_BROWSER_PATH", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RENDER_TIMEOUT_S = 1500
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:3": (1440, 1080), "3:4": (1080, 1440),
         "21:9": (2520, 1080)}

SYSTEM = """You are a senior motion designer who writes HyperFrames compositions: ONE self-contained HTML file that
HyperFrames renders frame by frame into a video. Reply in exactly this format and nothing else:
TITLE: <a short title for the animation>
```html
<!doctype html> ... the complete file ...
```

Composition contract (must hold exactly):
- Start the <style> with: html,body{margin:0;padding:0;width:{W}px;height:{H}px;overflow:hidden} (a scrollbar steals
  part of the frame in the renderer and cuts off the bottom of the picture).
- Every DIRECT child of #root that contains positioned elements must itself be position:absolute with left:0; top:0
  and the full frame width and height in px (HyperFrames turns root children into layers; a size-less wrapper shrinks
  and clips whatever sits near the bottom).
- The root element is <div id="root" data-composition-id="main" data-start="0" data-duration="{D}" data-width="{W}"
  data-height="{H}" data-fps="30"> sized {W}x{H}px with position:relative and overflow:hidden. Everything renders inside it.
- Load GSAP with <script src="assets/gsap.min.js"></script>, build ONE paused timeline and register it:
  const tl = gsap.timeline({paused:true}); ... window.__timelines = window.__timelines || {}; window.__timelines["main"] = tl;
  The timeline's total length must equal {D} seconds (end with tl.to({}, {duration: <rest>}, <time>) if needed).
- Prefer tl.fromTo(...) with explicit start positions so any frame can be seeked. NEVER use setTimeout, setInterval,
  requestAnimationFrame, Date, Math.random, CSS animations/transitions, or .play(). No network: no external fonts, CDNs or URLs.
- Use only the files listed under ASSETS (paths like assets/product.png). Images: <img src="assets/...">.
  {FOOTAGE}
- Fonts: system stacks only (e.g. "Helvetica Neue", Arial, sans-serif). Make text large and legible for {W}x{H}.
- Minimum text size 24px (labels, captions, axis ticks), titles 64px or more.
- SAFE AREA: every piece of text and every label stays fully inside the frame with at least 6% margin on all sides,
  at every moment (axis labels, captions and the final line included). Never position text below the bottom margin.
- Text never overlaps other text; text contrast against its background is at least 4.5:1.
- When a later element deliberately covers earlier ones (an end card dimming the chart behind it), put
  data-layout-allow-occlusion on the covered container so the layout check knows it is intentional.
- Charts and diagrams: build them from divs or inline SVG, animate values with GSAP; label everything clearly.
- Product turntable / showcase from a product image: use CSS 3D (perspective, rotateY), light sweeps, shadows, scale.
- Exploded / assembly views: split the subject into labelled layers or parts (SVG or layered images) that separate along
  an axis with leader-line callouts, then reassemble. Be honest about it being a stylised diagram.
- Design: one clear idea per beat, strong hierarchy, generous margins, smooth eases (power2/power3/expo), nothing static
  for more than ~1.5 s, and a deliberate final hold. Respect the brief's colours, language and exclusions.
- Motion graphics craft (Video Intelligence): kinetic type with word-by-word, mask and scale reveals; infographics
  with connecting lines that draw themselves, icon transformations, flow diagrams and progress indicators; 2.5D depth
  with layered planes at different parallax speeds; 3D with CSS perspective, rotateX/rotateY, translateZ and preserve-3d
  (turntables, floating parts, component separation along one axis, 3D typography), soft shadows and light sweeps.
  Every effect serves the story; no random decoration.""" + VI_RULES

FOOTAGE_RULE = """FOOTAGE: the base video assets/base.mp4 ({BD:.1f} s) must play underneath everything for the whole film as
  the first child of #root: <video id="base" src="assets/base.mp4" data-start="0" data-duration="{D}" data-track-index="0"
  muted playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>.
  Place your graphics ON TOP of it (higher z-index) at the moments the brief asks for; keep the footage visible."""


class MotionError(RuntimeError):
    pass


def available() -> str | None:
    """None when the runtime is installed, else why not."""
    if not CLI.exists():
        return f"HyperFrames is not installed at {RUNTIME} (run npm install there)."
    if not Path(CHROME).exists():
        return "Chrome is not available for HyperFrames."
    return None


def _env() -> dict:
    return {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_BROWSER_PATH": CHROME}


async def _cli(args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(str(CLI), *args, cwd=str(cwd), env=_env(),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise MotionError(f"HyperFrames {args[0]} took longer than {timeout} s.")
    return proc.returncode, out.decode(errors="replace")


FPS = 30  # the compositions' data-fps


async def _render(proj: Path, out_path: Path, base_video: str | None, spans: list | None, w: int, h: int,
                  dur: float, progress=None, label: str = "") -> tuple[int, str]:
    """Render the composition. With graphics on footage and `spans` (the seconds that carry graphics), only those
    spans go through Chrome (`hyperframes render --start/--end`); ffmpeg puts them back into the untouched footage,
    which is far faster than capturing every frame of the film. Without spans: one full render."""
    if not (base_video and spans):
        return await _cli(["render", ".", "--output", str(out_path)], proj, RENDER_TIMEOUT_S)
    parts: list[tuple[str, float, float]] = []  # ("base" | span file, start, end)
    t = 0.0
    for k, (a, b) in enumerate(spans):
        if a > t:
            parts.append(("base", t, a))
        seg = out_path.with_name(f"{out_path.stem}_span{k}.mp4")
        if progress:
            await progress(f"{label}rendering graphics {k + 1}/{len(spans)} ({a:.1f}-{b:.1f} s at {w}x{h})")
        code, out = await _cli(["render", ".", "--output", str(seg), "--start", f"{a:.3f}", "--end", f"{b:.3f}"],
                               proj, RENDER_TIMEOUT_S)
        if code != 0 or not seg.exists():
            return code or 1, out
        parts.append((str(seg), a, b))
        t = b
    if t < dur:
        parts.append(("base", t, dur))
    ff = shutil.which("ffmpeg") or "ffmpeg"
    inputs, chains = ["-i", base_video], []
    for k, (src, a, b) in enumerate(parts):
        norm = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p"
        if src == "base":
            chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,{norm}[v{k}]")
        else:
            inputs += ["-i", src]
            idx = inputs.count("-i") - 1
            chains.append(f"[{idx}:v]trim=duration={b - a:.3f},setpts=PTS-STARTPTS,{norm}[v{k}]")
    graph = ";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[out]"
    proc = await asyncio.create_subprocess_exec(
        ff, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), RENDER_TIMEOUT_S)
    return proc.returncode, out.decode(errors="replace")


def graphic_spans(times: list[tuple[float, float]], total: float, pad_in: float = 0.4, pad_out: float = 0.8,
                  join_gap: float = 1.5, max_share: float = 0.6) -> list[list[float]] | None:
    """The seconds of the film that carry graphics (each moment padded for its in/out animation, close ones joined),
    frame-aligned. None when they cover most of the film anyway: then one full render is simpler and as fast."""
    if total <= 0:
        return None
    snap = lambda x: round(round(x * FPS) / FPS, 3)
    spans: list[list[float]] = []
    for a, b in sorted((max(0.0, a - pad_in), min(total, b + pad_out)) for a, b in times if b > a or b == a):
        if spans and a - spans[-1][1] <= join_gap:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    spans = [[snap(a), snap(b)] for a, b in spans if b - a > 0.05]
    if not spans or sum(b - a for a, b in spans) > max_share * total:
        return None
    if total - spans[-1][1] < 0.5:  # a sliver of footage after the last graphic: render to the end
        spans[-1][1] = snap(total)
    return spans


def _check_report(text: str) -> tuple[list[str], list[str]]:
    """(blocking, quality) findings from `hyperframes check --json`: lint/runtime errors block rendering; layout and
    contrast errors are sent back for repair."""
    try:
        data = json.loads(text[text.find("{"):])
    except (ValueError, IndexError):
        return [f"hyperframes check gave no report: {text[-400:]}"], []
    def errs(section):
        out, seen = [], set()
        for f in (data.get(section) or {}).get("findings") or []:
            if f.get("severity") != "error":
                continue
            key = (f.get("code"), f.get("selector"))
            if key in seen:
                continue
            seen.add(key)
            where = f" at {f['time']:.1f}s" if isinstance(f.get("time"), (int, float)) else ""
            out.append(f"[{section}/{f.get('code')}] {f.get('selector') or ''}{where}: {f.get('message')} "
                       f"{('Fix: ' + f['fixHint']) if f.get('fixHint') else ''}".strip())
        return out
    return errs("lint") + errs("runtime"), (errs("layout") + errs("contrast"))[:14]


def lock_frame(html: str, w: int, h: int) -> str:
    """Pin the page to the frame with no scrolling. Without it any element poking past the edge adds a scrollbar,
    which shrinks the render viewport and cuts the bottom of every frame (labels under charts vanish)."""
    rule = f"html,body{{margin:0!important;padding:0!important;width:{w}px!important;height:{h}px!important;overflow:hidden!important}}"
    if "<head>" in html:
        return html.replace("<head>", f"<head><style>{rule}</style>", 1)
    return f"<style>{rule}</style>" + html


def _parse(reply: str) -> tuple[str, str]:
    """(title, html) from 'TITLE: ...' plus a fenced html block (tolerates a missing fence or title)."""
    m = re.search(r"^\s*TITLE:\s*(.+)$", reply, re.M)
    title = m.group(1).strip() if m else ""
    f = re.search(r"```(?:html)?\s*\n(.*?)(?:```|\Z)", reply, re.S)
    html = (f.group(1) if f else reply[reply.find("<"):] if "<" in reply else "").strip()
    return title, html


REVIEW_PROMPT = """These are frames (in time order) from a rendered motion-graphics video. Check ONLY for defects a
viewer would notice: text or labels cut off at a frame edge or missing where they are clearly expected (e.g. a bar chart
with no category labels), text overlapping other text, unreadable text, broken or empty frames, and measurement graphics that do not sit on
the part they measure (a ring not around the round part, a bar crossing the product or ending in empty space).
Brief: {brief}
Return JSON: {"ok": true|false, "problems": ["specific problem and where", ...]} (ok true and [] when it looks right)."""


async def _visual_review(video: Path, dur: float, work: Path, brief: str, times: list[float] | None = None) -> list[str]:
    """A vision model looks at frames of the render and lists visible defects (empty list when it looks right).
    `times` adds frames while text is animating in (stacked lines can collide mid-animation, not only at rest)."""
    frames = []
    picks = sorted({round(t, 2) for t in (times or []) if 0 <= t < dur})[:12]
    for i, t in enumerate([dur * 0.3, dur * 0.6, max(0.0, dur - 0.4)] + picks):
        jpg = work / f"review_{i}.jpg"
        proc = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                                                    "-frames:v", "1", "-vf", "scale=960:-2", str(jpg))
        await proc.wait()
        if jpg.exists():
            frames.append(llm.image_part(jpg))
    if not frames:
        return []
    try:
        data = await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": REVIEW_PROMPT.replace("{brief}", brief[:600])}, *frames]}],
            max_tokens=2000, reasoning_effort="low", stage="engine.motion.review")
    except llm.LLMError:
        return []  # the review is a quality aid; never fail the render because of it
    problems = [str(p) for p in (data.get("problems") or []) if str(p).strip()]
    return [] if data.get("ok") is True and not problems else problems[:8]


async def _frame_size(path: str) -> tuple[int, int] | None:
    proc = await asyncio.create_subprocess_exec("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                                "stream=width,height", "-of", "csv=p=0:s=x", path,
                                                stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        w, h = (int(x) for x in out.decode().strip().split("x")[:2])
        return w, h
    except ValueError:
        return None


def ratio_of(w: int, h: int) -> str:
    """The SIZES ratio closest to a frame's shape (graphics on footage must use the footage's own shape)."""
    return min(SIZES, key=lambda r: abs(SIZES[r][0] / SIZES[r][1] - w / h))


def plain_background(path: str) -> bool:
    """A product photo shot on a plain light background (a catalogue image): it must never be shown as a card."""
    import subprocess
    try:  # shrink to 16x16 with ffmpeg and look at the border pixels
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "scale=16:16:flags=area", "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=30).stdout
    except Exception:
        return False
    if len(raw) < 16 * 16 * 3:
        return False
    px = lambda x, y: raw[(y * 16 + x) * 3:(y * 16 + x) * 3 + 3]
    border = [px(x, y) for x in range(16) for y in (0, 15)] + [px(x, y) for x in (0, 15) for y in range(1, 15)]
    return all(min(p) > 225 and max(p) - min(p) < 18 for p in border)


async def _probe(path: str) -> float:
    proc = await asyncio.create_subprocess_exec("ffprobe", "-v", "error", "-show_entries", "format=duration",
                                                "-of", "csv=p=0", path, stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError:
        return 0.0


async def _chart_spec(brief: str) -> dict | None:
    """The chart's data from the brief (the LLM only extracts numbers the user gave), or None."""
    try:
        spec = await llm.chat_json(config.REASONING_MODEL, [{"role": "system", "content": bar_chart.SPEC_PROMPT},
                                                            {"role": "user", "content": brief}],
                                   max_tokens=3000, reasoning_effort="low", stage="engine.motion.chart")
    except llm.LLMError:
        return None
    cats, vals = spec.get("categories") or [], spec.get("values") or []
    try:
        vals = [float(v) for v in vals]
    except (TypeError, ValueError):
        return None
    return {**spec, "values": vals} if cats and len(cats) == len(vals) else None


async def run(app: dict, handoff: dict, assets: dict, base_video: str | None, work: Path, progress, quality: dict,
              caps: list[str] | None = None) -> dict:
    why = available()
    if why:
        raise MotionError(why)
    req = handoff.get("request") or {}
    ar = req.get("aspect_ratio") or (req.get("settings") or {}).get("aspect") or "16:9"
    w, h = SIZES.get(ar, SIZES["16:9"])
    # Always full size: HyperFrames 0.9.7's renderer drops small text and misplaces bottom-anchored layers on frames
    # with a short side under ~1080 px (measured: labels render at 1920x1080 and 1080x1920, vanish at 1280x720 and
    # 960x540). Rendering is local, so full size costs only time.
    want = float(req.get("total_duration") or req.get("duration") or 8)
    dur = min(want, float(quality.get("max_total", 60)))
    proj = work / "composition"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "assets").mkdir(parents=True)
    shutil.copy(GSAP, proj / "assets" / "gsap.min.js")
    listed = []
    if base_video:  # graphics on footage: the footage's own shape, never letterboxed into another one
        size = await _frame_size(base_video)
        if size:
            ar = ratio_of(*size)
            w, h = SIZES[ar]
    for aid, a in assets.items():
        if a.get("modality") == "image" and a.get("path") and Path(a["path"]).exists():
            # On footage the product is already filmed in the scenes: its catalogue photo would sit on top as a flat
            # sticker. Uploaded photos are only drawn when there is no footage (a standalone animation).
            if base_video and (a.get("role") or "") in ("product", "character", "garment", "background", "unclear"):
                continue
            name = f"{a.get('role') or 'image'}_{aid}{Path(a['path']).suffix.lower()}"
            shutil.copy(a["path"], proj / "assets" / name)
            plain = plain_background(a["path"])
            listed.append(f"assets/{name} - {a.get('role') or 'image'}: {a.get('filename')} {('- ' + a['summary']) if a.get('summary') else ''}"
                          + (" [PLAIN WHITE BACKGROUND: never show it as a rectangle, card or sticker with a shadow; "
                             "place it on a stage of the same light colour, or use mix-blend-mode: multiply so the "
                             "white disappears, and give it a soft contact shadow under the product only]" if plain else ""))
    footage = ""
    if base_video:
        shutil.copy(base_video, proj / "assets" / "base.mp4")
        bd = await _probe(base_video)
        dur = round(bd, 2) if bd else dur  # graphics on footage: the footage sets the length
        footage = FOOTAGE_RULE.replace("{BD:.1f}", f"{bd:.1f}").replace("{D}", f"{dur:g}")
        listed.append("assets/base.mp4 - the footage to place graphics on (the product and people are already "
                      "filmed in it: never paste a photo or cut-out of them on top; draw only graphics, type and accents)")
    system = (SYSTEM.replace("{D}", f"{dur:g}").replace("{W}", str(w)).replace("{H}", str(h))
              .replace("{FOOTAGE}", footage or "No footage: design the full frame yourself."))
    user = (f"Length: {dur:g} seconds. Frame: {w}x{h} ({ar}).\nASSETS:\n" + ("\n".join(listed) or "(none)")
            + f"\n\nBRIEF:\n{handoff.get('prompt') or ''}")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    out_path = work / "motion.mp4"
    if "motion.data_visualization" in (caps or []) and not base_video:
        await progress(f"{app['name']}: reading the chart data")
        spec = await _chart_spec(handoff.get("prompt") or "")
        if spec:
            html = lock_frame(bar_chart.render(spec, w, h, dur), w, h)
            (proj / "index.html").write_text(html)
            await progress(f"{app['name']}: checking the chart")
            _code, chk = await _cli(["check", ".", "--json"], proj, 300)
            blocking, issues = _check_report(chk)
            if not blocking:
                await progress(f"{app['name']}: rendering the chart ({dur:g} s at {w}x{h})")
                code, out = await _cli(["render", ".", "--output", str(out_path)], proj, RENDER_TIMEOUT_S)
                if code == 0 and out_path.exists():
                    return {"path": str(out_path), "duration_s": round(await _probe(str(out_path)), 2),
                            "title": str(spec.get("title") or "Chart"), "size": f"{w}x{h}", "overlay": False,
                            "open_issues": issues, "template": "bar_chart"}
        # no usable numbers in the brief, or the template could not render: free-form animation below
    title, html, blocking, quality_issues = "", "", [], []
    for attempt in range(3):
        await progress(f"{app['name']}: {'writing the animation' if attempt == 0 else 'fixing the animation'}")
        reply = await llm.chat_text(config.REASONING_MODEL, messages, stage="engine.motion")
        title, html = _parse(reply)
        if "data-composition-id" not in html or "__timelines" not in html:
            blocking, quality_issues = ["The HTML is missing the root composition or the registered paused timeline."], []
        else:
            html = lock_frame(html, w, h)
            (proj / "index.html").write_text(html)
            await progress(f"{app['name']}: checking layout, contrast and timing")
            _code, out = await _cli(["check", ".", "--json"], proj, 300)
            blocking, quality_issues = _check_report(out)
            missing = [t for t in (handoff.get("required_texts") or []) if t not in html]
            if missing:  # numbers and labels must stay exactly as planned
                blocking = blocking + [f"These exact texts from the brief are missing or were changed; use them "
                                       f"verbatim: {', '.join(missing)}"]
        if not blocking and not quality_issues:
            break
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "HyperFrames check reported these problems; fix every one and reply with "
                                                 "the complete corrected file in the same format:\n"
                                                 + "\n".join(blocking + quality_issues)}]
    if blocking:
        raise MotionError("The animation did not pass HyperFrames checks: " + "; ".join(blocking)[:400])
    for review in range(2):  # render, look at it, and repair once if the frames show a problem
        spans = handoff.get("render_spans") if base_video else None
        if spans:
            covered = sum(b - a for a, b in spans)
            await progress(f"{app['name']}: rendering only the graphics moments ({covered:.1f} of {dur:g} s at {w}x{h})")
        else:
            await progress(f"{app['name']}: rendering {dur:g} s at {w}x{h}")
        code, out = await _render(proj, out_path, base_video, spans, w, h, dur, progress, f"{app['name']}: ")
        if code != 0 or not out_path.exists():
            raise MotionError("HyperFrames render failed: " + out[-500:])
        if review == 1:
            break
        await progress(f"{app['name']}: reviewing the rendered frames")
        problems = await _visual_review(out_path, dur, work, handoff.get("prompt") or "", handoff.get("review_times"))
        if not problems:
            break
        quality_issues = problems
        await progress(f"{app['name']}: fixing what the review found")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "Looking at the rendered frames, these problems are visible; fix every one "
                                                 "and reply with the complete corrected file in the same format:\n"
                                                 + "\n".join(problems)}]
        reply = await llm.chat_text(config.REASONING_MODEL, messages, stage="engine.motion")
        new_title, new_html = _parse(reply)
        if "data-composition-id" not in new_html or "__timelines" not in new_html:
            break  # keep the first render rather than replace it with a broken file
        if any(t not in new_html for t in (handoff.get("required_texts") or [])):
            break  # the fix changed a planned value: keep the render that has the right numbers
        title, html = new_title or title, lock_frame(new_html, w, h)
        (proj / "index.html").write_text(html)
        _code, chk = await _cli(["check", ".", "--json"], proj, 300)
        if _check_report(chk)[0]:
            break
        quality_issues = []
    return {"path": str(out_path), "duration_s": round(await _probe(str(out_path)), 2), "title": title,
            "size": f"{w}x{h}", "overlay": bool(base_video), "open_issues": quality_issues}
