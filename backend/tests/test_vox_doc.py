import asyncio
import json

from engines import story_intel
from engines import vox_doc_engine as v


def test_the_playbook_has_its_four_prompts():
    assert sorted(v.PROMPTS) == [1, 2, 3, 4] and all(len(t) > 500 for t in v.PROMPTS.values())


def test_documentaries_route_to_the_vox_documentary(monkeypatch):
    calls = []

    async def fake(model, messages, **kw):
        calls.append(kw["stage"])
        return {"category": "documentary", "agent": "story", "research": "deep", "subject": "Titanic"}
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    intel = asyncio.run(story_intel.develop("a documentary about the titanic", duration_s=None))
    assert intel["route"] == "documentary" and calls == ["story.classify"]


def test_sentences_keep_abbreviations_together():
    assert v.sentences("Dr. Smith sailed. It sank!\n\nThe end?") == ["Dr. Smith sailed.", "It sank!", "The end?"]


def test_lengths_snap_to_the_playbook():
    assert v.minutes_for(90) == 1 and v.minutes_for(330) == 5 and v.minutes_for(None) is None
    assert v.minutes_of("8 Minutes") == 8 and v.minutes_of("3 Minutes") is None


def test_the_finished_picture_is_held_before_the_transition():
    tl, _ = v.timeline([{"n": 1, "sentence": "x"}, {"n": 2, "sentence": "y"}], [3.0, 3.0])
    html = v.page(tl, [v._clean_layers({})] * 2, ["img/s001.png", "img/s002.png"], "img/plate.png", 9)
    assert "0.5 * D" in html and "0.7 * D" not in html
    d = tl[0]["t1"] - tl[0]["t0"]
    build, fade_end, transition = min(6, max(1.2, 0.5 * d)), min(6, max(1.2, 0.5 * d)) + 0.35, d - 0.5
    assert transition - fade_end >= 1.0  # at least a second of the complete illustration


def test_a_scene_without_its_image_makes_no_sound():
    tl, _ = v.timeline([{"n": 1, "sentence": "x"}, {"n": 2, "sentence": "y"}], [2.0, 2.0])
    html = v.page(tl, [v._clean_layers({})] * 2, ["img/s001.png", None], "img/plate.png", 6)
    scenes = json.loads(html.split("const SC = ", 1)[1].split(";\n", 1)[0])
    assert scenes[0]["layers"] and scenes[0]["layers"][0]["sfx"] and scenes[1]["layers"] == []


def test_the_narrator_voice():
    assert v.voice_gender("a documentary with a female narrator") == "female"
    assert v.voice_gender("documentary, male voice over please", {"narrator_voice": "female"}) == "male"
    assert v.voice_gender("history of rome", {"narrator_voice": "female"}) == "female"
    assert v.voice_gender("history of rome") == "male"


def test_a_documentary_with_a_host_or_archive_photos_goes_to_host_and_board():
    auto = lambda p: {"mode": "auto", "prompt": p}
    for p in ("a documentary about the fall of rome", "documentary on olympic host cities", "ship anchors documentary"):
        assert v.wanted(auto(p)), p
    for p in ("a documentary about the titanic with a host", "documentary presented by a woman on camera",
              "titanic documentary using real archive photos", "a documentary with a presenter", "documentary with a female host"):
        assert not v.wanted(auto(p)), p
    assert v.wanted({"mode": "documentary", "prompt": "a documentary with a host"})  # the VOX mode, chosen by hand


def test_story_intel_sends_a_hosted_documentary_to_the_cinematic_method(monkeypatch):
    calls = []

    async def fake(model, messages, **kw):
        calls.append(kw["stage"])
        if kw["stage"] == "story.classify":
            return {"category": "documentary", "agent": "story", "research": "none", "subject": "Titanic"}
        raise RuntimeError("stop after routing")
    monkeypatch.setattr(story_intel.llm, "chat_json", fake)
    try:
        intel = asyncio.run(story_intel.develop("a titanic documentary with a host", duration_s=None))
    except RuntimeError:
        intel = {}
    assert intel.get("route") != "documentary" and len(calls) > 1


def test_the_explainer_mode_with_no_file_makes_a_documentary():
    from engines import explainroo_engine as xr
    run = {"mode": "explainer", "assets": {}, "prompt": "The Untold History of Coca-Cola: From 1886 to a Global Empire."}
    assert v.wanted(run) and not xr.wanted(run)
    with_doc = {**run, "assets": {"a1": {"modality": "document"}}}
    assert xr.wanted(with_doc) and not v.wanted(with_doc)
    assert not v.wanted({**run, "prompt": "  "})


def test_one_documentary_mode_a_topic_or_a_file():
    from engines import explainroo_engine as xr
    topic = {"mode": "documentary", "assets": {}, "prompt": "The Untold History of Coca-Cola"}
    assert v.wanted(topic) and not xr.wanted(topic)
    upload = {**topic, "assets": {"a1": {"modality": "document"}}}
    assert xr.wanted(upload) and not v.wanted(upload)


