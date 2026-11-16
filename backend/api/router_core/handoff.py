"""What the brain hands each selected app: a complete production prompt plus the exact request that app accepts.

The prompt is composed from the validated brief (nothing invented), ends with the user's own words so nothing is lost,
and is tailored to the step's job in the plan. The request body follows the app's real first endpoint (see the
registry profile's `invocation`) and clamps settings to that app's real limits.
"""

import re

from . import registry

HANGUL = re.compile(r"[가-힣]")


def _language(prompt: str, brief) -> str:
    text = " ".join([prompt, brief.audio.voiceover or "", " ".join(brief.constraints.must_include)]).lower()
    if HANGUL.search(prompt) or "korean" in text:
        return "ko"
    if "japanese" in text:
        return "ja"
    return "en"


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _nearest(v, options):
    return min(options, key=lambda o: abs(o - v))


def _lines(brief, step: dict, multi: bool) -> list[str]:
    h = lambda s: (s or "").replace("_", " ")
    out = [brief.user_goal.strip() or brief.requested_output.strip()]
    if brief.requested_output and brief.requested_output.strip() != out[0]:
        out.append(f"Deliverable: {brief.requested_output.strip()}")
    sc = brief.scene
    if sc.shot_structure == "multi_scene" or (sc.scene_count or 0) > 1:
        out.append(f"Structure: {sc.scene_count or 'several'} scenes" + (", told as a story" if sc.narrative else "") + ".")
    elif sc.shot_structure == "single_continuous":
        out.append("Structure: one continuous shot.")
    if sc.consistency:
        out.append("Keep consistent across scenes: " + "; ".join(sc.consistency) + ".")
    v = brief.visual
    look = [x for x in [v.style, v.lighting, v.composition] if x]
    if look:
        out.append("Look: " + "; ".join(look) + ".")
    if v.camera:
        out.append("Camera: " + "; ".join(v.camera) + ".")
    m = brief.motion
    if m.programmatic_elements:
        over = set(m.overlay_elements)
        own = [h(e) for e in m.programmatic_elements if e not in over]
        if own:
            out.append("Animated elements: " + ", ".join(own) + ".")
        if over:
            out.append("Graphics on top of the footage: " + ", ".join(h(e) for e in over) + ".")
    if m.details:
        out.append("Scene notes: " + " ".join(d.rstrip(".") + "." for d in m.details))
    a = brief.audio
    sound = [f"{h(k)} {'yes' if v == 'requested' else 'no'}" for k, v in
             (("music", a.music), ("voice-over", a.voiceover), ("sound effects", a.sound_effects), ("subtitles", a.subtitles))
             if v in ("requested", "excluded")]
    if sound:
        out.append("Sound: " + ", ".join(sound) + ".")
    c = brief.constraints
    fmt = [x for x in [f"{c.duration_s:g} seconds" if c.duration_s else "", c.aspect_ratio or ""] if x]
    if fmt:
        out.append("Length and format: " + ", ".join(fmt) + ".")
    if c.must_include:
        out.append("Must include: " + "; ".join(c.must_include) + ".")
    if c.exclusions:
        out.append("Avoid: " + "; ".join(c.exclusions) + ".")
    if multi:
        out.append(f"Your part in this plan: {step['responsibility']}")
    return [x for x in out if x]


