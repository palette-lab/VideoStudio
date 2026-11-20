"""3D Render: 3D to realistic video.

    USER INPUT -> story + event understanding (engines/temporal)  -> scene planning (render3d/spec)
    -> Three.js scene + animation timeline + camera (tools/render3d) -> RGB + depth + normal + masks + metadata
    -> reference checks (render3d/continuity) -> straight on, automatically (no review stop unless asked for)
    -> Kling 4.0 video-to-video (Seedance 2.0 only if Kling fails) (render3d/providers)
    -> the realistic video compared with the reference, one targeted retake of a failed event's shot -> final MP4

The 3D animation decides what happens, the camera how it is shown, the video model how real it looks, and the checks
whether the sequence survived. The 3D reference and every pass are kept next to the realistic video.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .render3d import continuity, providers, renderer
from .render3d import spec as S

log = logging.getLogger("palette_video.render3d")

APP_ID = "render3d-realistic-video"
NAME = "3D Render"
FIX_ROUNDS = 1
MAX_RETAKES = int(os.getenv("RENDER3D_MAX_RETAKES", "1"))
ASKS = re.compile(r"\b3d\s*render|\brender\w*\b.{0,40}\b(in|as)\s+3d\b|\b3d[- ]to[- ](realistic|real|photo\s*real)|"
                  r"\b3d\s+(reference|draft|blocking|previs)\b|\bprevis\b|\bthree\.?js\b|"
                  r"\b(in|as)\s+(a\s+)?3d\b|\b3d\s+(\w+\s+){0,2}(video|animation|animated|film|movie|scene|clip|ad|advert\w*|"
                  r"commercial|story|cartoon|short|trailer|shot)\b", re.I)
# "3D motion graphics", "3D explainer", "3D collage" stay with the 3D Motion Video agent
NOT_ASKS = re.compile(r"\b3d\s+(motion|collage|explainer|infographic|text|title|logo|chart)|motion\s+graphics", re.I)
MODEL_EXT = renderer.MODEL_EXT


class Render3DError(RuntimeError):
    pass


def wanted(run: dict) -> bool:
    """The 3D Render mode, or in Auto a request that names it - with no footage, audio or document of the user's."""
    if run.get("mode") == "render3d":
        return True
    if run.get("mode") != "auto" or run.get("parent_run"):
        return False
    if any(a.get("modality") in ("video", "audio", "document") for a in (run.get("assets") or {}).values()):
        return False
    p = run.get("prompt") or ""
    return bool(ASKS.search(p)) and not (NOT_ASKS.search(p) and not re.search(r"\b3d\s*render|render3d|3drender", p, re.I))


def available() -> str | None:
    return renderer.available()


def _quality() -> dict:
    from . import story_engine
    q = story_engine.quality()
    return {"resolution": "1080p" if q.get("resolution") in ("1080p", "4k") else "720p"}


