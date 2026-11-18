"""Palette-Video: Palette backend.

Mounted by Palette at /api/v1/plugins/palette-video/. Every route is permission-gated. Runs are stored in the app's
own org-scoped table, uploaded media in ctx.storage, and the slow work (perception + understanding, and plan
execution) runs as durable Palette jobs, never as in-process background tasks.
"""

import asyncio
import copy
import hashlib
import subprocess
import json
import logging
import mimetypes
import shutil
import time
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from palette_sdk import (
    JobExecution,
    PluginContext,
    PluginRouter,
    get_plugin_context,
    job,
    require_permission,
)

import os

from engines import adapter as engine_adapter
from engines import story_intel
from engines.story_intel import research as story_research
from engines import explainroo_engine
from engines import opuscar_engine, vox_doc_engine
from engines.story_intel import direct as story_direct
from models import Run
from router_core import config, execution, fallback, llm, perception, pipeline, registry, validator

engine_adapter.register()

log = logging.getLogger("palette_video")
router = PluginRouter(tags=["palette-video"])

READ = [require_permission("resources:read")]
WRITE = [require_permission("resources:write")]
MODES = {"auto", "normal", "hybrid", "analyze", "storyboard", "flow", "motion", "presenter", "motion3d", "explainer",
         "programmatic", "documentary"}
