"""The Flow Video / Storyboard & Video engines, with fal and the planner faked (real ffmpeg joins the clips)."""

import asyncio
import shutil
import subprocess

import pytest

from engines import story_engine

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _clip(path, colour):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:size=480x854:rate=24:duration=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


@pytest.mark.parametrize("app_id", ["pltt-flow-video-2", "pltt-storyboard-video-maker"])
def test_engine_plans_makes_stills_clips_and_joins(app_id, tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setenv("PALETTE_VIDEO_QUALITY", "test")
    seen = {"system": None, "images": 0, "videos": []}
    clips = {}
    for i, c in enumerate(["red", "blue"]):
        clips[f"https://fal.media/clip{i}.mp4"] = tmp_path / f"src{i}.mp4"
        _clip(clips[f"https://fal.media/clip{i}.mp4"], c)

    async def fake_chat(model, messages, **kw):
        if kw.get("stage") == "engine.story.locations":  # no places planned: the look frame anchors the scenes
            return {}
        if kw.get("stage") == "engine.critic":
            return {"score": 90, "issues": []}
        if kw.get("stage") == "engine.story.consistency":
            seen["checks"] = seen.get("checks", 0) + 1
            return {"same": True, "differences": []}
        if kw.get("stage") == "engine.story.face":  # the face-only identity check beside it
            seen["faces"] = seen.get("faces", 0) + 1
            return {"faces": [{"name": "girl", "visible": True, "score": 9, "box": [0.3, 0.1, 0.7, 0.5]}]}
        if kw.get("stage") == "engine.graphics_director":
            seen["director"] = True
            return {"add_graphics": False, "why": "a mood story; the pictures carry it"}
        if kw.get("stage") == "engine.clip_qa":  # every finished clip is analysed from frames across it
            seen["clip_qa"] = seen.get("clip_qa", 0) + 1
            assert sum(1 for x in messages[0]["content"] if x.get("type") == "image_url") >= 2
            return {"ok": True, "issues": []}
        if kw.get("stage") == "engine.scene_qa":  # the first frame checked has an extra plate; the redraw is clean
            seen["qa"] = seen.get("qa", 0) + 1
            if seen["qa"] == 1:
                return {"ok": False, "issues": ["an extra plate on the table -> remove it"]}
            return {"ok": True, "issues": []}
        seen["system"] = messages[0]["content"]
        return {"title": "Lost Cat", "story": "A girl finds a cat.", "bible": {"characters": "girl, 10, red coat"},
                "scenes": [{"title": "Alley", "duration": 5, "image_prompt": "girl in alley", "camera": "slow push-in",
                            "end_state": "she kneels by the cat"},
                           {"title": "Found", "duration": 5, "image_prompt": "girl hugs cat", "camera": "static"}]}

    def fake_image(key, model, prompt, **kw):
        seen["images"] += 1
        if prompt.startswith("PASSPORT-STYLE ID PORTRAIT of girl, 10, red coat"):  # the character comes first
            assert kw["aspect_ratio"] == "3:4" and "whole hairstyle" in prompt and "image_urls" not in kw
            seen["passport"] = True
            return {"images": [{"url": "https://fal.media/passport.png"}]}
        if prompt.startswith(("FULL-BODY FRONT VIEW of girl", "FULL-BODY BACK VIEW of girl")):  # from the portrait
            side = "front" if "FRONT" in prompt[:20] else "back"
            # the back is drawn after the front and from it, so both show the same outfit
            want = ["https://fal.media/passport.png"] + (["https://fal.media/front.png"] if side == "back" else [])
            assert kw["image_urls"] == want and kw["aspect_ratio"] == "9:16"
            assert side == "front" or seen.get("front")
            seen[side] = True
            return {"images": [{"url": f"https://fal.media/{side}.png"}]}
        if prompt.startswith("LOOK FRAME"):  # the film's world, drawn once beside the characters
            seen["look"] = True
            return {"images": [{"url": "https://fal.media/look.png"}]}
        assert "GLOBAL STYLE" in prompt and kw["aspect_ratio"] == "9:16"
        assert not seen["videos"]  # every scene image is drawn before any clip starts
        if prompt.startswith("The LAST frame"):
            seen["end"] = "https://fal.media/end1.png"
            return {"images": [{"url": seen["end"]}]}
        # every scene with the girl is drawn from her passport portrait, front and back, then the look frame
        assert kw["image_urls"][:4] == ["https://fal.media/passport.png", "https://fal.media/front.png",
                                        "https://fal.media/back.png", "https://fal.media/look.png"]
        assert "CHARACTER REFERENCES: reference images 1-3" in prompt
        return {"images": [{"url": f"https://fal.media/still{seen['images']}.png"}]}

    def fake_video(key, model, prompt, **kw):
        # realistic people: Seedance refuses them as real persons, so the clip goes to Kling (with her elements)
        assert model == "kling_3_0_standard" and kw["audio"] is False and kw["start_image"]
        url = f"https://fal.media/clip{len(seen['videos'])}.mp4"
        seen["videos"].append(kw["duration"])
        return {"video": {"url": url}}

    async def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(clips[url], dest)
        return str(dest)

    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    monkeypatch.setattr(story_engine.fal_helper, "generate_image", fake_image)
    monkeypatch.setattr(story_engine.fal_helper, "generate_video", fake_video)
    monkeypatch.setattr(story_engine, "_download", fake_download)
    monkeypatch.setattr(story_engine.music, "available", lambda: "off in tests")
    msgs = []

    async def progress(m):
        msgs.append(m)

    shown = []

    async def live(data):  # the live view: what is really made so far
        shown.append([(x["still_url"], x["clip_url"]) for x in data["scenes"]])

    progress.live = live

    app = {"app_id": app_id, "name": "App"}
    handoff = {"prompt": "40s reels story, girl finds lost cat", "request": {"duration": 40, "total_duration": 40, "aspect_ratio": "9:16"}}
    out = asyncio.run(story_engine.run(app, handoff, {}, tmp_path / "work", progress))
    marker = "film director planning a short AI-generated video"
    assert marker in seen["system"]  # the app's own planner prompt
    if app_id == "pltt-flow-video-2":
        assert "ENGINES (the models that render this film" in seen["system"]
    # passport, front and back, the look frame, one still per scene, one last frame, and one redraw after the AI
    # check found an extra plate
    assert seen["images"] == 8 and seen["passport"] and seen["front"] and seen["back"] and seen["look"]
    assert len(out["scenes"]) == 2
    assert seen["qa"] == 4  # every frame analysed (3) plus the redraw re-checked
    assert out["scenes"][0]["qa"]["first"] == {"issues": [], "redrawn": True,
                                               "fixed": ["MAJOR: an extra plate on the table -> remove it"]}
    assert out["scenes"][0]["image_prompt"] and out["scenes"][0]["motion_prompt"]
    assert seen["clip_qa"] == 2 and out["scenes"][1]["qa"]["clip"] == {"issues": [], "retaken": False}
    # the character check runs on both first frames and on the last frame the clip lands on
    assert out["plan"]["cast_sheet_url"] == "https://fal.media/passport.png" and seen["checks"] == 3
    assert seen["faces"] == 3 and any("realistic people in this film" in m for m in msgs)
    assert out["clip_models"][0] == "kling_4_0" and out["clip_models"][-1] == "seedance_2_0"
    assert out["plan"]["cast"][0]["back"] == "https://fal.media/back.png"
    assert out["plan"]["total"] == 10 and out["plan"]["requested_total"] == 40  # test quality caps the length
    assert 9.5 < out["duration_s"] < 10.5
    assert any("planning" in m for m in msgs) and any("joining" in m for m in msgs)
    assert seen.get("director") and any("no graphics needed" in m for m in msgs)  # the director decided: none
    assert out["graphics"]["skipped"] is True and out["path"].endswith("final.mp4")
    assert shown[0] == [(None, None), (None, None)]  # the scene list first, then each still, then each clip
    assert any(st[0][0] and not st[0][1] for st in shown) and all(a and b for a, b in shown[-1])


def test_engine_without_fal_key_fails_clearly(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(story_engine.EngineError, match="FAL_KEY"):
        story_engine.fal_key()


def test_refused_clip_falls_back_to_the_next_model(monkeypatch):
    calls = []

    def fake_video(key, model, prompt, **kw):
        calls.append((model, kw.get("resolution")))
        if model == "seedance_2_0":
            raise RuntimeError("content_policy_violation: may contain likenesses of real people")
        return {"video": {"url": "https://fal.media/k.mp4"}}

    async def progress(m):
        pass

    monkeypatch.setattr(story_engine.fal_helper, "generate_video", fake_video)
    url, used = asyncio.run(story_engine._clip({"app_id": "pltt-flow-video-2", "name": "App"}, 0, 1,
                                               {"duration": 5, "image_prompt": "girl", "camera": "push-in"},
                                               "https://s.png", "9:16", story_engine.QUALITY["test"], "k", progress))
    assert used == "kling_4_0" and url.endswith("k.mp4")
    assert calls == [("seedance_2_0", "480p"), ("kling_4_0", "720p")]  # Kling 4.0 only takes 720p / 1080p


def test_a_failing_clip_model_falls_back_down_the_whole_chain(monkeypatch):
    calls = []

    def flaky(key, model, prompt, **kw):
        calls.append(model)
        if model != "kling_3_0_standard":
            raise RuntimeError("502 bad gateway")
        return {"video": {"url": "https://fal.media/k.mp4"}}

    async def progress(m):
        pass

    monkeypatch.setattr(story_engine.fal_helper, "generate_video", flaky)
    url, used = asyncio.run(story_engine._clip({"app_id": "x", "name": "App"}, 0, 1, {"duration": 5, "image_prompt": "a"},
                                               "https://s.png", "16:9", story_engine.QUALITY["test"], "k", progress))
    assert used == "kling_3_0_standard" and calls == ["seedance_2_0", "kling_4_0", "kling_3_0_standard"]


def test_every_clip_model_failing_is_reported(monkeypatch):
    def boom(key, model, prompt, **kw):
        raise RuntimeError("502 bad gateway")

    async def progress(m):
        pass

    monkeypatch.setattr(story_engine.fal_helper, "generate_video", boom)
    with pytest.raises(story_engine.EngineError, match="502"):
        asyncio.run(story_engine._clip({"app_id": "x", "name": "App"}, 0, 1, {"duration": 5, "image_prompt": "a"},
                                       "https://s.png", "16:9", story_engine.QUALITY["test"], "k", progress))


def test_engine_adapter_module_imports():
    """The backend loads engines.adapter at startup; a syntax error there takes the whole app down."""
    import importlib

    import engines.adapter as adapter
    importlib.reload(adapter)
    assert adapter.EngineAdapter.wants_context


def test_critic_low_score_rewrites_the_plan_with_its_issues(monkeypatch):
    calls = []

    async def fake_chat(model, messages, **kw):
        calls.append(kw.get("stage"))
        if kw.get("stage") == "engine.critic":
            return {"score": 40 if calls.count("engine.critic") == 1 else 88, "issues": ["scene 2: only a camera move"]}
        return {"title": "T", "story": "S", "scenes": [
            {"title": "A", "duration": 5, "image_prompt": "x", "beat": "b", "action": ["0-2 s: she runs", "2-5 s: falls"],
             "weight": 3, "voice": "none"}]}

    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    q = story_engine.QUALITY["test"]
    first = asyncio.run(story_engine.plan("pltt-flow-video-2", {"prompt": "p", "request": {"total_duration": 5}}, [], q))
    assert first["scenes"][0]["action"] == "0-2 s: she runs 2-5 s: falls"  # list fields are joined
    review = asyncio.run(story_engine.critique("p", first))
    assert review["passed"] is False and review["issues"] == ["scene 2: only a camera move"]
    seen = {}

    async def capture(model, messages, **kw):
        seen["user"] = messages[1]["content"]
        return await fake_chat(model, messages, **kw)

    monkeypatch.setattr(story_engine.llm, "chat_json", capture)
    asyncio.run(story_engine.plan("pltt-flow-video-2", {"prompt": "p", "request": {"total_duration": 5}}, [], q,
                                  feedback=review["issues"]))
    assert "scene 2: only a camera move" in seen["user"]


def test_fit_to_narration_only_lengthens_within_limits(monkeypatch):
    lengths = {"a.mp3": 6.4, "b.mp3": 1.0, "c.mp3": 30.0}
    monkeypatch.setattr(story_engine.video_merge, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(story_engine.video_merge, "probe_duration", lambda ff, f: lengths[f])
    assert story_engine.fit_to_narration([4, 5, 6, 7], ["a.mp3", "b.mp3", "c.mp3", None], 3, 15) == [8, 5, 15, 7]


def test_retime_stretches_beats_to_the_new_length():
    s = {"duration": 4, "action": "0-2 s: she laces up; 2–4 s: stands. 4-5 s: looks up"}
    story_engine.retime(s, 10)
    assert s["duration"] == 10 and s["action"] == "0-4 s: she laces up; 4-8 s: stands. 8-10 s: looks up"
    t = {"duration": 5, "action": "she runs"}
    story_engine.retime(t, 7)
    assert t == {"duration": 7, "action": "she runs"}


def test_narration_lines_never_overlap_each_other():
    # scene 1 line (9 s) is far too long for its 4 s scene; scene 2's line must still start after it ends
    starts, ends = [0, 4, 8], [4, 8, 12]
    placed = story_engine.place_narration(starts, ends, [9.0, 2.0, 2.0], [False, False, False], 12)
    spans = [(s, u) for s, _r, u in placed]
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert b0 >= a1 + 0.2  # a gap between lines, never overlapping
    assert all(r <= 1.3 for _s, r, _u in placed)


def test_narration_never_runs_into_on_camera_speech():
    placed = story_engine.place_narration([0, 4], [4, 8], [6.0, None], [False, True], 8)
    start, rate, until = placed[0]
    assert until <= 3.9 and placed[1] is None  # faded out before the on-camera speaker starts


def test_character_drift_is_redrawn_once(monkeypatch):
    """A still that differs from the character sheet is caught by the vision check and the fix is put in the prompt."""
    async def fake_chat(model, messages, **kw):
        assert kw["stage"] == "engine.story.consistency"
        return {"same": False, "differences": ["pink sneakers instead of red rain boots"]}

    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    p = {"bible": {"characters": "girl in a red raincoat and red rain boots"}}
    assert asyncio.run(story_engine.consistency_issues("https://s/sheet.png", "https://s/still.png", p)) == [
        "MINOR: pink sneakers instead of red rain boots"]  # untagged and no face / hair / outfit word: minor


def test_continuity_sheet_becomes_hard_rules():
    c = {"entities": [{"name": "the girl", "kind": "character", "locked": ["glossy red raincoat", "red rain boots"],
                       "never": ["sneakers"]}]}
    text = story_engine._locks(c)
    assert "red rain boots" in text and "never: sneakers" in text
    assert story_engine.recurring_subject({"continuity": c, "scenes": [{}]}) and story_engine.has_cast({"continuity": c})
    assert not story_engine.recurring_subject({"bible": {"characters": ""}, "scenes": [{}, {}]})


def test_uploaded_person_comes_first_and_is_named_in_the_still_prompt():
    refs = [{"url": "p", "role": "product"}, {"url": "c", "role": "character"}, {"url": "b", "role": "background"}]
    ordered = story_engine._ordered_refs(refs)
    assert [r["url"] for r in ordered] == ["c", "p", "b"]
    note = story_engine._refs_note(ordered)
    assert "reference image 1 is THE PERSON" in note and "reference image 2 is the product" in note
    assert "the photo wins" in note and story_engine._refs_note([]) == ""


def test_identity_check_flags_a_different_person(monkeypatch):
    async def fake_chat(model, messages, **kw):
        assert kw["stage"] == "engine.story.identity"
        return {"same": False, "differences": ["looks 15 years older"]}
    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    assert asyncio.run(story_engine.identity_issues("photo", "still")) == ["looks 15 years older"]


def test_how_to_briefs_get_the_real_routine_rules():
    from engines import planners
    q = {"video_model": "kling_3_0_pro", "image_model": "nano_banana_pro", "audio": True}
    sys1, _ = planners.prompt_for("pltt-storyboard-video-maker", "show her applying a vitamin C serum", 30, "9:16",
                                  3, 10, 6, [], q, "ugc_ad")
    sys2, _ = planners.prompt_for("pltt-storyboard-video-maker", "a girl finds a lost cat", 30, "9:16",
                                  3, 10, 6, [], q, "cinematic")
    assert "PRODUCT IN USE" in sys1 and "wash the face at the sink" in sys1 and "PRODUCT IN USE" not in sys2


def test_scene_objects_and_ai_check_spec():
    from engines import story_engine as se
    s = {"title": "Layer", "start_state": "empty handi with chicken at the base", "end_state": "layered biryani",
         "props": ["1 steel handi", "1 colander of rice"], "action": "0-3 s: rice is layered"}
    first = se.props_line(s)
    assert "exactly these" in first and "1 steel handi" in first and "No additional plates" in first
    assert "plus only what the action itself brings in" in se.props_line(s, last=True)
    assert se.props_line({}) == ""
    spec = se._qa_spec(s, "first")
    assert "empty handi" in spec and "layered biryani" not in spec and "1 colander" in spec
    assert "layered biryani" in se._qa_spec(s, "last")


def test_large_change_does_not_morph_and_retake_lists_the_problems():
    from engines import story_engine as se
    assert se.use_end_frame({"change": "medium"}) and not se.use_end_frame({"change": "large"})
    q = {"video_model": "kling_3_0_pro", "audio": False}
    s = {"action": "0-3 s: the hare sprints", "image_prompt": "hare"}
    retake = se._motion("pltt-storyboard-video-maker", s, q, fix="the hare turns into a second hare")
    assert "AVOID" in retake and "second hare" in retake and "AVOID" not in se._motion("x", s, q)


def test_each_character_gets_passport_front_and_back_and_only_its_scenes_use_them():
    p = {"bible": {"characters": "unused", "grading": "warm 35mm film look"},
         "continuity": {"entities": [{"kind": "character", "name": "Mina", "locked": ["30s", "short black bob", "navy suit"]},
                                     {"kind": "prop", "name": "mug"},
                                     {"kind": "character", "name": "Leo", "locked": ["10", "red raincoat"]}]}}
    members = story_engine.cast_members(p)
    assert members == ["Mina: 30s; short black bob; navy suit", "Leo: 10; red raincoat"]
    views = {v: t for v, _, t in story_engine.CAST_VIEWS}
    prompt = story_engine.view_prompt(views["passport"], members[0], story_engine.art_style(p))
    assert prompt.startswith("PASSPORT-STYLE ID PORTRAIT of Mina: 30s; short black bob; navy suit: head and shoulders")
    assert "whole hairstyle" in prompt and prompt.endswith("warm 35mm film look.")
    assert "back of the hairstyle" in views["back"] and "head to toe" in views["front"]
    # a bible that lists its characters gives one per character, not one combined description
    listed = {"bible": {"characters": [{"name": "Asha", "look": "long braid"}, "an old fisherman"]}}
    assert story_engine.cast_members(listed) == ["Asha: long braid", "an old fisherman"]
    assert story_engine.art_style({}) == "photorealistic, true-to-life detail"
    cast = [{"name": "Mina", "urls": ["m1", "m2", "m3"], "views": ["passport portrait", "full-body front", "full-body back"]},
            {"name": "Leo", "urls": ["l1"], "views": ["passport portrait"]}]
    assert story_engine.scene_cast({"image_prompt": "Leo jumps in a puddle"}, cast) == [cast[1]]
    assert story_engine.scene_cast({"image_prompt": "a woman pours tea"}, cast) == cast[:1]  # unnamed: the lead only
    assert story_engine.scene_cast({"image_prompt": "a couple pours tea"}, cast) == cast  # a group: everyone
    assert story_engine.scene_cast({"image_prompt": "macro of a steaming mug"}, cast) == []  # no person: no refs
    note = story_engine._cast_note(cast)
    assert "reference images 1-3 are Mina" in note and "reference image 4 is Leo" in note and "blank" in note
    assert "reference images 2-4 are Mina" in story_engine._cast_note(cast, first=2)


def test_archive_photos_are_fetched_politely_and_pinned_exactly(tmp_path, monkeypatch):
    import httpx
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, content=b"jpeg-bytes")
    real = httpx.AsyncClient
    monkeypatch.setattr(story_engine.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def fake_upload(path):
        return f"https://fal.media/{path.rsplit('/', 1)[-1]}"
    monkeypatch.setattr(story_engine, "_upload", fake_upload)
    photos = [{"id": "A1", "url": "https://upload.wikimedia.org/a/First_flight.jpg", "what": "First flight"},
              {"id": "A2", "url": "https://upload.wikimedia.org/b/unused.jpg", "what": "unused"}]
    got = asyncio.run(story_engine.archive_refs(photos, [{"archive": ["A1"]}, {}], tmp_path))
    assert got == {"A1": "https://fal.media/A1.jpg"} and "VideoStudio-story-research" in seen["ua"]
    note = story_engine._archive_note([photos[0]], 3)
    assert "reference image 3 is a REAL ARCHIVE PHOTO (First flight)" in note and "never redrawn" in note
    assert story_engine._archive_note([], 1) == ""


def _talk(path, seconds=3):
    """A clip WITH sound (the host's lip-synced line)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=green:size=480x854:rate=24:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


@pytest.mark.parametrize("host_works", [True, False])
def test_documentary_host_is_cut_in_on_camera_or_narrated_when_lipsync_fails(host_works, tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setenv("PALETTE_VIDEO_QUALITY", "test")
    board_clip = tmp_path / "board.mp4"
    _clip(board_clip, "blue")
    seen = {"images": [], "videos": 0, "host": None, "narrated": []}

    async def fake_chat(model, messages, **kw):
        stage = kw.get("stage") or ""
        if stage == "engine.critic":  # an approved Story Intelligence plan is not reviewed a second time
            raise AssertionError("the approved story was reviewed again")
        if stage in ("engine.scene_qa", "engine.clip_qa"):
            return {"ok": True, "issues": []}
        return {"title": "Unsinkable", "story": "A ship sinks.", "bible": {"characters": ""},
                "scenes": [{"title": "Host open", "duration": 5, "image_prompt": "host", "camera": "static",
                            "voice": "on_camera", "sound": "One night in April."},
                           {"title": "The ship", "duration": 5, "image_prompt": "pinned print of a liner on cork",
                            "camera": "slow push-in", "voice": "narration", "script": "The ship left port."}]}

    def fake_image(key, model, prompt, **kw):
        seen["images"].append(prompt[:40])
        return {"images": [{"url": f"https://fal.media/img{len(seen['images'])}.png"}]}

    def fake_video(key, model, prompt, **kw):
        seen["videos"] += 1
        return {"video": {"url": "https://fal.media/board.mp4"}}

    async def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(board_clip, dest)
        return str(dest)

    async def fake_host(app, host, lines, ar, work, progress, language="english"):
        seen["host"] = (host["voice_gender"], lines)
        if not host_works:
            raise RuntimeError("every lip-sync model failed")
        out = tmp_path / "host_01.mp4"
        _talk(out)
        return [str(out)]

    async def fake_narration(lines, work, key, language="english", used=None, gender="female"):
        out = []
        for i, line in enumerate(lines):
            if not line:
                out.append(None)
                continue
            seen["narrated"].append((line, gender))
            f = tmp_path / f"vo_{len(seen['narrated'])}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.5",
                            str(f)], check=True)
            out.append(str(f))
        return out

    async def passthrough(app, brief, plan, video, *a, **kw):
        return {"path": video, "clean": video, "text_layer": {"items": []}, "effects": None, "graphics": None}

    from engines import presenter_engine, text_layer
    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    monkeypatch.setattr(story_engine.fal_helper, "generate_image", fake_image)
    monkeypatch.setattr(story_engine.fal_helper, "generate_video", fake_video)
    monkeypatch.setattr(story_engine, "_download", fake_download)
    monkeypatch.setattr(story_engine, "record_narration", fake_narration)
    monkeypatch.setattr(presenter_engine, "host_clips", fake_host)
    monkeypatch.setattr(text_layer, "finish", passthrough)
    monkeypatch.setattr(story_engine.music, "available", lambda: "off in tests")

    story = {"mode": "cinematic_doc", "classification": {"language": "en"},
             "plan": {"host": {"look": "a calm man in his fifties", "voice_gender": "male"},
                      "scenes": [{"n": 1, "host": True, "duration_s": 5,
                                  "audio": {"type": "dialogue", "line": "One night in April, a ship sank."}},
                                 {"n": 2, "duration_s": 5, "hero": "the liner print",
                                  "audio": {"type": "narration", "line": "The ship left port."}}]}}
    handoff = {"prompt": "titanic documentary", "_story": story,
               "request": {"duration": 10, "total_duration": 10, "aspect_ratio": "9:16"}}

    async def progress(m):
        pass

    out = asyncio.run(story_engine.run({"app_id": "pltt-storyboard-video-maker", "name": "App"}, handoff, {},
                                       tmp_path / "work", progress))
    assert seen["host"] == ("male", ["One night in April, a ship sank."])
    from engines.vendor.flow import video_merge
    assert video_merge.has_audio_stream(video_merge.find_ffmpeg(), out["path"])  # the host's voice survives the join
    if host_works:
        assert out["scenes"][0]["host"] and out["scenes"][0]["model"] == "host (lip-sync)"
        assert seen["videos"] == 1 and not any("host" == p for p in seen["images"])  # only the board scene is filmed
        assert seen["narrated"] == [("The ship left port.", "male")]  # the narrator IS the host's voice
    else:  # no lip-sync: the host's line is narrated over a board scene and the film still finishes
        assert seen["videos"] == 2 and ("One night in April, a ship sank.", "male") in seen["narrated"]


def test_places_are_drawn_first_and_every_scene_is_drawn_inside_its_place(tmp_path, monkeypatch):
    """The boy-and-the-door fix: an empty plate per place before the scenes (no look frame then); each scene's still
    gets its place's plate after its characters, a LOCKED LOOK in words, and a drifted still is EDITED, not redrawn."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setenv("PALETTE_VIDEO_QUALITY", "test")
    _clip(tmp_path / "src.mp4", "red")
    seen = {"images": [], "checks": 0}

    async def fake_chat(model, messages, **kw):
        st = kw.get("stage")
        if st == "engine.story.locations":
            return {"locations": [{"id": "L1", "name": "attic bedroom", "plate": "slanted blue walls, brass bed"},
                                  {"id": "L2", "name": "forest", "plate": "moss and tall trees"}],
                    "scenes": ["L1", "L2"]}
        if st == "engine.story.consistency":
            seen["checks"] += 1
            imgs = [p["image_url"]["url"] for p in messages[0]["content"][1:]]
            assert imgs[-2] in ("https://fal.media/plate_L1.png", "https://fal.media/plate_L2.png")  # the place too
            if seen["checks"] == 1:  # the first still has the wrong pyjamas
                return {"same": False, "differences": ["MAJOR: plain navy pyjamas instead of striped"]}
            return {"same": True, "differences": []}
        if st in ("engine.critic",):
            return {"score": 90, "issues": []}
        if st == "engine.story.face":
            return {"faces": []}
        if st in ("engine.scene_qa", "engine.clip_qa"):
            return {"ok": True, "issues": []}
        if st == "engine.graphics_director":
            return {"add_graphics": False, "why": "story"}
        return {"title": "Door", "story": "A boy finds a door.",
                "bible": {"characters": [{"name": "Eliot", "clothing": "navy-and-white striped pyjamas"}]},
                "scenes": [{"title": "Eliot wakes", "duration": 5, "image_prompt": "Eliot in navy pyjamas sits up in bed",
                            "camera": "static"},
                           {"title": "Eliot in the forest", "duration": 5, "image_prompt": "Eliot steps onto moss",
                            "camera": "static"}]}

    def fake_image(key, model, prompt, **kw):
        seen["images"].append((prompt, kw.get("image_urls") or []))
        if prompt.startswith("PASSPORT-STYLE"):
            return {"images": [{"url": "https://fal.media/passport.png"}]}
        if prompt.startswith("FULL-BODY"):
            return {"images": [{"url": f"https://fal.media/{'front' if 'FRONT' in prompt[:20] else 'back'}.png"}]}
        if prompt.startswith("LOOK FRAME"):
            raise AssertionError("with places planned the look frame is not drawn")
        if prompt.startswith("LOCATION PLATE"):
            return {"images": [{"url": f"https://fal.media/plate_{'L1' if 'slanted' in prompt else 'L2'}.png"}]}
        return {"images": [{"url": f"https://fal.media/still{len(seen['images'])}.png"}]}

    async def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_path / "src.mp4", dest)
        return str(dest)

    monkeypatch.setattr(story_engine.llm, "chat_json", fake_chat)
    monkeypatch.setattr(story_engine.fal_helper, "generate_image", fake_image)
    monkeypatch.setattr(story_engine.fal_helper, "generate_video", lambda *a, **k: {"video": {"url": "https://x/c.mp4"}})
    monkeypatch.setattr(story_engine, "_download", fake_download)
    monkeypatch.setattr(story_engine.music, "available", lambda: "off in tests")

    async def progress(m):
        seen.setdefault("msgs", []).append(m)

    out = asyncio.run(story_engine.run({"app_id": "pltt-storyboard-video-maker", "name": "App"},
                                       {"prompt": "a boy finds a door", "request": {"duration": 10, "aspect_ratio": "9:16"}},
                                       {}, tmp_path / "work", progress))
    plates = [p for p, _ in seen["images"] if p.startswith("LOCATION PLATE")]
    assert len(plates) == 2 and any("drawing 2 locations before the scenes" in m for m in seen["msgs"])
    scene_prompts = [(p, u) for p, u in seen["images"] if p.startswith("Eliot")]
    bed, forest = scene_prompts[0], scene_prompts[1]
    assert bed[1][:3] == ["https://fal.media/passport.png", "https://fal.media/front.png", "https://fal.media/back.png"]
    assert bed[1][3] == "https://fal.media/plate_L1.png" and forest[1][3] == "https://fal.media/plate_L2.png"
    assert "LOCATION PLATE (attic bedroom)" in bed[0] and "LOCKED LOOK" in bed[0] and "striped pyjamas" in bed[0]
    edits = [(p, u) for p, u in seen["images"] if p.startswith("EDIT reference image 1")]
    assert len(edits) == 1 and "plain navy pyjamas instead of striped" in edits[0][0]
    assert edits[0][1][0].startswith("https://fal.media/still") and "https://fal.media/plate_L" in edits[0][1][-1]
    assert [l["name"] for l in out["plan"]["locations"]] == ["attic bedroom", "forest"]


def test_3d_motion_on_the_video_model_films_on_seedance_2_0_first(monkeypatch):
    monkeypatch.setenv("PALETTE_VIDEO_QUALITY", "full")
    q = story_engine.quality("motion_graphics", "video_model")
    assert story_engine.clip_models(q) == ["seedance_2_0", "kling_4_0", "kling_3_0_pro"]
    assert story_engine.quality("motion_graphics", "opus_llm")["video_model"] == "kling_4_0"  # no clips: unchanged
    assert story_engine.clip_models(story_engine.quality("short_drama")) == ["kling_4_0", "seedance_2_0", "kling_3_0_pro"]
    assert story_engine.clip_resolution("kling_4_0", {"resolution": "480p"}) == "720p"
    assert story_engine.clip_resolution("kling_3_0_pro", {"resolution": "720p"}) is None
