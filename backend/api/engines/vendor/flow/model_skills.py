# Vendored from palette-lab/pltt-os-apps-rinsan flow_video/backend/api/core/model_skills.py (v0.0.35), imports adjusted only.
"""Per-model prompting skills: how each video and image model wants to be prompted.

Researched from the providers' own guides and API schemas (2026-11); every entry lists its sources. The story
planner folds the chosen models' skills into its system prompt (`planner_brief`), the motion-prompt writer writes
in the clip model's own format (`motion_brief`), and the request layer enforces the hard parts — length limits, the
negative-prompt field and Kling's native multi-shot `multi_prompt` (`finalize_motion`, `split_shots`, `fit_prompt`).

Keyword policy: `required` holds only phrases a model's guide asks for; `friendly` holds phrasing the model is known
to follow. Everything else stays generic (see `GENERIC_KEYWORDS`).

Leaf module: it imports nothing from the app, so `fal_helper`, `catalog` users and the routes can all use it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Matches catalog.DEFAULT_VIDEO_MODEL (a test keeps them equal).
DEFAULT_VIDEO_MODEL = "kling_3_0_standard"
DEFAULT_IMAGE_MODEL = "nano_banana_pro"

# Room the frontend needs after a written prompt (the start / end frame continuity sentences).
FRAME_HEADROOM = 300
NEGATIVE_MAX = 2500  # Kling's negative_prompt limit (fal schema maxLength)

# Generic phrasing every model here understands.
GENERIC_KEYWORDS = {
    "shot size": ["wide", "medium", "close-up", "macro close-up"],
    "angle": ["eye-level", "low angle", "high angle", "top-down"],
    "movement": ["static", "push-in", "pull-out", "dolly", "truck", "pan", "tilt", "orbit", "crane", "tracking",
                 "handheld"],
    "lens": ["35mm", "85mm", "shallow depth of field"],
    "light": ["soft key light", "rim light", "backlight", "golden hour", "high-key", "low-key"],
    "motion": ["smooth", "steady", "natural motion", "realistic physics"],
}

_KLING_SOURCES = (
    "https://fal.ai/models/fal-ai/kling-video/v3/pro/image-to-video/api",
    "https://blog.fal.ai/kling-3-0-prompting-guide/",
    "https://kling.ai/blog/kling-video-3-multi-shot-guide",
    "https://app.klingai.com/global/dev/document-api/apiReference/updateNotice",
)
_KLING_CAMERA = ("macro close-up", "profile shot", "over-the-shoulder", "tracking shot", "POV", "push-in", "pull-out",
                 "orbit", "dolly", "crane up", "pan", "tilt", "handheld", "shot-reverse-shot")
_KLING_DIALOGUE = ("Write the action first, then tag the line with its speaker and tone, e.g. "
                   "[Character A: Mara, warm and calm]: \"Every morning starts here.\" Use unique, consistent character "
                   "names, never pronouns.")


def _kling_3(name: str, negative: bool) -> dict:
    return {
        "family": "kling",
        "name": name,
        "strengths": ["realistic motion and physics", "stable identity for people, products and places",
                      "cinematic camera language", "native multi-shot: up to 6 shots inside one clip",
                      "native sound with lip-synced dialogue", "keeps text and labels that are in the start frame"],
        "avoid": ["several actions or camera moves inside one shot", "vague motion words",
                  "pronouns instead of character names in dialogue"],
        "structure": ("Name the subject(s) first, with the same names every time, then the action, the camera move, "
                      "the light and atmosphere, then the sound. The start frame is the anchor: describe how the scene "
                      "evolves from it (movement, camera, environment), not what it already shows."),
        "multi_shot": "multi_prompt",
        "max_shots": 6,
        "int_durations": False,
        "prompt_max": 2500,
        "prompt_budget": 1800,
        "shot_prompt_max": None,
        "negative_prompt": negative,
        "negatives": ["morphing", "warped hands", "extra fingers", "flicker", "jitter", "subtitles", "watermark"],
        "dialogue": _KLING_DIALOGUE,
        "languages": "English or Chinese (other languages are spoken in English)",
        "camera_terms": _KLING_CAMERA,
        "required": [],
        "single_shot_lead": "",
        "sound_tail": "",
        "friendly": ["smooth, stable camera", "natural motion", "realistic physics", "consistent identity"],
        "sources": _KLING_SOURCES,
    }


_SEEDANCE_DIALOGUE = ("Name the speaker and the language before the line, in quotes, e.g. the woman says in Korean: "
                      "\"…\"; keep one language per line and say who stays silent.")
# The guide's own constraint phrases (checked one by one, so a model that wrote one doesn't get it twice).
_SEEDANCE_CONSTRAINTS = ["Keep it subtitle-free.", "Do not generate a watermark."]
_SEEDANCE_CAMERA = ("wide shot", "medium shot", "close-up", "macro close-up", "slow push-in", "pull-out",
                    "smooth lateral tracking", "fixed shot", "orbit", "slow 360° rotation around the subject",
                    "crane", "handheld")

VIDEO_SKILLS: dict[str, dict[str, Any]] = {
    "kling_3_0_standard": _kling_3("Kling 3.0 Standard", negative=True),
    "kling_3_0_pro": _kling_3("Kling 3.0 Pro", negative=True),
    # Kling's unified "Omni" model: the same prompting, but its endpoints take no negative_prompt / cfg_scale.
    "kling_o3_pro": {
        **_kling_3("Kling O3 Pro", negative=False),
        "sources": _KLING_SOURCES + ("https://fal.ai/models/fal-ai/kling-video/o3/pro/image-to-video/api",
                                     "https://fal.ai/models/fal-ai/kling-video/o3/pro/reference-to-video/api"),
    },
    "kling_4_0": {
        "family": "kling",
        "name": "Kling 4.0",
        "strengths": ["clips up to 30 s with automatic shot pacing", "up to 8 middle keyframes",
                      "stereo sound with lip-synced dialogue in 9 languages", "renders text and logos",
                      "long, detailed prompts"],
        "avoid": ["negative instructions (only 'no subtitles' and 'no background music' are safe)",
                  "abstract quality words such as 'premium look' or 'vintage feel'; describe the visible facts instead"],
        "structure": ("Lead with the camera. Then the overall setting (scene, textures, visual style, camera tone), then "
                      "the events in order (what is seen, the action, the emotion), then the audio. Use concrete visual "
                      "facts: light direction, materials, depth of field."),
        "multi_shot": "multi_prompt",
        "max_shots": 6,
        "int_durations": True,
        "prompt_max": None,  # 8,000 tokens
        "prompt_budget": 2400,
        "shot_prompt_max": 512,  # fal KlingV4Shot.prompt maxLength
        "negative_prompt": False,
        "negatives": [],
        "dialogue": ("Write each line in quotes in the language it is spoken in, with the speaker's age, tone and "
                     "emotion, e.g. a woman in her thirties says softly in Korean: \"…\"."),
        "languages": "Korean, English, Chinese, Japanese, Spanish, Portuguese, German, French or Hindi",
        "camera_terms": ("tracking shot", "lateral tracking", "low-angle shot", "wide shot", "medium shot", "close-up",
                         "push-in", "pull-out", "pan", "orbit", "handheld"),
        # Kling 4.0 splits a clip into shots on its own unless it is asked for one continuous shot.
        "single_shot_lead": "One continuous shot.",
        "required": ["No subtitles."],
        "sound_tail": "No background music.",
        "friendly": ["concrete light direction", "visible textures", "depth of field described plainly"],
        "sources": ("https://kling.ai/quickstart/kling-4-model-user-guide",
                    "https://fal.ai/learn/devs/kling-4-prompting-guide",
                    "https://fal.ai/models/kling/v4/image-to-video/api"),
    },
    "seedance_2_0": {
        "family": "seedance",
        "name": "Seedance 2.0",
        "strengths": ["multi-subject scenes and interactions", "strong grasp of standard camera terms",
                      "showcase moves around a subject (close-up, slow 360° rotation)", "native sound with dialogue",
                      "reference-image mode (@Image1, @Image2…)"],
        "avoid": ["more than one camera movement per shot", "precise per-shot timings such as 0–3s (unstable)",
                  "more than 4 characters", "mixing languages within dialogue",
                  "redundant or conflicting descriptions"],
        "structure": ("Precise subject + action details + scene/environment + lighting and colour tone + camera "
                      "movement + visual style + image quality + constraints. Keep it concise, with no redundancy and "
                      "no conflicts."),
        "multi_shot": "shots",
        "max_shots": 3,
        "int_durations": False,
        "prompt_max": None,
        "prompt_budget": 1500,
        "shot_prompt_max": None,
        "negative_prompt": False,
        "negatives": [],
        "dialogue": _SEEDANCE_DIALOGUE,
        "languages": "the language named before each line (keep one per line)",
        "camera_terms": _SEEDANCE_CAMERA,
        "single_shot_lead": "",
        "required": list(_SEEDANCE_CONSTRAINTS),
        "sound_tail": "",
        "friendly": ["one camera movement per shot", "stable framing", "coherent motion"],
        "sources": ("https://ai.byteplus.com/ark/region:ap-southeast-1/docs/seedance-2-0-prompt-guide",
                    "https://fal.ai/models/bytedance/seedance-2.0/reference-to-video/api"),
    },
    "seedance_2_5": {
        "family": "seedance",
        "name": "Seedance 2.5",
        "strengths": ["long one-take clips (up to 30 s) without drift", "follows second-level timestamps",
                      "first and last frame control", "native sound with lip-synced dialogue in 10+ languages",
                      "reference mode with up to 30 images (@Image1…)"],
        "avoid": ["cuts inside a clip (it is built for one continuous take)", "objects teleporting or duplicating",
                  "slow motion unless asked", "repeated actions or entrances"],
        "structure": ("A compact brief: the starting state (where everything is in the first frame), the action as a "
                      "timeline, the camera path and framing (and what triggers each move), what must stay the same, "
                      "the audio, then the ending state (the final composition, nothing new after it) and the "
                      "constraints."),
        "multi_shot": "timeline",
        "max_shots": 3,
        "int_durations": False,
        "prompt_max": None,
        "prompt_budget": 1800,
        "shot_prompt_max": None,
        "negative_prompt": False,
        "negatives": [],
        "dialogue": _SEEDANCE_DIALOGUE + " Ask for clean lip-synced speech.",
        "languages": "Korean, English, Chinese, Japanese, Spanish and more (name the language before each line)",
        "camera_terms": _SEEDANCE_CAMERA,
        "single_shot_lead": "",
        "required": list(_SEEDANCE_CONSTRAINTS),
        "sound_tail": "",
        "friendly": ["one continuous take", "the camera moves only after the action that triggers it",
                     "keep the subject on the same side of frame"],
        "sources": ("https://fal.ai/learn/devs/seedance-2-5-prompting-guide",
                    "https://fal.ai/models/bytedance/seedance-2.5/image-to-video/api",
                    "https://fal.ai/models/bytedance/seedance-2.5/reference-to-video/api"),
    },
    "ltx_video": {
        "family": "ltx",
        "name": "LTX Video",
        "strengths": ["fast drafts", "simple, continuous motion"],
        "avoid": ["shot lists or cuts", "complex multi-step action", "dialogue"],
        "structure": ("ONE flowing paragraph in the present tense: establish the shot (shot size, genre look), set the "
                      "scene (light, palette, textures, atmosphere), the action as a natural sequence with active verbs "
                      "(walks, turns, exhales, reaches), the characters (age, hair, clothing, emotion shown through the "
                      "body), then the camera move relative to the subject and how the subject appears once the move "
                      "lands. Match the detail to the shot size."),
        "multi_shot": "none",
        "max_shots": 1,
        "int_durations": False,
        "prompt_max": None,
        "prompt_budget": 1000,
        "shot_prompt_max": None,
        "negative_prompt": True,
        "negatives": ["morphing", "warped hands", "flicker", "jitter", "subtitles", "watermark"],
        "dialogue": "",
        "languages": "",
        "camera_terms": ("wide shot", "medium shot", "close-up", "slow push-in", "pull-out", "pan", "tilt", "orbit",
                         "tracking shot", "handheld"),
        "single_shot_lead": "",
        "required": [],
        "sound_tail": "",
        "friendly": ["active verbs", "present tense"],
        "sources": ("https://docs.ltx.io/api-documentation/implementation-guides/prompting-guide",),
    },
}

IMAGE_SKILLS: dict[str, dict[str, Any]] = {
    "nano_banana_pro": {
        "name": "Nano Banana Pro",
        "style": ("natural, specific sentences in this order: the subject (exact identity), the composition (shot "
                  "size, angle), the action frozen mid-moment, the location, the style; then camera and light (e.g. "
                  "golden-hour backlight, shallow depth of field). Quote any text that must appear (text that is in a "
                  "reference) exactly, with its typeface and placement; it renders text well but small text is "
                  "fragile, so keep such text large enough to read."),
        "sources": ("https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/",),
    },
    "openai/gpt-image-2.5": {
        "name": "GPT Image 2.5",
        "style": ("scene/background, then subject, then key details, then constraints — short labelled segments are "
                  "fine. Say what the image is for (e.g. a cinematic film still, a product advertisement photo, a travel "
                  "photo). Preserve the geometry and label legibility of referenced objects exactly; put literal text "
                  "in quotes. Prefer plain visual facts over 'studio polish' words for photoreal looks."),
        "sources": ("https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide",),
    },
    "midjourney": {
        "name": "Midjourney",
        "style": ("concise: about 60 words or fewer — subject, setting, light, lens and style as one descriptive "
                  "sentence or a few short phrases; no rule lists, no negations (the app adds the aspect ratio)."),
        "sources": ("https://docs.midjourney.com/hc/en-us/articles/32859204029709-Parameter-List",),
    },
    "flux_schnell": {
        "name": "Flux",
        "style": "a plain natural-language description of subject, setting, light and style, with no negative phrasing.",
        "sources": (),
    },
}


def video_skill(model_id: Optional[str]) -> dict[str, Any]:
    """The skill of a catalog video model; unknown ids get the default model's, like `catalog.video_model`."""
    return VIDEO_SKILLS.get(model_id or "") or VIDEO_SKILLS[DEFAULT_VIDEO_MODEL]


