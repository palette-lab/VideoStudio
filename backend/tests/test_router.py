"""Phase 4: hard filter, scoring and deterministic route/plan building (no LLM)."""

import copy

from router_core import registry, router
from router_core.requirements import compile_brief
from router_core.schemas import Brief


def route(brief, assets=None, prompt="x"):
    b = Brief.model_validate(brief)
    comp = compile_brief(b, assets or {})
    return b, comp, router.build_routes(b, comp, assets or {}, bool(prompt))


def best(brief, assets=None):
    _, _, r = route(brief, assets)
    v = router.viable_routes(r)
    return [s["app_id"] for s in v[0]["steps"]] if v else None


IMG = {"a1": {"modality": "image", "filename": "shoe.jpg", "tags": ["product"]}}
GARMENT = {"a1": {"modality": "image", "filename": "dress.jpg", "tags": ["garment"]}}
VID = {"a1": {"modality": "video", "filename": "c.mp4", "tags": []}}


def test_single_clip_prefers_specialist_over_storyboard():
    _, _, r = route({"detected_intent": "create_new"})
    s = [e for e in r["singles"] if e["ok"]]
    assert s[0]["app_id"] == "normal-video-generator" and s[1]["app_id"] == "storyboard-studio"
    assert s[0]["score"] > s[1]["score"]


def test_single_continuous_shot_excludes_multi_scene_app():
    _, _, r = route({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous"}})
    sb = next(e for e in r["singles"] if e["app_id"] == "storyboard-studio")
    assert not sb["ok"] and any("not single continuous" in f for f in sb["fails"])


def test_duration_limit_is_enforced():
    _, _, r = route({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous"},
                     "constraints": {"duration_s": 60}})
    ng = next(e for e in r["singles"] if e["app_id"] == "normal-video-generator")
    assert not ng["ok"] and any("at most 10s" in f for f in ng["fails"])
    assert router.viable_routes(r) == []


def test_unknown_aspect_ratio_is_unconfirmed_not_supported():
    _, _, r = route({"detected_intent": "create_new", "constraints": {"aspect_ratio": "9:16"}})
    top = [e for e in r["singles"] if e["ok"]][0]
    assert any("9:16" in u for u in top["unconfirmed"])
    assert top["parts"]["constraint_fit"] < 1


def test_multi_scene_goes_to_storyboard():
    assert best({"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"}}) == ["storyboard-studio"]


def test_exploded_view_goes_to_motion_studio():
    assert best({"detected_intent": "create_new", "motion": {"kinds": ["programmatic_precise"],
                                                              "programmatic_elements": ["exploded_view"]}}) == ["programmatic-motion-studio"]


def test_fashion_needs_garment_image():
    b = {"detected_intent": "create_new", "production_type": ["fashion"]}
    assert best(b) is None
    _, _, r = route(b)
    ff = next(e for e in r["singles"] if e["app_id"] == "fashion-film-studio")
    assert ff["missing_assets"] and ff["missing_assets"][0]["tag"] == "garment"
    b2 = {**b, "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}
    assert best(b2, GARMENT) == ["fashion-film-studio"]


def test_edit_existing_goes_to_editor_with_the_footage():
    b = {"detected_intent": "edit_existing", "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit", "use": "edit_source"}],
         "editing": {"operations": ["captions"]}}
    b_, comp, r = route(b, VID)
    plan = router.to_plan(router.viable_routes(r)[0], "add captions", VID)
    assert plan["steps"][0]["app_id"] == "video-edit-enhance"
    assert {"source": "asset:a1", "format": "video_file"} in plan["steps"][0]["inputs"]


def test_story_plus_overlay_is_a_two_app_chain_with_handoff():
    b = {"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"},
         "motion": {"kinds": ["realistic"], "programmatic_elements": ["spec_callouts"], "integration": "overlay_on_footage"}}
    _, _, r = route(b)
    assert [s["app_id"] for s in r["multi"]["steps"]] == ["storyboard-studio", "programmatic-motion-studio"]
    plan = router.to_plan(r["multi"], "x", {})
    assert plan["steps"][1]["inputs"][0] == {"source": "step:s1", "format": "video_file",
                                             "use": "The video to place the graphics on."}


def test_clip_with_subtitles_chains_generator_and_editor():
    b = {"detected_intent": "create_new", "audio": {"subtitles": "requested"}}
    _, _, r = route(b)
    assert [s["app_id"] for s in r["multi"]["steps"]] == ["normal-video-generator", "video-edit-enhance"]


def test_segments_are_joined_by_the_editor_with_every_clip():
    b = {"detected_intent": "create_new", "motion": {"kinds": ["realistic"], "programmatic_elements": ["exploded_view"],
                                                     "integration": "separate_segments"}}
    _, comp, r = route(b)
    assert not registry.unavailable_capabilities({x["capability"] for x in comp["requirements"]})
    plan = router.to_plan(r["multi"], "x", {})
    last = plan["steps"][-1]
    assert last["app_id"] == "video-edit-enhance" and "edit.assemble_clips" in last["capabilities_used"]
    joined = [i["source"] for i in last["inputs"] if i["source"].startswith("step:")]
    assert joined == [f"step:{s['step_id']}" for s in plan["steps"][:-1]]


def test_unavailable_app_is_filtered_and_fallback_scored(monkeypatch):
    apps = copy.deepcopy(registry.apps())
    apps["normal-video-generator"]["status"] = "offline"
    monkeypatch.setattr(registry, "apps", lambda: apps)
    assert best({"detected_intent": "create_new"}) == ["storyboard-studio"]


def test_product_image_is_passed_to_generator_by_role():
    b = {"detected_intent": "animate_asset", "asset_roles": [{"asset_id": "a1", "role": "product", "use": "must_appear"}]}
    _, _, r = route(b, IMG)
    plan = router.to_plan(router.viable_routes(r)[0], "animate", IMG)
    assert plan["steps"][0]["app_id"] == "normal-video-generator"
    assert {"source": "asset:a1", "format": "image_file"} in plan["steps"][0]["inputs"]


def test_role_not_accepted_fails_when_known():
    # Normal Video Generator's accepted roles (unverified) do not include "garment".
    b = {"detected_intent": "animate_asset", "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}
    _, _, r = route(b, GARMENT)
    ng = next(e for e in r["singles"] if e["app_id"] == "normal-video-generator")
    assert any("garment" in f for f in ng["fails"])
