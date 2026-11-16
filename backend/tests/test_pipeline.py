"""Pipeline: clarification, unsupported, routing, consent, decision object, minimal LLM calls."""

import copy

from router_core import pipeline, registry, validator
from tests.fakes import FakeLLM


async def run(brief, prompt="x", mode="normal", assets=None, proceed=False, judge_choice=0, answers=None):
    llm = FakeLLM(brief, judge_choice)
    res = await pipeline.analyze(prompt=prompt, mode=mode, assets=copy.deepcopy(assets or {}), perception={},
                                 answers=answers or {}, proceed_with_defaults=proceed, chat=llm)
    return res, llm.calls


def apps(res):
    return [s["app_id"] for s in (res["plan"] or {}).get("steps", [])]


async def test_simple_clip_uses_one_llm_call():
    res, calls = await run({"detected_intent": "create_new", "confidence": 0.9})
    assert res["status"] == "awaiting_approval" and apps(res) == ["normal-video-generator"]
    assert calls == ["brief"]
    d = res["decision"]
    assert d["execution_ready"] is True and d["selected_applications"] == ["normal-video-generator"]
    assert 0 < d["confidence"] <= 1 and "route_score" in d["confidence_breakdown"]


async def test_ai_self_confidence_cannot_make_an_unconfirmed_plan_fully_confident():
    res, _ = await run({"detected_intent": "create_new", "confidence": 1.0, "constraints": {"aspect_ratio": "9:16"}})
    assert res["decision"]["confidence"] < 0.9
    assert res["decision"]["unconfirmed_requirements"]


async def test_unsupported_reference_recreation_is_explained_not_asked():
    res, _ = await run({"detected_intent": "recreate_reference", "asset_roles": [{"asset_id": "a1", "role": "motion_reference"}],
                        "missing_info": [{"question": "Upload your product?", "blocking": True}]},
                       assets={"a1": {"modality": "video", "filename": "ad.mp4"}})
    assert res["status"] == "not_possible" and res["questions"] == []
    assert res["decision"]["unsupported_requirements"][0]["capability"] == "story.reference_shot_breakdown"


async def test_conflict_asks_one_question_with_options():
    res, _ = await run({"detected_intent": "create_new", "scene": {"shot_structure": "single_continuous", "scene_count": 4}})
    assert res["status"] == "needs_clarification" and len(res["questions"]) == 1
    assert res["questions"][0]["options"]


async def test_defaults_skip_non_essential_questions():
    b = {"detected_intent": "create_new", "missing_info": [{"question": "How long?", "blocking": True}]}
    res, _ = await run(b)
    assert res["status"] == "needs_clarification"
    res, _ = await run(b, proceed=True)
    assert res["status"] == "awaiting_approval"


async def test_fashion_without_garment_asks_for_upload():
    res, _ = await run({"detected_intent": "create_new", "production_type": ["fashion"]})
    assert res["status"] == "needs_clarification" and res["questions"][0]["kind"] == "upload"


async def test_normal_mode_suggests_hybrid_when_combination_needed():
    b = {"detected_intent": "create_new", "audio": {"subtitles": "requested"}}
    res, _ = await run(b, mode="normal")
    assert res["status"] == "not_possible" and "Hybrid" in res["suggestion"]
    res, _ = await run(b, mode="hybrid")
    assert apps(res) == ["normal-video-generator", "video-edit-enhance"]
    assert res["decision"]["handoff_requirements"] == [{"from_step": "s1", "to_step": "s2", "format": "video_file", "validated": True}]


async def test_hybrid_does_not_combine_when_one_app_suffices():
    res, _ = await run({"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"}}, mode="hybrid")
    assert apps(res) == ["storyboard-studio"]


async def test_analyze_judges_only_when_several_routes_are_viable():
    _, calls = await run({"detected_intent": "create_new", "motion": {"kinds": ["programmatic_precise"],
                                                                       "programmatic_elements": ["exploded_view"]}}, mode="analyze")
    assert "judge" not in calls and "review" in calls
    res, calls = await run({"detected_intent": "create_new"}, mode="analyze", judge_choice=1)
    assert "judge" in calls and apps(res) == ["storyboard-studio"]


