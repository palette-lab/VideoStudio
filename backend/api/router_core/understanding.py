"""Prompt understanding: one LLM call turns the prompt + perception reports into a validated production brief."""

import json
import re

from . import config, llm, registry, schemas

PROMPT_VERSION = "brief-v11"

BRIEF_SYSTEM = f"""You are the understanding stage of a video-production router, thinking like an experienced
video-production director. The router never makes videos itself; it later chooses existing applications.
Your only job: describe precisely what the user wants, as a structured production brief.

How to think:
- Read the whole request and every uploaded file's perception report (their real content, not filenames).
- Decide what each file is FOR. A photo containing a shoe might be the product to feature, a style reference, or a
  background; only the user's words and context decide. If the purpose is genuinely unclear, use role "unclear".
- Different wordings of the same goal must give the same brief; similar-sounding requests can need different output
  (e.g. "floating product in a dark studio" is one continuous realistic shot; "exploded view" is precise programmatic
  animation; "a story in scenes" is multi-scene).
- Never assume one production type implies another. A product video is not automatically a story; a fashion film is
  not automatically multi-scene; an ad does not automatically need music or captions.
- shot_structure: "single_continuous" only if one continuous shot is asked for or clearly implied (one quick clip, a
  single shot, "a 5-second clip of ..."); "multi_scene" if several scenes/shots/a sequence/story progression are asked
  for or clearly needed; otherwise "unspecified".
- motion.kinds: "realistic" = camera-filmed-looking footage (people, products in environments, cinematic shots);
  "programmatic_precise" = exact, code-like animation (exploded views, technical callouts, kinetic text, charts).
  motion.integration: how programmatic graphics relate to realistic footage:
  "separate_segments" = the programmatic animation fills its own scene(s) or time in the film, placed between the
  realistic scenes (e.g. "4. Technical sequence: an exploded view", "an animated intro, then the film");
  "overlay_on_footage" = graphics on top of realistic shots (callouts over a shot, a title or slogan over the last
  shot); "standalone" = no realistic footage; "unspecified" = not stated. When a request has both (e.g. its own
  exploded-view scene AND a slogan over the final shot), the own scene decides: "separate_segments". For
  "separate_segments" add an evidence entry with field "motion.integration" quoting the user's words that make it its
  own scene/part.
  motion.overlay_elements: the programmatic elements that go ON TOP of realistic shots (a slogan or title over the
  last shot, spec callouts next to the moving product, a logo bug), even when other elements are their own scenes.
  Ads often mix both: e.g. exploded view as its own scene + slogan over the final shot = integration
  "separate_segments" and overlay_elements ["kinetic_text"]. Leave it empty if nothing is placed over footage.
  Any text that must appear on screen (slogan, title, tagline, brand line, logo text) is the programmatic element
  "kinetic_text"; never leave it out.
- production_type "talking_presenter": a person speaks to the camera with lip-synced speech for most of the video
  (a YouTube-style explainer with a host, a UGC/creator ad where someone talks about a product, a testimonial, a
  spokesperson, an avatar or lip-sync video, or a photo of a person made to say a script or the uploaded audio). A
  narrator over footage is NOT talking_presenter. Add it next to the other types that fit (e.g. ["talking_presenter",
  "explainer"]). An uploaded photo of the presenter is role "character"; an uploaded recording they should say is
  role "audio_track" with use "audio_source". A UGC/creator video that must SHOW things beyond a talking head
  (unboxing, product close-ups, using/wearing/carrying the product, several locations) is a multi-scene film: set
  shot_structure "multi_scene", motion.kinds ["realistic"], production_type ["social_short", "commercial"] (also
  "talking_presenter" only if the creator talks to camera in it), consistency ["the creator", "the product"]. UGC,
  TikTok, Reels and Shorts are vertical: aspect_ratio "9:16" unless the user says otherwise. Never ask about the
  script, speech style or language: write a natural one and list it as an assumption.
- audio fields: "requested" only if the user asks for it; "excluded" if they rule it out; else "unspecified".
  Subtitles/captions are audio.subtitles (+ editing "captions"), never motion "kinetic_text"; kinetic_text means
  designed animated titles or typography.
- editing.operations: only operations the user explicitly asks for. "effects" = creator-style effects/graphics/punch-ins
  on their (or the finished) video, e.g. "add effects", "make it more engaging with text pops". Transitions between the scenes of a NEW film are
  part of producing it, not an editing operation; "transitions"/"assemble" only apply when joining separate clips.
- constraints: duration in seconds and aspect ratio (e.g. "9:16") only when stated or unambiguous ("for Reels" = 9:16);
  exclusions = things the user does not want.
- conflicts: requirements that contradict each other (e.g. "one continuous shot" + "five different scenes").
- Cooking / recipe videos: a recipe video is a multi_scene "realistic" film. If the dish itself cannot be identified
  ("a cooking video", "make something tasty"), ask which dish (missing_info, blocking, affects "content", 3-4
  appealing options). Never ask about ingredients, quantities or steps of a known dish: the production works them out.
- missing_info: ask ONLY when the answer changes which kind of production is needed or the task is impossible
  without it. Use sensible defaults for everything else and list them in assumptions. Give 2-4 short answer options.
  Set "affects": "routing" if the answer changes the kind of production/application, or "content" if it is only a
  detail the production will need (exact numbers, names, wording).
- refers_to_uploads: true if the user talks about files they uploaded/attached ("my uploaded images", "this video"),
  whether or not any file actually arrived.
- suggested_capabilities: capability ids from the vocabulary you think are needed (used only as a cross-check).
- evidence: for each important field, the quote from the prompt or the asset finding that supports it.

Allowed values:
detected_intent: {schemas.INTENTS}
production_type (primary first): {schemas.PRODUCTION_TYPES}
asset role: {schemas.ASSET_ROLES}
asset use: {schemas.ASSET_USES}
motion.programmatic_elements: {schemas.PROGRAMMATIC_ELEMENTS}
editing.operations: {schemas.EDIT_OPERATIONS}

Return JSON only:
{{"detected_intent": "...", "user_goal": "one sentence", "requested_output": "e.g. 15s vertical product video",
 "production_type": ["..."],
 "asset_roles": [{{"asset_id": "a1", "role": "...", "use": "...", "note": "why"}}],
 "scene": {{"shot_structure": "...", "scene_count": null, "narrative": false, "consistency": ["what must stay the same"]}},
 "visual": {{"style": "", "lighting": "", "camera": [""], "composition": ""}},
 "motion": {{"kinds": [], "programmatic_elements": [], "integration": "unspecified", "overlay_elements": [], "details": []}},
 "audio": {{"voiceover": "unspecified", "music": "unspecified", "subtitles": "unspecified", "sound_effects": "unspecified"}},
 "editing": {{"operations": []}},
 "constraints": {{"duration_s": null, "aspect_ratio": null, "must_include": [], "exclusions": []}},
 "conflicts": [], "assumptions": [],
 "evidence": [{{"field": "scene.shot_structure", "source": "prompt", "quote": "..."}}],
 "suggested_capabilities": [],
 "refers_to_uploads": false,
 "missing_info": [{{"question": "...", "why": "...", "options": ["..", ".."], "blocking": true, "affects": "routing"}}],
 "confidence": 0.0}}"""


