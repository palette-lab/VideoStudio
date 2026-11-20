"""Universal Temporal Intelligence: the film shows the complete sequence of events, not only their results.

  extract (one LLM read of the whole input)  ->  graph (events, dependencies, state changes)
  pacing  (length from the events)           ->  the writer's brief (every event, in order, listed per scene)
  plan_issues (coverage, order, causality, overfull scenes, state discontinuities) -> the writer's revision
  controller (character / object / place state per scene) -> each scene's continuity brief in its image and clip
  clip checks (which events a clip shows, in which order) -> verdict per scene, targeted retakes

Model independent: it only writes text and checks plans and frames; the render engine passes the previous scene's
last frame and the character references to whichever provider supports them.
"""

from __future__ import annotations

import re

from . import controller, graph, pacing
from .extract import extract
from .graph import CODES, PASS, SEVERE

__all__ = ["extract", "writer_block", "plan_issues", "annotate", "scene_events_text", "clip_block",
           "issue_code", "verdict", "CODES", "PASS", "SEVERE"]


def writer_block(g: dict, pace_info: dict | None = None) -> str:
    """What the scene writer gets: every event in order, and how to map them onto scenes."""
    if not g.get("events"):
        return ""
    lines = []
    for e in g["events"]:
        bits = [f"{e['id']}: {e['description']}"]
        if e["requires"]:
            bits.append(f"after {', '.join(e['requires'])}")
        if e["parallel_with"]:
            bits.append(f"together with {', '.join(e['parallel_with'])}")
        if e["condition"]:
            bits.append(f"if {e['condition']}")
        bits.append(f"~{e['duration_s']:g} s")
        if not e["essential"]:
            bits.append("optional")
        lines.append("- " + " | ".join(bits))
    return ("\n\nTEMPORAL SEQUENCE (extracted from the whole input before writing; this overrides any shorter "
            "summary of the story):\n" + "\n".join(lines)
            + ("\n" + pace_info["note"] if pace_info and pace_info.get("note") else "")
            + "\nSCENES MUST FOLLOW IT: every scene lists the ids it shows in \"events\" (in the order they happen "
              "in it), every essential event is in exactly one scene, no event comes before an event it needs, "
              "parallel events may share a scene. Each event is performed on screen from its start to its end - "
              "never only its result, never a reaction before its trigger, never a character in a new place "
              "without the move there, never an object somewhere new without the move shown. A scene whose events "
              "need more than one 10 s clip is split into connected sub-scenes (the next starts exactly where the "
              "last ended) - split, never drop.")


def plan_issues(g: dict, scenes: list[dict]) -> tuple[list[dict], list[dict]]:
    """(blocking problems for the writer's revision, notes) as validator issues {scene, area, problem, fix, code}."""
    if not g.get("events"):
        return [], []
    rules, notes = [], []
    if scenes and not any(graph.scene_events(s, set(graph.by_id(g))) for s in scenes):
        return [{"scene": None, "area": "sequence", "code": graph.MISSING_EVENT,
                 "problem": "no scene lists the events it shows", "fix": "give every scene its \"events\" ids"}], []
    fixes = {graph.MISSING_EVENT: "add it to the scene where it belongs (split a scene if it does not fit)",
             graph.WRONG_EVENT_ORDER: "move it after the event it needs",
             graph.CAUSALITY_ERROR: "show the cause first, then the reaction",
             graph.INCOMPLETE_ACTION: "split the scene into connected sub-scenes; keep every event"}
    for p in graph.order_problems(g, scenes) + pacing.overfull(g, scenes):
        rules.append({"scene": p["scene"], "area": "sequence", "code": p["code"], "problem": p["problem"],
                      "fix": fixes.get(p["code"], "fix the sequence")})
    for p in graph.check(g) + controller.run(g, scenes)["problems"]:
        notes.append({"scene": p.get("scene"), "area": "continuity", "code": p["code"], "problem": p["problem"],
                      "fix": "show the change on screen, or start from the state the previous event left"})
    return rules, notes


def annotate(g: dict, scenes: list[dict], pace_info: dict | None = None) -> dict:
    """The temporal record kept on the plan: graph, pacing, coverage and each scene's continuity."""
    if not g.get("events"):
        return {}
    walk = controller.run(g, scenes)
    rules, notes = plan_issues(g, scenes)
    return {"graph": g, "pacing": pace_info or {}, "coverage": graph.coverage(g, scenes),
            "scenes": [{"n": s["n"], "events": s["events"], "brief": s["brief"], "start": s["start"],
                        "end": s["end"], "pending": s["pending"]} for s in walk["scenes"]],
            "issues": rules + notes}


def scene_events_text(record: dict, n: int) -> list[dict]:
    """Scene n's events as the clip check needs them: [{id, description, done_when}]."""
    ev = graph.by_id(record.get("graph") or {})
    row = next((s for s in record.get("scenes") or [] if s.get("n") == n), None)
    return [{"id": x, "description": ev[x]["description"], "done_when": ev[x]["done_when"],
             "actions": ev[x]["actions"]} for x in (row or {}).get("events") or [] if x in ev]


def clip_block(events: list[dict]) -> str:
    """The part of the clip check that verifies the sequence."""
    if not events:
        return ""
    steps = "; ".join(f"{k}. {e['description']}" + (f" (done when: {e['done_when']})" if e.get("done_when") else "")
                      for k, e in enumerate(events, start=1))
    return ("\nSEQUENCE CHECK - this clip must show these events, in this order, each performed from start to "
            f"end: {steps}. For each problem put its code in brackets right after MAJOR/MINOR: [MISSING_EVENT] an "
            "event is not shown; [WRONG_EVENT_ORDER] they happen in another order; [ACTION_SKIPPED] it jumps to the "
            "result without the action; [INCOMPLETE_ACTION] the action starts but does not finish; "
            "[CAUSALITY_ERROR] a reaction before its cause; [POSITION_DISCONTINUITY] someone or something jumps "
            "position; [CHARACTER_INCONSISTENCY] / [OBJECT_INCONSISTENCY] / [LOCATION_DISCONTINUITY] something "
            "changes identity or place. Missing, skipped, out-of-order, incomplete and causality problems are MAJOR.")


_CODE = re.compile(r"\[(" + "|".join(CODES) + r")\]")


def issue_code(issue: str) -> str | None:
    m = _CODE.search(str(issue))
    return m.group(1) if m else None


def verdict(issues: list[str] | None, checked: bool = True) -> dict:
    """PASS / FAIL (with codes) / UNCERTAIN (the vision check could not run: review it yourself)."""
    if not checked:
        return {"status": "UNCERTAIN", "codes": [], "note": "the clip could not be checked automatically - "
                                                            "watch this scene"}
    codes = sorted({c for c in (issue_code(i) for i in issues or []) if c})
    return {"status": "FAIL" if codes else PASS, "codes": codes}
