"""The temporal sequence graph: every event a film must show, its order, dependencies and state changes.

An event is one thing that happens on screen (an action, an interaction, a transformation, a reveal). It has the
events it needs first ("requires"), the events that cause it ("caused_by", a subset of requires), the events it may
run alongside ("parallel_with"), an optional "condition", what changes in the world when it happens ("changes") and
how a viewer can tell it happened ("done_when"). Nothing here knows about any story type: the extractor fills the
graph from the user's input, and these functions only check and order it.
"""

from __future__ import annotations

import re

# The validation codes used everywhere (plan checks, clip checks, the UI).
PASS = "PASS"
MISSING_EVENT = "MISSING_EVENT"
WRONG_EVENT_ORDER = "WRONG_EVENT_ORDER"
ACTION_SKIPPED = "ACTION_SKIPPED"
POSITION_DISCONTINUITY = "POSITION_DISCONTINUITY"
CHARACTER_INCONSISTENCY = "CHARACTER_INCONSISTENCY"
OBJECT_INCONSISTENCY = "OBJECT_INCONSISTENCY"
LOCATION_DISCONTINUITY = "LOCATION_DISCONTINUITY"
CAUSALITY_ERROR = "CAUSALITY_ERROR"
INCOMPLETE_ACTION = "INCOMPLETE_ACTION"
CODES = (MISSING_EVENT, WRONG_EVENT_ORDER, ACTION_SKIPPED, POSITION_DISCONTINUITY, CHARACTER_INCONSISTENCY,
         OBJECT_INCONSISTENCY, LOCATION_DISCONTINUITY, CAUSALITY_ERROR, INCOMPLETE_ACTION)
# Codes that make a clip or a plan wrong (the others are shown, and fixed when a redraw happens anyway).
SEVERE = {MISSING_EVENT, WRONG_EVENT_ORDER, ACTION_SKIPPED, CAUSALITY_ERROR, INCOMPLETE_ACTION,
          CHARACTER_INCONSISTENCY, LOCATION_DISCONTINUITY}

MAX_EVENTS = 60
STATE_ATTRS = ("location", "position", "orientation", "pose", "holding", "activity", "state", "owner", "appearance")


def _ids(v) -> list[str]:
    if isinstance(v, (str, int)):
        v = [v]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _num(v, default: float) -> float:
    try:
        return max(0.5, float(v))
    except (TypeError, ValueError):
        return default


def normalize(raw: dict | None) -> dict:
    """A clean graph from the extractor's JSON: unique ids, known references only, numbers as numbers."""
    raw = raw if isinstance(raw, dict) else {}
    ents = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    entities = {}
    for kind in ("characters", "objects", "locations"):
        out = []
        for i, e in enumerate(ents.get(kind) or [], start=1):
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id") or f"{kind[0].upper()}{i}").strip()
            start = e.get("start") if isinstance(e.get("start"), dict) else {}
            out.append({**{k: v for k, v in e.items() if k not in ("id", "start")}, "id": eid,
                        "name": str(e.get("name") or eid), "start": {k: str(v) for k, v in start.items() if v}})
        entities[kind] = out
    known = {e["id"] for k in entities.values() for e in k}
    events, seen = [], set()
    for i, e in enumerate(raw.get("events") or [], start=1):
        if not isinstance(e, dict) or len(events) >= MAX_EVENTS:
            continue
        eid = str(e.get("id") or f"E{i}").strip()
        if eid in seen:
            eid = f"{eid}_{i}"
        seen.add(eid)
        changes = []
        for c in e.get("changes") or []:
            if isinstance(c, dict) and c.get("entity") and str(c.get("attr") or "") in STATE_ATTRS:
                changes.append({"entity": str(c["entity"]), "attr": str(c["attr"]),
                                "from": str(c.get("from") or ""), "to": str(c.get("to") or "")})
        events.append({
            "id": eid, "seq": int(_num(e.get("seq"), i)), "description": str(e.get("description") or "").strip(),
            "location": str(e.get("location") or ""), "characters": _ids(e.get("characters")),
            "objects": _ids(e.get("objects")), "requires": _ids(e.get("requires")),
            "caused_by": _ids(e.get("caused_by")), "parallel_with": _ids(e.get("parallel_with")),
            "condition": str(e.get("condition") or ""), "start_state": str(e.get("start_state") or ""),
            "end_state": str(e.get("end_state") or ""),
            "actions": [str(a) for a in (e.get("actions") or [])] if isinstance(e.get("actions"), list)
            else [str(e.get("actions"))] if e.get("actions") else [],
            "motion": str(e.get("motion") or ""), "camera": str(e.get("camera") or ""),
            "duration_s": _num(e.get("duration_s"), 3.0), "continuity": str(e.get("continuity") or ""),
            "done_when": str(e.get("done_when") or e.get("completion") or ""),
            "essential": e.get("essential") is not False, "changes": changes,
            "unknown_refs": [x for x in _ids(e.get("characters")) + _ids(e.get("objects")) if known and x not in known]})
    ids = {e["id"] for e in events}
    # A model may refer to an event by its number ("1", 1, "e1") instead of its id ("E1"): resolve those.
    alias = {x.lower(): x for x in ids}
    for e in events:
        alias.setdefault(str(e["seq"]), e["id"])
    for x in ids:
        m = re.fullmatch(r"[A-Za-z]+(\d+)", x)
        if m:
            alias.setdefault(m.group(1), x)

    def resolve(refs: list[str]) -> list[str]:
        return [alias.get(r.lower(), alias.get(r, r)) for r in refs]
    for e in events:
        e["requires"], e["caused_by"], e["parallel_with"] = (resolve(e["requires"]), resolve(e["caused_by"]),
                                                             resolve(e["parallel_with"]))
    for e in events:  # references to events that do not exist are dropped (and reported by check())
        e["missing_refs"] = [x for x in e["requires"] + e["caused_by"] if x not in ids]
        e["requires"] = [x for x in dict.fromkeys(e["requires"] + e["caused_by"]) if x in ids and x != e["id"]]
        e["caused_by"] = [x for x in e["caused_by"] if x in ids and x != e["id"]]
        e["parallel_with"] = [x for x in e["parallel_with"] if x in ids and x != e["id"]]
    events.sort(key=lambda e: e["seq"])
    for k, e in enumerate(events, start=1):
        e["seq"] = k
    return {"entities": entities, "events": events,
            "acts": {k: _ids(raw.get(k)) for k in ("beginning", "middle", "ending") if raw.get(k)}}


