"""Short Drama Director: script -> cast -> one lip-synced shot per spoken line, episodes with the same cast."""

import asyncio
import json

from engines import explainroo_engine, presenter_engine, story_engine, story_intel
from engines.story_intel import drama, validate


def shot(n, speaker=None, line=None, **kw):
    s = {"n": n, "title": f"{speaker}: line" if speaker else f"Shot {n}", "duration_s": 5, "purpose": "conflict",
         "what_happens": ["", "Mira confronts Leo at the altar", "the guests gasp and freeze",
                          "Leo confesses about his brother"][min(n, 3)], "actions": "0-2 s: she looks up; 2-5 s: she steps in",
         "framing": f"medium close-up {n}", "camera_movement": f"slow push-in {n}", "lighting": "cold office light",
         "start_state": "Mira at the desk", "end_state": "Mira stands", "transition_in": "cut",
         "props": ["1 desk"], "prompt": {k: f"{k} " + "detail " * 25 for k in validate.PROMPT_FIELDS},
         "audio": ({"type": "dialogue", "speaker": speaker, "line": line} if line else {"type": "none"})}
    s.update(kw)
    return s


CAST = [{"name": "Mira", "age": 28, "voice_gender": "female", "face": "oval face"},
        {"name": "Leo", "age": 31, "voice_gender": "male", "face": "square jaw"},
        {"name": "Ava", "age": 50, "voice_gender": "female", "face": "sharp cheekbones"}]


def episode(scenes, **kw):
    p = {"title": "The Wedding Lie", "script": [{"scene": "S01", "heading": "INT. - hall - dusk", "action": "...",
                                                  "dialogue": []}],
         "bible": {"characters": CAST, "world": {"location": "wedding hall"}}, "scenes": scenes,
         "cliffhanger": "Leo's twin walks in", "episode": 1,
         "series": {"title": "Twin Vows", "episodes": [{"n": 1, "title": "Vows"}, {"n": 2, "title": "The Twin"}]}}
    p.update(kw)
    return p


def test_lint_checks_the_drama_rules():
    good = episode([shot(1, "Mira", "You knew all along?"), shot(2), shot(3, "Leo", "I wanted to tell you.")])
    assert drama.lint(good) == []
    problems = " | ".join(i["problem"] for i in drama.lint(episode([shot(1), shot(2), shot(3)], script=None,
                                                                     cliffhanger="")))
    assert "no script" in problems and "no dialogue" in problems and "no cliffhanger" in problems
    bad = episode([shot(1, "Zed", "Who are you?"), shot(2, "Mira", " ".join(["word"] * 25))],
                  bible={"characters": [{"name": "Mira"}], "world": {}})
    problems = " | ".join(i["problem"] for i in drama.lint(bad))
    assert "'Zed' is not in bible.characters" in problems and "25 words" in problems and "no voice_gender" in problems
    final = episode([shot(1, "Mira", "It is over.")], cliffhanger="", episode=2)  # the last episode: a payoff
    assert not [i for i in drama.lint(final) if "cliffhanger" in i["problem"]]


def test_every_character_gets_their_own_voice():
    v = drama.voices(episode([]), "english")
    assert v["Mira"]["gender"] == "female" and v["Leo"]["gender"] == "male"
    assert v["Mira"]["voice"] != v["Ava"]["voice"]  # two women, two voices
    assert v["Mira"]["voice"] in presenter_engine.KOKORO_POOL["en-us"]["female"]


def test_follow_ups_and_document_routing():
    assert drama.wants_episode("make the next episode") == 0 and drama.wants_episode("now episode 4") == 4
    assert drama.wants_episode("continue the series") == 0 and drama.wants_episode("make it brighter") is None
    assert drama.next_number({"current": 2}, 0) == 3 and drama.next_number({"current": 2}, 5) == 5
    doc = {"a1": {"modality": "document"}}
    assert not explainroo_engine.wanted({"mode": "auto", "assets": doc, "prompt": "turn this novel into a short drama"})
    assert explainroo_engine.wanted({"mode": "auto", "assets": doc, "prompt": "explain this chapter"})


