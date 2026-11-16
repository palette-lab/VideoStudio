"""The analysis pipeline shared by the API and the evaluation runner.

LLM calls: 1 (brief) in Normal/Hybrid; in AI Analyze +1 judge (only when several routes are viable) +1 review.
Everything else (requirements, routing, plans, validation, readiness, confidence) is deterministic.
"""

from . import brain, config, fallback, handoff, llm, registry, router, validator
from .requirements import compile_brief, rule_questions
from .schemas import Brief, Decision, Question, WorkflowStep
from .understanding import build_brief

SEGMENTS_Q = ("The animated parts (titles, typography, logo reveal) would be separate segments, and no application is "
              "confirmed to join segments yet. How should they be included?")
SEGMENTS_OVERLAY = "Animate them on top of the footage"
SEGMENTS_KEEP = "Keep them as separate segments (not possible yet)"

# One-app modes: the user picks the app; the AI still reads the request and plans, but only for that app.
APP_MODES = {"storyboard": "pltt-storyboard-video-maker", "flow": "pltt-flow-video-2", "motion": "palette-motion-studio",
             "presenter": "palette-presenter-studio"}

GROUP_LABEL = {"reference": "understands the reference", "generate": "produces the footage",
               "motion": "adds the programmatic animation", "edit": "edits and finishes the video",
               "finish": "adds the finishing effects"}


