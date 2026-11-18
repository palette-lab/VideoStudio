"""Cinematic Documentary Director: the cinematic-skill method (github.com/Moh4696/cinematic-skill, MIT, (c) 2026
exploraX) as a master prompt (prompts/cinematic_documentary_master.md) for documentaries and presentations of real
facts - history, biography, true crime, real places, discoveries, brand and founder stories, narrated factual
explainers that are not made as 3D motion graphics.

The skill draws its films in code; here each scene is one AI clip, so the method becomes: one designed world (an
evidence wall, a studio table, a blueprint...), one continuous camera across it, ONE hero per narration line, the
visual hit on a spoken word, no AI face for a real person, and every readable word (dates, names, stamps, numbers)
as the editable text layer on top of the film (text_layer.py), never drawn by the image or video model.
"""

import re
from pathlib import Path

MASTER = (Path(__file__).resolve().parent.parent / "prompts" / "cinematic_documentary_master.md").read_text(encoding="utf-8")

LOOKS = ("wall", "slate", "charcoal", "paper", "studio", "blueprint", "night")
PRESENTATION = {"educational", "product", "other"}  # factual presentations in these categories use the method too
MAX_TYPED_WORDS = 6

RUNTIME = """

---

## THIS APP (runtime: how the method runs here - the master prompt above decides everything else)

You write the plan as the StoryArchitect format below asks, applying the master prompt to every field:
- style.visual_style names the LOOK ({LOOKS}) and the world; style.look gives the grade (archive black-and-white /
  sepia / natural colour), the ONE accent colour and the board texture. bible.world is the board: its surface,
  clusters (one per section) and where each sits, the map if the story travels.
- Each scene is one narration line or two (one take of the camera move): one hero, one camera move from the
  grammar (push in / pull back / whip / hold with drift), the spotlight on the hero.
- prompt.subject = the hero item; prompt.environment = the board around it (dimmed); prompt.action = the items
  animating on (pop, tear in, slam, draw on, roll up, string pulling taut) with the camera landing about 0.3 s after
  the hero appears; prompt.visual_design = the look, grade, paper and print textures, grey rows on documents, blank
  stamp and label shapes where the text layer will sit. NO letters, numbers or readable words in any prompt.
- bible.characters: real people appear ONLY as an uploaded archive photo, a document with their name (as a blank
  name bar), hands, tools, silhouette or back view - say which in every scene they are in; never a generated face.
- audio.line: the narration line, 4-18 words, key word last, numbers as words. sfx: the items' own sounds from the
  master prompt plus at most two booms in the film.

THE HOST (the user's direction): the film is PRESENTED by a host who speaks on camera in the FIRST scene and the
LAST scene, and narrates every board scene in between with the same voice.
- Scene 1 (the cold open, said to camera: the most vivid moment or question) and the last scene (the close: pay off
  the opening, then the line that lands the meaning) are host scenes: "host": true, audio.type "dialogue",
  audio.speaker "Host", audio.line = what they say to camera (1-2 sentences, at most 30 words, numbers as words).
  A host scene shows only the host (hero "the host", no board items, no archive, overlay_text '' or the title).
- Every other scene is a board scene: "host": false, audio.type "narration", audio.speaker "Host (voice-over)".
- The host is a FICTIONAL presenter, never one of the real people in the story. Add to the plan "host": {"look":
  "apparent age range, build, hair, skin tone, wardrobe that suits the subject", "setting": "where they stand, e.g.
  a dim archive room with the board softly behind them", "voice_gender": "female | male"}.

Add these fields to EVERY scene:
 "host": true for the first and last scene, false for the board scenes,
 "hero": "the ONE thing the camera is on (a print, a document, a stamp, a number, a pin on the map)",
 "hit_word": "the word of audio.line the visual hit lands on (exactly as spoken)",
 "items": ["each board item that appears or animates in this scene, in order"],
 "cluster": "which cluster of the board this is (e.g. 'Kill Devil Hills, 1903')",
 "overlay_text": "the 1-6 words typed on screen in this scene (a date, a name, a place, a stamp word, a big number);
  '' when the scene shows no typed words. Never the same phrase as the narration word for word.",
 "typography_style": "big condensed caps; white or cream on dark looks, dark on light; the accent colour for what
  matters; where it sits (the clean space left in the frame)"
{ARCHIVE}And to the plan: "look": one of {LOOKS}, "accent": "#RRGGBB", "fact_notes": [one per claim with its source].
"""

ARCHIVE_FIELD = """ "archive": ["A1"] - the ARCHIVE PHOTOS this scene pins as prints on the board ([] for none). Use a real
  photo wherever the scene shows a real person, place or event it really shows; caption nothing it does not show
  (a photo of the machine being readied is not the crash). The photo is reproduced as it is, never redrawn.
"""


