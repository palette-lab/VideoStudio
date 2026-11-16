"""Presenter Studio: routing of talking-to-camera requests and the lip-sync chunking."""

import pytest

from engines import presenter_engine as pe
from router_core import config, pipeline, registry
from tests.conftest import REAL_REGISTRY
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def real_registry(monkeypatch):
    monkeypatch.setattr(config, "REGISTRY_DIR", REAL_REGISTRY)
    monkeypatch.setattr(config, "ASK_POLICY", "uploads")
    registry.apps.cache_clear(); registry.capabilities.cache_clear()
    yield
    registry.apps.cache_clear(); registry.capabilities.cache_clear()


async def plan(brief, prompt="x", mode="hybrid"):
    res = await pipeline.analyze(prompt=prompt, mode=mode, assets={}, perception={}, answers={}, chat=FakeLLM(brief))
    return res, [s["app_id"] for s in (res["plan"] or {}).get("steps", [])]


async def test_ugc_ad_with_a_talking_creator_goes_to_presenter_studio():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "commercial"],
                            "motion": {"kinds": ["realistic"]}, "audio": {"voiceover": "requested"},
                            "constraints": {"duration_s": 30, "aspect_ratio": "9:16"}})
    assert res["status"] == "awaiting_approval" and apps == ["palette-presenter-studio"]


async def test_youtube_explainer_with_a_host_gets_finishing_effects():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "explainer"], "motion": {"kinds": ["realistic"]}})
    assert apps == ["palette-presenter-studio", "palette-effects-studio"]


async def test_presenter_mode_makes_any_request_a_talking_presenter():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["commercial"],
                            "motion": {"kinds": ["realistic"]}}, mode="presenter")
    assert apps == ["palette-presenter-studio"]


def test_cut_points_cut_long_speech_at_pauses_under_the_limit():
    cuts = pe.cut_points(70.0, [5.0, 14.2, 22.9, 26.1, 40.0, 51.3, 60.0])
    assert cuts[0] == 0 and cuts[-1] == 70.0
    assert all(b - a <= pe.CHUNK_MAX_S for a, b in zip(cuts, cuts[1:]))
    assert 26.1 in cuts  # the latest pause inside the first window


def test_cut_points_hard_cut_without_pauses_and_none_when_short():
    assert pe.cut_points(20.0, []) == [0.0, 20.0]
    cuts = pe.cut_points(60.0, [])
    assert cuts == [0.0, pe.CHUNK_MAX_S, pe.CHUNK_MAX_S * 2, 60.0]


def test_format_detection():
    assert pe.detect_format("A UGC ad for my serum") == "ugc_ad"
    assert pe.detect_format("YouTube video explaining black holes") == "youtube_explainer"
    assert pe.detect_format("a customer testimonial") == "testimonial"


def test_free_by_default_paid_only_when_switched_on(monkeypatch):
    monkeypatch.delenv("PRESENTER_PAID", raising=False)
    monkeypatch.delenv("LIPSYNC_MODELS", raising=False)
    assert pe.quality()["lipsync"] == ["ditto_local"]
    monkeypatch.setenv("PRESENTER_PAID", "on")
    chain = pe.quality()["lipsync"]
    assert chain[0] == "ditto_local" and len(chain) > 1 and all(pe.LIPSYNC[m]["kind"] != "local" for m in chain[1:])


def test_lipsync_request_shapes_fit_each_model():
    a = pe.lipsync_args("longcat", "img", "aud", 20.0, "talk", "480p")
    assert a["num_segments"] == 4 and a["resolution"] == "480p"  # 5.8 + 3 x 5 s covers 20 s
    a = pe.lipsync_args("infinitalk", "img", "aud", 27.0, "talk", "720p")
    assert 41 <= a["num_frames"] <= 721
    a = pe.lipsync_args("kling_avatar", "img", "aud", 10.0, "talk", "480p")
    assert set(a) == {"image_url", "audio_url", "prompt"}


def _fake_media(monkeypatch, tmp_path):
    async def fake_upload(path):
        return "https://x/a.wav"

    async def fake_download(url, dest):
        return str(dest)

    monkeypatch.setattr(pe.story_engine, "_upload", fake_upload)
    monkeypatch.setattr(pe.story_engine, "_download", fake_download)
    monkeypatch.setattr(pe, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pe.video_merge, "probe_duration", lambda ff, p: 8.0)