# ------------------------------------------------------------------------------------------------ the plan
def build(run: dict) -> dict:
    """{"understanding", "plan"}: one 3D Render step with the user's words, settings and images."""
    from router_core import registry
    app = registry.apps()[APP_ID]
    st = run.get("settings") or {}
    words = (run.get("prompt") or "").strip()
    dur = st.get("duration_s")
    ar = st.get("aspect_ratio") if st.get("aspect_ratio") in S.SIZES else "16:9"
    res = st.get("resolution") if st.get("resolution") in ("720p", "1080p", "4k") else _quality()["resolution"]
    fps = int(st.get("fps")) if str(st.get("fps") or "").isdigit() and int(st["fps"]) in S.FPS_ALLOWED else 24
    # The 3D scene goes straight to the realistic render; it stops for a look only when the user asks for that.
    review = bool(re.search(r"\b(review|preview|check|see|show me) (the )?3d( (scene|reference|draft|version))? first\b|"
                            r"\b3d (draft|preview) only\b", words, re.I))
    caps = ["render3d.photoreal_from_3d"]
    images = [a["id"] for a in (run.get("assets") or {}).values() if a.get("modality") == "image"]
    models = [a["id"] for a in (run.get("assets") or {}).values() if a.get("modality") == "model"]
    step = {"step_id": "s1", "app_id": APP_ID, "subtask_ids": ["t_3d"], "capabilities_used": caps,
            "responsibility": ("Animate the story as a 3D scene with exact actions and camera moves, then turn it into a "
                               "realistic video" + (" (stopping first so you can see the 3D version)" if review else "") + "."),
            "inputs": [{"source": "prompt", "format": "text"}] + [{"source": "asset", "asset_id": i, "format": "image_file"} for i in images],
            "outputs": ["video_file"],
            "handoff": {"app_id": APP_ID, "app_name": app["name"], "prompt": words, "language": "", "endpoint": "",
                        "request": {"prompt": words, "duration": dur, "aspect_ratio": ar, "resolution": res, "fps": fps,
                                    "review_first": review, "images": images, "models": models}, "notes": []}}
    understanding = {
        "detected_intent": "create_new", "user_goal": words, "goal": words,
        "requested_output": f"a photoreal video made from a 3D animation, {ar}" + (f", {dur} s" if dur else ""),
        "production_type": ["cinematic"], "assumptions": [
            "the story is staged as a real 3D animation first; the video model only changes how it looks",
            "Kling 4.0 is the primary video-to-video model; Seedance 2.0 runs only if Kling fails"],
        "scene": {"narrative": True}, "constraints": {"duration_s": dur, "aspect_ratio": ar},
        "subtasks": [{"id": "t_3d", "group": "generate", "description": "3D reference, then a photoreal video from it",
                      "required_capabilities": caps, "basis": "request"}],
        "direct": True, "confidence": 1.0}
    return {"understanding": understanding, "plan": {"feasible": True, "steps": [step], "direct": True}}


# ------------------------------------------------------------------------------------------------ the 3D reference
def _event_times(spec: dict) -> list[float]:
    # 65% into each event: the action is well under way (a sit is mostly down, a jump in the air)
    return [round(min(spec["duration"] - 0.05, e["t0"] + 0.65 * (e["t1"] - e["t0"])), 2) for e in spec["events"]] or [spec["duration"] / 2]


def _sheet(stills: list[Path], dest: Path) -> str | None:
    if not stills:
        return None
    cols = 4 if len(stills) > 4 else len(stills)
    rows = (len(stills) + cols - 1) // cols
    args = ["ffmpeg", "-y", "-v", "error"]
    for p in stills:
        args += ["-i", str(p)]
    pads = len(stills)
    filt = "".join(f"[{i}:v]scale=480:-2,setsar=1[s{i}];" for i in range(pads))
    layout ="|".join(f"{'+'.join(['w0'] * (i % cols)) or '0'}_{'+'.join(['h0'] * (i // cols)) or '0'}" for i in range(pads))
    filt += "".join(f"[s{i}]" for i in range(pads)) + (f"xstack=inputs={pads}:layout={layout}:fill=black" if pads > 1 else "copy")
    r = subprocess.run(args + ["-filter_complex", filt, "-frames:v", "1", str(dest)], capture_output=True, timeout=120)
    return str(dest) if r.returncode == 0 else None


