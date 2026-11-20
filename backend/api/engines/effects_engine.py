"""Effects Studio: creator-style finishing effects on a finished (or filmed) video, picked and timed by the AI.

Runtime: the "claude-video-effects" tool (Vibha Ramprakash, MIT; OpenCV YuNet face tracking, MIT; MediaPipe selfie
segmentation, Apache-2.0) vendored in tools/vendor-src/claude-video-effects with its own .venv (scripts/setup.sh).
Its fx.py renders a plan of effects onto the video with ffmpeg and keeps the original audio.

Flow: look at the video (length, is a face on screen, what is said and when) -> the LLM writes an effects plan from a
curated list suited to explanation videos -> render check stills at each effect's midpoint -> a vision model reviews
them -> one revision if needed -> render the full video.
"""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from router_core import config, llm

ROOT = Path(__file__).resolve().parents[3]
TOOL = Path(os.getenv("VIDEO_EFFECTS_DIR", ROOT / "tools" / "vendor-src" / "claude-video-effects"))
PY = TOOL / ".venv" / "bin" / "python"
FX = TOOL / "scripts" / "fx.py"
log = logging.getLogger(__name__)

# Curated for explanation videos: every effect here takes its text from the plan (no built-in branding) and needs no
# folder of the user's social posts. "face" = needs a person on screen to make sense.
CATALOG = {
    "backdrop-word": ("a giant word drops in behind the person/subject", "text (1-2 words), color (hex), font (bold|serif)", False),
    "spotlight": ("the room goes dark, the subject stays lit, a glowing word behind", "text, color", False),
    "huge-strike": ("a huge serif word behind, then a red line crosses it out (myth vs fact)", "text, at", False),
    "crayon-write": ("a red crayon writes a word behind the subject and underlines it", "text", False),
    "led-sign": ("lights drop, a glowing LED sign types itself out behind", "text (lines with |), color", False),
    "headline-box": ("punch-in with a two-line headline, second line in a white box", "text (2 lines with |), zoom", False),
    "split-headline": ("a headline card slides down and pushes the video to the bottom half", "text (lines with |), hl (highlighted word)", False),
    "checklist": ("a task list card; each step spins then ticks green", "title (required), text (step::meta|step::meta)", False),
    "todo": ("a checklist card beside the subject ticks off as it is said", "title (required), text (items with |), ticks (seconds with |)", False),
    "liquid-glass-graph": ("a glass card with a climbing graph and a counting number", "text (label, required), from, to (e.g. 12K, 59%) - real numbers only", False),
    "apple-toggles": ("an iPhone settings card flips its switches on, one line at a time", "text (rows with |), ticks", False),
    "messages": ("iMessage notifications pop in one after another", "text (messages with |), title", False),
    "the-bill": ("instructions print out like a receipt", "text (lines with |)", False),
    "tape-label": ("a strip of yellow tape with a line lands on the chest/lower frame", "text", False),
    "reject-stamp": ("a big red stamp slams down (e.g. MYTH, WRONG)", "text, color", False),
    "pause-point": ("freeze frame with a hand-drawn arrow and a label", "text (label)", False),
    "scribble": ("marker doodles draw themselves around the subject", "text (label), color", False),
    "select-resize": ("a selection box grabs a number and a cursor drags it bigger", "text (the number, e.g. 3X)", False),
    "countdown-flip": ("a 3, 2, 1 flip clock", "from", False),
    "comment-box": ("a comment bar types a word and posts it", "text", False),
    "moody": ("a colour grade", "grade (moody|bw|warm|cool|pop)", False),
    "slow-push": ("a slow cinematic push-in while the background softens", "zoom, blur", True),
    "lean-in": ("a fast punch-in on one word", "zoom (max 2)", True),
    "locked-on": ("corner brackets lock onto the face, the camera follows", "zoom, text (tag)", True),
    # Advanced effects (Adam Digital edition, MIT): the premium tier for a person presenting a product or device.
    "pop-word": ("a huge bold word pops in behind the person in a brand colour (the product name or its one big benefit)",
                 "text (1-2 words), color (hex) or brand (logo name from LOGOS for its brand colour)", False),
    "logo-link": ("two real brand logos pop in beside the face, joined by a dashed line behind the head (X works with Y, "
                  "X vs Y, made by X)", "text (two logo names from LOGOS with |), label (two display names with |)", True),
    "over-the-shoulder-zoom": ("the camera pushes into one top corner, a liquid glass graph pops beside the head, then "
                               "the other corner with a second graph", "text (two labels with |), to (two real numbers with |)", True),
    "dot-reveal": ("the shot builds itself out of glowing dots, then snaps into the full picture (an opening reveal)",
                   "color (hex)", True),
    "halftone-paper": ("a cream paper page: the person cut out bottom-right, the spoken words building at the top with "
                       "red serif keywords (needs speech)", "keywords (words with |)", True),
    "red-collage": ("black-and-white person, a giant red word behind, kinetic lines stacked beside them",
                    "text (1 word), lines (2-3 short lines with |), tag", True),
    "cover-star": ("the person becomes a magazine cover with the title behind the head (a launch or hero moment)",
                   "text (cover title), lines (2-3 cover lines with |), issue", True),
    "lineup": ("numbered tiles fan out above the head (top features, 3-5 items)", "text (items with |)", False),
    "shortcut-rain": ("keyboard keys rain down behind the person (laptops, keyboards, software shortcuts)",
                      "text (keys with |, e.g. ⌘|K)", False),
    "swipe": ("a before-and-after slider sweeps across the frame (before vs after using the product)",
              "grade (bw|moody|warm|cool|pop for the 'before' side)", False),
    "rabbit-hole": ("the frame tunnels into itself, spinning (a 'mind blown' moment)", "speed, spin", False),
    "tier-list": ("a tier list drops in and one item flies into S tier (ranking against rivals)",
                  "title, item (the winner), others (four rivals with |)", False),
    "uninstall": ("a glitchy uninstall bar breaks the frame apart (replacing an old way or a rival)", "text", False),
    # More of the tool's 55 effects (the ones that need no folder of the user's own reels or a personal profile).
    "tap": ("a Follow button pops up and flips to Following (the call to action)", "at (video second of the flip), color", False),
    "reel-frame": ("the video shrinks into an Instagram reel card", "text (the account or brand handle)", False),
    "timeline": ("a film grade with an editing timeline, a REC timecode and a glowing playhead (behind the scenes)",
                 "text (title), sub, look (red for the dark red film grade)", False),
    "match-cut": ("news pages cut rapidly on one word, locked in a red box behind the head (a big name or topic)",
                  "text (the word), color", True),
    "headline-wall": ("headlines pile up behind the head, then a total pops in front (social proof, press)",
                      "text (headline::meta|headline::meta), total, label", True),
    "card-drop": ("the person slides down and a floating card with a short line pops in above them",
                  "text (the card's line)", True),
}
# Named looks: the same effect with a different look (pass "look" in opt).
LOOKS = {"spotlight": "look=sparkle: golden rays, a big gold word with a shine sweep, twinkling sparkles",
         "cover-star": "look=editorial: soft warm film look, classic serif masthead, elegant cover lines",
         "liquid-glass-graph": "look=bento: the graph on a smoked glass screen behind the head with glass bento tiles"}