def image_skill(model_id: Optional[str]) -> dict[str, Any]:
    """The skill of a resolved image model id (see `catalog.resolve_image_model`)."""
    return IMAGE_SKILLS.get(model_id or "") or IMAGE_SKILLS[DEFAULT_IMAGE_MODEL]


# --- Planner ---------------------------------------------------------------------------------------------------

_PLAN_MULTI_SHOT = {
    "multi_prompt": "It cuts natively between up to {n} shots inside one clip, so a scene may be covered by 2–3 setups.",
    "shots": "It can cut between a few shots inside one clip, but one continuous move is steadier.",
    "timeline": "It is strongest as one continuous take; several beats in one clip work when they happen in one shot.",
    "none": "One continuous shot per clip; no cuts inside a clip.",
}


def planner_brief(video: dict[str, Any], image_id: Optional[str], audio: bool) -> str:
    """The planner's ENGINES block: what the clip model is good and bad at, and how to write stills for the image
    model. `video` is the catalog entry (`catalog.video_model`)."""
    skill, image = video_skill(video.get("id")), image_skill(image_id)
    durations = video.get("durations") or [5]
    lines = [
        "",
        "",
        "ENGINES (the models that render this film; plan for them):",
        f"- Clips: {skill['name']}. Strong at: {'; '.join(skill['strengths'])}. "
        f"Weak at / avoid: {'; '.join(skill['avoid'])}.",
        f"  Clip length {min(durations)}–{max(durations)} s. "
        + _PLAN_MULTI_SHOT[skill["multi_shot"]].format(n=skill["max_shots"])
        + (" A clip can also be given an end frame." if video.get("end_frame") else ""),
    ]
    if audio and video.get("audio") and skill["languages"]:
        lines.append(f"  Native sound: each clip's own audio comes from its prompt, so fill \"sound\". On-camera lines "
                     f"only when the brief asks for them; it speaks {skill['languages']}.")
    elif audio and video.get("audio"):
        lines.append("  Native sound: each clip's own audio comes from its prompt, so fill \"sound\".")
    else:
        lines.append("  Sound is off for the clips: leave every \"sound\" empty.")
    lines.append("- Camera words every clip model follows: "
                 + "; ".join(f"{k}: {', '.join(v)}" for k, v in GENERIC_KEYWORDS.items()) + ".")
    lines.append(f"- Stills (each scene's opening frame): {image['name']}. Write every image_prompt and the "
                 f"master_prompt for it: {image['style']}")
    lines.append("Plan scenes that play to the clip model's strengths and stay away from its weak spots.")
    return "\n".join(lines)


