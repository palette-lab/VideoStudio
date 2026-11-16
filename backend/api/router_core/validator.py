"""Deterministic plan validation. No LLM here: every rule is checked in code against the registry.

Levels: error (blocks approval), unconfirmed (depends on an unknown application fact; shown, lowers confidence),
warning (informational).
"""

from . import registry

ERROR, WARNING, UNCONFIRMED = "error", "warning", "unconfirmed"


def _issue(level, code, message, step_id=None, **extra):
    return {"level": level, "code": code, "message": message, "step_id": step_id, **extra}


def _asset_inputs(step, assets):
    return [assets[s["source"].split(":", 1)[1]] for s in step.get("inputs", [])
            if s.get("source", "").startswith("asset:") and s["source"].split(":", 1)[1] in assets]


def _satisfied(req, step, assets, steps_by_id):
    """A capability requirement is met by a user asset (with the tag, if one is required) or an upstream handoff."""
    for a in _asset_inputs(step, assets):
        if a["modality"] == req["modality"] and (not req.get("tag") or req["tag"] in set(a.get("tags", [])) | {a.get("role")}):
            return True
    if req.get("tag"):
        return False
    for inp in step.get("inputs", []):
        src = inp.get("source", "")
        if src.startswith("step:") and registry.FORMAT_MODALITY.get(inp.get("format")) == req["modality"]:
            return True
    return False


