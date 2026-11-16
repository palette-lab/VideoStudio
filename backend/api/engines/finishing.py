"""Finishing pass for ads and product films: an on-screen hook, feature callouts, a measurement scale, accents at
the big cuts and an end card, drawn by Motion Studio (HyperFrames) on top of the finished footage.

1. One frame from the middle of every scene is taken from the real clips.
2. A graphics director (vision LLM) looks at those frames with the story and timings and plans each overlay: what it
   says, when, and where in the frame the thing it points at actually is (in % of the frame), so leader lines and
   scale bars sit on the product, not on a guess.
3. That plan becomes an exact brief for Motion Studio, which writes the HTML/GSAP overlay, checks it, renders it over
   the footage and reviews the rendered frames.
4. The film's own audio (narration + clip sound) is put back on the rendered video.
Numbers (sizes, weights, battery hours) come only from the brief; any value the director had to propose is listed in
"invented_specs" so the user can confirm or replace it before publishing.
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from router_core import config, llm

from . import motion_design, motion_engine
from .vendor.flow import video_merge

log = logging.getLogger("palette_video.engines")

# Genres that read better with graphics; cinematic, fashion and music films stay clean unless the brief asks.
FINISH_GENRES = {"advertisement", "ecommerce", "unboxing", "exploded_view", "ugc_ad", "banner", "explainer", "food",
                 "real_estate"}
ASK_WORDS = ("callout", "graphics", "text on screen", "on-screen", "measurement", "measure", "specs", "infographic",
             "hook text", "title card", "end card", "cta")

DIRECTOR_SYSTEM = """You are the motion-graphics director of a premium ad agency. You get a finished film (the brief,
the story, every scene with its exact start and end time, its narration, and ONE FRAME from the middle of each scene,
in order). Plan the graphics that go ON TOP of the footage to make it an ad that hooks, explains and sells.

PLANNED DIRECTION: "planned_direction" (when given) was decided together with the script: treatment, concept,
accent colour, energy curve and the planned graphic moments per scene. Start from it - keep its concept, accent and
moments, and return its treatment/concept/design - unless the frames show the planned thing is not really there
(truth beats the plan: drop or move that moment).

EFFECTS: "effects_already_on_screen" lists moments where an effect (a big word, a card, a stamp) is already in the
video. Put NO text overlay during those windows (an accent or nothing); plan graphics around them.

EXPLAINERS (genre Explainer / educational): the job is understanding, not selling. Use "label" (the name of the part
or idea, with a leader line to it), "step" (a numbered chip 1, 2, 3 for the order of the process), "arrow" (an
animated arrow or path showing what moves or flows: rotation direction, current, air, force - draw it where it
happens), "highlight" (a soft glowing ring around the part being explained right now) and short key-term captions
synced to the narration. The end card is a one-line summary of what was learned, no sales call to action. Label
only what is visibly there and state the physics correctly.

FIRST DECIDE whether this film gets graphics at all. Look at the frames, the story and the brief:
- Graphics help when the film has something to SAY that the picture cannot: a product to explain or sell (ads,
  UGC, e-commerce, unboxing, exploded views), an idea to teach (explainers), facts or numbers, a call to action.
- Graphics hurt when the film works on mood and image alone: cinematic stories, fashion films, music videos, art
  films, travel moods. Then add none.
- If "user_asked_for_graphics" is true, always add them.
Return {"add_graphics": false, "why": "one sentence"} when it gets none. Otherwise return the full plan below with
"add_graphics": true and "why".

