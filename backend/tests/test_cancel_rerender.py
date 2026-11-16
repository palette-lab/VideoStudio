"""Cancel, scene re-render (splice), parallel scenes stopping together, usage summary."""

import asyncio
import shutil
import subprocess

import pytest

from engines import story_engine
from router_core import execution


def _color_clip(path, color, seconds, size="320x568", tone=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}"]
    if tone:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}", "-shortest", "-c:a", "aac"]
    subprocess.run(cmd + ["-pix_fmt", "yuv420p", str(path)], check=True)


def _probe(path, entries):
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_splice_replaces_only_that_span_and_keeps_the_audio(tmp_path):
    film, new = tmp_path / "film.mp4", tmp_path / "new.mp4"
    _color_clip(film, "red", 6, tone=440)
    _color_clip(new, "blue", 5, size="640x360")  # another size and longer than the span
    out = story_engine.splice(str(film), str(new), 2.0, 4.0, str(tmp_path / "out.mp4"))
    assert abs(float(_probe(out, "format=duration")) - 6.0) < 0.15
    assert _probe(out, "stream=codec_type").split() == ["video", "audio"]
    assert _probe(out, "stream=width,height").splitlines()[0] == "320,568"

    def pixel(t):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", out, "-frames:v", "1", "-vf",
                              "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return tuple(raw[:3])
    assert pixel(1.0)[0] > 200 and pixel(3.0)[2] > 200 and pixel(5.0)[0] > 200  # red | blue | red


def test_cancel_inside_a_step_marks_the_run_cancelled(monkeypatch):
    class Cancels:
        name = "engine"
        wants_context = True

        async def launch(self, *a, **k):
            raise execution.RunCancelled("r")
    monkeypatch.setitem(execution.ADAPTERS, "engine", Cancels())
    monkeypatch.setattr(execution.registry, "apps", lambda: {"x": {"app_id": "x", "name": "X", "execution": {"adapter": "engine"}}})
    monkeypatch.setattr(execution.fallback, "fallback_options", lambda run, sid: [])
    run = {"id": "r", "events": [], "plan": {"steps": [{"step_id": "s1", "app_id": "x"}, {"step_id": "s2", "app_id": "x",
                                                                                     "inputs": [{"source": "step:s1", "format": "video_file"}]}]},
           "execution": {"s1": {"status": "queued"}, "s2": {"status": "queued"}}}

    async def save(r):
        return None
    asyncio.run(execution.run_plan(run, save))
    assert run["status"] == "cancelled" and run["execution"]["s1"]["status"] == "cancelled"
    assert run["execution"]["s2"]["status"] == "not_run" and not run["report"]["success"]


def test_parallel_scenes_stop_together():
    finished = []

    async def slow(i):
        await asyncio.sleep(0.3)
        finished.append(i)

    async def boom():
        await asyncio.sleep(0.05)
        raise execution.RunCancelled("r")

    with pytest.raises(execution.RunCancelled):
        asyncio.run(story_engine.all_or_nothing([slow(1), boom(), slow(2)]))
    assert finished == []  # the other scenes were stopped, not left running (and paying)
    assert asyncio.run(story_engine.all_or_nothing([asyncio.sleep(0, result=n) for n in (1, 2)])) == [1, 2]


def test_paid_calls_are_counted():
    calls = []
    tok = story_engine.fal_usage.set(calls)
    try:
        story_engine._count("image", "nano_banana_pro")
        story_engine._count("clip", "kling_3_0_pro")
        story_engine._count("clip", "kling_3_0_pro")
    finally:
        story_engine.fal_usage.reset(tok)
    from router_core import llm
    s = llm.usage_summary({"usage_log": [{"ok": True, "prompt_tokens": 10, "completion_tokens": 5, "stage": "x"}],
                            "model_calls": calls})
    assert s["generated"] == {"image": 1, "clip": 2, "voice": 0, "models": ["kling_3_0_pro", "nano_banana_pro"]}
    assert s["calls"] == 1 and s["prompt_tokens"] == 10