def validate(plan: dict, understanding: dict, mode: str, assets: dict[str, dict]) -> list[dict]:
    """assets: asset_id -> {"modality", "tags", ...}. Returns a list of issues (errors block approval)."""
    apps, vocab = registry.apps(), registry.capabilities()
    issues: list[dict] = []
    steps = plan.get("steps") or []
    subtasks = understanding.get("subtasks") or []

    if not plan.get("feasible", True):
        return [_issue(ERROR, "declared_infeasible", plan.get("infeasible_reason") or "The planner found no valid route.")]
    if not steps:
        return [_issue(ERROR, "empty_plan", "The plan has no steps.")]

    # Subtask capabilities must exist in the vocabulary.
    for t in subtasks:
        for c in t.get("required_capabilities", []):
            if c not in vocab:
                issues.append(_issue(ERROR, "unknown_capability", f"Sub-task '{t.get('id')}' needs unknown capability '{c}'."))

    seen: dict[str, dict] = {}
    for idx, step in enumerate(steps):
        sid = step.get("step_id") or f"#{idx + 1}"
        if sid in seen:
            issues.append(_issue(ERROR, "duplicate_step", f"Step id '{sid}' is used twice.", sid))
        app = apps.get(step.get("app_id"))
        if not app:
            issues.append(_issue(ERROR, "unknown_app", f"App '{step.get('app_id')}' is not in the registry.", sid))
            seen[sid] = step
            continue
        if app["status"] != "available":
            issues.append(_issue(ERROR, "app_unavailable", f"{app['name']} is currently {app['status']}.", sid,
                                 fallbacks=[f for f in app.get("fallback_apps", []) if apps.get(f, {}).get("status") == "available"]))

        for c in step.get("capabilities_used", []):
            if c not in vocab:
                issues.append(_issue(ERROR, "unknown_capability", f"Capability '{c}' does not exist.", sid))
            elif c not in app["capabilities"]:
                issues.append(_issue(ERROR, "capability_not_supported", f"{app['name']} does not provide '{c}'.", sid))

        for inp in step.get("inputs", []):
            src, fmt = inp.get("source", ""), inp.get("format")
            if src == "prompt":
                if "text" not in app["input_modalities"]:
                    issues.append(_issue(ERROR, "unsupported_input", f"{app['name']} does not accept text prompts.", sid))
            elif src.startswith("asset:"):
                aid = src.split(":", 1)[1]
                if aid not in assets:
                    issues.append(_issue(ERROR, "unknown_asset", f"Asset '{aid}' was not uploaded.", sid))
                elif assets[aid]["modality"] not in app["input_modalities"]:
                    issues.append(_issue(ERROR, "unsupported_input",
                                         f"{app['name']} cannot take {assets[aid]['modality']} input ({assets[aid].get('filename', aid)}).", sid))
            elif src.startswith("step:"):
                up_id = src.split(":", 1)[1]
                up = seen.get(up_id)
                if up is None:
                    later = any(s.get("step_id") == up_id for s in steps)
                    issues.append(_issue(ERROR, "invalid_handoff_order" if later else "unknown_step",
                                         f"Step '{sid}' needs output of '{up_id}', which {'runs later' if later else 'does not exist'}.", sid))
                    continue
                up_app = apps.get(up.get("app_id"))
                if fmt not in (up.get("outputs") or []) or (up_app and fmt not in up_app["emits"]):
                    issues.append(_issue(ERROR, "handoff_format_mismatch", f"Step '{up_id}' does not produce '{fmt}'.", sid))
                if fmt not in app["accepts_handoff"]:
                    issues.append(_issue(ERROR, "handoff_not_accepted", f"{app['name']} cannot receive '{fmt}' from another app.", sid))
            else:
                issues.append(_issue(ERROR, "bad_input_source", f"Input source '{src}' is not valid.", sid))

        for o in step.get("outputs", []):
            if o not in app["emits"]:
                issues.append(_issue(ERROR, "unsupported_output", f"{app['name']} does not produce '{o}'.", sid))

        for c in step.get("capabilities_used", []):
            for req in app.get("capability_requirements", {}).get(c, []):
                if not _satisfied(req, step, assets, seen):
                    what = f"{req['modality']}" + (f" showing a {req['tag']}" if req.get("tag") else "")
                    issues.append(_issue(ERROR, "missing_required_asset",
                                         f"{app['name']} {req['reason']} (no {what} was provided).", sid,
                                         requirement=req, capability=c))
        seen[sid] = step

    # Every sub-task's capabilities must be covered by the steps assigned to it.
    for t in subtasks:
        assigned = [s for s in steps if t.get("id") in (s.get("subtask_ids") or [])]
        # Only capabilities the chosen app really offers count as coverage.
        provided = {c for s in assigned for c in s.get("capabilities_used", [])
                    if c in apps.get(s.get("app_id"), {}).get("capabilities", [])}
        missing = set(t.get("required_capabilities", [])) - provided
        if missing:
            issues.append(_issue(ERROR, "uncovered_requirement",
                                 f"Sub-task '{t.get('description', t.get('id'))}' is not covered: missing {sorted(missing)}.",
                                 missing=sorted(missing)))

    required = {c for t in subtasks for c in t.get("required_capabilities", [])}

    # Mode rules.
    if mode == "normal" and len(steps) != 1:
        issues.append(_issue(ERROR, "mode_violation", "Normal mode allows exactly one application."))
    if len(steps) > 1:
        used_modalities = {assets[a]["modality"] for s in steps for a in
                           [i["source"].split(":", 1)[1] for i in s.get("inputs", []) if i.get("source", "").startswith("asset:")]
                           if a in assets}
        for app_id in registry.apps_covering(required):
            app = apps[app_id]
            if used_modalities <= set(app["input_modalities"]) and _single_app_requirements_ok(app, required, assets):
                issues.append(_issue(ERROR, "unnecessary_combination",
                                     f"{app['name']} alone covers every requirement; combining apps is not needed.", app_id=app_id))
                break
        for s in steps:
            others = {c for o in steps if o is not s for c in o.get("capabilities_used", [])}
            if set(s.get("capabilities_used", [])) <= others:
                issues.append(_issue(ERROR, "redundant_step", f"Step '{s.get('step_id')}' adds no capability the other steps lack.", s.get("step_id")))
        for s in steps:
            if "edit.assemble_clips" not in s.get("capabilities_used", []):
                continue
            ups = [i for i in s.get("inputs", []) if i.get("source", "").startswith("step:")]
            ids = {i["source"][5:] for i in ups}
            overlaid = any("motion.overlay_graphics" in o.get("capabilities_used", []) and o.get("step_id") in ids
                           and any(x.get("source", "")[5:] in ids for x in o.get("inputs", []) if x.get("source", "").startswith("step:"))
                           for o in steps)
            if overlaid and not all(i.get("use") for i in ups):
                issues.append(_issue(ERROR, "ambiguous_shot_versions",
                                     f"Step '{s.get('step_id')}' receives the same shots with and without graphics, but the "
                                     "plan does not say which version to use.", s.get("step_id")))
        consumed = {i["source"].split(":", 1)[1] for s in steps for i in s.get("inputs", []) if i.get("source", "").startswith("step:")}
        for s in steps[:-1]:
            if s.get("step_id") not in consumed:
                issues.append(_issue(ERROR, "output_not_used", f"Output of step '{s.get('step_id')}' is not passed to any later step, so it does not reach the final video.", s.get("step_id")))

    issues += _brief_checks(plan, understanding, assets)

    if mode == "hybrid" and len(steps) == 1:
        issues.append(_issue(WARNING, "single_app_sufficient", "One application covers this request, so no combination was made."))

    uploaded_used = {i["source"].split(":", 1)[1] for s in steps for i in s.get("inputs", []) if i.get("source", "").startswith("asset:")}
    for aid, a in assets.items():
        if aid not in uploaded_used:
            issues.append(_issue(WARNING, "asset_unused", f"Uploaded file '{a.get('filename', aid)}' is not used by the plan."))
    return issues


