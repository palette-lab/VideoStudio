"""Short Drama Director: vertical micro-drama episodes and whole series, from a short idea or a novel / story text.

Method: the pipeline idea of huobao-drama (github.com/chatfire-AI/huobao-drama) - script first, then the cast,
locations and props, then shots and prompts. Only the idea is used: huobao-drama is CC BY-NC-SA 4.0, so none of
its prompts, skills or code are copied here; every rule below is written for this app. What this app adds: the
character reference sheets (passport / front / back), one AI clip per shot, and every spoken line lip-synced in
the speaker's own voice (Kokoro, a different voice per character), episode after episode with the same cast.
"""

import json
import re

# The classifier's category for this director (agents.CATEGORIES) and the enhancer's agent key.
CATEGORY = "short_drama"
MAX_SPEAKERS = 4
MAX_LINE_WORDS = 20                  # about 8 s spoken: one lip-synced shot
SOURCE_CHARS = 30000                 # novel text handed to the writer for one episode

# A request for a drama, an episode or a series (used to keep an uploaded novel away from the Document Explainer).
ASKS = re.compile(r"\b(short[- ]?dramas?|micro[- ]?dramas?|mini[- ]?dramas?|dramas?|dramati[sz]e|web ?series|"
                  r"episodes?|screenplay|soap opera|[kc]-?dramas?|telenovela)\b", re.I)
NEXT = re.compile(r"\b(next|continue|following|another|part)\b.*\b(episode|ep|part|chapter)\b|"
                  r"\b(episode|ep|part)\s*(\d+)\b|\bcontinue( the)? (story|series|drama)\b", re.I)

RULES = """

SHORT DRAMA DIRECTOR (this overrides the general rules above wherever they differ). The film is ONE episode of a
vertical short drama (a micro-drama): characters talk, and the story runs on dialogue, emotion and reversals.

WORK IN THIS ORDER
1. SCRIPT FIRST, as "script": a list of {"scene": "S01", "heading": "INT. or EXT. - place - a specific time
   (dusk, late night, early morning; never just 'day')", "action": "what we see and hear, no camera words",
   "dialogue": [{"speaker": "exact character name", "emotion": "...", "line": "..."}]}.
   From a novel or story text (SOURCE TEXT below): keep its plot, names, relationships and order of events; turn
   narration and inner thoughts into actions, looks and spoken lines (at most one short voice-over line per
   episode); cut what does not move this episode forward.
2. CAST, LOCATIONS, PROPS in the bible: bible.characters = every character who speaks or returns, each
   {"name", "age", "gender", "voice_gender": "female | male", "face", "hair", "build", "wardrobe",
   "personality", "how they talk"}. At most 4 speaking characters and 3 locations in one episode. Recurring props
   in bible.objects, the locations in bible.world.
3. SHOTS: turn the script into scenes (the scene schema above), one AI clip each:
   - Every spoken line is its OWN scene: audio {"type": "dialogue", "speaker": the exact name from
     bible.characters, "line": at most 20 words, "emotion", "delivery", "est_seconds": words / 2.5}. That shot
     frames THE SPEAKER: medium close-up or close-up, face turned three-quarters to the camera, mouth fully
     visible with nothing in front of it. Anyone else in frame is seen from behind, out of focus or at the edge -
     never a second face toward the camera. The actions stay small (a look, a step, a hand tightening on a cup) so
     the lips can be synced to the voice. Name the speaker in "characters": ["<name>"].
   - Between lines: reaction shots (a face reacting, no speech) and action or establishing shots, audio type
     "none" (or one short "narration" line in a character's voice for an inner thought).
   - duration_s of a dialogue scene = the line's spoken length + 1 s, at least 3 s.
   - Scene titles in dialogue scenes name the speaker ("Mira: You knew?").

SHORT-DRAMA CRAFT
- Hook within the first 3 seconds: a confrontation, a secret, a slap, a shocking line.
- Conflict in every scene: someone wants something and someone stands in the way. Status flips (the humiliated
  one turns the tables); one clear reversal in the middle of the episode.
- End on a CLIFFHANGER (a reveal, a threat, an arrival, a question) unless this is the series' final episode -
  then a satisfying payoff. Write it in "cliffhanger".
- Mostly dialogue: at least half the scenes are spoken lines. Natural, sharp spoken language; no speeches, no
  narrator explaining what we can see.
- Vertical 9:16 unless the format says otherwise; faces big in frame.

SERIES: when the user asks for several episodes, or the SOURCE TEXT holds more story than this episode's length
can tell, plan the whole series in "series": {"title", "episodes": [{"n", "title", "synopsis", "cliffhanger"}]}
(2-12 episodes of 45-90 s each) and write scenes ONLY for the current episode (episode 1 unless told otherwise).
A single short idea is one episode: "series": null.
Add to the JSON: "episode": n, "cliffhanger": "...", "script": [...], "series": {...} or null."""


