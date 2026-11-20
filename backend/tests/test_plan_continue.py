"""A plan cut off by the answer limit is continued until every planned scene is written (the delivery-boy drama
stopped in the middle of scene 11 of 19 and was filmed without its climax)."""
import asyncio

from engines import story_intel as si
from engines.story_intel import agents, drama


def _scene(n, done=True):
    s = {"title": f"S{n}", "start_state": "a", "prompt": {"subject": "x"}}
    if done:
        s.update(end_state="b", prompt={"subject": "x", "ending": "y"})
    return s


def test_a_cut_off_plan_is_continued_to_the_planned_ending(monkeypatch):
    calls = []

    async def fake(msgs, stage, tokens=8000, effort=None):
        calls.append((stage, msgs[-1]["content"]))
        if stage == "story.drama":  # 5 planned, cut off in the middle of scene 3
            return {"structure": [{"stage": "hook", "scenes": [1, 2]}, {"stage": "climax", "scenes": [3, 4, 5]}],
                    "cliffhanger": "the door opens", "scenes": [_scene(1), _scene(2), _scene(3, done=False)]}
        if len(calls) == 2:
            return {"scenes": [_scene(3), _scene(4, done=False)]}  # cut off again
        return {"scenes": [_scene(4), _scene(5)]}

    monkeypatch.setattr(si, "_json", fake)
    plan = asyncio.run(si.architect("p", {"category": "short_drama", "duration_s": 60}, None, [], "9:16"))
    assert [s["n"] for s in plan["scenes"]] == [1, 2, 3, 4, 5] and all(not si.cut_off(s) for s in plan["scenes"])
    assert calls[1][0] == "story.drama.continue" and "3 to 5" in calls[1][1] and "scene 3 was cut off" in calls[1][1]
    assert "4 to 5" in calls[2][1] and len(calls) == 3


def test_a_complete_plan_is_not_continued(monkeypatch):
    calls = []

    async def fake(msgs, stage, tokens=8000, effort=None):
        calls.append(stage)
        return {"structure": [{"stage": "hook", "scenes": [1, 2]}], "scenes": [_scene(1), _scene(2)]}

    monkeypatch.setattr(si, "_json", fake)
    asyncio.run(si.architect("p", {"category": "short_drama", "duration_s": 60}, None, [], "9:16"))
    assert calls == ["story.drama"]


def test_the_writers_are_told_to_show_arrivals():
    assert "pushing the door open" in agents.ARCHITECT and "never skip HOW the characters got there" in drama.RULES
    assert "FLOW:" in agents.VALIDATOR
