"""Capability-based scoring router and deterministic plan builder.

1. Hard filter: an app is only a candidate if it is available, supports (confirmed/unverified) every required
   capability, accepts the inputs and handoff, has the required assets, and does not violate a KNOWN limit
   (duration, shot structure, aspect ratio, asset role). Unknown facts never pass as supported: they are listed as
   "unconfirmed" and lower the score.
2. Score (0..1) for candidates:  0.35 constraint fit + 0.20 role fit + 0.25 profile trust + 0.20 focus
   - constraint/role fit: 1 per satisfied known check, 0.5 per unknown one (average; 1 when nothing to check)
   - trust: 1 per confirmed capability, 0.7 per unverified one (average)
   - focus: required capabilities / capabilities the app offers (prefers the specialist)
3. Routes: best single app; otherwise best app per production group (reference -> generate -> motion -> edit),
   chained by video handoffs. Plans are built here, in code; no LLM is needed to write steps.
"""

from . import registry
from .requirements import GROUP_ORDER, VISIBLE_ROLES
from .schemas import Brief

WEIGHTS = {"constraint_fit": 0.35, "role_fit": 0.20, "trust": 0.25, "focus": 0.20}
CLOSE_CALL = 0.08
ROLE_CHECKED = VISIBLE_ROLES | {"footage_to_edit", "audio_track", "style_reference"}


def group_assets(group: str, groups: list[str], brief: Brief, assets: dict) -> list[str]:
    """Which uploaded files each production group receives, by their role (never by filename)."""
    out = []
    for aid, a in assets.items():
        role, m = brief.role_of(aid).role, a["modality"]
        if group == "reference":
            take = role == "motion_reference"
        elif group == "generate":
            take = m == "image" and (role in VISIBLE_ROLES or role in ("style_reference", "unclear"))
        elif group == "motion":
            # With a generate step, product images go there; graphics are overlaid on the handed-off video.
            take = "generate" not in groups and (
                (m == "image" and role in ("product", "logo", "background", "unclear")) or role == "footage_to_edit")
        elif group == "finish":  # effects on the finished video: the uploaded footage only when nothing makes video
            take = role == "footage_to_edit" and not any(g in groups for g in ("generate", "motion", "edit"))
        else:  # edit
            take = role == "audio_track" or m == "audio" or \
                   (role == "footage_to_edit" and "motion" not in groups) or \
                   (m == "video" and role == "unclear" and "motion" not in groups)
        if take:
            out.append(aid)
    return out


def _tagged(asset: dict, brief: Brief, aid: str) -> set:
    return set(asset.get("tags") or []) | {brief.role_of(aid).role}


