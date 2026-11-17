"""Editable text layer: every word on screen in presenter and 3D motion-graphics videos lives here, not in the pixels.

The image and video models make a CLEAN video (no letters anywhere). The words - hook, key words, callouts, labels,
end card, captions - are a list of items on top of it:

    {"id", "kind", "text", "start", "end", "x", "y", "w", "size", "color", "style": "3d" | "flat", "font", "anim",
     "bg"}

x / y are the item's centre and w its maximum width, in % of the frame; size is the letter height in % of the frame
height. The same CSS (CSS below) draws the layer live in the app's player - where the user clicks a word to edit it -
and in the HyperFrames render that burns it into the downloadable video, so what they edit is what they get.
"""

import asyncio
import html
import json
import re
import shutil
from pathlib import Path

from . import motion_engine

KINDS = ("hook", "title", "keyword", "callout", "label", "lower_third", "badge", "step", "end_title", "end_line", "cta",
         "caption")
STYLES = ("3d", "flat")
FONTS = {
    "display": "'Helvetica Neue', 'Arial Black', Arial, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif",
    "serif": "Georgia, 'Times New Roman', 'AppleMyungjo', 'Noto Serif KR', serif",
    "rounded": "'Arial Rounded MT Bold', 'Helvetica Neue', Arial, 'Apple SD Gothic Neo', sans-serif",
    "condensed": "'Helvetica Neue', 'Arial Narrow', Arial, 'Apple SD Gothic Neo', sans-serif",
}
ANIMS = ("pop", "rise", "fade")
ENTER_S, EXIT_S, MIN_S = 0.35, 0.25, 0.6

# Shared by the player and the render. --tlh is the layer's height in px, so sizes scale with the frame.
CSS = """
.tl-layer{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;overflow:hidden}
.tl-item{position:absolute;box-sizing:border-box;text-align:center;line-height:1.08;white-space:pre-wrap;
  overflow-wrap:break-word;letter-spacing:-0.01em;transform-origin:50% 50%;color:var(--c);
  font-size:calc(var(--tlh) * var(--s) / 100)}
.tl-flat{text-shadow:0 0.04em 0.2em rgba(0,0,0,.45)}
.tl-3d{text-shadow:0.018em 0.018em 0 var(--d),0.036em 0.036em 0 var(--d),0.054em 0.054em 0 var(--d),
  0.072em 0.072em 0 var(--d),0.09em 0.09em 0 var(--d),0.108em 0.108em 0 var(--d),0.15em 0.17em 0.25em rgba(0,0,0,.5)}
.tl-bg{padding:0.18em 0.5em;border-radius:0.3em;background:var(--bg)}
.tl-caption{font-weight:800}
"""


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _hex(c, default="#FFFFFF") -> str:
    c = str(c or "").strip()
    return c.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else default


def shade(color: str, f: float = 0.45) -> str:
    """The darker side colour of 3D letters (the extrusion)."""
    c = _hex(color)
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02X}{:02X}{:02X}".format(*(int(x * f) for x in (r, g, b)))


def item(kind: str, text: str, start: float, end: float, x: float = 50, y: float = 50, w: float = 80,
         size: float = 6, color: str = "#FFFFFF", style: str = "flat", font: str = "display", anim: str = "pop",
         bg: str = "", weight: int = 800, n: int = 0) -> dict:
    return {"id": f"t{n}", "kind": kind, "text": text, "start": start, "end": end, "x": x, "y": y, "w": w,
            "size": size, "color": color, "style": style, "font": font, "anim": anim, "bg": bg, "weight": weight}


def normalize(items: list, duration: float) -> list[dict]:
    """Clean user- or AI-made items: known values only, inside the frame and the film, unique ids."""
    out, seen = [], set()
    for i, it in enumerate([x for x in items or [] if isinstance(x, dict)]):
        text = str(it.get("text") or "").strip()[:300]
        if not text:
            continue
        start = _clamp(it.get("start"), 0, max(0.0, duration - MIN_S), 0.0)
        end = _clamp(it.get("end"), start + MIN_S, max(duration, start + MIN_S), min(duration, start + 2.5))
        iid = re.sub(r"[^A-Za-z0-9_-]", "", str(it.get("id") or ""))[:24] or f"t{i}"
        while iid in seen:
            iid += "x"
        seen.add(iid)
        out.append({"id": iid, "kind": it.get("kind") if it.get("kind") in KINDS else "keyword", "text": text,
                    "start": round(start, 2), "end": round(end, 2),
                    "x": round(_clamp(it.get("x"), 0, 100, 50), 2), "y": round(_clamp(it.get("y"), 0, 100, 50), 2),
                    "w": round(_clamp(it.get("w"), 5, 100, 80), 2), "size": round(_clamp(it.get("size"), 1.5, 30, 6), 2),
                    "color": _hex(it.get("color")), "style": it.get("style") if it.get("style") in STYLES else "flat",
                    "font": it.get("font") if it.get("font") in FONTS else "display",
                    "anim": it.get("anim") if it.get("anim") in ANIMS else "pop",
                    "bg": _hex(it.get("bg"), "") if it.get("bg") else "",
                    "weight": int(_clamp(it.get("weight"), 300, 900, 800))})
    return sorted(out, key=lambda x: (x["start"], x["y"]))


