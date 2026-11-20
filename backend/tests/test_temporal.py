"""Universal Temporal Intelligence: event graph, plan checks, continuity controller, pacing, clip verdicts.

The ten scenarios are graphs as the extractor returns them (the extractor itself is one LLM call; what is tested here
is everything the app does with its answer, the same for every kind of film)."""
import asyncio

import pytest

from engines import story_engine, temporal
from engines.temporal import controller, graph as G, pacing


def ev(i, desc, req=(), dur=3, changes=(), essential=True, par=(), cause=(), loc="L1", chars=("C1",), objs=(),
       cond="", done=""):
    return {"id": f"E{i}", "seq": i, "description": desc, "requires": list(req), "caused_by": list(cause),
            "parallel_with": list(par), "duration_s": dur, "essential": essential, "location": loc,
            "characters": list(chars), "objects": list(objs), "condition": cond, "done_when": done or desc,
            "changes": [dict(zip(("entity", "attr", "from", "to"), c)) for c in changes]}


def graph(events, chars=None, objs=None, locs=None):
    return G.normalize({"entities": {"characters": chars or [{"id": "C1", "name": "Hero", "start": {}}],
                                     "objects": objs or [], "locations": locs or [{"id": "L1", "name": "place"}]},
                        "events": events})


def scenes(*groups, env=None):
    return [{"n": k, "events": list(g), "environment": (env or {}).get(k, "the same room with a window and a table")}
            for k, g in enumerate(groups, start=1)]


# ---- the ten scenarios ------------------------------------------------------------------------------------------

def race():
    return graph([ev(1, "the hare boasts at the start line", chars=("C1", "C2")),
                  ev(2, "the race starts: both leave the line", [1], chars=("C1", "C2")),
                  ev(3, "the hare dashes ahead and out of sight", [2]),
                  ev(4, "the tortoise keeps a slow steady pace", [2], par=(3,), chars=("C2",)),
                  ev(5, "the hare lies under a tree and falls asleep", [3]),
                  ev(6, "the tortoise passes the sleeping hare", [4, 5], chars=("C2",)),
                  ev(7, "the hare wakes and sprints", [6]),
                  ev(8, "the tortoise crosses the finish line first", [6], chars=("C2",)),
                  ev(9, "the hare arrives too late", [7, 8], cause=(8,))],
                 chars=[{"id": "C1", "name": "Hare", "start": {}}, {"id": "C2", "name": "Tortoise", "start": {}}])


def sit_down():
    return graph([ev(1, "she walks down the corridor to the door", changes=[("C1", "location", "corridor", "door")]),
                  ev(2, "she turns the handle and opens the door", [1], objs=("O1",),
                     changes=[("O1", "state", "closed", "open")]),
                  ev(3, "she steps into the room", [2], changes=[("C1", "location", "door", "room")]),
                  ev(4, "she crosses to the chair", [3], changes=[("C1", "position", "", "by the chair")]),
                  ev(5, "she sits down", [4], changes=[("C1", "pose", "standing", "sitting")])],
                 chars=[{"id": "C1", "name": "Mira", "start": {"location": "corridor", "pose": "standing"}}],
                 objs=[{"id": "O1", "name": "door", "start": {"state": "closed"}}])


def cooking():
    return graph([ev(1, "the ingredients are laid out on the counter", objs=("O1",)),
                  ev(2, "she chops the onions", [1]), ev(3, "she heats oil in the pan", [1], par=(2,)),
                  ev(4, "the onions go into the pan and sizzle", [2, 3]), ev(5, "she adds the rice and stirs", [4]),
                  ev(6, "the pot is covered and simmers", [5], dur=4), ev(7, "she plates the dish", [6]),
                  ev(8, "she serves it at the table", [7], cause=(7,))])


def drama():
    return graph([ev(1, "the boss throws coins at the courier's feet", chars=("C1", "C2")),
                  ev(2, "the courier refuses to pick them up", [1], cause=(1,), chars=("C2",)),
                  ev(3, "the courier sees the photo on the desk", [2], chars=("C2",)),
                  ev(4, "he opens his locket: the same photo", [3], chars=("C2",)),
                  ev(5, "the boss goes pale", [4], cause=(4,), chars=("C1",)),
                  ev(6, "the boss kneels and picks up the coins", [5], chars=("C1",))],
                 chars=[{"id": "C1", "name": "Boss", "start": {}}, {"id": "C2", "name": "Courier", "start": {}}])