# What each effect does in the edit, so every film's set covers the jobs an edit needs.
KIND = {"word": {"backdrop-word", "spotlight", "huge-strike", "crayon-write", "led-sign", "pop-word", "red-collage",
                 "match-cut", "reject-stamp", "tape-label"},
        "card": {"headline-box", "split-headline", "checklist", "todo", "apple-toggles", "messages", "the-bill",
                 "lineup", "tier-list", "card-drop", "headline-wall", "comment-box", "liquid-glass-graph",
                 "over-the-shoulder-zoom", "select-resize"},
        "camera": {"slow-push", "lean-in", "locked-on", "dot-reveal", "rabbit-hole", "pause-point", "reel-frame"},
        "look": {"moody", "swipe", "timeline", "halftone-paper", "cover-star", "scribble", "countdown-flip",
                 "shortcut-rain", "uninstall", "logo-link"},
        "cta": {"tap", "comment-box"}}
SET_SIZE = 12  # effects offered to the editor for one film (from these it picks 3-7)
# Word-by-word captions over the whole clip whenever someone speaks (the tool transcribes with whisper and steps
# them aside while a lower-screen card is showing). The style varies from film to film.
CAPTION_STYLES = {"clean": "white words, keywords in gold serif italic, just under the face",
                  "bold": "one big word at a time, yellow keywords",
                  "pill": "short phrases on a dark pill",
                  "grey": "small grey-pill subtitles"}
