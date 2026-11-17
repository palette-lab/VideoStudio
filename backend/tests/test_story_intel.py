"""Universal Story Intelligence: rule checks, edits, the hand-off to the film engine, documents, the pipeline order."""

import asyncio
import json
import shutil
import subprocess

import pytest

from engines import planners, story_intel
from engines.story_intel import research, validate


def scene(n, **kw):
    base = {"n": n, "title": f"Scene {n}", "duration_s": 6, "purpose": "moves the story",
            "what_happens": ["", "the hare boasts to the tortoise beside the oak", "the race starts at the flag",
                             "the hare naps under a willow", "the tortoise passes the sleeping hare",
                             "the hare wakes and sprints", "the tortoise crosses the line", "everyone cheers"][n],
            "actions": "0-2 s: the hare hops; 2-6 s: he stops by the oak", "framing": f"wide shot {n}",
            "camera_movement": f"slow push-in {n}", "lighting": "warm morning sun from the left",
            "start_state": "the hare stands by the oak on the dirt path", "end_state": "the hare stands by the oak",
            "transition_in": "cut on action", "props": ["1 hare", "1 tortoise", "1 oak"],
            "audio": {"type": "narration", "speaker": "Narrator", "line": "The hare was sure of himself."},
            "prompt": {k: f"{k} " + "detail " * 25 for k in validate.PROMPT_FIELDS}}
    base.update(kw)
    return base


def plan(scenes, **kw):
    return {"title": "Slow and Steady", "bible": {"characters": [{"name": "hare"}], "world": {"location": "meadow"}},
            "scenes": scenes, **kw}


def test_a_good_plan_passes_the_rules():
    assert validate.lint(plan([scene(1), scene(2), scene(3)]), 18, "children_story") == []


def test_rules_catch_weak_prompts_repeats_long_lines_and_overloaded_clips():
    weak = scene(2, prompt={**scene(2)["prompt"], "action": "rabbit running in a forest, cinematic camera"})
    repeat = scene(3, what_happens=scene(1)["what_happens"])
    long_line = scene(4, audio={"type": "narration", "line": " ".join(["word"] * 25)})
    busy = scene(5, actions="0-1 s: a; 1-2 s: b; 2-3 s: c; 3-4 s: d")
    camera_only = scene(6, what_happens="The camera slowly orbits the oak tree.")
    long_clip = scene(7, duration_s=14)
    found = " | ".join(f"{i['scene']}:{i['problem']}" for i in
                       validate.lint(plan([scene(1), weak, repeat, long_line, busy, camera_only, long_clip]), 42))
    assert "2:the prompt's action is only" in found
    assert "3:scene 3 repeats scene 1" in found
    assert "4:the narration line has 25 words" in found and "4:25 words cannot be spoken" in found
    assert "5:4 timed beats" in found and "6:the scene is only a camera move" in found
    assert "7:14 s is outside" in found


def test_rules_for_facts_puzzles_continuity_and_time_order():
    doc = plan([scene(1, audio={"type": "narration", "line": "In nineteen twelve the ship sailed."},
                      what_happens="the ship leaves port in 1912"),
                scene(2), scene(3, what_happens="the ship was built in 1909 at the yard")])
    found = " ".join(i["problem"] for i in validate.lint(doc, 18, "documentary"))
    assert "1909 steps back in time after 1912" in found and "without fact notes" in found
    assert any(i["area"] == "puzzle" for i in validate.lint(plan([scene(1)]), 6, "puzzle_mystery"))
    jump = scene(2, start_state="a dragon above a volcano", transition_in="")
    assert any("does not start where" in i["problem"] for i in validate.lint(plan([scene(1), jump]), 12))
    digits = scene(1, audio={"type": "narration", "line": "It sank in 1912."})
    assert any("digits" in i["problem"] for i in validate.lint(plan([digits]), 6))