# --- Motion-prompt writer ---------------------------------------------------------------------------------------

_SHOT_EXAMPLE = "[Shot 1, 3s: wide establishing shot] what happens and how the camera moves"


def _format_rule(skill: dict[str, Any], mode: str) -> str:
    lead = skill["single_shot_lead"]
    lead_rule = f" Begin every single-shot prompt with \"{lead}\"" if lead else ""
    if mode == "bridge":
        return ("- FORMAT (Flow): the film plays as ONE continuous shot. Write each clip as a single continuous shot "
                "with no cuts. IMPORTANT: each clip is generated with the NEXT scene's opening frame as its ending "
                "keyframe (attached when available). Choreograph the motion so the shot naturally ARRIVES at that "
                "composition by its end (the last scene simply resolves its own action). Describe the journey between "
                "the two frames, not a static loop." + lead_rule)
    if mode == "chain":
        return ("- FORMAT (Flow): the film plays as ONE continuous shot. Write each clip as a single continuous shot "
                "with no cuts that carries on the same action and camera from where the previous clip ended."
                + lead_rule)
    single = ("- FORMAT: follow each scene's camera plan. A single camera move, and any hero, detail, reveal or "
              "closing beat where a subject must stay exactly identical, is ONE continuous shot written as one "
              "passage." + lead_rule)
    kind = skill["multi_shot"]
    if kind == "multi_prompt":
        return (single + f"\n- When the camera plan lists several setups, write them as 2–{skill['max_shots']} shot "
                f"blocks, one per line, exactly in this form: {_SHOT_EXAMPLE}. The seconds are whole numbers that add "
                "up EXACTLY to the scene's duration. Give each shot ONE visible action and ONE camera intent, and keep "
                "the people, objects, setting and light identical across shots; only the camera changes. "
                "You may put ONE short sentence before the first block naming the subject(s) consistently.")
    if kind == "shots":
        return (single + "\n- When the camera plan lists several setups, write 2–3 shots in order as \"Shot 1: …\", "
                "\"Shot 2: …\", with no per-shot timings and ONE camera movement per shot.")
    if kind == "timeline":
        return (single + "\n- When several beats happen in the clip, keep ONE continuous take and write a timeline "
                "of whole-second blocks that cover the scene's duration exactly (\"0–3s: …\", \"3–6s: …\"), then the "
                "final composition, with nothing new after it. Only when the camera plan explicitly asks for different "
                "setups, write \"Shot 1: …\", \"Shot 2: …\" instead.")
    return ("- FORMAT: always ONE flowing paragraph, with no shot list and no cuts; follow the main move of the "
            "scene's camera plan.")