async def reference(prompt: str, req: dict, work: Path, progress, name: str, assets: dict | None = None) -> dict:
    """Plan, render and check the 3D reference. Returns the render3d record (everything later steps need)."""
    from . import temporal
    why = renderer.available()
    if why:
        raise Render3DError(why)
    aspect = req.get("aspect_ratio") if req.get("aspect_ratio") in S.SIZES else "16:9"
    resolution = req.get("resolution") if req.get("resolution") in ("720p", "1080p", "4k") else "720p"
    fps = int(req.get("fps") or 24)
    seconds = float(req["duration"]) if req.get("duration") else None
    assets = assets or {}
    images = [assets[i] for i in req.get("images") or [] if i in assets and assets[i].get("path")]
    models = [assets[i] for i in req.get("models") or [] if i in assets and assets[i].get("path")]
    images_note = "; ".join(f"image {k + 1} ({a.get('filename')}): {a.get('summary') or 'no description'}"
                            for k, a in enumerate(images))
    if models:
        images_note += ("; " if images_note else "") + "3D model files the user uploaded (use them as props with "\
            "\"asset\" set to the name): " + ", ".join(f"assets/{Path(a['filename']).name}" for a in models)
    work.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    await progress(f"{name}: understanding the story (characters, objects, places, every event in order)")
    graph = await temporal.extract(prompt, files_note=images_note)
    n_ev = len(graph.get("events") or [])
    await progress(f"{name}: {n_ev} event{'s' if n_ev != 1 else ''} found; staging them as a 3D scene")
    raw = await S.plan(prompt, graph, seconds, aspect, images_note)
    spec, notes = S.normalize(raw, aspect=aspect, resolution=resolution, fps=fps, seconds=seconds)
    film = work / "film"
    model_files = {Path(a["filename"]).name: a["path"] for a in models}

    issues: list[dict] = []
    for rnd in range(FIX_ROUNDS + 1):
        renderer.write_spec(film, spec, model_files)
        await progress(f"{name}: building the 3D scene - {len(spec['actors'])} character(s), {len(spec['props'])} "
                       f"object(s), {len(spec['events'])} animated steps, {len(spec['camera'])} camera shot(s)")
        meta_dir = work / "meta"
        if meta_dir.exists():
            shutil.rmtree(meta_dir)
        code, text = await renderer._node([str(film), "--out", str(meta_dir), "--meta-only"], 1200)
        if code != 0:
            err = renderer._error(text)
            if rnd < FIX_ROUNDS:
                await progress(f"{name}: the scene failed to build ({err[:100]}); re-planning it")
                raw = await S.plan(prompt, graph, seconds, aspect, images_note, fix=[f"the renderer failed: {err}"], previous=raw)
                spec, notes = S.normalize(raw, aspect=aspect, resolution=resolution, fps=fps, seconds=seconds)
                continue
            raise Render3DError("the 3D scene failed to build: " + err)
        meta = json.loads((meta_dir / "metadata.json").read_text())
        issues = continuity.reference_issues(spec, meta, graph)
        severe = [i for i in issues if i.get("code") in continuity.SEVERE or i.get("code") in (continuity.OFFSCREEN, continuity.CAMERA_DISCONTINUITY)]
        if not severe or rnd == FIX_ROUNDS:
            break
        await progress(f"{name}: the 3D reference has {len(severe)} staging problem(s) ({severe[0]['message'][:90]}); fixing the plan")
        raw = await S.plan(prompt, graph, seconds, aspect, images_note, fix=[i["message"] for i in severe], previous=raw)
        spec, notes = S.normalize(raw, aspect=aspect, resolution=resolution, fps=fps, seconds=seconds)

    nframes = round(spec["duration"] * spec["fps"])
    await progress(f"{name}: rendering {nframes} frames x 5 passes (RGB, depth, normal, character masks, object masks)")
    rend = await renderer.render(film, work / "passes")
    issues = continuity.reference_issues(spec, rend["meta"], graph)
    times = _event_times(spec)
    stills = []
    for k, t in enumerate(times):
        p = continuity.still(rend["passes"]["rgb"], t, work / "stills" / f"ev_{k:02d}.jpg")
        if p:
            stills.append(p)
    await progress(f"{name}: checking the 3D reference (positions, camera, every event on screen, a look at each step)")
    issues += await continuity.vision_reference(stills[:10], spec["events"][:10])
    sheet = _sheet(stills[:12], work / "sheet.jpg")
    pr = renderer.probe(rend["passes"]["rgb"])
    return {"stage": "reference", "spec": spec, "notes": notes,
            "graph": {"events": [{k: e.get(k) for k in ("id", "seq", "description", "characters", "objects", "requires",
                                                          "essential", "duration_s")} for e in graph.get("events") or []],
                      "entities": graph.get("entities") or {}, "acts": graph.get("acts") or {}},
            "reference": rend["passes"]["rgb"], "passes": rend["passes"], "metadata": rend["metadata"],
            "renderer": {"gl": rend["info"].get("gl"), "meshes": rend["info"].get("meshes"),
                         "masks": rend["info"].get("masks"), "timeline": rend["info"].get("timeline"),
                         "warnings": rend["warnings"][:500]},
            "probe": pr, "sheet": sheet, "stills": [str(p) for p in stills],
            "reference_check": {"status": "FAIL" if any(i.get("severity") == "major" for i in issues) else "PASS",
                                "issues": issues},
            "images": [a["path"] for a in images], "work": str(work), "seconds": round(time.time() - t_start, 1)}