def by_id(graph: dict) -> dict[str, dict]:
    return {e["id"]: e for e in graph.get("events") or []}


def topo_order(graph: dict) -> tuple[list[str], list[str]]:
    """(an order that respects every dependency, the events caught in a cycle). Ties keep the story's own order."""
    ev = by_id(graph)
    indeg = {k: len(v["requires"]) for k, v in ev.items()}
    nxt: dict[str, list[str]] = {k: [] for k in ev}
    for k, v in ev.items():
        for r in v["requires"]:
            nxt[r].append(k)
    ready = sorted((k for k, d in indeg.items() if d == 0), key=lambda k: ev[k]["seq"])
    out = []
    while ready:
        k = ready.pop(0)
        out.append(k)
        for m in nxt[k]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
                ready.sort(key=lambda x: ev[x]["seq"])
    return out, [k for k in ev if k not in out]


def followers(graph: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {e["id"]: [] for e in graph.get("events") or []}
    for e in graph.get("events") or []:
        for r in e["requires"]:
            out.setdefault(r, []).append(e["id"])
    return out


def check(graph: dict) -> list[dict]:
    """Problems in the graph itself: a cycle (nothing can happen first), dependencies on unknown events, an event
    with no description or no way to tell it happened."""
    out = []
    _, cyc = topo_order(graph)
    if cyc:
        out.append({"code": CAUSALITY_ERROR, "events": cyc,
                    "problem": f"events {', '.join(cyc)} depend on each other in a circle"})
    for e in graph.get("events") or []:
        if e.get("missing_refs"):
            out.append({"code": CAUSALITY_ERROR, "events": [e["id"]],
                        "problem": f"{e['id']} needs {', '.join(e['missing_refs'])}, which is not an event"})
        if not e["description"]:
            out.append({"code": MISSING_EVENT, "events": [e["id"]], "problem": f"{e['id']} has no description"})
    return out


def scene_events(scene: dict, known: set[str]) -> list[str]:
    """The event ids a scene says it shows (the writer lists them in "events")."""
    raw = scene.get("events")
    if isinstance(raw, str):
        raw = re.findall(r"[A-Za-z]+\d+(?:_\d+)?", raw)
    out = []
    for x in raw or []:
        x = x.get("id") if isinstance(x, dict) else x
        x = str(x or "").strip()
        if x in known and x not in out:
            out.append(x)
    return out


def assign(graph: dict, scenes: list[dict]) -> dict[str, int]:
    """Event id -> the 1-based number of the first scene that shows it."""
    known = set(by_id(graph))
    where: dict[str, int] = {}
    for n, s in enumerate(scenes, start=1):
        for x in scene_events(s, known):
            where.setdefault(x, n)
    return where


def order_problems(graph: dict, scenes: list[dict]) -> list[dict]:
    """Every essential event shown, and every event after the events it needs (a reaction after its trigger)."""
    ev, where = by_id(graph), assign(graph, scenes)
    out = []
    known = set(ev)
    order_in_scene = {n: scene_events(s, known) for n, s in enumerate(scenes, start=1)}
    for e in graph.get("events") or []:
        if e["id"] not in where:
            if e["essential"]:
                out.append({"code": MISSING_EVENT, "scene": None, "events": [e["id"]],
                            "problem": f"{e['id']} ({e['description'][:90]}) is in no scene"})
            continue
        n = where[e["id"]]
        for r in e["requires"]:
            if r not in where:
                continue
            m = where[r]
            same_scene_late = m == n and order_in_scene[n].index(r) > order_in_scene[n].index(e["id"])
            if m > n or same_scene_late:
                code = CAUSALITY_ERROR if r in e["caused_by"] else WRONG_EVENT_ORDER
                out.append({"code": code, "scene": n, "events": [r, e["id"]],
                            "problem": f"{e['id']} ({e['description'][:60]}) comes before {r} "
                                       f"({ev[r]['description'][:60]}), which it needs"})
    return out


def coverage(graph: dict, scenes: list[dict]) -> dict:
    """For the UI: each event's scene (or None) and the totals."""
    where = assign(graph, scenes)
    rows = [{"id": e["id"], "description": e["description"], "scene": where.get(e["id"]), "essential": e["essential"],
             "requires": e["requires"], "parallel_with": e["parallel_with"], "condition": e["condition"],
             "done_when": e["done_when"], "duration_s": e["duration_s"]} for e in graph.get("events") or []]
    missing = [r["id"] for r in rows if r["scene"] is None and r["essential"]]
    return {"events": rows, "covered": sum(1 for r in rows if r["scene"]), "total": len(rows), "missing": missing}