def product_ad():
    return graph([ev(1, "the bottle stands closed on the sink", objs=("O1",)),
                  ev(2, "she twists the cap off", [1], objs=("O1",), changes=[("O1", "state", "closed", "open")]),
                  ev(3, "a drop falls onto her fingertip", [2]), ev(4, "she pats it into her cheek", [3]),
                  ev(5, "her skin glows", [4], cause=(4,)),
                  ev(6, "the cap goes back on", [5], changes=[("O1", "state", "open", "closed")])],
                 objs=[{"id": "O1", "name": "serum bottle", "start": {"state": "closed"}}])


def puzzle():
    return graph([ev(1, "the question is posed"), ev(2, "clue 1: the clock stopped at 9", [1]),
                  ev(3, "clue 2: muddy boots by the door", [1]), ev(4, "a pause to think", [2, 3], essential=False),
                  ev(5, "the reveal: the gardener", [2, 3]), ev(6, "the reasoning, clue by clue", [5])])


def motion_graphics():
    return graph([ev(1, "a cube appears on the plinth", objs=("O1",), chars=()),
                  ev(2, "the cube splits into eight smaller cubes", [1], objs=("O1",), chars=()),
                  ev(3, "the cubes reassemble into a sphere", [2], objs=("O1",), chars=(),
                     changes=[("O1", "state", "", "sphere")]),
                  ev(4, "the sphere opens to show the chip inside", [3], objs=("O1",), chars=())])


def two_places():
    return graph([ev(1, "he locks his flat and leaves", loc="L1", changes=[("C1", "location", "flat", "street")]),
                  ev(2, "he walks down the street to the cafe", [1], loc="L2",
                     changes=[("C1", "location", "street", "cafe door")]),
                  ev(3, "he enters the cafe and orders", [2], loc="L3",
                     changes=[("C1", "location", "cafe door", "cafe")])],
                 chars=[{"id": "C1", "name": "Tom", "start": {"location": "flat"}}])


def simultaneous():
    return graph([ev(1, "the alarm rings", chars=()), ev(2, "the mother runs to the kitchen", [1], par=(3,)),
                  ev(3, "the child hides under the table", [1], par=(2,), chars=("C2",)),
                  ev(4, "the mother finds the child", [2, 3], chars=("C1", "C2"))],
                 chars=[{"id": "C1", "name": "Mother", "start": {}}, {"id": "C2", "name": "Child", "start": {}}])


def long_story():
    return graph([ev(i, f"step {i} of the journey", [i - 1] if i > 1 else [], dur=5) for i in range(1, 31)])


SCENARIOS = {"race": race, "sit_down": sit_down, "cooking": cooking, "drama": drama, "product_ad": product_ad,
             "puzzle": puzzle, "motion_graphics": motion_graphics, "two_places": two_places,
             "simultaneous": simultaneous, "long_story": long_story}


