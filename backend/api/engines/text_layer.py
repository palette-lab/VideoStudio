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


def layer(items: list, width: int, height: int, duration: float, tidy: bool = False,
          faces: list[dict] | None = None) -> dict:
    """The text layer. tidy=True (a layer the engine made, not the user's own edits) also lays it out: one text at a
    time, never over a face (declutter)."""
    clean = normalize(items, duration)
    if tidy:
        clean = declutter(clean, width, height, faces)
    return {"version": 1, "width": width, "height": height, "duration": round(duration, 2),
            "items": clean, "css": CSS, "fonts": FONTS}


# ---------------------------------------------------------------------------------------------- layout

MAX_LINES = 3
MIN_SIZE = 3.2
SLOTS_Y = (16, 30, 45, 60)
PRIORITY = {"caption": 0, "hook": 1, "end_title": 2, "keyword": 3, "title": 4, "end_line": 5, "cta": 5}
CENTRED = set(KINDS) - {"caption"}  # every text sits in the middle of the frame, stacked top to bottom
MARGIN = 5.0  # % kept clear at every edge


def _same_words(a: str, b: str) -> bool:
    """One text repeats the other (same words, or one inside the other): show it once."""
    na, nb = (" ".join(re.findall(r"\w+", t.lower())) for t in (a, b))
    return bool(na and nb) and (na == nb or na in nb or nb in na)


def _inside(it: dict, width: int, height: int) -> dict:
    """Moved sideways so the whole text stays inside the frame, MARGIN from each edge."""
    l, _, r, _ = box(it, width, height)
    if l < MARGIN:
        it["x"] = round(it["x"] + MARGIN - l, 2)
    elif r > 100 - MARGIN:
        it["x"] = round(it["x"] - (r - (100 - MARGIN)), 2)
    it["w"] = min(it["w"], 100 - 2 * MARGIN)
    return it


def _lines(it: dict, width: int, height: int) -> int:
    """About how many lines the item wraps into (bold display letters are ~0.55 of their height wide)."""
    per_line = max(1, int((it["w"] / 100 * width) / (0.55 * it["size"] / 100 * height)))
    n = 0
    for para in it["text"].split("\n"):
        words, cur = para.split() or [""], 0
        n += 1
        for wd in words:
            if cur and cur + 1 + len(wd) > per_line:
                n, cur = n + 1, len(wd)
            else:
                cur += (1 if cur else 0) + len(wd)
    return n


def box(it: dict, width: int, height: int) -> tuple[float, float, float, float]:
    """The item's box in % of the frame: (left, top, right, bottom); x / y are its centre."""
    h = _lines(it, width, height) * it["size"] * 1.08 + (it["size"] * 0.36 if it.get("bg") else 0)
    longest = max(len(p) for p in it["text"].split("\n"))
    w = min(it["w"], longest * 0.55 * it["size"] * height / width + 1)
    return it["x"] - w / 2, it["y"] - h / 2, it["x"] + w / 2, it["y"] + h / 2