def test_develop_writes_script_cast_and_shots(monkeypatch):
    calls = []

    async def fake(model, messages, **kw):
        stage = kw["stage"]
        calls.append(stage)
        if stage == "story.classify":
            return {"category": "short_drama", "agent": "story", "research": "none", "language": "english",
                    "duration_s": 15}
        if stage == "story.drama":
            system, user = messages[0]["content"], messages[1]["content"]
            assert "SHORT DRAMA DIRECTOR" in system and "CLIFFHANGER" in system and "Hook line" in system
            assert "SOURCE TEXT" in user and "Mira married the wrong twin" in user
            return episode([shot(1, "Mira", "You knew all along?"), shot(2), shot(3, "Leo", "I wanted to tell you.")])
        if stage == "story.validate":
            assert "SHORT DRAMA episode" in messages[0]["content"]
            return {"score": 90, "pass": True, "issues": []}
        raise AssertionError(stage)
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    intel = asyncio.run(story_intel.develop("make a short drama from this novel", [],
                                            [{"filename": "novel.txt", "text": "Mira married the wrong twin."}]))
    assert calls == ["story.classify", "story.events", "story.drama", "story.validate"]  # no research: the novel is the source
    assert intel["mode"] == "short_drama" and intel["aspect_ratio"] == "9:16"
    assert intel["plan"]["voices"]["Leo"]["gender"] == "male"
    assert intel["series"]["title"] == "Twin Vows" and len(intel["series"]["episodes"]) == 2
    assert {c["name"] for c in intel["series"]["characters"]} == {"Mira", "Leo", "Ava"}
    brief = story_intel.as_brief(intel)
    assert "vertical short drama, episode 1 of 2" in brief and "lip-synced" in brief
    block = json.loads(story_intel.engine_block(intel))
    assert "short drama" in block["mode"] and block["voices"]["Mira"]["voice"]


def test_next_episode_keeps_the_cast(monkeypatch):
    series = {"title": "Twin Vows", "current": 1, "episodes": [{"n": 1, "title": "Vows", "synopsis": "the wedding",
                                                                "cliffhanger": "the twin arrives"},
                                                               {"n": 2, "title": "The Twin", "synopsis": "who is who"}],
              "characters": [{"name": "Mira", "desc": "oval face", "voice_gender": "female"}],
              "cast": [{"name": "Mira", "passport": "https://x/p.png", "front": "https://x/f.png", "back": None}]}
    seen = {}

    async def fake(model, messages, **kw):
        if kw["stage"] == "story.classify":  # the follow-up's words look like anything: it is still the drama
            return {"category": "other", "research": "deep"}
        if kw["stage"] == "story.drama":
            seen["user"] = messages[1]["content"]
            return episode([shot(1, "Mira", "Which one of you did I marry?")], episode=None, cliffhanger="")
        return {"score": 90, "pass": True, "issues": []}
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    intel = asyncio.run(story_intel.develop("next episode", series=series, episode=2))
    assert "THIS IS EPISODE 2 of 2" in seen["user"] and "THE CAST IS LOCKED" in seen["user"]
    assert "the twin arrives" in seen["user"]
    assert intel["plan"]["episode"] == 2 and intel["series"]["current"] == 2
    assert intel["series"]["cast"][0]["passport"] == "https://x/p.png"  # the drawn faces carry over


def test_engine_reuses_drawn_faces_and_finds_voices():
    members = [{"name": "Mira", "desc": "Mira: oval face"}, {"name": "Leo", "desc": "Leo: square jaw"}]
    out = story_engine.reuse_cast(members, [{"name": "mira", "passport": "P", "front": "F", "back": "B"}])
    assert out[0]["urls"] == ["P", "F", "B"] and "urls" not in out[1]
    kept = story_engine.remember_cast([{"name": "Mira", "passport": "P"}],
                                      [{"name": "Leo", "passport": "L", "front": "LF", "back": None}])
    assert {c["name"] for c in kept} == {"Mira", "Leo"}
    voices = {"Mira": {"gender": "female", "voice": "af_bella"}, "Leo Chen": {"gender": "male", "voice": "am_adam"}}
    assert story_engine._voice_of("mira", voices)["voice"] == "af_bella"
    assert story_engine._voice_of("Leo", voices)["voice"] == "am_adam"
    assert story_engine._voice_of("Nobody", voices) == {"gender": "female"}


def test_speaker_talks_in_the_clip_prompt():
    q = {"video_model": "kling_3_0_pro", "audio": False}
    talking = story_engine._motion("pltt-storyboard-video-maker",
                                   {"action": "Mira grips the glass", "image_prompt": "x", "line": "You knew?",
                                    "speaker": "Mira", "voice": "on_camera"}, q)
    assert "Mira is talking the whole shot" in talking and "nothing covers the mouth" in talking
    quiet = story_engine._motion("pltt-storyboard-video-maker", {"action": "Leo turns away", "image_prompt": "x"}, q)
    assert "talking the whole shot" not in quiet


def test_speak_uses_the_characters_voice(monkeypatch, tmp_path):
    seen = {}

    async def fake_local(cmd, what, timeout=0):
        seen["voice"] = cmd[cmd.index("--voice") + 1]
        return {"ok": True}
    monkeypatch.setattr(presenter_engine, "run_local", fake_local)
    monkeypatch.setattr(presenter_engine, "local_ready", lambda: None)
    asyncio.run(presenter_engine.speak("Hi", "male", "english", "", tmp_path / "a", [], voice="am_onyx"))
    assert seen["voice"] == "am_onyx"
    asyncio.run(presenter_engine.speak("Hi", "male", "english", "", tmp_path / "b", [], voice="bogus"))
    assert seen["voice"] == "am_michael"  # an unknown voice falls back to the language's default