# Words that ask for the host + board method instead of the VOX Documentary: someone on camera, or real archive photos.
HOSTED = re.compile(r"\b((with|by|and|has|have|use|using|needs?) (a|an|the|one|our) (\w+ )?(host|presenter|anchor)|"
                    r"hosted by|presented by|presenters?|on[- ]camera|talking head|someone (presenting|explaining)|"
                    r"a person (presenting|explaining)|(real|archive|archival|historical) (photos?|photographs?|"
                    r"pictures?|images?|footage))\b", re.I)


def wants_host(prompt: str) -> bool:
    """The request asks for a person on camera or real archive photos: the cinematic host + board method makes it."""
    return bool(HOSTED.search(prompt or ""))


def applies(cls: dict) -> bool:
    """Documentaries always; a factual presentation in another category when it is not made as 3D motion graphics."""
    if cls.get("production") == "motion_graphics" or cls.get("agent") in ("motion", "food", "presenter"):
        return False
    cat = cls.get("category")
    return cat == "documentary" or (cat in PRESENTATION and bool(cls.get("is_factual")))


def system(base: str, archive: bool = False) -> str:
    """The StoryArchitect prompt with the master prompt and this app's runtime section after it."""
    return base + "\n\n" + MASTER + (RUNTIME.replace("{LOOKS}", " | ".join(LOOKS))
                                     .replace("{ARCHIVE}", ARCHIVE_FIELD if archive else ""))


def archive_note(photos: list[dict]) -> str:
    """The free archive photos found for this film, for the architect (ids, what they show, date, licence)."""
    rows = [f"{p['id']}: {p['what'][:220]}" + (f" ({p['date']})" if p.get("date") else "") + f" [{p['license']}]"
            for p in photos]
    return ("ARCHIVE PHOTOS (real photos from Wikimedia Commons, free to use with credit; pin the ones that fit as "
            "prints, by id; ignore the ones that do not fit the story):\n" + "\n".join(rows))


def _line(s: dict) -> str:
    a = s.get("audio") if isinstance(s.get("audio"), dict) else {}
    return str(a.get("line") or "") if a.get("type") != "none" else ""


def _norm(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (t or "").lower()))


def lint(plan: dict, archive_ids: set[str] | None = None) -> list[dict]:
    """The method's rule-checkable parts: one hero per scene, the hit on a spoken word, short typed words that are
    not also spoken word for word, a look from the set."""
    out = []

    def add(scene, problem, fix):
        out.append({"scene": scene, "area": "documentary", "problem": problem, "fix": fix})

    scenes = [x for x in plan.get("scenes") or [] if isinstance(x, dict)]
    if len(scenes) >= 3:  # presented by a host: on camera first and last, the board in between
        for k in (0, len(scenes) - 1):
            if not scenes[k].get("host"):
                add(k + 1, "this scene should be the host on camera", "make the first and last scene host scenes")
        for k, sc in enumerate(scenes[1:-1], start=2):
            if sc.get("host"):
                add(k, "a host scene in the middle of the board", "only the first and last scene are the host")
        for k, sc in enumerate(scenes, start=1):
            if sc.get("host") and not _line(sc).strip():
                add(k, "the host says nothing", "write what the host says to camera (audio.line)")
        if not isinstance(plan.get("host"), dict) or not plan["host"].get("look"):
            add(None, "the host has no look", 'add "host": {"look", "setting", "voice_gender"}')
    look = str(plan.get("look") or "").strip().lower()
    if look and look not in LOOKS:
        add(None, f"look '{look}' is not one of the method's looks", f"pick one of {', '.join(LOOKS)}")
    for i, s in enumerate([x for x in plan.get("scenes") or [] if isinstance(x, dict)], start=1):
        if s.get("host"):
            continue  # the host's scenes have no board hero, hit or typed words to check
        if not str(s.get("hero") or "").strip():
            add(i, "no hero: the camera has nothing to land on", "name the ONE thing this line is about")
        line, hit = _line(s), str(s.get("hit_word") or "").strip()
        if line and hit and _norm(hit) not in _norm(line):
            add(i, f"hit_word '{hit}' is not in the spoken line", "pick a word the narrator actually says")
        for a in s.get("archive") or []:
            if archive_ids is not None and str(a) not in archive_ids:
                add(i, f"archive photo {a} does not exist", "use only the ids of the ARCHIVE PHOTOS given")
        typed = str(s.get("overlay_text") or "").strip()
        if len(typed.split()) > MAX_TYPED_WORDS:
            add(i, f"typed text has {len(typed.split())} words", f"at most {MAX_TYPED_WORDS} words, readable in a second")
        if len(typed.split()) >= 3 and _norm(typed) in _norm(line):
            add(i, "the typed words repeat the narration word for word", "type it OR say it, not both")
    return out