def _hit(a, b, pad: float = 1.5) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _together(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


END_CARD = {"end_title", "end_line", "cta"}  # shown together as one card
FACE_PAD = 3.0  # % kept clear around a face
SHRINK = (1.0, 0.8, 0.65, 0.5)  # a text that does not fit gets smaller, never smaller than MIN_SIZE
SIDE_SHRINK = (0.8, 0.65, 0.5)  # beside a person the text is always a little smaller


def _face_boxes(faces: list[dict] | None, it: dict) -> list[tuple]:
    """The whole person to keep clear: the face (with the hair) and the body below it - wider than the face and
    about three face-heights down - plus a small margin."""
    out = []
    for f in faces or []:
        if not (f["start"] < it["end"] and it["start"] < f["end"]):
            continue
        l, t, r, b = f["box"]
        fw, fh = r - l, b - t
        out.append((max(0.0, l - 0.4 * fw) - FACE_PAD, t - FACE_PAD, min(100.0, r + 0.4 * fw) + FACE_PAD,
                    min(100.0, b + 3 * fh) + FACE_PAD))
    return out


def _fit_lines(it: dict, width: int, height: int) -> dict:
    """Wider first, then smaller, then only its first line, until it needs at most MAX_LINES (+1 for a card)."""
    most = MAX_LINES + (1 if "\n" in it["text"] else 0)
    while _lines(it, width, height) > most:
        if it["w"] < 100 - 2 * MARGIN:
            it["w"] = min(100 - 2 * MARGIN, it["w"] + 10)
        elif it["size"] > MIN_SIZE:
            it["size"] = round(max(MIN_SIZE, it["size"] * 0.9), 2)
        elif "\n" in it["text"]:
            it["text"] = it["text"].split("\n")[0]
        else:
            break
    return it


def _spots(it: dict, faces: list[tuple], width: int, height: int):
    """Where the text may go, best first: centred at its own height, the standard heights or just above each head -
    from full size down to half; then beside each person (the free side of the frame), smaller."""
    for f in SHRINK:
        base = _fit_lines({**it, "size": max(MIN_SIZE, round(it["size"] * f, 2))}, width, height)
        h = box(base, width, height)[3] - box(base, width, height)[1]
        ys = [it["y"]] + [y for y in SLOTS_Y if y != it["y"]] + [fb[1] - h / 2 - 1 for fb in faces]
        for y in ys:
            yield {**base, "x": 50.0 if it["kind"] in CENTRED else it["x"], "y": round(y, 2)}
    for f in SIDE_SHRINK:
        size = max(MIN_SIZE, round(it["size"] * f, 2))
        for fb in faces:
            for lo, hi in ((MARGIN, fb[0] - 1), (fb[2] + 1, 100 - MARGIN)):
                if hi - lo < 18:
                    continue
                side = {**it, "size": size, "w": round(hi - lo, 2), "x": round((lo + hi) / 2, 2)}
                if _lines(side, width, height) > MAX_LINES + (1 if "\n" in it["text"] else 0):
                    continue
                for y in [fb[1] + 6, (fb[1] + fb[3]) / 2] + list(SLOTS_Y):
                    yield {**side, "y": round(y, 2)}


def declutter(items: list[dict], width: int, height: int, faces: list[dict] | None = None) -> list[dict]:
    """The engine's text, kept clean:
    - one text on screen at a time (captions apart; an end card's lines count as one), placed by importance (hook,
      end title, key words, then callouts and labels); a later one waits until the screen is free, else is left out;
    - the same words never twice; every text inside the frame, at most MAX_LINES lines, captions keep their band;
    - never over a face: faces ({"start", "end", "box": (left, top, right, bottom) in %}) are kept clear - the text
      goes above the head or beside the person, smaller when it must."""
    out: list[dict] = []
    band = [box(c, width, height) for c in items if c["kind"] == "caption"]
    band_top = min((b[1] for b in band), default=100.0)
    for it in sorted(items, key=lambda i: (PRIORITY.get(i["kind"], 6), i["start"])):
        it = dict(it)
        if it["kind"] == "caption":
            out.append(it)
            continue
        if any(o["kind"] != "caption" and _together(o, it) and _same_words(o["text"], it["text"]) for o in out):
            continue  # already on screen
        if it["kind"] in CENTRED:
            it["x"] = 50.0
        it["w"] = min(it["w"], 100 - 2 * MARGIN)
        it = _inside(_fit_lines(it, width, height), width, height)

        def blockers(cand: dict) -> list[dict]:
            """What stops this text here: another text on screen at that time, or the frame / captions edge."""
            b = box(cand, width, height)
            if b[0] < MARGIN - 0.01 or b[2] > 100 - MARGIN + 0.01 or b[1] < MARGIN or b[3] > min(100 - MARGIN, band_top - 1):
                return [{"end": cand["end"]}]
            card = cand["kind"] in END_CARD
            return [o for o in out if o["kind"] != "caption" and _together(o, cand)
                    and (not (card and o["kind"] in END_CARD) or _hit(box(o, width, height), b))]

        def place(at: dict) -> dict | None:
            faces_now = _face_boxes(faces, at)
            for cand in _spots(at, faces_now, width, height):
                b = box(cand, width, height)
                if not blockers(cand) and not any(_hit(fb, b, 0) for fb in faces_now):
                    return cand
            return None

        placed = place(it)
        if placed is None:  # the screen is taken: start when the text before it has gone, if time is left
            later = max([o["end"] for o in out if o["kind"] != "caption" and _together(o, it)] or [it["start"]]) + 0.1
            if it["end"] - later >= MIN_S:
                placed = place({**it, "start": round(later, 2)})
        if placed is not None:
            out.append(placed)
    return sorted(out, key=lambda x: (x["start"], x["y"]))


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
                  captions_band: tuple[float, float] | None = None, words_owned: bool = False) -> list[dict]:
    """The graphics director's plan (hook, overlays, end card) as editable text items, all in the hook's look: 3D
    letters, no background panels. Shapes without words (accents, arrows, rings) are not text and are left out.
    words_owned: the scenes carry their own key words (3D motion graphics, documentaries), so only the end card is
    added - a hook and overlays on top would repeat them."""
    accent = _hex(((gp.get("design") or {}).get("accent")) or accent, "#FFD84D")
    out, n = [], 0
    hook = {} if words_owned else (gp.get("hook") or {})
    if hook.get("text"):
        a = _clamp(hook.get("start"), 0, duration, 0.2)
        e = _clamp(hook.get("end"), a + MIN_S, duration, min(duration, a + 2.6))
        out.append(item("hook", str(hook["text"]), a, e, 50, 18, 86, 9, "#FFFFFF", style, "display", "pop", n=n))
        n += 1
        # (the hook's sub-line is left out: fewer words on screen)
    low = captions_band[0] if captions_band else 100
    for o in ([] if words_owned else gp.get("overlays") or []):
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
        out.append(item(kind, words, a, e, x, y, 70 if big else 64, 7 if big else 4.2, "#FFFFFF", style, "display",
                        "pop" if big else "rise", n=n))
        n += 1
    card = gp.get("end_card") or {}
    if card.get("title"):
        a = _clamp(card.get("start"), 0, max(0.0, duration - MIN_S), max(0.0, duration - 3))
        out.append(item("end_title", str(card["title"]), a, duration, 50, 42, 84, 8, "#FFFFFF", style, n=n))
        n += 1
        for k, y in (("cta", 60),):  # the title and its call to action; no extra line
            if card.get(k):
                out.append(item("end_line" if k == "line" else "cta", str(card[k]), a + 0.25, duration, 50, y, 80,
                                3.8 if k == "line" else 4.6, accent if k == "cta" else "#FFFFFF", style, "display",
                                "rise", n=n))
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

