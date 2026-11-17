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


async def test_youtube_explainer_with_a_host_is_presenter_studio_alone():
    # Presenter Studio finishes the video itself (camera-move effects + every word as editable text): no later app
    # may burn text into it.
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "explainer"], "motion": {"kinds": ["realistic"]}})
    assert apps == ["palette-presenter-studio"]
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "explainer"],
                            "motion": {"kinds": ["realistic"], "programmatic_elements": ["kinetic_text", "data_visualization"]}})
    assert apps == ["palette-presenter-studio"]


async def test_a_host_explainer_split_into_scenes_still_goes_to_presenter_studio():
    # What the analysis really returned for "a friendly host at a desk explains compound interest": it split the
    # talk into 5 "scenes". A host explaining stays one talking head, not a storyboarded film.
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "explainer", "educational"],
                            "scene": {"shot_structure": "multi_scene", "scene_count": 5,
                                      "consistency": ["the host's identity", "the desk setup"]},
                            "motion": {"kinds": ["realistic"]}})
    assert res["status"] == "awaiting_approval" and apps[0] == "palette-presenter-studio"
    assert not {"pltt-storyboard-video-maker", "pltt-flow-video-2"} & set(apps)


async def test_a_presenter_with_music_needs_no_separate_audio_mix_step():
    # The failed run: Presenter -> Motion Studio -> Flow Video "mix audio" -> Effects; the mix step had no track.
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9,
                            "production_type": ["talking_presenter", "explainer", "educational"],
                            "audio": {"music": "requested", "voiceover": "requested", "sound_effects": "requested"},
                            "motion": {"kinds": ["realistic"]}})
    caps = {r["capability"] for r in res["understanding"]["requirements"]}
    assert apps[0] == "palette-presenter-studio" and "edit.audio_mix" not in caps
    assert "pltt-flow-video-2" not in apps


async def test_presenter_mode_makes_any_request_a_talking_presenter():
    res, apps = await plan({"detected_intent": "create_new", "confidence": 0.9, "production_type": ["commercial"],
                            "motion": {"kinds": ["realistic"]}}, mode="presenter")
    assert apps == ["palette-presenter-studio"]


def test_cut_points_cut_long_speech_at_pauses_under_the_limit():
    cuts = pe.cut_points(30.0, [3.0, 7.2, 9.4, 14.0, 18.1, 26.5])
    assert cuts[0] == 0 and cuts[-1] == 30.0
    assert all(pe.CHUNK_MIN_S <= b - a <= pe.CHUNK_MAX_S for a, b in zip(cuts, cuts[1:]))
    assert 9.4 in cuts  # the latest pause inside the first window


def test_cut_points_hard_cut_without_pauses_and_never_a_tiny_last_piece():
    assert pe.cut_points(8.0, []) == [0.0, 8.0]
    assert pe.cut_points(30.0, []) == [0.0, 10.0, 20.0, 30.0]
    cuts = pe.cut_points(20.5, [])  # a hard cut at 20 s would leave 0.5 s, below Kling LipSync's 2 s
    assert cuts[-1] == 20.5 and all(b - a >= pe.CHUNK_MIN_S for a, b in zip(cuts, cuts[1:]))


def test_format_detection():
    assert pe.detect_format("A UGC ad for my serum") == "ugc_ad"
    assert pe.detect_format("YouTube video explaining black holes") == "youtube_explainer"
    assert pe.detect_format("a customer testimonial") == "testimonial"


def test_lipsync_is_kling_then_sync_1_9_no_local_model(monkeypatch):
    monkeypatch.delenv("LIPSYNC_MODELS", raising=False)
    monkeypatch.delenv("PRESENTER_PAID", raising=False)
    assert pe.quality()["lipsync"] == ["kling_lipsync", "sync_lipsync"]
    assert pe.LIPSYNC["kling_lipsync"]["endpoint"] == "fal-ai/kling-video/lipsync/audio-to-video"
    assert pe.LIPSYNC["sync_lipsync"]["endpoint"] == "fal-ai/sync-lipsync"
    assert set(pe.LIPSYNC) == {"kling_lipsync", "sync_lipsync"}


def test_lipsync_request_shapes_fit_each_model():
    assert pe.lipsync_args("kling_lipsync", "v", "a") == {"video_url": "v", "audio_url": "a"}
    a = pe.lipsync_args("sync_lipsync", "v", "a")
    assert a == {"video_url": "v", "audio_url": "a", "model": "lipsync-1.9.0-beta", "sync_mode": "cut_off"}


def _fake_media(monkeypatch, tmp_path):
    async def fake_upload(path):
        return f"https://x/{path.rsplit('/', 1)[-1]}"

    async def fake_download(url, dest):
        return str(dest)

    monkeypatch.setattr(pe.story_engine, "_upload", fake_upload)
    monkeypatch.setattr(pe.story_engine, "_download", fake_download)