def applies(cls: dict) -> bool:
    return (cls or {}).get("category") == CATEGORY


def asks_for_drama(prompt: str) -> bool:
    return bool(ASKS.search(prompt or ""))


def system(base: str) -> str:
    return base + RULES


def source_note(documents: list[dict]) -> str:
    """The uploaded novel / story text, as the episode's source (not research: it is adapted, not cited)."""
    text = "\n\n".join(f"=== {d.get('filename') or 'document'} ===\n{d.get('text') or ''}" for d in documents or [])
    if not text.strip():
        return ""
    cut = len(text) > SOURCE_CHARS
    return ("SOURCE TEXT (adapt this story; keep its plot, names and relationships"
            + ("; it is longer than shown - plan the series from what is here" if cut else "") + "):\n"
            + text[:SOURCE_CHARS])


def wants_episode(message: str) -> int | None:
    """The episode a follow-up asks for: its number, 0 for 'the next one', None when it asks for something else."""
    m = NEXT.search(message or "")
    if not m:
        return None
    n = re.search(r"\b(?:episode|ep|part)\s*(\d+)\b", message, re.I)
    return int(n.group(1)) if n else 0


def series_note(series: dict, n: int) -> str:
    """What the writer of episode n gets: the series plan, the locked cast and what already happened."""
    eps = [e for e in series.get("episodes") or [] if isinstance(e, dict)]
    this = next((e for e in eps if int(e.get("n") or 0) == n), None)
    done = [e for e in eps if int(e.get("n") or 0) < n]
    cast = [{k: c.get(k) for k in ("name", "desc", "voice_gender")} for c in series.get("characters") or []]
    return "\n".join([
        f"THIS IS EPISODE {n} of {len(eps) or '?'} of the series \"{series.get('title') or ''}\".",
        "THE CAST IS LOCKED: the same characters, names, faces, hair, builds and voice_gender as below, described "
        "word for word in bible.characters (wardrobe may change only when the story moves to a new day). New "
        "characters only when this episode needs them.",
        "CAST: " + json.dumps(cast, ensure_ascii=False),
        "STORY SO FAR: " + json.dumps([{k: e.get(k) for k in ("n", "title", "synopsis", "cliffhanger")} for e in done],
                                      ensure_ascii=False),
        "THIS EPISODE: " + json.dumps(this or {"n": n, "synopsis": "continue the story from the last cliffhanger"},
                                      ensure_ascii=False),
        "Open by paying off the previous episode's cliffhanger. \"episode\": " + str(n)
        + ", and \"series\": the same series plan (you may refine later episodes' synopses)."])


def remember_series(plan: dict, previous: dict | None = None) -> dict | None:
    """The series record kept on the run: the plan of episodes, the locked cast and the episode just written.
    The film engine adds the drawn character references ("cast") after the episode is made."""
    raw = plan.get("series") if isinstance(plan.get("series"), dict) else None
    if not raw and not previous:
        return None
    base = dict(previous or {})
    if raw:
        base["title"] = raw.get("title") or base.get("title") or plan.get("title")
        if raw.get("episodes"):
            base["episodes"] = [e for e in raw["episodes"] if isinstance(e, dict)]
    chars = []
    for c in (plan.get("bible") or {}).get("characters") or []:
        if isinstance(c, dict) and c.get("name"):
            desc = "; ".join(f"{k}: {v}" for k, v in c.items() if k not in ("name", "voice_gender") and str(v or "").strip()
                             and not isinstance(v, (dict, list)))
            chars.append({"name": str(c["name"]), "desc": desc[:900],
                          "voice_gender": "male" if str(c.get("voice_gender")).lower() == "male" else "female"})
    known = {c["name"].lower() for c in chars}
    base["characters"] = chars + [c for c in base.get("characters") or [] if c["name"].lower() not in known]
    try:
        base["current"] = int(plan.get("episode") or base.get("current") or 1)
    except (TypeError, ValueError):
        base["current"] = 1
    return base