def test_edits_and_brief_and_engine_block():
    intel = {"classification": {"category": "children_story", "duration_s": 12},
             "plan": plan([scene(1), scene(2)], takeaway="Slow and steady wins.")}
    story_intel.apply_edits(intel, 2, {"title": "The Nap", "line": "He lay down for a quick nap.",
                                       "prompt.action": "the hare curls up " * 10, "bogus": "x"})
    s = intel["plan"]["scenes"][1]
    assert s["title"] == "The Nap" and s["audio"]["line"].startswith("He lay") and s["edited"] and "bogus" not in s
    assert "rules" in intel["validation"]
    brief = story_intel.as_brief(intel)
    assert "SCENE 2 - The Nap (6 s)" in brief and "ACTION: the hare curls up" in brief and "Slow and steady" in brief
    with pytest.raises(ValueError):
        story_intel.apply_edits(intel, 9, {})
    block = json.loads(story_intel.engine_block(intel))
    assert len(block["scenes"]) == 2 and "SUBJECT:" in block["scenes"][0]["video_prompt"] and block["bible"]
    q = {"video_model": "kling_3_0_pro", "image_model": "nano_banana_pro", "audio": True}
    system, user = planners.prompt_for("pltt-storyboard-video-maker", "rabbit vs tortoise", 12, "9:16", 3, 10, 2, [], q,
                                       approved=intel)
    assert "APPROVED STORY" in system and "render exactly this" in user and "The Nap" in user


def test_regenerate_keeps_neighbours(monkeypatch):
    intel = {"classification": {"duration_s": 18}, "plan": plan([scene(1), scene(2), scene(3)])}
    seen = {}

    async def fake(model, messages, **kw):
        seen["ctx"] = json.loads(messages[1]["content"])
        return {**scene(2), "title": "New nap", "n": 99}
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    asyncio.run(story_intel.regenerate_scene(intel, 2, "make him trip first"))
    assert seen["ctx"]["previous_scene"]["n"] == 1 and seen["ctx"]["next_scene"]["n"] == 3
    assert seen["ctx"]["instruction"] == "make him trip first"
    assert intel["plan"]["scenes"][1]["title"] == "New nap" and intel["plan"]["scenes"][1]["n"] == 2


def test_develop_runs_the_agents_in_order(monkeypatch):
    calls = []

    async def fake(model, messages, **kw):
        stage = kw["stage"]
        calls.append(stage)
        if stage == "story.classify":
            return {"category": "documentary", "agent": "story", "research": "deep", "research_queries": ["Titanic"],
                    "subject": "Titanic", "duration_s": 18}
        if stage == "story.research":
            assert "SOURCE: Titanic" in messages[1]["content"]
            return {"summary": "sank 1912", "facts": [{"claim": "sank 15 April 1912", "source": "Titanic"}]}
        if stage.startswith("story.documentary"):  # a documentary: the cinematic-skill master prompt directs it
            system = messages[0]["content"]
            assert "CINEMATIC DOCUMENTARY & PRESENTATION FILM MASTER PROMPT" in system and '"hit_word"' in system
            assert '"archive": ["A1"]' in system and "A1: RMS Titanic departing Southampton (1912)" in messages[1]["content"]
            assert "PRESENTED by a host" in system
            doc = dict(hero="the pinned photo of the ship", hit_word="sure", overlay_text="APRIL 1912", archive=["A1"])
            host = dict(host=True, hero="the host", audio={"type": "dialogue", "speaker": "Host",
                                                           "line": "One night in April, an unsinkable ship sank."})
            return plan([scene(1, **host), scene(2, **doc), scene(3, **host)], look="wall",
                        host={"look": "woman in her forties, dark coat", "setting": "archive room",
                              "voice_gender": "female"},
                        fact_notes=[{"claim": "sank 1912", "source": "Titanic"}])
        if stage == "story.validate":
            assert "CINEMATIC DOCUMENTARY plan" in messages[0]["content"]
            return {"score": 90, "pass": True, "issues": []}
        raise AssertionError(stage)

    async def fake_gather(queries, lang="en"):
        return [{"title": "Titanic", "url": "https://en.wikipedia.org/wiki/Titanic", "text": "RMS Titanic sank.",
                 "kind": "wikipedia"}]
    async def fake_archive(queries, limit=8):
        assert queries == ["Titanic"]
        return [{"id": "A1", "what": "RMS Titanic departing Southampton", "date": "1912", "license": "Public domain",
                 "url": "https://upload.wikimedia.org/t.jpg", "source": "https://commons.wikimedia.org/wiki/File:T.jpg",
                 "credit": "F.G.O. Stuart", "title": "File:T.jpg"}]
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    monkeypatch.setattr(story_intel.research_, "gather", fake_gather)
    monkeypatch.setattr(story_intel.research_, "archive_photos", fake_archive)
    intel = asyncio.run(story_intel.develop("history of the titanic", duration_s=None))
    assert calls == ["story.classify", "story.research", "story.documentary", "story.validate"]
    assert intel["mode"] == "cinematic_doc"
    block = json.loads(story_intel.engine_block(intel))
    assert "cinematic documentary, 'wall' look" in block["mode"] and block["scenes"][0]["hero"]
    assert block["scenes"][1]["archive"] == ["A1"] and block["archive"][0]["id"] == "A1"
    assert block["scenes"][0]["host"] and block["host"]["voice_gender"] == "female" and "presenter on camera" in block["mode"]
    assert "opened and closed by a host" in story_intel.as_brief(intel)
    assert intel["archive"][0]["credit"] == "F.G.O. Stuart"
    assert "cinematic documentary in the 'wall' look" in story_intel.as_brief(intel)
    assert intel["validation"]["passed"] and intel["research"]["sources"][0]["url"].endswith("Titanic")


