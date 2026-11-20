"""Execution adapter that runs the apps' engines inside Palette-Video (registered as adapter "engine")."""

import time
from pathlib import Path

from router_core import config, registry
from router_core.execution import RunDeleted

from . import effects_engine, explainroo_engine, genres, motion_engine, music, opuscar_engine, presenter_engine, render3d_engine, story_engine, vox_doc_engine
from .render3d import providers as r3d_providers, renderer as r3d_renderer, spec as r3d_spec

GENERATE_PREFIXES = ("story.", "video.", "fashion.")
# Edits this adapter really performs on existing footage (anything else fails honestly instead of a fake success).
EDIT_DONE = {"edit.assemble_clips", "edit.reformat_aspect", "edit.captions_subtitles"}
EDIT_NAMES = {"edit.trim_and_cut": "trim or cut", "edit.audio_mix": "mix audio without an uploaded audio track",
              "edit.transitions": "add transitions", "edit.enhance_quality": "enhance quality",
              "edit.color_grade": "colour-grade", "edit.explainer_effects": "add explainer effects"}


def _editable(out: dict) -> dict:
    """The clean (text-free) video and its editable text layer, when the engine made the words a layer."""
    if not out.get("text_layer") or not out.get("clean"):
        return {}
    return {"text_layer": out["text_layer"], "clean_path": out["clean"]}


async def _quiet(msg: str) -> None:
    """No progress listener: messages are dropped (the engines always await their progress callback)."""