def layer(items: list, width: int, height: int, duration: float) -> dict:
    return {"version": 1, "width": width, "height": height, "duration": round(duration, 2),
            "items": normalize(items, duration), "css": CSS, "fonts": FONTS}


# ---------------------------------------------------------------------------------------------- building the layer

def captions(segments: list[dict], band: tuple[float, float] = (74, 84), words_per_line: int = 4,
             style: str = "flat", n0: int = 100) -> list[dict]:
    """Short caption lines from the transcript segments ({start, end, text}), timed by word count."""
    out, n = [], n0
    y = (band[0] + band[1]) / 2
    for seg in segments or []:
        words = str(seg.get("text") or "").split()
        a, b = float(seg.get("start") or 0), float(seg.get("end") or 0)
        if not words or b <= a:
            continue
        per = (b - a) / len(words)
        for i in range(0, len(words), words_per_line):
            chunk = words[i:i + words_per_line]
            s, e = a + i * per, a + (i + len(chunk)) * per
            out.append(item("caption", " ".join(chunk), round(s, 2), round(max(e, s + MIN_S), 2), 50, y, 86, 4.6,
                            "#FFFFFF", style, "display", "fade", n=n))
            n += 1
    return out


SPOTS = {"top": (50, 16), "top_left": (28, 16), "top_right": (72, 16), "middle": (50, 45), "left": (26, 45),
         "right": (74, 45), "bottom": (50, 66)}


def from_graphics(gp: dict, duration: float, accent: str = "#FFD84D", style: str = "3d",
                  captions_band: tuple[float, float] | None = None) -> list[dict]:
    """The graphics director's plan (hook, overlays, end card) as editable text items. Shapes without words
    (accents, arrows, rings) are not text and are left out."""
    accent = _hex(((gp.get("design") or {}).get("accent")) or accent, "#FFD84D")
    out, n = [], 0
    hook = gp.get("hook") or {}
    if hook.get("text"):
        a = _clamp(hook.get("start"), 0, duration, 0.2)
        e = _clamp(hook.get("end"), a + MIN_S, duration, min(duration, a + 2.6))
        out.append(item("hook", str(hook["text"]), a, e, 50, 18, 86, 9, "#FFFFFF", style, "display", "pop", n=n))
        n += 1
        if hook.get("sub"):
            out.append(item("title", str(hook["sub"]), a + 0.2, e, 50, 28, 80, 4.2, accent, "flat", "display",
                            "rise", n=n))
            n += 1
    low = captions_band[0] if captions_band else 100
    for o in gp.get("overlays") or []:
        words = str(o.get("text") or o.get("value") or "").strip()
        if not words or o.get("kind") in ("accent", "arrow", "highlight", "sweep", "guide", "circuit"):
            continue
        if o.get("detail"):
            words += "\n" + str(o["detail"]).strip()
        a = _clamp(o.get("start"), 0, duration, 0.0)
        e = _clamp(o.get("end"), a + MIN_S, duration, min(duration, a + 2.5))
        t = o.get("target") or {}
        if isinstance(t, dict) and t.get("x") is not None:  # beside the part it points at, inside the frame
            x = _clamp(float(t.get("x") or 0) + float(t.get("w") or 0) / 2, 18, 82, 50)
            y = _clamp(float(t.get("y") or 0) - 6, 10, 88, 40)
        else:
            x, y = SPOTS.get(str(o.get("place") or ""), SPOTS["top"])
        if y > low - 6:  # never inside the captions band
            y = 16
        kind = {"kinetic_text": "keyword", "lower_third": "lower_third", "spec_badge": "badge", "step": "step",
                "callout": "callout", "label": "label", "part_label": "label", "measure": "label",
                "compare": "label"}.get(o.get("kind"), "keyword")
        big = kind == "keyword"
        out.append(item(kind, words, a, e, x, y, 70 if big else 40, 7 if big else 3.6,
                        "#FFFFFF" if big else "#111111", style if big else "flat", "display",
                        "pop" if big else "rise", "" if big else "#FFFFFF", n=n))
        n += 1
    card = gp.get("end_card") or {}
    if card.get("title"):
        a = _clamp(card.get("start"), 0, max(0.0, duration - MIN_S), max(0.0, duration - 3))
        out.append(item("end_title", str(card["title"]), a, duration, 50, 42, 84, 8, "#FFFFFF", style, n=n))
        n += 1
        for k, y in (("line", 54), ("cta", 64)):
            if card.get(k):
                out.append(item("end_line" if k == "line" else "cta", str(card[k]), a + 0.25, duration, 50, y, 80,
                                3.8 if k == "line" else 4.2, accent if k == "cta" else "#FFFFFF", "flat", "display",
                                "rise", "#111111" if k == "cta" else "", n=n))
                n += 1
    return out


