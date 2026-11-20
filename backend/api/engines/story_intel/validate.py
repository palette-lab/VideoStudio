"""SceneValidationAgent, deterministic part (no LLM call): what can be checked by rule before the LLM review.

The narration lint (line length, digits in spoken text, time only moving forward after the opening) is adapted from
cinematic-skill's scripts/make_beats.py (MIT, (c) 2026 exploraX).
"""

import re

SCENE_FIELDS = ("title", "duration_s", "purpose", "what_happens", "actions", "framing", "camera_movement", "lighting",
                "start_state", "end_state", "props", "prompt")
PROMPT_FIELDS = ("subject", "environment", "action", "camera", "lighting", "motion", "continuity", "ending")
SCENE_MIN, SCENE_MAX = 3, 10
MAX_LINE_WORDS = 22                 # make_beats.py: one idea per line
WORDS_PER_S = 2.5
MIN_ACTION_WORDS = 20               # "rabbit running in a forest, cinematic camera" is not a prompt
CAMERA_ONLY = re.compile(r"^\W*(the )?camera (slowly )?(pans|orbits|circles|rotates|glides|zooms|pushes|pulls|tilts|"
                         r"tracks)\b[^.]*\.?\s*$", re.I)
SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\s*s\b")
CUT_WORDS = re.compile(r"\b(cut|time jump|later|meanwhile|next (day|morning|night)|new (place|location)|"
                       r"dissolve|fade|match cut|elsewhere|flashback|title)\b", re.I)
STOP = {"the", "a", "an", "of", "on", "in", "into", "with", "and", "to", "at", "is", "are", "its", "it", "from", "by",
        "his", "her", "their", "as", "he", "she", "they", "then", "while", "frame", "scene", "still", "same", "now"}


