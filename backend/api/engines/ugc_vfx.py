"""UGC VFX agent: the user's "Cinematic Video Effects, VFX & Motion Design for UGC" module as one agent.

Called automatically for UGC videos (creator ads, reviews, testimonials, influencer-style product videos) by the
film engine (after the scenes are planned, before any clip is rendered) and by Presenter Studio (after the talking
clips are made). One LLM call returns the effect intensity level and a per-10-second effect timeline split into the
module's four layers, each handed to the part of VideoStudio that can really do it:

    A  in the video model      -> appended to that scene's clip prompt (film engine only; presenter clips exist)
    B  post-production effects -> Effects Studio (mapped onto the effects it can render)
    C  motion graphics / type  -> the graphics pass (Motion Studio overlays)
    D  audio / sound design    -> the music mood and the effects' sound cues
The module's continuity rules and final validation checklist also go into the effects frame review.
"""

import json
import re
from pathlib import Path

from router_core import config, llm

MODULE = (Path(__file__).parent / "prompts" / "ugc_vfx_module.md").read_text(encoding="utf-8")

UGC_WORDS = re.compile(r"\b(ugc|user[- ]generated|creator|influencer|testimonial|review video|unboxing|tiktok|"
                       r"reels?|selfie|talking to (the )?camera|vlog)\b", re.I)
UGC_GENRES = {"ugc_ad"}
UGC_FORMATS = {"ugc_ad", "testimonial"}


def is_ugc(genre: str | None, brief: str = "", production_types=(), presenter_format: str | None = None) -> bool:
    if (genre or "") in UGC_GENRES or (presenter_format or "") in UGC_FORMATS:
        return True
    if "social_short" in (production_types or ()) and "talking_presenter" in (production_types or ()):
        return True
    return bool(UGC_WORDS.search(brief or ""))


HERE = """

---
## THIS VIDEO STUDIO (runtime - plan only what these can really do)
- Layer A (inside the video model): the AI clip model can do slow motion, a camera push-in or orbit, a light sweep,
  focus pull, macro framing, steam / splash / particles - write it as a short instruction for ONE scene's clip.
  {A_NOTE}
- Layer B (post-production, Effects Studio): creator effects rendered on the finished video - big words behind the
  person, spotlight, punch-ins, slow push, speed-ramp-like lean-in, product labels, cards and lists, before/after
  swipe, light / glow looks and colour grades. Describe the INTENT and timing; Effects Studio maps it to what it has.
- Layer C (motion graphics, Motion Studio overlays): animated product name, feature / benefit labels, tracking-style
  callouts with leader lines, arrows, measurement indicators, minimal kinetic typography, 2.5D / CSS-3D graphics,
  stylised exploded diagrams. Only facts from the brief - never invented specs, ingredients or benefits.
- Layer D (audio): music mood and tempo for the bed, and sound cues (whoosh, pop, tick, splash, sizzle) at moments.
- Captions are added separately when someone speaks: keep Layer C out of the lower-middle caption band.

THE VIDEO:
- Brief: {BRIEF}
- Length: {TOTAL:.1f} s.  Parts / scenes with their exact times:
{PARTS}

OUTPUT: return ONLY JSON:
{"product_category": "skincare | food | electronics | fashion | other: ...",
 "intensity": 1 | 2 | 3, "why_intensity": "one sentence",
 "grade": "one colour grade for the whole video",
 "timeline": [{"part": 1, "start": 0.0, "end": 10.0,
               "items": [{"layer": "A | B | C | D", "start": 0.0, "end": 2.0, "scene": 1,
                          "effect": "what exactly", "why": "what it does for the story"}]}],
 "transitions": [{"between_scenes": [1, 2], "type": "cut-on-action | match cut | whip-pan | ...", "why": "..."}],
 "not_possible_in_model": ["effects the video model cannot do reliably, moved to B or C"],
 "validation_checklist": ["the module's final checks, made specific to this video"]}
Use effects sparingly: level 1 = 1-2 B items and almost no C; level 2 = 2-4 B, 1-3 C; level 3 = up to 6 B, up to 5 C.
A continuous physical action stays continuous. Never cover the face, the product branding or the key action."""


def messages(brief: str, parts: list[dict], total: float, can_change_clips: bool) -> list[dict]:
    lines = "\n".join(f"  - scene {p['scene']} {p['start']:.1f}-{p['end']:.1f} s: {p.get('what', '')[:240]}"
                      for p in parts)
    a_note = ("These clips are not rendered yet: Layer A items will be generated." if can_change_clips else
              "The clips are ALREADY rendered (a talking presenter): return NO Layer A items; use B, C and D.")
    system = MODULE + (HERE.replace("{A_NOTE}", a_note).replace("{BRIEF}", (brief or "")[:2000])
                       .replace("{TOTAL:.1f}", f"{total:.1f}").replace("{PARTS}", lines or "  (one part)"))
    return [{"role": "system", "content": system},
            {"role": "user", "content": "Plan the effects for this UGC video now."}]