def motion_brief(model_id: str, mode: str, sound: bool) -> str:
    """The clip model's writing rules for the motion-prompt writer: format for the continuity mode, syntax, camera
    words, sound and length."""
    skill = video_skill(model_id)
    parts = [
        "",
        "",
        f"TARGET MODEL: {skill['name']}.",
        f"- Structure: {skill['structure']}",
        _format_rule(skill, mode),
        f"- Camera words it follows well: {', '.join(skill['camera_terms'])}.",
        f"- Avoid: {'; '.join(skill['avoid'])}.",
    ]
    if skill["friendly"]:
        parts.append(f"- Phrasing that works well for it: {', '.join(skill['friendly'])}.")
    if sound:
        parts.append("- SOUND: this clip's audio is generated from the prompt's wording. Describe what is heard, tied "
                     "to the action (ambience, foley, distinct effects), following the scene's sound notes. Never "
                     "music: the film's music is added in the edit.")
        if skill["dialogue"]:
            parts.append(f"- Dialogue (only when a scene's sound notes include a line): {skill['dialogue']}")
    else:
        parts.append("- SOUND: this clip renders with NO audio track. Do not describe any sound, dialogue, music or "
                     "sound effect; keep the prompt purely visual.")
    length = f"- LENGTH: keep each video_prompt under {skill['prompt_budget']} characters"
    if skill["shot_prompt_max"]:
        length += f", and each shot block under {skill['shot_prompt_max'] - 60} characters"
    parts.append(length + ".")
    return "\n".join(parts)


