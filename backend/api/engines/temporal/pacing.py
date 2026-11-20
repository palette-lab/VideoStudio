"""Duration from the events, not the events squeezed into a fixed length.

Each event carries its own estimate (the extractor's, from its actions, movement, dialogue and importance). The film
needs about the sum of its essential events plus a share for the rest. With no length from the user the film gets
the length its events need (within the app's limits); with the user's length, the essential events all stay and the
rest are condensed, and the plan says so. A scene whose events need more than one clip can hold is split into
connected sub-scenes by the writer (the plan check asks for it).
"""

from __future__ import annotations

SCENE_MAX = 10.0       # one AI clip (the longest the clip models are asked for)
MIN_TOTAL, MAX_TOTAL = 10, 120
OPTIONAL_SHARE = 0.5   # a non-essential event, condensed, still takes about half its time


def needed_seconds(graph: dict) -> float:
    ev = graph.get("events") or []
    must = sum(e["duration_s"] for e in ev if e["essential"])
    rest = sum(e["duration_s"] for e in ev if not e["essential"])
    return must + rest * OPTIONAL_SHARE


def pace(graph: dict, target_s: float | None, user_fixed: bool) -> dict:
    """{"needed_s", "total_s" (the length to plan for), "fixed", "note"}."""
    need = round(needed_seconds(graph))
    target = int(target_s or 0)
    if not graph.get("events"):
        return {"needed_s": 0, "total_s": target, "fixed": user_fixed, "note": ""}
    if not user_fixed:
        total = max(MIN_TOTAL, min(MAX_TOTAL, max(target, need)))
        note = (f"The events need about {need} s, so the film is planned at {total} s."
                + (" That is the app's longest film: keep every essential event, condense the rest."
                   if need > MAX_TOTAL else ""))
        return {"needed_s": need, "total_s": total, "fixed": False, "note": note}
    if need > target * 1.15:
        note = (f"The events need about {need} s but the film is {target} s (your length): every essential event "
                "stays, each one shorter, and the optional ones are condensed or folded into other scenes. "
                "Never drop an essential event or show only its result.")
    else:
        note = f"The events fit your {target} s."
    return {"needed_s": need, "total_s": target, "fixed": True, "note": note}


def overfull(graph: dict, scenes: list[dict]) -> list[dict]:
    """Scenes whose events need more than one clip can show: split into connected sub-scenes, nothing removed."""
    from . import graph as G
    ev, known = G.by_id(graph), set(G.by_id(graph))
    out = []
    for n, s in enumerate(scenes, start=1):
        ids = G.scene_events(s, known)
        need = sum(ev[x]["duration_s"] for x in ids)
        if len(ids) > 1 and need > SCENE_MAX + 1:
            out.append({"code": G.INCOMPLETE_ACTION, "scene": n, "events": ids,
                        "problem": f"scene {n} holds {len(ids)} events that need about {need:g} s, more than one "
                                   f"{SCENE_MAX:g} s clip can show"})
    return out
