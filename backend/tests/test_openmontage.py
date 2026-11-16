"""Real OpenMontage editing (needs ffmpeg and repos/OpenMontage; skipped otherwise)."""

import asyncio
import shutil
import subprocess

import pytest

from router_core import config
from router_core.execution import OpenMontageAdapter, _probe_duration

APP = {"app_id": "video-edit-enhance", "name": "Video Edit & Enhance"}
ALL = ["edit.assemble_clips", "edit.transitions", "edit.audio_mix"]
real = pytest.mark.skipif(not shutil.which("ffmpeg") or not (config.OPENMONTAGE_DIR / "tools").exists(),
                          reason="needs ffmpeg and repos/OpenMontage")


def _clip(path, src, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=size=640x360:rate=25:duration=2"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def _step(caps):
    return {"capabilities_used": caps, "outputs": ["video_file"]}


@real
def test_joins_with_crossfades_and_music(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE", tmp_path / "storage")
    a, b = _clip(tmp_path / "a.mp4", "testsrc2"), _clip(tmp_path / "b.mp4", "smptebars", audio=False)
    music = tmp_path / "m.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=10", str(music)], check=True)
    inputs = [{"format": "video_file", "path": a}, {"format": "video_file", "path": b},
              {"format": "audio_file", "path": str(music)}]
    r = asyncio.run(OpenMontageAdapter().launch(APP, _step(ALL), inputs, "t-s1-1", None))
    assert r["status"] == "succeeded" and r["mock"] is False, r
    out = r["outputs"][0]["path"]
    assert abs(asyncio.run(_probe_duration(out)) - 3.5) < 0.2  # 2 + 2 - 0.5 crossfade


@real
def test_simulated_upstream_clips_stay_mock():
    inputs = [{"format": "video_file", "ref": "mock://storyboard-studio/x/video_file"}]
    r = asyncio.run(OpenMontageAdapter().launch(APP, _step(["edit.assemble_clips"]), inputs, "t", None))
    assert r["mock"] is True and "simulated" in r["message"]


@real
def test_unconnected_operation_is_reported_as_mock():
    r = asyncio.run(OpenMontageAdapter().launch(APP, _step(["edit.captions_subtitles"]), [], "t", None))
    assert r["mock"] is True and "captions subtitles is not connected" in r["message"]


@real
def test_music_requested_without_a_file_fails_clearly(tmp_path):
    a = _clip(tmp_path / "a.mp4", "testsrc2")
    r = asyncio.run(OpenMontageAdapter().launch(APP, _step(["edit.audio_mix"]), [{"format": "video_file", "path": a}], "t", None))
    assert r["status"] == "failed" and "Upload an audio track" in r["message"]


def test_without_openmontage_the_step_is_simulated_not_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OPENMONTAGE_DIR", tmp_path / "missing")
    r = asyncio.run(OpenMontageAdapter().launch(APP, _step(["edit.assemble_clips"]), [], "t", None))
    assert r["status"] == "succeeded" and r["mock"] is True and "not installed" in r["message"]