class EngineAdapter:
    name = "engine"
    wants_context = True  # gets progress + the run (assets, perception) from run_plan

    async def launch(self, app, step, inputs, request_id, simulate, *, progress=None, run=None):
        caps = step.get("capabilities_used", [])
        work = config.STORAGE / "renders" / request_id
        prog = progress or _quiet
        try:
            if app["app_id"] == explainroo_engine.APP_ID:  # uploaded documents -> an explainroo explainer
                t0 = time.time()
                out = await explainroo_engine.run(app, step.get("handoff") or {"prompt": "", "request": {}},
                                                  explainroo_engine.documents_of(run or {}), work, prog)
                a = out["analysis"]
                return {"status": "succeeded", "mock": False,
                        "message": (f"{app['name']} made “{out['title'] or 'the explainer'}” from your document: "
                                    f"{len(a.get('outline') or [])} scenes, {len(a.get('facts') or [])} facts, the "
                                    f"{a.get('theme')} look, in {time.time() - t0:.0f} s. Verify: {out['verify'] or 'n/a'}."
                                    + (f" Check notes left: {len(out['check'])}." if out["check"] else "")),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "title": out["title"],
                                     "story": a.get("angle") or "", "genre": "Document explainer",
                                     "subtitles_path": out.get("subtitles"), "chapters_path": out.get("chapters"),
                                     "cover_path": out.get("cover"), "script": out.get("script") or ""}]}
            if app["app_id"] == vox_doc_engine.APP_ID:  # a VOX documentary: images + voice, animated in code
                t0 = time.time()
                out = await vox_doc_engine.run(app, step.get("handoff") or {"prompt": "", "request": {}}, work, prog)
                return {"status": "succeeded", "mock": False,
                        "message": (f"{app['name']} made “{out['title'] or 'the documentary'}”: "
                                    f"{out['duration_s'] / 60:.1f} min, {len(out['scenes'])} illustrated scenes in "
                                    f"{time.time() - t0:.0f} s"
                                    + (f", narrated ({', '.join(out['voice'])})" if out["voice"] else ", no narration")
                                    + f", {out['sfx']} paper sound effects, no music."
                                    + (f" {out['missing_images']} scene image(s) failed and show the empty paper."
                                       if out["missing_images"] else "")),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "title": out["title"],
                                     "story": out.get("visual_bible") or "", "genre": "VOX editorial documentary",
                                     "subtitles_path": out.get("subtitles"), "script": out.get("script") or ""}]}
            if app["app_id"] == opuscar_engine.APP_ID:  # a whole film drawn in code (Lemo-Opuscar's renderer)
                t0 = time.time()
                out = await opuscar_engine.run(app, step.get("handoff") or {"prompt": "", "request": {}}, work, prog)
                return {"status": "succeeded", "mock": False,
                        "message": (f"{app['name']} made “{out['title'] or 'the film'}” in the {out['style_name']} "
                                    f"look: {out['duration_s']:.1f} s in {time.time() - t0:.0f} s"
                                    + (f", narrated ({', '.join(out['voice'])})" if out["voice"] else ", no narration")
                                    + f", {out['sfx']} sound effects" + (", music" if out["music"] else "") + "."
                                    + (f" Notes left after the fixes: {len(out['checks'])}." if out["checks"] else "")),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "title": out["title"],
                                     "story": out.get("why_style") or "", "genre": f"Programmatic film ({out['style_name']})",
                                     "subtitles_path": out.get("subtitles"), "script": out.get("script") or ""}]}
            if app["app_id"] == render3d_engine.APP_ID:  # 3D Render: a 3D reference, then Kling 4.0 makes it real
                assets = {aid: {**a, "summary": ((run or {}).get("perception", {}).get(aid) or {}).get("summary", "")}
                          for aid, a in ((run or {}).get("assets") or {}).items()}
                t0 = time.time()
                out = await render3d_engine.run(app, step.get("handoff") or {"prompt": "", "request": {}}, work, prog, assets)
                return {"status": "succeeded", "mock": False, "message": render3d_message(app, out, time.time() - t0),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "title": out["title"],
                                     "story": out["render3d"]["spec"].get("logline") or "", "genre": "3D Render",
                                     "render3d": out["render3d"]}]}
            if app["app_id"] == "palette-effects-studio":
                videos = [i for i in inputs if i["format"] == "video_file"]
                if not videos or any(not i.get("path") for i in videos):
                    return _mock(app, step, request_id, f"{app['name']} would add effects here, but the video comes from a "
                                                        "simulated step.")
                t0 = time.time()
                out = await effects_engine.run(app, step.get("handoff") or {"prompt": "", "request": {}},
                                               videos[-1]["path"], work, prog)
                names = ", ".join(f"{e['fx']} {e['start']:.1f}-{e['end']:.1f}s" for e in out["plan"])
                return {"status": "succeeded", "mock": False,
                        "message": f"{app['name']} added {len(out['plan'])} effects ({names}) in {time.time() - t0:.0f} s"
                                   + (" after one revision." if out["revised"] else "."),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "effects": out["plan"]}]}
            if app["app_id"] == presenter_engine.APP_ID:
                assets = {aid: {**a, "summary": ((run or {}).get("perception", {}).get(aid) or {}).get("summary", "")}
                          for aid, a in ((run or {}).get("assets") or {}).items()}
                t0 = time.time()
                research = (((run or {}).get("story_intel") or {}).get("research")
                            or (run or {}).get("story_research"))  # the cinematic-skill research, when there was any
                out = await presenter_engine.run(app, {**(step.get("handoff") or {"prompt": "", "request": {}}),
                                                       "effects": "palette-effects-studio" not in _later_apps(run, step),
                                                       **({"_research": research} if research else {})},
                                                 assets, work, prog)
                return {"status": "succeeded", "mock": False,
                        "message": (f"{app['name']} made the {out['format_label']} “{out['title'] or 'video'}”: "
                                    f"{out['duration_s']:.1f} s, lip-synced in {out['chunks']} "
                                    f"part{'s' if out['chunks'] > 1 else ''}, in {time.time() - t0:.0f} s"
                                    + (" with a generated presenter" if out["generated_presenter"] else "")
                                    + f". Lip-sync: {', '.join(out['lipsync_models'])}; voice: {', '.join(out['voice_models'])}"
                                    + "." + _finish_note(out)),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "title": out["title"],
                                     "story": " ".join(out["script"])[:600] if out["script"] else "",
                                     "genre": out["format_label"], "presenter_url": out["presenter_url"],
                                     "script": out["script"], **_editable(out)}]}
            if app["app_id"] == "palette-motion-studio":
                base = None
                if "motion.overlay_graphics" in caps:
                    videos = [i for i in inputs if i["format"] == "video_file"]
                    if not videos or any(not i.get("path") for i in videos):
                        return _mock(app, step, request_id, f"{app['name']} would place graphics on footage here, but the "
                                                            "footage comes from a simulated step.")
                    base = videos[-1]["path"]
                assets = {aid: {**a, "summary": ((run or {}).get("perception", {}).get(aid) or {}).get("summary", "")}
                          for aid, a in ((run or {}).get("assets") or {}).items()}
                t0 = time.time()
                # Motion Studio renders locally (free): its length is not capped by the paid engines' test quality.
                out = await motion_engine.run(app, step.get("handoff") or {"prompt": "", "request": {}}, assets, base,
                                              work, prog, {**story_engine.quality(), "max_total": 60}, caps)
                fx_note = ""
                if not base:  # a programmatic animation has no sound: a music bed for its whole length
                    mprompt = (step.get("handoff") or {}).get("prompt") or ""
                    mgenre = "explainer" if "motion.data_visualization" in caps else genres.detect(mprompt)
                    out["path"], mp = await music.add_to(out["path"], mprompt, mgenre, False, work / "music", prog,
                                                         app["name"])
                    fx_note += f" Music: {mp.split(', instrumental only')[0][:90]}." if mp else ""
                # Motion Video gets effects too - unless an earlier step already added them (no double effects).
                if "palette-effects-studio" not in _later_apps(run, step) and not _earlier_did_effects(run, step):
                    prompt = (step.get("handoff") or {}).get("prompt") or ""
                    style = story_engine.EFFECT_STYLE.get(genres.detect(prompt))
                    if style and effects_engine.available() is None:
                        try:
                            fx = await effects_engine.run({**app, "name": f"{app['name']} effects"},
                                                          {"prompt": prompt, "effects_style": style, "allow_none": True},
                                                          out["path"], work / "effects", prog)
                            if not fx.get("skipped"):
                                out["path"] = fx["path"]
                                fx_note = f" Effects: {', '.join(e['fx'] for e in fx['plan'])}."
                        except Exception as e:  # effects are a finishing touch
                            fx_note = f" Effects skipped ({str(e)[:120]})."
                return {"status": "succeeded", "mock": False,
                        "message": (f"{app['name']} made “{out['title'] or 'the animation'}”"
                                    + (" from the tested chart template" if out.get("template") else "")
                                    + (" on top of the footage" if out["overlay"] else "")
                                    + f": {out['duration_s']:.1f} s at {out['size']} in {time.time() - t0:.0f} s"
                                    + (f" ({len(out['open_issues'])} layout notes left after repairs)."
                                       if out.get("open_issues") else ".") + fx_note),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "title": out["title"]}]}
            # A filming app makes the film when the step asks for footage, or when it gets no video to work on (e.g. a
            # full-3D motion-graphics plan routed only by its motion and finishing capabilities): an "edit" with
            # nothing to edit is never faked as a simulation. A step that receives a film still only edits it.
            gets_video = any(i["format"] == "video_file" for i in inputs)
            films = app["app_id"] in story_engine.ENGINE_APPS and not gets_video
            if any(c.startswith(GENERATE_PREFIXES) for c in caps) or films:
                if app["app_id"] not in story_engine.ENGINE_APPS:
                    return _mock(app, step, request_id, f"{app['name']} has no engine in VideoStudio yet.")
                assets = {aid: {**a, "summary": ((run or {}).get("perception", {}).get(aid) or {}).get("summary", "")}
                          for aid, a in ((run or {}).get("assets") or {}).items()}
                t0 = time.time()
                later = _later_apps(run, step)
                # Hybrid and single runs both get graphics and effects exactly once: a later Motion Studio step draws
                # the graphics and a later Effects Studio step adds the effects; otherwise the engine adds them itself.
                extra = {"_recipe": run["recipe"]} if (run or {}).get("recipe") else {}  # understood once, reused
                intel = (run or {}).get("story_intel") or {}
                if (intel.get("plan") or {}).get("scenes") and not extra:  # the approved (and edited) story
                    extra["_story"] = intel
                # One resume folder per run + step + plan revision (request_id is "<run>-<step>-<attempt>"): a Retry
                # can reuse what the failed attempt already made; an edited, re-approved plan starts fresh.
                extra["_resume"] = str(config.STORAGE / "renders" / (
                    f"{request_id.rsplit('-', 1)[0]}-r{(run or {}).get('plan_revision', 0)}-resume"))
                # Progress is always saved there, but only a Retry of this step (the user's click) reads it back;
                # any other start (a new approval, another app) begins from the beginning.
                retry = (run or {}).get("retry_steps") or []
                extra["_resume_load"] = step["step_id"] in retry
                if step["step_id"] in retry:
                    retry.remove(step["step_id"])
                out = await story_engine.run(app, {**(step.get("handoff") or {"prompt": "", "request": {}}),
                                                   "graphics": "palette-motion-studio" not in later,
                                                   "effects": "palette-effects-studio" not in later, **extra},
                                             assets, work, prog)
                q = story_engine.quality()
                note = (f" (test quality: {q['video_model']} {q['resolution']}, {len(out['scenes'])} scenes, "
                        f"{out['plan']['total']} of {out['plan']['requested_total']} s)" if q is story_engine.QUALITY["test"] else "")
                return {"status": "succeeded", "mock": False,
                        "message": f"{app['name']} made “{out['plan']['title'] or 'the film'}”: {len(out['scenes'])} scenes, "
                                   f"{out['duration_s']:.1f} s in {time.time() - t0:.0f} s{note}."
                                   + (f" Story method: {out['plan']['method']['label']}." if out["plan"].get("method") else "")
                                   + (f" Music: {out['plan']['music_prompt'].split(', instrumental only')[0][:90]}."
                                      if out["plan"].get("music_added") else "")
                                   + (f" Archive photos: {out['credits'].count(chr(10) + '- ')} from Wikimedia Commons "
                                      "(credits attached)." if out.get("credits") else "")
                                   + _finish_note(out),
                        "outputs": [{"format": "video_file", "ref": f"file://{out['path']}", "path": out["path"],
                                     "duration_s": out["duration_s"], "scenes": out["scenes"],
                                     "spans": out.get("spans"), "aspect_ratio": out.get("aspect_ratio"),
                                     "title": out["plan"]["title"], "story": out["plan"]["story"],
                                     "genre": out["plan"].get("genre_label"), "review": out["plan"].get("review"),
                                     "invented_specs": (out.get("graphics") or {}).get("invented_specs") or [],
                                     "graphics_error": (out.get("graphics") or {}).get("error"),
                                     "graphics_note": (("No graphics: " if (out.get("graphics") or {}).get("skipped")
                                                        else "Graphics added: ") + (out.get("graphics") or {}).get("why", ""))
                                                       if (out.get("graphics") or {}).get("why") else "",
                                     "credits": out.get("credits") or "",
                                     **_editable(out)}]}
            # Editing step (the user's own footage, or clips an earlier step made): only what really runs here.
            videos = [i for i in inputs if i["format"] == "video_file"]
            if not videos or any(not i.get("path") for i in videos):
                return _mock(app, step, request_id, f"{app['name']} would edit here, but the clips it gets come from "
                                                    "simulated steps, so there is nothing real to edit.")
            audio = [i for i in inputs if i["format"] == "audio_file" and i.get("path")]
            missing = sorted(c for c in caps if c.startswith("edit.") and c not in EDIT_DONE
                             and not (c == "edit.audio_mix" and audio))
            if missing:  # never report an edit as done when it was not made
                names = ", ".join(EDIT_NAMES.get(c, c) for c in missing)
                return {"status": "failed", "mock": False,
                        "message": f"{app['name']} cannot {names} on existing footage inside VideoStudio yet. Nothing was "
                                   "changed. Ask for a new video made from the footage instead, or use an editor for this."}
            work.mkdir(parents=True, exist_ok=True)
            ar = ((step.get("handoff") or {}).get("request") or {}).get("aspect_ratio") or "16:9"
            done = []
            await prog(f"{app['name']}: {'joining ' + str(len(videos)) + ' clips' if len(videos) > 1 else 'fitting the video'} to {ar}")
            path = await story_engine.join([v["path"] for v in videos], work / "edit.mp4", ar, story_engine.quality()["resolution"])
            done.append(f"joined {len(videos)} clips" if len(videos) > 1 else f"fitted to {ar}")
            if "edit.audio_mix" in caps and audio:
                await prog(f"{app['name']}: mixing {audio[0].get('filename') or 'your audio'}")
                path = await music.mix(path, audio[0]["path"], True, work / "edit_audio.mp4")
                done.append("mixed in your audio")
            if "edit.captions_subtitles" in caps:
                why = effects_engine.available()
                if why:
                    return {"status": "failed", "mock": False, "message": f"{app['name']}: captions need Effects Studio, "
                                                                          f"which is not available here ({why})."}
                out = await effects_engine.run({**app, "name": f"{app['name']} captions"},
                                               {"prompt": (step.get("handoff") or {}).get("prompt") or "",
                                                "captions_only": True, "allow_none": True}, path, work / "captions", prog)
                path = out["path"]
                done.append("added captions")
            dur = story_engine._probe(path)
            return {"status": "succeeded", "mock": False, "message": f"{app['name']}: {', '.join(done)} ({dur:.1f} s).",
                    "outputs": [{"format": "video_file", "ref": f"file://{path}", "path": path, "duration_s": dur}]}
        except RunDeleted:
            raise
        except (story_engine.EngineError, motion_engine.MotionError, effects_engine.EffectsError,
                presenter_engine.PresenterError, explainroo_engine.ExplainerError, opuscar_engine.ProgrammaticError,
                vox_doc_engine.DocumentaryError, render3d_engine.Render3DError, r3d_renderer.RenderError,
                r3d_spec.SpecError, r3d_providers.TransformError) as e:
            return {"status": "failed", "mock": False, "message": f"{app['name']}: {e}"}
        except Exception as e:  # a provider error is a failure, never a success
            return {"status": "failed", "mock": False, "message": f"{app['name']} failed: {type(e).__name__}: {str(e)[:400]}"}


