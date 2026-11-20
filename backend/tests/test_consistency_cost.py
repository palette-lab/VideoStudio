"""Fewer wasted redraws and clips, and a real face check (the delivery-boy film: 73 images and 44 clips for 11 scenes,
most spent on coin counts and Seedance refusals, while Leo's face still changed between scenes)."""
import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from engines import story_engine as se


def test_issues_are_tagged_and_only_major_ones_count():
    assert se.tag_issue("MINOR: 7 coins instead of 5") == "MINOR: 7 coins instead of 5"
    assert se.tag_issue("**major** - Leo has another face") == "MAJOR: Leo has another face"
    assert se.tag_issue("an extra plate", default_major=True).startswith("MAJOR: ")
    assert se.tag_issue("the bracelet is missing").startswith("MINOR: ")  # untagged: the face/hair/outfit words decide
    assert se.tag_issue("his windbreaker has a hood").startswith("MAJOR: ")
    issues = ["MINOR: 7 coins", "MAJOR: a different face", "MINOR: the glass is in the other hand"]
    assert se.severity_score(issues) == (1, 3) and se.drift_score(issues) == (1, 3)
    assert se.severity_score(["MINOR: a"] * 4) < se.severity_score(["MAJOR: b"])  # one major outweighs any minors
    assert se.plain("MAJOR: a different face") == "a different face"


def test_the_checks_ask_for_the_tags():
    assert "{SEVERITY}" in se.SCENE_QA and "{SEVERITY}" in se.CLIP_QA and "{SEVERITY}" in se.CONSISTENCY
    assert "MAJOR" in se.SEVERITY and "coins" in se.SEVERITY and se.CLIP_RETAKES == 1


def test_scene_check_issues_come_back_tagged(monkeypatch):
    async def fake(model, messages, **kw):
        assert "Start every issue with MAJOR: or MINOR:" in messages[0]["content"][0]["text"]
        return {"ok": False, "issues": ["MINOR: seven coins, the scene has five", "a second whiskey glass"]}
    monkeypatch.setattr(se.llm, "chat_json", fake)
    out = asyncio.run(se.scene_issues("https://s.png", {"title": "t"}))
    assert out == ["MINOR: seven coins, the scene has five", "MAJOR: a second whiskey glass"]


def test_photoreal_people_put_seedance_last():
    people = [{"name": "Leo", "desc": "Leo Carter, 24, male, lean face, yellow windbreaker"}]
    assert se.photoreal_people({"bible": {}}, people)
    assert not se.photoreal_people({"bible": {"style": "3D animated storybook, Pixar-like"}}, people)
    assert not se.photoreal_people({"bible": {}}, [{"name": "Crow", "desc": "a glossy black crow with a white flower"}])
    q = {"video_model": "seedance_2_0", "fallbacks": ["kling_4_0", "kling_3_0_pro"]}
    assert se.clip_models(q)[0] == "seedance_2_0"
    assert se.clip_models({**q, "_no_seedance": True}) == ["kling_4_0", "kling_3_0_pro", "seedance_2_0"]


def test_a_real_person_refusal_skips_seedance_for_the_rest_of_the_film(monkeypatch):
    calls = []

    def video(key, model, prompt, **kw):
        calls.append((model, bool(kw.get("image_references"))))
        if model.startswith("seedance"):
            raise RuntimeError("InputImageSensitiveContentDetected.PrivacyInformation: may contain real person")
        return {"video": {"url": "https://fal.media/k.mp4"}}

    async def progress(m):
        pass

    monkeypatch.setattr(se.fal_helper, "generate_video", video)
    q = dict(se.QUALITY["test"])
    cast = [{"name": "Leo", "passport": "https://p.png", "front": "https://f.png", "back": "https://b.png"}]
    _, used = asyncio.run(se._clip({"app_id": "x", "name": "App"}, 0, 2, {"duration": 5, "image_prompt": "a"},
                                   "https://s.png", "9:16", q, "k", progress, cast=cast))
    assert used.startswith("kling") and q["_no_seedance"] is True
    assert sum(1 for m, _ in calls if m.startswith("seedance")) == 1  # refused once: no retry without references
    calls.clear()
    asyncio.run(se._clip({"app_id": "x", "name": "App"}, 1, 2, {"duration": 5, "image_prompt": "b"},
                         "https://s2.png", "9:16", q, "k", progress, cast=cast))
    assert not any(m.startswith("seedance") for m, _ in calls)  # the next scene goes straight to Kling
    assert "_no_seedance" not in se.QUALITY["test"]  # the shared preset is never changed


def _frame(path: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=900x1600",
                    "-frames:v", "1", str(path)], check=True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to crop the face")
def test_a_small_face_is_cropped_enlarged_and_judged_again(tmp_path, monkeypatch):
    src = tmp_path / "src.png"
    _frame(src)
    seen = []

    async def fake(model, messages, **kw):
        assert kw["stage"] == "engine.story.face"
        frame = messages[0]["content"][1]["image_url"]["url"]
        seen.append(frame)
        if frame == "https://still.png":  # the wide shot: Leo is tiny, the full-frame look says "probably him"
            return {"faces": [{"name": "Leo", "visible": True, "score": 8, "box": [0.45, 0.1, 0.53, 0.16]},
                              {"name": "Richard", "visible": True, "score": 9, "box": [0.1, 0.1, 0.5, 0.5]}]}
        assert frame.startswith("data:image/")  # the enlarged crop
        return {"faces": [{"name": "Leo", "visible": True, "score": 3, "differences": "short flat hair, no stubble"}]}

    async def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        return str(dest)

    monkeypatch.setattr(se.llm, "chat_json", fake)
    monkeypatch.setattr(se, "_download", fake_download)
    who = [{"name": "Leo", "passport": "https://leo.png"}, {"name": "Richard", "passport": "https://ri.png"}]
    note = {}
    out = asyncio.run(se.face_issues(who, "https://still.png", tmp_path / "fc", note))
    assert len(seen) == 2  # Richard's face is large enough: no second look
    assert len(out) == 1 and out[0].startswith("MAJOR: Leo's face is a different person (short flat hair")
    assert {"name": "Leo", "score": 3.0, "small": True} in note["face_scores"]


def test_no_passport_means_no_face_check(monkeypatch):
    async def fake(*a, **k):
        raise AssertionError("no call without a portrait")
    monkeypatch.setattr(se.llm, "chat_json", fake)
    assert asyncio.run(se.face_issues([{"name": "x"}], "https://s.png", Path("/tmp"))) == []