def _text(v) -> str:
    if isinstance(v, dict):
        return " ".join(_text(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return " ".join(_text(x) for x in v)
    return str(v or "")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}


def _similar(a: str, b: str) -> float:
    x, y = _words(a), _words(b)
    return len(x & y) / max(1, len(x | y))


def lint(plan: dict, target_s: int | None = None, category: str = "") -> list[dict]:
    """Every rule-checkable problem as {"scene", "area", "problem", "fix"}."""
    out: list[dict] = []

    def add(scene, area, problem, fix):
        out.append({"scene": scene, "area": area, "problem": problem, "fix": fix})

    scenes = [s for s in plan.get("scenes") or [] if isinstance(s, dict)]
    if not scenes:
        add(None, "scenes", "the plan has no scenes", "write the scene-by-scene plan")
        return out
    bible = plan.get("bible") or {}
    if not (bible.get("characters") or bible.get("objects")) or not bible.get("world"):
        add(None, "continuity", "the continuity bible is missing characters/objects or the world",
            "describe every character (look, size, wardrobe), the world and the key objects")
    total = 0.0
    for i, s in enumerate(scenes, start=1):
        miss = [f for f in SCENE_FIELDS if not _text(s.get(f)).strip()]
        if miss:
            add(i, "scenes", f"missing {', '.join(miss)}", "fill every scene field")
        try:
            d = float(s.get("duration_s") or 0)
        except (TypeError, ValueError):
            d = 0
        total += d
        if d and not SCENE_MIN <= d <= SCENE_MAX:
            add(i, "scenes", f"{d:g} s is outside one clip's {SCENE_MIN}-{SCENE_MAX} s",
                "split it or merge it so each scene is 3-10 s")
        prompt = s.get("prompt") if isinstance(s.get("prompt"), dict) else {}
        pmiss = [f for f in PROMPT_FIELDS if not str(prompt.get(f) or "").strip()]
        if pmiss:
            add(i, "prompts", f"the video prompt lacks {', '.join(pmiss)}", "write every part of the prompt")
        act = str(prompt.get("action") or "")
        if act and len(act.split()) < MIN_ACTION_WORDS:
            add(i, "prompts", f"the prompt's action is only {len(act.split())} words (\"{act[:60]}\")",
                "describe the movement, contact, reactions and how the action concludes")
        what = str(s.get("what_happens") or "")
        if CAMERA_ONLY.match(what) or CAMERA_ONLY.match(act):
            add(i, "scenes", "the scene is only a camera move", "give it a real event that moves the story")
        spans = SPAN.findall(str(s.get("actions") or ""))
        if len(spans) > 3:
            add(i, "scenes", f"{len(spans)} timed beats is more than one clip can perform",
                "keep at most 3 beats and move the rest to its own scene")
        audio = s.get("audio") if isinstance(s.get("audio"), dict) else {}
        line = str(audio.get("line") or "").strip()
        if line and str(audio.get("type") or "") != "none":
            n = len(line.split())
            if n > MAX_LINE_WORDS:
                add(i, "scenes", f"the {audio.get('type') or 'voice'} line has {n} words",
                    "split it (one idea per line, 4-18 words)")
            if d and n / WORDS_PER_S > d + 0.5:
                add(i, "scenes", f"{n} words cannot be spoken in {d:g} s", "shorten the line or lengthen the scene")
            if audio.get("type") == "narration" and re.search(r"\d", line):
                add(i, "scenes", f"digits in the spoken line \"{line[:60]}\"", "write numbers as words")
    # repeated scenes and repeated camera
    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            if _similar(scenes[i].get("what_happens"), scenes[j].get("what_happens")) > 0.6:
                add(j + 1, "scenes", f"scene {j + 1} repeats scene {i + 1}", "make it a new event or cut it")
    for i in range(1, len(scenes)):
        a, b = scenes[i - 1], scenes[i]
        if (str(a.get("framing")).strip().lower(), str(a.get("camera_movement")).strip().lower()) == \
                (str(b.get("framing")).strip().lower(), str(b.get("camera_movement")).strip().lower()):
            add(i + 1, "prompts", "same framing and camera movement as the previous scene", "vary the shot")
        if not CUT_WORDS.search(str(b.get("transition_in") or "")) and \
                not (_words(a.get("end_state")) & _words(b.get("start_state"))):
            add(i + 1, "continuity", f"scene {i + 1} does not start where scene {i} ended",
                "continue from the previous end_state or name the cut / time jump in transition_in")
    from .. import flow_budget
    for n in flow_budget.teleports(scenes, target_s or total):
        add(n, "continuity", f"scene {n} jumps to a new place with no way in shown (no walk, door, stairs or arrival)",
            f"show the move: end scene {n - 1} with the character leaving or heading there, or open scene {n} with "
            "them arriving (walking in, opening the door, coming up the stairs); add a 3-5 s movement shot if the "
            "length allows")
    if target_s and total and abs(total - target_s) > max(6, target_s * 0.2):
        add(None, "scenes", f"the scenes add up to {total:g} s for a {target_s} s film", "fit the durations")
    # documentaries: story time only moves forward after the opening (make_beats.py)
    if category == "documentary":
        max_year = None
        for i, s in enumerate(scenes, start=1):
            line = str((s.get("audio") or {}).get("line") or "") if isinstance(s.get("audio"), dict) else ""
            years = [int(y) for y in re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", line + " " + str(s.get("what_happens")))]
            for y in years:
                if i > 2 and max_year and y < max_year:
                    add(i, "facts", f"{y} steps back in time after {max_year}",
                        "after the opening, story time only moves forward")
                max_year = max(max_year or y, y)
    if category in ("documentary", "educational") and not plan.get("fact_notes"):
        add(None, "facts", "a factual film without fact notes", "list each claim with its source")
    for f in plan.get("fact_notes") or []:
        if isinstance(f, dict) and f.get("claim") and not str(f.get("source") or "").strip():
            add(None, "facts", f"no source for \"{str(f['claim'])[:60]}\"", "give its source or drop the claim")
    if category == "puzzle_mystery":
        pz = plan.get("puzzle") or {}
        if not (pz.get("clues") and pz.get("solution") and pz.get("question")):
            add(None, "puzzle", "the puzzle has no clear question, clues and solution", "state all three")
    return out


def scene_prompt_text(s: dict) -> str:
    """The scene's standalone video-generation prompt, A-L, as one block of text."""
    p = s.get("prompt") if isinstance(s.get("prompt"), dict) else {}
    order = (("subject", "SUBJECT"), ("environment", "ENVIRONMENT"), ("action", "ACTION"),
             ("interaction", "INTERACTION"), ("emotion", "EMOTION"), ("camera", "CAMERA"), ("lighting", "LIGHTING"),
             ("visual_design", "VISUAL DESIGN"), ("motion", "MOTION"), ("effects", "CINEMATIC EFFECTS"),
             ("continuity", "CONTINUITY"), ("ending", "ENDING"))
    return "\n".join(f"{label}: {str(p[k]).strip()}" for k, label in order if str(p.get(k) or "").strip())