async def analyze(*, prompt: str, mode: str, assets: dict, perception: dict, answers: dict,
                  proceed_with_defaults: bool = False, chat=llm.chat_json, _recheck: dict | None = None,
                  settings: dict | None = None) -> dict:
    brief = await build_brief(prompt, assets, perception, answers, chat, recheck=_recheck)
    settings = settings or {}
    if settings.get("duration_s"):  # picked in the composer: wins over what the prompt says
        brief.constraints.duration_s = float(settings["duration_s"])
    if settings.get("aspect_ratio"):
        brief.constraints.aspect_ratio = settings["aspect_ratio"]
    joining = answers.get(SEGMENTS_Q)
    if joining == SEGMENTS_OVERLAY and brief.motion.integration == "separate_segments":
        brief.motion.integration = "overlay_on_footage"  # the user chose this explicitly (consent, not a silent change)
    if mode == "presenter" and "talking_presenter" not in brief.production_type:
        brief.production_type = ["talking_presenter", *brief.production_type]  # the user picked a talking presenter
        if brief.detected_intent in ("unclear", "edit_existing"):
            brief.detected_intent = "create_new"
    for aid, a in assets.items():  # roles travel with the asset; the original reference (sha256, path) is untouched
        r = brief.role_of(aid)
        a["role"], a["use"] = r.role, r.use
    comp = compile_brief(brief, assets, presenter_mode=mode == "presenter")
    und = understanding_view(brief, comp)
    res = {"open_details": [q.model_dump() for q in brief.missing_info if q.affects == "content"],
           "understanding": und, "plan": None, "issues": [], "questions": [], "suggestion": None, "review": None,
           "uncertainty": [], "plan_changes": [], "routes": None, "status": None}

    caps = {r["capability"] for r in comp["requirements"]}
    unsupported = registry.unavailable_capabilities(caps)
    upload_q = []
    if brief.refers_to_uploads and not assets and not proceed_with_defaults:
        upload_q = [Question(id="upload_mentioned", source="rule", kind="upload",
                             question="You mention uploaded files, but none arrived. Please upload them.",
                             why="The request refers to your files; without them the plan would be based on guesses.")]
    if (unsupported == ["edit.assemble_clips"] and brief.motion.integration == "separate_segments"
            and joining is None and not proceed_with_defaults and config.ASK_POLICY == "all"):
        # Only joining separate segments is missing, and that came from how the parts relate: ask, don't dead-end.
        q = Question(id="segments", source="rule", kind="choice", question=SEGMENTS_Q,
                     why="No application is confirmed to join separately made segments into one video yet.",
                     options=[SEGMENTS_OVERLAY, SEGMENTS_KEEP])
        res["questions"] = [q.model_dump()] + [u.model_dump() for u in upload_q]
        res["status"] = "needs_clarification"
        return _finish(res, brief, comp, assets, mode)
    if unsupported and _recheck is None and prompt.strip():
        # Second look before saying "not possible": the AI re-reads the request and keeps each unmet requirement
        # only if the user's own words ask for it (it may have been an assumption).
        vocab = registry.capabilities()
        unmet = [{"capability": r["capability"], "description": vocab[r["capability"]], "because": r["reason"],
                  "from_brief_field": r["field"]} for r in comp["requirements"] if r["capability"] in unsupported]
        second = await analyze(prompt=prompt, mode=mode, assets=assets, perception=perception, answers=answers,
                               proceed_with_defaults=proceed_with_defaults, chat=chat, settings=settings,
                               _recheck={"previous_brief": brief.model_dump(), "unmet_requirements": unmet})
        und2 = second.get("understanding") or {}
        if und2.get("detected_intent") not in (None, "unclear") and und2.get("requirements"):
            return second
        # The re-read threw the request away instead of correcting an assumption: keep the first reading.
        _recheck = {"discarded": True}
    if unsupported:  # explain, do not ask questions about things no app can do
        vocab = registry.capabilities()
        res["status"] = "not_possible"
        res["unsupported"] = [{"capability": c, "description": vocab[c]} for c in unsupported]
        res["suggestion"] = ("None of the available applications is confirmed to do this yet: "
                             + "; ".join(vocab[c] for c in unsupported)
                             + (". (Checked twice against your request.)" if _recheck else ""))
        return _finish(res, brief, comp, assets, mode)

    ai_q = None
    if not proceed_with_defaults:
        qs = upload_q + rule_questions(brief, comp, prompt, assets)
        ai_q = None if qs else next((q for q in brief.missing_info if q.blocking and q.affects == "routing"), None)
        if ai_q and (brief.detected_intent == "unclear" or brief.confidence < 0.6):
            qs = [ai_q]  # the AI is unsure what is wanted: ask before planning
        if config.ASK_POLICY == "uploads":
            # Only ask for files. Other open points become sensible defaults, shown as optional details.
            res["open_details"] += [{**q.model_dump(), "blocking": False} for q in qs if q.kind != "upload"]
            if ai_q:
                res["open_details"].append({**ai_q.model_dump(), "blocking": False})
            qs, ai_q = [q for q in qs if q.kind == "upload"], None
            for c in comp["conflicts"][:2]:
                und.setdefault("assumptions", []).append(
                    f"{c} Planned as the request is written; send a follow-up to change it.")
            if not caps and not qs:
                res["status"] = "not_possible"
                res["suggestion"] = "There is nothing to produce yet: please describe the video you want."
                return _finish(res, brief, comp, assets, mode)
        if not caps and not qs:
            qs = [Question(id="outcome", source="rule", question="What should be produced?",
                           why="The request did not describe a video to make or edit.")]
        if qs:
            res["questions"] = [q.model_dump() for q in qs[:2]]
            res["status"] = "needs_clarification"
            return _finish(res, brief, comp, assets, mode)
    elif not caps:
        res["status"] = "not_possible"
        res["suggestion"] = "There is nothing to produce yet: please describe the video you want."
        return _finish(res, brief, comp, assets, mode)

    routes = router.build_routes(brief, comp, assets, bool(prompt.strip()))
    res["routes"] = _routes_view(routes)
    viable = router.viable_routes(routes)
    if mode == "normal":
        viable = [v for v in viable if v["kind"] == "single"]
    picked = None
    if mode in APP_MODES:
        picked = next((e for e in routes["singles"] if e["app_id"] == APP_MODES[mode]), None)
        viable = [router.single_route(picked, routes["groups"])] if picked and picked["ok"] else []

    if not viable and ai_q:
        res["questions"] = [ai_q.model_dump()]  # no route as understood: the AI's question may resolve it
        res["status"] = "needs_clarification"
        return _finish(res, brief, comp, assets, mode)
    if ai_q:  # a route exists and understanding is confident: offer the question as an optional refinement
        res["open_details"].append({**ai_q.model_dump(), "blocking": False})

    if not viable:
        missing = _missing_assets(routes)
        if missing and not proceed_with_defaults:
            res["questions"] = [Question(id=f"upload{i + 1}", source="rule", kind="upload", question=m + " Please upload it.",
                                         why="Required by the only applications that can do this.").model_dump()
                                for i, m in enumerate(missing[:2])]
            res["status"] = "needs_clarification"
        else:
            res["status"] = "not_possible"
            if mode in APP_MODES:
                name = registry.apps()[APP_MODES[mode]]["name"]
                why = " ".join(str(f) for f in (picked or {}).get("fails", [])[:3]) or f"{name} is not available."
                res["suggestion"] = f"{name} can't make this on its own: {why} Switch to Auto to let the AI pick or combine apps."
            elif mode == "normal" and routes["multi"]:
                res["suggestion"] = "No single application covers this. Switch to Hybrid or AI Analyze to combine applications."
            else:
                res["suggestion"] = "No available application (or combination) satisfies every requirement."
            res["blocking_reasons"] = _top_reasons(routes)
        return _finish(res, brief, comp, assets, mode)

    if mode == "auto":
        return await _auto(res, viable, routes, prompt, und, assets, brief, comp, chat)

    choice, judged = viable[0], None
    if mode == "analyze" and len(viable) > 1:
        judged = await brain.judge(prompt, brief.model_dump(), viable[:3], chat)
        choice = viable[:3][judged["choice"]]

    plan = router.to_plan(choice, prompt, assets)
    handoff.attach(plan, brief, prompt, assets)
    plan["why"] = _why(choice, routes, judged)
    plan["alternatives"] = _alternatives(routes, choice)
    issues = validator.validate(plan, und, "normal" if mode in APP_MODES else mode, assets)
    res.update(plan=plan, issues=issues, judge=judged)
    if validator.errors(issues):  # a deterministic plan should never get here; report honestly if it does
        res["status"] = "not_possible"
        res["suggestion"] = "The generated plan failed validation: " + validator.errors(issues)[0]["message"]
        return _finish(res, brief, comp, assets, mode)

    res["plan_changes"] = _plan_changes(routes, plan)

    if mode != "analyze":
        res["uncertainty"] = _uncertainty(brief, comp, routes, choice, issues)
    else:
        res["review"] = await brain.review(prompt, brief.model_dump(), plan, chat)
    res["status"] = "awaiting_approval"
    return _finish(res, brief, comp, assets, mode)