def render3d_message(app: dict, out: dict, seconds: float) -> str:
    """Short: what the user gets (the details stay in the backend record)."""
    r = out["render3d"]
    title = r["spec"].get("title") or "the video"
    if r.get("realize_error"):
        return (f"{app['name']}: the 3D version of “{title}” is made, but the realistic video was not: "
                f"{r['realize_error']}. Then press “Make it realistic” (the 3D part is not redone).")
    if r.get("stage") == "reference":
        return f"{app['name']}: the 3D version of “{title}” is ready; press “Make it realistic” for the realistic video."
    pv = r.get("provider") or {}
    return f"{app['name']} made “{title}” ({pv.get('label') or 'realistic video'}) in {seconds / 60:.1f} min."


def _finish_note(out: dict) -> str:
    """What the finishing passes really added, so the user can see effects and motion design were applied."""
    fx = [e["fx"] for e in ((out.get("effects") or {}).get("plan") or [])]
    gp = out.get("graphics") or {}
    g = gp.get("graphics") or {}
    parts = []
    if fx:
        parts.append(f"Effects: {', '.join(fx)}")
    elif (out.get("effects") or {}).get("error"):
        parts.append(f"Effects failed ({str(out['effects']['error'])[:100]})")
    if g and not gp.get("skipped"):
        kinds = [o.get("kind") for o in g.get("overlays") or []]
        bits = (["hook"] if (g.get("hook") or {}).get("text") else []) + \
               [f"{kinds.count(k)} {k.replace('_', ' ')}" for k in dict.fromkeys(kinds)] + \
               (["end card"] if (g.get("end_card") or {}).get("title") else [])
        parts.append(f"Motion design: {', '.join(bits)}" if bits else "Motion design added")
    elif gp.get("error"):
        parts.append(f"Motion design failed ({str(gp['error'])[:100]})")
    return (" " + ". ".join(parts) + ".") if parts else ""


def _later_apps(run, step) -> set[str]:
    """App ids of the plan's steps after this one (empty for a single-app plan)."""
    steps = ((run or {}).get("plan") or {}).get("steps") or []
    ids = [s.get("step_id") for s in steps]
    return {s.get("app_id") for s in steps[ids.index(step["step_id"]) + 1:]} if step.get("step_id") in ids else set()


def _earlier_did_effects(run, step) -> bool:
    """Did a step before this one already add the finishing effects (a filming engine or Effects Studio)?"""
    steps = ((run or {}).get("plan") or {}).get("steps") or []
    ids = [s.get("step_id") for s in steps]
    if step.get("step_id") not in ids:
        return False
    done = {s.get("app_id") for s in steps[:ids.index(step["step_id"])]}
    return bool(done & (story_engine.ENGINE_APPS | {presenter_engine.APP_ID, "palette-effects-studio"}))


def _mock(app, step, request_id, why):
    return {"status": "succeeded", "mock": True, "message": f"MOCK: {why}",
            "outputs": [{"format": f, "ref": f"mock://{app['app_id']}/{request_id}/{f}"} for f in step.get("outputs", [])]}


def register():
    from router_core import execution
    execution.ADAPTERS["engine"] = EngineAdapter()