FACES = """This is one frame of a video. Find every visible FACE or HEAD: real people, statues, figurines, dolls,
cartoon, 3D or animated characters, animals' heads, and heads seen from behind or in profile.
For each, give its box in PERCENT of the frame (0-100): "left" and "top" are the box's top-left corner, "width" and
"height" its size. Include the hair. Return ONLY JSON {"faces": [{"left": .., "top": .., "width": .., "height": ..}]};
{"faces": []} when there is none."""
FACE_SAMPLE_S = 3.0
FACE_MAX_FRAMES = 20


async def faces_in(video: str, duration: float, work: Path, spans: list | None = None) -> list[dict]:
    """Where faces are on screen over time ({"start", "end", "box"} in % of the frame), from one frame per scene
    (or one every FACE_SAMPLE_S seconds) checked by the vision model. Best effort: [] when it cannot look."""
    from router_core import config, llm
    from .vendor.flow import video_merge
    ff = video_merge.find_ffmpeg()
    if not ff or duration <= 0:
        return []
    if spans:
        windows = [(float(a), float(b)) for a, b in spans if b > a]
    else:
        n = max(1, min(FACE_MAX_FRAMES, int(duration // FACE_SAMPLE_S) + 1))
        step = duration / n
        windows = [(k * step, min(duration, (k + 1) * step)) for k in range(n)]
    windows = windows[:FACE_MAX_FRAMES]
    out_dir = work / "faces"
    out_dir.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(4)

    async def one(k: int, a: float, b: float) -> list[dict]:
        frame = out_dir / f"f{k:02d}.jpg"
        t = a + (b - a) * 0.5
        proc = await asyncio.create_subprocess_exec(ff, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video,
                                                    "-frames:v", "1", "-vf", "scale=-2:512", "-q:v", "4", str(frame))
        await proc.wait()
        if not frame.exists():
            return []
        async with gate:
            try:
                data = await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": [
                    {"type": "text", "text": FACES}, llm.image_part(frame)]}], max_tokens=600,
                    reasoning_effort="low", stage="engine.text.faces")
            except Exception:  # no face map: the layout still keeps one text at a time
                return []
        found = []
        for f in data.get("faces") or []:
            try:
                l, tp = float(f["left"]), float(f["top"])
                w, h = float(f["width"]), float(f["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if w > 0 and h > 0:
                found.append({"start": round(a, 2), "end": round(b, 2),
                              "box": (max(0.0, l), max(0.0, tp), min(100.0, l + w), min(100.0, tp + h))})
        return found

    got = await asyncio.gather(*(one(k, a, b) for k, (a, b) in enumerate(windows)))
    return [f for fs in got for f in fs]


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
    words = from_scene_words(scene_words, spans, style=words_style) if scene_words and spans else []
    items = from_graphics(gp, dur, captions_band=band if captions_on and segs else None,
                          words_owned=bool(words)) if gp else []
    items += words
    if captions_on and segs:
        items += captions(segs, band)
    faces = await faces_in(clean, dur, work, spans) if any(i["kind"] != "caption" for i in items) else []
    if faces:
        await progress(f"{name}: keeping the text off {len(faces)} face{'s' if len(faces) > 1 else ''}")
    lay = layer(items, w, h, dur, tidy=True, faces=faces)
    await progress(f"{name}: {len(lay['items'])} editable texts")
    final = await burn(clean, lay, work / "text", progress, name)
    return {"path": final, "clean": clean, "text_layer": lay, "effects": effects, "graphics": graphics}
