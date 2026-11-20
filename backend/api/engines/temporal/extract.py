"""The event extractor: the whole input read once, before any scene is written, into a temporal sequence graph."""

from __future__ import annotations

import json
import logging

from router_core import config, llm

from . import graph as G

log = logging.getLogger("palette_video.temporal")

EXTRACTOR = """You are the Temporal Sequence Analyst of a video studio. Read the user's ENTIRE input (and any source
text) and list EVERY event the video must show, in the order they happen, before anyone writes scenes. An event is
one thing that happens on screen: a physical action, a movement from one place to another, an interaction, a
reaction, a transformation of an object or material, a reveal, a line of dialogue that changes the situation, a
state change in a graphic. The input can be anything: a story, a one-line idea, an ad, a recipe, a lesson, a
puzzle, motion graphics, an animation, a reference-based brief.

Rules:
- Keep the user's level of detail: a detailed input gives many small events; never collapse several actions into one
  summary event, never jump to a result ("the meal is served") without the actions that lead to it.
- Fill in the intermediate actions a viewer must see for the next event to make sense (getting up before walking,
  opening before pouring, picking up before handing over, arriving before talking in a new place), as events of
  their own when they take visible time.
- Order: "requires" lists the events that must have happened first; "caused_by" the events that directly cause it (a
  reaction is caused by its trigger). Events that happen at the same time list each other in "parallel_with" and
  share their requirements. A branch that depends on a condition states it in "condition".
- "changes" is what the event changes in the world, as {"entity": id, "attr": one of location | position |
  orientation | pose | holding | activity | state | owner | appearance, "from": "...", "to": "..."}: who moves where,
  who picks up or puts down what, what opens, breaks, cooks, transforms, appears.
- "essential": false only for colour that could be condensed without breaking the story; everything the story needs
  to make sense is essential.
- "duration_s": the seconds a viewer needs to see it clearly (a glance 1-2, a step or a short action 2-4, crossing a
  room or a transformation 4-8, a spoken line its words / 2.5 + 1).
- "done_when": what a viewer can see once it is complete (the door is open and she is through it; the batter is
  smooth; the cube has become a sphere).
- Entities: every character (people, animals, creatures, mascots), every object that matters, every location, each
  with its look and its starting state.
Return ONLY JSON:
{"entities": {"characters": [{"id": "C1", "name": "...", "look": "face, hair, build, wardrobe or species, colours",
  "start": {"location": "L1", "position": "...", "orientation": "...", "pose": "...", "holding": "...",
  "activity": "..."}}],
 "objects": [{"id": "O1", "name": "...", "look": "...", "start": {"location": "L1", "position": "...", "owner": "...",
  "state": "..."}}],
 "locations": [{"id": "L1", "name": "...", "layout": "...", "light": "...", "time": "...", "weather": "..."}]},
 "events": [{"id": "E1", "seq": 1, "description": "one sentence, what visibly happens", "location": "L1",
  "characters": ["C1"], "objects": ["O1"], "requires": [], "caused_by": [], "parallel_with": [], "condition": "",
  "start_state": "the moment it begins", "end_state": "the moment it is complete",
  "actions": ["each physical step, in order"], "motion": "how bodies, objects or the graphic move",
  "camera": "what the camera must show for it to read", "duration_s": 3, "continuity": "what must stay the same",
  "done_when": "...", "essential": true, "changes": [{"entity": "C1", "attr": "location", "from": "L1", "to": "L2"}]}],
 "beginning": ["E1", ...], "middle": [...], "ending": [...]}"""


async def extract(prompt: str, cls: dict | None = None, source: str = "", files_note: str = "",
                  tokens: int = 16000) -> dict:
    """The normalized graph ({"entities", "events", "acts"}); an empty graph when the call fails (the film then
    goes on exactly as before, without the sequence checks)."""
    cls = cls or {}
    user = "\n\n".join(x for x in (
        f"INPUT (the user's words):\n{prompt.strip()}",
        f"UNDERSTANDING: {json.dumps({k: cls.get(k) for k in ('category', 'intent', 'subject', 'central_idea', 'story_type', 'tone', 'visual_style', 'duration_s')}, ensure_ascii=False)}"
        if cls else "",
        f"UPLOADED FILES: {files_note}" if files_note else "",
        source) if x)
    try:
        raw = await llm.chat_json(config.REASONING_MODEL, [{"role": "system", "content": EXTRACTOR},
                                                           {"role": "user", "content": user}],
                                  max_tokens=tokens, reasoning_effort="medium", stage="story.events")
    except Exception as e:  # noqa: BLE001 - best effort: without the graph the film is planned as before
        if type(e).__name__ == "RunCancelled":
            raise
        log.warning("event extraction failed: %s", e)
        return G.normalize({})
    return G.normalize(raw)
