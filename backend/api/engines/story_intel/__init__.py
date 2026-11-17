"""Universal Story Intelligence: any topic -> understood, researched, written, directed scene by scene, validated.

    classify()        InputUnderstandingAgent   category, intent, audience, tone, style, research depth, length
    do_research()     TopicResearchAgent        Wikipedia + uploaded documents -> facts with sources, timeline, angle
    architect()       StoryArchitect + CharacterContinuity + ScenePlanner + CinematicPromptDirector + AudioPlanner
                      + MotionDesignDirector    story, continuity bible, scenes (21 fields + A-L prompt), audio, effects
    validate()        SceneValidationAgent + FactVerificationAgent (+ an independent puzzle solver) -> one revision
    motion.*          3D Motion Graphics Director (education, finance, business, science, study, commercial
                      explainers): script beats, palette lock, keyframe + motion prompts, capabilities
    regenerate_scene / apply_edits / switch_palette   change one scene or the palette, keep the rest

The approved plan is handed to the film engine (story_engine), whose planner renders exactly these scenes.
Method credits: cinematic-skill (github.com/Moh4696/cinematic-skill, MIT, (c) 2026 exploraX) - story shapes,
research rules, narration lint. Its Skia renderer is not used: this app generates real AI video clips.
"""

import json
import logging
import time

from router_core import config, llm

from . import agents, motion, research as research_, validate as validate_

log = logging.getLogger("palette_video.story_intel")

PASS_SCORE = 80
BLOCKING = ("story", "facts", "puzzle")
SKIP_AGENTS = {"food", "presenter"}  # the recipe engine owns cooking films; Presenter Studio writes its own script


async def _json(msgs: list[dict], stage: str, tokens: int = 8000, effort: str | None = None) -> dict:
    return await llm.chat_json(config.REASONING_MODEL, msgs, max_tokens=tokens, reasoning_effort=effort, stage=stage)


def _files_note(files: list[dict]) -> str:
    if not files:
        return "none"
    return "; ".join(f"{f.get('filename')} ({f.get('role') or f.get('modality')}): {str(f.get('summary') or '')[:300]}"
                     for f in files[:8])


async def classify(prompt: str, files: list[dict], duration_s=None, aspect_ratio=None) -> dict:
    user = (f"REQUEST:\n{prompt.strip()}\n\nUPLOADED FILES: {_files_note(files)}\n"
            f"Length set in the app: {f'{duration_s:g} s' if duration_s else 'not set'}. "
            f"Format: {aspect_ratio or 'not set'}.")
    out = await _json([{"role": "system", "content": agents.CLASSIFY.replace("{CATEGORIES}", agents.categories_text())},
                       {"role": "user", "content": user}], "story.classify", 3000, "low")
    if out.get("category") not in agents.CATEGORIES:
        out["category"] = "other"
    if duration_s:  # the user's length wins
        out["duration_s"] = int(duration_s)
    try:
        out["duration_s"] = max(10, min(120, int(out.get("duration_s") or 45)))
    except (TypeError, ValueError):
        out["duration_s"] = 45
    return out


async def do_research(cls: dict, prompt: str, documents: list[dict]) -> dict | None:
    """Facts with sources for factual films and for uploaded documents; None for pure fiction."""
    if cls.get("research") in (None, "", "none") and not documents:
        return None
    # English Wikipedia (the host the manifest allows); the film itself is still written in the request's language.
    sources = await research_.gather(cls.get("research_queries") or [cls.get("subject") or prompt[:80]],
                                     "en") if cls.get("research") not in (None, "", "none") else []
    docs = [{"title": d["filename"], "url": "", "text": d["text"], "kind": "uploaded document"} for d in documents]
    pool = docs + sources
    if not pool:
        return {"summary": "", "facts": [], "sources": [], "timeline": [], "conflicts": [],
                "note": "no sources could be reached; facts are unverified and the film must avoid specific claims"}
    body = "\n\n".join(f"=== SOURCE: {p['title']} ({p['kind']}{', ' + p['url'] if p['url'] else ''}) ===\n{p['text']}"
                       for p in pool)
    out = await _json([{"role": "system", "content": agents.RESEARCH},
                       {"role": "user", "content": f"SUBJECT: {cls.get('subject')}\nREQUEST: {prompt}\n\n{body}"}],
                      "story.research", 6000, "medium")
    known = {p["title"]: p["url"] for p in pool}
    out["sources"] = [{"title": t, "url": u, "kind": next(p["kind"] for p in pool if p["title"] == t)}
                      for t, u in known.items()]
    return out


