"""Phase 6: fallback classification (equivalent / changes_result / invalid), consent, no silent downgrade."""

import copy

from router_core import fallback, registry
from router_core.requirements import compile_brief
from router_core.schemas import Brief
from tests.helpers import step


def run_for(brief, steps, assets=None, mode="normal"):
    b = Brief.model_validate(brief)
    comp = compile_brief(b, assets or {})
    return {"mode": mode, "assets": assets or {}, "plan": {"steps": steps},
            "understanding": {"brief": b.model_dump(), "subtasks": comp["subtasks"]}}


def by_app(opts):
    return {o["app_id"]: o for o in opts}


def test_single_clip_fallback_to_storyboard_changes_result():
    run = run_for({"detected_intent": "create_new"},
                  [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"])])
    o = by_app(fallback.fallback_options(run, "s1"))
    sb = o["storyboard-studio"]
    assert sb["classification"] == "changes_result"
    assert any("multi scene" in c for c in sb["changes"])
    assert o["programmatic-motion-studio"]["classification"] == "invalid"


def test_explicit_single_shot_makes_storyboard_invalid():
    run = run_for({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous"}},
                  [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"])])
    assert by_app(fallback.fallback_options(run, "s1"))["storyboard-studio"]["classification"] == "invalid"


def test_exploded_view_has_no_valid_fallback():
    run = run_for({"detected_intent": "create_new", "motion": {"kinds": ["programmatic_precise"], "programmatic_elements": ["exploded_view"]}},
                  [step("s1", "programmatic-motion-studio", ["motion.exploded_view"], [("prompt", "text")], subtasks=["t_motion"])])
    assert all(o["classification"] == "invalid" for o in fallback.fallback_options(run, "s1"))


def test_unavailable_app_is_invalid_fallback(monkeypatch):
    apps = copy.deepcopy(registry.apps())
    apps["storyboard-studio"]["status"] = "offline"
    monkeypatch.setattr(registry, "apps", lambda: apps)
    run = run_for({"detected_intent": "create_new"},
                  [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"])])
    o = by_app(fallback.fallback_options(run, "s1"))["storyboard-studio"]
    assert o["classification"] == "invalid" and "offline" in o["reason"]


def test_substitute_keeps_inputs_and_reverts_when_invalid():
    run = run_for({"detected_intent": "create_new", "motion": {"kinds": ["programmatic_precise"], "programmatic_elements": ["exploded_view"]}},
                  [step("s1", "programmatic-motion-studio", ["motion.exploded_view"], [("prompt", "text")], subtasks=["t_motion"])])
    before = copy.deepcopy(run["plan"])
    fallback.substitute(run, "s1", "normal-video-generator")
    assert run["plan"] == before


def test_compare_apps_reports_confirmed_to_unverified_downgrade():
    apps = registry.apps()
    ch = fallback.compare_apps(apps["storyboard-studio"], apps["normal-video-generator"], ["video.text_to_video"])
    assert any("confirmed for Storyboard Studio but not yet" in c for c in ch)


def _direct_run(app_id, request):
    """A written film (story_intel/direct.py): no brief, the approved story's request on the handoff."""
    return {"mode": "auto", "assets": {}, "prompt": "an explainer", "plan": {"steps": [
                {**step("s1", app_id, ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"]),
                 "handoff": {"app_id": app_id, "prompt": "an explainer", "request": request}}]},
            "understanding": {"direct": True, "constraints": {"duration_s": 30, "aspect_ratio": "9:16"},
                              "subtasks": [{"id": "t_generate", "required_capabilities": ["video.text_to_video"]}]}}


def test_retarget_keeps_the_approved_length_and_format():
    run = _direct_run("normal-video-generator", {"prompt": "an explainer", "duration": 30, "total_duration": 30,
                                                 "duration_given": True, "aspect_ratio": "9:16"})
    tstep = run["plan"]["steps"][0]
    old = tstep["handoff"]
    tstep["app_id"] = "storyboard-studio"
    req = fallback.retarget(run, tstep, old)["request"]
    assert req["duration"] == req["total_duration"] == 30 and req["duration_given"] is True
    assert req["aspect_ratio"] == "9:16" and req["prompt"] == "an explainer"


def test_retarget_without_a_handoff_uses_the_plans_length():
    run = _direct_run("storyboard-studio", {})
    tstep = run["plan"]["steps"][0]
    new = fallback.retarget(run, tstep, None)
    assert new["app_id"] == "storyboard-studio"
    assert new["request"]["duration"] == new["request"]["total_duration"] == 30
    assert new["request"]["duration_given"] is True
