"""Direct plan: when Story Intelligence or the 3D Motion Graphics Director has already written the whole film (story,
bible, every scene with its keyframe and motion prompt), the app does not send it through understanding -> router ->
judge again. A second reading only second-guesses the approved scenes (e.g. "kinetic typography" in a full-3D plan
made the router demand Motion Studio overlays and refuse). The film engine renders the approved scenes itself.

The plan has the same shape as a routed one (one step, real registry capabilities, a handoff the engine reads), so
approval, validation, execution, cancel, recovery and re-render all work unchanged.
"""

from router_core import registry

FILM_APP = {"flow": "pltt-flow-video-2"}  # the user's own pick; otherwise Storyboard & Video
DEFAULT_APP = "pltt-storyboard-video-maker"
CAPS = ["story.multi_scene_storyboard", "story.character_consistency", "video.from_storyboard"]
# The engine makes its own keyframes; "image_to_video" (which needs the user's image) is claimed only with uploads.
WITH_IMAGES = ["video.image_to_video"]
PRODUCTION = {"children_story": ["animation", "narrative"], "fiction": ["narrative"], "puzzle_mystery": ["narrative"],
              "educational": ["explainer"], "documentary": ["documentary"], "product": ["advertisement"],
              "other": ["narrative"]}


def applies(run: dict) -> bool:
    """A written film, no footage of the user's to edit, and no app mode that needs a different engine."""
    intel = run.get("story_intel") or {}
    if not (intel.get("plan") or {}).get("scenes"):
        return False
    cls = intel.get("classification") or {}
    if "presenter" in (cls.get("category"), cls.get("agent")):
        return False  # a person talking to camera: Presenter Studio (lip-sync) through the router
    if run.get("mode") in ("presenter", "motion", "analyze", "hybrid", "normal"):
        return False  # the user asked for a specific app or for the full analysis
    return not any(a.get("modality") in ("video", "audio") for a in (run.get("assets") or {}).values())


def build(run: dict) -> dict:
    """{"understanding", "plan", "issues"} for a written film."""
    intel = run["story_intel"]
    plan, cls = intel["plan"], intel.get("classification") or {}
    scenes = plan["scenes"]
    app_id = FILM_APP.get(run.get("mode"), DEFAULT_APP)
    app = registry.apps()[app_id]
    total = sum(float(s.get("duration_s") or 0) for s in scenes) or float(cls.get("duration_s") or 45)
    dur = int(round(max(10.0, min(120.0, total))))
    ar = (run.get("settings") or {}).get("aspect_ratio") or intel.get("aspect_ratio") or "9:16"
    narrated = any((s.get("audio") or {}).get("type") == "narration" and (s.get("audio") or {}).get("line")
                   for s in scenes if isinstance(s.get("audio"), dict))
    motion = intel.get("mode") == "motion_graphics"
    production = ["explainer", "animation"] if motion else PRODUCTION.get(cls.get("category"), ["narrative"])
    images = [aid for aid, a in (run.get("assets") or {}).items() if a.get("modality") == "image"]
    caps = CAPS + (WITH_IMAGES if images else [])
    words = run["prompt"].strip()
    brief = (f"{words}\n\nDetailed version of this request (written from the words above; follow it, but the words "
             f"above win wherever they differ):\n{run.get('enhanced_prompt') or ''}")
    what = ("Render the approved full-3D motion graphics film" if motion else "Film the approved story")
    step = {"step_id": "s1", "app_id": app_id, "subtask_ids": ["t_film"], "capabilities_used": caps,
            "responsibility": f"{what}: {len(scenes)} scenes, keyframe images -> AI clips (each AI-checked), "
                              "narration, music and finishing - exactly as planned and approved.",
            "inputs": [{"source": "prompt", "format": "text"}]
                      + [{"source": f"asset:{aid}", "format": "image_file"} for aid in images],
            "outputs": ["video_file"],
            "handoff": {"app_id": app_id, "app_name": app["name"], "prompt": brief,
                        "language": cls.get("language") or "en", "endpoint": "",
                        "request": {"prompt": brief, "duration": dur, "total_duration": dur, "duration_given": True,
                                    "aspect_ratio": ar, "scenes": len(scenes), "production_type": production,
                                    "references": [{"index": i, "asset_id": aid} for i, aid in enumerate(images)],
                                    "audio_mode": "voiceover_music" if narrated else "music"},
                        "notes": []}}
    understanding = {
        "detected_intent": "create_new", "user_goal": plan.get("logline") or cls.get("intent") or words,
        "goal": plan.get("logline") or cls.get("intent") or words,
        "requested_output": f"{dur} s {ar} {'3D motion graphics' if motion else 'film'}: {plan.get('title') or ''}",
        "production_type": production, "assumptions": cls.get("assumptions") or [],
        "scene": {"scene_count": len(scenes), "narrative": True},
        "constraints": {"duration_s": dur, "aspect_ratio": ar},
        "subtasks": [{"id": "t_film", "group": "generate", "description": what, "required_capabilities": caps,
                      "basis": "story_intel"}],
        "direct": True, "confidence": 1.0}
    return {"understanding": understanding,
            "plan": {"feasible": True, "steps": [step], "direct": True}}