def _single_app_requirements_ok(app, required, assets):
    for c in required:
        for req in app.get("capability_requirements", {}).get(c, []):
            if not any(a["modality"] == req["modality"] and (not req.get("tag") or req["tag"] in set(a.get("tags", [])) | {a.get("role")})
                       for a in assets.values()):
                return False
    return True


def errors(issues):
    return [i for i in issues if i["level"] == ERROR]


AUDIO_CAPS = {"edit.audio_mix": ("voiceover", "music", "sound_effects"), "edit.captions_subtitles": ("subtitles",)}


def _brief_checks(plan: dict, understanding: dict, assets: dict) -> list[dict]:
    """Checks that need the production brief: limits, roles, exclusions, unrequested work, final deliverable."""
    from . import router
    from .schemas import Brief

    raw = understanding.get("brief")
    if not raw:
        return []
    brief = Brief.model_validate(raw)
    apps = registry.apps()
    steps = plan.get("steps") or []
    out = []
    groups = [t.get("group") for t in understanding.get("subtasks", []) if t.get("group")]
    primary = next((g for g in ("generate", "motion", "edit", "reference") if g in groups), None)
    required = {c for t in understanding.get("subtasks", []) for c in t.get("required_capabilities", [])}

    for i, step in enumerate(steps):
        app = apps.get(step.get("app_id"))
        if not app:
            continue
        sid = step.get("step_id")
        asset_ids = [x["source"][6:] for x in step.get("inputs", []) if x.get("source", "").startswith("asset:") and x["source"][6:] in assets]
        handoff = any(x.get("source", "").startswith("step:") for x in step.get("inputs", []))
        is_primary = (f"t_{primary}" in (step.get("subtask_ids") or [])) if primary else i == 0
        ev = router.evaluate(app, [c for c in step.get("capabilities_used", []) if registry.supports(app, c)], brief,
                             asset_ids, assets, handoff, is_primary)
        for f in ev["limit_fails"]:
            out.append(_issue(ERROR, "limit_violation", f, sid))
        for u in ev["unconfirmed"]:
            out.append(_issue(UNCONFIRMED, "unconfirmed_requirement", u, sid))
        for c in step.get("capabilities_used", []):
            if required and c not in required:
                out.append(_issue(ERROR, "unrequested_capability", f"Step '{sid}' does '{c}', which the request does not need.", sid))
            for k in AUDIO_CAPS.get(c, ()):
                if getattr(brief.audio, k) == "excluded" and not any(getattr(brief.audio, o) == "requested" for o in AUDIO_CAPS[c]):
                    out.append(_issue(ERROR, "exclusion_violated", f"Step '{sid}' adds {k.replace('_', ' ')}, which was excluded.", sid))
    if steps and "video_file" not in (steps[-1].get("outputs") or []):
        out.append(_issue(ERROR, "final_output_missing", "The last step does not produce the requested video."))
    return out
