"""The continuity controller: the state of every character, object and place, scene by scene.

It starts from the entities' starting state in the graph and applies each scene's events, in order, through their
"changes" (who is where, facing which way, holding what, doing what; an object's position, owner and state). For
every scene it gives the state the scene must START in (the previous scene's end) and must END in, plus the
completed / active / pending events. A change whose "from" does not match the current state is a discontinuity
(something moved or changed off screen). The text it writes goes into every image and clip prompt, so continuity
does not depend on the video model remembering anything.
"""

from __future__ import annotations

import copy
import re

from . import graph as G

_ATTR_CODE = {"location": G.LOCATION_DISCONTINUITY, "position": G.POSITION_DISCONTINUITY,
              "orientation": G.POSITION_DISCONTINUITY, "pose": G.POSITION_DISCONTINUITY,
              "appearance": G.CHARACTER_INCONSISTENCY}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def same(a: str, b: str) -> bool:
    """Loose match of two state descriptions ("at the door" ~ "by the front door")."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b or a == b or a in b or b in a:
        return True
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


class Continuity:
    def __init__(self, graph: dict):
        self.graph = graph
        self.events = G.by_id(graph)
        self.names = {e["id"]: e["name"] for k in graph.get("entities", {}).values() for e in k}
        self.kind = {e["id"]: kind for kind, k in graph.get("entities", {}).items() for e in k}
        self.state: dict[str, dict[str, str]] = {
            e["id"]: dict(e.get("start") or {}) for k in graph.get("entities", {}).values() for e in k}
        self.done: list[str] = []
        self.problems: list[dict] = []

    def apply(self, eid: str, scene: int | None = None) -> None:
        e = self.events.get(eid)
        if not e or eid in self.done:
            return
        for c in e["changes"]:
            cur = self.state.setdefault(c["entity"], {})
            now = cur.get(c["attr"], "")
            if c["from"] and now and not same(c["from"], now):
                kind = self.kind.get(c["entity"], "")
                code = _ATTR_CODE.get(c["attr"], G.OBJECT_INCONSISTENCY if kind == "objects"
                                      else G.POSITION_DISCONTINUITY)
                self.problems.append({"code": code, "scene": scene, "events": [eid],
                                      "problem": f"{self.names.get(c['entity'], c['entity'])}'s {c['attr']} is "
                                                 f"'{now}' but {eid} starts from '{c['from']}' - the change "
                                                 "between them is never shown"})
            if c["to"]:
                cur[c["attr"]] = c["to"]
        self.done.append(eid)

    def snapshot(self) -> dict[str, dict[str, str]]:
        return copy.deepcopy(self.state)

    def describe(self, snap: dict[str, dict[str, str]], only: set[str] | None = None) -> str:
        parts = []
        for eid, attrs in snap.items():
            if only is not None and eid not in only:
                continue
            bits = [f"{k}: {v}" for k, v in attrs.items() if v]
            if bits:
                parts.append(f"{self.names.get(eid, eid)} ({'; '.join(bits)})")
        return " | ".join(parts)


def run(graph: dict, scenes: list[dict]) -> dict:
    """Walk the scenes in order. Returns {"scenes": [per scene: start, end, events, completed, pending, brief],
    "problems": [...]}. Events no scene lists are applied where the graph puts them (before the next listed one),
    so a missing event still moves the state on and is reported once by the plan check, not again here."""
    ctl = Continuity(graph)
    order, _ = G.topo_order(graph)
    known = set(ctl.events)
    listed = {x for s in scenes for x in G.scene_events(s, known)}
    out = []
    for n, s in enumerate(scenes, start=1):
        mine = G.scene_events(s, known)
        # unlisted events that must already have happened before this scene's first event
        if mine:
            first = min(order.index(x) for x in mine if x in order) if any(x in order for x in mine) else len(order)
            for x in order[:first]:
                if x not in listed:
                    ctl.apply(x, n)
        start = ctl.snapshot()
        for x in mine:
            ctl.apply(x, n)
        end = ctl.snapshot()
        involved = {i for x in mine for i in ctl.events[x]["characters"] + ctl.events[x]["objects"]}
        pending = [x for x in order if x not in ctl.done]
        out.append({"n": n, "events": mine, "start": start, "end": end, "completed": list(ctl.done),
                    "pending": pending, "brief": brief(ctl, mine, start, end, involved)})
    return {"scenes": out, "problems": ctl.problems}


def brief(ctl: Continuity, mine: list[str], start: dict, end: dict, involved: set[str]) -> str:
    """The continuity instructions for one scene, for the image and clip prompts."""
    if not mine:
        return ""
    steps = []
    for k, x in enumerate(mine, start=1):
        e = ctl.events[x]
        acts = "; ".join(a for a in e["actions"] if a)
        steps.append(f"{k}. {e['description']}" + (f" ({acts})" if acts else "")
                     + (f" - done when: {e['done_when']}" if e["done_when"] else ""))
    only = involved or None
    s_txt, e_txt = ctl.describe(start, only), ctl.describe(end, only)
    return ("EVENTS THIS SCENE MUST SHOW, IN THIS ORDER, each one performed on screen from its start to its end "
            "(never only its result): " + " ".join(steps)
            + (f"\nSTATE AT THE START: {s_txt}" if s_txt else "")
            + (f"\nSTATE AT THE END: {e_txt}" if e_txt and e_txt != s_txt else ""))
