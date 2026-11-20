"""3D Render: scene-spec normalisation, reference checks, the Kling-first / Seedance-fallback policy, frame-level
comparison, and (when Chrome + node are here) a real Three.js render and a realize with an injected transform."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engines import render3d_engine as R
from engines.render3d import continuity as C
from engines.render3d import providers as P
from engines.render3d import renderer
from engines.render3d import spec as S
from engines.temporal import graph as G


def _raw():
    return {
        "title": "Coming home", "duration": 9, "look": "warm evening, 35 mm",
        "environment": {"time_of_day": "golden", "ground": "grass"},
        "actors": [{"id": "A1", "name": "man", "kind": "humanoid", "start": {"x": 1.5, "z": 7, "facing": 180},
                    "look": "a man in his 30s"}],
        "props": [{"id": "P1", "type": "room", "w": 6, "d": 5}, {"id": "P2", "type": "door", "room": "P1"},
                  {"id": "P3", "type": "chair", "x": 0, "z": -1.0, "rot": 180}, {"id": "P4", "type": "spaceship"}],
        "events": [
            {"id": "S1", "graph_event": "E1", "actor": "A1", "action": "walks", "t0": 0, "t1": 2.6,
             "to": {"prop": "P2", "anchor": "approach"}},
            {"id": "S2", "graph_event": "E2", "actor": "A1", "action": "open", "target": "P2", "t0": 2.6, "t1": 4.0},
            {"id": "S3", "graph_event": "E3", "actor": "A1", "action": "walk", "t0": 3.5, "t1": 6.5, "to": {"prop": "P3"}},
            {"id": "S4", "graph_event": "E4", "actor": "A1", "action": "sit_down", "target": "P3", "t0": 6.6, "t1": 8.6},
            {"id": "S5", "actor": "ghost", "action": "walk", "t0": 1, "t1": 2}],
        "camera": [{"t0": 0, "t1": 4.2, "move": "follow", "target": "A1", "angle": 150},
                   {"t0": 3.8, "t1": 8, "move": "warp", "target": "A1"}]}


def _graph():
    return G.normalize({"events": [
        {"id": "E1", "description": "walks to the door"}, {"id": "E2", "description": "opens the door", "requires": ["E1"]},
        {"id": "E3", "description": "walks inside", "requires": ["E2"]}, {"id": "E4", "description": "sits down", "requires": ["E3"]}]})


# ------------------------------------------------------------------------------------------------ spec
def test_normalize_fixes_vocabulary_overlaps_and_camera():
    spec, notes = S.normalize(_raw(), aspect="16:9", resolution="720p", fps=24)
    assert (spec["width"], spec["height"], spec["fps"]) == (1280, 720, 24)
    acts = {e["id"]: e for e in spec["events"]}
    assert acts["S1"]["action"] == "walk" and acts["S4"]["action"] == "sit"  # synonyms
    assert "S5" not in acts  # unknown subject dropped
    assert acts["S3"]["t0"] == pytest.approx(4.0)  # waited for the previous move of A1? (open is a gesture, walk is not)
    assert next(p for p in spec["props"] if p["id"] == "P4")["type"] == "box"  # unknown type drawn as a box
    shots = spec["camera"]
    assert shots[0]["t0"] == 0 and shots[-1]["t1"] == spec["duration"]
    assert all(a["t1"] == b["t0"] for a, b in zip(shots, shots[1:]))  # contiguous, no gap, no overlap
    assert shots[1]["move"] == "follow"  # unknown move
    assert any("unknown type" in n for n in notes)


def test_normalize_moves_only_overlapping_locomotion():
    raw = _raw()
    raw["events"] = [{"id": "a", "actor": "A1", "action": "walk", "t0": 0, "t1": 3, "to": {"x": 0, "z": 0}},
                     {"id": "b", "actor": "A1", "action": "run", "t0": 2, "t1": 4, "to": {"x": 4, "z": 0}},
                     {"id": "c", "actor": "A1", "action": "wave", "t0": 1, "t1": 2}]
    spec, notes = S.normalize(raw)
    ev = {e["id"]: e for e in spec["events"]}
    assert ev["b"]["t0"] == pytest.approx(3) and ev["b"]["t1"] == pytest.approx(5)
    assert ev["c"]["t0"] == pytest.approx(1)  # a gesture may overlap a move
    assert any("moved to 3.00" in n for n in notes)


def test_normalize_compresses_long_films_to_the_model_limit():
    raw = _raw()
    raw["events"] = [{"id": "a", "actor": "A1", "action": "walk", "t0": 0, "t1": 50, "to": {"x": 0, "z": 0}}]
    spec, notes = S.normalize(raw)
    assert spec["duration"] <= S.MAX_DURATION and spec["events"][0]["t1"] <= S.MAX_DURATION
    assert any("compressed" in n for n in notes)


def test_normalize_refuses_an_empty_scene():
    with pytest.raises(S.SpecError):
        S.normalize({"actors": [], "props": []})


def test_coverage_finds_missing_and_out_of_order_events():
    spec, _ = S.normalize(_raw())
    g = _graph()
    assert S.coverage(spec, g) == []
    spec["events"] = [e for e in spec["events"] if e.get("graph_event") != ["E3"]]
    for e in spec["events"]:
        if e.get("graph_event") == ["E2"]:
            e["t0"] = -1  # opens before walking to the door
    codes = {i["code"] for i in S.coverage(spec, g)}
    assert codes == {G.MISSING_EVENT, G.WRONG_EVENT_ORDER}


def test_planner_prompt_lists_the_vocabulary():
    p = S.planner_prompt()
    for word in ("crane_arc", "pick_up", "explode", "tortoise", "room", "{") :
        assert word in p
    assert "{MOVES}" not in p and "{PROPS}" not in p


# ------------------------------------------------------------------------------------------------ reference checks
def _meta(positions, cams, vis=1.0, shot=None):
    return {"fps": 24, "frames_meta": [
        {"f": i, "t": i / 24, "shot": (shot[i] if shot else 0), "camera": {"pos": cams[i]},
         "actors": {"A1": {"pos": [x, 0, z], "visible": vis, "area": 0.05}}, "props": {}}
        for i, ((x, z)) in enumerate(positions)]}


def test_reference_issues_teleport_camera_jump_and_offscreen():
    spec = {"actors": [{"id": "A1", "kind": "humanoid"}],
            "events": [{"id": "S1", "actor": "A1", "action": "walk", "t0": 0, "t1": 1.0, "label": "walks"}]}
    n = 30
    walk = [(i * 0.05, 0) for i in range(n)]
    cams = [[i * 0.05, 1.6, 5] for i in range(n)]
    assert C.reference_issues(spec, _meta(walk, cams)) == []
    jumpy = walk[:10] + [(x + 5, z) for x, z in walk[10:]]
    assert {i["code"] for i in C.reference_issues(spec, _meta(jumpy, cams))} == {G.POSITION_DISCONTINUITY}
    cam_jump = cams[:15] + [[c[0] + 3, c[1], c[2]] for c in cams[15:]]
    assert {i["code"] for i in C.reference_issues(spec, _meta(walk, cam_jump))} == {C.CAMERA_DISCONTINUITY}
    # a planned cut between shots is not a discontinuity
    assert C.reference_issues(spec, _meta(walk, cam_jump, shot=[0] * 15 + [1] * 15)) == []
    assert {i["code"] for i in C.reference_issues(spec, _meta(walk, cams, vis=0.1))} == {C.OFFSCREEN}


def test_reference_issues_interaction_distance():
    spec = {"actors": [{"id": "A1", "kind": "humanoid"}, {"id": "A2", "kind": "quadruped", "height": 0.2}],
            "events": [{"id": "S1", "actor": "A1", "action": "meet", "target": "A2", "t0": 0, "t1": 0.5, "label": "meets"}]}
    frames = [{"f": i, "t": i / 24, "shot": 0, "camera": {"pos": [0, 1, 5]},
               "actors": {"A1": {"pos": [0, 0, 0], "visible": 1, "area": 0.1}, "A2": {"pos": [6, 0, 0], "visible": 1, "area": 0.1}}}
              for i in range(13)]
    assert {i["code"] for i in C.reference_issues(spec, {"fps": 24, "frames_meta": frames})} == {C.MISSING_INTERACTION}


# ------------------------------------------------------------------------------------------------ providers
def test_provider_order_is_kling_first(monkeypatch):
    monkeypatch.delenv("RENDER3D_PRIMARY", raising=False)
    monkeypatch.delenv("RENDER3D_FALLBACK", raising=False)
    assert P.order() == ["kling_4_0", "seedance_2_0"]
    monkeypatch.setenv("RENDER3D_FALLBACK", "off")
    assert P.order() == ["kling_4_0"]


def test_fit_size_respects_each_endpoint():
    for w, h in ((1280, 720), (720, 1280), (1024, 1024), (1920, 1080), (640, 360)):
        kw, kh = P.fit_size(w, h, "kling_4_0")
        assert min(kw, kh) >= 700 and max(kw, kh) <= 4553 and kw % 2 == 0 and kh % 2 == 0
        sw, sh = P.fit_size(w, h, "seedance_2_0")
        assert 640 * 640 <= sw * sh <= 834 * 1112 and abs(sw / sh - w / h) < 0.02


def test_chunks_cut_at_shots_and_never_leave_a_short_tail():
    assert P.chunks(12, [], 15) == [(0.0, 12)]
    shots = [{"t0": 0}, {"t0": 6}, {"t0": 13}, {"t0": 22}]
    parts = P.chunks(28, shots, 15)
    assert parts[0] == (0.0, 13) and parts[-1][1] == 28
    assert all(b - a <= 15 + 1e-6 and b - a >= 2 for a, b in parts)
    parts = P.chunks(16, [], 15)
    assert all(b - a >= P.LIMITS["seedance"]["min_s"] for a, b in parts)


def test_request_fields_match_the_endpoint_schemas():
    ep, a = P.request("kling_4_0", "p", "https://x/v.mp4", seconds=8, aspect="16:9", resolution="720p")
    assert ep == "kling/v4/video-to-video/edit" and a["video_url"] == "https://x/v.mp4" and a["resolution"] == "720p"
    assert "video_urls" not in a and "duration" not in a  # Kling follows the source video's length
    ep, a = P.request("seedance_2_0", "p", "https://x/v.mp4", seconds=8.4, aspect="9:16", resolution="1080p")
    assert ep == "bytedance/seedance-2.0/reference-to-video" and a["video_urls"] == ["https://x/v.mp4"]
    assert a["duration"] == "8" and a["aspect_ratio"] == "9:16" and a["resolution"] == "1080p"


def _video(path: Path, seconds=4, size="1280x720", color="testsrc2"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{color}=size={size}:rate=24", "-t", str(seconds),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


@needs_ffmpeg
async def test_transform_falls_back_to_seedance_only_when_kling_fails(tmp_path):
    ref = _video(tmp_path / "ref.mp4")
    calls = []

    async def upload(p):
        return f"https://up/{Path(p).name}"

    async def download(url, dest):
        shutil.copyfile(ref, dest)
        return str(dest)

    async def kling_refuses(ep, args):
        calls.append(ep)
        if ep.startswith("kling"):
            raise RuntimeError("422 Unprocessable Entity: video rejected")
        return {"video": {"url": "https://out/s.mp4"}}

    res = await P.transform(ref, "prompt", tmp_path / "w1", aspect="16:9", resolution="720p", shots=[{"t0": 0}],
                            subscribe=kling_refuses, upload=upload, download=download)
    assert calls == ["kling/v4/video-to-video/edit", "bytedance/seedance-2.0/reference-to-video"]  # no Kling retry for a refusal
    assert res["model"] == "seedance_2_0" and res["fallback_used"]
    assert [a["status"] for a in res["attempts"]] == ["error", "succeeded"]

    calls.clear()

    async def kling_ok(ep, args):
        calls.append(ep)
        await asyncio.sleep(0.05)  # slow is not a failure
        return {"video": {"url": "https://out/k.mp4"}}

    res = await P.transform(ref, "prompt", tmp_path / "w2", aspect="16:9", resolution="720p", shots=[],
                            subscribe=kling_ok, upload=upload, download=download)
    assert calls == ["kling/v4/video-to-video/edit"] and res["model"] == "kling_4_0" and not res["fallback_used"]
    assert renderer.probe(res["path"])["duration"] == pytest.approx(4, abs=0.1)

    calls.clear()
    state = {"n": 0}

    async def kling_transient(ep, args):
        calls.append(ep)
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("503 Service Unavailable")
        return {"video": {"url": "https://out/k.mp4"}}

    res = await P.transform(ref, "prompt", tmp_path / "w3", aspect="16:9", resolution="720p", shots=[],
                            subscribe=kling_transient, upload=upload, download=download)
    assert calls == ["kling/v4/video-to-video/edit"] * 2 and res["model"] == "kling_4_0"


@needs_ffmpeg
async def test_transform_reports_every_failure(tmp_path):
    ref = _video(tmp_path / "ref.mp4")

    async def boom(ep, args):
        raise RuntimeError("400 bad request")

    async def upload(p):
        return "https://up/x.mp4"

    with pytest.raises(P.TransformError) as e:
        await P.transform(ref, "p", tmp_path / "w", aspect="16:9", resolution="720p", shots=[], subscribe=boom,
                          upload=upload, download=None)
    assert {a["model"] for a in e.value.attempts} == {"kling_4_0", "seedance_2_0"}


@needs_ffmpeg
def test_prepare_pads_short_and_scales_small_inputs(tmp_path):
    ref = _video(tmp_path / "ref.mp4", seconds=2, size="640x360")
    out = P.prepare(ref, "kling_4_0", tmp_path / "k.mp4")
    pr = renderer.probe(out)
    assert min(pr["width"], pr["height"]) >= 700 and pr["duration"] >= 3.0


@needs_ffmpeg
def test_frame_similarity_separates_same_from_different(tmp_path):
    a = _video(tmp_path / "a.mp4", seconds=3)
    b = _video(tmp_path / "b.mp4", seconds=3, color="mandelbrot")
    same = C.frame_similarity(a, a, 3)
    diff = C.frame_similarity(a, b, 3)
    assert same["layout_mean"] > 0.95 and diff["layout_mean"] < same["layout_mean"] - 0.3


# ------------------------------------------------------------------------------------------------ the agent
def test_wanted_and_build(monkeypatch):
    from router_core import config, registry
    monkeypatch.setattr(config, "REGISTRY_DIR", Path(__file__).resolve().parents[1] / "api" / "router_core" / "registry")
    registry.apps.cache_clear()
    registry.capabilities.cache_clear()
    try:
        assert R.wanted({"mode": "render3d", "prompt": "x"})
        assert R.wanted({"mode": "auto", "prompt": "a 3D render of a rabbit jumping over a river", "assets": {}})
        assert R.wanted({"mode": "auto", "prompt": "make it 3d to realistic please", "assets": {}})
        assert not R.wanted({"mode": "auto", "prompt": "a rabbit jumping over a river", "assets": {}})
        for p in ("make a 3d video of a rabbit", "3d animation of a car race", "render this in 3d", "a 3D product ad",
                  "use 3drender for a dog playing", "show it as 3d"):
            assert R.wanted({"mode": "auto", "prompt": p, "assets": {}}), p
        for p in ("3d motion graphics explainer about taxes", "a 3D collage explainer", "a cat video in 4k"):
            assert not R.wanted({"mode": "auto", "prompt": p, "assets": {}}), p
        assert not R.wanted({"mode": "auto", "prompt": "3d render this", "assets": {"a1": {"modality": "video"}}})
        d = R.build({"prompt": "a man walks in and sits", "settings": {"aspect_ratio": "9:16", "duration_s": 8},
                     "assets": {"a1": {"id": "a1", "modality": "image"}}})
        step = d["plan"]["steps"][0]
        req = step["handoff"]["request"]
        assert step["app_id"] == R.APP_ID and req["aspect_ratio"] == "9:16" and not req["review_first"] and req["images"] == ["a1"]
        assert R.build({"prompt": "3d render, show me the 3d first", "settings": {}})["plan"]["steps"][0]["handoff"]["request"]["review_first"]
    finally:
        registry.apps.cache_clear()
        registry.capabilities.cache_clear()


def test_realism_prompt_keeps_the_reference_and_fits():
    spec, _ = S.normalize(_raw())
    spec["events"] = spec["events"] * 30
    p = R.realism_prompt(spec, "rainy", n_images=2)
    assert p.startswith("@Video1") and "Keep exactly" in p and "@Image1-@Image2" in p and "rainy" in p
    assert len(p) <= 2500


needs_renderer = pytest.mark.skipif(renderer.available() is not None, reason=f"renderer not available: {renderer.available()}")


@needs_renderer
async def test_real_render_passes_are_frame_aligned_and_realize_composes(tmp_path):
    spec, _ = S.normalize({
        "title": "t", "environment": {"time_of_day": "day", "ground": "grass"},
        "actors": [{"id": "A1", "name": "rabbit", "kind": "quadruped", "species": "rabbit", "height": 0.2,
                    "start": {"x": -2, "z": 0, "facing": 90}},
                   {"id": "A2", "name": "tortoise", "kind": "quadruped", "species": "tortoise", "start": {"x": 2.5, "z": 0, "facing": -90}}],
        "props": [{"id": "P1", "type": "river", "x": 0, "z": 0, "rot": 90, "w": 1.2, "d": 12}],
        "events": [{"id": "S1", "actor": "A1", "action": "jump", "t0": 0.2, "t1": 1.6, "to": {"x": 1.0, "z": 0}, "label": "jumps the river"},
                   {"id": "S2", "actor": "A1", "action": "meet", "target": "A2", "t0": 1.8, "t1": 3.2, "label": "meets the tortoise"}],
        "camera": [{"t0": 0, "t1": 3.5, "move": "track_side", "target": "A1", "distance": 2.2}], "duration": 3.5})
    film = renderer.write_spec(tmp_path / "film", spec)
    rend = await renderer.render(film, tmp_path / "passes")
    probes = {p: renderer.probe(f) for p, f in rend["passes"].items()}
    assert set(probes) == set(renderer.PASSES)
    assert len({(v["width"], v["height"], v["frames"]) for v in probes.values()}) == 1  # identical size and frame count
    assert probes["rgb"]["frames"] == round(spec["duration"] * spec["fps"]) == len(rend["meta"]["frames_meta"])
    m = rend["meta"]["frames_meta"]
    assert m[0]["actors"]["A1"]["pos"][0] == pytest.approx(-2, abs=0.01)
    assert max(f["actors"]["A1"]["pos"][1] for f in m) > 0.1  # it really leaves the ground over the river
    assert not [i for i in C.reference_issues(spec, rend["meta"]) if i["code"] == G.POSITION_DISCONTINUITY]

    async def fake_transform(ref, prompt, work, **kw):  # a "realistic" video: the reference, re-graded
        work.mkdir(parents=True, exist_ok=True)
        out = work / "fake.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", ref, "-f", "lavfi", "-i", "sine=f=220:d=10",
                        "-vf", "eq=saturation=1.4:gamma=1.1", "-shortest", "-c:a", "aac", str(out)], check=True)
        return {"path": str(out), "model": "kling_4_0", "label": P.LABELS["kling_4_0"], "attempts": [], "fallback_used": False}

    async def prog(msg):
        pass
    r3d = {"spec": spec, "reference": rend["passes"]["rgb"], "graph": {"events": []}, "images": []}
    out = await R.realize(r3d, tmp_path / "realize", prog, transform=fake_transform)
    assert out["stage"] == "final" and Path(out["final"]).exists() and Path(out["comparison"]).exists()
    pr = renderer.probe(out["final"])
    assert (pr["width"], pr["height"]) == (spec["width"], spec["height"]) and pr["audio"]
    assert pr["duration"] == pytest.approx(spec["duration"], abs=0.1)
    assert out["provider"]["model"] == "kling_4_0" and not out["provider"]["fallback_used"]
    assert out["validation"]["similarity"]["layout_mean"] > 0.6  # the same composition as the reference
    assert out["validation"]["status"] in ("PASS", "UNCERTAIN")  # never FAIL for an unchanged layout


@needs_ffmpeg
async def test_an_account_error_stops_without_a_pointless_fallback(tmp_path):
    ref = _video(tmp_path / "ref.mp4")
    calls = []

    async def locked_upload(p):
        calls.append("upload")
        raise RuntimeError("Client error '403 Forbidden' for url 'https://rest.fal.ai/storage/auth/token'")

    with pytest.raises(P.TransformError) as e:
        await P.transform(ref, "p", tmp_path / "w", aspect="16:9", resolution="720p", shots=[], subscribe=None,
                          upload=locked_upload, download=None)
    assert e.value.account and "fal.ai/dashboard/billing" in str(e.value)
    assert calls == ["upload"]  # Seedance is not tried on the same locked account
    assert [a["status"] for a in e.value.attempts] == ["error", "skipped"]
