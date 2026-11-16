"""ChefVision food engine: library, recipe validation, planner rules, prompt builders and the quality gate."""

import json

from engines import food, planners
from engines.food import library, prompts, recipe, validate

Q = {"video_model": "kling_3_0_pro", "image_model": "nano_banana_pro", "audio": True}

RECIPE = recipe.clean({
    "is_cooking_process": True, "dish": "Cheese omelette", "cuisine": "Continental", "categories": ["Continental", "Bogus"],
    "ingredients": [{"name": "eggs", "quantity": "3", "initial_state": "whole in a carton"},
                    {"name": "cheese", "quantity": "40 g", "initial_state": "a block"}],
    "cookware": ["non-stick pan"], "utensils": ["whisk", "spatula"],
    "set": {"hands": "light skin, short natural nails, rolled grey sleeves", "light": "soft window light from the left"},
    "workflow": [{"stage": "Crack the eggs", "actions": ["crack_egg", "teleport"], "ingredients": ["eggs"]},
                 {"stage": "Whisk", "actions": ["whisk"]}, {"stage": "Grate the cheese", "actions": ["grate"]},
                 {"stage": "Cook in the pan", "actions": ["heat_pan", "pour_liquid", "fold"]},
                 {"stage": "Plate and taste", "actions": ["lift_transfer", "plate", "first_bite"]}]})


def scene(n, stage, action, start, end, state, **kw):
    return {"title": f"S{n}", "duration": 4, "recipe_stage": stage, "beat": stage, "action": action,
            "start_state": start, "end_state": end, "ingredients": ["3 eggs"], "hands": ["left hand holds the egg"],
            "camera": "overhead close-up, static", "state": state, "image_prompt": start,
            "props": ["1 glass bowl", "3 eggs", "2 hands"],
            "motion": {"starting_state": start, "action_sequence": ["the hand moves", "contact", "result"],
                       "physics": {"gravity": "falls"}, "food_response": "changes", "ending_state": end}, **kw}


def test_recipe_is_validated_against_the_library_and_categories():
    assert RECIPE["categories"] == ["Continental"]
    assert RECIPE["workflow"][0]["actions"] == ["crack_egg"] and RECIPE["unknown_actions"] == ["teleport"]
    assert recipe.actions_used(RECIPE)[:3] == ["crack_egg", "whisk", "grate"]
    assert recipe.is_candidate("a cooking reel of biryani", None) and recipe.is_candidate("anything", "food")
    assert not recipe.is_candidate("a girl finds a lost cat in the rain", "cinematic")


def test_planner_gets_only_this_recipes_actions_and_replaces_the_story_rules():
    system, _ = planners.prompt_for("pltt-storyboard-video-maker", "make an omelette", 45, "9:16", 3, 10, 15, [], Q,
                                    "food", None, RECIPE)
    assert "THE RECIPE IS THE STORY" in system and "STORY FIRST" not in system
    assert "crack_egg:" in system and "whisk:" in system and "knead:" not in system and "deep_fry:" not in system
    assert "Cooking process (ChefVision)" in system and "Default voice for this genre: none" in system
    assert "Use 8-12 scenes by default" in system and '"Cheese omelette"' in system


def test_state_progression_ranks():
    assert library.rank("whole in a carton") == 0 and library.rank("sliced") == 3
    assert library.rank("uncooked rice") == 0 and library.rank("golden and crisp") == 7
    assert library.rank("plated and garnished") == 10 and library.rank("in the pan, sizzling") == 6


