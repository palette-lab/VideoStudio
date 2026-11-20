"""Regression tests for the health-check fixes: model replies, the Opus fallback, image MIME types, scene regenerate,
and per-call fal keys."""

import asyncio
import json
import os
import threading

import httpx
import pytest

from router_core import config, llm


# --- model replies -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reply", ['[{"a": 1}]', "no json here", '{"a": 1', '"just a string"'])
def test_a_reply_that_is_not_a_json_object_is_an_llm_error(reply):
    with pytest.raises(llm.LLMReplyError) as e:
        llm._extract_json(reply)
    assert isinstance(e.value, llm.LLMError) and isinstance(e.value, ValueError)  # both kinds of handler catch it


def test_json_inside_prose_or_a_fence_still_parses():
    assert llm._extract_json('Here you go: {"a": 1} thanks') == {"a": 1}
    assert llm._extract_json('```json\n{"a": 2}\n```') == {"a": 2}


# --- a named model (Opus for motion) falls back to the reasoning chain ---------------------------------------------

def test_a_named_model_that_is_switched_off_falls_back_to_the_chain(monkeypatch):
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if "/" in model:
            return httpx.Response(503, json={"error": {"message": "Vendor models are not switched on", "code": "unavailable"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(config, "PREFERRED_MODEL", "")
    monkeypatch.setattr(llm, "_down_until", {})
    assert asyncio.run(llm.chat_json("anthropic/claude-opus-5.5", [], stage="t")) == {"ok": True}
    assert asyncio.run(llm.chat_json("anthropic/claude-opus-5.5", [], stage="t")) == {"ok": True}
    assert seen == ["anthropic/claude-opus-5.5", "palette-llm-max", "palette-llm-max"]  # tried once, then remembered


# --- image MIME types ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,mime", [("a.png", "image/png"), ("a.webp", "image/webp"), ("a.JPG", "image/jpeg"),
                                       ("a.bin", "image/jpeg")])
def test_image_part_names_the_real_type(tmp_path, name, mime):
    p = tmp_path / name
    p.write_bytes(b"x")
    assert llm.image_part(p)["image_url"]["url"].startswith(f"data:{mime};base64,")


# --- scene regenerate keeps fields the generic schema does not know ------------------------------------------------

def test_regenerate_keeps_motion_and_documentary_fields(monkeypatch):
    from engines import story_intel

    async def fake_json(msgs, stage, tokens=8000, effort=None):
        return {"what_happens": "a sharper event", "title": "New"}
    monkeypatch.setattr(story_intel, "_json", fake_json)
    old = {"n": 1, "title": "Old", "what_happens": "x", "duration_s": 5, "edited": True,
           "motion_gfx": {"keyframe_prompt": "KF"}, "hero": "H", "hit_word": "W"}
    intel = {"plan": {"scenes": [old]}, "classification": {}}
    new = asyncio.run(story_intel.regenerate_scene(intel, 1))
    assert new["what_happens"] == "a sharper event" and new["title"] == "New"
    assert new["motion_gfx"] == {"keyframe_prompt": "KF"} and new["hero"] == "H" and new["hit_word"] == "W"
    assert "edited" not in new and intel["plan"]["scenes"][0] is new


# --- fal calls carry their own key; the process-wide FAL_KEY is never swapped --------------------------------------

def test_parallel_fal_calls_each_use_their_own_key(monkeypatch):
    from engines.vendor.flow import fal_helper
    used, env_seen = [], []
    gate = threading.Barrier(4)

    class FakeClient:
        def __init__(self, key):
            self.key = key

        def subscribe(self, model_id, **kw):
            gate.wait(timeout=5)  # all four calls are inside subscribe at once
            env_seen.append(os.environ.get("FAL_KEY"))
            used.append(self.key)
            return {"image": {"url": f"https://x/{self.key}.png"}}
    monkeypatch.setattr(fal_helper, "client", FakeClient)
    monkeypatch.setenv("FAL_KEY", "process-key")
    threads = [threading.Thread(target=fal_helper.upscale_image, args=(f"key{i}", "https://x/a.png")) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(used) == ["key0", "key1", "key2", "key3"]
    assert set(env_seen) == {"process-key"} and os.environ["FAL_KEY"] == "process-key"


# --- execute_job: an unexpected engine crash fails the run instead of leaving it "executing" ------------------------

def test_an_engine_crash_fails_the_run_with_its_reason(monkeypatch):
    pytest.importorskip("palette_sdk")  # supplied by `pltt test` (or PYTHONPATH=<cli>/backend-sdk)
    import main
    from router_core import execution, registry
    from tests.conftest import REAL_REGISTRY
    monkeypatch.setattr(config, "REGISTRY_DIR", REAL_REGISTRY)
    registry.apps.cache_clear()
    registry.capabilities.cache_clear()

    run = {"id": "r1", "events": [], "assets": {}, "status": "approved", "execution": {"s1": {"status": "planned"}},
           "plan": {"steps": [{"step_id": "s1", "app_id": "pltt-flow-video-2"}]}}
    saved = []

    async def load(ctx, rid):
        return run

    async def save(ctx, r):
        saved.append(r["status"])

    async def noop(*a, **k):
        return None

    async def crash(r, save_cb):
        r["status"] = "executing"
        r["execution"]["s1"]["status"] = "running"
        raise RuntimeError("ffmpeg exploded")

    class Exec:
        id = "job1"
        progress = staticmethod(noop)
    monkeypatch.setattr(main, "_configure", lambda ctx: None)
    monkeypatch.setattr(main, "_load", load)
    monkeypatch.setattr(main, "_save", save)
    monkeypatch.setattr(main, "_local_assets", noop)
    monkeypatch.setattr(execution, "run_plan", crash)
    out = asyncio.run(main.execute_job(None, Exec(), {"run_id": "r1"}))
    assert out == {"run_id": "r1", "status": "failed"} and saved[-1] == "failed"
    ex = run["execution"]["s1"]
    assert ex["status"] == "failed" and "RuntimeError: ffmpeg exploded" in ex["message"]
    assert run["report"]["success"] is False and any("ffmpeg exploded" in str(e) for e in run["events"])
    registry.apps.cache_clear()  # back to the fixture registry for the other tests (monkeypatch restores the dir)
    registry.capabilities.cache_clear()


# --- a plan rewritten by the critic keeps the film's language (was: KeyError: 'language' at the narration) ----------

def test_a_rewritten_plan_keeps_the_language(monkeypatch, tmp_path):
    from engines import story_engine as se
    plans = iter([{"scenes": [{"script": "Hello there."}], "n": 1}, {"scenes": [{"script": "Hello again."}], "n": 2}])
    scores = iter([{"score": 64, "passed": False, "issues": ["weak hook"]}, {"score": 80, "passed": True, "issues": []}])
    seen = {}

    class Stop(BaseException):  # past the engine's own "except Exception" around the narration
        pass

    async def fake_plan(*a, **k):
        return next(plans)

    async def fake_critique(*a, **k):
        return next(scores)

    async def narration(lines, work, key, lang, *a, **k):
        seen["lang"], seen["lines"] = lang, lines
        raise Stop

    async def progress(_):
        pass
    monkeypatch.setattr(se, "plan", fake_plan)
    monkeypatch.setattr(se, "critique", fake_critique)
    monkeypatch.setattr(se, "record_narration", narration)
    monkeypatch.setattr(se, "fal_key", lambda: "k")
    with pytest.raises(Stop):
        asyncio.run(se.run({"name": "Flow Video", "app_id": "pltt-flow-video-2"},
                           {"prompt": "a short film about the sea", "request": {}}, {}, tmp_path, progress))
    assert seen == {"lang": "english", "lines": ["Hello again."]}  # the rewrite won, and it still has its language


# --- Retry resumes a film step where it failed (plan, images, clips are reused) --------------------------------------

def test_checkpoint_round_trip_and_key_change(tmp_path):
    from engines import story_engine as se
    ck = se.Checkpoint(str(tmp_path), {"prompt": "a"})
    ck.put("plan", {"scenes": [1]})
    assert se.Checkpoint(str(tmp_path), {"prompt": "a"}).get("plan") == {"scenes": [1]}
    assert se.Checkpoint(str(tmp_path), {"prompt": "b"}).get("plan") is None  # another request starts fresh
    none = se.Checkpoint(None, {})
    none.put("x", 1)
    assert none.get("x") is None


def test_a_retry_reuses_the_plan_images_and_clips(monkeypatch, tmp_path):
    from engines import story_engine as se
    calls = {"plan": 0, "critique": 0, "first_frame_scenes": [], "clip_scenes": []}

    async def fake_plan(*a, **k):
        calls["plan"] += 1
        return {"title": "t", "story": "s", "total": 10, "requested_total": 10, "bible": {}, "n": 1,
                "scenes": [{"title": f"S{i}", "duration": 5, "image_prompt": "x"} for i in range(2)]}

    async def fake_critique(*a, **k):
        calls["critique"] += 1
        return {"score": 90, "passed": True, "issues": []}

    async def fake_fal(fn, key, m, prompt, **kw):
        if fn.__name__ == "generate_image":
            calls["first_frame_scenes"].append(prompt[:2])
            return {"images": [{"url": f"img-{prompt[:2]}"}]}
        raise AssertionError("no clip should be made through _fal in this test")

    attempt = {"n": 1}

    async def fake_clip(app, i, n, s, still, ar, q, key, progress, **kw):
        calls["clip_scenes"].append(i)
        if attempt["n"] == 1 and i == 1:
            raise RuntimeError("fal 503 on scene 2")
        return f"clip{i}.mp4", "seedance_2_0"

    async def dl(url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("v")
        return str(dest)

    async def no_issues(*a, **k):
        return []

    class Done(BaseException):
        pass

    async def stop_at_join(*a, **k):
        raise Done

    async def progress(_):
        pass
    from pathlib import Path
    for name, fn in {"plan": fake_plan, "critique": fake_critique, "_fal": fake_fal, "_clip": fake_clip,
                     "_download": dl, "scene_issues": no_issues, "clip_issues": no_issues,
                     "consistency_issues": no_issues, "join": stop_at_join}.items():
        monkeypatch.setattr(se, name, fn)
    monkeypatch.setattr(se, "fal_key", lambda: "k")
    monkeypatch.setattr(se.music, "wanted", lambda *a, **k: False)  # no local MusicGen in a unit test
    async def no_places(p):
        return {}
    monkeypatch.setattr(se, "plan_locations", no_places)
    monkeypatch.setattr(se, "cast_list", lambda p, story=None: [])
    # stills are drawn with the scene title first in the prompt, so the fake image URL says which scene it was
    monkeypatch.setattr(se, "props_line", lambda *a, **k: "")
    handoff = {"prompt": "a film", "request": {"aspect_ratio": "16:9"}, "_resume": str(tmp_path / "resume"),
               "_resume_load": False}
    app = {"name": "Flow Video", "app_id": "pltt-flow-video-2"}
    with pytest.raises(RuntimeError):  # attempt 1: scene 2's clip fails
        asyncio.run(se.run(app, dict(handoff), {}, tmp_path / "a1", progress))
    images_1 = len(calls["first_frame_scenes"])
    attempt["n"] = 2
    calls["clip_scenes"].clear()
    with pytest.raises(Done):  # attempt 2 (the user pressed Retry): to the join without planning or drawing again
        asyncio.run(se.run(app, {**handoff, "_resume_load": True}, {}, tmp_path / "a2", progress))
    assert calls["plan"] == 1 and calls["critique"] == 1
    assert len(calls["first_frame_scenes"]) == images_1  # no image drawn twice
    assert calls["clip_scenes"] == [1]  # only the clip that failed is made again
    # a start that is NOT a Retry (e.g. a new approval) begins from the beginning
    with pytest.raises(Done):
        asyncio.run(se.run(app, dict(handoff), {}, tmp_path / "a3", progress))
    assert calls["plan"] == 2


def test_only_a_retry_marks_the_step_to_resume(monkeypatch):
    pytest.importorskip("palette_sdk")
    import main
    run = {"id": "r1", "status": "failed", "events": [], "plan_revision": 1,
           "execution": {"s1": {"status": "failed"}}, "plan": {"steps": [{"step_id": "s1"}]}}
    saved = []

    async def load(ctx, rid):
        return run

    async def save(ctx, r):
        saved.append(1)

    async def enqueue(*a, **k):
        return None
    monkeypatch.setattr(main, "_load", load)
    monkeypatch.setattr(main, "_save", save)
    monkeypatch.setattr(main, "_enqueue", enqueue)
    monkeypatch.setattr(main, "_public", lambda r: r)
    asyncio.run(main.recover("r1", main.Recovery(step_id="s1"), None))
    assert run["retry_steps"] == ["s1"] and run["execution"]["s1"]["status"] == "queued"


# --- graphics: only the seconds that carry graphics go through Chrome --------------------------------------------

def test_graphic_spans_pad_join_and_fall_back():
    from engines import motion_engine as me
    spans = me.graphic_spans([(0.2, 2.8), (10.0, 13.0), (13.5, 15.0), (56.3, 60.3)], 60.3)
    assert spans == [[0.0, 3.6], [9.6, 15.8], [55.9, 60.3]]  # padded, 10-13 and 13.5-15 joined, end card to the end
    assert me.graphic_spans([(0, 50)], 60) is None  # graphics nearly everywhere: one full render
    assert me.graphic_spans([], 60) is None


def test_finishing_times_cover_hook_overlays_accents_and_end_card():
    from engines import finishing
    gp = {"hook": {"text": "x", "start": 0.2, "end": 2.8}, "overlays": [{"start": 10, "end": 13}, {"start": "bad"}],
          "accents": [{"at": 20}], "end_card": {"title": "T", "start": 56}}
    assert finishing.graphic_times(gp, 60) == [(0.2, 2.8), (10.0, 13.0), (19.8, 20.8), (56.0, 60)]


def test_span_render_is_spliced_into_the_footage(monkeypatch, tmp_path):
    import shutil
    import subprocess
    from engines import motion_engine as me
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg not installed")
    base = tmp_path / "base.mp4"  # 6 s of 320x180 footage
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6",
                    "-pix_fmt", "yuv420p", str(base)], check=True)
    rendered = []

    async def fake_cli(args, cwd, timeout):  # HyperFrames stand-in: a red clip as long as the asked span
        out, a, b = args[args.index("--output") + 1], float(args[args.index("--start") + 1]), float(args[args.index("--end") + 1])
        rendered.append((a, b))
        subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=red:size=640x360:rate=30:duration={b - a}",
                        "-pix_fmt", "yuv420p", out], check=True)
        return 0, ""
    monkeypatch.setattr(me, "_cli", fake_cli)
    out = tmp_path / "motion.mp4"
    code, log = asyncio.run(me._render(tmp_path, out, str(base), [[1.0, 2.0], [4.0, 6.0]], 640, 360, 6.0))
    assert code == 0, log
    assert rendered == [(1.0, 2.0), (4.0, 6.0)]  # only 3 of the 6 seconds went through the renderer
    probe = subprocess.run([ff.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "stream=width,height:format=duration",
                            "-of", "json", str(out)], capture_output=True, text=True).stdout
    info = json.loads(probe)
    assert info["streams"][0]["width"] == 640 and abs(float(info["format"]["duration"]) - 6.0) < 0.1

    def red_at(t):  # is the frame at t red (a rendered span) or the footage?
        raw = subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-vf", "scale=1:1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return raw[0] > 200 and raw[1] < 60 and raw[2] < 60
    assert [red_at(t) for t in (0.5, 1.5, 3.0, 5.0)] == [False, True, False, True]