Return ONLY JSON:
{"add_graphics": true, "why": "one sentence",
 "hook": {"text": "2-5 words, the scroll-stopper", "sub": "optional short line or ''", "start": 0.2, "end": 2.8,
          "style": "how it moves (e.g. slams in word by word, scale 1.3 to 1 with a white flash)"},
 "overlays": [{"scene": 1-based, "start": seconds, "end": seconds,
               "kind": "callout | measure | spec_badge | kinetic_text | lower_third | accent | label | step | arrow | highlight | circuit | part_label | guide | compare | sweep",
               "shape": "ring | line (measure only: ring = around a round part's rim, line = across a straight part)",
               "axis": "horizontal | vertical (measure line only)",
               "text": "main label", "detail": "second line or ''", "value": "e.g. 38 mm or ''",
               "target": {"x": 0-100, "y": 0-100, "w": 0-100, "h": 0-100, "what": "the thing it points at"},
               "place": "where the text block sits (e.g. right third, upper left) - never on a face or the product",
               "from_brief": true/false}],
 "accents": [{"at": seconds, "kind": "flash | light_sweep | whip_lines | glow_pulse | shockwave_ring | particle_burst | motion_streak | impact_flash | sheen | light_transition | mask_wipe", "why": "the cut or moment"}],
 "end_card": {"start": seconds, "title": "brand / product name", "line": "the promise in one line",
              "cta": "a call to action", "style": "how it builds"},
 "invented_specs": ["every number you used that is NOT in the brief, e.g. 'Stack height 38 mm'"]}

Rules:
- HOOK: in the first 3 seconds, big and bold, a promise, a question or a contrast. It must make sense with scene 1.
- EXPLAIN the product where it is on screen: "callout" = a leader line from a point ON the product (inside target)
  to a label; "measure" = a scale bar drawn beside the product along its real edge (e.g. a vertical bar from the
  sole to the top of the heel with tick marks and the value), aligned to target; "spec_badge" = a small chip with a
  number and a word. Use measure/callout on scenes where the product is large and steady in the frame (close-ups,
  the packshot), never on a scene where it is tiny or moving fast.
- MEASUREMENTS are optional. Add them ONLY when a size is a real reason to buy (shoes: heel stack; headphones: ear
  cup / driver size; a bag: capacity; a phone: screen; furniture: dimensions) or the user asked. At most 2 in the
  film, often 1, and often none.
- Measure ONE meaningful PART, not a box around the whole product: the round ear cup of headphones (a "ring" around
  its rim with the diameter), the heel stack of a shoe (a short vertical "line" across the sole at the heel), a
  screen diagonal, a strap width. Whole-product length/height only when the overall size is the point (luggage,
  furniture) and the product is shown fully side-on.
- Put a measurement only on a scene where that part is large, sharp and NOT MOVING (no rotation, no camera move)
  for the whole overlay time; never during a 360-degree turn, an exploded animation or a fast move. If no such
  moment exists, skip the measurement.
- For each measure give: "shape": "ring" (round part: rim) or "line" (straight edge), "axis" (line only),
  "target.what" naming the exact part ("the left ear cup's outer rim"), and the label placed outside the product.
- target is measured on THAT scene's frame you were given, in % of the frame (x, y = top-left corner, w, h = size).
  Look at the frame: the box must cover exactly the thing named in "what".
- At most 2 overlays at the same time; at most one per scene unless the scene is a product close-up. Leave story
  scenes with a person's face mostly clean (a lower_third or kinetic_text at the edge is fine).