def from_scene_words(scenes: list[dict], spans: list[tuple[float, float]], style: str = "3d",
                     color: str = "#FFFFFF") -> list[dict]:
    """The scene's typed words (the 3D director's key words, a documentary's dates / names / stamps) as editable text."""
    out = []
    for i, (s, (a, b)) in enumerate(zip(scenes, spans)):
        words = str(s.get("overlay_text") or "").strip()
        if words:
            out.append(item("keyword", words, round(a + 0.3, 2), round(max(b - 0.2, a + 0.3 + MIN_S), 2), 50, 30,
                            84, 9, color, style, "display", "pop", n=200 + i))
    return out


# ---------------------------------------------------------------------------------------------- export

def _ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(lay: dict, kinds: tuple = ("caption",)) -> str:
    rows = [i for i in lay.get("items") or [] if i.get("kind") in kinds] or (lay.get("items") or [])
    return "\n".join(f"{n}\n{_ts(i['start'])} --> {_ts(i['end'])}\n{i['text']}\n"
                     for n, i in enumerate(sorted(rows, key=lambda r: r["start"]), start=1))


# ---------------------------------------------------------------------------------------------- render

def _style_attr(it: dict, height: int) -> str:
    css = (f"left:{it['x']}%;top:{it['y']}%;width:{it['w']}%;--s:{it['size']};--c:{it['color']};"
           f"--d:{shade(it['color'])};font-family:{FONTS[it['font']]};font-weight:{it['weight']};"
           f"visibility:hidden")
    if it.get("bg"):
        css += f";--bg:{it['bg']}"
    return html.escape(css, quote=True)


def _classes(it: dict) -> str:
    return " ".join(["tl-item", f"tl-{it['style']}", f"tl-{it['kind']}"] + (["tl-bg"] if it.get("bg") else []))


def composition(lay: dict, base: str = "assets/base.mp4") -> str:
    """HyperFrames composition: the clean video underneath, the text layer on top with one seekable GSAP timeline.
    The enter / exit motion matches the app's live preview (textLayer.ts)."""
    w, h, d = lay["width"], lay["height"], lay["duration"]
    divs, tweens = [], []
    for it in lay["items"]:
        divs.append(f'<div id="{it["id"]}" class="{_classes(it)}" style="{_style_attr(it, h)}">'
                    f'{html.escape(it["text"])}</div>')
        y0 = round(h * 0.04, 1) if it["anim"] == "rise" else 0
        s0 = 0.8 if it["anim"] == "pop" else 1
        tweens.append(f'tl.set("#{it["id"]}",{{xPercent:-50,yPercent:-50,autoAlpha:0}},0);'
                      f'tl.fromTo("#{it["id"]}",{{autoAlpha:0,y:{y0},scale:{s0}}},'
                      f'{{autoAlpha:1,y:0,scale:1,duration:{ENTER_S},ease:"power2.out"}},{it["start"]});'
                      f'tl.to("#{it["id"]}",{{autoAlpha:0,duration:{EXIT_S},ease:"none"}},{max(it["start"] + ENTER_S, it["end"] - EXIT_S)});')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden;background:#000}}
#root{{position:relative;width:{w}px;height:{h}px;overflow:hidden}}
{CSS}
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{d:g}" data-width="{w}" data-height="{h}" data-fps="30">
<video id="base" src="{base}" data-start="0" data-duration="{d:g}" data-track-index="0" muted playsinline
 style="position:absolute;left:0;top:0;width:{w}px;height:{h}px;object-fit:cover"></video>
