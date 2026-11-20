import asyncio
import json

from engines import motion_code as mc
from engines import story_engine as se


def test_a_bad_plan_becomes_a_safe_one():
    assert mc.clean_plan({}, 6) == mc.default_plan(6)
    assert mc.clean_plan({"layers": [{"box": [0.1, 0.1, 0.11, 0.11]}, "x"]}, 6) == mc.default_plan(6)
    p = mc.clean_plan({"camera": "spin", "strength": 5, "layers": [
        {"box": [0.5, 0.2, 0.9, 0.8], "entry": "explode", "t": 9, "idle": "dance", "sfx": "boom", "from": "up"},
        {"box": [-1, 0, 0.5, 0.3], "entry": "slide", "t": 0.1, "idle": "float", "sfx": "pop", "from": "left"}]}, 6)
    assert p["camera"] == "push_in" and p["strength"] == 0.12
    first, second = p["layers"]  # sorted by start time
    assert first["box"] == [0.0, 0.0, 0.5, 0.3] and first["entry"] == "slide" and first["sfx"] == "pop"
    assert second["t"] == 4.2 and second["entry"] == "pop" and second["idle"] == "none" and second["sfx"] == ""
    assert second["from"] == "bottom"


def test_the_page_plays_the_plan_and_its_sounds():
    plan = mc.clean_plan({"camera": "pan_left", "layers": [
        {"box": [0, 0, 0.5, 0.5], "t": 0.2, "sfx": "whoosh"}, {"box": [0.5, 0.5, 1, 1], "t": 1.0}]}, 6)
    html = mc.page(plan, 6)
    got = json.loads(html.split("const P = ", 1)[1].split(", D = ", 1)[0])
    assert got["camera"] == "pan_left" and len(got["layers"]) == 2 and "window.DUR = D" in html
    assert '"plate.png"' in html and '"img.png"' in html
    assert mc.sfx_events(plan) == [{"t": 0.23, "type": "whoosh", "gain": mc.SFX_GAIN}]


def test_the_plan_is_asked_with_the_keyframe_and_the_directors_motion_prompt(monkeypatch, tmp_path):
    img = tmp_path / "k.png"
    img.write_bytes(b"\x89PNG fake")
    seen = {}

    async def fake(msgs, stage, *a, **k):
        seen["system"], seen["user"] = msgs[0]["content"], msgs[1]["content"]
        return {"camera": "drift", "layers": [{"box": [0.1, 0.1, 0.6, 0.6], "t": 0.0, "entry": "drop"}]}
    monkeypatch.setattr(mc.vox, "_json", fake)
    plan = asyncio.run(mc.plan_scene(img, {"script": "Habits compound.", "motion_prompt": "0-2s: the title pops"},
                                     6, 3))
    assert "0-2s: the title pops" in seen["system"] and "Habits compound." in seen["system"]
    assert seen["user"][1]["type"] == "image_url" and plan["camera"] == "drift"


def test_motion_graphics_use_no_video_model_by_default(monkeypatch):
    monkeypatch.setattr(se, "MOTION_VIDEO_MODEL", "off")
    assert se.code_motion("motion_graphics") and not se.code_motion("short_drama") and not se.code_motion(None)
    monkeypatch.setattr(se, "MOTION_VIDEO_MODEL", "on")
    assert not se.code_motion("motion_graphics")


def test_animate_renders_a_real_clip(monkeypatch, tmp_path):
    """End to end on this machine (renderer + ffmpeg): a keyframe becomes a clip of the scene's length."""
    import shutil
    import pytest
    from engines import opuscar_engine as oc, vox_doc_engine as vox
    from engines.vendor.flow import video_merge
    if oc.available() or not shutil.which("ffprobe"):
        pytest.skip("needs the Opuscar renderer, Chrome and ffmpeg")
    key = tmp_path / "key.png"
    vox._ff(["-f", "lavfi", "-i", "color=c=0x1E252E:s=540x960", "-vf",
             "drawbox=x=100:y=300:w=340:h=300:color=0x14E0C8:t=fill", "-frames:v", "1", str(key)])

    async def fake(msgs, stage, *a, **k):
        return {"camera": "push_in", "layers": [{"box": [0.15, 0.3, 0.85, 0.65], "t": 0.1, "entry": "pop"}]}
    monkeypatch.setattr(mc.vox, "_json", fake)
    path, plan = asyncio.run(mc.animate(key, {"script": "x"}, 2.0, "9:16", tmp_path / "w", 1))
    assert "fallback" not in plan
    assert abs(video_merge.probe_duration(video_merge.find_ffmpeg(), path) - 2.0) < 0.2
    assert vox._size(__import__("pathlib").Path(path)) == mc.size_for("9:16")


def test_motion_engine_choice():
    from engines import story_engine as se
    assert se.code_motion("motion_graphics", "opus_llm") is True
    assert se.code_motion("motion_graphics", "video_model") is False
    assert se.code_motion("short_drama", "opus_llm") is False
    assert se.code_motion("motion_graphics", None) is (se.default_motion_engine() == "opus_llm")


def test_one_language_from_the_users_words():
    from engines import lang
    assert lang.detect("Make a 3D motion graphics video about Wi-Fi", "ko") == "english"
    assert lang.detect("와이파이에 대한 3D 모션 그래픽 영상", "en") == "korean"
    assert lang.detect("Make a Wi-Fi video in Korean", "en") == "korean"
    assert lang.detect("와이파이 영상을 영어로 만들어줘", "ko") == "english"
    assert lang.detect("Haz un video sobre wifi", "es") == "es"
    assert "English only" in lang.rule("english")


def test_transcript_language_check():
    from engines import lang
    assert lang.iso("english") == "en" and lang.iso("korean") == "ko" and lang.iso("") is None
    assert not lang.matches("밤의 하늘을 보며 아름다움을 볼 수 있습니다.", "english")
    assert lang.matches("You look up at the night sky and see beauty.", "english")
    assert not lang.matches("SEE BEAUTY", "korean")
    assert lang.matches("아름다움을 봅니다", "korean")