- Text must not repeat the narration word for word; it adds the fact the picture cannot say (a number, a name).
- Accents mark the 1-3 biggest cuts or the emotional peak; subtle, 0.3-0.6 s.
- END CARD over the last scene: product name, the promise, the call to action.
- Numbers: use the brief's numbers. If the brief has none and a measurement is clearly wanted, propose a plausible
  value for the product type and list it in invented_specs. Never invent prices, awards or claims like "#1"."""


KINDS = {"callout", "measure", "spec_badge", "kinetic_text", "lower_third", "accent", "label", "step", "arrow",
         "highlight", "circuit", "part_label", "guide", "compare", "sweep"}
# Overlays that are only true on certain footage: a circuit on a device, part labels and guides on parts that are apart.
NEEDS_TREATMENT = {"circuit": "electronic_device", "part_label": "exploded_view", "guide": "exploded_view"}


def validate_overlays(gp: dict) -> list[dict]:
    """Keep overlays the renderer knows, with sane times, and only where the footage supports them (doc 5.1)."""
    main = str(gp.get("treatment") or "")
    per = {s.get("scene"): s.get("treatment") for s in gp.get("scene_treatments") or [] if isinstance(s, dict)}
    out = []
    for o in gp.get("overlays") or []:
        if not isinstance(o, dict) or o.get("kind") not in KINDS:
            continue
        try:
            if float(o.get("end")) <= float(o.get("start")) or float(o.get("start")) < 0:
                continue
        except (TypeError, ValueError):
            continue
        need = NEEDS_TREATMENT.get(o["kind"])
        if need and per.get(o.get("scene"), main) != need:
            log.info("%s overlay %r dropped: scene %s is not %s footage", o["kind"], o.get("text"), o.get("scene"), need)
            continue
        out.append(o)
    return out


def asked_for(brief: str) -> bool:
    """The user's own words ask for on-screen graphics: then they are always added."""
    text = (brief or "").lower()
    return any(w in text for w in ASK_WORDS)


def wants_graphics(genre: str, brief: str) -> bool:
    """Genres that usually benefit (a hint for the director; the director decides from the video itself)."""
    return genre in FINISH_GENRES or asked_for(brief)


async def _probe(ffmpeg: str, path: str) -> float:
    return await asyncio.to_thread(video_merge.probe_duration, ffmpeg, path)


async def scene_timings(ffmpeg: str, scene_paths: list[str]) -> list[tuple[float, float]]:
    out, t = [], 0.0
    for p in scene_paths:
        d = await _probe(ffmpeg, p)
        out.append((round(t, 2), round(t + d, 2)))
        t += d
    return out


async def scene_frames(ffmpeg: str, scene_paths: list[str], work: Path) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, p in enumerate(scene_paths):
        d = await _probe(ffmpeg, p)
        jpg = work / f"frame_{i + 1:02d}.jpg"
        await asyncio.to_thread(subprocess.run, [ffmpeg, "-y", "-v", "error", "-ss", f"{d * 0.55:.2f}", "-i", p,
                                                 "-frames:v", "1", "-vf", "scale=960:-2", str(jpg)],
                                capture_output=True, timeout=60)
        frames.append(jpg)
    return frames


CLEAN_RULES = """

CLEAN FINISH (this film is action / cinematic): the picture stays clean. "add_graphics" is true, but add ONLY
typography: the HOOK (big type in the first 3 s), "kinetic_text" title words on 1-3 key narration beats (at the
frame edges, never on a face or the action), and an END CARD with the title and one line ("cta" may be ''). No
callouts, measures, badges, labels, arrows, highlights, circuits or accents."""

FULL_RULES = """

FULL FINISH (a person explains / talks, or a product, device or ad): "add_graphics" MUST be true. Always give the
hook, at least one overlay per scene that explains what is said or shown (product callouts, labels, steps,
kinetic key words synced to the speech, spec badges only from the brief) and the end card."""


