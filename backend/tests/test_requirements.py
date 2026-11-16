"""Phase 2: brief fields -> capabilities, deterministically (no LLM)."""

from router_core.requirements import compile_brief, rule_questions
from router_core.schemas import Brief

IMG = {"a1": {"modality": "image", "filename": "p.jpg"}}
VID = {"a1": {"modality": "video", "filename": "c.mp4"}}


def caps(brief, assets=None):
    return [r["capability"] for r in compile_brief(Brief.model_validate(brief), assets or {})["requirements"]]


def test_single_shot_text():
    assert caps({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous"}}) == ["video.text_to_video"]


def test_unspecified_shot_text_defaults_to_one_clip():
    assert caps({"detected_intent": "create_new"}) == ["video.text_to_video"]


def test_product_photo_must_appear_is_image_to_video():
    b = {"detected_intent": "animate_asset", "asset_roles": [{"asset_id": "a1", "role": "product", "use": "must_appear"}]}
    assert caps(b, IMG) == ["video.image_to_video"]


def test_style_only_image_does_not_force_image_to_video():
    b = {"detected_intent": "create_new", "asset_roles": [{"asset_id": "a1", "role": "style_reference", "use": "style_only"}]}
    assert caps(b, IMG) == ["video.text_to_video"]


def test_multi_scene_with_consistency():
    b = {"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene", "consistency": ["same girl"]}}
    assert caps(b) == ["story.multi_scene_storyboard", "video.from_storyboard", "story.character_consistency"]


def test_scene_count_implies_multi_scene():
    assert "story.multi_scene_storyboard" in caps({"detected_intent": "create_new", "scene": {"scene_count": 3}})


def test_programmatic_only_has_no_generation():
    b = {"detected_intent": "create_new", "motion": {"kinds": ["programmatic_precise"], "programmatic_elements": ["exploded_view", "spec_callouts"]}}
    assert caps(b) == ["motion.exploded_view", "motion.product_animation"]


def test_realistic_plus_graphics_defaults_to_overlay_with_note():
    b = {"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"},
         "motion": {"kinds": ["realistic", "programmatic_precise"], "programmatic_elements": ["spec_callouts", "kinetic_text"]}}
    out = compile_brief(Brief.model_validate(b), {})
    c = [r["capability"] for r in out["requirements"]]
    assert "motion.overlay_graphics" in c and "edit.assemble_clips" not in c
    assert out["notes"]


def test_separate_segments_needs_assembly():
    b = {"detected_intent": "create_new", "motion": {"kinds": ["realistic"], "programmatic_elements": ["exploded_view"],
                                                     "integration": "separate_segments"}}
    assert "edit.assemble_clips" in caps(b)


def test_fashion_uses_garment_film_not_generic_generation():
    b = {"detected_intent": "create_new", "production_type": ["fashion"],
         "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}
    c = caps(b, IMG)
    assert c == ["fashion.garment_film"]


def test_edit_existing_only_edit_caps():
    b = {"detected_intent": "edit_existing", "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit", "use": "edit_source"}],
         "editing": {"operations": ["captions", "reformat"]}}
    assert caps(b, VID) == ["edit.captions_subtitles", "edit.reformat_aspect"]


def test_requested_audio_adds_audio_mix_but_unspecified_does_not():
    assert caps({"detected_intent": "create_new", "audio": {"music": "requested"}}) == ["video.text_to_video", "edit.audio_mix"]
    assert caps({"detected_intent": "create_new", "audio": {"music": "unspecified"}}) == ["video.text_to_video"]


def test_reference_recreation():
    b = {"detected_intent": "recreate_reference", "asset_roles": [{"asset_id": "a1", "role": "motion_reference"}]}
    c = caps(b, VID)
    assert c[0] == "story.reference_shot_breakdown"


def test_groups_are_ordered():
    b = {"detected_intent": "create_new", "audio": {"subtitles": "requested"},
         "motion": {"kinds": ["realistic"], "programmatic_elements": ["kinetic_text"]}}
    groups = [t["group"] for t in compile_brief(Brief.model_validate(b), {})["subtasks"]]
    assert groups == ["generate", "motion", "edit"]


def test_conflict_detected_and_asked_with_options():
    b = Brief.model_validate({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous", "scene_count": 5}})
    out = compile_brief(b, {})
    assert out["conflicts"]
    q = rule_questions(b, out, "x", {})
    assert q[0].id == "conflict" and q[0].options == ["One continuous shot", "5 separate scenes"]


def test_subtitles_requested_and_excluded_conflict():
    b = Brief.model_validate({"detected_intent": "edit_existing", "audio": {"subtitles": "excluded"},
                              "editing": {"operations": ["captions"]}})
    out = compile_brief(b, VID)
    assert out["conflicts"] and "edit.captions_subtitles" not in [r["capability"] for r in out["requirements"]]


def test_no_prompt_upload_asks_outcome_with_options():
    b = Brief.model_validate({"detected_intent": "unclear"})
    q = rule_questions(b, compile_brief(b, IMG), "", IMG)
    assert q[0].id == "outcome" and len(q[0].options) >= 2


def test_edit_without_footage_asks_upload():
    b = Brief.model_validate({"detected_intent": "edit_existing", "editing": {"operations": ["trim"]}})
    q = rule_questions(b, compile_brief(b, {}), "trim my video", {})
    assert q[0].kind == "upload"


def test_clear_request_asks_nothing():
    b = Brief.model_validate({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous"}})
    assert rule_questions(b, compile_brief(b, {}), "a clip of waves", {}) == []


def test_cross_check_reports_disagreement():
    b = Brief.model_validate({"detected_intent": "create_new", "suggested_capabilities": ["video.text_to_video", "edit.audio_mix", "fake.cap"]})
    cc = compile_brief(b, {})["cross_check"]
    assert cc["ai_only"] == ["edit.audio_mix"] and cc["agree"] is False


def test_transitions_inside_new_production_are_not_an_edit_job():
    b = {"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"}, "editing": {"operations": ["transitions"]}}
    out = compile_brief(Brief.model_validate(b), {})
    assert "edit.transitions" not in [r["capability"] for r in out["requirements"]] and out["notes"]


def test_transitions_between_uploaded_clips_still_required():
    b = {"detected_intent": "edit_existing", "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit"}],
         "editing": {"operations": ["assemble", "transitions"]}}
    assert "edit.transitions" in caps(b, VID)


def test_ad_with_own_animated_scene_and_overlay_needs_both():
    b = {"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"},
         "motion": {"kinds": ["realistic", "programmatic_precise"], "programmatic_elements": ["exploded_view", "kinetic_text"],
                    "integration": "separate_segments", "overlay_elements": ["kinetic_text"]}}
    c = caps(b)
    assert {"edit.assemble_clips", "motion.overlay_graphics", "motion.exploded_view", "motion.kinetic_typography"} <= set(c)


def test_own_scenes_without_overlays_do_not_overlay():
    b = {"detected_intent": "create_new", "motion": {"kinds": ["realistic"], "programmatic_elements": ["exploded_view"],
                                                     "integration": "separate_segments"}}
    assert "motion.overlay_graphics" not in caps(b)


def test_overlay_element_counts_even_if_listed_once():
    b = Brief.model_validate({"motion": {"kinds": ["realistic"], "programmatic_elements": ["exploded_view"],
                                         "overlay_elements": ["kinetic_text"]}})
    assert "kinetic_text" in b.motion.programmatic_elements and "programmatic_precise" in b.motion.kinds