async def test_unavailable_preferred_app_requires_consent(monkeypatch):
    a = copy.deepcopy(registry.apps())
    a["normal-video-generator"]["status"] = "offline"
    monkeypatch.setattr(registry, "apps", lambda: a)
    res, _ = await run({"detected_intent": "create_new"})
    assert apps(res) == ["storyboard-studio"]
    assert res["plan_changes"] and res["plan_changes"][0]["unavailable"] == "Normal Video Generator"
    assert res["decision"]["execution_ready"] is False


async def test_close_call_is_flagged_only_when_scores_are_close():
    res, _ = await run({"detected_intent": "create_new", "confidence": 0.9})
    assert not any("almost equally" in u for u in res["uncertainty"])


async def test_asset_roles_preserved_with_original_reference():
    assets = {"a1": {"modality": "image", "filename": "shoe.jpg", "sha256": "abc", "tags": ["product"]}}
    res, _ = await run({"detected_intent": "animate_asset", "asset_roles": [{"asset_id": "a1", "role": "product", "use": "must_appear"}]},
                       assets=assets)
    d = res["decision"]
    assert d["input_assets"][0]["sha256"] == "abc" and d["asset_roles"][0]["role"] == "product"
    assert {"source": "asset:a1", "format": "image_file"} in d["workflow_steps"][0]["inputs"]


async def test_content_questions_do_not_block_routing():
    res, _ = await run({"detected_intent": "create_new", "motion": {"kinds": ["programmatic_precise"],
                        "programmatic_elements": ["data_visualization"]},
                        "missing_info": [{"question": "Revenue per year?", "blocking": True, "affects": "content"}]})
    assert res["status"] == "awaiting_approval" and res["open_details"][0]["question"] == "Revenue per year?"


async def test_confident_route_turns_ai_question_into_optional_refinement():
    res, _ = await run({"detected_intent": "create_new", "production_type": ["fashion"], "confidence": 0.85,
                        "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}],
                        "missing_info": [{"question": "Model or product-only?", "blocking": True, "affects": "routing"}]},
                       assets={"a1": {"modality": "image", "filename": "d.jpg", "tags": ["garment"]}})
    assert res["status"] == "awaiting_approval"
    assert res["open_details"][0]["question"] == "Model or product-only?" and res["open_details"][0]["blocking"] is False


async def test_low_confidence_ai_question_blocks():
    res, _ = await run({"detected_intent": "create_new", "confidence": 0.4,
                        "missing_info": [{"question": "Story or single shot?", "blocking": True, "affects": "routing"}]})
    assert res["status"] == "needs_clarification"


async def test_subtitles_on_edit_are_captions_not_kinetic_text():
    res, _ = await run({"detected_intent": "edit_existing", "asset_roles": [{"asset_id": "a1", "role": "footage_to_edit"}],
                        "audio": {"subtitles": "requested"}, "motion": {"programmatic_elements": ["kinetic_text"]},
                        "editing": {"operations": ["captions", "reformat"]}},
                       assets={"a1": {"modality": "video", "filename": "i.mp4"}})
    assert apps(res) == ["video-edit-enhance"]


async def test_auto_offers_every_valid_approach_with_recommendation():
    res, calls = await run({"detected_intent": "create_new", "confidence": 0.9}, mode="auto")
    # Auto includes AI Analyze: the judge picks among several valid options and the recommended plan is reviewed.
    assert calls == ["brief", "judge", "review"] and res["status"] == "awaiting_approval"
    assert res["review"]["matches_intent"] and res["judge"]["choice"] == 0
    opts = res["options"]
    assert [o["apps"] for o in opts] == [["normal-video-generator"], ["storyboard-studio"]]
    assert opts[0]["recommended"] and not opts[1]["recommended"]
    assert any("multi scene" in d for d in opts[1]["differences_from_recommended"])
    assert set(res["option_results"]) == {"o1", "o2"} and apps(res) == ["normal-video-generator"]


async def test_auto_offers_hybrid_when_one_app_is_not_enough():
    res, _ = await run({"detected_intent": "create_new", "audio": {"subtitles": "requested"}}, mode="auto")
    assert [o["apps"] for o in res["options"]] == [["normal-video-generator", "video-edit-enhance"]]
    assert res["options"][0]["kind"] == "multi" and res["plan_mode"] == "hybrid"