CAPTION_BAND = {"clean": (58, 82), "bold": (70, 84), "pill": (70, 83), "grey": (64, 75)}  # % of the frame height


def caption_style(calm: bool = False, rng=None) -> str:
    """This film's caption style, leaning away from the last films' (calm films: clean or grey only)."""
    import random
    from . import approaches
    rng = rng or random
    pool = ["clean", "grey"] if calm else list(CAPTION_STYLES)
    recent = approaches.recent("captions")[-4:]
    return rng.choices(pool, weights=[1.0 / (1 + 3 * recent.count(x)) for x in pool])[0]


def captions_entry(style: str, keywords) -> dict:
    kw = [str(k).strip() for k in (keywords or []) if str(k).strip()][:10]
    return {"fx": "captions", "style": style, **({"keywords": "|".join(kw)} if kw else {})}
# Effects a person presenting a product or device should lean on.
ADVANCED = ("pop-word", "logo-link", "over-the-shoulder-zoom", "dot-reveal", "halftone-paper", "red-collage", "cover-star",
            "lineup", "shortcut-rain", "swipe", "rabbit-hole", "tier-list", "liquid-glass-graph", "apple-toggles",
            "select-resize", "spotlight", "led-sign", "slow-push", "locked-on")
REQUIRED_TEXT = {"backdrop-word", "spotlight", "huge-strike", "crayon-write", "led-sign", "headline-box", "split-headline",
                 "checklist", "todo", "liquid-glass-graph", "apple-toggles", "messages", "the-bill", "tape-label",
                 "reject-stamp", "pause-point", "select-resize", "comment-box", "pop-word", "logo-link", "lineup",
                 "red-collage", "cover-star", "shortcut-rain", "uninstall", "reel-frame", "match-cut", "headline-wall",
                 "card-drop"}
# Effects that draw no letters: the only ones used when the words live in the editable text layer (text_layer.py).
NO_TEXT_FX = ("moody", "slow-push", "lean-in", "dot-reveal", "swipe", "rabbit-hole")
PRODUCT_WORDS = ("product", "device", "phone", "smartphone", "iphone", "galaxy", "pixel", "laptop", "macbook", "tablet",
                 "ipad", "watch", "smartwatch", "earbuds", "headphones", "airpods", "speaker", "camera", "gadget", "app",
                 "software", "tool", "launch", "unbox", "review", "feature", "specs", "battery", "chip", "gaming",
                 "console", "keyboard", "monitor", "drone", "skincare", "shoe", "brand")


def pick_set(names: list[str], rng=None) -> list[str]:
    """This film's effects: a random set of SET_SIZE that leans away from what the last films used, and still
    covers every job (a word, a card, a camera move, a look, a call to action), so films don't all look alike."""
    import random
    from . import approaches
    rng = rng or random
    recent = approaches.recent("effects")[-24:]
    weight = {n: 1.0 / (1 + 3 * recent.count(n)) for n in names}  # used lately -> much less likely
    chosen: list[str] = []

    def draw(pool):
        pool = [n for n in pool if n not in chosen]
        if pool:
            chosen.append(rng.choices(pool, weights=[weight[n] for n in pool])[0])
    for kind in ("word", "word", "card", "card", "camera", "look", "cta"):
        draw([n for n in names if n in KIND[kind]])
    while len(chosen) < min(SET_SIZE, len(names)):
        draw(names)
    return chosen