def evaluate(app: dict, caps: list[str], brief: Brief, asset_ids: list[str], assets: dict,
             handoff_in: bool, primary: bool, has_prompt: bool = True) -> dict:
    fails, unconfirmed, missing_assets, limit_fails = [], [], [], []
    cfit, rfit = [], []
    name = app["name"]

    if app["status"] != "available":
        fails.append(f"{name} is currently {app['status']}.")
    for c in caps:
        st = registry.cap_status(app, c)
        if st not in registry.SUPPORTED:
            declared_unknown = c in app.get("capability_status", {})
            fails.append(f"{name} does not offer '{c}'" + (" (its support is unknown, so it is not counted)" if declared_unknown else "") + ".")
    mods = {assets[a]["modality"] for a in asset_ids}
    for m in sorted(mods - set(app["input_modalities"])):
        fails.append(f"{name} cannot take {m} input.")
    if handoff_in and "video_file" not in app["accepts_handoff"]:
        fails.append(f"{name} cannot receive a video from the previous application.")

    for c in caps:
        for req in app.get("capability_requirements", {}).get(c, []):
            ok = any(assets[a]["modality"] == req["modality"] and (not req.get("tag") or req["tag"] in _tagged(assets[a], brief, a))
                     for a in asset_ids) or (handoff_in and req["modality"] == "video" and not req.get("tag"))
            if not ok:
                what = req["modality"] + (f" showing a {req['tag']}" if req.get("tag") else "")
                fails.append(f"{name} {req['reason']} (no {what} was provided).")
                missing_assets.append({**req, "capability": c})

    def known(key):
        return registry.fact(app, key)

    if primary:
        d = brief.constraints.duration_s
        if d:
            val, st = known("max_duration_s")
            if st in registry.SUPPORTED and val is not None:
                if d > val:
                    fails.append(f"{name} makes at most {val:g}s; {d:g}s was requested.")
                    limit_fails.append(fails[-1])
                cfit.append(1.0)
            elif st == "unknown":
                unconfirmed.append(f"{name}: maximum duration unknown ({d:g}s requested).")
                cfit.append(0.5)
        ss = brief.scene.shot_structure
        implied = ("single_continuous" if {"video.text_to_video", "video.image_to_video"} & set(caps) else
                   "multi_scene" if "story.multi_scene_storyboard" in caps else None)
        if ss == "unspecified" and implied:
            # Not stated by the user: a soft preference from the capability, never a hard rejection.
            val, st = known("shot_structures")
            if st in registry.SUPPORTED and val is not None:
                cfit.append(1.0 if implied in val else 0.3)
        if ss != "unspecified":
            val, st = known("shot_structures")
            if st in registry.SUPPORTED and val is not None:
                if ss not in val:
                    fails.append(f"{name} makes {' / '.join(v.replace('_', ' ') for v in val)} video, not "
                                 f"{ss.replace('_', ' ')}.")
                    limit_fails.append(fails[-1])
                cfit.append(1.0)
            elif st == "unknown":
                unconfirmed.append(f"{name}: cannot confirm it produces {ss.replace('_', ' ')} video.")
                cfit.append(0.5)
    ar = brief.constraints.aspect_ratio
    if ar and (primary or "edit.reformat_aspect" in caps):
        val, st = known("aspect_ratios")
        if st in registry.SUPPORTED and val is not None:
            if ar not in val:
                fails.append(f"{name} does not output {ar}.")
                limit_fails.append(fails[-1])
            cfit.append(1.0)
        elif st == "unknown":
            unconfirmed.append(f"{name}: {ar} output is not confirmed.")
            cfit.append(0.5)

    val, st = known("accepts_asset_roles")
    for a in asset_ids:
        role = brief.role_of(a).role
        if role not in ROLE_CHECKED:
            continue
        if st in registry.SUPPORTED and val is not None:
            if role not in val:
                fails.append(f"{name} does not take a {role.replace('_', ' ')} file ({assets[a].get('filename', a)}).")
                limit_fails.append(fails[-1])
            rfit.append(1.0)
        else:
            unconfirmed.append(f"{name}: cannot confirm it uses a {role.replace('_', ' ')} file.")
            rfit.append(0.5)

    trust = [1.0 if registry.cap_status(app, c) == "confirmed" else 0.7 for c in caps if registry.supports(app, c)]
    parts = {
        "constraint_fit": sum(cfit) / len(cfit) if cfit else 1.0,
        "role_fit": sum(rfit) / len(rfit) if rfit else 1.0,
        "trust": sum(trust) / len(trust) if trust else 0.0,
        "focus": len(caps) / max(1, len(app["capabilities"])),
    }
    score = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 3) if not fails else 0.0
    return {"app_id": app["app_id"], "name": name, "ok": not fails, "fails": fails, "unconfirmed": unconfirmed,
            "missing_assets": missing_assets, "limit_fails": limit_fails, "score": score, "parts": {k: round(v, 2) for k, v in parts.items()},
            "caps": list(caps), "assets": list(asset_ids), "handoff_in": handoff_in}


def build_routes(brief: Brief, compiled: dict, assets: dict, has_prompt: bool) -> dict:
    groups = [t["group"] for t in compiled["subtasks"]]
    caps_by_group = {t["group"]: t["required_capabilities"] for t in compiled["subtasks"]}
    all_caps = [c for g in groups for c in caps_by_group[g]]
    primary = next((g for g in ("generate", "motion", "edit", "reference") if g in groups), None)
    apps = registry.apps()
    if not groups:
        return {"groups": [], "singles": [], "multi": None, "all_caps": []}

    used = []
    for g in groups:
        used += [a for a in group_assets(g, groups, brief, assets) if a not in used]
    singles = sorted((evaluate(app, all_caps, brief, used, assets, False, True, has_prompt) for app in apps.values()),
                     key=lambda e: -e["score"])

    # Multi-app route: best candidate per group, chained by video handoff.
    per_group, chain_ok = {}, True
    for i, g in enumerate(groups):
        evals = sorted((evaluate(app, caps_by_group[g], brief, group_assets(g, groups, brief, assets), assets,
                                 handoff_in=i > 0, primary=g == primary, has_prompt=has_prompt)
                        for app in apps.values()), key=lambda e: -e["score"])
        per_group[g] = evals
        chain_ok = chain_ok and evals[0]["ok"]
    multi = None
    if len(groups) > 1 and chain_ok:
        steps = []
        for g in groups:
            best = per_group[g][0]
            if steps and steps[-1]["app_id"] == best["app_id"]:
                steps[-1]["caps"] += best["caps"]
                steps[-1]["groups"].append(g)
                steps[-1]["assets"] += [a for a in best["assets"] if a not in steps[-1]["assets"]]
                steps[-1]["unconfirmed"] += best["unconfirmed"]
            else:
                steps.append({**best, "groups": [g]})
        if len(steps) > 1:
            multi = {"kind": "multi", "steps": steps, "score": round(sum(s["score"] for s in steps) / len(steps), 3),
                     "unconfirmed": [u for s in steps for u in s["unconfirmed"]]}
    return {"groups": groups, "primary": primary, "singles": singles, "multi": multi, "per_group": per_group,
            "all_caps": all_caps, "used_assets": used}