async def test_auto_never_offers_unnecessary_combination():
    res, _ = await run({"detected_intent": "create_new", "scene": {"shot_structure": "multi_scene"}}, mode="auto")
    assert all(o["kind"] == "single" for o in res["options"])


async def test_option_explanation_matches_scores():
    res, _ = await run({"detected_intent": "create_new", "confidence": 0.9}, mode="auto")
    second = res["options"][1]
    assert "scores lower than Normal Video Generator" in second["why"]


LAUNCH = {"detected_intent": "create_new", "production_type": ["commercial", "fashion"], "confidence": 0.85,
          "refers_to_uploads": True,
          "scene": {"shot_structure": "multi_scene"},
          "motion": {"kinds": ["realistic", "programmatic_precise"], "programmatic_elements": ["kinetic_text"],
                     "integration": "separate_segments"},
          "evidence": [{"field": "motion.integration", "source": "prompt", "quote": "an animated intro, then the film"}]}
LAUNCH_PROMPT = "Make an animated intro, then the film, using my uploaded photos."


GARMENT = {"a1": {"modality": "image", "filename": "dress.jpg", "tags": ["garment"]}}
LAUNCH_G = {**LAUNCH, "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}


async def test_separate_segments_are_joined_not_dead_ended():
    res, _ = await run(LAUNCH_G, prompt=LAUNCH_PROMPT, assets=GARMENT, mode="hybrid")
    assert res["status"] == "awaiting_approval", res.get("suggestion")
    last = res["plan"]["steps"][-1]
    assert last["app_id"] == "video-edit-enhance" and "edit.assemble_clips" in last["capabilities_used"]
    assert len([i for i in last["inputs"] if i["source"].startswith("step:")]) == len(res["plan"]["steps"]) - 1


async def test_choosing_overlay_builds_a_plan_without_assembly():
    garment = {"a1": {"modality": "image", "filename": "dress.jpg", "tags": ["garment"]}}
    b = {**LAUNCH, "asset_roles": [{"asset_id": "a1", "role": "garment", "use": "must_appear"}]}
    res, _ = await run(b, assets=garment, mode="hybrid", answers={pipeline.SEGMENTS_Q: pipeline.SEGMENTS_OVERLAY})
    assert res["status"] == "awaiting_approval"
    caps = {r["capability"] for r in res["understanding"]["requirements"]}
    assert "edit.assemble_clips" not in caps and "motion.overlay_graphics" in caps


async def test_keeping_separate_segments_builds_a_joined_plan():
    res, _ = await run(LAUNCH_G, prompt=LAUNCH_PROMPT, assets=GARMENT, mode="hybrid",
                       answers={pipeline.SEGMENTS_Q: pipeline.SEGMENTS_KEEP})
    assert res["status"] == "awaiting_approval"
    assert "edit.assemble_clips" in {r["capability"] for r in res["understanding"]["requirements"]}


CAR = {"detected_intent": "create_new", "production_type": ["commercial", "product_animation"], "confidence": 0.9,
       "scene": {"shot_structure": "multi_scene", "scene_count": 8, "consistency": ["same car design"]},
       "motion": {"kinds": ["realistic", "programmatic_precise"], "programmatic_elements": ["exploded_view"],
                  "integration": "separate_segments"},
       "audio": {"music": "requested", "voiceover": "excluded"},
       "editing": {"operations": ["assemble", "transitions", "audio_mix"]}}
CAR_PROMPT = ("A cinematic car ad in 8 scenes. Show an exploded-view animation of the battery and motors. "
              "Smooth camera movements and cinematic transitions. Include cinematic background music.")


async def test_exploded_view_scene_in_a_story_is_joined_by_the_editor():
    res, _ = await run(CAR, prompt=CAR_PROMPT, mode="hybrid", proceed=True)
    caps = {r["capability"] for r in res["understanding"]["requirements"]}
    assert "edit.assemble_clips" in caps and "motion.overlay_graphics" not in caps
    assert res["status"] == "awaiting_approval", res.get("suggestion")
    assert apps(res) == ["storyboard-studio", "programmatic-motion-studio", "video-edit-enhance"]


async def test_quoted_segments_are_kept():
    b = {**CAR, "evidence": [{"field": "motion.integration", "source": "prompt", "quote": "as a separate clip, then join"}]}
    res, _ = await run(b, prompt=CAR_PROMPT + " Make the exploded view as a separate clip, then join it in.", mode="hybrid")
    caps = {r["capability"] for r in res["understanding"]["requirements"]}
    assert "edit.assemble_clips" in caps and "edit.transitions" in caps  # real clips are joined: transitions are an edit job


def test_quote_with_model_commentary_still_counts():
    from router_core.understanding import quoted_in_prompt
    p = "4. Technical sequence: Show an exploded-view animation where parts separate."
    assert quoted_in_prompt("4. Technical sequence: Show an exploded-view animation... — it is its own scene", p)
    assert not quoted_in_prompt("its own numbered sequence within the film", p)


MIXED_AD = {"detected_intent": "create_new", "production_type": ["commercial"], "confidence": 0.9,
            "scene": {"shot_structure": "multi_scene"},
            "motion": {"kinds": ["realistic", "programmatic_precise"], "programmatic_elements": ["exploded_view", "kinetic_text"],
                       "integration": "separate_segments", "overlay_elements": ["kinetic_text"]},
            "evidence": [{"field": "motion.integration", "source": "prompt", "quote": "4. Technical sequence: exploded view"}]}
MIXED_PROMPT = "An 8-scene car ad. 4. Technical sequence: exploded view. Slogan over the final rooftop shot."


async def test_editor_is_told_the_graphic_version_of_a_shot_wins():
    res, _ = await run(MIXED_AD, prompt=MIXED_PROMPT, mode="hybrid", proceed=True)
    assert res["status"] == "awaiting_approval", res.get("suggestion")
    s1, s2, s3 = res["plan"]["steps"]
    assert "motion.overlay_graphics" in s2["capabilities_used"]
    assert "return those shots" in s2["inputs"][0]["use"]
    uses = {i["source"]: i["use"] for i in s3["inputs"] if i["source"].startswith("step:")}
    assert "replaces this one" in uses["step:s1"] and "instead of the plain shots" in uses["step:s2"]
    assert "never use both" in s3["instructions"]
    assert not [i for i in res["issues"] if i["code"] == "ambiguous_shot_versions"]


async def test_validator_blocks_a_join_without_version_rules():
    res, _ = await run(MIXED_AD, prompt=MIXED_PROMPT, mode="hybrid", proceed=True)
    plan = copy.deepcopy(res["plan"])
    for i in plan["steps"][-1]["inputs"]:
        i.pop("use", None)
    issues = validator.validate(plan, res["understanding"], "hybrid", {})
    assert any(i["code"] == "ambiguous_shot_versions" and i["level"] == "error" for i in issues)


async def test_exploded_view_in_a_story_is_its_own_scene_even_if_ai_is_unsure():
    b = {**MIXED_AD, "motion": {**MIXED_AD["motion"], "integration": "unspecified",
                                "programmatic_elements": ["exploded_view"]}, "evidence": []}
    res, _ = await run(b, prompt=MIXED_PROMPT, mode="hybrid", proceed=True)
    caps = {r["capability"] for r in res["understanding"]["requirements"]}
    assert {"edit.assemble_clips", "motion.overlay_graphics", "motion.kinetic_typography"} <= caps
    assert "never use both" in res["plan"]["steps"][-1]["instructions"]


async def test_second_look_before_not_possible_can_fix_an_assumption():
    guessed = {"detected_intent": "recreate_reference", "confidence": 0.9}  # AI assumed a recreation
    llm = FakeLLM(guessed)
    llm.rechecked = {"detected_intent": "create_new", "confidence": 0.9}  # re-read: a plain new clip
    res = await pipeline.analyze(prompt="A clip like a classic ad", mode="normal", assets={}, perception={},
                                 answers={}, chat=llm)
    assert llm.calls[:2] == ["brief", "brief.recheck"]
    assert res["status"] == "awaiting_approval"


async def test_second_look_keeps_a_real_limit():
    res, calls = await run({"detected_intent": "recreate_reference", "confidence": 0.9}, prompt="Recreate this ad")
    assert calls == ["brief", "brief.recheck"]
    assert res["status"] == "not_possible" and "Checked twice" in res["suggestion"]


# ---- "uploads" question policy: only ask for files; everything else uses defaults

async def test_uploads_policy_does_not_ask_about_conflicts(monkeypatch):
    from router_core import config
    monkeypatch.setattr(config, "ASK_POLICY", "uploads")
    b = {"detected_intent": "create_new", "confidence": 0.9,
         "scene": {"shot_structure": "single_continuous", "scene_count": 5}}
    res, _ = await run(b, mode="hybrid")
    assert res["questions"] == [] and res["status"] != "needs_clarification"
    assert any("Planned as the request is written" in a for a in res["understanding"]["assumptions"])


async def test_uploads_policy_does_not_ask_the_ai_questions(monkeypatch):
    from router_core import config
    monkeypatch.setattr(config, "ASK_POLICY", "uploads")
    b = {"detected_intent": "unclear", "confidence": 0.4,
         "missing_info": [{"id": "q1", "question": "Which style?", "blocking": True, "affects": "routing", "options": ["A", "B"]}]}
    res, _ = await run(b, prompt="a video")
    assert res["questions"] == []
    assert any(d["question"] == "Which style?" and not d["blocking"] for d in res["open_details"])


async def test_uploads_policy_still_asks_for_mentioned_files(monkeypatch):
    from router_core import config
    monkeypatch.setattr(config, "ASK_POLICY", "uploads")
    res, _ = await run({"detected_intent": "create_new", "confidence": 0.9, "refers_to_uploads": True},
                       prompt="Animate my uploaded photo")
    assert res["status"] == "needs_clarification"
    assert [q["kind"] for q in res["questions"]] == ["upload"]


async def test_uploads_policy_asks_for_the_input_the_only_capable_app_needs(monkeypatch):
    from router_core import config
    monkeypatch.setattr(config, "ASK_POLICY", "uploads")
    b = {"detected_intent": "edit_existing", "production_type": ["editing"], "confidence": 0.9,
         "editing": {"operations": ["captions"]}}
    res, _ = await run(b, prompt="Add subtitles to my video", mode="hybrid")
    assert res["status"] == "needs_clarification"
    assert all(q["kind"] == "upload" for q in res["questions"])


async def test_a_recheck_that_guts_the_brief_is_discarded():
    llm = FakeLLM({"detected_intent": "recreate_reference", "confidence": 0.9})
    llm.rechecked = {"detected_intent": "unclear", "confidence": 0.3}  # the re-read threw everything away
    res = await pipeline.analyze(prompt="Recreate this ad", mode="normal", assets={}, perception={}, answers={}, chat=llm)
    assert res["status"] == "not_possible"
    assert res["unsupported"] and "Checked twice" in res["suggestion"]


async def test_auto_judge_reorders_the_recommendation():
    res, _ = await run({"detected_intent": "create_new", "confidence": 0.9}, mode="auto", judge_choice=1)
    assert [o["apps"] for o in res["options"]][0] == ["storyboard-studio"] and apps(res) == ["storyboard-studio"]


async def test_auto_with_one_option_skips_the_judge():
    res, calls = await run({"detected_intent": "create_new", "audio": {"subtitles": "requested"}}, mode="auto")
    assert calls == ["brief", "review"] and len(res["options"]) == 1


async def test_app_mode_uses_only_the_picked_app(monkeypatch):
    monkeypatch.setitem(pipeline.APP_MODES, "storyboard", "storyboard-studio")
    res, _ = await run({"detected_intent": "create_new", "confidence": 0.9}, mode="storyboard")
    assert res["status"] == "awaiting_approval" and apps(res) == ["storyboard-studio"]


async def test_app_mode_explains_when_the_app_cannot_do_it(monkeypatch):
    monkeypatch.setitem(pipeline.APP_MODES, "motion", "programmatic-motion-studio")
    res, _ = await run({"detected_intent": "create_new", "audio": {"subtitles": "requested"}}, mode="motion")
    assert res["status"] == "not_possible" and "Switch to Auto" in res["suggestion"]
    assert res["suggestion"].startswith("Programmatic Motion Studio can't make this on its own")