async def test_kling_lipsync_runs_first_with_the_video_and_speech(monkeypatch, tmp_path):
    _fake_media(monkeypatch, tmp_path)
    calls = []

    async def fake_subscribe(endpoint, args, key):
        calls.append((endpoint, args))
        return {"video": {"url": "https://x/v.mp4"}}

    monkeypatch.setattr(pe, "_subscribe", fake_subscribe)
    path, used = await pe.lipsync("/w/base_01.mp4", "/w/a.wav", pe.quality(), "k", tmp_path / "t.mp4")
    assert used == "kling_lipsync" and path.endswith("t.mp4")
    assert calls == [("fal-ai/kling-video/lipsync/audio-to-video",
                      {"video_url": "https://x/base_01.mp4", "audio_url": "https://x/a.wav"})]


async def test_sync_lipsync_1_9_is_the_fallback(monkeypatch, tmp_path):
    _fake_media(monkeypatch, tmp_path)
    calls, fell = [], []

    async def fake_subscribe(endpoint, args, key):
        calls.append(endpoint)
        if "kling" in endpoint:
            raise RuntimeError("video must be 720p")
        return {"video": {"url": "https://x/v.mp4"}}

    async def on_fallback(label, e):
        fell.append(label)

    monkeypatch.setattr(pe, "_subscribe", fake_subscribe)
    path, used = await pe.lipsync("/w/b.mp4", "/w/a.wav", pe.quality(), "k", tmp_path / "t.mp4", on_fallback)
    assert used == "sync_lipsync" and calls == ["fal-ai/kling-video/lipsync/audio-to-video", "fal-ai/sync-lipsync"]
    assert fell == ["Kling LipSync"]

    async def broken(endpoint, args, key):
        raise RuntimeError("down")

    monkeypatch.setattr(pe, "_subscribe", broken)
    with pytest.raises(pe.PresenterError, match="Every lip-sync model failed"):
        await pe.lipsync("/w/b.mp4", "/w/a.wav", pe.quality(), "k", tmp_path / "t.mp4")


async def test_base_clip_films_the_presenter_from_the_photo(monkeypatch, tmp_path):
    _fake_media(monkeypatch, tmp_path)
    seen = []

    async def fake_fal(fn, key, model, prompt, **kw):
        seen.append((model, kw))
        if model == "kling_3_0_pro":
            raise RuntimeError("refused")
        return {"video": {"url": "https://x/base.mp4"}}

    async def progress(m):
        pass

    monkeypatch.setattr(pe.story_engine, "_fal", fake_fal)
    q = {**pe.QUALITY["full"], "lipsync": pe.LIPSYNC_CHAIN}
    out = await pe.base_clip({"name": "P"}, "https://x/p.png", "talks", "9:16", q, "k", tmp_path, progress)
    assert out.endswith("presenter_base.mp4") and [m for m, _ in seen] == ["kling_3_0_pro", "kling_3_0_standard"]
    kw = seen[-1][1]
    assert kw["start_image"] == "https://x/p.png" and kw["duration"] == "10" and kw["audio"] is False
    assert "mouth moving" in pe.base_prompt(pe.FORMATS["ugc_ad"], "in a kitchen")


def test_each_part_gets_its_own_window_of_the_base_clip(monkeypatch):
    runs = []
    monkeypatch.setattr(pe, "_run", lambda cmd, what: runs.append(cmd))
    for i in range(3):
        pe.window("ffmpeg", "base.mp4", 10.0, i, 4.0, f"w{i}.mp4")
    starts = [float(c[c.index("-ss") + 1]) for c in runs]
    durs = [float(c[c.index("-t") + 1]) for c in runs]
    assert len(set(starts)) == 3 and all(s + 4.0 <= 10.0 for s in starts) and durs == [4.0, 4.0, 4.0]
    runs.clear()
    pe.window("ffmpeg", "base.mp4", 10.0, 0, 0.8, "w.mp4")  # never under the lip-sync's 2 s minimum
    assert float(runs[0][runs[0].index("-t") + 1]) == 2.0


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


def test_script_is_written_from_the_cinematic_research(monkeypatch):
    import asyncio
    sent = {}

    async def fake_chat(model, messages, **kw):
        sent["system"], sent["user"] = messages[0]["content"], messages[1]["content"]
        return {"segments": [{"text": "Your money earns money on its money.", "mood": "curious"}]}
    monkeypatch.setattr(pe.llm, "chat_json", fake_chat)
    research = {"angle": "interest on interest", "facts": [{"claim": "interest is added to the principal",
                                                            "source": "Compound interest", "confidence": "verified"}]}
    out = asyncio.run(pe.write_script({"prompt": "explain compound interest", "_research": research},
                                      "youtube_explainer", 20, [], False))
    assert out["segments"] and '"research"' in sent["user"] and "interest is added to the principal" in sent["user"]
    assert "every fact the presenter states comes" in sent["system"]