def next_number(series: dict, asked: int) -> int:
    return asked if asked else int(series.get("current") or 1) + 1


def characters(plan: dict) -> list[dict]:
    return [c for c in (plan.get("bible") or {}).get("characters") or [] if isinstance(c, dict) and c.get("name")]


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def speaker_of(s: dict) -> str:
    a = s.get("audio") if isinstance(s.get("audio"), dict) else {}
    return str(a.get("speaker") or "").strip() if a.get("type") == "dialogue" and a.get("line") else ""


def is_final(plan: dict) -> bool:
    eps = ((plan.get("series") or {}) if isinstance(plan.get("series"), dict) else {}).get("episodes") or []
    try:
        return not eps or int(plan.get("episode") or 1) >= len(eps)
    except (TypeError, ValueError):
        return True


def lint(plan: dict) -> list[dict]:
    """The rule-checkable parts of a short-drama episode."""
    out = []

    def add(scene, problem, fix):
        out.append({"scene": scene, "area": "drama", "problem": problem, "fix": fix})

    scenes = [x for x in plan.get("scenes") or [] if isinstance(x, dict)]
    cast = {_key(c["name"]): c for c in characters(plan)}
    if not plan.get("script"):
        add(None, "no script: a short drama is written as a script before its shots",
            'add "script": the episode as scenes with headings, action and dialogue')
    spoken = [i for i, s in enumerate(scenes, start=1) if speaker_of(s)]
    if scenes and not spoken:
        add(None, "no dialogue: a short drama runs on spoken lines", "give the characters their lines, one per scene")
    elif scenes and len(spoken) * 2 < len(scenes) - 1:
        add(None, f"only {len(spoken)} of {len(scenes)} scenes have dialogue",
            "make about half the scenes spoken lines (reaction and action shots between them)")
    speakers = {_key(speaker_of(scenes[i - 1])) for i in spoken}
    if len(speakers) > MAX_SPEAKERS:
        add(None, f"{len(speakers)} speaking characters", f"at most {MAX_SPEAKERS} speak in one episode")
    for i in spoken:
        s = scenes[i - 1]
        who = speaker_of(s)
        if _key(who) not in cast:
            add(i, f"the speaker '{who}' is not in bible.characters", "use the exact name of a bible character")
        line = str((s.get("audio") or {}).get("line") or "")
        if len(line.split()) > MAX_LINE_WORDS:
            add(i, f"the line has {len(line.split())} words (one lip-synced shot holds about {MAX_LINE_WORDS})",
                "split it into two shots or shorten it")
    for c in cast.values():
        if str(c.get("voice_gender") or "").lower() not in ("female", "male"):
            add(None, f"{c['name']} has no voice_gender", 'give every character "voice_gender": "female" or "male"')
    if not is_final(plan) and not str(plan.get("cliffhanger") or "").strip():
        add(None, "the episode has no cliffhanger", 'end on a reveal, threat or question and write it in "cliffhanger"')
    return out


def voices(plan: dict, language: str) -> dict[str, dict]:
    """A different local voice for every speaking character ({name: {"gender", "voice"}}); characters of the same
    gender get different voices while the language has them."""
    from ..presenter_engine import kokoro_pool
    pool = kokoro_pool(language)
    taken: dict[str, int] = {"female": 0, "male": 0}
    out = {}
    for c in characters(plan):
        g = "male" if str(c.get("voice_gender")).lower() == "male" else "female"
        names = (pool or {}).get(g) or []
        out[str(c["name"])] = {"gender": g, "voice": names[taken[g] % len(names)] if names else None}
        taken[g] += 1
    return out


def brief_line(plan: dict) -> str:
    """How the film is made, said plainly for the router and the chat."""
    n = len([s for s in plan.get("scenes") or [] if isinstance(s, dict)])
    talk = len([s for s in plan.get("scenes") or [] if isinstance(s, dict) and speaker_of(s)])
    ep = plan.get("episode") or 1
    total = len(((plan.get("series") or {}) if isinstance(plan.get("series"), dict) else {}).get("episodes") or [])
    return (f"Production: a vertical short drama{f', episode {ep} of {total}' if total else ''} - {n} shots, each ONE "
            f"AI clip; {talk} spoken lines, each in its speaker's own voice and lip-synced on camera by the filming "
            "app; music under it; subtitles as an editable text layer. No separate graphics, charts or text pass."
            + (f" Ends on: {plan['cliffhanger']}" if plan.get("cliffhanger") else ""))