def fallback_motion(camera: Optional[str] = None) -> str:
    """A safe motion prompt when the writer couldn't answer: the planned camera, natural motion, no re-description."""
    move = (camera or "").strip().rstrip(".") or "A slow, steady push-in"
    return f"{move}. The scene comes to life with natural, subtle motion; the camera stays smooth and stable."


# --- Enforcement ------------------------------------------------------------------------------------------------

_SHOT_BLOCK = re.compile(
    r"\[\s*shot\s*(\d+)\s*(?:[,·–-]\s*(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?\s*)?(?::\s*([^\]]*))?\]",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"^(?:no|never|without|not)\s+", re.IGNORECASE)
_NEVER_TAIL = re.compile(r"\s*(?:is |are )?(?:never|not) (?:visible|shown|seen)\.?$", re.IGNORECASE)
# Never put text or logos into a negative prompt: a referenced subject in the same frame may carry them (a product
# label, a sign, a print).
_MARK_WORDS = re.compile(r"\b(?:logo|logos|label|labels|text|letter|letters|lettering|print|prints|printed|brand"
                         r"|wordmark|writing|words?)\b", re.IGNORECASE)


def trim_text(text: str, limit: int) -> str:
    """`text` within `limit` characters, cut at the last sentence end (or word) that fits."""
    text = (text or "").strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [cut.rfind(p) for p in (". ", "! ", "? ", "\n")]
    end = max(ends)
    if end >= limit * 0.6:
        return cut[: end + 1].strip()
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).strip()