def _logos() -> set[str]:
    try:
        icons = json.loads((TOOL / "assets" / "logos.json").read_text()).get("icons") or {}
    except (OSError, ValueError):
        return set()
    return {k for k in icons} | {a for v in icons.values() for a in (v.get("aka") or [])}


def product_talk(face: bool, brief: str, segs: list[dict]) -> bool:
    """A person on screen talking about a product or device: the advanced effects tier applies."""
    if not face:
        return False
    text = " " + " ".join([brief or ""] + [s.get("text") or "" for s in segs]).lower() + " "
    return any(re.search(rf"\b{re.escape(w)}s?\b", text) for w in PRODUCT_WORDS)


PRODUCT_RULES = """- ADVANCED TIER: a person is presenting a product or device. Make it feel like a premium creator
  edit: at least {MIN_ADV} of the effects come from [{ADVANCED}] when today's set has them. This tier and its effect
  count win over any count or "no cards" note in the style line below. Ideas, each only if today's set has it:
  * The product's name or its one big benefit: a big word effect the moment it is first said.
  * Brands named in the video that exist in LOGOS: logo-link (product brand | the thing it works with or beats).
  * Specs and numbers that are actually said (48MP, 2X, 30 h, 59%): select-resize, liquid-glass-graph or
    over-the-shoulder-zoom. Never invent a number.
  * A list of features: lineup or apple-toggles. Before vs after: swipe. Ranked against rivals: tier-list.
  * Open strong (dot-reveal or slow-push in the first 2 s) and give the hero/launch moment one cinematic effect
    (spotlight, led-sign, cover-star or red-collage).
- LOGOS (only these names work for logo-link and brand): {LOGOS}"""

SYSTEM = """You are the finishing editor for short explanation videos. Pick creator-style effects that make the
explanation clearer and more engaging, and time them to what is said. Return ONLY JSON:
{"effects": [{"fx": "<name>", "start": <s>, "end": <s>, "text": "...", "opt": {"key": "value"}}], "why": "one line",
 "caption_keywords": ["product names, brand names, numbers and the key verbs said, as spoken (max 8)"]}

Rules:
- TODAY'S EFFECTS: use ONLY these (picked for this film so videos don't all look alike; use a wide mix of them,
  and prefer the less obvious ones when they fit the moment) (name: what it does; options):
{CATALOG}
- Numeric options (zoom, speed, spin, size, count, push, scale, x, y, ...) are plain numbers like 1.3 - never words
  like "in" or "slow"; leave an option out to use its default.
- {COUNT} effects, each 1.5-4 s long, inside 0..{D} s, not overlapping each other, ordered by start time.
- Time each effect to the words in the TRANSCRIPT (land a word effect on the moment that word or idea is said). With
  no transcript, spread them over the video's key moments.
- Every text comes from the brief or the transcript, in the video's language: short (1-3 words for big words, max ~6
  words per line). Never invent numbers: graphs and counters only with numbers the user or the transcript gives.
- Always supply text for effects that show text (and title for checklist/todo); never leave defaults.
- {FACE}
- Prefer variety (one word effect, one card/list effect, one camera or grade effect) over repeating the same kind.
- Effect budget (motion-design rule): a few polished, meaningful effects beat many; never the same effect twice;
  spend effects on the strongest beats (the hook in the first 2 s, the peak, the payoff) and keep the rest clean.
  Never cover the product's silhouette or the moment an exploded view separates its parts.
{PRODUCT}- {STYLE}"""


class EffectsError(RuntimeError):
    pass


def available() -> str | None:
    if not (PY.exists() and FX.exists()):
        return f"The effects tool is not set up at {TOOL} (run scripts/setup.sh there)."
    return None


async def _run(args: list[str], timeout: int = 1800) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise EffectsError(f"{Path(args[1]).name if len(args) > 1 else args[0]} took longer than {timeout} s.")
    return proc.returncode, out.decode(errors="replace")


async def _duration(path: str) -> float:
    code, out = await _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], 60)
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


