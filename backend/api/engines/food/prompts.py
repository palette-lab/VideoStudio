"""ImagePromptEnhancer + VideoMotionPromptEnhancer: each planned scene -> the ChefVision image JSON and motion JSON.

Built from the scene fields the planner already returned and the recipe's set (no extra LLM call). The image JSON is
what the image model receives (a JSON-structured prompt); the motion JSON is kept in the plan and rendered as an
ordered motion prompt for the clip model, within its prompt length.
"""

import json

IMAGE_REALISM = ["Exactly the objects_in_frame, each once: no extra plates, bowls, lids or utensils",
                 "No floating ingredients or utensils", "No extra or malformed fingers", "No duplicated objects",
                 "No impossible food geometry", "No unexplained ingredient or cookware changes",
                 "No text, labels or watermarks drawn into the picture"]
MOTION_NEGATIVE = ["No floating or teleporting objects", "No ingredients appearing or disappearing",
                   "No extra fingers or fused hands", "No utensil passing through solid food or cookware",
                   "No instant cooking or unexplained state change", "No camera movement that hides the main action"]
MOTION_MAX_CHARS = 2400


def _s(v) -> str:
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v if str(x).strip())
    if isinstance(v, dict):
        return "; ".join(f"{k}: {x}" for k, x in v.items() if str(x).strip())
    return str(v or "").strip()


def image_json(scene: dict, n: int, recipe: dict, aspect_ratio: str) -> dict:
    st = recipe.get("set") or {}
    m = scene.get("motion") if isinstance(scene.get("motion"), dict) else {}
    return {
        "scene_number": n,
        "scene_purpose": _s(scene.get("beat")) or _s(scene.get("recipe_stage")),
        "shot_type": _s(scene.get("camera")),
        "subject": " | ".join(x for x in (_s(scene.get("image_prompt")), "Ingredients: " + _s(scene.get("ingredients")),
                                          "Hands: " + " - ".join(x for x in (_s(scene.get("hands")),  # what they do
                                                                             st.get("hands", "")) if x),  # who they are
                                          "Utensils and cookware: " + _s(scene.get("utensils"))) if x.split(": ")[-1]),
        "objects_in_frame": _s(scene.get("props")),
        "action_moment": _s(m.get("starting_state")) or _s(scene.get("start_state")),
        "food_texture": _s(scene.get("texture")),
        "environment": "; ".join(x for x in (st.get("surface"), st.get("kitchen")) if x),
        "lighting": _s(scene.get("lighting")) or st.get("light", ""),
        "composition": "the action in sharp focus, hands and food clearly placed, natural negative space",
        "continuity": _s(scene.get("continuity")),
        "realism_constraints": IMAGE_REALISM,
        "style": "Photorealistic cinematic UGC food photography",
        "aspect_ratio": aspect_ratio,
    }


def image_prompt_text(scene: dict, n: int, recipe: dict, aspect_ratio: str) -> str:
    return ("Photorealistic food photograph of ONE frozen moment (not a collage, not several frames), following this "
            "JSON specification exactly:\n" + json.dumps(image_json(scene, n, recipe, aspect_ratio), ensure_ascii=False))


def motion_json(scene: dict, n: int) -> dict:
    m = scene.get("motion") if isinstance(scene.get("motion"), dict) else {}
    phys = m.get("physics") if isinstance(m.get("physics"), dict) else {}
    return {
        "scene_number": n,
        "duration_seconds": scene.get("duration"),
        "camera": _s(scene.get("camera")),
        "starting_state": _s(m.get("starting_state")) or _s(scene.get("start_state")),
        "action_sequence": [str(x) for x in (m.get("action_sequence") or []) if str(x).strip()]
        or [_s(scene.get("action"))],
        "physics": {"gravity": _s(phys.get("gravity")) or "natural falling, pouring, dripping and settling",
                    "fluid_or_material_behavior": _s(phys.get("fluid_or_material_behavior")),
                    "contact": _s(phys.get("contact")) or "objects stay supported and touch surfaces naturally"},
        "food_response": _s(m.get("food_response")) or _s(scene.get("texture")),
        "ending_state": _s(m.get("ending_state")) or _s(scene.get("end_state")),
        "continuity_lock": "Preserve ingredients, quantities, cookware, hands, utensils, lighting and background. "
                           + _s(scene.get("continuity")),
        "motion_style": "Natural human movement, realistic timing, no abrupt morphing",
        "negative_constraints": MOTION_NEGATIVE,
    }


def motion_prompt_text(scene: dict, n: int) -> str:
    """The motion JSON as an ordered prompt for the clip model (kept under its prompt length)."""
    mj = motion_json(scene, n)
    steps = " ".join(f"{i}) {a.rstrip('.')}." for i, a in enumerate(mj["action_sequence"], start=1))
    ph = mj["physics"]
    text = (f"Camera: {mj['camera']}. Start: {mj['starting_state']}. Action: {steps} Physics: {ph['gravity']}; "
            f"{ph['fluid_or_material_behavior']}; {ph['contact']}. Food response: {mj['food_response']}. "
            f"End: {mj['ending_state']}. {mj['continuity_lock']} {mj['motion_style']}. "
            + " ".join(f"{x}." for x in MOTION_NEGATIVE))
    return text[:MOTION_MAX_CHARS]