def _plan_changes(routes: dict, plan: dict) -> list[dict]:
    """An unavailable application that would have been preferred: the replacement must be explained and consented to."""
    for ev in fallback.unavailable_preferred(routes):
        if ev["app_id"] not in [s["app_id"] for s in plan["steps"]] and len(plan["steps"]) == 1:
            alt = registry.apps()[plan["steps"][0]["app_id"]]
            changes = fallback.compare_apps(registry.apps()[ev["app_id"]], alt, plan["steps"][0]["capabilities_used"])
            return [{"unavailable": ev["name"], "replacement": alt["name"],
                     "changes": changes or ["No known difference in the result."]}]
    return []


async def _auto(res, viable, routes, prompt, und, assets, brief, comp, chat=llm.chat_json) -> dict:
    """Auto mode (Auto + AI Analyze in one): analyse, then offer every valid approach (one app / apps combined).

    Each option is a complete, validated plan. A combination is only an option when it passes validation, i.e. when
    no single application already covers the request, so one app vs. combined (Normal vs. Hybrid) is decided here.
    When several options are valid the AI judge picks the recommendation; the recommended plan is then reviewed.
    """
    candidates = list(viable[:3])
    if routes["multi"] and all(v["kind"] == "single" for v in candidates):
        candidates.append(routes["multi"])  # kept only if validation accepts it (not an unnecessary combination)
    results = []
    for route in candidates:
        pmode = "normal" if route["kind"] == "single" else "hybrid"
        plan = router.to_plan(route, prompt, assets)
        handoff.attach(plan, brief, prompt, assets)
        plan["why"] = _why(route, routes, None)
        plan["alternatives"] = _alternatives(routes, route)
        issues = validator.validate(plan, und, pmode, assets)
        if validator.errors(issues):
            continue
        r = {**res, "plan": plan, "issues": issues, "plan_mode": pmode, "status": "awaiting_approval",
             "plan_changes": _plan_changes(routes, plan),
             "uncertainty": _uncertainty(brief, comp, routes, route, issues)}
        results.append((route, _finish(r, brief, comp, assets, pmode)))
    if not results:
        res["status"] = "not_possible"
        res["suggestion"] = "No valid route passed validation."
        return _finish(res, brief, comp, assets, "auto")

    judged = None
    if len(results) > 1:
        judged = await brain.judge(prompt, brief.model_dump(), [r for r, _ in results], chat)
        k = judged["choice"]
        results = [results[k]] + results[:k] + results[k + 1:]
        results[0][1]["plan"]["why"] = judged.get("why") or results[0][1]["plan"]["why"]
    results[0][1]["judge"] = judged
    results[0][1]["review"] = await brain.review(prompt, brief.model_dump(), results[0][1]["plan"], chat)
    apps = registry.apps()
    best_route = results[0][0]
    options = []
    for i, (route, r) in enumerate(results):
        names = [s["name"] for s in route["steps"]]
        changes = []
        if i and route["kind"] == "single" and best_route["kind"] == "single":
            changes = fallback.compare_apps(apps[best_route["steps"][0]["app_id"]], apps[route["steps"][0]["app_id"]],
                                            r["plan"]["steps"][0]["capabilities_used"])
        options.append({
            "id": f"o{i + 1}", "recommended": i == 0, "kind": route["kind"],
            "approach": "One application" if route["kind"] == "single" else f"{len(names)} applications combined (Hybrid)",
            "apps": [s["app_id"] for s in route["steps"]], "names": names, "why": r["plan"]["why"],
            "confidence": r["decision"]["confidence"], "unconfirmed": [u["message"] for u in r["decision"]["unconfirmed_requirements"]],
            "differences_from_recommended": changes, "needs_consent": bool(r.get("plan_changes")),
        })
    option_results = {o["id"]: dict(r) for o, (_, r) in zip(options, results)}  # copies: no self-reference
    chosen = dict(results[0][1])
    chosen["options"] = options
    chosen["option_results"] = option_results
    chosen["selected_option"] = "o1"
    return chosen