def clean(data, can_change_clips: bool, total: float) -> dict:
    d = data if isinstance(data, dict) else {}
    level = d.get("intensity") if d.get("intensity") in (1, 2, 3) else 2
    items = []
    for part in d.get("timeline") or []:
        for it in (part.get("items") if isinstance(part, dict) else None) or []:
            if not isinstance(it, dict) or it.get("layer") not in ("A", "B", "C", "D") or not it.get("effect"):
                continue
            if it["layer"] == "A" and not can_change_clips:
                continue
            try:
                start, end = float(it.get("start") or 0), float(it.get("end") or 0)
            except (TypeError, ValueError):
                continue
            start, end = max(0.0, start), min(total, max(end, start + 0.5))
            items.append({"layer": it["layer"], "start": round(start, 2), "end": round(end, 2),
                          "scene": it.get("scene") if isinstance(it.get("scene"), int) else None,
                          "effect": str(it["effect"])[:200], "why": str(it.get("why") or "")[:160]})
    return {"product_category": str(d.get("product_category") or "other")[:60], "intensity": level,
            "why_intensity": str(d.get("why_intensity") or "")[:200], "grade": str(d.get("grade") or "")[:120],
            "items": sorted(items, key=lambda x: x["start"])[:40],
            "transitions": [t for t in (d.get("transitions") or []) if isinstance(t, dict)][:12],
            "not_possible_in_model": [str(x)[:200] for x in (d.get("not_possible_in_model") or [])][:8],
            "validation_checklist": [str(x)[:200] for x in (d.get("validation_checklist") or [])][:12]}


async def direct(brief: str, parts: list[dict], total: float, can_change_clips: bool, chat=None) -> dict:
    data = await (chat or llm.chat_json)(config.REASONING_MODEL, messages(brief, parts, total, can_change_clips),
                                         max_tokens=10000, stage="engine.ugc_vfx")
    return clean(data, can_change_clips, total)


# ---------------------------------------------------------------- hand-offs to the existing passes

def in_model(plan: dict, scene_no: int) -> str:
    """Layer A for one scene, as a short instruction for its clip prompt."""
    fx = [i["effect"] for i in plan.get("items") or [] if i["layer"] == "A" and i.get("scene") == scene_no]
    return ("In-camera effect: " + "; ".join(fx[:2]) + ". The physical action stays clearly visible.") if fx else ""


def effects_handoff(plan: dict) -> dict:
    """Layer B (+ the grade and transitions) for Effects Studio: its style line, planned moments and effect count."""
    b = [i for i in plan.get("items") or [] if i["layer"] == "B"]
    lvl = plan.get("intensity", 2)
    style = (f"UGC VFX level {lvl} ({['', 'natural UGC', 'premium UGC', 'high-end commercial'][lvl]}): "
             f"{plan.get('why_intensity') or ''} Grade: {plan.get('grade') or 'subtle, natural'}. Keep the authentic "
             "creator energy; the physical action stays the primary layer; never cover the face, the product's "
             "branding or the key action; no effect hides an incomplete action.")
    moments = "UGC VFX AGENT - planned post-production effects (map each to the closest effect you have):\n" + \
        "\n".join(f"- {i['start']:.1f}-{i['end']:.1f} s: {i['effect']} ({i['why']})" for i in b)
    count = {1: "1-2", 2: "2-4", 3: "4-6"}[lvl]
    return {"effects_style": style, "planned_moments": moments if b else "", "count": count}


def graphics_handoff(plan: dict) -> list[dict]:
    """Layer C for the graphics director."""
    return [{"start": i["start"], "end": i["end"], "graphic": i["effect"], "why": i["why"]}
            for i in plan.get("items") or [] if i["layer"] == "C"]


def audio_note(plan: dict) -> str:
    d = [f"{i['effect']} at {i['start']:.1f} s" for i in plan.get("items") or [] if i["layer"] == "D"]
    return "; ".join(d[:8])


def review_checklist(plan: dict) -> str:
    checks = plan.get("validation_checklist") or []
    return ("\nAlso check (UGC VFX): is the product recognizable, are the physical actions still visible, are the "
            "graphics readable, does it keep authentic UGC energy" + ("; " + "; ".join(checks[:6]) if checks else "")
            + ".")


def summary(plan: dict) -> str:
    n = {k: sum(1 for i in plan.get("items") or [] if i["layer"] == k) for k in "ABCD"}
    return (f"level {plan.get('intensity')} for {plan.get('product_category')}: "
            f"{n['A']} in-camera, {n['B']} post effects, {n['C']} motion graphics, {n['D']} sound cues")


def to_json(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False)
