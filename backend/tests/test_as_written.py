"""The user's own scenes are used exactly as written: no enhancer, no Story Intelligence, no planner rewrite."""

import asyncio

import pytest

from engines import as_written, story_engine as se

SCENES = """Scene 1 (5s): A red fox on a snowy ridge at dawn, wide shot, soft pink light.
Motion: slow push in as the fox turns its head.
Narration: Every winter, the fox walks alone.

Scene 2: Close-up of the fox's paw pressing into fresh snow.
Duration: 4s

Scene 3 - The fox leaps into a snowdrift, powder bursting around it."""


def test_numbered_scenes_are_read_word_for_word():
    s = as_written.parse(SCENES)
    assert [x["n"] for x in s] == [1, 2, 3]
    assert s[0]["image"] == "A red fox on a snowy ridge at dawn, wide shot, soft pink light."
    assert s[0]["motion"] == "slow push in as the fox turns its head."
    assert s[0]["line"] == "Every winter, the fox walks alone." and s[0]["duration"] == 5
    assert s[1]["image"] == "Close-up of the fox's paw pressing into fresh snow." and s[1]["duration"] == 4
    assert s[2]["image"] == "The fox leaps into a snowdrift, powder bursting around it." and s[2]["motion"] == ""


def test_other_heading_styles():
    assert len(as_written.parse("1. A cat on a roof.\n2. The cat jumps.\n3) It lands on a car.")) == 3
    assert len(as_written.parse("Shot 1: a door opens\nShot 2: a hand reaches in")) == 2
    bare = as_written.parse("Scene 1:\nA lighthouse at night.\nScene 2:\nWaves crash below.")
    assert [x["image"] for x in bare] == ["A lighthouse at night.", "Waves crash below."]


def test_a_plain_idea_is_still_enhanced_unless_the_user_says_not_to():
    assert as_written.parse("a cat explores neon Tokyo at night") is None
    assert as_written.parse("Make episode 1 of a short drama about two lawyers") is None
    one = as_written.parse("A cat explores neon Tokyo at night, rain, reflections. Don't enhance this.")
    assert len(one) == 1 and one[0]["image"].startswith("A cat explores neon Tokyo")
    assert as_written.wanted("use it as written") and as_written.wanted("do not improve my prompt")


def test_the_story_panel_plan_keeps_the_text_and_spreads_the_length():
    intel = as_written.story_intel(as_written.parse(SCENES), total=15)
    sc = intel["plan"]["scenes"]
    assert intel["mode"] == "as_written" and [x["duration_s"] for x in sc] == [5, 4, 6]  # 15 - 5 - 4 = 6 for scene 3
    assert sc[0]["what_happens"] == "A red fox on a snowy ridge at dawn, wide shot, soft pink light."
    assert sc[0]["audio"] == {"type": "narration", "line": "Every winter, the fox walks alone.", "speaker": "Narrator"}
    assert sc[2]["actions"] == sc[2]["what_happens"]  # no motion text: the clip gets the same words


def test_the_engine_plan_and_clip_prompts_are_verbatim():
    intel = as_written.story_intel(as_written.parse(SCENES))
    p = se.written_plan(intel, {**se.QUALITY["full"]})
    s0, s2 = p["scenes"][0], p["scenes"][2]
    assert s0["image_prompt"] == "A red fox on a snowy ridge at dawn, wide shot, soft pink light."
    assert s0["script"] == "Every winter, the fox walks alone." and s0["voice"] == "narration"
    q = se.QUALITY["full"]
    assert se._motion("pltt-flow-video-2", s0, q) == "slow push in as the fox turns its head."
    assert se._motion("pltt-flow-video-2", s2, q) == "The fox leaps into a snowdrift, powder bursting around it."
    assert se._motion("pltt-flow-video-2", s0, q, fix="the fox morphs").endswith("slow push in as the fox turns its head.")
    assert p["genre"] == "as_written" and len(p["scenes"]) == 3


def test_the_test_quality_scene_cap_still_applies():
    intel = as_written.story_intel(as_written.parse(SCENES))
    assert len(se.written_plan(intel, se.QUALITY["test"])["scenes"]) == se.QUALITY["test"]["max_scenes"]


def test_analysis_skips_the_enhancer_and_story_intelligence(monkeypatch):
    pytest.importorskip("palette_sdk")
    import main
    run = {"id": "r1", "prompt": SCENES, "events": [], "assets": {}, "settings": {}, "mode": "auto"}

    async def boom(*a, **k):
        raise AssertionError("must not be called for scenes written by the user")
    monkeypatch.setattr(main, "_enhance", boom)
    monkeypatch.setattr(main, "_story_intelligence", boom)
    monkeypatch.setattr(main, "_recipe_for", boom)
    asyncio.run(main._auto_enhance(None, run))
    assert run["enhanced_prompt"] is None and "exactly as written" in run["enhance_note"]
    assert run["story_intel"]["mode"] == "as_written" and len(run["story_intel"]["plan"]["scenes"]) == 3
    from engines.story_intel import direct
    assert direct.applies(run)  # rendered straight from the written scenes (Flow Video), no router rewrite


def test_the_engine_draws_and_animates_the_exact_words(monkeypatch, tmp_path):
    from pathlib import Path
    images, clips = [], []

    async def fake_fal(fn, key, m, prompt, **kw):
        assert fn.__name__ == "generate_image", "clips go through _clip in this test"
        images.append(prompt)
        return {"images": [{"url": f"img{len(images)}"}]}

    async def fake_clip(app, i, n, s, still, ar, q, key, progress, **kw):
        clips.append(se._motion(app["app_id"], s, q))
        return f"clip{i}.mp4", "kling_4_0"

    async def dl(url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("v")
        return str(dest)

    async def nothing(*a, **k):
        return []

    async def never(*a, **k):
        raise AssertionError("no planner / critic / locations for scenes written by the user")

    class Done(BaseException):
        pass

    async def stop(*a, **k):
        raise Done

    async def progress(_):
        pass
    for name, fn in {"_fal": fake_fal, "_clip": fake_clip, "_download": dl, "scene_issues": nothing,
                     "clip_issues": nothing, "consistency_issues": nothing, "plan": never, "critique": never,
                     "plan_locations": never, "join": stop}.items():
        monkeypatch.setattr(se, name, fn)
    monkeypatch.setattr(se, "fal_key", lambda: "k")
    monkeypatch.setattr(se.music, "wanted", lambda *a, **k: False)
    monkeypatch.setattr(se, "quality", lambda *a, **k: {**se.QUALITY["full"], "voice": False})
    intel = as_written.story_intel(as_written.parse(SCENES))
    handoff = {"prompt": SCENES, "request": {"aspect_ratio": "16:9"}, "_story": intel}
    with pytest.raises(Done):
        asyncio.run(se.run({"name": "Flow Video", "app_id": "pltt-flow-video-2"}, handoff, {}, tmp_path, progress))
    assert sorted(images) == sorted(x["what_happens"] for x in intel["plan"]["scenes"])  # word for word, no look frame
    assert sorted(clips) == sorted(["slow push in as the fox turns its head.",
                                    "Close-up of the fox's paw pressing into fresh snow.",
                                    "The fox leaps into a snowdrift, powder bursting around it."])