def good_plan(g):
    """One scene per dependency level, so parallel events share a scene: the writer's correct answer."""
    order, _ = G.topo_order(g)
    level = {}
    for x in order:
        e = G.by_id(g)[x]
        level[x] = 1 + max((level[r] for r in e["requires"]), default=0)
    groups = {}
    for x in order:
        groups.setdefault(level[x], []).append(x)
    return scenes(*[groups[k] for k in sorted(groups)])


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_graph_is_complete_and_ordered(name):
    g = SCENARIOS[name]()
    order, cyc = G.topo_order(g)
    assert not cyc and not G.check(g) and len(order) == len(g["events"])
    for k, x in enumerate(order):  # every event after everything it needs
        assert all(order.index(r) < k for r in G.by_id(g)[x]["requires"])
    plan = good_plan(g)
    rules, _ = temporal.plan_issues(g, plan)
    assert rules == [] or all(r["code"] == G.INCOMPLETE_ACTION for r in rules)
    rec = temporal.annotate(g, plan)
    assert rec["coverage"]["covered"] == len(g["events"]) and rec["coverage"]["missing"] == []
    assert [s["n"] for s in rec["scenes"]] == list(range(1, len(plan) + 1))


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_omission_and_reorder_are_caught(name):
    g = SCENARIOS[name]()
    plan = good_plan(g)
    essential = [e["id"] for e in g["events"] if e["essential"] and e["requires"]]
    victim = essential[len(essential) // 2]
    dropped = [{**s, "events": [x for x in s["events"] if x != victim]} for s in plan]
    rules, _ = temporal.plan_issues(g, dropped)
    assert any(r["code"] == G.MISSING_EVENT and victim in r["problem"] for r in rules)
    swapped = list(reversed(plan))  # the result before the actions that lead to it
    rules, _ = temporal.plan_issues(g, swapped)
    assert any(r["code"] in (G.WRONG_EVENT_ORDER, G.CAUSALITY_ERROR) for r in rules)


def test_a_reaction_before_its_trigger_is_a_causality_error():
    g = drama()
    plan = scenes(["E1"], ["E2", "E3"], ["E5"], ["E4"], ["E6"])  # the boss goes pale before he sees the locket
    rules, _ = temporal.plan_issues(g, plan)
    assert any(r["code"] == G.CAUSALITY_ERROR and r["scene"] == 3 for r in rules)


def test_state_flows_from_scene_to_scene():
    g = sit_down()
    walk = controller.run(g, scenes(["E1", "E2"], ["E3"], ["E4", "E5"]))
    assert walk["problems"] == []
    s1, s2, s3 = walk["scenes"]
    assert s1["start"]["O1"]["state"] == "closed" and s1["end"]["O1"]["state"] == "open"
    assert s2["start"] == s1["end"]  # the handoff: the next scene starts in the state the last one ended in
    assert s3["end"]["C1"]["pose"] == "sitting" and s3["pending"] == []
    assert "IN THIS ORDER" in s3["brief"] and "sits down" in s3["brief"] and "STATE AT THE START" in s3["brief"]


def test_a_change_never_shown_is_a_discontinuity():
    g = product_ad()
    g["events"][5]["changes"] = [{"entity": "O1", "attr": "state", "from": "half full", "to": "closed"}]
    probs = controller.run(g, good_plan(g))["problems"]
    assert probs and probs[0]["code"] == G.OBJECT_INCONSISTENCY
    g2 = two_places()
    g2["events"][2]["changes"] = [{"entity": "C1", "attr": "location", "from": "the park", "to": "cafe"}]
    assert controller.run(g2, good_plan(g2))["problems"][0]["code"] == G.LOCATION_DISCONTINUITY


def test_a_cycle_and_unknown_dependencies_are_reported():
    g = graph([ev(1, "a", [2]), ev(2, "b", [1]), ev(3, "c", ["E9"])])
    codes = [p["code"] for p in G.check(g)]
    assert codes.count(G.CAUSALITY_ERROR) == 2


def test_pacing_gives_the_events_their_time():
    g = long_story()  # 30 events x 5 s
    auto = pacing.pace(g, 45, user_fixed=False)
    assert auto["total_s"] == 120 and "longest film" in auto["note"]
    fixed = pacing.pace(g, 45, user_fixed=True)
    assert fixed["total_s"] == 45 and "every essential event stays" in fixed["note"]
    assert pacing.pace(sit_down(), 30, user_fixed=False)["total_s"] == 30  # enough already
    crowded = scenes(["E1", "E2", "E3", "E4"])
    assert pacing.overfull(g, crowded)[0]["code"] == G.INCOMPLETE_ACTION


def test_the_writer_brief_lists_every_event_in_order():
    g = simultaneous()
    text = temporal.writer_block(g, {"note": "fits"})
    assert text.index("E1:") < text.index("E4:") and "together with E3" in text and "split, never drop" in text
    assert temporal.writer_block({}) == ""


def test_scenes_that_list_nothing_are_sent_back():
    rules, _ = temporal.plan_issues(cooking(), [{"n": 1}, {"n": 2}])
    assert rules[0]["code"] == G.MISSING_EVENT and "lists" in rules[0]["problem"]


def test_clip_verdicts_pass_fail_and_uncertain():
    events = [{"id": "E2", "description": "she opens the door", "done_when": "door open"}]
    block = temporal.clip_block(events)
    assert "[ACTION_SKIPPED]" in block and "she opens the door" in block
    assert temporal.verdict([], True)["status"] == "PASS"
    bad = temporal.verdict(["MAJOR: [ACTION_SKIPPED] she is already inside", "MINOR: a cup moved"], True)
    assert bad == {"status": "FAIL", "codes": ["ACTION_SKIPPED"]}
    assert temporal.verdict([], checked=False)["status"] == "UNCERTAIN"


def test_continuous_scenes_hand_off_their_frame_and_cuts_do_not():
    a = {"environment": "the penthouse office, glass desk, rain on the window", "link": "opening"}
    b = {"environment": "the penthouse office by the glass desk", "link": "reverse cut"}
    street = {"environment": "a rainy street with neon signs outside", "link": "cut"}
    later = {"environment": "the penthouse office, glass desk", "link": "time jump: the next morning"}
    assert story_engine.continues(a, b) and not story_engine.continues(b, street)
    assert not story_engine.continues(a, later) and not story_engine.continues(None, a)
    assert story_engine.continues({"title": "a"}, {"link": "continues the same moment"})  # no places known: the link decides


def test_engine_scene_gets_events_brief_and_sequence_note():
    g = sit_down()
    rec = temporal.annotate(g, scenes(["E1", "E2"], ["E3", "E4", "E5"]))
    s = {}
    story_engine._sequence_fields(s, rec, 2)
    assert [e["id"] for e in s["events"]] == ["E3", "E4", "E5"]
    assert s["sequence_note"].startswith("In this order") and "2. she crosses to the chair" in s["sequence_note"]
    assert "STATE AT THE START" in s["continuity_brief"]


# ---- integration: the writer is sent back until the sequence is complete ------------------------------------------

def test_develop_sends_an_incomplete_plan_back_and_keeps_the_sequence(monkeypatch):
    from engines import story_intel as si
    calls = []
    g_raw = {"entities": {"characters": [{"id": "C1", "name": "Mira", "start": {"location": "corridor"}}]},
             "events": [ev(1, "she walks to the door"), ev(2, "she opens the door", [1]),
                        ev(3, "she steps in and sits", [2])]}

    def sc(n, ids):
        return {"n": n, "title": f"S{n}", "duration_s": 5, "events": ids, "environment": "the hallway and the room",
                "start_state": "a", "end_state": "b", "transition_in": "cut", "prompt": {"ending": "x"}}

    async def fake_chat(model, messages, **kw):
        stage = kw["stage"]
        calls.append(stage)
        if stage == "story.classify":
            return {"category": "fiction", "agent": "story", "production": "cinematic", "research": "none",
                    "duration_s": 15}
        if stage == "story.events":
            assert "INPUT" in messages[1]["content"]
            return g_raw
        if stage == "story.architect":
            assert "TEMPORAL SEQUENCE" in messages[1]["content"] and "E2: she opens the door" in messages[1]["content"]
            return {"title": "T", "structure": [{"stage": "all", "scenes": [1, 2]}],
                    "bible": {"characters": ["Mira"], "world": "w"}, "scenes": [sc(1, ["E1"]), sc(2, ["E3"])]}
        if stage == "story.architect.revise":
            assert "E2" in messages[-1]["content"] and "is in no scene" in messages[-1]["content"]
            return {"scenes": [sc(1, ["E1", "E2"]), sc(2, ["E3"])]}
        if stage == "story.validate":
            return {"score": 90, "pass": True, "issues": []}
        raise AssertionError(stage)

    monkeypatch.setattr(si.llm, "chat_json", fake_chat)
    intel = asyncio.run(si.develop("Mira comes home and sits down"))
    assert "story.events" in calls and "story.architect.revise" in calls
    rec = intel["temporal"]
    assert rec["coverage"]["missing"] == [] and rec["coverage"]["covered"] == 3
    assert intel["classification"]["duration_s"] >= 10 and "_events" not in intel["classification"]
    assert rec["scenes"][1]["start"]["C1"] == rec["scenes"][0]["end"]["C1"]


# ---- integration: the render engine hands the last frame to the scene that continues -----------------------------

def _render(tmp_path, monkeypatch, link2, env2=None):
    import shutil
    import subprocess
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setenv("PALETTE_VIDEO_QUALITY", "test")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
                    "-pix_fmt", "yuv420p", str(tmp_path / "src.mp4")], check=True)
    seen = {"images": [], "clips": []}

    async def fake_chat(model, messages, **kw):
        st = kw.get("stage")
        if st in ("engine.critic",):
            return {"score": 90, "issues": []}
        if st in ("engine.scene_qa",):
            return {"ok": True, "issues": []}
        if st == "engine.clip_qa":
            assert "SEQUENCE CHECK" in messages[0]["content"][0]["text"]
            return {"ok": False, "issues": ["MINOR: [POSITION_DISCONTINUITY] the cup shifts"]}
        if st in ("engine.story.locations",):
            return {}
        if st == "engine.graphics_director":
            return {"add_graphics": False, "why": "story"}
        return {"title": "Home", "story": "s", "bible": {},
                "scenes": [{"title": "Door", "duration": 5, "image_prompt": "a door in a hallway", "camera": "static",
                            "end_state": "the door is open", "link": "opening"},
                           {"title": "In", "duration": 5, "image_prompt": "the room", "camera": "static",
                            "end_state": "she sits", "link": link2}]}

    def fake_image(key, model, prompt, **kw):
        seen["images"].append((prompt, list(kw.get("image_urls") or [])))
        if prompt.startswith("The LAST frame"):
            n = sum(1 for p, _ in seen["images"] if p.startswith("The LAST frame"))
            return {"images": [{"url": f"https://fal.media/end_{n}.png"}]}
        return {"images": [{"url": f"https://fal.media/img{len(seen['images'])}.png"}]}

    def fake_video(key, model, prompt, **kw):
        seen["clips"].append((prompt, kw.get("start_image"), kw.get("end_image")))
        return {"video": {"url": "https://x/c.mp4"}}

    async def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_path / "src.mp4", dest)
        return str(dest)

    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    monkeypatch.setattr(story_engine.fal_helper, "generate_image", fake_image)
    monkeypatch.setattr(story_engine.fal_helper, "generate_video", fake_video)
    monkeypatch.setattr(story_engine, "_download", fake_download)
    monkeypatch.setattr(story_engine.music, "available", lambda: "off in tests")
    events = [ev(1, "she opens the door", chars=()), ev(2, "she steps into the room", [1], chars=())]
    g = G.normalize({"events": events})
    plan_scenes = [{"n": 1, "title": "Door", "duration_s": 5, "events": ["E1"],
                    "environment": "the narrow hallway of her flat, a green front door"},
                   {"n": 2, "title": "In", "duration_s": 5, "events": ["E2"],
                    "environment": env2 or "the narrow hallway of her flat by the green front door"}]
    story = {"mode": "story", "plan": {"scenes": plan_scenes}, "temporal": temporal.annotate(g, plan_scenes)}
    msgs = []

    async def progress(m):
        msgs.append(m)

    out = asyncio.run(story_engine.run({"app_id": "pltt-storyboard-video-maker", "name": "App"},
                                       {"prompt": "she comes home", "_story": story,
                                        "request": {"duration": 10, "aspect_ratio": "9:16"}},
                                       {}, tmp_path / "work", progress))
    return out, seen, msgs


