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
        if kw.get("stage") == "engine.critic":
            return {"score": 90, "issues": []}
        if kw.get("stage") == "engine.story.consistency":
            seen["checks"] = seen.get("checks", 0) + 1
            return {"same": True, "differences": []}
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
        if prompt.startswith("Character turnaround reference sheet of girl, 10, red coat"):  # the user's template
            assert kw["aspect_ratio"] == "16:9" and "full-body side profile view" in prompt
            assert "solid flat mid-gray background" in prompt and "image_urls" not in kw
            seen["sheet"] = True
            return {"images": [{"url": "https://fal.media/sheet.png"}]}
        if prompt.startswith("LOOK FRAME"):  # the film's world, drawn once beside the sheet
            seen["look"] = True
            return {"images": [{"url": "https://fal.media/look.png"}]}
        assert "GLOBAL STYLE" in prompt and kw["aspect_ratio"] == "9:16"
        if prompt.startswith("The LAST frame"):
            seen["end"] = "https://fal.media/end1.png"
            return {"images": [{"url": seen["end"]}]}
        if seen.get("end") and "girl hugs cat" in prompt:  # scene 2 starts from scene 1's LAST frame
            seen["chained"] = kw["image_urls"][-1] == seen["end"]
        if seen.get("sheet"):  # every scene still is made from the turnaround sheet
            assert kw["image_urls"][0] == "https://fal.media/sheet.png" and "turnaround sheet" in prompt
        return {"images": [{"url": f"https://fal.media/still{seen['images']}.png"}]}

    def fake_video(key, model, prompt, **kw):
        assert model == "seedance_2_0" and kw["resolution"] == "480p" and kw["audio"] is False and kw["start_image"]
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
    assert seen.get("chained") is True
    # the sheet, the look frame, one still per scene, one last frame, and one redraw after the AI check found an
    # extra plate
    assert seen["images"] == 6 and seen["sheet"] and seen["look"] and len(out["scenes"]) == 2
    assert seen["qa"] == 4  # every frame analysed (3) plus the redraw re-checked
    assert out["scenes"][0]["qa"]["first"] == {"issues": [], "redrawn": True,
                                               "fixed": ["an extra plate on the table -> remove it"]}
    assert out["scenes"][0]["image_prompt"] and out["scenes"][0]["motion_prompt"]
    assert seen["clip_qa"] == 2 and out["scenes"][1]["qa"]["clip"] == {"issues": [], "retaken": False}
    assert out["plan"]["cast_sheet_url"] == "https://fal.media/sheet.png" and seen["checks"] == 2
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
    assert used == "kling_3_0_standard" and url.endswith("k.mp4")
    assert calls == [("seedance_2_0", "480p"), ("kling_3_0_standard", None)]


def test_other_provider_errors_are_not_retried(monkeypatch):
    def boom(key, model, prompt, **kw):
        raise RuntimeError("502 bad gateway")

    async def progress(m):
        pass

    monkeypatch.setattr(story_engine.fal_helper, "generate_video", boom)
    with pytest.raises(RuntimeError, match="502"):
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
        "pink sneakers instead of red rain boots"]


def test_continuity_sheet_becomes_hard_rules():
    c = {"entities": [{"name": "the girl", "kind": "character", "locked": ["glossy red raincoat", "red rain boots"],
                       "never": ["sneakers"]}]}
    text = story_engine._locks(c)
    assert "red rain boots" in text and "never: sneakers" in text
    assert story_engine.recurring_subject({"continuity": c, "scenes": [{}]})
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


def test_turnaround_sheet_uses_the_users_template_per_character():
    p = {"bible": {"characters": "unused", "grading": "warm 35mm film look"},
         "continuity": {"entities": [{"kind": "character", "name": "Mina", "locked": ["30s", "short black bob", "navy suit"]},
                                     {"kind": "prop", "name": "mug"},
                                     {"kind": "character", "name": "Leo", "locked": ["10", "red raincoat"]}]}}
    members = story_engine.cast_members(p)
    assert members == ["Mina: 30s; short black bob; navy suit", "Leo: 10; red raincoat"]
    prompt = story_engine.turnaround_prompt(members[0], story_engine.art_style(p))
    assert prompt == ("Character turnaround reference sheet of Mina: 30s; short black bob; navy suit, showing four views "
                      "side-by-side: a close-up front portrait of the face, full-body front view, full-body side profile "
                      "view, and full-body back view. Standing in a neutral relaxed pose, even studio lighting, solid "
                      "flat mid-gray background, no text, no labels, clean sharp focus, warm 35mm film look.")
    assert story_engine.cast_members({"bible": {"characters": "a girl, 10, red coat"}}) == ["a girl, 10, red coat"]
    assert story_engine.art_style({}) == "photorealistic, true-to-life detail"
    assert "FIRST 2 reference images are character turnaround sheets" in story_engine._cast_note(2)
