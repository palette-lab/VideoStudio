"""Movie-like flow within the film's length (the user's number, or the length the app chose).

A film made of separate AI clips jumps from place to place unless the plan spends some of its time on the moves
between them: the walk up the corridor, the knock, the door opening, the stairs. How much time that gets depends on
the length: a 15 s film folds the move into the first beat of a scene; a 90 s film gives each arrival its own shot.
Used by every story writer (Story Intelligence and the film engine's planners) and checked by the plan linter.
"""

import re

# Words that show a move on screen (arriving, leaving, going between places).
MOVE_WORDS = re.compile(
    r"\b(enter\w*|walk\w*|step\w*|arriv\w*|door\w*|doorway|threshold|knock\w*|stair\w*|climb\w*|descend\w*|"
    r"corridor|hallway|hall|lift|elevator|leav\w*|exit\w*|run\w*|rush\w*|hurr\w*|drive\w*|ride\w*|approach\w*|"
    r"comes? in|goes? (in|out|up|down)|heads? (in|out|to|toward)|follow\w*|track\w*|gate|path|road|street)\b", re.I)


def tier(total_s: float) -> str:
    total = float(total_s or 0)
    return "short" if total < 25 else "medium" if total <= 60 else "long"


def flow_budget(total_s: float) -> str:
    """The flow rules for this length, with the seconds to spend on moves between places."""
    total = int(round(float(total_s or 0)))
    if not total:
        return ""
    t = tier(total)
    move_s = {"short": 0, "medium": round(total * 0.15), "long": round(total * 0.2)}[t]
    head = (f"\nFLOW WITHIN THE LENGTH ({total} s, the user's length or the length chosen for this story - the scene "
            "durations must add up to it): the film flows like a movie, never teleporting between places. ")
    if t == "short":
        return head + ("This film is short: no separate travel scenes. Fold each move INTO the first beat of the scene "
                       "where it happens (0-1.5 s: she pushes the door open and steps in; then the main action), keep "
                       "to at most 2 places, and carry the motion across the cuts (the hand that turns the handle, "
                       "then the room it opens on).")
    if t == "medium":
        return head + (f"Spend about {move_s} s of it on moves between places: each time the story changes place, ONE "
                       "short movement shot of 3-5 s that shows the way in (walking the corridor or up the stairs, "
                       "knocking, pushing the door open and taking in the room) or the way out. Inside one place, "
                       "keep the geography with a wider shot when someone crosses the room. At most 3 places.")
    return head + (f"Spend about {move_s} s of it on moves and arrivals: an establishing shot of each new place (the "
                   "building, the street, the room from the doorway), the journey between places as its own shots "
                   "(the stairs, the corridor, the car, the walk up the path), the door opening and the first look "
                   "around before the talk starts, and characters leaving on screen. Movement shots follow the "
                   "character in one continuous move (tracking from behind or leading them).")


def _place(s: dict) -> str:
    for k in ("environment", "location", "place", "setting"):
        v = s.get(k)
        if isinstance(v, str) and v.strip():
            return v
    pr = s.get("prompt") if isinstance(s.get("prompt"), dict) else {}
    return str(pr.get("environment") or "")


_STOP = {"the", "a", "an", "of", "in", "on", "at", "with", "and", "to", "by", "his", "her", "their", "its", "behind",
         "front", "late", "night", "day", "morning", "evening", "same", "inside", "outside", "near", "under", "over"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", str(text).lower()) if len(w) > 2 and w not in _STOP}


def place_changed(a: dict, b: dict) -> bool:
    """Do two consecutive scenes happen in clearly different places? (Little word overlap between them.)"""
    pa, pb = _words(_place(a)), _words(_place(b))
    if len(pa) < 2 or len(pb) < 2:
        return False
    return len(pa & pb) / min(len(pa), len(pb)) < 0.25


def _text(s: dict, *keys: str) -> str:
    out = []
    for k in keys:
        v = s.get(k)
        out.append(" ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v or ""))
    return " ".join(out)


def teleports(scenes: list[dict], total_s: float) -> list[int]:
    """1-based numbers of scenes that open in a new place without any move into it on screen (neither the scene's
    start nor the previous scene's end shows walking, a door, stairs, an arrival or a departure). Not checked for a
    short film, which may simply cut."""
    if tier(total_s) == "short":
        return []
    out = []
    for i in range(1, len(scenes)):
        a, b = scenes[i - 1], scenes[i]
        if not place_changed(a, b):
            continue
        shown = (MOVE_WORDS.search(_text(b, "transition_in", "what_happens", "actions", "action", "start_state"))
                 or MOVE_WORDS.search(_text(a, "transition_out", "end_state")))
        if not shown:
            out.append(i + 1)
    return out