def test_same_shot_starts_on_the_previous_last_frame_and_the_clips_meet_there(tmp_path, monkeypatch):
    out, seen, msgs = _render(tmp_path, monkeypatch, "continues the same moment")
    assert not [p for p, _ in seen["images"] if p.startswith("the room")]  # no new first frame: the handoff is it
    assert out["scenes"][1]["still_url"] == "https://fal.media/end_1.png" and out["scenes"][1]["handoff"] == "exact"
    (_, s1, e1), (p2, s2, _) = seen["clips"][0], seen["clips"][1]
    assert e1 == "https://fal.media/end_1.png" == s2  # clip 1 ends on the frame clip 2 starts on
    assert any("starts on scene 1's last frame" in m for m in msgs)
    assert "In this order" in p2 and "she steps into the room" in p2 and out["scenes"][1]["events"] == ["E2"]
    assert out["scenes"][1]["qa"]["clip"]["sequence"] == {"status": "FAIL", "codes": ["POSITION_DISCONTINUITY"]}


def test_new_angle_is_drawn_from_the_previous_last_frame(tmp_path, monkeypatch):
    out, seen, msgs = _render(tmp_path, monkeypatch, "reverse cut to her face")
    second = [(p, u) for p, u in seen["images"] if p.startswith("the room")]
    assert second[0][1][-1] == "https://fal.media/end_1.png" and out["scenes"][1]["handoff"] == "draw"
    assert "this shot starts right after it" in second[0][0]


def test_new_place_still_carries_the_look_from_the_previous_last_frame(tmp_path, monkeypatch):
    out, seen, msgs = _render(tmp_path, monkeypatch, "cut", env2="a sunny beach with palm trees and blue sea")
    second = [(p, u) for p, u in seen["images"] if p.startswith("the room")]
    assert second[0][1][-1] == "https://fal.media/end_1.png" and out["scenes"][1]["handoff"] == "ref"
    assert "do not copy that frame's set" in second[0][0]