def _architect_user(prompt: str, cls: dict, res: dict | None, files: list[dict], aspect_ratio) -> str:
    parts = [f"REQUEST (the user's words win):\n{prompt.strip()}",
             f"UNDERSTANDING: {json.dumps({k: cls.get(k) for k in ('category', 'intent', 'subject', 'central_idea', 'audience', 'story_type', 'tone', 'visual_style', 'language', 'assumptions')}, ensure_ascii=False)}",
             f"LENGTH: {cls['duration_s']} seconds. FORMAT: {aspect_ratio or 'not set'}.",
             f"UPLOADED FILES: {_files_note(files)} (uploaded people and products are the real ones: keep their exact "
             "look; refer to them as 'the person / product from the uploaded photo')."]
    if res:
        parts.append("RESEARCH (the only facts you may state; cite them in fact_notes):\n"
                     + json.dumps({k: res.get(k) for k in ("summary", "angle", "timeline", "facts", "conflicts", "note")},
                                  ensure_ascii=False)[:14000])
    return "\n\n".join(parts)


async def architect(prompt: str, cls: dict, res: dict | None, files: list[dict], aspect_ratio,
                    feedback: list[dict] | None = None, previous: dict | None = None) -> dict:
    system = (agents.ARCHITECT.replace("{CATEGORY}", f"{cls['category']} - {agents.CATEGORIES[cls['category']]}")
              .replace("{SHAPE}", agents.SHAPES[cls["category"]]))
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": _architect_user(prompt, cls, res, files, aspect_ratio)}]
    if feedback and previous:
        msgs += [{"role": "assistant", "content": json.dumps(previous, ensure_ascii=False)[:60000]},
                 {"role": "user", "content": agents.REVISE.replace("{ISSUES}", "\n".join(
                     f"- {'scene ' + str(i['scene']) if i.get('scene') else 'film'} [{i.get('area')}]: "
                     f"{i.get('problem')} -> {i.get('fix')}" for i in feedback))}]
    plan = await _json(msgs, "story.architect" + (".revise" if feedback else ""), 32000)
    scenes = [s for s in plan.get("scenes") or [] if isinstance(s, dict)]
    for n, s in enumerate(scenes, start=1):
        s["n"] = n
    plan["scenes"] = scenes
    return plan


async def solve_puzzle(plan: dict) -> dict | None:
    pz = plan.get("puzzle") or {}
    if not (pz.get("clues") and pz.get("question")):
        return None
    user = json.dumps({"rules": pz.get("rules"), "clues": pz.get("clues"), "question": pz.get("question")},
                      ensure_ascii=False)
    try:
        return await _json([{"role": "system", "content": agents.SOLVER}, {"role": "user", "content": user}],
                           "story.puzzle_solver", 4000, "high")
    except llm.LLMError as e:
        log.warning("puzzle solver failed: %s", e)
        return None


async def review(prompt: str, cls: dict, plan: dict, res: dict | None) -> dict:
    """The rule checks plus the LLM review (with the independent solver's answer for puzzles)."""
    rules = validate_.lint(plan, cls.get("duration_s"), cls.get("category", ""))
    solver = await solve_puzzle(plan) if cls.get("category") == "puzzle_mystery" else None
    body = {"request": prompt, "category": cls.get("category"), "plan": plan,
            "research": {k: (res or {}).get(k) for k in ("facts", "timeline", "conflicts", "note")} if res else None,
            "independent_solver": solver}
    try:
        llm_review = await _json([{"role": "system", "content": agents.VALIDATOR},
                                  {"role": "user", "content": json.dumps(body, ensure_ascii=False)[:90000]}],
                                 "story.validate", 8000, "medium")
    except llm.LLMError as e:
        log.warning("story review failed: %s", e)
        llm_review = {"score": None, "pass": True, "issues": [], "error": str(e)[:200]}
    issues = [i for i in llm_review.get("issues") or [] if isinstance(i, dict) and i.get("problem")]
    try:
        score = int(llm_review.get("score")) if llm_review.get("score") is not None else None
    except (TypeError, ValueError):
        score = None
    blocking = [i for i in issues if i.get("area") in BLOCKING]
    for i in issues:
        i["blocking"] = i.get("area") in BLOCKING
    # Passed = nothing that makes the film wrong: no rule errors, no story / facts / puzzle problem, and a score that
    # is not low. Other notes (a tight clip, a camera idea) are shown but do not force a rewrite.
    passed = not rules and not blocking and (score is None or score >= PASS_SCORE - 10)
    return {"passed": passed, "score": score, "rules": rules, "issues": issues,
            "strengths": llm_review.get("strengths") or [], "solver": solver}