def test_food_requests_are_left_to_the_recipe_engine(monkeypatch):
    async def fake(model, messages, **kw):
        return {"category": "other", "agent": "food", "duration_s": 45}
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    intel = asyncio.run(story_intel.develop("chicken biryani"))
    assert intel["skipped"] and "plan" not in intel


@pytest.mark.skipif(not shutil.which("pdftotext") or not shutil.which("cupsfilter"), reason="needs poppler + cupsfilter")
def test_pdf_text_is_read(tmp_path):
    src = tmp_path / "apollo.txt"
    src.write_text("Apollo 11 landed on the Moon on 20 July 1969.")
    pdf = tmp_path / "apollo.pdf"
    with pdf.open("wb") as fh:
        subprocess.run(["cupsfilter", str(src)], stdout=fh, stderr=subprocess.DEVNULL, check=True)
    assert "Apollo 11 landed" in research.document_text(str(pdf))
    assert research.document_text(str(src)).startswith("Apollo 11")


def mg_scene(n, line="Your brain spends money before you do.", words="spends money"):
    return {"n": n, "beat": f"B{n:02d}", "script_line": line, "duration_s": 5, "visual_concept": f"concept {n}",
            "hero_subject": "a ceramic piggy bank", "keyframe_prompt": "a matte ceramic piggy bank on a paper grid " * 8,
            "typography_text": words, "typography_style": "extruded black sans", "three_d_elements": "glazed ceramic, paper",
            "graphic_elements": "dashed lines", "composition": "hero centre, type top third", "lighting": "soft key",
            "color": "palette 1", "method": "both", "end_frame": "the bank tilts left",
            "motion": {"hero_object_motion": "the bank rotates 30 degrees and coins rise " * 3, "camera": f"dolly-in {n}",
                       "transition_out": "mask wipe", "sfx": "coin clicks",
                       "timeline": [{"t": "0.0-0.5", "what": "OPEN"}, {"t": "0.5-3.5", "what": "BUILD"}]}}