BUSY = {"analyzing", "approved", "executing"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
EXT_MODALITY = {".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".heic": "image",
                ".mp4": "video", ".mov": "video", ".webm": "video", ".mkv": "video", ".m4v": "video",
                ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio", ".ogg": "audio", ".flac": "audio",
                ".pdf": "document", ".txt": "document", ".md": "document", ".docx": "document"}
DOC_TYPES = ("application/pdf", "text/plain", "text/markdown",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def _media(assets: dict) -> dict:
    """The files the router and the apps work with: documents are source material for the story, not footage."""
    return {k: v for k, v in assets.items() if v.get("modality") != "document"}


# ---------------------------------------------------------------- runtime configuration (per request / job)

def _configure(ctx: PluginContext) -> None:
    config.configure(api_key=ctx.secret("PALETTE_API_KEY") or "", base_url=ctx.secret("PALETTE_LLM_BASE_URL"),
                     openmontage_dir=ctx.secret("OPENMONTAGE_DIR"), model=ctx.secret("PALETTE_LLM_MODEL") or "")
    for name in ("FAL_KEY", "PALETTE_VIDEO_QUALITY", "OPENMONTAGE_GUIDES_DIR", "RESEARCH_CONTACT",
                 "LLM_ROUTER_API_KEY", "LLM_ROUTER_BASE_URL"):  # the engines read these from the environment
        value = ctx.secret(name)
        if value:
            os.environ[name] = value


def _chat_for(ctx: PluginContext):
    """The model client the pipeline uses: the Palette LLM gateway when the app has its key, otherwise the
    platform's own models through ctx.llm (declared in platform_services)."""
    if config.LLM_API_KEY:
        return llm.chat_json

    async def ctx_chat(model, messages, **kw):
        reply = await ctx.llm.chat(messages, max_tokens=8000, temperature=0.2,
                                   response_format={"type": "json_object"})
        if reply.get("model") == "pltt-dev-stub":
            raise llm.LLMError("No model is available under pltt dev: add PALETTE_API_KEY to .env "
                               "(or OPENAI_API_KEY for ctx.llm), or use a hosted sandbox.")
        return llm._extract_json(reply.get("text") or "")

    return ctx_chat


# ---------------------------------------------------------------- run documents

def _new_run(prompt: str, mode: str, user_id: str, *, message: str | None = None, conversation_id: str | None = None) -> dict:
    rid = uuid.uuid4().hex[:12]
    return {"id": rid, "conversation_id": conversation_id or rid, "message": (message if message is not None else prompt).strip(),
            "created_at": time.time(), "user_id": user_id, "prompt": prompt.strip(),
            "mode": mode, "status": "draft", "stage": None, "assets": {}, "perception": {}, "understanding": None,
            "questions": [], "answers": {}, "plan": None, "plan_revision": 0, "issues": [], "review": None,
            "uncertainty": [], "plan_changes": [], "decision": None, "execution": {}, "report": None, "events": [],
            "error": None, "_unsaved": True}


async def _load(ctx: PluginContext, run_id: str) -> dict:
    row = await ctx.repo(Run).get(run_id)
    if row is None:
        raise HTTPException(404, "Run not found.")
    return json.loads(row.data)


async def _save(ctx: PluginContext, run: dict) -> None:
    """Create a new run once; afterwards only update it. A run deleted while a job works on it is never re-created:
    the job gets RunDeleted at its next save and stops."""
    new = run.pop("_unsaved", False)
    row = await ctx.repo(Run).get(run["id"])
    if row is not None and not run.get("cancel_requested") and '"cancel_requested": true' in (row.data or ""):
        # The user pressed Cancel while a job held its own copy of the run: keep the flag and stop that job here.
        run["cancel_requested"] = True
        if run.get("status") in BUSY:
            raise execution.RunCancelled(run["id"])
    data = json.dumps(run, default=str)
    if row is None:
        if not new:
            raise execution.RunDeleted(run["id"])
        await ctx.repo(Run).create(id=run["id"], user_id=str(run.get("user_id") or ""), status=run["status"],
                                   conversation_id=run.get("conversation_id") or run["id"],
                                   prompt=run["prompt"][:4000], data=data, created_at=run["created_at"],
                                   updated_at=time.time())
    else:
        await ctx.repo(Run).update(run["id"], status=run["status"], data=data, updated_at=time.time())


def _event(run: dict, msg: str) -> None:
    run["events"].append({"t": time.time(), "msg": msg})


def _public(run: dict) -> dict:
    out = json.loads(json.dumps(run, default=str))
    for a in out["assets"].values():
        a.pop("path", None)
        a.pop("storage", None)
    outputs = [o for ex in out.get("execution", {}).values() for o in ex.get("outputs", []) or []]
    outputs += [o for st in (out.get("report") or {}).get("steps") or [] for o in st.get("outputs") or []]
    for o in outputs:
        o.pop("path", None)
        o.pop("storage", None)
        o.pop("clean_path", None)
        o["has_clean"] = bool(o.pop("clean_storage", None))  # the text-free video, for the editable text layer
        o["version"] = len(o.pop("versions", None) or [])  # bumps when a scene is re-rendered
        for sc in o.get("scenes") or []:
            sc.pop("local_clip", None)
            sc["stored"] = sorted((sc.pop("storage", None) or {}).keys())  # which media the scene route serves
    for ex in out.get("execution", {}).values():
        for i in ex.get("inputs", []) or []:
            i.pop("path", None)
    _mark_finish(out)
    out.pop("usage_log", None)
    out.pop("model_calls", None)
    out.pop("option_results", None)
    for d in (out.get("documents") or {}).values():
        d["chars"] = len(d.pop("text", "") or "")
    out["apps"] = {k: {"name": v["name"], "verified": v["verified"], "adapter": v["execution"]["adapter"]}
                   for k, v in registry.apps().items()}
    return out


def _mark_finish(run: dict) -> None:
    """Show on each filming step which finish it gets: full effects + motion design, or a clean typographic finish."""
    from engines import genres, motion_design, presenter_engine, story_engine
    types = list((run.get("understanding") or {}).get("production_type") or [])
    plans = [run.get("plan")] + [o.get("plan") for o in run.get("options") or [] if isinstance(o, dict)]
    for plan in plans:
        for step in (plan or {}).get("steps") or []:
            aid = step.get("app_id")
            if aid != presenter_engine.APP_ID and aid not in story_engine.ENGINE_APPS:
                continue
            prompt = (step.get("handoff") or {}).get("prompt") or run.get("prompt") or ""
            level = motion_design.finish_level(genres.detect(prompt, types), prompt, types,
                                               person_talks=aid == presenter_engine.APP_ID or "talking_presenter" in types)
            if aid in story_engine.ENGINE_APPS and (run.get("story_intel") or {}).get("mode") == "cinematic_doc":
                level = "documentary"
            step["finish"] = {"level": level, "summary": motion_design.finish_summary(level)}


# ---------------------------------------------------------------- media

def _modality(f: UploadFile) -> str | None:
    ct = f.content_type or mimetypes.guess_type(f.filename or "")[0] or ""
    for m in ("image", "video", "audio"):
        if ct.startswith(m + "/"):
            return m
    if ct in DOC_TYPES:
        return "document"
    return EXT_MODALITY.get(Path(f.filename or "").suffix.lower())


async def _materialize(path_or_url: str, dest: Path) -> str:
    """A local file for ffmpeg / vision: local storage paths are used directly, hosted URLs are downloaded."""
    if path_or_url.startswith("file://"):
        return path_or_url[7:]
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", path_or_url) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in r.aiter_bytes():
                    fh.write(chunk)
    return str(dest)


async def _local_assets(run: dict) -> None:
    work = config.STORAGE / "runs" / run["id"] / "inputs"
    for aid, a in run["assets"].items():
        if a.get("path") and Path(a["path"]).exists():
            continue
        url = (a.get("storage") or {}).get("file_url")
        if url:
            a["path"] = await _materialize(url, work / f"{aid}{Path(a['filename']).suffix.lower()}")


# ---------------------------------------------------------------- jobs

@job("palette_video.analyze", timeout_seconds=1800, max_attempts=1)
async def analyze_job(ctx: PluginContext, execution_: JobExecution, payload: dict):
    _configure(ctx)
    run = await _load(ctx, payload["run_id"])
    run["job"] = {"id": execution_.id, "name": "palette_video.analyze", "started_at": time.time()}
    usage = run.setdefault("usage_log", [])
    token = llm.usage_log.set(usage)
    try:
        run["status"], run["stage"] = "analyzing", "perception"
        await _local_assets(run)
        work = config.STORAGE / "runs" / run["id"] / "work"
        work.mkdir(parents=True, exist_ok=True)
        for a in run["assets"].values():  # documents: their text is the story's source material
            if a.get("modality") == "document" and a["id"] not in run.setdefault("documents", {}):
                try:
                    text = await asyncio.to_thread(story_research.document_text, a["path"])
                    run["documents"][a["id"]] = {"filename": a["filename"], "text": text}
                    run["perception"][a["id"]] = {"summary": f"Document, {len(text)} characters: {text[:240]}",
                                                  "tags": ["text"]}
                except Exception as e:
                    run["perception"][a["id"]] = {"error": f"Could not read this document: {e}"}
        todo = [a for a in _media(run["assets"]).values() if a["id"] not in run["perception"]]
        if todo:
            _event(run, f"Looking at {len(todo)} file(s)")
            await _save(ctx, run)
            await execution_.progress(15, "Looking at your files")
            for a in todo:
                try:
                    r = await perception.perceive(a, work, deep=run["mode"] in ("analyze", "auto"))
                except Exception as e:  # perception is best effort; the request text still gets understood
                    r = {"error": f"Could not analyse this file: {e}"}
                run["perception"][a["id"]] = r
                a["tags"] = r.get("tags", [])
        explainer = explainroo_engine.wanted(run)
        if explainer:  # documents -> Document Explainer: it reads and analyses them itself, no story or enhancer
            _event(run, f"Document Explainer: {len(explainroo_engine.documents_of(run))} document(s) to explain")
        programmatic = not explainer and opuscar_engine.wanted(run)
        if programmatic:  # a film drawn in code: it writes its own script and look, no story or enhancer
            style = opuscar_engine.named_style(run["prompt"])
            _event(run, "Programmatic Film" + (f": the {opuscar_engine.STYLES[style]['name']} look" if style else ""))
        documentary = not explainer and not programmatic and vox_doc_engine.wanted(run)
        if documentary:  # the VOX documentary: it writes its own script from the playbook, no story or enhancer
            _event(run, "VOX Documentary (the Made by AI Playbook)")
        if run.get("parent_run") and not explainer and not programmatic and await _next_episode(ctx, run):
            _event(run, "Next episode written with the series' cast and story so far")
        if ("enhanced_prompt" not in run and not run.get("parent_run") and not explainer and not programmatic
                and not documentary):
            run["stage"] = "enhancing"
            _event(run, "Enhancing the prompt")
            await _save(ctx, run)
            await execution_.progress(35, "Enhancing the prompt")
            await _auto_enhance(ctx, run)
        documentary = documentary or run.get("route") == "documentary"  # Story Intelligence saw a documentary
        if documentary:
            ask = await _documentary_prepare(run)
            if ask:  # the playbook's STEP 1 (a topic) or STEP 2 (the length): the user answers in the chat
                run.update(status="needs_clarification", stage=None, questions=[ask])
                _event(run, "A question for you: " + ask["question"])
                run["usage"] = llm.usage_summary(run)
                llm.usage_log.reset(token)
                await _save(ctx, run)
                return {"run_id": run["id"], "status": run["status"]}
        if run.get("pending_topic"):  # 3D Motion Graphics Director, stage 1: the user picks one of five topics
            run.update(status="needs_clarification", stage=None, questions=[{
                "id": "topic", "question": TOPIC_Q, "why": "Your request has no single topic yet; pick one to develop.",
                "options": run["pending_topic"], "blocking": True, "affects": "content", "kind": "choice",
                "source": "ai"}])
            _event(run, "Five topic ideas: pick one")
            run["usage"] = llm.usage_summary(run)
            llm.usage_log.reset(token)
            await _save(ctx, run)
            return {"run_id": run["id"], "status": run["status"]}
        run["stage"] = "understanding"
        _event(run, "Understanding the request")
        await _save(ctx, run)
        await execution_.progress(50, "Understanding the request")
        planned = run.get("enhanced_prompt")
        if planned:  # the user's words first (their intent wins), the enhanced version as the detailed brief
            planned = (f"{run['prompt']}\n\nDetailed version of this request (written from the words above; follow "
                       f"it, but the words above win wherever they differ):\n{planned}")
        if explainer:
            res = _explainer_result(run)
        elif programmatic:
            res = _programmatic_result(run)
        elif documentary:
            res = _documentary_result(run)
        elif story_direct.applies(run):  # the film is already written and approved: no second reading
            res = _direct_result(run)
        else:
            res = await pipeline.analyze(prompt=planned or run["prompt"], mode=run["mode"],
                                         assets=_media(run["assets"]), perception=run["perception"],
                                         answers=run["answers"],
                                         proceed_with_defaults=bool(run.get("proceed_with_defaults")),
                                         settings=run.get("settings") or {}, chat=_chat_for(ctx))
        run.update({k: res.get(k) for k in ("understanding", "plan", "issues", "questions", "suggestion", "review",
                                            "uncertainty", "plan_changes", "routes", "decision", "judge",
                                            "open_details", "options", "option_results", "selected_option")})
        run["plan_mode"] = res.get("plan_mode") or run["mode"]
        run["unsupported"] = res.get("unsupported", [])
        run["blocking_reasons"] = res.get("blocking_reasons", [])
        if res["status"] == "awaiting_approval":
            run["plan_revision"] += 1
            run["execution"] = {s["step_id"]: {"status": "planned"} for s in res["plan"]["steps"]}
        run["status"], run["stage"] = res["status"], None
        _event(run, {"awaiting_approval": "Plan ready for your approval", "needs_clarification": "A question for you",
                     "not_possible": "No connected app can do this"}.get(run["status"], "Analysis finished"))
    except execution.RunDeleted:
        llm.usage_log.reset(token)
        return {"run_id": run["id"], "status": "deleted"}
    except execution.RunCancelled:
        run.update(status="cancelled", stage=None)
        _event(run, "Cancelled")
    except Exception as e:
        log.exception("analysis failed for %s", run["id"])
        run.update(status="error", stage=None, error=str(e))
    run["usage"] = llm.usage_summary(run)
    llm.usage_log.reset(token)
    try:
        await _save(ctx, run)
    except execution.RunDeleted:
        return {"run_id": run["id"], "status": "deleted"}
    return {"run_id": run["id"], "status": run["status"]}


def _explainer_result(run: dict) -> dict:
    """Uploaded documents -> one Document Explainer step (explainroo), or why it cannot be made."""
    if not explainroo_engine.documents_of(run):
        why = ("No readable document: upload a PDF, Word, TXT or Markdown file with text in it "
               "(a scanned PDF has no text to read).")
        _event(run, why)
        return {"understanding": None, "plan": None, "issues": [], "questions": [], "plan_mode": run["mode"],
                "status": "not_possible", "blocking_reasons": [why]}
    missing = explainroo_engine.available()
    d = explainroo_engine.build(run)
    issues = validator.validate(d["plan"], d["understanding"], run["mode"], _media(run["assets"]))
    blocking = [i["message"] for i in validator.errors(issues)] + ([f"explainroo cannot run here: {missing}"]
                                                                    if missing else [])
    _event(run, "Plan: the Document Explainer explains your document" if not blocking
           else "The Document Explainer cannot make this video")
    return {"understanding": d["understanding"], "plan": d["plan"], "issues": issues, "questions": [],
            "plan_mode": run["mode"], "status": "not_possible" if blocking else "awaiting_approval",
            "blocking_reasons": blocking}


def _programmatic_result(run: dict) -> dict:
    """A film drawn in code: one Programmatic Film step (Lemo-Opuscar's renderer), or why it cannot be made here."""
    missing = opuscar_engine.available()
    d = opuscar_engine.build(run)
    issues = validator.validate(d["plan"], d["understanding"], run["mode"], _media(run["assets"]))
    blocking = [i["message"] for i in validator.errors(issues)] + ([f"the Programmatic Film cannot run here: {missing}"]
                                                                    if missing else [])
    _event(run, "Plan: a Programmatic Film drawn in code" if not blocking
           else "The Programmatic Film cannot be made here")
    return {"understanding": d["understanding"], "plan": d["plan"], "issues": issues, "questions": [],
            "plan_mode": run["mode"], "status": "not_possible" if blocking else "awaiting_approval",
            "blocking_reasons": blocking}


async def _documentary_prepare(run: dict) -> dict | None:
    """The Made by AI Playbook's first steps before the plan: a topic (50 ideas when there is none), a length (1, 5, 8,
    12 or 20 minutes; the composer's length snaps to the closest), then Opus writes the script for the approval.
    Returns the question to ask, or None when the script is written."""
    doc = run.setdefault("documentary", {})
    answers = run.get("answers") or {}
    topic = answers.get(vox_doc_engine.TOPIC_Q) or doc.get("topic")
    if not topic:
        if "ideas" not in doc:
            _event(run, "Documentary: is there a topic? (Prompt 1, step 1)")
            doc["ideas"] = await vox_doc_engine.topic_ideas(run["prompt"]) or []
        if doc["ideas"] and not run.get("proceed_with_defaults"):
            return {"id": "topic", "question": vox_doc_engine.TOPIC_Q, "options": doc["ideas"], "kind": "choice",
                    "why": "Your request has no single topic yet: pick one of these documentary ideas.",
                    "blocking": True, "affects": "content", "source": "ai"}
        topic = doc["ideas"][0] if doc["ideas"] else run["prompt"]
    doc["topic"] = topic
    minutes = (vox_doc_engine.minutes_of(answers.get(vox_doc_engine.LENGTH_Q))
               or vox_doc_engine.minutes_for((run.get("settings") or {}).get("duration_s"))
               or (1 if run.get("proceed_with_defaults") else None))
    if not minutes:
        return {"id": "length", "question": vox_doc_engine.LENGTH_Q, "options": vox_doc_engine.LENGTH_OPTIONS,
                "kind": "choice", "why": "The script is written to match the length (about 150 words a minute).",
                "blocking": True, "affects": "content", "source": "ai"}
    doc["minutes"] = minutes
    if not doc.get("script") or doc.get("script_for") != [topic, minutes]:
        _event(run, f"Documentary: Opus writes the {minutes}-minute script (Prompt 1)")
        doc["script"] = await vox_doc_engine.write_script(topic, minutes)
        doc["script_for"] = [topic, minutes]
        n = len(vox_doc_engine.sentences(vox_doc_engine.script_text(doc["script"])))
        _event(run, f"Script ready: “{doc['script'].get('title') or topic}”, {n} sentences = {n} illustrations")
    run.update(enhanced_prompt=vox_doc_engine.script_text(doc["script"]), agent="documentary",
               agent_label="VOX Documentary Director (Made by AI Playbook)", agent_why=f"{minutes} min: {topic}"[:200])
    return None


def _documentary_result(run: dict) -> dict:
    """The written documentary as one VOX Documentary step, or why it cannot be made here."""
    missing = vox_doc_engine.available()
    d = vox_doc_engine.build(run)
    issues = validator.validate(d["plan"], d["understanding"], run["mode"], _media(run["assets"]))
    blocking = [i["message"] for i in validator.errors(issues)] + ([f"the VOX Documentary cannot run here: {missing}"]
                                                                    if missing else [])
    _event(run, "Plan: a VOX documentary, animated in code" if not blocking
           else "The VOX Documentary cannot be made here")
    return {"understanding": d["understanding"], "plan": d["plan"], "issues": issues, "questions": [],
            "plan_mode": run["mode"], "status": "not_possible" if blocking else "awaiting_approval",
            "blocking_reasons": blocking}


def _direct_result(run: dict) -> dict:
    """The written film as an approvable plan; falls back to "not_possible" only if it fails validation."""
    d = story_direct.build(run)
    mode = run["mode"]
    issues = validator.validate(d["plan"], d["understanding"], mode, _media(run["assets"]))
    ok = not validator.errors(issues)
    _event(run, "Plan built from the approved story" if ok else "The approved story did not pass plan validation")
    return {"understanding": d["understanding"], "plan": d["plan"], "issues": issues, "questions": [],
            "plan_mode": mode, "status": "awaiting_approval" if ok else "not_possible",
            "blocking_reasons": [] if ok else [i["message"] for i in validator.errors(issues)]}


@job("palette_video.execute", timeout_seconds=7200, max_attempts=1)
async def execute_job(ctx: PluginContext, execution_: JobExecution, payload: dict):
    _configure(ctx)
    run = await _load(ctx, payload["run_id"])
    run["job"] = {"id": execution_.id, "name": "palette_video.execute", "started_at": time.time()}
    await _save(ctx, run)
    await _local_assets(run)
    await execution_.progress(5, "Running the plan")
    from engines import story_engine
    usage = run.setdefault("usage_log", [])  # the engines' LLM calls join the analysis calls
    paid = run.setdefault("model_calls", [])  # images, clips and voice lines generated on fal
    tokens = (llm.usage_log.set(usage), story_engine.fal_usage.set(paid))
    try:
        await execution.run_plan(run, lambda r: _save(ctx, r))
    except execution.RunDeleted:
        log.info("run %s was deleted while running; stopped", run["id"])
        return {"run_id": run["id"], "status": "deleted"}
    except execution.RunCancelled:  # cancelled between steps
        run["status"] = "cancelled"
        for ex in run["execution"].values():
            if ex.get("status") in ("running", "queued", "planned"):
                ex["status"] = "not_run"
        _event(run, "Cancelled")
        run["report"] = execution.build_report(run)
    llm.usage_log.reset(tokens[0])
    story_engine.fal_usage.reset(tokens[1])
    run["usage"] = llm.usage_summary(run)
    # Real outputs are kept in app storage: local render folders are scratch space.
    for sid, ex in run["execution"].items():
        for o in ex.get("outputs", []) or []:
            if o.get("path") and Path(o["path"]).exists() and not o.get("storage"):
                o["storage"] = await ctx.storage.upload_file_path(
                    o["path"], "video/mp4", key=f"renders/{run['id']}/{sid}.mp4")
            await _store_clean(ctx, run["id"], sid, o)
            await _store_scenes(ctx, run["id"], sid, o)
    await _save(ctx, run)
    return {"run_id": run["id"], "status": run["status"]}


async def _store_clean(ctx: PluginContext, run_id: str, sid: str, o: dict) -> None:
    """The text-free video under the editable text layer, kept so the words can be re-rendered after edits."""
    if o.get("clean_path") and Path(o["clean_path"]).exists() and not o.get("clean_storage"):
        try:
            o["clean_storage"] = await ctx.storage.upload_file_path(
                o["clean_path"], "video/mp4", key=f"renders/{run_id}/{sid}-clean.mp4")
        except Exception as e:
            log.warning("clean video of %s not stored: %s", run_id, e)


async def _clean_video(run: dict, o: dict) -> str:
    """A local copy of the clean video (the render folder, or app storage)."""
    if o.get("clean_path") and Path(o["clean_path"]).exists():
        return o["clean_path"]
    url = (o.get("clean_storage") or {}).get("file_url") or ""
    if not url:
        raise HTTPException(404, "This video has no clean (text-free) version.")
    return str(_local_file(url) or await _materialize(url, config.STORAGE / "renders" / run["id"] / "clean.mp4"))


@job("palette_video.text", timeout_seconds=3600, max_attempts=1)
async def text_job(ctx: PluginContext, execution_: JobExecution, payload: dict):
    """The user edited the words: render the text layer on the clean video again (no AI video is made)."""
    from engines import text_layer
    _configure(ctx)
    run = await _load(ctx, payload["run_id"])
    run["job"] = {"id": execution_.id, "name": "palette_video.text", "started_at": time.time()}
    sid = payload["step_id"]
    ex = run["execution"][sid]
    o = next(o for o in ex["outputs"] if o.get("text_layer"))

    async def progress(msg: str) -> None:
        ex["message"] = msg
        _event(run, msg)
        await _save(ctx, run)
    try:
        clean = await _clean_video(run, o)
        work = config.STORAGE / "renders" / f"{run['id']}-{sid}-text-{int(time.time())}"
        work.mkdir(parents=True, exist_ok=True)
        path = await text_layer.burn(clean, o["text_layer"], work, progress, "Text")
        version = len(o.setdefault("versions", [])) + 1
        o["versions"].append({"storage": o.get("storage"), "text": True, "at": time.time()})
        o["path"], o["ref"] = path, f"file://{path}"
        o["storage"] = await ctx.storage.upload_file_path(path, "video/mp4",
                                                          key=f"renders/{run['id']}/{sid}-v{version}.mp4")
        ex["message"] = f"Text updated ({len(o['text_layer']['items'])} texts); the video itself is unchanged."
        run["report"] = execution.build_report(run)
        _event(run, ex["message"])
    except execution.RunDeleted:
        return {"run_id": run["id"], "status": "deleted"}
    except Exception as e:
        log.exception("text render failed for %s", run["id"])
        ex["message"] = f"The text could not be rendered ({str(e)[:200]}); your edits are saved."
        _event(run, ex["message"])
    run["status"] = "completed"
    run.pop("rerender", None)
    await _save(ctx, run)
    return {"run_id": run["id"], "status": run["status"]}


async def _store_scenes(ctx: PluginContext, run_id: str, sid: str, o: dict) -> None:
    """Each scene's clip (the local render) and first frame (from the provider) into app storage, so the scene cards
    keep working after the provider's links expire. Best effort: a scene that cannot be stored keeps its link."""
    for n, sc in enumerate(o.get("scenes") or [], start=1):
        st = sc.setdefault("storage", {})
        v = int(sc.get("rerendered") or 0)
        try:
            if sc.get("local_clip") and Path(sc["local_clip"]).exists() and "clip" not in st:
                st["clip"] = await ctx.storage.upload_file_path(
                    sc["local_clip"], "video/mp4", key=f"renders/{run_id}/{sid}/scene{n}_v{v}.mp4")
            if sc.get("still_url") and "still" not in st:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                    r = await c.get(sc["still_url"])
                    r.raise_for_status()
                ext = ".png" if "png" in (r.headers.get("content-type") or "") else ".jpg"
                st["still"] = await ctx.storage.upload_file(f"scene{n}{ext}", r.content,
                                                           r.headers.get("content-type") or "image/png",
                                                           key=f"renders/{run_id}/{sid}/scene{n}_v{v}{ext}")
        except Exception as e:
            log.warning("scene %s of %s not stored: %s", n, run_id, e)


@job("palette_video.rerender", timeout_seconds=3600, max_attempts=1)
async def rerender_job(ctx: PluginContext, execution_: JobExecution, payload: dict):
    """A new take of one scene of a finished film, spliced in (story_engine.rerender_scene)."""
    from engines import story_engine
    _configure(ctx)
    run = await _load(ctx, payload["run_id"])
    run["job"] = {"id": execution_.id, "name": "palette_video.rerender", "started_at": time.time()}
    sid, n, how = payload["step_id"], int(payload["scene"]), payload.get("instruction") or ""
    ex = run["execution"][sid]
    o = next(o for o in ex["outputs"] if o.get("scenes"))
    usage = run.setdefault("usage_log", [])
    paid = run.setdefault("model_calls", [])
    tokens = (llm.usage_log.set(usage), story_engine.fal_usage.set(paid))

    async def progress(msg: str) -> None:
        ex["message"] = msg
        _event(run, msg)
        await _save(ctx, run)
    try:
        film = o.get("path") if o.get("path") and Path(o["path"]).exists() else None
        if not film:  # the render folder is gone: fetch the stored film
            url = (o.get("storage") or {}).get("file_url") or ""
            film = str(_local_file(url) or await _materialize(url, config.STORAGE / "renders" / run["id"] / "film.mp4"))
        work = config.STORAGE / "renders" / f"{run['id']}-{sid}-scene{n}-{int(time.time())}"
        res = await story_engine.rerender_scene({**o, "path": film}, n, how, work, progress)
        version = len(o.setdefault("versions", [])) + 1
        o["versions"].append({"storage": o.get("storage"), "scene": n, "at": time.time()})
        o["path"], o["ref"] = res["path"], f"file://{res['path']}"
        o["storage"] = await ctx.storage.upload_file_path(res["path"], "video/mp4",
                                                          key=f"renders/{run['id']}/{sid}-v{version}.mp4")
        o["scenes"][n - 1] = {**res["scene"], "storage": {}}
        await _store_scenes(ctx, run["id"], sid, o)
        ex["message"] = f"Scene {n} re-rendered" + (f" ({how[:80]})" if how else "") + "; the rest of the film is unchanged."
        run["status"] = "completed"
        run["report"] = execution.build_report(run)  # the chat reads the film and its scenes from the report
        _event(run, ex["message"])
    except execution.RunDeleted:
        return {"run_id": run["id"], "status": "deleted"}
    except execution.RunCancelled:
        run["status"] = "completed"  # the film as it was is still there
        ex["message"] = f"Scene {n} re-render cancelled; the film is unchanged."
        _event(run, ex["message"])
    except Exception as e:
        log.exception("re-render failed for %s", run["id"])
        run["status"] = "completed"
        ex["message"] = f"Scene {n} could not be re-rendered ({str(e)[:200]}); the film is unchanged."
        _event(run, ex["message"])
    finally:
        llm.usage_log.reset(tokens[0])
        story_engine.fal_usage.reset(tokens[1])
    run.pop("rerender", None)
    run["cancel_requested"] = False
    run["usage"] = llm.usage_summary(run)
    await _save(ctx, run)
    return {"run_id": run["id"], "status": run["status"]}


# ---------------------------------------------------------------- runs whose job died

_BUSY = ("analyzing", "approved", "executing")
_JOB_TIMEOUT = {"palette_video.analyze": 1800, "palette_video.execute": 7200, "palette_video.rerender": 3600,
                "palette_video.text": 3600}
_JOB_ENDED = ("completed", "failed", "cancelled", "succeeded", "timed_out", "dead")


async def _job_alive(ctx: PluginContext, run: dict) -> bool:
    """Whether the job working on a busy run can still finish it. Asks the platform (ctx.jobs) about the job the run
    recorded; a job the platform no longer knows (e.g. the local simulator restarted) or that already ended is dead.
    Without a job id (queued, or a run from before ids were kept) a run is dead only once it has been idle longer than
    any job may run."""
    job_ = run.get("job") or {}
    if job_.get("id"):
        try:
            state = await ctx.jobs.get(job_["id"])
        except HTTPException as e:
            if e.status_code == 404:
                return False
            state = None
        except Exception as e:  # jobs unavailable in this runtime: fall back to the idle rule
            log.info("job status unavailable for run %s: %s", run.get("id"), e)
            state = None
        if state is not None:
            return str(state.get("status") or "").lower() not in _JOB_ENDED
    last = max([run.get("created_at") or 0, job_.get("started_at") or 0] + [e.get("t", 0) for e in run.get("events") or []])
    limit = _JOB_TIMEOUT.get(job_.get("name") or ("palette_video.analyze" if run["status"] == "analyzing"
                                                    else "palette_video.execute"), 7200) + 600
    return time.time() - last < limit


async def _reap(ctx: PluginContext, run: dict) -> dict:
    """A busy run whose job is gone is marked failed (or error, while analysing) so the chat stops waiting and offers a retry."""
    if run.get("status") not in _BUSY or await _job_alive(ctx, run):
        return run
    why = "The job stopped without finishing (the server was probably restarted)."
    if run.get("rerender"):  # a scene re-render died: the finished film is still there, unchanged
        job_info = run.pop("rerender")
        run["status"] = "completed"
        _event(run, "Updating the text stopped without finishing; your edits are saved, press Save again."
               if job_info.get("text") else
               f"Re-rendering scene {job_info.get('scene')} stopped without finishing; the film is unchanged.")
        try:
            await _save(ctx, run)
        except execution.RunDeleted:
            pass
        return run
    if run["status"] == "analyzing":
        run.update(status="error", stage=None, error=f"{why} Send the request again.")
    else:
        for ex in run.get("execution", {}).values():
            if ex.get("status") in ("running", "queued", "planned", None):
                ex["status"] = "failed"
                ex["message"] = f"{why} Retry this step."
        if not any(ex.get("status") == "failed" for ex in run.get("execution", {}).values()):
            for st in (run.get("plan") or {}).get("steps", []):  # approved but no step had started
                run.setdefault("execution", {})[st["step_id"]] = {"status": "failed", "message": f"{why} Retry this step."}
        run["status"] = "failed"
    _event(run, why)
    try:
        await _save(ctx, run)
    except execution.RunDeleted:
        pass
    return run


async def _enqueue(ctx: PluginContext, name: str, run: dict, key: str) -> None:
    await ctx.jobs.enqueue(name, {"run_id": run["id"]}, idempotency_key=f"{name}:{run['id']}:{key}")


# ---------------------------------------------------------------- routes

ASPECTS = {"9:16", "16:9", "1:1", "4:5", "4:3", "3:4", "21:9"}


class NewRun(BaseModel):
    prompt: str = Field("", max_length=20000)
    mode: str = "auto"
    parent_run: str | None = None  # a follow-up in the same chat: earlier request and files stay as context
    # Picked in the composer (None = Auto: read from the prompt). A pick wins over what the prompt says.
    duration_s: float | None = Field(None, ge=3, le=600)
    aspect_ratio: str | None = None


def _settings(body: NewRun, parent: dict | None = None) -> dict:
    """The composer's length / format picks; a follow-up keeps the chat's earlier picks unless it changes them."""
    if body.aspect_ratio and body.aspect_ratio not in ASPECTS:
        raise HTTPException(400, f"aspect_ratio must be one of {sorted(ASPECTS)}")
    out = dict((parent or {}).get("settings") or {})
    if body.duration_s:
        out["duration_s"] = round(float(body.duration_s), 1)
    if body.aspect_ratio:
        out["aspect_ratio"] = body.aspect_ratio
    return out


class Clarification(BaseModel):
    answers: dict[str, str] = {}
    proceed_with_defaults: bool = False


class Rerun(BaseModel):
    mode: str


class Selection(BaseModel):
    option_id: str


class Approval(BaseModel):
    plan_revision: int
    accept_changes: bool = False


class Recovery(BaseModel):
    step_id: str
    app_id: str | None = None
    accept_changes: bool = False


@router.get("/health", dependencies=READ)
async def health(ctx: PluginContext = Depends(get_plugin_context)):
    _configure(ctx)
    return {"ok": True, "models": "palette-gateway" if config.LLM_API_KEY else "platform-ctx.llm",
            "real_editing": (config.OPENMONTAGE_DIR / "tools").exists() and bool(shutil.which("ffmpeg")),
            "adapters": {k: v["execution"]["adapter"] for k, v in registry.apps().items()}}


@router.get("/registry", dependencies=READ)
async def get_registry(ctx: PluginContext = Depends(get_plugin_context)):
    return {"apps": list(registry.apps().values()), "capabilities": registry.capabilities()}


@router.get("/conversations", dependencies=READ)
async def list_conversations(ctx: PluginContext = Depends(get_plugin_context)):
    """One entry per chat: its first message as the title, its latest status."""
    rows = await ctx.repo(Run).list(order_by="-created_at", limit=300)
    chats: dict[str, dict] = {}
    for r in rows:
        if r.status == "draft":
            continue
        cid = r.conversation_id or r.id
        data = json.loads(r.data)
        if r.status in _BUSY:
            data = await _reap(ctx, data)
        c = chats.setdefault(cid, {"id": cid, "status": data["status"], "updated_at": r.created_at, "turns": 0})
        c["turns"] += 1
        c["title"] = (data.get("message") or r.prompt)[:160]  # rows are newest first: ends on the first message
        if "video" not in c:  # the newest finished video in the chat, for the Download button in the history
            real = [st for st in ((data.get("report") or {}).get("steps") or [])
                    if not st.get("mock") and st.get("status") == "succeeded" and st.get("outputs")]
            if real:
                c["video"] = {"run_id": r.id, "step_id": real[-1]["step_id"]}
    return sorted(chats.values(), key=lambda c: -c["updated_at"])[:40]


@router.get("/conversations/{conversation_id}", dependencies=READ)
async def get_conversation(conversation_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    rows = await ctx.repo(Run).list(filters={"conversation_id": conversation_id}, order_by="created_at", limit=100)
    runs = [_public(await _reap(ctx, json.loads(r.data))) for r in rows if r.status != "draft"]
    if not runs:
        raise HTTPException(404, "Conversation not found.")
    return {"id": conversation_id, "runs": runs}


@router.delete("/conversations/{conversation_id}", dependencies=WRITE)
async def delete_conversation(conversation_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    rows = await ctx.repo(Run).list(filters={"conversation_id": conversation_id}, limit=100)
    for r in rows:  # running ones too: their jobs stop at the next save and nothing is re-created
        await ctx.repo(Run).delete(r.id)
    return {"deleted": conversation_id}


@router.post("/runs", dependencies=WRITE)
async def create_run(body: NewRun, ctx: PluginContext = Depends(get_plugin_context)):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    if not body.parent_run:
        run = _new_run(body.prompt, body.mode, str(ctx.user_id))
        run["settings"] = _settings(body)
        await _save(ctx, run)
        return _public(run)
    parent = await _load(ctx, body.parent_run)
    if parent["status"] in BUSY:
        raise HTTPException(409, "Wait until the current step has finished.")
    text = body.prompt.strip()
    combined = f"{parent['prompt']}\n\nFollow-up from the user (it changes or adds to the request above): {text}" \
        if text else parent["prompt"]
    run = _new_run(combined, body.mode, str(ctx.user_id), message=text,
                   conversation_id=parent.get("conversation_id") or parent["id"])
    run.update(parent_run=parent["id"], answers=dict(parent.get("answers", {})), settings=_settings(body, parent))
    if parent["status"] in ("awaiting_approval", "needs_clarification"):  # the follow-up replaces that plan
        parent["status"] = "superseded"
        _event(parent, f"Replaced by a follow-up ({run['id']})")
        await _save(ctx, parent)
    for aid, a in parent["assets"].items():  # same stored files, same sha256
        run["assets"][aid] = {k: v for k, v in a.items() if k not in ("path", "role", "use")} | {"tags": [], "inherited": True}
    await _save(ctx, run)
    return _public(run)


async def _enhance(ctx: PluginContext, prompt: str, duration_s, aspect_ratio, files: list[str],
                   recipe: dict | None = None) -> dict:
    """The AI picks the agent for this request (food, product, presenter, explainer, story) and enhances the prompt
    the way that agent needs it, with the user's Cinematic Video Prompt Enhancer as the base. One LLM call."""
    from engines import prompt_enhancer as pe
    msgs = pe.auto_messages(prompt, duration_s, aspect_ratio, files, recipe)
    if config.LLM_API_KEY:
        data = await llm.chat_json(config.REASONING_MODEL, msgs, max_tokens=8000, reasoning_effort="medium",
                                   stage="enhance")
    else:
        reply = await ctx.llm.chat(msgs, max_tokens=4000, temperature=0.7, response_format={"type": "json_object"})
        if reply.get("model") == "pltt-dev-stub":
            raise llm.LLMError("No model is available under pltt dev: add PALETTE_API_KEY to .env.")
        data = llm._extract_json(reply.get("text") or "")
    out = pe.parse_auto(data, prompt, recipe)
    if out["prompt"] == prompt.strip():
        raise llm.LLMError("the enhancer returned nothing")
    if out["agent"] == "motion":  # the Video Intelligence agent: style lock, techniques, capability-aware storyboard
        from engines import video_intelligence as vi
        msgs = vi.messages(prompt, duration_s, aspect_ratio, files, out["prompt"])
        try:
            if config.LLM_API_KEY:
                vdata = await llm.chat_json(config.REASONING_MODEL, msgs, max_tokens=16000, reasoning_effort="high",
                                            stage="enhance.video_intelligence")
            else:
                reply = await ctx.llm.chat(msgs, max_tokens=8000, temperature=0.5,
                                           response_format={"type": "json_object"})
                vdata = llm._extract_json(reply.get("text") or "")
            plan = vi.clean(vdata, out["prompt"])
            out.update(prompt=plan["prompt"], vi=plan)
        except (llm.LLMError, ValueError) as e:  # the first enhancement still stands
            log.warning("video intelligence agent failed: %s", e)
    return out


async def _recipe_for(prompt: str, run: dict | None = None) -> dict | None:
    """Food first: when the request is about making food, the food agent understands the recipe before the prompt
    is enhanced (reused later by the film engine, so it is understood once)."""
    from engines import food
    if not food.recipe.is_candidate(prompt, None):
        return None
    refs = [{"role": "upload", "filename": a.get("filename"),
             "summary": ((run or {}).get("perception", {}).get(aid) or {}).get("summary", "")}
            for aid, a in ((run or {}).get("assets") or {}).items()]
    try:
        rec = await food.recipe.understand(prompt, refs)
    except Exception as e:  # the general enhancer still runs
        log.warning("recipe understanding failed: %s", e)
        return None
    return rec if rec.get("is_cooking_process") and rec.get("workflow") else None


async def _auto_enhance(ctx: PluginContext, run: dict) -> None:
    """Before planning: the AI picks the agent and enhances a short idea into a detailed prompt for it (once per
    run). The user's own words stay in "prompt" and "message"; the planner reads "enhanced_prompt". Best effort:
    on failure the original is used."""
    from engines import prompt_enhancer as pe
    if "enhanced_prompt" in run or run.get("parent_run"):  # once; follow-ups build on the chat's earlier request
        return
    why = pe.should_auto_enhance(run["prompt"])
    if why:
        run["enhanced_prompt"] = None
        run["enhance_note"] = why
        return
    st = run.get("settings") or {}
    try:
        recipe = await _recipe_for(run["prompt"], run)
        if recipe:
            run["recipe"] = recipe
            _event(run, f"Food agent: {recipe['dish'] or 'the recipe'} ({len(recipe['workflow'])} stages)")
        elif config.LLM_API_KEY and await _story_intelligence(ctx, run):
            return
        out = await _enhance(ctx, run["prompt"], st.get("duration_s"), st.get("aspect_ratio"),
                             [a.get("filename") or "" for a in run["assets"].values()], recipe)
        run.update(enhanced_prompt=out["prompt"], agent=out["agent"], agent_label=out["label"],
                   agent_why=out["why"])
        if out.get("vi"):
            run["video_intelligence"] = {k: v for k, v in out["vi"].items() if k != "prompt"}
        _event(run, f"Prompt enhanced by the {out['label']}")
    except llm.LLMError as e:
        run["enhanced_prompt"] = None
        run["enhance_note"] = f"not enhanced ({str(e)[:120]})"


async def _story_intelligence(ctx: PluginContext, run: dict) -> bool:
    """Any topic -> classified, researched, written and directed scene by scene, validated (engines/story_intel).
    True when it produced the plan; False hands the request to the plain enhancer (motion / food / failure)."""
    st = run.get("settings") or {}

    async def progress(msg: str) -> None:
        _event(run, msg)
        await _save(ctx, run)

    files = _story_files(run)
    docs = [d for d in (run.get("documents") or {}).values() if d.get("text")]
    topic = (run.get("answers") or {}).get(TOPIC_Q)  # stage 1 answered: develop the chosen topic
    run.pop("pending_topic", None)
    try:
        intel = await story_intel.develop(_story_prompt(run), files, docs, st.get("duration_s"),
                                          st.get("aspect_ratio"), progress, force_motion=run["mode"] == "motion3d")
    except (llm.LLMError, ValueError, KeyError, TypeError) as e:
        log.warning("story intelligence failed for %s: %s", run["id"], e)
        _event(run, f"Story intelligence unavailable ({str(e)[:100]}); using the prompt enhancer")
        return False
    cls = intel.get("classification") or {}
    if intel.get("route") == "documentary":  # a documentary: the VOX Documentary writes its own script
        run.update(route="documentary", enhanced_prompt=None)  # asked once: the documentary writes its own script
        _event(run, "A documentary: the VOX Documentary (Made by AI Playbook) makes it")
        return True
    if intel.get("topic_ideas") and not topic:
        run["story_intel"] = intel
        run["pending_topic"] = intel["topic_ideas"]
        return True
    if not (intel.get("plan") or {}).get("scenes"):
        run["story_classification"] = cls
        if intel.get("research"):  # a presentation: Presenter Studio writes its script from these sourced facts
            run["story_research"] = intel["research"]
            n = len(intel["research"].get("facts") or [])
            _event(run, f"Research for the presenter's script: {n} sourced fact{'s' if n != 1 else ''}")
        return False
    run["story_intel"] = intel
    from engines import prompt_enhancer as pe
    if intel.get("mode") == "motion_graphics":
        agent, label = "motion", "3D Motion Graphics Director"
        if run["mode"] == "auto":  # full 3D = AI 3D clips from 3D keyframes: one app, not a graphics chain
            run["mode"] = "storyboard"
            _event(run, "Full 3D motion graphics: made by Storyboard & Video (3D keyframes -> AI clips, narration, music)")
        if not st.get("aspect_ratio"):
            run.setdefault("settings", {})["aspect_ratio"] = intel.get("aspect_ratio") or "9:16"
    else:
        agent = {"presenter": "presenter", "product": "product", "educational": "explainer"}.get(cls.get("category"), "story")
        label = pe.AGENTS[agent][0]
        if intel.get("mode") == "cinematic_doc":  # the cinematic-skill method wrote and directed it
            agent, label = "story", "Cinematic Documentary Director"
        if intel.get("mode") == "short_drama":  # script, cast, one lip-synced shot per spoken line
            agent, label = "drama", pe.AGENTS["drama"][0]
    run.update(enhanced_prompt=story_intel.as_brief(intel), agent=agent, agent_label=label,
               agent_why=f"{cls.get('category', '').replace('_', ' ')}: {cls.get('intent') or ''}"[:200])
    v = intel.get("validation") or {}
    _event(run, f"Story ready: {len(intel['plan']['scenes'])} scenes"
                + (f", review {v['score']}/100" if v.get("score") is not None else "")
                + (" (revised)" if v.get("revised") else ""))
    return True


async def _next_episode(ctx: PluginContext, run: dict) -> bool:
    """A follow-up asking for the next episode (or episode N) of a short-drama series: written by the Short Drama
    Director with the series' locked cast (and its drawn faces) and the story so far. False when it is not that."""
    from engines.story_intel import drama
    if "story_intel" in run or not config.LLM_API_KEY:
        return False
    asked = drama.wants_episode(run.get("message") or "")
    if asked is None:
        return False
    try:
        parent = await _load(ctx, run["parent_run"])
    except Exception:  # the earlier run is gone: an ordinary follow-up
        return False
    series = (parent.get("story_intel") or {}).get("series")
    if not series:
        return False
    n = drama.next_number(series, asked)
    st = run.get("settings") or {}

    async def progress(msg: str) -> None:
        _event(run, msg)
        await _save(ctx, run)

    docs = [d for d in (run.get("documents") or {}).values() if d.get("text")]
    try:
        intel = await story_intel.develop(_story_prompt(run), _story_files(run), docs, st.get("duration_s"),
                                          st.get("aspect_ratio"), progress, series=copy.deepcopy(series), episode=n)
    except (llm.LLMError, ValueError, KeyError, TypeError) as e:
        log.warning("episode %s failed for %s: %s", n, run["id"], e)
        _event(run, f"Episode {n} could not be written ({str(e)[:100]}); treating it as a normal follow-up")
        return False
    if not (intel.get("plan") or {}).get("scenes"):
        return False
    from engines import prompt_enhancer as pe
    run["story_intel"] = intel
    run.update(enhanced_prompt=story_intel.as_brief(intel), agent="drama", agent_label=pe.AGENTS["drama"][0],
               agent_why=f"episode {n} of {series.get('title') or 'the series'}"[:200])
    return True


TOPIC_Q = "Which idea do you want to develop?"


def _story_files(run: dict) -> list[dict]:
    return [{"filename": a.get("filename"), "modality": a.get("modality"), "role": a.get("role"),
             "summary": (run["perception"].get(aid) or {}).get("summary", "")}
            for aid, a in _media(run["assets"]).items()]


def _story_prompt(run: dict) -> str:
    """The user's words, or the topic they picked from the five ideas (stage 1)."""
    topic = (run.get("answers") or {}).get(TOPIC_Q)
    return f"{topic}\n\n(The user's request was: {run['prompt']})" if topic else run["prompt"]


class PalettePick(BaseModel):
    option: int


class SceneEdit(BaseModel):
    fields: dict = {}


class SceneRegen(BaseModel):
    instruction: str = ""


def _story_or_409(run: dict) -> dict:
    intel = run.get("story_intel") or {}
    if not (intel.get("plan") or {}).get("scenes"):
        raise HTTPException(404, "This run has no story plan.")
    if run["status"] not in ("awaiting_approval", "needs_clarification"):
        raise HTTPException(409, "Scenes can only be changed before the plan is approved.")
    return intel


@router.post("/runs/{run_id}/story/scenes/{n}", dependencies=WRITE)
async def edit_scene(run_id: str, n: int, body: SceneEdit, ctx: PluginContext = Depends(get_plugin_context)):
    """The user's own edit of one scene; the rest of the story stays as approved."""
    run = await _load(ctx, run_id)
    intel = _story_or_409(run)
    try:
        story_intel.apply_edits(intel, n, body.fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    run["enhanced_prompt"] = story_intel.as_brief(intel)
    _event(run, f"Scene {n} edited")
    await _save(ctx, run)
    return _public(run)


@router.post("/runs/{run_id}/cancel", dependencies=WRITE)
async def cancel_run(run_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    """Stop a run that is analysing or rendering. The job stops at its next save point (between steps, scenes and
    model calls); what was already made is kept. A run whose job is already gone is marked cancelled right away."""
    run = await _load(ctx, run_id)
    if run["status"] not in BUSY:
        raise HTTPException(409, "Only a run that is analysing or rendering can be cancelled.")
    run["cancel_requested"] = True
    _event(run, "Cancel requested")
    if not await _job_alive(ctx, run):
        run["status"] = "cancelled"
        for ex in (run.get("execution") or {}).values():
            if ex.get("status") in ("running", "queued", "planned"):
                ex["status"] = "not_run"
    await ctx.repo(Run).update(run["id"], status=run["status"], data=json.dumps(run, default=str),
                               updated_at=time.time())
    return _public(run)


class Rerender(BaseModel):
    instruction: str = Field("", max_length=1000)
    step_id: str | None = None


def _scene_output(run: dict, step_id: str | None) -> tuple[str, dict]:
    for sid, ex in (run.get("execution") or {}).items():
        if step_id and sid != step_id:
            continue
        o = next((o for o in ex.get("outputs") or [] if o.get("scenes")), None)
        if o:
            return sid, o
    raise HTTPException(404, "This run has no film with scenes.")


@router.post("/runs/{run_id}/scenes/{n}/rerender", dependencies=WRITE)
async def rerender_scene(run_id: str, n: int, body: Rerender, ctx: PluginContext = Depends(get_plugin_context)):
    """Make a new take of one scene of the finished film and splice it in; everything else stays."""
    run = await _load(ctx, run_id)
    if run["status"] != "completed":
        raise HTTPException(409, "Only a finished film can have a scene re-rendered.")
    sid, o = _scene_output(run, body.step_id)
    if not 1 <= n <= len(o["scenes"]) or len(o.get("spans") or []) != len(o["scenes"]):
        raise HTTPException(400, "This film has no timing for that scene (made before scenes could be re-rendered).")
    run.update(status="executing", rerender={"step_id": sid, "scene": n}, cancel_requested=False)
    run["execution"][sid]["message"] = f"Re-rendering scene {n}"
    _event(run, f"Re-rendering scene {n}" + (f": {body.instruction[:80]}" if body.instruction else ""))
    run.pop("job", None)
    await _save(ctx, run)
    await ctx.jobs.enqueue("palette_video.rerender", {"run_id": run_id, "step_id": sid, "scene": n,
                                                      "instruction": body.instruction},
                           idempotency_key=f"rerender:{run_id}:{sid}:{n}:{len(run['events'])}")
    return _public(run)


class TextEdit(BaseModel):
    step_id: str
    items: list[dict]


@router.post("/runs/{run_id}/text", dependencies=WRITE)
async def save_text(run_id: str, body: TextEdit, ctx: PluginContext = Depends(get_plugin_context)):
    """Save the user's edited words and render them onto the clean video again (seconds of local rendering)."""
    from engines import text_layer
    run = await _load(ctx, run_id)
    if run["status"] != "completed":
        raise HTTPException(409, "Wait until the video is finished.")
    ex = (run.get("execution") or {}).get(body.step_id) or {}
    o = next((o for o in ex.get("outputs") or [] if o.get("text_layer")), None)
    if not o:
        raise HTTPException(404, "This video has no editable text.")
    old = o["text_layer"]
    o["text_layer"] = text_layer.layer(body.items, old["width"], old["height"], old["duration"])
    run.update(status="executing", rerender={"step_id": body.step_id, "text": True}, cancel_requested=False)
    ex["message"] = "Updating the text"
    _event(run, f"Text edited: {len(o['text_layer']['items'])} texts")
    run.pop("job", None)
    await _save(ctx, run)
    await ctx.jobs.enqueue("palette_video.text", {"run_id": run_id, "step_id": body.step_id},
                           idempotency_key=f"text:{run_id}:{body.step_id}:{len(run['events'])}")
    return _public(run)


@router.get("/runs/{run_id}/clean/{step_id}", dependencies=READ)
async def clean_video(run_id: str, step_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    """The video without its text layer (to edit the words on, or to download for another editor)."""
    run = await _load(ctx, run_id)
    o = next((o for o in ((run.get("execution") or {}).get(step_id) or {}).get("outputs") or []
              if o.get("text_layer")), None)
    if not o:
        raise HTTPException(404, "This video has no clean version.")
    if o.get("clean_path") and Path(o["clean_path"]).is_file():
        return FileResponse(o["clean_path"], media_type="video/mp4", filename=f"{run_id}-{step_id}-clean.mp4")
    url = (o.get("clean_storage") or {}).get("file_url") or ""
    local = _local_file(url) if url else None
    if local:
        return FileResponse(local, media_type="video/mp4", filename=f"{run_id}-{step_id}-clean.mp4")
    if url and not url.startswith("file://"):
        return RedirectResponse(url)
    raise HTTPException(404, "This video has no clean version.")


@router.get("/runs/{run_id}/scenes/{step_id}/{n}/{kind}", dependencies=READ)
async def scene_media(run_id: str, step_id: str, n: int, kind: str, ctx: PluginContext = Depends(get_plugin_context)):
    """A scene's stored first frame ("still") or clip ("clip")."""
    if kind not in ("still", "clip"):
        raise HTTPException(404, "Unknown scene media.")
    run = await _load(ctx, run_id)
    _, o = _scene_output(run, step_id)
    if not 1 <= n <= len(o["scenes"]):
        raise HTTPException(404, "No such scene.")
    st = ((o["scenes"][n - 1].get("storage") or {}).get(kind) or {})
    url = st.get("file_url") or ""
    local = _local_file(url) if url else None
    if local:
        return FileResponse(local, media_type="video/mp4" if kind == "clip" else (
            "image/png" if local.suffix == ".png" else "image/jpeg"))
    if url and not url.startswith("file://"):
        return RedirectResponse(url)
    raise HTTPException(404, "This scene's media was not stored.")


@router.post("/runs/{run_id}/story/palette", dependencies=WRITE)
async def switch_palette(run_id: str, body: PalettePick, ctx: PluginContext = Depends(get_plugin_context)):
    """3D Motion Graphics Director, stage 3: lock another palette; the approved script stays word for word."""
    run = await _load(ctx, run_id)
    intel = _story_or_409(run)
    _configure(ctx)
    try:
        await story_intel.switch_palette(intel, body.option, _story_prompt(run), _story_files(run),
                                         (run.get("settings") or {}).get("aspect_ratio"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, f"The palette could not be applied: {e}")
    run["enhanced_prompt"] = story_intel.as_brief(intel)
    _event(run, f"Palette {body.option} locked")
    await _save(ctx, run)
    return _public(run)


@router.post("/runs/{run_id}/story/scenes/{n}/regenerate", dependencies=WRITE)
async def regenerate_scene(run_id: str, n: int, body: SceneRegen, ctx: PluginContext = Depends(get_plugin_context)):
    """A new version of one scene that keeps the bible and connects to its neighbours."""
    run = await _load(ctx, run_id)
    intel = _story_or_409(run)
    _configure(ctx)
    try:
        await story_intel.regenerate_scene(intel, n, body.instruction)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, f"The scene could not be regenerated: {e}")
    run["enhanced_prompt"] = story_intel.as_brief(intel)
    _event(run, f"Scene {n} regenerated" + (f": {body.instruction[:80]}" if body.instruction else ""))
    await _save(ctx, run)
    return _public(run)


@router.post("/runs/{run_id}/files", dependencies=WRITE)
async def upload_file(run_id: str, file: UploadFile = File(...), ctx: PluginContext = Depends(get_plugin_context)):
    run = await _load(ctx, run_id)
    if run["status"] not in ("draft", "needs_clarification"):
        raise HTTPException(409, "Files can only be added before analysis or when the router asks for them.")
    modality = _modality(file)
    if not modality:
        raise HTTPException(415, f"{file.filename}: only images, video, audio and documents (PDF, Word, TXT, MD) are supported.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{file.filename} is larger than 500 MB.")
    aid = f"a{len(run['assets']) + 1}"
    stored = await ctx.storage.upload_file(file.filename, data, file.content_type,
                                           key=f"runs/{run_id}/{aid}{Path(file.filename).suffix.lower()}")
    run["assets"][aid] = {"id": aid, "filename": file.filename, "modality": modality, "content_type": file.content_type,
                          "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "tags": [],
                          "storage": {"object_path": stored.get("object_path"), "file_url": stored.get("file_url")}}
    await _save(ctx, run)
    return {"id": aid, "filename": file.filename, "modality": modality}


@router.post("/runs/{run_id}/start", dependencies=WRITE)
async def start_run(run_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    run = await _load(ctx, run_id)
    if run["status"] != "draft":
        raise HTTPException(409, "This run has already started.")
    if not run["prompt"] and not run["assets"]:
        raise HTTPException(400, "Describe the video or add at least one image, video or audio file.")
    run.update(status="analyzing", stage="upload")
    _event(run, "Request received")
    run.pop("job", None)  # the previous job is over; the new one records itself when it starts
    await _save(ctx, run)
    await _enqueue(ctx, "palette_video.analyze", run, "1")
    return _public(run)


@router.get("/runs/{run_id}", dependencies=READ)
async def get_run(run_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    return _public(await _reap(ctx, await _load(ctx, run_id)))


@router.delete("/runs/{run_id}", dependencies=WRITE)
async def delete_run(run_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    await _load(ctx, run_id)  # 404 when missing; a running run may be deleted too (its job stops at the next save)
    await ctx.repo(Run).delete(run_id)
    return {"deleted": run_id}


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _local_file(url: str) -> Path | None:
    """The local file behind a stored file:// URL. Paths saved before the project folder was moved or renamed
    are found again under this project's own `.palette/` dev storage."""
    if not url.startswith("file://"):
        return None
    path = Path(url[7:])
    if path.exists():
        return path
    if "/.palette/" in url:
        moved = _PROJECT_ROOT / ".palette" / url.split("/.palette/", 1)[1]
        if moved.exists():
            return moved
    return None


@router.get("/runs/{run_id}/output/{step_id}", dependencies=READ)
async def get_output(run_id: str, step_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    """The real video a step produced (kept in app storage)."""
    run = await _load(ctx, run_id)
    ex = run.get("execution", {}).get(step_id) or {}
    out = next((o for o in ex.get("outputs", []) if o.get("storage")), None)
    url = ((out or {}).get("storage") or {}).get("file_url")
    local = _local_file(url) if url else None
    if local:
        return FileResponse(local, media_type="video/mp4", filename=f"{run_id}-{step_id}.mp4")
    if url and not url.startswith("file://"):
        return RedirectResponse(url)
    # The run is marked completed before its output is copied into app storage: in that gap, serve the render itself.
    render = next((o["path"] for o in ex.get("outputs", []) if o.get("format") == "video_file" and o.get("path")
                   and Path(o["path"]).is_file()), None)
    if render:
        return FileResponse(render, media_type="video/mp4", filename=f"{run_id}-{step_id}.mp4")
    raise HTTPException(404, "This step has no real video output.")


@router.get("/runs/{run_id}/files/{asset_id}", dependencies=READ)
async def get_file(run_id: str, asset_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    """A file the user uploaded, for the previews in the chat."""
    run = await _load(ctx, run_id)
    a = run["assets"].get(asset_id)
    if not a:
        raise HTTPException(404, "No such file in this run.")
    url = (a.get("storage") or {}).get("file_url")
    local = _local_file(url) if url else (Path(a["path"]) if a.get("path") and Path(a["path"]).exists() else None)
    if local:
        return FileResponse(local, media_type=a.get("content_type") or None, filename=a["filename"],
                            headers={"Cache-Control": "private, max-age=86400"})
    if url and not url.startswith("file://"):
        return RedirectResponse(url)
    raise HTTPException(404, "This file is no longer stored.")


@router.get("/runs/{run_id}/thumbnail/{step_id}", dependencies=READ)
async def get_thumbnail(run_id: str, step_id: str, ctx: PluginContext = Depends(get_plugin_context)):
    """A small JPEG frame of a step's video for the history cards (made once with ffmpeg, then cached)."""
    from engines.vendor.flow import video_merge
    run = await _load(ctx, run_id)
    ex = run.get("execution", {}).get(step_id) or {}
    out = next((o for o in ex.get("outputs", []) if o.get("storage")), None)
    url = ((out or {}).get("storage") or {}).get("file_url")
    if not url:
        raise HTTPException(404, "This step has no real video output.")
    thumb = config.STORAGE / "thumbs" / f"{run_id}-{step_id}.jpg"
    if not thumb.exists():
        ffmpeg = video_merge.find_ffmpeg()
        if not ffmpeg:
            raise HTTPException(404, "No thumbnail: ffmpeg is not installed.")
        local = _local_file(url)
        src = str(local) if local else (url[7:] if url.startswith("file://") else url)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        dur = await asyncio.to_thread(video_merge.probe_duration, ffmpeg, src)
        at = f"{min(1.5, max(0.0, dur * 0.3)):.2f}"  # past any fade-in from black
        proc = await asyncio.to_thread(subprocess.run, [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", at, "-i", src, "-frames:v", "1",
            "-vf", "scale=480:-2", "-q:v", "4", str(thumb)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not thumb.exists():
            raise HTTPException(404, "No thumbnail could be made for this video.")
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/runs/{run_id}/clarify", dependencies=WRITE)
async def clarify(run_id: str, body: Clarification, ctx: PluginContext = Depends(get_plugin_context)):
    run = await _load(ctx, run_id)
    if run["status"] != "needs_clarification":
        raise HTTPException(409, "This run is not waiting for answers.")
    for q in run["questions"]:
        a = (body.answers or {}).get(q["id"])
        if a:
            run["answers"][q["question"]] = a
    run["proceed_with_defaults"] = body.proceed_with_defaults
    run.update(questions=[], status="analyzing", stage="understanding")
    _event(run, "Answers received")
    run.pop("job", None)  # the previous job is over; the new one records itself when it starts
    await _save(ctx, run)
    await _enqueue(ctx, "palette_video.analyze", run, f"clarify-{len(run['events'])}")
    return _public(run)


@router.post("/runs/{run_id}/rerun", dependencies=WRITE)
async def rerun(run_id: str, body: Rerun, ctx: PluginContext = Depends(get_plugin_context)):
    """A new run with the same request, files and answers in another mode."""
    old = await _load(ctx, run_id)
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    if old["status"] in BUSY:
        raise HTTPException(409, "Wait until this run has finished.")
    label = {"analyze": "AI Analyze", "hybrid": "Hybrid", "normal": "Normal", "auto": "Auto", "storyboard": "Storyboard",
             "flow": "Flow Video", "motion": "Motion Video",
             "presenter": "Presenter", "motion3d": "3D Motion Video", "explainer": "Document Explainer",
             "programmatic": "Programmatic Film", "documentary": "Documentary"}[body.mode]
    run = _new_run(old["prompt"], body.mode, str(ctx.user_id), message=f"Think again in {label} mode.",
                   conversation_id=old.get("conversation_id") or old["id"])
    run.update(parent_run=run_id, answers=dict(old.get("answers", {})), settings=dict(old.get("settings") or {}),
               status="analyzing", stage="upload")
    for k in ("story_intel", "enhanced_prompt", "agent", "agent_label", "agent_why", "recipe", "documents",
              "video_intelligence"):  # keep the written story: a rerun re-plans it, it does not re-read a slogan
        if old.get(k) is not None:
            run[k] = copy.deepcopy(old[k])
    for aid, a in old["assets"].items():  # same stored files, same sha256
        run["assets"][aid] = {k: v for k, v in a.items() if k not in ("path", "role", "use")} | {"tags": [], "inherited": True}
    _event(run, f"Re-running in {body.mode} mode (from run {run_id})")
    run.pop("job", None)  # the previous job is over; the new one records itself when it starts
    await _save(ctx, run)
    await _enqueue(ctx, "palette_video.analyze", run, "1")
    return _public(run)


@router.post("/runs/{run_id}/select", dependencies=WRITE)
async def select_option(run_id: str, body: Selection, ctx: PluginContext = Depends(get_plugin_context)):
    """Auto mode: switch to another validated option. The plan revision changes, so approval is needed again."""
    run = await _load(ctx, run_id)
    if run["status"] != "awaiting_approval":
        raise HTTPException(409, "Options can only be changed before approval.")
    chosen = (run.get("option_results") or {}).get(body.option_id)
    if not chosen:
        raise HTTPException(404, "Unknown option.")
    for k in ("plan", "issues", "decision", "uncertainty", "plan_changes"):
        run[k] = chosen.get(k)
    run["plan_mode"] = chosen.get("plan_mode")
    run["selected_option"] = body.option_id
    run["plan_revision"] += 1
    run["execution"] = {s["step_id"]: {"status": "planned"} for s in run["plan"]["steps"]}
    _event(run, f"Option {body.option_id} selected")
    await _save(ctx, run)
    return _public(run)


@router.post("/runs/{run_id}/approve", dependencies=WRITE)
async def approve(run_id: str, body: Approval, ctx: PluginContext = Depends(get_plugin_context)):
    run = await _load(ctx, run_id)
    if run["status"] != "awaiting_approval":
        raise HTTPException(409, "Only a validated plan that is awaiting approval can be approved.")
    if body.plan_revision != run["plan_revision"]:
        raise HTTPException(409, "The plan changed since you reviewed it. Please review it again.")
    if validator.errors(validator.validate(run["plan"], run["understanding"], run.get("plan_mode") or run["mode"],
                                           _media(run["assets"]))):
        raise HTTPException(409, "The plan no longer passes validation.")
    if run.get("plan_changes") and not body.accept_changes:
        raise HTTPException(409, "This plan uses a replacement application that changes the result. "
                                 "Confirm the changes to continue.")
    run["approved_at"] = time.time()
    run["approved_snapshot"] = {"plan_revision": run["plan_revision"],
                                "assets": {a: v["sha256"] for a, v in run["assets"].items()}}
    run["status"] = "approved"  # set before enqueueing so a second approval is rejected
    for e in run["execution"].values():
        e["status"] = "queued"
    _event(run, f"Plan revision {run['plan_revision']} approved")
    run.pop("job", None)  # the previous job is over; the new one records itself when it starts
    await _save(ctx, run)
    await _enqueue(ctx, "palette_video.execute", run, f"rev{run['plan_revision']}")
    return _public(run)


@router.post("/runs/{run_id}/recover", dependencies=WRITE)
async def recover(run_id: str, body: Recovery, ctx: PluginContext = Depends(get_plugin_context)):
    run = await _load(ctx, run_id)
    if run["status"] != "failed":
        raise HTTPException(409, "Only a failed run can be recovered.")
    ex = run["execution"].get(body.step_id)
    if not ex or ex["status"] != "failed":
        raise HTTPException(400, "That step did not fail.")
    if body.app_id:
        options = {f["app_id"]: f for f in fallback.fallback_options(run, body.step_id)}
        opt = options.get(body.app_id)
        if not opt or opt["classification"] == "invalid":
            raise HTTPException(400, "That application cannot replace this step.")
        if opt["classification"] == "changes_result" and not body.accept_changes:
            raise HTTPException(409, "This replacement changes the result. Confirm the changes to continue.")
        old_app = next(s["app_id"] for s in run["plan"]["steps"] if s["step_id"] == body.step_id)
        issues = fallback.substitute(run, body.step_id, body.app_id)
        if validator.errors(issues):
            raise HTTPException(409, "The replacement does not pass validation.")
        run["issues"] = issues
        run.setdefault("fallback_history", []).append({"step_id": body.step_id, "from": old_app, "to": body.app_id,
                                                       "classification": opt["classification"],
                                                       "changes": opt.get("changes", []), "at": time.time()})
        _event(run, f"Step {body.step_id} switched to {registry.apps()[body.app_id]['name']}")
    for e in run["execution"].values():
        if e["status"] in ("failed", "not_run"):
            e["status"] = "queued"
    run["status"] = "approved"
    run.pop("job", None)  # the previous job is over; the new one records itself when it starts
    await _save(ctx, run)
    await _enqueue(ctx, "palette_video.execute", run, f"recover-{len(run['events'])}")
    return _public(run)