def single_route(ev: dict, groups: list[str]) -> dict:
    return {"kind": "single", "steps": [{**ev, "groups": list(groups)}], "score": ev["score"], "unconfirmed": ev["unconfirmed"]}


def viable_routes(routes: dict) -> list[dict]:
    out = [single_route(e, routes["groups"]) for e in routes["singles"] if e["ok"]]
    if not out and routes["multi"]:
        out.append(routes["multi"])
    return out


def to_plan(route: dict, prompt: str, assets: dict) -> dict:
    apps, vocab = registry.apps(), registry.capabilities()
    rs = route["steps"]
    # The step that puts graphics on the filmed shots (it receives the footage from the step right before it).
    over = next((i for i, s in enumerate(rs) if i > 0 and "motion.overlay_graphics" in s["caps"]), None)
    steps = []
    for i, s in enumerate(rs):
        app = apps[s["app_id"]]
        inputs, instructions = [], None
        if i > 0 and "edit.assemble_clips" in s["caps"]:
            # Joining segments: every earlier step's clip arrives here, each with what it is for.
            for j in range(i):
                inp = {"source": f"step:s{j + 1}", "format": "video_file"}
                if over is not None and j == over - 1:
                    inp["use"] = (f"Filmed scenes. Use them for every shot step s{over + 1} does not return; "
                                  f"where s{over + 1} returns a shot with graphics, that version replaces this one.")
                elif over is not None and j == over:
                    inp["use"] = ("Animated scenes, plus the filmed shots with graphics placed on them. "
                                  "These graphic versions are final: use them instead of the plain shots.")
                else:
                    inp["use"] = "Scenes to place in story order."
                inputs.append(inp)
            instructions = ("Place every scene in story order. When two versions of the same shot arrive, keep the "
                            "one with graphics on it" + (f" (from step s{over + 1})" if over is not None else "") +
                            "; never use both, and never drop a scene.")
        elif i > 0:
            inp = {"source": f"step:s{i}", "format": "video_file"}
            if i == over and any("edit.assemble_clips" in t["caps"] for t in rs[i + 1:]):
                inp["use"] = ("Filmed scenes: place the requested graphics on the shots that need them and return "
                              "those shots, together with the animated scenes. Other shots need not be returned.")
            elif i == over:
                inp["use"] = "The video to place the graphics on."
            inputs.append(inp)
        if prompt.strip() and "text" in app["input_modalities"]:
            inputs.append({"source": "prompt", "format": "text"})
        for a in s["assets"]:
            inputs.append({"source": f"asset:{a}", "format": registry.MODALITY_FORMAT[assets[a]["modality"]]})
        step = {
            "step_id": f"s{i + 1}", "app_id": s["app_id"], "subtask_ids": [f"t_{g}" for g in s["groups"]],
            "capabilities_used": list(dict.fromkeys(s["caps"])),
            "responsibility": "; ".join(vocab[c].rstrip(".") for c in dict.fromkeys(s["caps"])) + ".",
            "inputs": inputs, "outputs": ["video_file"],
        }
        if instructions:
            step["instructions"] = instructions
        steps.append(step)
    return {"feasible": True, "steps": steps, "route_kind": route["kind"], "route_score": route["score"]}


def explain(ev: dict) -> str:
    p = ev["parts"]
    return (f"{ev['name']}: score {ev['score']:.2f} (limits {p['constraint_fit']:.2f}, inputs {p['role_fit']:.2f}, "
            f"profile trust {p['trust']:.2f}, focus {p['focus']:.2f})")
