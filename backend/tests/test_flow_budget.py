"""Movie-like flow inside the film's length: moves between places get time, and a jump to a new place is caught."""
from engines import flow_budget as fb, planners
from engines.story_intel import validate


def test_the_budget_follows_the_length():
    short, medium, long_ = fb.flow_budget(15), fb.flow_budget(45), fb.flow_budget(90)
    assert "no separate travel scenes" in short and "pushes the door open" in short
    assert "about 7 s" in medium and "3-5 s" in medium  # 15 % of 45 s for the moves
    assert "about 18 s" in long_ and "stairs" in long_ and "establishing shot" in long_
    assert all("must add up to it" in x for x in (short, medium, long_)) and fb.flow_budget(0) == ""


OFFICE = "penthouse office at late night, rain-streaked window wall, glass desk"
STREET = "rainy street outside the tower, neon signs, wet asphalt"


def _s(env, **kw):
    return {"environment": env, "what_happens": "they talk", "start_state": "x", "end_state": "y", **kw}


def test_a_jump_to_a_new_place_is_flagged_unless_the_move_is_shown():
    jump = [_s(STREET), _s(OFFICE)]
    assert fb.teleports(jump, 45) == [2]
    walked = [_s(STREET), _s(OFFICE, start_state="he pushes the office door open and steps in")]
    assert fb.teleports(walked, 45) == []
    left = [_s(STREET, end_state="he walks into the tower lobby"), _s(OFFICE)]
    assert fb.teleports(left, 45) == []
    assert fb.teleports([_s(OFFICE), _s(OFFICE + ", by the door")], 45) == []  # same place: no move needed
    assert fb.teleports(jump, 15) == []  # a short film may simply cut


def test_the_linter_asks_for_the_move():
    plan = {"bible": {"characters": ["a"], "world": "w"}, "scenes": [_s(STREET, duration_s=5), _s(OFFICE, duration_s=5)]}
    issues = [i for i in validate.lint(plan, 45) if "no way in shown" in i["problem"]]
    assert issues and issues[0]["scene"] == 2 and "opening the door" in issues[0]["fix"]


def test_the_film_planner_gets_the_flow_rules():
    q = {"video_model": "seedance_2_0", "image_model": "nano_banana_pro", "audio": False}
    system, _ = planners.prompt_for("pltt-storyboard-video-maker", "a boy comes home", 60, "9:16", 3, 10, 12, [], q)
    assert "never teleporting" in system and "FLOW WITHIN THE LENGTH (60 s" in system