FACE_SCRIPT = r"""
import cv2, json, subprocess, sys, numpy as np
src, model, dur = sys.argv[1], sys.argv[2], float(sys.argv[3])
det = cv2.FaceDetectorYN.create(model, "", (320, 320), 0.6)
hits = 0
for t in (dur * 0.2, dur * 0.5, dur * 0.8):
    png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "png", "-"], capture_output=True).stdout
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR) if png else None
    if img is None:
        continue
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    hits += 0 if faces is None else 1
print(json.dumps({"face_frames": hits}))
"""


async def has_face(video: str, dur: float) -> bool:
    code, out = await _run([str(PY), "-c", FACE_SCRIPT, video, str(TOOL / "models" / "yunet.onnx"), f"{dur:.2f}"], 120)
    try:
        return json.loads(out.strip().splitlines()[-1]).get("face_frames", 0) >= 2
    except (ValueError, IndexError):
        return False


async def transcript(video: str, work: Path, language: str | None = None) -> list[dict]:
    wav = work / "speech.wav"
    code, _ = await _run(["ffmpeg", "-v", "error", "-y", "-i", video, "-t", "600", "-ac", "1", "-ar", "16000", str(wav)], 120)
    if code != 0 or not wav.exists():
        return []  # no audio track
    try:
        from . import lang
        t = await llm.transcribe(wav, lang.iso(language))
    except llm.LLMError:
        return []
    return [{"start": round(g.get("start") or 0, 2), "end": round(g.get("end") or 0, 2), "text": (g.get("text") or "").strip()}
            for g in t.get("segments", []) if (g.get("text") or "").strip()][:200]


def clean_plan(plan: dict, dur: float, face: bool, allowed=None) -> list[dict]:
    """Keep only allowed effects with sane, non-overlapping windows inside the video and the text they need."""
    out, last_end, used = [], 0.0, set()
    for e in sorted(plan.get("effects") or [], key=lambda e: float(e.get("start") or 0)):
        fx = str(e.get("fx") or "")
        if fx not in CATALOG or (CATALOG[fx][2] and not face) or (allowed and fx not in allowed) or fx in used:
            continue
        try:
            start, end = float(e.get("start")), float(e.get("end"))
        except (TypeError, ValueError):
            continue
        start, end = max(start, last_end, 0.0), min(end, dur - 0.05)
        if end - start < 1.0:
            continue
        end = min(end, start + 4.0)
        item = {"fx": fx, "start": round(start, 2), "end": round(end, 2)}
        if e.get("text"):
            item["text"] = str(e["text"])[:120]
        if fx in REQUIRED_TEXT and not item.get("text"):
            continue
        for k, v in (e.get("opt") or {}).items():
            if k not in ("text", "start", "end", "fx") and v not in (None, ""):
                item[str(k)] = str(v)[:120] if not isinstance(v, (int, float)) else v
        if fx in ("checklist", "todo") and not item.get("title"):
            continue
        if fx == "logo-link" or item.get("brand"):  # only logos the tool ships; an unknown name would draw a blank
            known = _logos()
            if fx == "logo-link":
                names = [n.strip().lower() for n in str(item.get("text") or "").split("|") if n.strip()]
                if len(names) != 2 or any(n not in known for n in names):
                    continue
                item["text"] = "|".join(names)
            if item.get("brand") and str(item["brand"]).lower() not in known:
                item.pop("brand")
        if item.get("look") and fx not in LOOKS:
            item.pop("look")
        out.append(item)
        used.add(fx)
        last_end = end
    return out[:8]


async def _stills(plan: list[dict], video: str, work: Path) -> list[Path]:
    planf = work / "effects_check.json"
    planf.write_text(json.dumps({"effects": plan}))
    times = ",".join(f"{(e['start'] + e['end']) / 2:.2f}" for e in plan if "start" in e)
    target = work / "check.png"
    code, out = await _run([str(PY), str(FX), "--plan", str(planf), video, str(target), "--still", times], 600)
    shots = sorted(work.glob("check*.png"))
    if code != 0 and not shots:
        raise EffectsError("Could not render the check frames: " + out[-300:])
    return shots


