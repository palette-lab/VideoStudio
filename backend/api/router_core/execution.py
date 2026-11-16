"""Execution adapters and the approved-plan runner. Only an adapter's own result can mark a step as succeeded."""

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import config, fallback, registry


class MockAdapter:
    """MOCK: no real application runs. Returns placeholder references so the workflow can be exercised end to end."""

    name = "mock"

    async def launch(self, app: dict, step: dict, inputs: list[dict], request_id: str, simulate: str | None) -> dict:
        await asyncio.sleep(config.MOCK_STEP_SECONDS)
        if simulate == "fail":
            return {"status": "failed", "message": f"{app['name']} reported an error (simulated failure).", "mock": True}
        outputs = [{"format": f, "ref": f"mock://{app['app_id']}/{request_id}/{f}"} for f in step.get("outputs", [])]
        if simulate == "bad_handoff":
            outputs = [{"format": "unexpected_format", "ref": f"mock://{app['app_id']}/{request_id}/broken"}]
        msg = (f"MOCK: the prompt for {app['name']} is ready, but {app['name']} can't be called by other apps yet "
               "(it has no broker service), so it was not launched." if step.get("handoff")
               else f"MOCK: {app['name']} was not actually launched.")
        return {"status": "succeeded", "outputs": outputs, "mock": True, "message": msg}


class BrokerAdapter:
    """Palette OS broker (palette.broker.call / os-broker dispatch). Disabled until an app's service contract is verified."""

    name = "broker"

    async def launch(self, app, step, inputs, request_id, simulate):
        target = app.get("execution", {}).get("service_target")
        if not app.get("verified") or not target:
            return {"status": "failed", "mock": False,
                    "message": f"{app['name']} has no verified Palette broker service yet, so it cannot be launched."}
        return {"status": "failed", "mock": False,
                "message": "Server-side broker dispatch is not implemented: the documented call is palette.broker.call from the frontend."}


