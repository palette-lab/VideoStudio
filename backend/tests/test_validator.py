"""Scenario cases 1-12 at the deterministic layer: the plan a brain might propose, and what the validator decides."""

from router_core.validator import errors, validate
from tests.helpers import AUDIO, IMG_GARMENT, IMG_PRODUCT, VIDEO, step, und


def codes(issues, level="error"):
    return {i["code"] for i in issues if i["level"] == level}


def test_01_prompt_only_product_video_single_app():
    u = und(("product clip", ["video.text_to_video"]))
    p = {"steps": [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")])]}
    assert errors(validate(p, u, "normal", {})) == []


def test_02_image_only_video():
    u = und(("animate photo", ["video.image_to_video"]))
    p = {"steps": [step("s1", "normal-video-generator", ["video.image_to_video"], [("asset:a1", "image_file")])]}
    assert errors(validate(p, u, "normal", IMG_PRODUCT)) == []


def test_03_fashion_with_garment_images_ok_and_without_garment_asks():
    u = und(("campaign", ["fashion.garment_film"]))
    p = {"steps": [step("s1", "fashion-film-studio", ["fashion.garment_film"], [("prompt", "text"), ("asset:a1", "image_file")])]}
    assert errors(validate(p, u, "normal", IMG_GARMENT)) == []
    # Same plan, but the image shows a product, not a garment -> must not pass.
    assert "missing_required_asset" in codes(validate(p, u, "normal", IMG_PRODUCT))


def test_04_edit_existing_video():
    u = und(("enhance", ["edit.enhance_quality", "edit.captions_subtitles"]))
    p = {"steps": [step("s1", "video-edit-enhance", ["edit.enhance_quality", "edit.captions_subtitles"], [("asset:a1", "video_file")])]}
    assert errors(validate(p, u, "normal", VIDEO)) == []


def test_04b_edit_without_video_is_rejected():
    u = und(("enhance", ["edit.enhance_quality"]))
    p = {"steps": [step("s1", "video-edit-enhance", ["edit.enhance_quality"], [("prompt", "text")])]}
    assert "missing_required_asset" in codes(validate(p, u, "normal", {}))


def test_05_multi_scene_story_single_app():
    u = und(("story", ["story.multi_scene_storyboard", "video.from_storyboard", "story.character_consistency"]))
    p = {"steps": [step("s1", "storyboard-studio", ["story.multi_scene_storyboard", "video.from_storyboard", "story.character_consistency"],
                        [("prompt", "text")], outputs=("storyboard_json", "video_file"))]}
    assert errors(validate(p, u, "hybrid", {})) == []
    assert "single_app_sufficient" in codes(validate(p, u, "hybrid", {}), "warning")


def test_05b_normal_video_generator_cannot_do_multi_scene():
    u = und(("story", ["story.multi_scene_storyboard", "video.from_storyboard"]))
    p = {"steps": [step("s1", "normal-video-generator", ["story.multi_scene_storyboard"], [("prompt", "text")])]}
    c = codes(validate(p, u, "normal", {}))
    assert {"capability_not_supported", "uncovered_requirement"} <= c


def test_06_exploded_view():
    u = und(("exploded", ["motion.exploded_view"]))
    p = {"steps": [step("s1", "programmatic-motion-studio", ["motion.exploded_view"], [("prompt", "text"), ("asset:a1", "image_file")])]}
    assert errors(validate(p, u, "analyze", IMG_PRODUCT)) == []


def test_07_reference_recreation_is_not_available():
    from router_core import registry
    assert registry.unavailable_capabilities({"story.reference_shot_breakdown"}) == ["story.reference_shot_breakdown"]
    u = und(("recreate", ["story.reference_shot_breakdown", "video.from_storyboard"]))
    p = {"steps": [step("s1", "storyboard-studio", ["story.reference_shot_breakdown", "video.from_storyboard"],
                        [("prompt", "text"), ("asset:a1", "video_file")])]}
    c = codes(validate(p, u, "normal", VIDEO))
    assert {"capability_not_supported", "unsupported_input", "uncovered_requirement"} <= c


def test_07b_storyboard_takes_text_and_images():
    u = und(("story from photos", ["story.multi_scene_storyboard", "video.from_storyboard"]))
    p = {"steps": [step("s1", "storyboard-studio", ["story.multi_scene_storyboard", "video.from_storyboard"],
                        [("prompt", "text"), ("asset:a1", "image_file")])]}
    assert errors(validate(p, u, "normal", IMG_PRODUCT)) == []


def test_08_genuine_hybrid_with_valid_handoff():
    u = und(("story film", ["story.multi_scene_storyboard", "video.from_storyboard"]),
            ("exploded insert + titles on the film", ["motion.overlay_graphics"]))
    p = {"steps": [
        step("s1", "storyboard-studio", ["story.multi_scene_storyboard", "video.from_storyboard"], [("prompt", "text")], subtasks=["t1"]),
        step("s2", "programmatic-motion-studio", ["motion.overlay_graphics"], [("step:s1", "video_file")], subtasks=["t2"]),
    ]}
    assert errors(validate(p, u, "hybrid", {})) == []
    assert "mode_violation" in codes(validate(p, u, "normal", {}))


def test_08b_unnecessary_combination_rejected():
    u = und(("clip", ["video.text_to_video"]))
    p = {"steps": [
        step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")]),
        step("s2", "video-edit-enhance", ["edit.enhance_quality"], [("step:s1", "video_file")]),
    ]}
    c = codes(validate(p, u, "hybrid", {}))
    assert "unnecessary_combination" in c


def test_10_unsupported_input_and_unavailable_app(monkeypatch):
    u = und(("music video", ["video.text_to_video"]))
    p = {"steps": [step("s1", "normal-video-generator", ["video.text_to_video"], [("asset:a1", "audio_file")])]}
    assert "unsupported_input" in codes(validate(p, u, "normal", AUDIO))
    from router_core import registry
    apps = {k: dict(v) for k, v in registry.apps().items()}
    apps["normal-video-generator"]["status"] = "offline"
    monkeypatch.setattr(registry, "apps", lambda: apps)
    p2 = {"steps": [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")])]}
    assert "app_unavailable" in codes(validate(p2, u, "normal", {}))


def test_11_handoff_errors():
    u = und(("a", ["story.multi_scene_storyboard"]), ("b", ["edit.captions_subtitles"]))
    # storyboard_json is not accepted by the editor; and order reversed
    p = {"steps": [
        step("s1", "storyboard-studio", ["story.multi_scene_storyboard"], [("prompt", "text")], outputs=("storyboard_json",), subtasks=["t1"]),
        step("s2", "video-edit-enhance", ["edit.captions_subtitles"], [("step:s1", "storyboard_json")], subtasks=["t2"]),
    ]}
    assert "handoff_not_accepted" in codes(validate(p, u, "hybrid", {}))
    p["steps"].reverse()
    assert "invalid_handoff_order" in codes(validate(p, u, "hybrid", {}))


def test_invented_app_and_capability_rejected():
    u = und(("x", ["video.hologram"]))
    p = {"steps": [step("s1", "magic-studio", ["video.hologram"], [("prompt", "text")])]}
    c = codes(validate(p, u, "normal", {}))
    assert {"unknown_app", "unknown_capability"} <= c


def test_declared_infeasible_blocks():
    assert "declared_infeasible" in codes(validate({"feasible": False, "steps": []}, und(), "normal", {}))