REVIEW = """These frames show finishing effects on an explanation video, one frame per effect (in order).
Effects plan: {PLAN}
List only problems a viewer would notice: text covering a face, text cut off by the frame edge, unreadable or
misspelled text, text that does not fit the video's message, an effect that looks broken. Return JSON
{"ok": true|false, "problems": ["effect N: problem", ...]}."""


async def _review(shots: list[Path], plan: list[dict]) -> list[str]:
    if not shots:
        return []
    parts = [{"type": "text", "text": REVIEW.replace("{PLAN}", json.dumps(plan, ensure_ascii=False))}]
    for p in shots[:6]:
        jpg = p.with_suffix(".jpg")
        await _run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", "scale=-2:720", str(jpg)], 60)
        parts.append(llm.image_part(jpg if jpg.exists() else p))
    try:
        data = await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": parts}], max_tokens=2000,
                                   reasoning_effort="low", stage="engine.effects.review")
    except llm.LLMError:
        return []
    probs = [str(x) for x in (data.get("problems") or []) if str(x).strip()]
    return [] if data.get("ok") is True and not probs else probs[:8]


def _numeric_keys() -> set[str]:
    """Options fx.py reads as numbers (float(e.o.get('zoom', 1.2)), int(...)), taken from fx.py itself."""
    try:
        return set(re.findall(r"(?:float|int)\(\s*e\.o\.get\(\s*'([a-z_]+)'", FX.read_text()))
    except OSError:
        return set()


def _sanitize(plan: list[dict]) -> list[dict]:
    """The planner sometimes writes a word where fx.py needs a number ("zoom": "in", "speed": "slow"); one such value
    crashes the whole render. Drop it so that effect uses its own default."""
    keys = _numeric_keys()
    out = []
    for e in plan:
        e = dict(e)
        for k in [k for k in e if k in keys and k not in ("start", "end")]:
            try:
                float(e[k])
            except (TypeError, ValueError):
                log.info("effects: dropping non-numeric %s=%r on %s", k, e[k], e.get("fx"))
                e.pop(k)
        out.append(e)
    return out


async def _render(plan: list[dict], video: str, work: Path) -> Path:
    planf = work / "effects.json"
    planf.write_text(json.dumps({"effects": _sanitize(plan)}, ensure_ascii=False))
    out_path = work / "effects.mp4"
    code, out = await _run([str(PY), str(FX), "--plan", str(planf), video, str(out_path)], 2400)
    if code != 0 or not out_path.exists():
        raise EffectsError("The effects render failed: " + out[-400:])
    return out_path