async def direct(brief: str, plan: dict, timings: list[tuple[float, float]], frames: list[Path],
                 narration: list[str], busy: list | None = None, level: str = "", captions: str | None = None) -> dict:
    scenes = []
    for i, (s, (a, b)) in enumerate(zip(plan["scenes"], timings)):
        scenes.append({"scene": i + 1, "start": a, "end": b, "title": s.get("title"), "beat": s.get("beat"),
                       "action": s.get("action"), "narration": narration[i] if i < len(narration) else ""})
    info = {"brief": brief, "user_asked_for_graphics": asked_for(brief) or level in ("full", "clean"),
            "genre_usually_benefits": plan.get("genre") in FINISH_GENRES, "genre": plan.get("genre_label"), "title": plan.get("title"), "story": plan.get("story"),
            "treatment_hint": (plan.get("edit_direction") or {}).get("treatment")
            or motion_design.treatment_hint(plan.get("genre")),
            "planned_direction": plan.get("edit_direction") or None,
            "total_seconds": timings[-1][1] if timings else 0, "scenes": scenes,
            "effects_already_on_screen": [{"start": a, "end": b, "effect": n} for a, b, n in (busy or [])]}
    if captions:
        from . import effects_engine
        top, bottom = effects_engine.CAPTION_BAND.get(captions, (58, 84))
        info["captions_on_screen"] = (
            f"Word-by-word captions ({captions} style) are burned in whenever someone speaks, in the band from "
            f"{top}% to {bottom}% of the frame height. Keep every text overlay OUT of that band (use the top third or "
            "the sides), use no lower_third, and never repeat the spoken words as kinetic text - add what is not said.")
    content = [{"type": "text", "text": json.dumps(info, ensure_ascii=False) + "\n\nThe frames follow, scene 1 first."}]
    content += [llm.image_part(f) for f in frames if f.exists()]
    sig = motion_design.pick_signature(info.get("treatment_hint"))
    rules = {"full": FULL_RULES, "clean": CLEAN_RULES}.get(level, "") + motion_design.signature_rules(sig)
    messages = [{"role": "system", "content": DIRECTOR_SYSTEM + motion_design.director_rules() + rules},
                {"role": "user", "content": content}]
    gp = await llm.chat_json(config.DEEP_VISION_MODEL, messages, max_tokens=12000, stage="engine.graphics_director")
    if level and not _has_graphics(gp):  # the finish was decided by the rules: ask once more instead of skipping
        messages += [{"role": "assistant", "content": json.dumps(gp, ensure_ascii=False)},
                     {"role": "user", "content": "This film must get graphics (see the FULL/CLEAN FINISH rules). "
                                                 "Return the full plan with add_graphics true."}]
        gp = await llm.chat_json(config.DEEP_VISION_MODEL, messages, max_tokens=12000, stage="engine.graphics_director")
    if isinstance(gp, dict) and _has_graphics(gp):
        gp["signature"] = sig
        motion_design.remember_signature(sig)
    return gp


def _has_graphics(gp: dict) -> bool:
    return bool(gp.get("add_graphics", True)) and bool(
        (gp.get("hook") or {}).get("text") or gp.get("overlays") or (gp.get("end_card") or {}).get("title"))


def apply_level(gp: dict, level: str) -> dict:
    """A clean finish keeps only typography: the hook, title words and the end card."""
    if level == "clean":
        gp["overlays"] = [o for o in gp.get("overlays") or [] if o.get("kind") in motion_design.CLEAN_KINDS]
        gp["accents"] = []
    if level:
        gp["add_graphics"] = True
    return gp


LOCATE_PROMPT = """Two frames of a video, {W}x{H}, at the start and the end of the moment a measurement is shown.
Find exactly this part: {WHAT}. Measurement shape: {SHAPE}.
- ring: the circle that runs along the part's outer rim (centre and radius).
- line: the segment {AXIS_HINT}, from one edge of the part to the opposite edge, ending exactly at the edges.
Use the FIRST frame for coordinates. Coordinates in % of the frame: x and r as % of the WIDTH, y as % of the HEIGHT.
"steady" is true only if the part is in the same place and size (within about 3% of the frame) in both frames.
Return ONLY JSON: {"found": true|false, "steady": true|false, "cx": 0, "cy": 0, "r": 0,
"x1": 0, "y1": 0, "x2": 0, "y2": 0, "confidence": 0.0}"""