def build(step: dict, brief, prompt: str, assets: dict, multi: bool) -> dict:
    app = registry.apps()[step["app_id"]]
    lines = _lines(brief, step, multi)
    own = prompt.strip()
    if own:
        lines.append(f"The user's own words: \"{own}\"")
    app_prompt = "\n".join(lines)
    refs = [{"asset_id": aid, "filename": a.get("filename"), "role": brief.role_of(aid).role}
            for aid, a in assets.items() if a.get("modality") in ("image", "video", "audio")]
    c = brief.constraints
    lang = _language(prompt, brief)
    ar = c.aspect_ratio
    allowed = (app.get("profile", {}).get("aspect_ratios") or {}).get("value") or []
    if ar and allowed and ar not in allowed:
        ar = None  # the router never picks an app that cannot make the requested format; this is a safety net
    aid = app["app_id"]
    inv = app.get("invocation") or {}
    first = (inv.get("chain") or ["?"])[0]
    notes: list[str] = []
    if aid == "pltt-flow-video-2":
        dur = _clamp(c.duration_s or 20, 3, 120)
        body = {"prompt": app_prompt, "total_duration": dur, "aspect_ratio": ar or "16:9",
                "audio": brief.audio.music == "requested" or brief.audio.sound_effects == "requested",
                "scene_count": brief.scene.scene_count, "language": lang,
                "references": [{"index": i, "asset_id": r["asset_id"]} for i, r in enumerate(refs) if r["role"] != "audio_track"]}
    elif aid == "pltt-storyboard-video-maker":
        want = c.duration_s or 30
        dur = int(_clamp(want, 20, 60))
        if dur != want:
            notes.append(f"Storyboard & Video plans 20-60 s, so {want:g} s becomes {dur} s.")
        body = {"prompt": app_prompt, "duration": dur, "aspect_ratio": ar or "9:16",
                "scenes": brief.scene.scene_count,
                "references": [{"index": i, "asset_id": r["asset_id"]} for i, r in enumerate(refs) if r["role"] != "audio_track"],
                "audio_mode": ("voiceover_music" if brief.audio.voiceover == "requested" else
                               "music" if brief.audio.music == "requested" else "generated")}
    elif aid == "palette-video-smart":
        want = c.duration_s or 30
        dur = _nearest(want, [20, 30, 40, 50, 60, 90])
        if dur != want:
            notes.append(f"Palette Video Smart makes 20/30/40/50/60/90 s films, so {want:g} s becomes {dur} s.")
        body = {"brief": app_prompt[:4000], "source_ids": [r["asset_id"] for r in refs if r["role"] != "style_reference"],
                "reference_ids": [r["asset_id"] for r in refs if r["role"] == "style_reference"],
                "settings": {"duration": dur, "aspect": ar or "9:16", "language": lang,
                             "outputs": ("narration_captions" if brief.audio.voiceover == "requested" else
                                         "captions" if brief.audio.subtitles == "requested" else "both")}}
    elif aid == "palette-presenter-studio":
        dur = _clamp(c.duration_s or 0, 0, 180)
        if c.duration_s and dur != c.duration_s:
            notes.append(f"Presenter Studio makes up to 180 s, so {c.duration_s:g} s becomes {dur:g} s.")
        body = {"prompt": app_prompt, "duration": dur or None, "duration_given": bool(c.duration_s),
                "aspect_ratio": ar, "language": lang, "production_type": list(brief.production_type or [])}
    elif aid == "palette-effects-studio":
        body = {"prompt": app_prompt, "language": lang}
    elif aid == "palette-motion-studio":
        dur = _clamp(c.duration_s or 10, 2, 60)
        body = {"prompt": app_prompt, "duration": dur, "aspect_ratio": ar or "16:9", "language": lang}
    else:  # an app without a known request shape: the prompt is still the hand-off
        body = {"prompt": app_prompt}
    if aid in ("pltt-flow-video-2", "pltt-storyboard-video-maker"):
        # The engines pick the genre's length when the user gave none, and its story playbook from these.
        body["duration_given"] = bool(c.duration_s)
        body["production_type"] = list(brief.production_type or [])
    return {"app_id": aid, "app_name": app["name"], "prompt": app_prompt, "language": lang,
            "endpoint": f"{inv.get('base', '')} {first}".strip(), "request": body, "notes": notes}


def attach(plan: dict, brief, prompt: str, assets: dict) -> None:
    multi = len(plan["steps"]) > 1
    for step in plan["steps"]:
        step["handoff"] = build(step, brief, prompt, assets, multi)