def test_motion_director_output_becomes_a_story_plan():
    from engines.story_intel import motion
    raw = {"title": "Why We Overspend", "objective": "explain mental accounting",
           "script": "Your brain spends money before you do.", "palette": {"option": 2, "why": "tech"},
           "beats": [{"id": "B01", "line": "Your brain spends money before you do.", "role": "hook"}],
           "scenes": [mg_scene(1), mg_scene(2, "It labels cash into invisible jars.", "invisible jars")]}
    plan = motion.to_plan(raw)
    s = plan["scenes"][0]
    assert s["audio"]["line"] == "Your brain spends money before you do." and s["overlay_text"] == "spends money"
    assert s["in_scene_text"] == ""  # no letters drawn into the keyframe: the words are the editable text layer
    assert s["on_screen_text"] == ""  # full 3D: the words are 3D letters in the keyframe, never a flat overlay
    assert s["motion_gfx"]["keyframe_prompt"] and "Dark Technical Minimal" in plan["style"]["look"]
    assert plan["motion_graphics"]["palette"]["option"] == 2 and plan["motion_graphics"]["connection"] == "S01 -> S02"
    assert motion.lint(plan) == []
    bad = motion.to_plan({**raw, "scenes": [mg_scene(1, " ".join(["word"] * 16), "not in line here at all")]})
    found = " ".join(i["problem"] for i in motion.lint(bad))
    assert "16 words" in found and "over 4 words" in found and "not taken from the line" in found
    # Realistic look: glass / chrome / marble are flagged for revision unless the user asked for them.
    shiny = mg_scene(1)
    shiny["keyframe_prompt"] = "a clear glass brain with a polished chrome arm on a marble plinth"
    glassy = motion.to_plan({**raw, "scenes": [shiny]})
    found = " ".join(i["problem"] for i in motion.lint(glassy))
    assert "stylised materials" in found and "glass" in found and "chrome" in found and "marble" in found
    assert motion.lint(glassy, stylized_ok=True) == []
    assert motion.wants_stylized("a glass brain ad") and not motion.wants_stylized("how habits form in the brain")
    block = json.loads(story_intel.engine_block({"mode": "motion_graphics", "plan": plan}))
    assert block["mode"].startswith("full 3D motion graphics") and block["scenes"][0]["keyframe_prompt"]
    assert block["scenes"][0]["overlay_text"] == "spends money" and "editable text layer" in block["mode"]
    assert "NO text" in block["mode"]


def test_motion_route_topic_ideas_and_palette_switch(monkeypatch):
    stages = []

    async def fake(model, messages, **kw):
        stages.append(kw["stage"])
        if kw["stage"] == "story.classify":
            return {"category": "educational", "agent": "explainer", "production": "motion_graphics",
                    "has_topic": False, "research": "none", "duration_s": 30}
        if kw["stage"] == "story.motion_director":
            return {"needs_topic": True, "topic_ideas": ["Why we overspend", "The 1% rule", "Compound habits",
                                                         "The anchoring trap", "Loss aversion"]}
        raise AssertionError(kw["stage"])
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    intel = asyncio.run(story_intel.develop("finance video ideas"))
    assert intel["mode"] == "motion_graphics" and len(intel["topic_ideas"]) == 5 and "plan" not in intel
    assert stages == ["story.classify", "story.motion_director"]

    seen = {}

    async def fake2(model, messages, **kw):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        return {"title": "t", "palette": {"option": 3}, "script": "Your brain spends money before you do.",
                "scenes": [mg_scene(1)]}
    monkeypatch.setattr(story_intel.llm, "chat_json", fake2)
    own = {"1": {"name": "Ink", "spec": "a", "hex": ["#111111"]}, "2": {"name": "Paper", "spec": "b", "hex": []},
           "3": {"name": "Rust", "spec": "c", "hex": ["#B4532A"]}}
    intel = {"mode": "motion_graphics", "classification": {"category": "educational", "duration_s": 5},
             "plan": {"motion_graphics": {"script": "Your brain spends money before you do.", "palette_specs": own,
                                          "art_direction": {"name": "Workshop"}}}}
    asyncio.run(story_intel.switch_palette(intel, 3, "why we overspend"))
    assert "LOCKED by the user: option 3 (Rust)" in seen["system"] and "KEEP THIS APPROVED SCRIPT" in seen["user"]
    assert "KEEP THIS ART DIRECTION" in seen["user"] and "Workshop" in seen["user"]
    mg = intel["plan"]["motion_graphics"]
    assert mg["palette"]["option"] == 3 and mg["palettes"][3] == "Rust" and mg["art_direction"]["name"] == "Workshop"
    with pytest.raises(ValueError):
        asyncio.run(story_intel.switch_palette(intel, 7, "x"))