<div class="tl-layer" style="width:{w}px;height:{h}px;--tlh:{h}px;z-index:2">
{chr(10).join(divs)}
</div></div>
<script src="assets/gsap.min.js"></script>
<script>
const tl = gsap.timeline({{paused:true}});
{chr(10).join(tweens)}
tl.to({{}}, {{duration:0.001}}, {max(0.0, d - 0.001):g});
window.__timelines = window.__timelines || {{}}; window.__timelines["main"] = tl;
</script></body></html>"""


async def burn(clean: str, lay: dict, work: Path, progress=None, label: str = "") -> str:
    """The downloadable video: the clean video with the text layer rendered on top (HyperFrames), audio kept."""
    why = motion_engine.available()
    if why:
        raise RuntimeError(why)
    from .vendor.flow import video_merge
    ff = video_merge.find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg is not installed")
    proj = work / "text_composition"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "assets").mkdir(parents=True)
    shutil.copy(motion_engine.GSAP, proj / "assets" / "gsap.min.js")
    shutil.copy(clean, proj / "assets" / "base.mp4")
    (proj / "index.html").write_text(motion_engine.lock_frame(composition(lay), lay["width"], lay["height"]))
    (work / "text_layer.json").write_text(json.dumps(lay, ensure_ascii=False))
    if progress:
        await progress(f"{label + ': ' if label else ''}rendering the text layer ({len(lay['items'])} texts)")
    if not lay["items"]:
        return clean
    silent = work / "text_silent.mp4"
    code, out = await motion_engine._cli(["render", ".", "--output", str(silent)], proj, motion_engine.RENDER_TIMEOUT_S)
    if code != 0 or not silent.exists():
        raise RuntimeError("The text layer did not render: " + out[-400:])
    final = work / "with_text.mp4"
    proc = await asyncio.to_thread(__import__("subprocess").run, [
        ff, "-y", "-v", "error", "-i", str(silent), "-i", clean, "-map", "0:v", "-map", "1:a?", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)],
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("putting the audio back failed: " + (proc.stderr or "")[-300:])
    return str(final)


def frame_size(aspect_ratio: str) -> tuple[int, int]:
    return motion_engine.SIZES.get(aspect_ratio or "16:9", motion_engine.SIZES["16:9"])


# ---------------------------------------------------------------------------------------------- the editable finish

async def finish(app: dict, brief: str, plan: dict, video: str, scene_paths: list[str], narration: list[str],
                 aspect_ratio: str, work: Path, progress, effects_style: str | None = None, level: str = "full",
                 scene_words: list[dict] | None = None, spans: list | None = None, do_effects: bool = True,
                 do_graphics: bool = True, captions_on: bool = True, words_style: str = "3d") -> dict:
    """Finish a film with every word editable: camera-move / grade effects only (no letters), the graphics director's
    plan turned into text items (not drawn), captions from the speech, the 3D key words - then one render of the
    text layer on the clean video.
    Returns {"path": video with text, "clean": video without text, "text_layer", "effects", "graphics"}."""
    from . import effects_engine, finishing
    from .vendor.flow import video_merge
    name = app["name"]
    clean, effects, segs = video, None, None
    if do_effects and effects_engine.available() is None:
        try:
            effects = await effects_engine.run({**app, "name": f"{name} effects"},
                                               {"prompt": brief, "effects_style": effects_style, "text_free": True,
                                                "allow_none": True}, video, work / "effects", progress)
            clean, segs = effects["path"], effects.get("transcript")
        except Exception as e:  # camera moves are a finishing touch
            effects = {"error": str(e)[:300], "plan": []}
    if segs is None and captions_on:
        try:
            segs = await effects_engine.transcript(clean, work)
        except Exception:
            segs = []
    ff = video_merge.find_ffmpeg()
    dur = await asyncio.to_thread(video_merge.probe_duration, ff, clean)
    size = await motion_engine._frame_size(clean)
    w, h = motion_engine.SIZES[motion_engine.ratio_of(*size)] if size else frame_size(aspect_ratio)
    band = (74, 84) if h > w else (80, 90)
    gp, graphics = {}, None
    if do_graphics:
        try:
            busy = [(float(e["start"]), float(e["end"]), e["fx"]) for e in (effects or {}).get("plan") or []]
            gp = await finishing.plan_graphics(app, brief, plan, clean, scene_paths, narration, aspect_ratio,
                                               work / "graphics", progress, busy, level,
                                               "clean" if captions_on and segs else None)
            graphics = {"graphics": gp, "skipped": False, "why": gp.get("why") or "", "editable": True,
                        "invented_specs": [str(x) for x in gp.get("invented_specs") or []]}
        except Exception as e:
            graphics = {"error": str(e)[:300]}
    items = from_graphics(gp, dur, captions_band=band if captions_on and segs else None) if gp else []
    if scene_words and spans:
        items += from_scene_words(scene_words, spans, style=words_style)
    if captions_on and segs:
        items += captions(segs, band)
    lay = layer(items, w, h, dur)
    await progress(f"{name}: {len(lay['items'])} editable texts")
    final = await burn(clean, lay, work / "text", progress, name)
    return {"path": final, "clean": clean, "text_layer": lay, "effects": effects, "graphics": graphics}