# ------------------------------------------------------------------------------------------------ the realistic video
def realism_prompt(spec: dict, instruction: str = "", focus: list[dict] | None = None, n_images: int = 0) -> str:
    """One provider-neutral instruction: keep everything from @Video1, change only how real it looks."""
    who = "; ".join(f"{a['name']}: {a['look'] or a.get('species') or a['kind']}" for a in spec["actors"])[:700]
    what = "; ".join(f"{p['name']}: {p['look']}" for p in spec["props"] if p.get("look"))[:400]
    timeline = "; ".join(f"{e['t0']:.1f}-{e['t1']:.1f}s {e['label']}" for e in spec["events"])
    parts = [
        "@Video1 is an untextured 3D animation that fixes the film's action, positions, timing and camera. Re-render it "
        "as photoreal live-action cinematic footage.",
        "Keep exactly from @Video1: every movement and its timing, the order of events, each character's and object's "
        "position and path, poses and contacts, the camera position, path, framing and cuts, the scene layout.",
        "Change only the appearance" + (f": {spec['look']}" if spec.get("look") else "") + ".",
        f"Place: {spec['environment'].get('description')}." if spec["environment"].get("description") else "",
        f"Characters: {who}." if who else "",
        f"Objects: {what}." if what else "",
        (f"Reference images @Image1-@Image{n_images} show how the user's characters / products look; match them."
         if n_images else ""),
        "Real skin, hair, fur, fabric and material detail, natural light and shadows, believable depth of field.",
        ("Make sure these happen clearly: " + "; ".join(f"{e['t0']:.1f}-{e['t1']:.1f}s {e['label']}" for e in focus) + "."
         if focus else ""),
        f"Direction: {instruction.strip()}." if instruction.strip() else "",
        "No text, captions, logos or watermarks; no extra people or objects; nothing that is not in @Video1.",
    ]
    head = " ".join(p for p in parts if p)
    room = 2450 - len(head) - 12
    return head + (f" Timeline: {timeline[:room]}" if room > 60 else "")


def _ff(args: list[str], timeout: int = 900) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise Render3DError(f"ffmpeg: {(r.stderr or '').strip()[-300:]}")


def conform(src: str, spec: dict, dest: Path) -> str:
    """The final frame: the planned size and frame rate, the reference's length; sound kept."""
    pr = renderer.probe(src)
    _ff(["-i", src, "-vf", f"scale={spec['width']}:{spec['height']}:force_original_aspect_ratio=decrease,"
         f"pad={spec['width']}:{spec['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={spec['fps']}",
         "-t", f"{spec['duration']:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"]
        + (["-c:a", "aac", "-b:a", "192k"] if pr["audio"] else ["-an"]) + ["-movflags", "+faststart", str(dest)])
    return str(dest)


def side_by_side(ref: str, out: str, dest: Path) -> str | None:
    pr = renderer.probe(out)
    h = 540 if pr["height"] >= pr["width"] else 360
    try:
        _ff(["-i", ref, "-i", out, "-filter_complex", f"[0:v]scale=-2:{h},setsar=1[a];[1:v]scale=-2:{h},setsar=1[b];[a][b]hstack=inputs=2[v]",
             "-map", "[v]"] + (["-map", "1:a?", "-c:a", "aac"] if pr["audio"] else []) +
            ["-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", str(dest)])
        return str(dest)
    except Render3DError as e:
        log.warning("comparison video failed: %s", e)
        return None