def fit_durations(raw: list[Any], total: int, lo: int, hi: int) -> list[int]:
    """Whole seconds within [lo, hi] that sum to `total` whenever that is feasible."""
    durations = []
    for d in raw:
        try:
            v = int(round(float(d)))
        except (TypeError, ValueError):
            v = lo
        durations.append(min(max(v, lo), hi))
    guard = 0
    while sum(durations) != total and guard < 400:
        guard += 1
        diff = total - sum(durations)
        if diff > 0:
            i = min(range(len(durations)), key=lambda k: durations[k])
            if durations[i] >= hi:
                break
            durations[i] += 1
        else:
            i = max(range(len(durations)), key=lambda k: durations[k])
            if durations[i] <= lo:
                break
            durations[i] -= 1
    return durations


def _has(text: str, phrase: str) -> bool:
    return phrase.lower().rstrip(".") in text.lower()


def _negative_terms(skill: dict[str, Any], never: Iterable[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in [*skill["negatives"], *never]:
        term = _NEVER_TAIL.sub("", _NEGATION.sub("", str(raw or "").strip())).strip(" .;,")
        if not term or len(term) > 80 or _MARK_WORDS.search(term) or term.lower() in seen:
            continue
        seen.add(term.lower())
        terms.append(term)
    return terms


def finalize_motion(model_id: str, prompt: str, never: Iterable[str] = (), sound: bool = True) -> tuple[str, str]:
    """(prompt, negative_prompt) ready for `model_id`: the model's required phrases added, the length kept within
    its limit (leaving room for the frame sentences the frontend appends), and the continuity sheet's never-items
    as a negative prompt — only for models whose endpoint has that field; everyone else gets ""."""
    skill = video_skill(model_id)
    text = re.sub(r"\s+\n|\n\s+", "\n", (prompt or "").strip().strip('"').strip())
    lead = skill["single_shot_lead"]
    if lead and not _SHOT_BLOCK.search(text) and not _has(text, lead):
        text = f"{lead} {text}"
    tail = [p for p in skill["required"] if not _has(text, p)]
    if sound and skill["sound_tail"] and not _has(text, skill["sound_tail"]):
        tail.append(skill["sound_tail"])
    tail_text = " ".join(tail)
    limit = (skill["prompt_max"] - FRAME_HEADROOM) if skill["prompt_max"] else skill["prompt_budget"] * 2
    text = trim_text(text, limit - (len(tail_text) + 1 if tail_text else 0))
    if tail_text:
        text = f"{text} {tail_text}".strip()
    negative = ""
    if skill["negative_prompt"]:
        negative = trim_text(", ".join(_negative_terms(skill, never)), NEGATIVE_MAX - 500)
    return text, negative


def supports_negative(model_id: str) -> bool:
    skill = VIDEO_SKILLS.get(model_id)
    return bool(skill and skill["negative_prompt"])


def merge_negative(*parts: Optional[str]) -> str:
    """Comma-joined negative terms, de-duplicated and within Kling's 2,500-character limit."""
    terms: list[str] = []
    for part in parts:
        for term in str(part or "").split(","):
            term = term.strip()
            if term and term.lower() not in {t.lower() for t in terms}:
                terms.append(term)
    out = ""
    for term in terms:
        nxt = f"{out}, {term}" if out else term
        if len(nxt) > NEGATIVE_MAX:
            break
        out = nxt
    return out


def split_shots(model_id: str, prompt: Optional[str], duration: Any) -> Optional[list[dict[str, Any]]]:
    """Kling's native multi-shot `multi_prompt` from a prompt written as `[Shot N, Xs: framing] text` blocks
    (2–6 of them): durations fitted to the clip, per-shot length caps. None when the model has no multi_prompt
    or the prompt isn't a valid shot list — it is then sent as a plain prompt."""
    skill = VIDEO_SKILLS.get(model_id)
    if not skill or skill["multi_shot"] != "multi_prompt" or not prompt:
        return None
    blocks = list(_SHOT_BLOCK.finditer(prompt))
    if not 2 <= len(blocks) <= skill["max_shots"]:
        return None
    try:
        total = int(round(float(duration)))
    except (TypeError, ValueError):
        return None
    if total < len(blocks):
        return None
    lead = prompt[: blocks[0].start()].strip()
    shots: list[tuple[Optional[str], str]] = []
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(prompt)
        body = prompt[m.end():end].strip()
        if not body:
            return None
        framing = (m.group(3) or "").strip().rstrip(".")
        shots.append((m.group(2), f"{framing[:1].upper()}{framing[1:]}. {body}" if framing else body))
    n = len(shots)
    seconds = fit_durations([float(d) if d else total / n for d, _ in shots], total, 1, total - (n - 1))
    if sum(seconds) != total:
        return None
    cap = skill["shot_prompt_max"]
    out = []
    for (_, text), sec in zip(shots, seconds):
        shot = f"{lead} {text}" if lead and (not cap or len(lead) + len(text) < cap) else text
        out.append({"prompt": trim_text(shot, cap) if cap else shot,
                    "duration": sec if skill["int_durations"] else str(sec)})
    return out


def fit_prompt(model_id: str, prompt: str) -> str:
    """A hard safety trim to the endpoint's documented prompt limit (also for hand-edited prompts). Models without a
    documented limit, and image models, are left alone."""
    skill = VIDEO_SKILLS.get(model_id)
    limit = skill["prompt_max"] if skill else None
    return trim_text(prompt, limit) if limit and prompt and len(prompt) > limit else prompt
