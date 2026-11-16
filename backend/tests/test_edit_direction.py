"""The motion-design direction is decided with the script and carried to the effects and graphics passes."""

import asyncio
import json

from engines import finishing, motion_design, planners, story_engine


def test_planner_prompt_asks_for_an_edit_ready_film_and_direction():
    q = {"video_model": "x", "image_model": "y", "audio": False}
    system, _ = planners.prompt_for("pltt-storyboard-video-maker", "UGC unboxing of earbuds", 30, "9:16", 3, 8, 6,
                                    [], q, "unboxing")
    assert "EDIT-READY FILM" in system and '"edit_direction"' in system
    assert 'the genre suggests\n   "product"' in system or '"product"' in system


def test_clean_direction_keeps_valid_parts_only():
    raw = {"treatment": "spaceship", "concept": "calm in the noise", "design": {"accent": "#1ec8ff", "bad": "x"},
           "energy_curve": [{"scene": 1, "beat": "hook", "what": "box lands"}, {"scene": 9, "beat": "peak"},
                            {"scene": 2, "beat": "dance"}],
           "graphic_moments": [{"scene": 2, "kind": "callout", "text": "ANC"}, {"scene": 2, "kind": "explosion"}],
           "effect_moments": [{"scene": 3, "idea": "pop-word on the name"}, {"scene": 0, "idea": "x"}]}
    d = motion_design.clean_direction(raw, "unboxing", 3)
    assert d["treatment"] == "product"  # unknown -> the genre's treatment
    assert d["design"] == {"accent": "#1ec8ff"}
    assert [c["scene"] for c in d["energy_curve"]] == [1]
    assert [g["kind"] for g in d["graphic_moments"]] == ["callout"]
    assert [e["scene"] for e in d["effect_moments"]] == [3]
    assert motion_design.clean_direction(None, "cinematic", 2)["treatment"] == "cinematic"


def test_planned_moments_are_timed_for_the_effects_editor():
    d = motion_design.clean_direction({"treatment": "speech_led", "design": {"accent": "#ff3366"},
                                       "energy_curve": [{"scene": 2, "beat": "peak", "what": "first sound"}],
                                       "effect_moments": [{"scene": 1, "idea": "headline-box hook"}]}, "ugc_ad", 3)
    text = motion_design.planned_moments(d, story_engine.scene_spans([{"duration": 4}, {"duration": 5}, {"duration": 6}]))
    assert "scene 2 (4.0-9.0 s): peak" in text and "scene 1 (0.0-4.0 s)" in text and "#ff3366" in text
    assert motion_design.planned_moments({}, []) == ""


def test_graphics_director_starts_from_the_planned_direction(monkeypatch):
    seen = {}

    async def fake_chat(model, messages, **kw):
        seen["info"] = json.loads(messages[1]["content"][0]["text"].split("\n\nThe frames follow")[0])
        seen["system"] = messages[0]["content"]
        return {"add_graphics": False}

    monkeypatch.setattr(finishing.llm, "chat_json", fake_chat)
    direction = {"treatment": "product", "concept": "precision", "design": {"accent": "#00e0ff"}}
    plan = {"genre": "ugc_ad", "scenes": [{"title": "a"}], "edit_direction": direction}
    asyncio.run(finishing.direct("brief", plan, [(0.0, 4.0)], [], ["line"]))
    assert seen["info"]["planned_direction"] == direction and seen["info"]["treatment_hint"] == "product"
    assert "PLANNED DIRECTION" in seen["system"]