def splice(base: str, part: str, t0: float, t1: float, dest: Path) -> str:
    """base with [t0, t1) replaced by part (time-fitted to that window), same size and rate."""
    pb, pp = renderer.probe(base), renderer.probe(part)
    w, h, fps = pb["width"], pb["height"], pb["fps"] or 24
    audio = pb["audio"] and pp["audio"]
    sp = (pp["duration"] / (t1 - t0)) if pp["duration"] and t1 > t0 else 1.0
    fc = (f"[0:v]trim=0:{t0:.3f},setpts=PTS-STARTPTS[a];[1:v]setpts=PTS/{sp:.6f},trim=0:{t1 - t0:.3f},scale={w}:{h},setsar=1,fps={fps:g},setpts=PTS-STARTPTS[b];"
          f"[0:v]trim={t1:.3f},setpts=PTS-STARTPTS[c];")
    if audio:
        fc += (f"[0:a]atrim=0:{t0:.3f},asetpts=PTS-STARTPTS[x];[1:a]atempo={min(2, max(0.5, sp)):.6f},atrim=0:{t1 - t0:.3f},asetpts=PTS-STARTPTS[y];"
               f"[0:a]atrim={t1:.3f},asetpts=PTS-STARTPTS[z];[a][x][b][y][c][z]concat=n=3:v=1:a=1[v][o]")
    else:
        fc += "[a][b][c]concat=n=3:v=1:a=0[v]"
    _ff(["-i", base, "-i", part, "-filter_complex", fc, "-map", "[v]"] + (["-map", "[o]", "-c:a", "aac"] if audio else []) +
        ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)])
    return str(dest)