async def test_local_ditto_runs_first(monkeypatch, tmp_path):
    _fake_media(monkeypatch, tmp_path)
    ran = []

    async def fake_local(cmd, what, timeout=0):
        ran.append(what)
        return {"ok": True}

    monkeypatch.setattr(pe, "local_ready", lambda: None)
    monkeypatch.setattr(pe, "run_local", fake_local)
    path, used = await pe.lipsync("u", "img.png", "a.wav", "talk", {"lipsync": ["ditto_local", "kling_avatar"],
                                                                     "resolution": "480p"}, "k", tmp_path / "t.mp4")
    assert used == "ditto_local" and ran == ["Ditto"] and path.endswith("t.mp4")


async def test_without_local_models_and_paid_off_it_says_what_to_do(monkeypatch, tmp_path):
    _fake_media(monkeypatch, tmp_path)
    monkeypatch.delenv("PRESENTER_PAID", raising=False)
    monkeypatch.setattr(pe, "local_ready", lambda: "not installed here (Ditto models missing)")
    with pytest.raises(pe.PresenterError, match="PRESENTER_PAID=on"):
        await pe.lipsync("u", "img.png", "a.wav", "talk", pe.quality(), "k", tmp_path / "t.mp4")


async def test_paid_fal_lipsync_is_the_fallback_when_switched_on(monkeypatch, tmp_path):
    _fake_media(monkeypatch, tmp_path)
    monkeypatch.setenv("PRESENTER_PAID", "on")
    monkeypatch.setattr(pe, "local_ready", lambda: "not installed here")
    calls = []

    async def fake_subscribe(endpoint, args, key):
        calls.append(endpoint)
        return {"video": {"url": "https://x/v.mp4"}}

    monkeypatch.setattr(pe, "_subscribe", fake_subscribe)
    q = pe.quality()
    path, used = await pe.lipsync("u", "img.png", "a.wav", "talk", q, "k", tmp_path / "t.mp4")
    assert used == q["lipsync"][1] and calls == [pe.LIPSYNC[used]["endpoint"]]


def test_kokoro_voices_by_language():
    assert pe.kokoro_voice("English", "female") == ("en-us", "af_heart")
    assert pe.kokoro_voice("English (UK)", "male") == ("en-gb", "bm_george")
    assert pe.kokoro_voice("Hindi", "male") == ("hi", "hm_omega")
    assert pe.kokoro_voice("", "female")[0] == "en-us"
    assert pe.kokoro_voice("Telugu", "female") is None


async def test_voice_is_local_kokoro_and_paid_only_when_on(monkeypatch, tmp_path):
    monkeypatch.delenv("PRESENTER_PAID", raising=False)
    monkeypatch.setattr(pe, "local_ready", lambda: None)

    async def fake_local(cmd, what, timeout=0):
        return {"ok": True}

    monkeypatch.setattr(pe, "run_local", fake_local)
    used = []
    path = await pe.speak("hi", "female", "English", "k", tmp_path / "line_01", used)
    assert path.endswith("line_01.wav") and used == ["Kokoro (open source, local)"]
    with pytest.raises(pe.PresenterError, match="Upload your own recording"):
        await pe.speak("hi", "female", "Telugu", "k", tmp_path / "line_02", used)


async def test_multi_scene_ugc_unboxing_is_a_storyboarded_film_with_effects():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "commercial"],
                            "scene": {"shot_structure": "multi_scene", "consistency": ["the creator"]},
                            "motion": {"kinds": ["realistic"]}, "constraints": {"aspect_ratio": "9:16"}})
    assert res["status"] == "awaiting_approval"
    assert "palette-presenter-studio" not in apps
    caps = {r["capability"] for r in res["understanding"]["requirements"]}
    assert {"story.multi_scene_storyboard", "video.from_storyboard", "edit.explainer_effects"} <= caps


async def test_composer_length_and_format_win_over_the_prompt():
    brief = {"detected_intent": "create_new", "confidence": 0.9, "production_type": ["commercial"],
             "scene": {"shot_structure": "multi_scene"}, "motion": {"kinds": ["realistic"]},
             "constraints": {"duration_s": 30, "aspect_ratio": "16:9"}}
    res = await pipeline.analyze(prompt="a 30 s 16:9 ad", mode="storyboard", assets={}, perception={}, answers={},
                                 chat=FakeLLM(brief), settings={"duration_s": 20, "aspect_ratio": "9:16"})
    req = res["plan"]["steps"][0]["handoff"]["request"]
    assert req["duration"] == 20 and req["aspect_ratio"] == "9:16"
