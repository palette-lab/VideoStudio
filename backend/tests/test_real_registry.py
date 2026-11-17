"""Routing against the real Palette video apps (pltt-os-apps-rinsan: flow_video, storyboard-video, palette_video_smart)."""

import pytest

from router_core import config, pipeline, registry
from tests.conftest import REAL_REGISTRY
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def real_registry(monkeypatch):
    monkeypatch.setattr(config, "REGISTRY_DIR", REAL_REGISTRY)
    monkeypatch.setattr(config, "ASK_POLICY", "uploads")
    registry.apps.cache_clear(); registry.capabilities.cache_clear()
    yield
    registry.apps.cache_clear(); registry.capabilities.cache_clear()


async def plan(brief, prompt="x", mode="hybrid", assets=None):
    res = await pipeline.analyze(prompt=prompt, mode=mode, assets=assets or {}, perception={}, answers={},
                                 chat=FakeLLM(brief))
    return res, [s["app_id"] for s in (res["plan"] or {}).get("steps", [])]


def test_real_registry_has_exactly_the_three_apps():
    assert set(registry.apps()) == {"pltt-flow-video-2", "pltt-storyboard-video-maker", "palette-video-smart",
                                    "palette-motion-studio", "palette-effects-studio", "palette-presenter-studio",
                                    "explainroo-document-explainer"}


async def test_single_clip_goes_to_flow_video():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "scene": {"shot_structure": "single_continuous"}})
    assert apps == ["pltt-flow-video-2"]


async def test_multi_scene_story_has_a_real_storyboard_app():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["storytelling"],
                            "scene": {"shot_structure": "multi_scene", "consistency": ["same girl"]},
                            "constraints": {"duration_s": 40, "aspect_ratio": "9:16"}})
    assert res["status"] == "awaiting_approval" and apps[0] in {"pltt-flow-video-2", "pltt-storyboard-video-maker"}


async def test_long_film_rules_out_the_60s_app():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "scene": {"shot_structure": "multi_scene"},
                            "constraints": {"duration_s": 100}})
    assert apps == ["pltt-flow-video-2"]


async def test_charts_and_kinetic_text_go_to_motion_studio_not_the_disabled_app():
    assert registry.apps()["palette-video-smart"]["status"] == "disabled"
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["explainer"],
                            "scene": {"shot_structure": "multi_scene"},
                            "motion": {"kinds": ["programmatic_precise"], "programmatic_elements": ["data_visualization", "kinetic_text"],
                                       "integration": "standalone"}})
    assert apps == ["palette-motion-studio", "palette-effects-studio"]  # an explainer gets finishing effects


async def test_exploded_view_goes_to_motion_studio():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "motion": {"kinds": ["programmatic_precise"], "programmatic_elements": ["exploded_view"],
                                       "integration": "standalone"}})
    assert apps == ["palette-motion-studio"]


async def test_story_ad_with_animated_scene_and_slogan_is_a_hybrid_of_real_apps():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["commercial"],
                            "scene": {"shot_structure": "multi_scene", "scene_count": 4},
                            "constraints": {"duration_s": 40},
                            "motion": {"kinds": ["realistic", "programmatic_precise"], "programmatic_elements": ["exploded_view", "kinetic_text"],
                                       "integration": "separate_segments", "overlay_elements": ["kinetic_text"]},
                            "evidence": [{"field": "motion.integration", "source": "prompt", "quote": "an exploded-view scene"}]},
                           prompt="Car ad in 4 scenes with an exploded-view scene and the slogan over the last shot")
    assert res["status"] == "awaiting_approval"
    assert "palette-motion-studio" in apps and apps[0] in {"pltt-flow-video-2", "pltt-storyboard-video-maker"}
    assert all(a != "palette-video-smart" for a in apps)


async def test_fashion_film_needs_the_garment_photo():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["fashion"],
                            "scene": {"shot_structure": "multi_scene"}}, prompt="hoodie campaign")
    assert res["status"] in {"awaiting_approval", "needs_clarification"}
    if apps:
        assert apps == ["pltt-flow-video-2"]


async def test_each_step_gets_a_prompt_and_request_for_its_app():
    brief = {"detected_intent": "create_new", "confidence": 0.9, "user_goal": "A cosy story about a girl finding a lost cat in Seoul.",
             "scene": {"shot_structure": "multi_scene", "scene_count": 6, "consistency": ["same girl"]},
             "audio": {"music": "requested"}, "constraints": {"duration_s": 75, "aspect_ratio": "9:16", "exclusions": ["no text on screen"]}}
    res, apps = await plan(brief, prompt="40s reels story, girl finds lost cat")
    h = res["plan"]["steps"][0]["handoff"]
    assert h["app_id"] == apps[0] and "girl finding a lost cat" in h["prompt"]
    assert "Keep consistent across scenes: same girl." in h["prompt"] and "Avoid: no text on screen." in h["prompt"]
    assert "The user's own words" in h["prompt"]
    if apps[0] == "pltt-flow-video-2":
        assert h["request"]["total_duration"] == 75 and h["request"]["aspect_ratio"] == "9:16"
    if apps[0] == "pltt-storyboard-video-maker":  # 75 s is outside its 20-60 s range: clamped and explained
        assert h["request"]["duration"] == 60 and h["notes"]


async def test_routes_only_to_the_two_enabled_apps():
    for brief in ({"detected_intent": "create_new", "confidence": 0.9, "scene": {"shot_structure": "single_continuous"}},
                  {"detected_intent": "create_new", "confidence": 0.9, "scene": {"shot_structure": "multi_scene"},
                   "audio": {"voiceover": "requested", "subtitles": "requested"}}):
        res, apps = await plan(brief)
        assert apps and set(apps) <= {"pltt-flow-video-2", "pltt-storyboard-video-maker", "palette-motion-studio"}


async def test_a_disabled_app_never_asks_for_replacement_consent():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "scene": {"shot_structure": "multi_scene"},
                            "constraints": {"duration_s": 10},
                            "motion": {"kinds": ["programmatic_precise"], "programmatic_elements": ["data_visualization"],
                                       "integration": "standalone"}}, mode="normal")
    assert apps == ["palette-motion-studio"] and res["plan_changes"] == []


async def test_explainer_videos_end_with_finishing_effects():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["explainer"],
                            "scene": {"shot_structure": "multi_scene"}, "constraints": {"duration_s": 30}})
    # One AI-footage app films it and its engine adds the explainer effects itself (no separate Effects step).
    assert res["status"] == "awaiting_approval" and apps in (["pltt-flow-video-2"], ["pltt-storyboard-video-maker"])
    assert "edit.explainer_effects" in res["plan"]["steps"][0]["capabilities_used"]


async def test_uploaded_talking_head_with_effects_goes_to_effects_studio():
    assets = {"a1": {"modality": "video", "filename": "me.mp4", "tags": ["person"]}}
    res, apps = await plan({"detected_intent": "edit_existing", "confidence": 0.9, "production_type": ["editing"],
                            "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit", "use": "edit_source"}],
                            "editing": {"operations": ["effects"]}}, prompt="add effects to my video", assets=assets)
    assert apps == ["palette-effects-studio"]


async def test_non_explainer_story_has_no_effects_step():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["storytelling"],
                            "scene": {"shot_structure": "multi_scene"}})
    assert "palette-effects-studio" not in apps
