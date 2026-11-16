"""Editing existing footage: only what really runs is reported as done; the rest fails honestly."""

import asyncio

from engines import adapter

APP = {"app_id": "pltt-flow-video-2", "name": "Flow Video"}


def launch(caps, inputs, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter.config, "STORAGE", tmp_path)
    step = {"step_id": "s1", "capabilities_used": caps, "handoff": {"prompt": "x", "request": {"aspect_ratio": "9:16"}}}
    return asyncio.run(adapter.EngineAdapter().launch(APP, step, inputs, "r1", None))


def test_trim_is_never_reported_as_done(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(adapter.story_engine, "join", lambda *a, **k: called.append(1))
    out = launch(["edit.trim_and_cut", "edit.assemble_clips"],
                 [{"format": "video_file", "path": "/tmp/a.mp4"}], monkeypatch, tmp_path)
    assert out["status"] == "failed" and "trim or cut" in out["message"] and "Nothing was changed" in out["message"]
    assert not called  # it fails before doing (and paying for) anything


def test_audio_mix_needs_an_uploaded_track(monkeypatch, tmp_path):
    out = launch(["edit.audio_mix"], [{"format": "video_file", "path": "/tmp/a.mp4"}], monkeypatch, tmp_path)
    assert out["status"] == "failed" and "audio track" in out["message"]


def test_join_reformat_and_audio_mix_really_run(monkeypatch, tmp_path):
    seen = []

    async def join(paths, out, ar, res):
        seen.append(("join", len(paths), ar))
        return str(out)

    async def mix(video, track, voiced, out):
        seen.append(("mix", track))
        return str(out)
    monkeypatch.setattr(adapter.story_engine, "join", join)
    monkeypatch.setattr(adapter.story_engine, "_probe", lambda p: 12.0)
    monkeypatch.setattr(adapter.music, "mix", mix)
    out = launch(["edit.assemble_clips", "edit.reformat_aspect", "edit.audio_mix"],
                 [{"format": "video_file", "path": "/tmp/a.mp4"}, {"format": "video_file", "path": "/tmp/b.mp4"},
                  {"format": "audio_file", "path": "/tmp/song.mp3", "filename": "song.mp3"}], monkeypatch, tmp_path)
    assert out["status"] == "succeeded" and not out["mock"]
    assert seen == [("join", 2, "9:16"), ("mix", "/tmp/song.mp3")]
    assert "joined 2 clips" in out["message"] and "mixed in your audio" in out["message"]


def test_filming_app_without_footage_makes_the_film(monkeypatch, tmp_path):
    """A full-3D motion-graphics plan pinned to Storyboard & Video lists only motion / finishing capabilities: with no
    video coming in, the engine must make the film - never report a simulated edit."""
    made = []

    async def run(app, handoff, assets, work, prog):
        made.append(app["app_id"])
        return {"path": str(tmp_path / "f.mp4"), "duration_s": 30.0, "scenes": [], "spans": [], "aspect_ratio": "9:16",
                "plan": {"title": "t", "story": "", "method": None, "review": None, "total": 30, "requested_total": 30},
                "graphics": {}, "effects": {}}
    monkeypatch.setattr(adapter.story_engine, "run", run)
    monkeypatch.setattr(adapter.config, "STORAGE", tmp_path)
    app = {"app_id": "pltt-storyboard-video-maker", "name": "Storyboard & Video"}
    step = {"step_id": "s1", "capabilities_used": ["motion.kinetic_typography", "edit.audio_mix", "edit.explainer_effects"],
            "handoff": {"prompt": "x", "request": {}}}
    out = asyncio.run(adapter.EngineAdapter().launch(app, step, [{"format": "text", "source": "prompt"}], "r1", None,
                                                     run={"assets": {}, "perception": {}}))
    assert made == ["pltt-storyboard-video-maker"] and out["status"] == "succeeded" and not out["mock"]
