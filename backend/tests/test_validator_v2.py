"""Phase 5: brief-aware validation (limits, roles, exclusions, unrequested work, deliverable, handoffs)."""

from router_core import router
from router_core.requirements import compile_brief
from router_core.schemas import Brief
from router_core.validator import errors, validate
from tests.helpers import step


def und(brief, assets=None):
    b = Brief.model_validate(brief)
    comp = compile_brief(b, assets or {})
    return {"brief": b.model_dump(), "subtasks": comp["subtasks"]}


def codes(issues, level="error"):
    return {i["code"] for i in issues if i["level"] == level}


def test_duration_over_known_limit_is_an_error():
    u = und({"detected_intent": "create_new", "constraints": {"duration_s": 30}})
    p = {"steps": [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"])]}
    assert "limit_violation" in codes(validate(p, u, "normal", {}))


def test_unknown_aspect_is_unconfirmed_not_error():
    u = und({"detected_intent": "create_new", "constraints": {"aspect_ratio": "9:16"}})
    p = {"steps": [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"])]}
    issues = validate(p, u, "normal", {})
    assert errors(issues) == [] and "unconfirmed_requirement" in codes(issues, "unconfirmed")


def test_excluded_subtitles_cannot_be_added():
    u = und({"detected_intent": "edit_existing", "audio": {"subtitles": "excluded"},
             "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit"}], "editing": {"operations": ["trim"]}},
            {"a1": {"modality": "video"}})
    u["subtasks"][0]["required_capabilities"].append("edit.captions_subtitles")  # simulate a bad plan source
    p = {"steps": [step("s1", "video-edit-enhance", ["edit.trim_and_cut", "edit.captions_subtitles"],
                        [("asset:a1", "video_file")], subtasks=["t_edit"])]}
    assert "exclusion_violated" in codes(validate(p, u, "normal", {"a1": {"modality": "video", "role": "footage_to_edit"}}))


def test_unrequested_capability_rejected():
    u = und({"detected_intent": "create_new"})
    p = {"steps": [step("s1", "storyboard-studio", ["video.text_to_video", "story.multi_scene_storyboard"],
                        [("prompt", "text")], subtasks=["t_generate"])]}
    assert "unrequested_capability" in codes(validate(p, u, "normal", {}))


def test_unused_intermediate_output_is_error():
    u = und({"detected_intent": "create_new", "audio": {"subtitles": "requested"}})
    p = {"steps": [step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")], subtasks=["t_generate"]),
                   step("s2", "video-edit-enhance", ["edit.captions_subtitles"], [("prompt", "text")], subtasks=["t_edit"])]}
    c = codes(validate(p, u, "hybrid", {}))
    assert "output_not_used" in c and "missing_required_asset" in c


def test_router_plans_always_validate():
    cases = [
        ({"detected_intent": "create_new"}, {}),
        ({"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene", "consistency": ["hero"]}}, {}),
        ({"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"},
          "motion": {"kinds": ["realistic"], "programmatic_elements": ["spec_callouts"]}}, {}),
        ({"detected_intent": "create_new", "audio": {"subtitles": "requested", "music": "requested"}}, {}),
        ({"detected_intent": "edit_existing", "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit"}],
          "editing": {"operations": ["enhance", "reformat"]}}, {"a1": {"modality": "video", "tags": []}}),
        ({"detected_intent": "create_new", "production_type": ["fashion"],
          "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}, {"a1": {"modality": "image", "tags": ["garment"]}}),
    ]
    for brief, assets in cases:
        b = Brief.model_validate(brief)
        comp = compile_brief(b, assets)
        r = router.build_routes(b, comp, assets, True)
        route = (router.viable_routes(r) or [r["multi"]])[0]
        plan = router.to_plan(route, "prompt", assets)
        for a in assets:
            assets[a]["role"] = b.role_of(a).role
        mode = "hybrid" if len(plan["steps"]) > 1 else "normal"
        assert errors(validate(plan, {"brief": b.model_dump(), "subtasks": comp["subtasks"]}, mode, assets)) == [], brief


def test_garment_role_satisfies_requirement_without_perception_tag():
    u = und({"detected_intent": "create_new", "production_type": ["fashion"],
             "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}, {"a1": {"modality": "image"}})
    p = {"steps": [step("s1", "fashion-film-studio", ["fashion.garment_film"], [("prompt", "text"), ("asset:a1", "image_file")],
                        subtasks=["t_generate"])]}
    assert errors(validate(p, u, "normal", {"a1": {"modality": "image", "tags": [], "role": "garment"}})) == []
