"""Fallback comparison: an alternative application is only offered with an honest statement of what changes.

Classification for each alternative:
  equivalent      - satisfies every requirement and nothing known changes
  changes_result  - valid, but something about the result changes or cannot be confirmed: needs explicit consent
  invalid         - cannot satisfy an essential requirement (reason given); never offered as a button
"""

import copy

from . import registry, router, validator
from .schemas import Brief


def compare_apps(original: dict, alt: dict, caps: list[str]) -> list[str]:
    """Differences in what the user would get if `alt` replaces `original` (known facts only, plus unknowns)."""
    changes = []
    o_ss, o_st = registry.fact(original, "shot_structures")
    a_ss, a_st = registry.fact(alt, "shot_structures")
    if o_st in registry.SUPPORTED and a_st in registry.SUPPORTED and o_ss and a_ss and set(o_ss) != set(a_ss):
        changes.append(f"{alt['name']} makes {' / '.join(x.replace('_', ' ') for x in a_ss)} video "
                       f"instead of {' / '.join(x.replace('_', ' ') for x in o_ss)}.")
    elif a_st == "unknown" and o_st in registry.SUPPORTED:
        changes.append(f"It is not known whether {alt['name']} produces the same kind of shot structure.")
    o_d, o_dst = registry.fact(original, "max_duration_s")
    a_d, a_dst = registry.fact(alt, "max_duration_s")
    if o_d and a_d and a_dst in registry.SUPPORTED and a_d < o_d:
        changes.append(f"Maximum length drops to {a_d:g}s (from {o_d:g}s).")
    for c in caps:
        if registry.cap_status(original, c) == "confirmed" and registry.cap_status(alt, c) == "unverified":
            changes.append(f"'{c}' is confirmed for {original['name']} but not yet for {alt['name']}.")
    return changes


def fallback_options(run: dict, step_id: str) -> list[dict]:
    apps = registry.apps()
    plan = run["plan"]
    step = next(s for s in plan["steps"] if s["step_id"] == step_id)
    original = apps[step["app_id"]]
    brief = Brief.model_validate((run.get("understanding") or {}).get("brief") or {})
    out = []
    for app in apps.values():
        if app["app_id"] == original["app_id"]:
            continue
        trial = copy.deepcopy(plan)
        tstep = next(s for s in trial["steps"] if s["step_id"] == step_id)
        tstep["app_id"] = app["app_id"]  # inputs (original file references) are kept exactly as they were
        issues = validator.validate(trial, run.get("understanding") or {}, run.get("plan_mode") or run["mode"], run["assets"])
        errs = validator.errors(issues)
        name = app["name"]
        if app["status"] != "available":
            out.append({"app_id": app["app_id"], "name": name, "classification": "invalid",
                        "reason": f"{name} is currently {app['status']}."})
            continue
        if errs:
            out.append({"app_id": app["app_id"], "name": name, "classification": "invalid", "reason": errs[0]["message"]})
            continue
        changes = compare_apps(original, app, step["capabilities_used"])
        changes += [i["message"] for i in issues if i["level"] == validator.UNCONFIRMED and i.get("step_id") == step_id]
        out.append({"app_id": app["app_id"], "name": name,
                    "classification": "changes_result" if changes else "equivalent", "changes": changes})
    order = {"equivalent": 0, "changes_result": 1, "invalid": 2}
    return sorted(out, key=lambda f: order[f["classification"]])


def substitute(run: dict, step_id: str, app_id: str) -> list[dict]:
    """Swap the application of one step and re-validate the whole plan; revert if invalid."""
    step = next(s for s in run["plan"]["steps"] if s["step_id"] == step_id)
    old = step["app_id"]
    step["app_id"] = app_id
    step.pop("handoff", None)  # the prompt was written for the previous app
    issues = validator.validate(run["plan"], run.get("understanding") or {}, run.get("plan_mode") or run["mode"], run["assets"])
    if validator.errors(issues):
        step["app_id"] = old
    return issues


def unavailable_preferred(routes: dict) -> list[dict]:
    """Apps that would have qualified but are temporarily unavailable (offline, maintenance), so a replacement must be
    explained. A deliberately disabled app is not a preference to replace: it is simply not considered."""
    out = []
    for ev in routes.get("singles", []):
        app = registry.apps()[ev["app_id"]]
        if app["status"] not in ("available", "disabled") and all("is currently" in f for f in ev["fails"]):
            out.append(ev)
    return out


__all__ = ["compare_apps", "fallback_options", "substitute", "unavailable_preferred", "router"]