def test_director_uses_the_users_master_prompt_verbatim_plus_full_3d():
    from engines.story_intel import motion
    master = (motion.Path(motion.__file__).resolve().parent.parent / "prompts" / "motion_graphics_3d_master.md").read_text()
    msgs = motion.messages("how compound interest works", {"category": "educational", "duration_s": 30}, None, "none",
                           "- Storyboard & Video: ...", "9:16")
    system = msgs[0]["content"]
    assert system.startswith(master)  # the master prompt, word for word, first
    for must in ("4.5 3D typography", "THE GOLDEN RULE", "STAGE 6", "FULL 3D", "Section 4 is used in EVERY scene",
                 "EDITABLE text layer", "no text, no letters, no logos", "- Storyboard & Video: ...",
                 "Target 30 seconds"):
        assert must in system
    assert "{APPS}" not in system and "{LENGTH}" not in system and "{PALETTE_RULE}" not in system
    assert "{STYLES}" not in system and "DIFFERENT FOR EVERY VIDEO" in system and "REALISTIC LOOK" in system


def test_each_video_gets_its_own_art_direction_and_palettes():
    from engines.story_intel import motion
    # Different runs are offered different style families, so the looks do not repeat.
    offered = {tuple(motion.style_options()) for _ in range(20)}
    assert len(offered) > 10 and all(len(x) == 4 for x in offered)
    raw = {"title": "Coffee", "script": "Your brain spends money before you do.",
           "art_direction": {"name": "Roastery tabletop", "concept": "coffee's real world", "set": "burlap and wood",
                             "materials": "real beans, enamel mugs", "typography": "stencil slab in matte paint"},
           "palettes": [{"name": "Dark Roast", "spec": "espresso brown", "hex": ["#2B1A12", "#C8873A", "#F3E9DC"]},
                        {"name": "Crema", "spec": "cream and caramel", "hex": ["#F3E9DC", "#B5651D", "not-a-hex"]},
                        {"name": "Origin Green", "spec": "leaf green", "hex": ["#1F3B2D", "#E4D6A7"]}],
           "palette": {"option": 2, "why": "warm"}, "scenes": [mg_scene(1)]}
    plan = motion.to_plan(raw)
    mg = plan["motion_graphics"]
    assert mg["palettes"] == {1: "Dark Roast", 2: "Crema", 3: "Origin Green"}
    assert mg["palette_swatches"][2] == ["#F3E9DC", "#B5651D"]  # bad HEX dropped
    assert "Crema" in plan["style"]["look"] and "Roastery tabletop" in plan["style"]["look"]
    assert "Roastery tabletop" in plan["style"]["visual_style"]
    kept = motion.keep_style(json.loads(json.dumps(plan)))  # survives the JSON round trip of the run store
    assert kept["palettes"][3]["name"] == "Origin Green" and kept["art_direction"]["name"] == "Roastery tabletop"
    # No palettes from the director: the original three are the fallback.
    assert motion.to_plan({**raw, "palettes": []})["motion_graphics"]["palettes"][1] == "Signature Editorial"


def test_scene_checks_verify_the_3d_words():
    from engines import story_engine as se
    spec = se._qa_spec({"title": "B01", "in_scene_text": "spends money", "start_state": "a chrome piggy bank"}, "first")
    assert "spends money" in spec and "spelled exactly" in spec
    assert "misspelled" in se.SCENE_QA and "re-spell" in se.CLIP_QA


def test_3d_motion_video_mode_forces_the_3d_director_for_any_topic(monkeypatch):
    from router_core import pipeline
    stages = []

    async def fake(model, messages, **kw):
        stages.append(kw["stage"])
        if kw["stage"] == "story.classify":  # a cooking topic the classifier would normally film as live action
            return {"category": "other", "agent": "food", "production": "cinematic", "has_topic": True,
                    "research": "none", "duration_s": 20}
        if kw["stage"] == "story.motion_director":
            return {"needs_topic": True, "topic_ideas": ["a", "b", "c", "d", "e"]}
        raise AssertionError(kw["stage"])
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    intel = asyncio.run(story_intel.develop("how sourdough rises", force_motion=True))
    assert intel["mode"] == "motion_graphics" and stages == ["story.classify", "story.motion_director"]
    assert pipeline.APP_MODES["motion3d"] == "pltt-storyboard-video-maker"
    stages.clear()  # without the mode the food agent keeps the cooking film
    assert asyncio.run(story_intel.develop("how sourdough rises")).get("skipped") and stages == ["story.classify"]