async def _probe_duration(path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


class OpenMontageAdapter:
    """REAL editing through OpenMontage's FFmpeg tools (repos/OpenMontage, AGPL-3.0, used unmodified as a separate
    process via openmontage_runner.py). Connected so far: joining clips, crossfade transitions, music under the video.
    A step is only reported as real when ffprobe confirms the output video exists."""

    name = "openmontage"
    DOES = {"edit.assemble_clips", "edit.transitions", "edit.audio_mix"}
    RUNNER = Path(__file__).with_name("openmontage_runner.py")
    TIMEOUT_S = 900

    def _mock(self, app, step, request_id, why):
        return {"status": "succeeded", "mock": True, "message": f"MOCK: {why}",
                "outputs": [{"format": f, "ref": f"mock://{app['app_id']}/{request_id}/{f}"} for f in step.get("outputs", [])]}

    async def _tool(self, module: str, cls: str, inputs: dict, work: Path) -> dict:
        args = work / f"{cls}-{int(time.time() * 1000)}.json"
        args.write_text(json.dumps(inputs))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(self.RUNNER), str(config.OPENMONTAGE_DIR), module, cls, str(args),
            cwd=str(work), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "error": f"{cls} took longer than {self.TIMEOUT_S}s."}
        lines = [l for l in out.decode(errors="replace").splitlines() if l.strip().startswith("{")]
        if not lines:
            return {"success": False, "error": f"{cls} gave no result. {err.decode(errors='replace')[-400:]}"}
        return json.loads(lines[-1])

    async def launch(self, app, step, inputs, request_id, simulate):
        if not (config.OPENMONTAGE_DIR / "tools" / "video" / "video_stitch.py").exists() or not shutil.which("ffmpeg"):
            return self._mock(app, step, request_id, f"{app['name']} needs OpenMontage and ffmpeg, which are not "
                              "installed where this app runs, so this step was simulated.")
        caps = set(step.get("capabilities_used", []))
        if caps - self.DOES:
            todo = ", ".join(sorted(c.split(".", 1)[1].replace("_", " ") for c in caps - self.DOES))
            return self._mock(app, step, request_id, f"{app['name']} is connected to OpenMontage for joining, transitions "
                              f"and music only; {todo} is not connected yet, so this step was not run.")
        videos = [i for i in inputs if i["format"] == "video_file"]
        if not videos or any(not i.get("path") for i in videos):
            return self._mock(app, step, request_id, f"{app['name']} is connected (OpenMontage), but the clips it would "
                              "edit come from simulated steps, so there was nothing real to edit.")
        music = next((i["path"] for i in inputs if i["format"] == "audio_file" and i.get("path")), None)
        if "edit.audio_mix" in caps and not music:
            return {"status": "failed", "mock": False,
                    "message": "Music was requested, but no music file was provided. Upload an audio track and retry."}
        work = config.STORAGE / "renders" / request_id
        work.mkdir(parents=True, exist_ok=True)
        current, done = videos[0]["path"], []
        if len(videos) > 1:
            fade = "edit.transitions" in caps
            r = await self._tool("tools.video.video_stitch", "VideoStitch", {
                "operation": "stitch", "clips": [v["path"] for v in videos], "output_path": str(work / "joined.mp4"),
                "transition": "crossfade" if fade else "cut", "transition_duration": 0.5, "auto_normalize": True}, work)
            if not r.get("success"):
                return {"status": "failed", "mock": False, "message": f"Joining clips failed: {r.get('error')}"}
            current = str(work / "joined.mp4")
            done.append(f"joined {len(videos)} clips" + (" with crossfades" if fade else ""))
        if music:
            dur = await _probe_duration(current)
            r = await self._tool("tools.audio.audio_mixer", "AudioMixer", {
                "operation": "segmented_music", "video_path": current, "music_path": music, "music_volume": 0.6,
                "segments": [{"start": 0, "end": dur}], "fade_duration": 1.0,
                "output_path": str(work / "with_music.mp4")}, work)
            if not r.get("success"):
                return {"status": "failed", "mock": False, "message": f"Adding music failed: {r.get('error')}"}
            current = str(work / "with_music.mp4")
            done.append("added the music")
        final = work / "final.mp4"
        Path(current).replace(final) if Path(current) != final else None
        dur = await _probe_duration(str(final))
        if dur <= 0:
            return {"status": "failed", "mock": False, "message": "OpenMontage reported success, but the output video is unreadable."}
        return {"status": "succeeded", "mock": False,
                "message": f"Done with OpenMontage: {', '.join(done) or 'passed the clip through'} ({dur:.1f}s).",
                "outputs": [{"format": "video_file", "ref": f"file://{final}", "path": str(final), "duration_s": round(dur, 2)}]}


ADAPTERS = {"mock": MockAdapter(), "broker": BrokerAdapter(), "openmontage": OpenMontageAdapter()}


async def _save(save, run):
    """`save` may be a plain function or a coroutine function (the Palette backend persists to its database)."""
    out = save(run)
    if asyncio.iscoroutine(out):
        await out


def _downstream(steps, sid):
    hit, out = {sid}, []
    for s in steps:
        if any(i["source"].startswith("step:") and i["source"][5:] in hit for i in s.get("inputs", [])):
            hit.add(s["step_id"])
            out.append(s["step_id"])
    return out


class RunDeleted(Exception):
    """The user deleted the run while it was working: stop at the next save point and keep it deleted."""


class RunCancelled(Exception):
    """The user pressed Cancel: stop at the next save point, keep what was made, mark the run cancelled."""