def test_a_named_model_that_is_switched_off_fails_fast(monkeypatch):
    """Opus asked for by name and not enabled for the key: one request, then an error (the caller falls back)."""
    import httpx
    from router_core import config, llm
    posts = []

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            posts.append(kw["json"]["model"])
            return httpx.Response(403, text='{"error":{"message":"Vendor models are not switched on for this '
                                            'organization.","code":"vendor_not_enabled"}}')
    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(config, "PREFERRED_MODEL", "anthropic/claude-opus-5.5")
    monkeypatch.setattr(llm, "_down_until", {})
    for call in (llm.chat_json, llm.chat_text):
        try:
            asyncio.run(asyncio.wait_for(call("anthropic/claude-opus-5.5", [{"role": "user", "content": "x"}]), 5))
            raise AssertionError("should fail")
        except llm.LLMError:
            pass
    assert posts == ["anthropic/claude-opus-5.5"]  # the second call does not even ask: it is known to be off


def test_a_script_too_long_for_its_duration_is_shortened(monkeypatch):
    long = {k: " ".join(["word"] * 60) + "." for k in v.SECTIONS}  # 360 words for 1 minute (140-170)
    short = {k: " ".join(["word"] * 26) + "." for k in v.SECTIONS}  # 156 words
    stages = []

    async def fake(msgs, stage, *a, **k):
        stages.append(stage)
        return {"title": "T", **(long if stage == "engine.documentary.script" else short)}
    monkeypatch.setattr(v, "_json", fake)
    s = asyncio.run(v.write_script("Coca-Cola", 1))
    assert stages == ["engine.documentary.script", "engine.documentary.shorten"]
    assert len(v.script_text(s).split()) == 156 and s["title"] == "T" and s["minutes"] == 1


def test_square_pictures_are_widened_with_their_own_paper_and_plates_must_be_empty(tmp_path):
    import shutil
    import pytest
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("needs ffmpeg")
    sq = tmp_path / "s.png"  # beige paper with a red block drawn on it, square
    v._ff(["-f", "lavfi", "-i", "color=c=0xEADCC2:s=400x400", "-vf",
           "drawbox=x=120:y=120:w=160:h=160:color=red:t=fill", "-frames:v", "1", str(sq)])
    assert v.busyness(sq) > v.PLATE_BUSY
    c = v.paper_colour(sq)  # the paper's colour (within PNG/YUV rounding)
    assert all(abs(int(c[i:i + 2], 16) - int("EADCC2"[i - 2:i], 16)) <= 2 for i in (2, 4, 6))
    assert v.fit_aspect(sq, "16:9") and v._size(sq) == (712, 400) and not v.fit_aspect(sq, "16:9")
    plate = tmp_path / "plate.png"
    v.paper_plate(sq, plate, "16:9")
    assert v._size(plate) == (1920, 1080) and v.busyness(plate) < v.PLATE_BUSY


def _script():
    return {"title": "T", "hook": "A hook. Another.", "introduction": "Intro one. Intro two.",
            "main_story": "M1. M2. M3. M4. M5. M6.", "turning_point": "Twist.", "big_picture": "Big.",
            "ending": "End."}


def test_every_chapter_gets_its_own_paper():
    s = _script()
    ch = v.chapters(s)
    assert len(ch) == len(v.sentences(v.script_text(s))) == 13 and ch[0] == "hook" and ch[-1] == "ending"
    papers = v.scene_papers(s, "kraft_ink")
    L = v.LOOKS["kraft_ink"]
    assert papers[0] == L["dark"] and papers[2] == L["light"] and papers[10] == L["accent"]  # hook, intro, twist
    assert L["accent"] in papers[4:10] and L["light"] in papers[4:10]  # a long chapter changes paper too
    assert len(set(papers)) == 3


def test_the_look_is_never_one_used_recently(monkeypatch, tmp_path):
    from router_core import config
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    for k in ["noir", "kraft_ink", "newsprint", "blueprint", "teal_tech"]:
        v.remember_look(k)
    assert v.recent_looks() == ["teal_tech", "blueprint", "newsprint", "kraft_ink", "noir"]

    async def picks_a_recent_one(msgs, stage, *a, **k):
        assert "noir" not in msgs[0]["content"] and "sage_archive" in msgs[0]["content"]
        return {"look": "noir"}  # not allowed: a recent one
    monkeypatch.setattr(v, "_json", picks_a_recent_one)
    look = asyncio.run(v.choose_look(_script()))
    assert look in v.LOOKS and look not in v.recent_looks()


def test_each_scene_builds_on_its_own_paper():
    tl, _ = v.timeline([{"n": 1, "sentence": "x"}, {"n": 2, "sentence": "y"}], [2.0, 2.0])
    html = v.page(tl, [v._clean_layers({})] * 2, ["img/s001.png", "img/s002.png"], ["img/p001.png", "img/p002.png"], 6)
    scenes = json.loads(html.split("const SC = ", 1)[1].split(";\n", 1)[0])
    assert [s["plate"] for s in scenes] == ["img/p001.png", "img/p002.png"] and "plates[next]" in html
    assert v._with_paper({"paper": "deep teal paper", "image_prompt": "a fox"}, "9:16").startswith(
        "BACKGROUND: deep teal paper") and "Tall 9:16" in v._with_paper({"image_prompt": "a"}, "9:16")