def test_a_talking_host_skips_the_story_writer_and_never_becomes_a_storyboard_film(monkeypatch):
    from engines.story_intel import direct
    stages = []

    async def fake(model, messages, **kw):
        stages.append(kw["stage"])
        if kw["stage"] == "story.classify":  # what the classifier really returned for the compound-interest host
            return {"category": "educational", "agent": "presenter", "production": "cinematic", "has_topic": True,
                    "research": "light", "duration_s": 60, "subject": "compound interest"}
        if kw["stage"] == "story.research":  # the presentation still gets the cinematic-skill research
            return {"summary": "interest on interest", "facts": [{"claim": "interest is added to the principal",
                                                                  "source": "Compound interest"}]}
        raise AssertionError(kw["stage"])

    async def fake_gather(queries, lang="en"):
        return [{"title": "Compound interest", "url": "https://en.wikipedia.org/wiki/Compound_interest",
                 "text": "Compound interest is interest on interest.", "kind": "wikipedia"}]
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    monkeypatch.setattr(story_intel.research_, "gather", fake_gather)
    intel = asyncio.run(story_intel.develop("A 60-second YouTube explainer: a friendly host at a desk explains "
                                            "how compound interest works"))
    assert intel.get("skipped") and "plan" not in intel and stages == ["story.classify", "story.research"]
    assert intel["research"]["facts"][0]["source"] == "Compound interest"  # handed to Presenter Studio's script
    # A story written before this fix (agent presenter, category educational) still goes through the router.
    old = {"mode": "auto", "assets": {}, "story_intel": {"plan": {"scenes": [{"n": 1}]},
                                                         "classification": {"category": "educational",
                                                                            "agent": "presenter"}}}
    assert direct.applies(old) is False
    old["story_intel"]["classification"]["agent"] = "explainer"
    assert direct.applies(old) is True


def test_documentary_method_applies_to_real_subjects_and_checks_its_rules():
    from engines.story_intel import documentary
    assert documentary.applies({"category": "documentary"})
    assert documentary.applies({"category": "educational", "is_factual": True})  # a factual presentation
    assert not documentary.applies({"category": "educational", "is_factual": True, "production": "motion_graphics"})
    assert not documentary.applies({"category": "fiction"}) and not documentary.applies({"category": "product"})
    line = {"type": "narration", "line": "Twelve seconds changed everything."}
    good = {"scenes": [{"hero": "the photo", "hit_word": "Twelve", "overlay_text": "DEC 17, 1903", "audio": line}],
            "look": "wall"}
    assert documentary.lint(good) == []
    bad = {"look": "neon", "scenes": [{"hero": "", "hit_word": "flight", "audio": line,
                                       "overlay_text": "Twelve seconds changed everything"}]}
    problems = " | ".join(p["problem"] for p in documentary.lint(bad))
    assert "not one of the method's looks" in problems and "no hero" in problems
    assert "'flight' is not in the spoken line" in problems and "repeat the narration word for word" in problems
    assert "Never generate a stand-in face" in documentary.MASTER and "One hero per line" in documentary.MASTER