async def run_plan(run: dict, save) -> None:
    steps = run["plan"]["steps"]
    apps = registry.apps()
    run["status"] = "executing"
    await _save(save, run)
    for step in steps:
        sid = step["step_id"]
        ex = run["execution"][sid]
        if ex["status"] == "succeeded":
            continue

        app = apps[step["app_id"]]
        # Resolve inputs and verify each handoff actually arrived in the declared format.
        inputs, handoff_error = [], None
        for inp in step.get("inputs", []):
            src = inp["source"]
            if src.startswith("step:"):
                produced = run["execution"][src[5:]].get("outputs", [])
                match = next((o for o in produced if o["format"] == inp["format"]), None)
                if not match:
                    handoff_error = (f"Handoff failed: step '{src[5:]}' did not deliver '{inp['format']}' "
                                     f"(it delivered {[o['format'] for o in produced] or 'nothing'}).")
                    break
                inputs.append({"source": src, **match, **({"use": inp["use"]} if inp.get("use") else {})})
            elif src.startswith("asset:"):
                a = run["assets"][src[6:]]
                inputs.append({"source": src, "format": inp["format"], "ref": f"upload://{a['id']}", "sha256": a["sha256"],
                               "path": a.get("path")})
            else:
                inputs.append({"source": "prompt", "format": "text", "ref": "prompt"})
        ex.update(status="running", started_at=time.time(), adapter=app["execution"]["adapter"], attempts=ex.get("attempts", 0) + 1)
        run["events"].append({"t": time.time(), "msg": f"Launching {app['name']} for step {sid}"})
        await _save(save, run)
        if handoff_error:
            result = {"status": "failed", "message": handoff_error, "mock": False}
        else:
            simulate = run.get("simulate", {}).get(sid) if ex["attempts"] == 1 else None
            adapter = ADAPTERS.get(app["execution"]["adapter"], ADAPTERS["mock"])
            request_id = f"{run['id']}-{sid}-{ex['attempts']}"

            save_lock = asyncio.Lock()  # an engine reports from parallel scenes; one database save at a time

            async def progress(msg, ex=ex):
                ex["message"] = msg
                run["events"].append({"t": time.time(), "msg": msg})
                async with save_lock:
                    await _save(save, run)

            async def live(data: dict, ex=ex):
                """What an engine has really made so far (scene stills and clips), shown while it works."""
                ex["live"] = {**(ex.get("live") or {}), **data}
                async with save_lock:
                    await _save(save, run)

            progress.live = live  # engines that can show partial results call progress.live({...})
            extra = {"progress": progress, "run": run} if getattr(adapter, "wants_context", False) else {}
            try:
                result = await adapter.launch(app, step, inputs, request_id, simulate, **extra)
            except RunDeleted:
                raise
            except RunCancelled:
                result = {"status": "cancelled", "mock": False,
                          "message": "Cancelled by you: nothing more was generated for this step."}
            except Exception as e:  # an adapter crash is a failure, never a success
                result = {"status": "failed", "message": f"Adapter error: {e}", "mock": False}
            ex["request_id"] = request_id
        ex.update(status=result["status"], message=result.get("message"), outputs=result.get("outputs", []),
                  mock=result.get("mock", False), finished_at=time.time(), inputs=inputs)
        if result["status"] == "cancelled":
            for d in _downstream(steps, sid):
                run["execution"][d]["status"] = "not_run"
            run["status"] = "cancelled"
            run["events"].append({"t": time.time(), "msg": "Cancelled"})
            break
        if result["status"] != "succeeded":
            for d in _downstream(steps, sid):
                run["execution"][d]["status"] = "not_run"
            ex["recovery"] = {"retry": True, "fallbacks": fallback.fallback_options(run, sid)}
            run["status"] = "failed"
            run["events"].append({"t": time.time(), "msg": f"Step {sid} failed: {result.get('message')}"})
            break
        run["events"].append({"t": time.time(), "msg": f"Step {sid} finished ({'MOCK' if ex['mock'] else 'confirmed by app'})"})
        await _save(save, run)
    if run["status"] not in ("failed", "cancelled"):
        run["status"] = "completed"
    run["report"] = build_report(run)
    await _save(save, run)


def build_report(run: dict) -> dict:
    apps = registry.apps()
    rows = []
    for step in run["plan"]["steps"]:
        ex = run["execution"][step["step_id"]]
        rows.append({"step_id": step["step_id"], "app": apps[step["app_id"]]["name"], "status": ex["status"],
                     "mock": ex.get("mock", False), "message": ex.get("message"), "outputs": ex.get("outputs", [])})
    any_mock = any(r["mock"] for r in rows)
    ok = all(r["status"] == "succeeded" for r in rows)
    if ok and any_mock:
        headline = "Workflow finished in MOCK mode: no real application was launched and no video was produced."
    elif ok:
        headline = "All applications confirmed success."
    else:
        headline = "The workflow did not finish. See the failed step and recovery options."
    return {"headline": headline, "steps": rows, "mock": any_mock, "success": ok and not any_mock,
            "fallbacks_used": run.get("fallback_history", [])}