async def realize(r3d: dict, work: Path, progress, name: str = NAME, instruction: str = "", transform=None) -> dict:
    """The realistic video from a stored 3D reference: transform, compare, one targeted retake, compose.
    Returns the updated render3d record (stage "final"); `transform` is injectable for tests."""
    from . import music
    transform = transform or providers.transform
    spec, ref = r3d["spec"], r3d["reference"]
    if not Path(ref).exists():
        raise Render3DError("the 3D reference is no longer on this computer; render the 3D scene again")
    work.mkdir(parents=True, exist_ok=True)
    images = [p for p in r3d.get("images") or [] if Path(p).exists()]
    prompt = realism_prompt(spec, instruction, n_images=len(images))
    res = await transform(ref, prompt, work / "transform", aspect=spec["aspect"], resolution=spec["resolution"],
                          shots=spec["camera"], image_paths=images, progress=progress, label=f"{name}: ")
    final = conform(res["path"], spec, work / "realistic.mp4")
    await progress(f"{name}: comparing the realistic video with the 3D reference, frame by frame")
    essential_graph = {e["id"] for e in r3d.get("graph", {}).get("events") or [] if e.get("essential", True)}
    essential = {e["id"] for e in spec["events"] if not essential_graph or set(e.get("graph_event") or []) & essential_graph
                 or not e.get("graph_event")}
    report = await continuity.compare(ref, final, spec, work / "compare", essential)
    attempts = list(res.get("attempts") or [])
    retakes = []
    for k in range(MAX_RETAKES):
        if report["status"] != "FAIL" or not report["failed_events"]:
            break
        ev = next(e for e in spec["events"] if e["id"] == report["failed_events"][0])
        shot = next((s for s in spec["camera"] if s["t0"] <= (ev["t0"] + ev["t1"]) / 2 < s["t1"]), spec["camera"][-1])
        a, b = min(shot["t0"], ev["t0"]), max(shot["t1"], ev["t1"])
        if b - a < 3.0:  # the endpoints take at least 3 s
            a, b = max(0.0, (a + b) / 2 - 1.5), min(spec["duration"], (a + b) / 2 + 1.5)
            if b - a < 3.0:
                a, b = max(0.0, b - 3.0), min(spec["duration"], a + 3.0)
        await progress(f"{name}: '{ev['label'][:60]}' did not come through; re-making {a:.1f}-{b:.1f} s only")
        window = work / f"retake{k}"
        window.mkdir(exist_ok=True)
        cut = window / "ref_window.mp4"
        _ff(["-ss", f"{a:.3f}", "-i", ref, "-t", f"{b - a:.3f}", "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(cut)])
        sub = {**spec, "duration": round(b - a, 3), "events": [{**e, "t0": max(0, e["t0"] - a), "t1": min(b - a, e["t1"] - a)}
                                                               for e in spec["events"] if e["t1"] > a and e["t0"] < b]}
        try:
            part = await transform(str(cut), realism_prompt(sub, instruction, focus=[{**ev, "t0": max(0, ev["t0"] - a), "t1": min(b - a, ev["t1"] - a)}],
                                                            n_images=len(images)),
                                   window, aspect=spec["aspect"], resolution=spec["resolution"],
                                   shots=[{"t0": 0, "t1": b - a}], image_paths=images, progress=progress, label=f"{name}: ")
        except providers.TransformError as e:
            attempts += e.attempts
            retakes.append({"event": ev["id"], "window": [a, b], "status": "failed", "reason": str(e)[:300]})
            break
        attempts += [{**x, "retake": k + 1} for x in part.get("attempts") or []]
        candidate = conform(splice(final, part["path"], a, b, window / "spliced.mp4"), spec, window / "realistic.mp4")
        rep2 = await continuity.compare(ref, candidate, spec, window / "compare", essential)
        better = continuity.score(rep2) > continuity.score(report)
        retakes.append({"event": ev["id"], "window": [round(a, 2), round(b, 2)], "model": part["model"],
                        "kept": better, "status_after": rep2["status"]})
        if better:
            final, report = candidate, rep2
    if not renderer.probe(final)["audio"] and music.available() is None:
        await progress(f"{name}: the video came back silent; adding a music bed")
        final, _ = await music.add_to(final, spec.get("look") or spec.get("logline") or "", "cinematic", False,
                                      work / "music", progress, name)
    cmp_path = side_by_side(ref, final, work / "comparison.mp4")
    continuity.dump(report, work / "validation.json")
    return {**r3d, "stage": "final", "final": final, "realize_error": None, "account_error": False, "comparison": cmp_path, "provider": {
        "model": res["model"], "label": res["label"], "fallback_used": res.get("fallback_used", False),
        "primary": providers.order()[0] if providers.order() else None,
        "attempts": attempts, "endpoint": providers.ENDPOINTS.get(res["model"])},
        "validation": {k: report[k] for k in ("status", "events", "issues", "similarity", "vision", "same_characters",
                                              "failed_events")} | {"output": report["output"]},
        "retakes": retakes, "prompt": prompt, "instruction": instruction}


# ------------------------------------------------------------------------------------------------ the step
async def run(app: dict, handoff: dict, work: Path, progress, assets: dict | None = None) -> dict:
    """The adapter's entry: the 3D reference, then (unless the user reviews it first) the realistic video."""
    req = handoff.get("request") or {}
    prompt = (handoff.get("prompt") or req.get("prompt") or "").strip()
    if not prompt:
        raise Render3DError("there is no story to stage")
    name = app.get("name") or NAME
    r3d = await reference(prompt, req, work, progress, name, assets)
    if req.get("review_first", False):
        await progress(f"{name}: 3D reference ready - review it, then press 'Make it realistic'")
        return {"path": r3d["reference"], "duration_s": r3d["probe"]["duration"], "title": r3d["spec"]["title"],
                "render3d": r3d}
    try:
        r3d = await realize(r3d, work / "realize", progress, name)
    except providers.TransformError as e:  # the 3D video is kept: "Make it realistic" retries only this part
        await progress(f"{name}: the realistic video could not be made ({e}); the 3D version is kept")
        r3d = {**r3d, "stage": "reference", "realize_error": str(e), "account_error": e.account,
               "provider": {"attempts": e.attempts, "model": None, "label": None, "failed": True}}
        return {"path": r3d["reference"], "duration_s": r3d["probe"]["duration"], "title": r3d["spec"]["title"],
                "render3d": r3d}
    return {"path": r3d["final"], "duration_s": renderer.probe(r3d["final"])["duration"], "title": r3d["spec"]["title"],
            "render3d": r3d}