def test_archive_photos_keep_only_free_licences_and_write_credits():
    ok = research.free_licence
    assert ok("Public domain") and ok("PD-US") and ok("CC0") and ok("CC BY 4.0") and ok("CC BY-SA 3.0")
    assert not ok("CC BY-NC 2.0") and not ok("CC BY-ND 4.0") and not ok("No known copyright restrictions")
    assert not ok("Fair use") and not ok("")
    md = research.credits_md([{"what": "First flight", "credit": "John T. Daniels", "license": "Public domain",
                               "source": "https://commons.wikimedia.org/wiki/File:First_flight2.jpg"}])
    assert "John T. Daniels - Public domain - https://commons.wikimedia.org" in md and research.credits_md([]) == ""
    from engines.story_intel import documentary
    bad = {"scenes": [{"hero": "x", "archive": ["A9"]}]}
    assert "archive photo A9 does not exist" in documentary.lint(bad, {"A1"})[0]["problem"]


def test_archive_searches_are_short_keywords_and_documentaries_get_their_own_finish():
    cls = {"research_queries": ["1815 eruption of Mount Tambora effects",
                                "Year Without a Summer 1816 causes and consequences"], "subject": "Year Without a Summer"}
    assert research.archive_queries(cls) == ["1815 eruption Mount", "Year Without Summer"]
    assert research.archive_queries({"archive_queries": ["Mount Tambora", "Villa Diodati"]}) == ["Mount Tambora",
                                                                                                "Villa Diodati"]
    from engines import motion_design
    text = motion_design.finish_summary("documentary")
    assert "Documentary finish" in text and "no punch-ins, pop words or callouts" in text


def test_documentary_host_opens_and_closes():
    from engines.story_intel import documentary
    line = lambda t: {"type": "narration", "line": t}
    board = {"hero": "the photo", "hit_word": "ship", "audio": line("The ship left port.")}
    no_host = {"scenes": [dict(board), dict(board), dict(board)]}
    problems = " | ".join(p["problem"] for p in documentary.lint(no_host))
    assert "should be the host on camera" in problems and "the host has no look" in problems
    host = {"host": True, "audio": {"type": "dialogue", "line": "It was the safest ship ever built."}}
    good = {"host": {"look": "a calm man in his fifties"}, "scenes": [host, dict(board), dict(host)]}
    assert documentary.lint(good) == []
    assert "a host scene in the middle" in documentary.lint({**good, "scenes": [host, host, host]})[0]["problem"]


def test_a_fix_returns_only_the_changed_scenes_and_a_worse_rewrite_is_never_kept(monkeypatch):
    prev = plan([scene(1), scene(2), scene(3)], fact_notes=["a"])
    merged = story_intel.merge_patch(prev, {"scenes": [{"n": 2, "title": "Fixed"}], "fact_notes": ["a", "b"]})
    assert [s["title"] for s in merged["scenes"]] == ["Scene 1", "Fixed", "Scene 3"]
    assert merged["scenes"][1]["what_happens"] == prev["scenes"][1]["what_happens"]  # fields it left out stay
    assert merged["fact_notes"] == ["a", "b"] and merged["title"] == prev["title"]
    assert len(story_intel.merge_patch(prev, {"scenes_all": [scene(1)]})["scenes"]) == 1  # a structural fix
    rev = lambda score, rules=0, facts=0, passed=False: {"passed": passed, "score": score, "rules": [{}] * rules,
                                                        "issues": [{"area": "facts"}] * facts}
    assert not story_intel._better(rev(82, rules=0), rev(86, rules=1))  # the 1816 case: lower score, fewer notes
    assert story_intel._better(rev(80, facts=0), rev(90, facts=2))  # a fact error fixed beats a nicer score
    assert story_intel._better(rev(70, passed=True), rev(90))

    sent = {}

    async def fake(model, messages, **kw):
        sent["stage"], sent["last"], sent["tokens"] = kw["stage"], messages[-1]["content"], kw["max_tokens"]
        return {"scenes": [{"n": 1, "title": "Better opening"}]}
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    cls = {"category": "fiction", "duration_s": 18}
    out = asyncio.run(story_intel.architect("a fable", cls, None, [], "9:16",
                                            [{"scene": 1, "area": "scenes", "problem": "weak", "fix": "stronger"}], prev))
    assert sent["stage"] == "story.architect.revise" and "ONLY what changes" in sent["last"] and sent["tokens"] == 16000
    assert [s["title"] for s in out["scenes"]] == ["Better opening", "Scene 2", "Scene 3"]
