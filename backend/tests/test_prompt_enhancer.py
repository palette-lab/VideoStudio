"""The composer's Enhance button: the user's enhancer spec, the composer context, and a clean output."""

from engines import prompt_enhancer as pe


def test_system_is_the_users_spec_plus_the_composer_context():
    m = pe.messages("a cat steals a fish", 20, "9:16", ["cat.jpg"])
    assert m[0]["content"].startswith("# Cinematic Video Prompt Enhancer")
    assert "Return ONLY the enhanced video prompt." in m[0]["content"]
    assert "Length: 20 seconds" in m[0]["content"] and "Format: 9:16" in m[0]["content"] and "cat.jpg" in m[0]["content"]
    assert m[1] == {"role": "user", "content": "a cat steals a fish"}
    assert "not set" in pe.messages("x", None, None, [])[0]["content"]


def test_output_is_only_the_prompt():
    assert pe.clean("```\nA cat leaps.\n```") == "A cat leaps."
    assert pe.clean("**Enhanced prompt:**\nA cat leaps onto the counter.") == "A cat leaps onto the counter."
    assert pe.clean('"A cat leaps."') == "A cat leaps."
    assert pe.clean("Scene 1: A cat waits.\nScene 2: It leaps.") == "Scene 1: A cat waits.\nScene 2: It leaps."


def test_auto_enhance_only_short_creative_ideas():
    assert pe.should_auto_enhance("a cat steals a fish from the kitchen") is None
    assert pe.should_auto_enhance("a 15 s reel for my serum with captions") is None  # captions as a feature
    assert pe.should_auto_enhance("add captions to my video")  # an edit of their own video
    assert pe.should_auto_enhance("an animated bar chart of Q3 revenue by region") is None  # data now enhanced
    assert pe.should_auto_enhance("sales grew 40% this year") is None
    assert pe.should_auto_enhance(" ".join(["word"] * 60)) is None  # detailed briefs are split into detailed scenes
    long_msgs = pe.auto_messages(" ".join(["word"] * 60), None, None, [])
    assert "ALREADY DETAILED" in long_msgs[0]["content"] and "SCENE-BY-SCENE" in long_msgs[0]["content"]
    assert pe.should_auto_enhance("Create a hyper-realistic cinematic cooking video of freshly made chicken biryani") is None
    assert pe.should_auto_enhance("") == "no text to enhance"


def test_agent_is_picked_and_parsed_with_safe_fallbacks():
    m = pe.auto_messages("make paneer butter masala", 45, "9:16", [])
    assert m[0]["content"].startswith("# Cinematic Video Prompt Enhancer")  # the user's spec stays the base
    assert '"agent": "food | motion | product | presenter | explainer | drama | story"' in m[0]["content"]
    out = pe.parse_auto({"agent": "presenter", "why": "talks to camera", "enhanced_prompt": "A creator..."}, "x")
    assert out == {"agent": "presenter", "label": "Presenter agent", "why": "talks to camera", "prompt": "A creator..."}
    assert pe.parse_auto({"agent": "bogus", "enhanced_prompt": "P"}, "x")["agent"] == "story"
    assert pe.parse_auto({}, "  my words ")["prompt"] == "my words"


def test_house_style_rules_reach_every_enhancement():
    sys_msg = pe.auto_messages("a cat steals a fish", 20, "9:16", [])[0]["content"]
    assert sys_msg.startswith("# Cinematic Video Prompt Enhancer")  # the user's spec stays first and verbatim
    assert "HOUSE STYLE" in sys_msg
    for rule in ("Photoreal", "Never glass, chrome", "fresh look for THIS video", "Characters stay identical",
                 "No text in the picture", "Text layer:", "fictional host"):
        assert rule in sys_msg
    assert sys_msg.index("HOUSE STYLE") < sys_msg.index("OUTPUT FORMAT FOR THIS APP")  # JSON format stays last
    assert "HOUSE STYLE" in pe.auto_messages(" ".join(["word"] * 60), None, None, [])[0]["content"]


def test_food_agent_gets_the_understood_recipe():
    rec = {"dish": "Paneer butter masala", "cuisine": "Indian", "categories": ["Indian"], "diet": "vegetarian",
           "servings": "2", "ingredients": [{"name": "paneer", "quantity": "200 g", "initial_state": "a block"}],
           "cookware": ["kadai"], "utensils": ["knife"], "set": {}, "hero_moments": [], "sensory": [],
           "assumptions": [], "workflow": [{"stage": "Cut the paneer", "actions": ["dice"]}]}
    m = pe.auto_messages("paneer butter masala", 45, "9:16", [], rec)
    assert "THE RECIPE (already understood" in m[0]["content"] and "Cut the paneer" in m[0]["content"]
    assert pe.parse_auto({"agent": "story", "enhanced_prompt": "P"}, "x", rec)["agent"] == "food"


def test_video_intelligence_agent_uses_the_master_prompt_and_live_apps(monkeypatch):
    from engines import video_intelligence as vi
    from router_core import config, registry
    from tests.conftest import REAL_REGISTRY
    monkeypatch.setattr(config, "REGISTRY_DIR", REAL_REGISTRY)
    registry.apps.cache_clear(); registry.capabilities.cache_clear()
    m = vi.messages("explain how AI changes the workplace", 30, "16:9", [], "first pass")
    assert m[0]["content"].startswith("# VIDEO INTELLIGENCE")  # the user's master prompt, verbatim
    assert "Motion Studio:" in m[0]["content"] and "Storyboard & Video:" in m[0]["content"]  # the real registry
    assert "Palette Video Smart" not in m[0]["content"]  # a disabled app is never offered
    out = vi.clean({"style_lock": "deep navy #07111F, cyan #00D4FF", "enhanced_prompt": "Scene 1 ...",
                    "three_d": "css_2_5d", "categories": ["Motion Graphics Explainer"]}, "x")
    assert out["prompt"].startswith("STYLE LOCK: deep navy #07111F") and out["three_d"] == "css_2_5d"
    assert vi.clean({"three_d": "holograms"}, " words ")["three_d"] == "none"
    registry.apps.cache_clear(); registry.capabilities.cache_clear()


def test_motion_agent_is_offered_and_data_videos_are_enhanced_now():
    m = pe.auto_messages("explain how AI changes the workplace", None, None, [])
    assert '"motion"' in m[0]["content"] and 'even when the user never wrote "3D"' in m[0]["content"]
    assert pe.should_auto_enhance("an animated bar chart of Q3 revenue by region") is None  # programmatic: enhanced
    assert pe.should_auto_enhance("add captions to my video")  # edits of their own video still skip


def test_engines_honour_the_style_lock():
    from engines import motion_engine, planners
    assert "STYLE LOCK" in motion_engine.SYSTEM and "rotateX/rotateY" in motion_engine.SYSTEM
    q = {"video_model": "kling_3_0_pro", "image_model": "nano_banana_pro", "audio": True}
    locked, _ = planners.prompt_for("pltt-storyboard-video-maker", "STYLE LOCK: navy #07111F\n\nScene 1", 20, "16:9",
                                    3, 10, 6, [], q, "advertisement")
    plain, _ = planners.prompt_for("pltt-storyboard-video-maker", "a shoe ad", 20, "16:9", 3, 10, 6, [], q,
                                   "advertisement")
    assert "Copy the STYLE LOCK into the bible" in locked and "Copy the STYLE LOCK" not in plain
