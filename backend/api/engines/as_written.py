"""The user's own scenes, used exactly as written: no enhancer, no Story Intelligence, no planner or critic rewrite.

A request counts as "as written" when it lists at least two numbered scenes ("Scene 1: ...", "Shot 2 - ...",
"S03 ...", "1. ...", "2) ...") or asks for it in words ("don't enhance", "as written", "exactly as I wrote", "use my
prompts as they are"). Each scene's text is the image prompt word for word; labelled lines inside a scene split it:

    Scene 1 (6s): A red fox on a snowy ridge at dawn, wide shot.
    Motion: slow push in as the fox turns its head.
    Narration: Every winter, the fox walks alone.

Image / Visual / Prompt -> the still; Motion / Camera / Action / Video -> the clip (else the scene text); Narration /
VO / Voice-over -> the spoken line; Duration or "(6s)" in the heading -> its length. Nothing is rewritten.
"""

import re

MAX_SCENES = 30

ASK = re.compile(r"\b(do ?n[o']?t|do not|no|without|never)\s+(enhanc\w*|improv\w*|chang\w*|rewrit\w*|edit\w*)\b|"
                 r"\b(as[- ]written|as[- ]is|word for word|verbatim|exactly as (i|we) (wrote|gave|typed|wrote it)|"
                 r"keep (my|the) (prompts?|scenes?|text) (the )?same|use my (prompts?|scenes?) (exactly|as they are))\b",
                 re.I)
HEAD = re.compile(r"^\s*(?:[#*_>\-\s]*)(?:(?:scene|shot|clip|frame)\s*#?\s*(\d{1,2})|s(\d{2})|(\d{1,2})\s*[.)])"
                  r"\s*(?:\(([^)]*)\))?\s*[:.\-–—)]?\s*(.*)$", re.I)
LABEL = re.compile(r"^\s*[*_]*(image|visual|visuals|prompt|still|motion|camera|action|video|movement|narration|vo|"
                   r"voice[- ]?over|voice|dialogue|line|duration|length|title)[*_]*\s*[:\-–]\s*(.*)$", re.I)
SECONDS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)\b", re.I)
IMAGE_KEYS = {"image", "visual", "visuals", "prompt", "still"}
MOTION_KEYS = {"motion", "camera", "action", "video", "movement"}
VOICE_KEYS = {"narration", "vo", "voice-over", "voiceover", "voice over", "voice", "dialogue", "line"}


def wanted(prompt: str) -> bool:
    """The user asked in words for no enhancement."""
    return bool(ASK.search(prompt or ""))


def parse(prompt: str) -> list[dict] | None:
    """The user's scenes ([{n, title, image, motion, line, duration}]) when the request is written scene by scene
    (two or more numbered scenes), or the whole text as one scene when they asked for no enhancement; else None."""
    text = (prompt or "").strip()
    if not text:
        return None
    blocks: list[dict] = []
    preamble: list[str] = []
    for raw in text.splitlines():
        m = HEAD.match(raw)
        if m and (m.group(5) or "").strip() != "" or (m and _bare_heading(raw)):
            n = int(m.group(1) or m.group(2) or m.group(3))
            blocks.append({"n": n, "head": (m.group(5) or "").strip(), "meta": m.group(4) or "", "lines": []})
        elif blocks:
            blocks[-1]["lines"].append(raw)
        else:
            preamble.append(raw)
    if len(blocks) < 2:
        if not wanted(text):
            return None
        return [_scene(1, "", "", text.splitlines())]  # one scene, the whole text as written
    return [_scene(i, b["head"], b["meta"], b["lines"]) for i, b in enumerate(blocks[:MAX_SCENES], start=1)]


def _bare_heading(raw: str) -> bool:
    """"Scene 3:" on its own line (the text follows on the next lines)."""
    return bool(re.match(r"^\s*[#*_>\-\s]*(scene|shot|clip|frame)\s*#?\s*\d{1,2}\s*(\([^)]*\))?\s*[:.\-–—]?\s*[*_]*\s*$",
                         raw, re.I))


def _scene(n: int, head: str, meta: str, lines: list[str]) -> dict:
    image, motion, voice, title = [], [], [], ""
    duration = None
    target = image
    body = ([head] if head else []) + lines
    for line in body:
        m = LABEL.match(line)
        if m:
            key, rest = m.group(1).lower().replace("_", " "), m.group(2).strip()
            if key in ("duration", "length"):
                d = SECONDS.search(rest) or re.search(r"(\d+(?:\.\d+)?)", rest)
                duration = float(d.group(1)) if d else duration
                continue
            if key == "title":
                title = rest
                continue
            target = image if key in IMAGE_KEYS else motion if key in MOTION_KEYS else voice
            if rest:
                target.append(rest)
            continue
        if line.strip():
            target.append(line.strip())
    d = SECONDS.search(meta or "") or SECONDS.search(head[:40] if head and not image else "")
    if duration is None and d:
        duration = float(d.group(1))
    img = " ".join(image).strip() or " ".join(motion).strip()
    return {"n": n, "title": title or (head[:60].rstrip(" .,:;") if head else f"Scene {n}"), "image": img,
            "motion": " ".join(motion).strip(), "line": " ".join(voice).strip().strip('"“”'),
            "duration": duration}


def story_intel(scenes: list[dict], total: float | None = None) -> dict:
    """The scenes as a Story Intelligence plan (shown and editable in the Story panel; the film engine renders it
    with mode "as_written", word for word)."""
    given = [s["duration"] for s in scenes if s.get("duration")]
    rest = [s for s in scenes if not s.get("duration")]
    share = max(3.0, ((float(total) - sum(given)) / len(rest))) if (total and rest) else 6.0
    plan_scenes = []
    for s in scenes:
        dur = float(s.get("duration") or share)
        plan_scenes.append({"n": s["n"], "title": s["title"], "duration_s": round(dur, 1),
                            "what_happens": s["image"], "actions": s["motion"] or s["image"],
                            "purpose": "your scene, as written",
                            "audio": {"type": "narration", "line": s["line"], "speaker": "Narrator"} if s["line"]
                            else {"type": "none", "line": ""},
                            "as_written": {"image": s["image"], "motion": s["motion"], "line": s["line"]}})
    total_s = round(sum(x["duration_s"] for x in plan_scenes), 1)
    return {"mode": "as_written",
            "classification": {"category": "as_written", "duration_s": total_s, "language": None},
            "plan": {"title": "Your scenes", "story_concept": "Your own scenes, used exactly as you wrote them: "
                                                              "nothing is enhanced or rewritten.",
                     "scenes": plan_scenes},
            "validation": {"rules": [], "issues": [], "passed": True}}