async def develop(prompt: str, files: list[dict] | None = None, documents: list[dict] | None = None,
                  duration_s=None, aspect_ratio=None, progress=None, force_motion: bool = False) -> dict:
    """The whole pipeline. Returns the story intelligence record kept on the run."""
    files, documents = files or [], documents or []
    say = progress or (lambda m: None)
    t0 = time.time()
    await _maybe(say, "Story: understanding the request")
    cls = await classify(prompt, files, duration_s, aspect_ratio)
    if force_motion:  # 3D Motion Video mode: any topic goes to the 3D Motion Graphics Director
        cls["production"] = "motion_graphics"
    intel = {"classification": cls, "version": 1}
    if cls.get("agent") in SKIP_AGENTS and not force_motion:
        intel["skipped"] = f"handled by the {cls['agent']} agent"
        return intel
    if cls.get("has_topic") is False:  # stage 1 (topic ideas) needs no research rabbit hole
        cls["research"] = "none"
    if cls.get("research") not in (None, "", "none") or documents:
        await _maybe(say, f"Story: researching {cls.get('subject') or 'the topic'}")
    res = await do_research(cls, prompt, documents)
    intel["research"] = res
    if is_motion(cls):
        return await _develop_motion(intel, prompt, cls, res, files, aspect_ratio, say, t0)
    await _maybe(say, f"Story: writing the {cls['category'].replace('_', ' ')} and directing each scene")
    plan = await architect(prompt, cls, res, files, aspect_ratio)
    await _maybe(say, "Story: validating the story, facts and scenes")
    rev = await review(prompt, cls, plan, res)
    first = rev
    if not rev["passed"]:
        await _maybe(say, f"Story: fixing {len(rev['rules']) + len(rev['issues'])} problem(s)")
        try:
            plan2 = await architect(prompt, cls, res, files, aspect_ratio, rev["rules"] + rev["issues"], plan)
            rev2 = await review(prompt, cls, plan2, res)
            if plan2.get("scenes") and _better(rev2, rev):
                plan, rev = plan2, {**rev2, "first_score": first["score"], "revised": True}
        except llm.LLMError as e:
            log.warning("story revision failed: %s", e)
    intel.update(plan=plan, validation=rev, seconds=round(time.time() - t0, 1))
    return intel


def is_motion(cls: dict) -> bool:
    return cls.get("production") == "motion_graphics" or cls.get("agent") == "motion"


def _apps() -> str:
    try:
        from ..video_intelligence import apps_summary
        return apps_summary()
    except Exception as e:  # the registry is context; the director still works without it
        log.warning("app registry unavailable: %s", e)
        return ""


async def _direct(prompt: str, cls: dict, res: dict | None, files: list[dict], aspect_ratio, palette: int | None = None,
                  keep_script: str | None = None, feedback: list[dict] | None = None, previous: dict | None = None,
                  keep_style: dict | None = None) -> dict:
    msgs = motion.messages(prompt, cls, res, _files_note(files), _apps(), aspect_ratio, palette, keep_script, keep_style)
    if feedback and previous:
        msgs += [{"role": "assistant", "content": json.dumps(previous, ensure_ascii=False)[:60000]},
                 {"role": "user", "content": agents.REVISE.replace("{ISSUES}", "\n".join(
                     f"- {'scene ' + str(i['scene']) if i.get('scene') else 'film'} [{i.get('area')}]: "
                     f"{i.get('problem')} -> {i.get('fix')}" for i in feedback))}]
    return await _json(msgs, "story.motion_director" + (".revise" if feedback else ""), 24000, "medium")


async def _motion_review(prompt: str, cls: dict, plan: dict, res: dict | None) -> dict:
    rev = await review(prompt, cls, plan, res)
    extra = motion.lint(plan, motion.wants_stylized(prompt))
    rev["rules"] = rev["rules"] + extra
    rev["passed"] = rev["passed"] and not extra
    return rev