async def run(app: dict, handoff: dict, video: str, work: Path, progress) -> dict:
    why = available()
    if why:
        raise EffectsError(why)
    work.mkdir(parents=True, exist_ok=True)
    await progress(f"{app['name']}: watching the video")
    dur = await _duration(video)
    if dur < 2:
        raise EffectsError("The video is too short for effects.")
    face, segs = await asyncio.gather(has_face(video, dur), transcript(video, work, handoff.get("language")))
    text_free = bool(handoff.get("text_free"))  # the words go in the editable text layer, not the pixels
    want_captions = bool(segs) and handoff.get("captions", True) and not text_free
    cap = handoff.get("caption_style") or caption_style(bool(handoff.get("captions_only")))
    if handoff.get("captions_only"):  # a calm film: only captions, no effects
        if not want_captions:
            return {"path": video, "skipped": True, "why": "no speech to caption", "plan": [], "captions": None,
                    "transcript": segs}
        await progress(f"{app['name']}: adding {cap} captions")
        out_path = await _render([captions_entry(cap, [])], video, work)
        from . import approaches
        approaches.remember("captions", cap)
        return {"path": str(out_path), "duration_s": round(await _duration(str(out_path)), 2), "plan": [],
                "captions": cap, "face": face, "speech": True, "revised": False, "advanced": False,
                "transcript": segs}
    advanced = product_talk(face, handoff.get("prompt") or "", segs) and not text_free
    count = ("3-4" if dur <= 15 else "4-5" if dur <= 40 else "5-7") if advanced else \
        ("2-3" if dur <= 15 else "3-4" if dur <= 40 else "4-6")
    if text_free:  # only camera moves and grades: a few well-placed ones
        count = "1-2" if dur <= 20 else "2-4"
    usable = [k for k, v in CATALOG.items() if (face or not v[2]) and (segs or k != "halftone-paper")
              and (advanced or k != "logo-link") and (not text_free or k in NO_TEXT_FX)]
    todays = usable if text_free else pick_set(usable)  # a different set every film, so the effects vary from video to video
    catalog = "\n".join(f"  - {k}: {CATALOG[k][0]}; options: {CATALOG[k][1]}"
                        + (f"; or {LOOKS[k]}" if k in LOOKS else "") for k in todays)
    product = PRODUCT_RULES.replace("{MIN_ADV}", "2" if dur <= 15 else "3").replace(
        "{ADVANCED}", ", ".join(ADVANCED)).replace("{LOGOS}", ", ".join(sorted(_logos()))) + "\n" if advanced else ""
    system = (SYSTEM.replace("{CATALOG}", catalog).replace("{COUNT}", count).replace("{D}", f"{dur:.1f}")
              .replace("{PRODUCT}", product)
              .replace("{STYLE}", handoff.get("effects_style") or "Match the effects to the kind of video.")
              .replace("{FACE}", "A person's face is on screen: effects go behind or around them, never over the face."
                       if face else "No face is on screen: do not use face effects; place word effects behind the main subject."))
    user = (f"VIDEO: {dur:.1f} s, face on screen: {'yes' if face else 'no'}.\nTRANSCRIPT (seconds):\n"
            + ("\n".join(f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in segs) or "(no speech)")
            + f"\n\nBRIEF:\n{handoff.get('prompt') or ''}"
            + (f"\n\n{handoff['planned_moments']}\nLand the effects on these planned moments (hook, peak, payoff) "
               "and use the accent colour; the transcript timing still decides the exact second."
               if handoff.get("planned_moments") else ""))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    effort = "high" if advanced else "medium"  # the product tier asks more of the planner: give it room to think
    await progress(f"{app['name']}: choosing the {'advanced product ' if advanced else ''}effects")
    data = await llm.chat_json(config.REASONING_MODEL, messages, max_tokens=8000 if advanced else 4000,
                               reasoning_effort=effort, stage="engine.effects.plan")
    plan = clean_plan(data, dur, face, todays)
    if not plan and not want_captions:
        if handoff.get("allow_none") or text_free:  # the editor decided this video is better without effects
            return {"path": video, "skipped": True, "why": str(data.get("why") or ""), "plan": [], "transcript": segs}
        raise EffectsError("No usable effects were planned for this video.")
    caps = [captions_entry(cap, data.get("caption_keywords"))] if want_captions else []
    await progress(f"{app['name']}: checking {len(plan)} effects on still frames")
    problems = await _review(await _stills(plan + caps, video, work), plan) if plan else []
    if problems:
        await progress(f"{app['name']}: adjusting the effects")
        messages += [{"role": "assistant", "content": json.dumps(data)},
                     {"role": "user", "content": "Check frames show these problems; return the corrected JSON plan:\n"
                                                 + "\n".join(problems)}]
        try:
            revised = clean_plan(await llm.chat_json(config.REASONING_MODEL, messages, max_tokens=8000 if advanced else 4000,
                                                     reasoning_effort=effort, stage="engine.effects.plan"), dur, face, todays)
            plan = revised or plan
        except llm.LLMError:
            pass
    await progress(f"{app['name']}: rendering {len(plan)} effects" + (f" and {cap} captions" if caps else ""))
    out_path = await _render(plan + caps, video, work)
    from . import approaches
    for e in plan:  # the next films lean away from these
        approaches.remember("effects", e["fx"], keep=40)
    if caps:
        approaches.remember("captions", cap)
    return {"path": str(out_path), "duration_s": round(await _duration(str(out_path)), 2), "plan": plan,
            "captions": cap if caps else None,
            "face": face, "speech": bool(segs), "revised": bool(problems), "advanced": advanced, "transcript": segs}