def test_quality_gate_catches_generic_regressions_instant_cooking_and_forgotten_plating():
    scenes = [
        scene(1, "Crack the eggs", "0-2 s: the left hand taps the egg on the bowl rim and opens it", "3 eggs whole beside a glass bowl",
              "3 eggs cracked into the glass bowl", {"eggs": "cracked"}),
        scene(2, "Whisk", "camera slowly rotates around the bowl", "3 eggs cracked in the glass bowl",
              "the eggs in the glass bowl", {"eggs": "whole, uncracked in the shell"}),
        scene(3, "Cook in the pan", "the pan of batter is baked in the oven until golden", "raw batter in the pan",
              "golden cooked omelette", {"eggs": "cooked"}),
        scene(4, "Grate the cheese", "the hand grates cheese", "a block of cheese on a board", "grated cheese",
              {"cheese": "grated"}),
    ]
    issues = validate.check(scenes, RECIPE, 15)
    text = " ".join(issues)
    assert "use at least 8" in text
    assert "Scene 2: the action is only a camera move" in text
    assert "Scene 2: eggs was cracked in scene 1" in text
    assert "Scene 3: the food goes from" in text and "time_jump" in text
    assert "Scene 4: its start_state does not continue scene 3" in text
    assert "plate" in text and "first bite" in text
    ok = [scene(1, "Crack the eggs", "0-2 s: the left hand taps the egg and opens the shell", "3 eggs beside the bowl",
                "3 eggs cracked into the bowl", {"eggs": "cracked"})]
    assert not [i for i in validate.check(ok, {"workflow": []}, 1) if "Scene 1" in i]


def test_image_and_motion_json_follow_the_chefvision_format():
    s = scene(1, "Crack the eggs", "the left hand taps the egg and opens it", "3 eggs beside a glass bowl",
              "eggs in the bowl, shell halves in the fingers", {"eggs": "cracked"}, texture="glossy whites",
              continuity="same glass bowl")
    food.finalize([s], RECIPE, "9:16")
    ij, mj = s["image_json"], s["motion_json"]
    assert set(ij) >= {"scene_number", "scene_purpose", "shot_type", "subject", "action_moment", "food_texture",
                       "environment", "lighting", "composition", "continuity", "realism_constraints", "style",
                       "aspect_ratio"}
    assert set(mj) >= {"scene_number", "duration_seconds", "camera", "starting_state", "action_sequence", "physics",
                       "food_response", "ending_state", "continuity_lock", "motion_style", "negative_constraints"}
    assert ij["aspect_ratio"] == "9:16" and "rolled grey sleeves" in ij["subject"] and ij["lighting"]
    assert json.loads(s["image_prompt"].split("\n", 1)[1])["scene_number"] == 1  # the image model gets valid JSON
    assert "1) the hand moves." in s["motion_text"] and len(s["motion_text"]) <= prompts.MOTION_MAX_CHARS
    assert validate.json_valid([ij, mj])


def test_combining_a_cooked_part_is_not_a_reversion_but_a_raw_return_is():
    rec = {"workflow": []}
    melt = scene(1, "Melt", "the hand stirs the chocolate as it melts", "chocolate in a bowl", "melted chocolate in the bowl",
                 {"chocolate": "melted and cooked"})
    fold = scene(2, "Fold", "the spatula folds the chocolate into the eggs", "melted chocolate in the bowl",
                 "chocolate folded into the batter", {"chocolate": "folded into the batter"})
    raw = scene(3, "Oops", "the hand lifts the chicken", "chocolate batter", "raw chicken on the board",
                {"chocolate": "raw"})
    text = " ".join(validate.check([melt, fold], rec, 2))
    assert "never goes back" not in text
    assert "a cooked ingredient never goes back" in " ".join(validate.check([melt, fold, raw], rec, 3))


def test_overloaded_clip_is_split():
    from engines.food import validate
    busy = scene(1, "Plate", "0-1 s: lift; 1-2 s: plate; 2-3 s: garnish; 3-4 s: fork pulls the chicken", "a", "b",
                 {"rice": "plated"})
    assert any("timed beats" in i for i in validate.check([busy], {"workflow": []}, 1))
    calm = scene(1, "Plate", "0-2 s: the spatula lifts a portion; 2-4 s: it lands on the plate", "a", "b",
                 {"rice": "plated"})
    assert not any("timed beats" in i for i in validate.check([calm], {"workflow": []}, 1))