def assets_for_llm(assets: dict, perception: dict) -> list[dict]:
    out = []
    for aid, a in assets.items():
        p = {k: v for k, v in (perception.get(aid) or {}).items() if k not in ("metadata", "model") and v}
        out.append({"asset_id": aid, "filename": a.get("filename"), "modality": a["modality"], "perception": p})
    return out


RECHECK = ("Your previous brief (below) leads to requirements that no available application can meet. For each one "
           "listed, read the user's request again. Keep it only if the user's own words clearly ask for it, and quote "
           "those words in evidence. If it came from your own assumption or interpretation, change the brief so it is "
           "no longer required. Never drop anything the user actually asked for, and never change the requested "
           "duration, scenes or exclusions. Return the complete corrected brief in the same JSON format.")


async def build_brief(prompt: str, assets: dict, perception: dict, answers: dict, chat=llm.chat_json,
                      recheck: dict | None = None) -> schemas.Brief:
    """recheck = {"previous_brief": {...}, "unmet_requirements": [...]}: a second look before saying 'not possible'."""
    user = {
        "prompt": prompt or "(no text prompt)",
        "uploaded_assets": assets_for_llm(assets, perception),
        "answers_to_earlier_questions": answers,
        "capability_vocabulary": registry.capabilities(),
    }
    if recheck:
        user["check_again"] = {"instruction": RECHECK, **recheck}
    raw = await chat(config.REASONING_MODEL, [{"role": "system", "content": BRIEF_SYSTEM},
                                              {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
                     stage="brief.recheck" if recheck else "brief")
    brief = schemas.Brief.model_validate(raw if isinstance(raw, dict) else {})
    # Keep only roles for assets that exist; add "unclear" for any the model skipped.
    known = {r.asset_id for r in brief.asset_roles if r.asset_id in assets}
    brief.asset_roles = [r for r in brief.asset_roles if r.asset_id in assets]
    brief.asset_roles += [schemas.AssetRole(asset_id=a) for a in assets if a not in known]
    for i, q in enumerate(brief.missing_info):
        q.id = q.id or f"q{i + 1}"
    _require_segment_evidence(brief, prompt, answers)
    _scene_sized_animation_is_its_own_scene(brief)
    return brief


SCENE_SIZED = {"exploded_view", "product_rotation", "data_visualization"}


def _scene_sized_animation_is_its_own_scene(brief: schemas.Brief) -> None:
    """Structural rule, not a guess: in a multi-scene film with filmed footage, an exploded view, product turntable or
    data animation that is not placed on top of a shot fills its own scene, so the scenes must be joined."""
    m = brief.motion
    if m.integration != "unspecified" or brief.detected_intent not in ("create_new", "animate_asset"):
        return
    own = [e for e in m.programmatic_elements if e in SCENE_SIZED and e not in m.overlay_elements]
    multi = brief.scene.shot_structure == "multi_scene" or (brief.scene.scene_count or 0) > 1
    if own and multi and "realistic" in m.kinds:
        m.integration = "separate_segments"
        brief.assumptions = [a for a in brief.assumptions if "placed on top of the footage" not in a]
        brief.assumptions.append(f"{', '.join(e.replace('_', ' ') for e in own).capitalize()} fills its own scene "
                                 "in the film, so the scenes are joined in story order.")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def quoted_in_prompt(quote: str, prompt: str) -> bool:
    """True when the quote contains a real piece of the user's text (the model may add its own words after an
    ellipsis or dash; those are ignored, but at least one substantial piece must be verbatim)."""
    text = _norm(prompt)
    parts = [_norm(p) for p in re.split(r"\.\.\.|\u2026|\s[\u2014\u2013-]\s", quote)]
    return any(len(p) >= 12 and p in text for p in parts)


def _require_segment_evidence(brief: schemas.Brief, prompt: str, answers: dict) -> None:
    """'Separate segments joined together' changes the route (it needs a joining app), so it must come from the
    user's own words or answers, never from a guess. Without a real quote it becomes 'unspecified'."""
    if brief.motion.integration != "separate_segments" or answers:
        return
    if any(e.field == "motion.integration" and e.source == "prompt" and quoted_in_prompt(e.quote, prompt)
           for e in brief.evidence):
        return
    brief.motion.integration = "unspecified"
    brief.assumptions.append("The programmatic animation is placed on top of the footage; the request does not make it "
                             "its own scene.")