def understanding_view(brief: Brief, comp: dict) -> dict:
    """Backward-compatible understanding block (goal, deliverable, subtasks) plus the full brief."""
    b = brief.model_dump()
    return {**b, "brief": b, "goal": brief.user_goal, "deliverable": brief.requested_output,
            "subtasks": comp["subtasks"], "requirements": comp["requirements"],
            "assumptions": brief.assumptions + comp["notes"], "conflicts": comp["conflicts"],
            "cross_check": comp["cross_check"]}


def _routes_view(routes: dict) -> dict:
    return {"groups": routes["groups"],
            "singles": [{k: e[k] for k in ("app_id", "name", "ok", "score", "parts", "fails", "unconfirmed")} for e in routes["singles"]],
            "multi": [s["app_id"] for s in routes["multi"]["steps"]] if routes["multi"] else None}


def _missing_assets(routes: dict) -> list[str]:
    msgs = []
    for ev in routes["singles"]:
        if ev["missing_assets"] and len(ev["fails"]) == len(ev["missing_assets"]):
            for m in ev["missing_assets"]:
                what = m["modality"] + (f" showing the {m['tag']}" if m.get("tag") else "")
                msg = f"{ev['name']} {m['reason']}: no {what} was provided."
                if msg not in msgs:
                    msgs.append(msg)
    return msgs


def _top_reasons(routes: dict) -> list[str]:
    out = []
    for ev in routes["singles"][:5]:
        if ev["fails"]:
            out.append(ev["fails"][0])
    return out[:4]


def _why(choice: dict, routes: dict, judged: dict | None) -> str:
    names = [s["name"] for s in choice["steps"]]
    if choice["kind"] == "single":
        text = f"{names[0]} covers every requirement on its own, so no other application is needed."
        others = [e for e in routes["singles"] if e["ok"] and e["app_id"] != choice["steps"][0]["app_id"]]
        if others:
            o = others[0]
            text += (f" It fits better than {o['name']} ({choice['score']:.2f} vs {o['score']:.2f})." if choice["score"] >= o["score"]
                     else f" It scores lower than {o['name']} ({choice['score']:.2f} vs {o['score']:.2f}).")
    else:
        parts = [f"{s['name']} {', '.join(GROUP_LABEL[g] for g in s['groups'])}" for s in choice["steps"]]
        text = "No single application covers everything: " + "; then ".join(parts) + "."
    if judged and judged.get("why"):
        text += " " + judged["why"]
    return text


def _alternatives(routes: dict, choice: dict) -> list[dict]:
    chosen = [s["app_id"] for s in choice["steps"]]
    out = []
    for ev in routes["singles"]:
        if [ev["app_id"]] == chosen:
            continue
        verdict = (f"Also possible (score {ev['score']:.2f})." if ev["ok"] else ev["fails"][0])
        out.append({"apps": [ev["app_id"]], "verdict": verdict})
    if routes["multi"] and [s["app_id"] for s in routes["multi"]["steps"]] != chosen:
        out.append({"apps": [s["app_id"] for s in routes["multi"]["steps"]],
                    "verdict": "Not needed: a single application covers the request." if choice["kind"] == "single"
                    else f"Combination score {routes['multi']['score']:.2f}."})
    return out[:5]