async def _develop_motion(intel: dict, prompt: str, cls: dict, res: dict | None, files: list[dict], aspect_ratio, say,
                          t0: float) -> dict:
    """The 3D Motion Graphics Director's stages 1-6, validated like every other plan (one revision)."""
    intel["mode"] = "motion_graphics"
    intel["stylized_ok"] = motion.wants_stylized(prompt)  # the user asked for glass / chrome themselves
    aspect_ratio = aspect_ratio or "9:16"  # the master prompt: vertical 9:16 unless the user asks otherwise
    intel["aspect_ratio"] = aspect_ratio
    await _maybe(say, "Motion graphics director: script, palette, keyframes and motion")
    raw = await _direct(prompt, cls, res, files, aspect_ratio)
    if raw.get("needs_topic") or cls.get("has_topic") is False:
        ideas = [str(x).strip() for x in raw.get("topic_ideas") or [] if str(x).strip()][:5]
        if ideas:  # Stage 1: STOP - the user picks one of five titles
            intel.update(topic_ideas=ideas, seconds=round(time.time() - t0, 1))
            return intel
    plan = motion.to_plan(raw)
    await _maybe(say, "Motion graphics director: validating script, beats and prompts")
    rev = await _motion_review(prompt, cls, plan, res)
    if not rev["passed"]:
        await _maybe(say, f"Motion graphics director: fixing {len(rev['rules']) + len(rev['issues'])} problem(s)")
        try:
            raw2 = await _direct(prompt, cls, res, files, aspect_ratio, feedback=rev["rules"] + rev["issues"],
                                 previous=raw)
            plan2 = motion.to_plan(raw2)
            rev2 = await _motion_review(prompt, cls, plan2, res)
            if plan2.get("scenes") and _better(rev2, rev):
                raw, plan, rev = raw2, plan2, {**rev2, "first_score": rev["score"], "revised": True}
        except llm.LLMError as e:
            log.warning("motion revision failed: %s", e)
    intel.update(plan=plan, validation=rev, seconds=round(time.time() - t0, 1))
    return intel


async def switch_palette(intel: dict, option: int, prompt: str, files: list[dict] | None = None,
                         aspect_ratio=None) -> dict:
    """Stage 3 again: the user's palette, locked; the approved script stays word for word."""
    if intel.get("mode") != "motion_graphics":
        raise ValueError("palettes belong to motion graphics plans")
    style = motion.keep_style(intel.get("plan") or {})
    if option not in style["palettes"]:
        raise ValueError(f"choose palette {', '.join(str(k) for k in sorted(style['palettes']))}")
    cls = intel["classification"]
    keep = ((intel.get("plan") or {}).get("motion_graphics") or {}).get("script")
    raw = await _direct(prompt, cls, intel.get("research"), files or [], aspect_ratio, option, keep, keep_style=style)
    if not raw.get("palettes"):  # the director dropped them: this video's palettes and look stay as they were
        raw["palettes"] = [{"option": k, **v} for k, v in sorted(style["palettes"].items())]
    if not raw.get("art_direction"):
        raw["art_direction"] = style["art_direction"]
    plan = motion.to_plan(raw)
    if not plan["scenes"]:
        raise llm.LLMError("the director returned no scenes")
    intel["plan"] = plan
    _relint(intel)
    return plan


def _better(a: dict, b: dict) -> bool:
    bad = lambda r: len(r["rules"]) + 2 * len([i for i in r["issues"] if i.get("area") in ("story", "facts", "puzzle")])
    return (a["passed"] and not b["passed"]) or bad(a) < bad(b) or ((a["score"] or 0) > (b["score"] or 0) and bad(a) == bad(b))


async def _maybe(fn, msg):
    r = fn(msg)
    if hasattr(r, "__await__"):
        await r


async def regenerate_scene(intel: dict, n: int, instruction: str = "") -> dict:
    """A new version of scene n that keeps the bible, the style and its neighbours."""
    plan = intel["plan"]
    scenes = plan["scenes"]
    if not 1 <= n <= len(scenes):
        raise ValueError(f"there is no scene {n}")
    ctx = {"title": plan.get("title"), "story_concept": plan.get("story_concept"), "bible": plan.get("bible"),
           "style": plan.get("style"), "previous_scene": scenes[n - 2] if n > 1 else None,
           "scene_to_rewrite": scenes[n - 1], "next_scene": scenes[n] if n < len(scenes) else None,
           "instruction": instruction or "make this scene stronger: a clearer event, more specific action and prompt"}
    new = await _json([{"role": "system", "content": agents.SCENE_REGEN},
                       {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)[:40000]}],
                      "story.scene_regenerate", 8000)
    if not isinstance(new, dict) or not new.get("what_happens"):
        raise llm.LLMError("the scene came back empty")
    new["n"] = n
    scenes[n - 1] = new
    _relint(intel)
    return new