async def locate(ffmpeg: str, video: str, o: dict, work: Path, w: int, h: int) -> dict | None:
    """Exact geometry of a measurement on the real frames (ring centre/radius or line end points, in px of the
    graphics frame), or None when the part is not found, moves during the overlay, or the match is unsure."""
    shape = "ring" if str(o.get("shape") or "").lower() == "ring" else "line"
    try:
        a, b = float(o.get("start")), float(o.get("end"))
    except (TypeError, ValueError):
        return None
    frames = []
    for k, t in enumerate((a + 0.1, max(a + 0.1, b - 0.1))):
        jpg = work / f"locate_{int(a * 10)}_{k}.jpg"
        await asyncio.to_thread(subprocess.run, [ffmpeg, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video,
                                                 "-frames:v", "1", "-vf", "scale=1280:-2", str(jpg)],
                                capture_output=True, timeout=60)
        if jpg.exists():
            frames.append(llm.image_part(jpg))
    if len(frames) < 2:
        return None
    axis = str(o.get("axis") or "vertical")
    prompt = (LOCATE_PROMPT.replace("{W}", str(w)).replace("{H}", str(h))
              .replace("{WHAT}", str((o.get("target") or {}).get("what") or o.get("text") or "the product"))
              .replace("{SHAPE}", shape)
              .replace("{AXIS_HINT}", "straight up/down" if axis == "vertical" else "left to right"))
    try:
        g = await llm.chat_json(config.DEEP_VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": prompt}, *frames]}], max_tokens=4000, stage="engine.graphics_locate")
    except Exception as e:
        log.warning("locate failed: %s", e)
        return None
    if not g.get("found") or not g.get("steady") or float(g.get("confidence") or 0) < 0.5:
        return None
    try:
        if shape == "ring":
            cx, cy, r = float(g["cx"]) * w / 100, float(g["cy"]) * h / 100, float(g["r"]) * w / 100
            return {"shape": "ring", "cx": round(cx), "cy": round(cy), "r": round(r)} if r > 8 else None
        x1, y1, x2, y2 = (float(g[k]) for k in ("x1", "y1", "x2", "y2"))
        p = {"shape": "line", "x1": round(x1 * w / 100), "y1": round(y1 * h / 100),
             "x2": round(x2 * w / 100), "y2": round(y2 * h / 100)}
        return p if abs(p["x2"] - p["x1"]) + abs(p["y2"] - p["y1"]) > 24 else None
    except (KeyError, TypeError, ValueError):
        return None


def required_texts(gp: dict) -> list[str]:
    """Values that must appear verbatim in the overlay code (numbers drift when the motion LLM repairs layout)."""
    out = []
    for o in gp.get("overlays") or []:
        if o.get("value"):
            out.append(str(o["value"]))
    return out


def motion_brief(gp: dict, total: float, plan: dict) -> str:
    """The director's plan as an exact, timed brief for Motion Studio."""
    creator = "ugc" in str(plan.get("genre_label") or "").lower()
    look = ("Creator style (UGC): bold rounded captions with a thick outline or a solid highlight box, sticker-like "
            "callouts with arrows, punchy pop-in motion, slightly playful; it should feel native to TikTok/Reels, "
            "not like a TV ad." if creator else
            "Premium, clean, confident: white text with a soft dark shadow or a subtle dark glass panel behind it for "
            "contrast, thin 1.5-2 px white leader lines and scale bars with small round end dots, one accent colour "
            "taken from the footage.")
    lines = [*motion_design.brief_header(gp), "", f"Graphics on top of the footage for “{plan.get('title') or ''}” ({total:.1f} s). {look} "
             "Coordinates below are % of the frame (x, y = top-left corner of the box). Keep the footage visible; "
             "never cover a face or the product with text. Use every text and every number EXACTLY as written below "
             "(same words, same values, same units) - never change, round or replace a value, also when fixing "
             "layout problems later. Layout rules for EVERY moment of every animation, not only the settled state: "
             "lines of a stacked title sit on fixed separate rows (each row its own absolutely positioned element with "
             "its own top) and never overlap while they animate in or out; a sub-line enters only after the title has "
             "settled and sits below it with a clear gap; every text block fits inside 6% safe margins - size each "
             "title so its longest row fits the stated width (shrink the font, never let it run off the frame); in a "
             "badge or label each value and each line of words is its own row and nothing is drawn on top of it.", ""]
    band = gp.get("captions_band")
    if band:
        lines.append(f"CAPTIONS are already burned into the footage between {band[0]}% and {band[1]}% of the frame "
                     "height: draw nothing in that band (move any text there to the top third), the end card included.")
    h = gp.get("hook") or {}
    if h.get("text"):
        lines.append(f"HOOK {h.get('start', 0.2):.1f}-{h.get('end', 2.8):.1f} s: “{h['text']}”"
                     + (f" with “{h['sub']}” under it" if h.get("sub") else "")
                     + f". Big (100-140 px), centred or lower third. Motion: {h.get('style') or 'slams in word by word'}.")
    for o in gp.get("overlays") or []:
        t = o.get("target") or {}
        box = (f"target box x {t.get('x')}%, y {t.get('y')}%, w {t.get('w')}%, h {t.get('h')}% ({t.get('what', '')})"
               if t else "")
        kind = o.get("kind", "callout")
        how = {"callout": "a dot appears on the target, the leader line draws out to the label, then the label types in",
               "measure": "a scale bar with tick marks grows along the target's edge (vertical for height, horizontal "
                          "for length), end caps snap on, then the value counts up next to it",
               "spec_badge": "a small rounded chip pops in with the value large and the word small",
               "kinetic_text": "the words animate in with a strong ease and leave cleanly",
               "lower_third": "a slim bar slides in from the left with the text",
               "label": "a dot lands on the part inside the target box, a thin leader line draws out to the name",
               "step": "a round numbered chip pops in with the step's short title beside it",
               "arrow": "an arrow path draws itself along the motion inside the target box (curved for rotation), "
                        "with a soft glow, then fades",
               "highlight": "a soft glowing ring fades in around the part in the target box and pulses once",
               "accent": "a quick graphic accent", **motion_design.KIND_MOTION}.get(kind, "animates in and out cleanly")
        geo = o.get("geometry")
        if kind == "measure" and geo:
            if geo["shape"] == "ring":
                how = (f"draw EXACTLY an SVG circle centred at ({geo['cx']}px, {geo['cy']}px) with radius {geo['r']}px "
                       "(thin 2 px white stroke, stroke-dashoffset animation so the ring draws itself around the rim), "
                       f"then a diameter line through the centre from ({geo['cx'] - geo['r']}px, {geo['cy']}px) to "
                       f"({geo['cx'] + geo['r']}px, {geo['cy']}px) with small end dots; a short leader line from the ring "
                       "to the label outside the product")
            else:
                how = (f"draw EXACTLY an SVG line from ({geo['x1']}px, {geo['y1']}px) to ({geo['x2']}px, {geo['y2']}px) "
                       "(2 px white, end caps as short perpendicular ticks), growing from the first point; the label "
                       "sits outside the product next to the line")
            box = "the coordinates above are measured on the real frame: use them as given, do not move them"
        elif kind == "measure" and o.get("axis"):
            how = (f"{o['axis']} scale bar exactly from one edge of the target box to the other "
                   f"({'left to right along its bottom' if o['axis'] == 'horizontal' else 'bottom to top along its side'}), "
                   "tick marks and end caps; it grows from one end, then the value counts up in the label")
        lines.append(f"{kind.upper()} {o.get('start', 0):.1f}-{o.get('end', 0):.1f} s (scene {o.get('scene')}): "
                     f"“{o.get('text', '')}”" + (f" / “{o['detail']}”" if o.get("detail") else "")
                     + (f" value “{o['value']}”" if o.get("value") else "") + f". {box}. Text block: "
                     f"{o.get('place') or 'beside the target'}. Motion: {how}.")
    for a in gp.get("accents") or []:
        lines.append(f"ACCENT at {float(a.get('at', 0)):.1f} s: {a.get('kind', 'flash')} ({a.get('why', '')}), 0.3-0.6 s, "
                     "subtle (a white flash at most 35% opacity, light sweeps as a soft diagonal gradient).")
    e = gp.get("end_card") or {}
    if e.get("title"):
        lines.append(f"END CARD from {float(e.get('start', max(0.0, total - 4))):.1f} s to the end: "
                     f"“{e['title']}” (largest), “{e.get('line', '')}”, then “{e.get('cta', '')}” "
                     f"as a pill button. A dark gradient fades in under it so the footage stays visible. "
                     f"Motion: {e.get('style') or 'title rises in, the line fades up, the CTA pops'}; hold the last 1 s.")
    return "\n".join(lines)


def review_times(gp: dict) -> list[float]:
    """Moments to check for overlapping text: just after each piece of text starts animating in, and once settled."""
    ts = []
    for item in [gp.get("hook") or {}, *(gp.get("overlays") or []), gp.get("end_card") or {}]:
        try:
            s = float(item.get("start"))
        except (TypeError, ValueError):
            continue
        ts += [s + 0.35, s + 0.7, s + 1.3]
    return ts


async def _with_audio(ffmpeg: str, graphics: Path, audio_from: str, out: Path) -> str:
    proc = await asyncio.to_thread(subprocess.run, [
        ffmpeg, "-y", "-v", "error", "-i", str(graphics), "-i", audio_from, "-map", "0:v", "-map", "1:a?",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"putting the audio back failed: {(proc.stderr or '')[-300:]}")
    return str(out)


async def run(app: dict, brief: str, plan: dict, video: str, scene_paths: list[str], narration: list[str],
              aspect_ratio: str, work: Path, progress, quality: dict, busy: list | None = None,
              level: str = "", captions: str | None = None) -> dict:
    """Returns {"path", "graphics", "invented_specs"}; raises when the graphics cannot be made."""
    why = motion_engine.available()
    if why:
        raise RuntimeError(why)
    ffmpeg = video_merge.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    work.mkdir(parents=True, exist_ok=True)
    await progress(f"{app['name']}: " + ("planning the hook and title typography" if level == "clean"
                                          else "planning the motion design: hook, callouts and labels"))
    timings = await scene_timings(ffmpeg, scene_paths)
    frames = await scene_frames(ffmpeg, scene_paths, work / "frames")
    gp = apply_level(await direct(brief, plan, timings, frames, narration, busy, level, captions), level)
    if captions:  # the captions own the lower band: no lower thirds on top of them
        from . import effects_engine
        gp["overlays"] = [o for o in gp.get("overlays") or [] if o.get("kind") != "lower_third"]
        gp["captions_band"] = list(effects_engine.CAPTION_BAND.get(captions, (58, 84)))
    gw, gh = motion_engine.SIZES.get(aspect_ratio, motion_engine.SIZES["16:9"])
    kept = []
    for o in validate_overlays(gp):  # measurements are placed on the real frames, or dropped
        if o.get("kind") == "measure":
            geo = await locate(ffmpeg, video, o, work / "frames", gw, gh)
            if not geo:
                log.info("measurement %r dropped: part not found or not steady", o.get("text"))
                continue
            o["geometry"] = geo
        kept.append(o)
    if gp.get("overlays") is not None:
        gp["overlays"] = kept
    drawable = (gp.get("hook") or {}).get("text") or gp.get("overlays") or (gp.get("end_card") or {}).get("title")
    if not drawable or (not gp.get("add_graphics", True) and not asked_for(brief)):
        await progress(f"{app['name']}: no graphics needed ({str(gp.get('why') or '')[:160]})")
        return {"path": video, "skipped": True, "why": gp.get("why") or "", "graphics": gp, "invented_specs": []}
    total = await _probe(ffmpeg, video)
    handoff = {"prompt": motion_brief(gp, total, plan), "request": {"aspect_ratio": aspect_ratio},
               "review_times": review_times(gp), "required_texts": required_texts(gp)}
    out = await motion_engine.run({**app, "name": f"{app['name']} graphics"}, handoff, {}, video, work, progress,
                                  quality, ["motion.overlay_graphics"])
    final = await _with_audio(ffmpeg, Path(out["path"]), video, work / "final_graphics.mp4")
    return {"path": final, "skipped": False, "why": gp.get("why") or "", "graphics": gp, "brief": handoff["prompt"],
            "invented_specs": [str(x) for x in gp.get("invented_specs") or []],
            "open_issues": out.get("open_issues") or []}