def _uncertainty(brief, comp, routes, choice, issues) -> list[str]:
    reasons = []
    ok = [e for e in routes["singles"] if e["ok"]]
    if choice["kind"] == "single" and len(ok) > 1 and ok[0]["score"] - ok[1]["score"] < router.CLOSE_CALL:
        reasons.append(f"{ok[0]['name']} and {ok[1]['name']} fit almost equally well.")
    # Unconfirmed application facts are listed separately: deeper AI analysis cannot confirm them, so no nudge here.
    if brief.confidence < 0.6:
        reasons.append(f"I am only {round(brief.confidence * 100)}% sure I understood the request.")
    if comp["cross_check"]["ai_only"]:
        reasons.append("The request may also need: " + ", ".join(registry.capabilities().get(c, c).rstrip(".").lower()
                                                                  for c in comp["cross_check"]["ai_only"][:2]) + ".")
    return reasons


def _confidence(brief: Brief, comp: dict, res: dict) -> tuple[float, dict]:
    """Deterministic confidence: AI self-report is only one of five inputs."""
    plan = res.get("plan") or {}
    issues = res.get("issues") or []
    n_unconf = sum(1 for i in issues if i["level"] == validator.UNCONFIRMED)
    parts = {
        "route_score": plan.get("route_score", 0.0),
        "understanding": brief.confidence,
        "validation": 0.0 if validator.errors(issues) else (1.0 if not issues else 0.85),
        "requirement_agreement": 1.0 if comp["cross_check"]["agree"] else 0.8,
        "unconfirmed_penalty": round(min(0.3, 0.06 * n_unconf), 2),
    }
    conf = 0.4 * parts["route_score"] + 0.25 * parts["understanding"] + 0.2 * parts["validation"] + \
        0.15 * parts["requirement_agreement"] - parts["unconfirmed_penalty"]
    return round(max(0.0, min(1.0, conf)), 2), parts


def _finish(res: dict, brief: Brief, comp: dict, assets: dict, mode: str) -> dict:
    apps = registry.apps()
    plan = res.get("plan")
    conf, parts = _confidence(brief, comp, res) if plan else (0.0, {})
    if plan:
        plan["confidence"] = conf
    errors = validator.errors(res.get("issues") or [])
    blockers = []
    if res["status"] != "awaiting_approval":
        blockers.append({"needs_clarification": "Waiting for your answer.", "not_possible": res.get("suggestion") or "Not possible."}
                        .get(res["status"], res["status"]))
    blockers += [e["message"] for e in errors]
    if res.get("plan_changes"):
        blockers.append("A replacement application changes the result; your explicit consent is required.")
    steps = (plan or {}).get("steps", [])
    decision = Decision(
        detected_intent=brief.detected_intent, user_goal=brief.user_goal, requested_output=brief.requested_output,
        input_assets=[{"asset_id": a, "filename": v.get("filename"), "modality": v["modality"], "sha256": v.get("sha256")}
                      for a, v in assets.items()],
        asset_roles=brief.asset_roles, production_type=brief.production_type,
        scene_requirements=brief.scene, visual_requirements=brief.visual, motion_requirements=brief.motion,
        audio_requirements=brief.audio, editing_requirements=brief.editing, constraints=brief.constraints,
        required_capabilities=comp["requirements"],
        selected_applications=[s["app_id"] for s in steps],
        selection_reasons=[plan["why"]] if plan and plan.get("why") else [],
        workflow_steps=[WorkflowStep(step_id=s["step_id"], app_id=s["app_id"], app_name=apps[s["app_id"]]["name"],
                                     responsibility=s["responsibility"], capabilities=s["capabilities_used"],
                                     inputs=s["inputs"], outputs=s["outputs"]) for s in steps],
        handoff_requirements=[{"from_step": i["source"][5:], "to_step": s["step_id"], "format": i["format"],
                               "validated": not any(e.get("step_id") == s["step_id"] and "handoff" in e["code"] for e in errors)}
                              for s in steps for i in s["inputs"] if i["source"].startswith("step:")],
        unsupported_requirements=res.get("unsupported", []),
        unconfirmed_requirements=[{"step_id": i.get("step_id"), "message": i["message"]}
                                  for i in res.get("issues") or [] if i["level"] == validator.UNCONFIRMED],
        confidence=conf, confidence_breakdown=parts,
        clarification_required=res["status"] == "needs_clarification",
        questions=[Question.model_validate(q) for q in res.get("questions") or []],
        execution_ready=res["status"] == "awaiting_approval" and not errors and not res.get("plan_changes"),
        readiness_blockers=blockers,
    )
    res["decision"] = decision.model_dump()
    return res