EDITABLE = {"title", "duration_s", "what_happens", "actions", "camera_movement", "framing", "lighting", "start_state",
            "end_state", "transition_in", "transition_out"}


def apply_edits(intel: dict, n: int, fields: dict) -> dict:
    """The user's own edits to one scene (text fields, the voice line, the prompt parts)."""
    scenes = intel["plan"]["scenes"]
    if not 1 <= n <= len(scenes):
        raise ValueError(f"there is no scene {n}")
    s = scenes[n - 1]
    for k, v in (fields or {}).items():
        if k in EDITABLE:
            s[k] = v
        elif k == "line":
            s.setdefault("audio", {})["line"] = v
        elif k.startswith("prompt.") and k[7:] in dict.fromkeys(("subject", "environment", "action", "interaction",
                                                                 "emotion", "camera", "lighting", "visual_design",
                                                                 "motion", "effects", "continuity", "ending")):
            s.setdefault("prompt", {})[k[7:]] = v
    s["edited"] = True
    _relint(intel)
    return s


def _relint(intel: dict) -> None:
    cls = intel.get("classification") or {}
    v = intel.setdefault("validation", {})
    v["rules"] = validate_.lint(intel["plan"], cls.get("duration_s"), cls.get("category", ""))
    if intel.get("mode") == "motion_graphics":
        v["rules"] += motion.lint(intel["plan"], bool(intel.get("stylized_ok")))
    v["edited_at"] = time.time()


def as_brief(intel: dict) -> str:
    """The plan as the detailed, scene-by-scene enhanced prompt (shown in the chat and read by the router)."""
    plan = intel.get("plan") or {}
    cls = intel.get("classification") or {}
    out = [f"{plan.get('title') or ''} - {plan.get('logline') or ''}".strip(" -"),
           f"Length: {cls.get('duration_s')} seconds. Style: {(plan.get('style') or {}).get('visual_style') or cls.get('visual_style')}."]
    if intel.get("mode") == "motion_graphics":
        # Said plainly, so the router plans ONE filming app: no programmatic charts, overlay graphics or text pass.
        out.append(f"Production: {len(plan.get('scenes') or [])} scenes, each ONE AI-generated photoreal 3D clip "
                   "animated from its own 3D keyframe image, with narration and music. Every on-screen word is part of "
                   "the 3D keyframe image itself. No separate graphics, charts, overlays or text animation pass. "
                   f"Format: {intel.get('aspect_ratio') or '9:16'} vertical.")
    if plan.get("story_concept"):
        out.append(plan["story_concept"])
    for s in plan.get("scenes") or []:
        a = s.get("audio") if isinstance(s.get("audio"), dict) else {}
        voice = (f"\n{a.get('speaker') or 'Narrator'} ({a.get('type')}): \"{a['line']}\"" if a.get("line")
                 and a.get("type") != "none" else "")
        out.append(f"SCENE {s.get('n')} - {s.get('title')} ({s.get('duration_s')} s): {s.get('what_happens')}"
                   f"\n{validate_.scene_prompt_text(s)}{voice}")
    if plan.get("takeaway"):
        out.append(f"Ending: {plan['takeaway']}")
    return "\n\n".join(x for x in out if x)


def engine_block(intel: dict) -> str:
    """The approved story for the film engine's planner (compact, every field it needs)."""
    plan = intel.get("plan") or {}
    keep = ("n", "title", "duration_s", "what_happens", "actions", "emotions", "positioning", "framing",
            "camera_movement", "lens", "lighting", "atmosphere", "sfx", "audio", "transition_in", "start_state",
            "end_state", "props", "on_screen_text", "in_scene_text", "typography_style")
    scenes = []
    for s in plan.get("scenes") or []:
        d = {k: s.get(k) for k in keep if s.get(k)}
        d["video_prompt"] = validate_.scene_prompt_text(s)
        mg = s.get("motion_gfx") or {}
        if mg.get("keyframe_prompt"):
            d["keyframe_prompt"] = mg["keyframe_prompt"]
            d["method"] = mg.get("method")
        scenes.append(d)
    body = {"title": plan.get("title"), "story": plan.get("story_concept"), "bible": plan.get("bible"),
            "style": plan.get("style"), "audio_plan": plan.get("audio_plan"), "scenes": scenes}
    if intel.get("mode") == "motion_graphics":
        body["mode"] = ("full 3D motion graphics (locked palette; each scene's words are 3D letters INSIDE the keyframe, "
                        "spelled exactly - no flat text overlay)")
    return json.dumps(body, ensure_ascii=False)
