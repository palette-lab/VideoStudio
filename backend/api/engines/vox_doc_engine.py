"""VOX-style Editorial Documentary: the user's "Made by AI Playbook" (prompts/vox_documentary_master.md, four prompts,
verbatim) made into a film with NO video model - Opus writes, an image model paints, a voice model speaks, and the
animation is code (Lemo-Opuscar's headless-Chrome frame renderer, vendored in tools/opuscar).

    Prompt 1  script        Opus: topic ideas when there is no topic (the chat offers them), the length the user picks
                            (1 / 5 / 8 / 12 / 20 minutes), then the documentary script (hook ... powerful ending)
    Prompt 2  image prompts Opus: every sentence -> core idea, editorial title, visual metaphor, image prompt
    images                  the image model (Nano Banana Pro on fal; the LLM Router, engines/router_images.py, when
                            fal cannot make it): one VOX editorial collage per sentence, all from the
                            first one's look; each documentary gets its own paper look (LOOKS, chosen for
                            its topic, never a recent one) and each chapter its own paper from it; every
                            scene builds on its own empty paper, made in code from its picture's colour
    voice                   every sentence through the voice model -> the real timeline (each scene as long as its line)
    Prompt 3/4 animation    Opus looks at every image and plans its paper layers (what appears, where, in which order,
                            how it enters, what idles) and the transition to the next scene; a tested canvas template
                            plays that plan frame by frame: layer-by-layer build, everything locked once placed, a hold
                            with micro-motion, paper-wipe transitions, static camera
    sound                   narration + paper SFX at every layer and transition, no music (Prompt 4)
"""

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path

from router_core import config, llm

from . import opuscar_engine as oc

log = logging.getLogger("palette_video.vox_doc")

APP_ID = "vox-editorial-documentary"
MASTER_FILE = Path(__file__).resolve().parent / "prompts" / "vox_documentary_master.md"
FPS = 24
LEAD_S, GAP_S, TAIL_S = 0.5, 0.35, 1.2
MIN_SCENE_S = 2.6
PLATE_BUSY = 14.0  # brightness spread above which a "plate" still has a drawing on it
PARALLEL = max(1, int(os.getenv("VOX_PARALLEL", "6")))
# Opus writes everything (the user's direction); the usual reasoning chain answers if it is not available.
MODEL = os.getenv("DOC_LLM_MODEL", config.MOTION_MODEL)

# Prompt 1, step 2: the five lengths and their word counts.
LENGTHS = {1: (140, 170), 5: (700, 850), 8: (1100, 1300), 12: (1700, 2000), 20: (2800, 3400)}
LENGTH_Q = "What video duration would you like?"
LENGTH_OPTIONS = ["1 Minute", "5 Minutes", "8 Minutes", "12 Minutes", "20 Minutes"]
TOPIC_Q = "Which idea do you want to develop?"  # the same question the motion director asks (main.TOPIC_Q)

ASKS = re.compile(r"\b(documentar(y|ies)|docu[- ]?series|vox[- ]style|johnny harris|editorial documentary)\b", re.I)
ENTRIES = ("paper_slide", "paper_drop", "soft_pop", "fade", "scale", "unfold", "mask_reveal", "wipe", "rotate")
IDLES = ("none", "flutter", "smoke", "dust", "glow", "sparkle", "vibrate", "blink", "wave", "needle", "cloud",
         "breathe")
TRANSITIONS = ("paper_wipe", "push_zoom", "zoom_out", "shape_match", "mask", "paper_slide")
ENTRY_SFX = {"paper_slide": "whoosh", "paper_drop": "thump", "soft_pop": "pop", "scale": "pop", "unfold": "click",
             "mask_reveal": "click", "wipe": "whoosh", "rotate": "click", "fade": None}


class DocumentaryError(RuntimeError):
    pass


def _prompts() -> dict[int, str]:
    """The four prompts of the playbook, word for word."""
    text = MASTER_FILE.read_text(encoding="utf-8")
    parts = re.split(r"^<<<PROMPT (\d) - [^>]*>>>\s*$", text, flags=re.M)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


PROMPTS = _prompts()


def wanted(run: dict) -> bool:
    """The Documentary mode with no file uploaded (only a topic; a file goes to the Document Explainer), or in Auto a request that asks for a documentary (no footage or document of the user's)
    and not for a host on camera or real archive photos (those go to the cinematic host + board method)."""
    mode = run.get("mode")
    has_doc = any(a.get("modality") == "document" for a in (run.get("assets") or {}).values())
    if mode == "documentary":  # the one Documentary mode: a topic is a VOX film, an uploaded file is explained
        return not has_doc
    if mode == "explainer":  # the older Document Explainer mode with no file: the same
        return not has_doc and bool((run.get("prompt") or "").strip())
    if mode != "auto" or run.get("parent_run"):
        return False
    if any(a.get("modality") in ("video", "audio", "document") for a in (run.get("assets") or {}).values()):
        return False
    from .story_intel.documentary import wants_host  # a host on camera / archive photos: the cinematic method
    return bool(ASKS.search(run.get("prompt") or "")) and not wants_host(run.get("prompt") or "")


def minutes_for(seconds) -> float | None:
    """The length the user picked in the composer, exactly, in minutes (None when Auto / not set)."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return None
    return round(s / 60, 3) if s > 0 else None


def minutes_of(answer: str | None) -> int | None:
    m = re.match(r"\s*(\d+)", str(answer or ""))
    return int(m.group(1)) if m and int(m.group(1)) in LENGTHS else None


_SAID_LEN = re.compile(r"\b(\d+(?:\.\d+)?)\s*-?\s*(minutes?|mins?|seconds?|secs?)\b", re.I)


def minutes_in_prompt(prompt: str) -> float | None:
    """A length the request states itself ("a 5-minute documentary", "90 seconds"), in minutes."""
    m = _SAID_LEN.search(prompt or "")
    if not m:
        return None
    n = float(m.group(1))
    mins = n if m.group(2).lower().startswith("min") else n / 60
    return round(mins, 3) if 0 < mins <= 60 else None


def word_range(minutes: float) -> tuple[int, int]:
    """Narration words for the length: the playbook's table, else about 140-170 words a minute."""
    if minutes in LENGTHS:
        return LENGTHS[int(minutes)]
    return max(12, round(minutes * 140)), max(16, round(minutes * 170))


def length_label(minutes: float) -> str:
    """"1 Minute", "5 Minutes", "30 seconds", "90 seconds"."""
    s = round(float(minutes) * 60)
    if s % 60:
        return f"{s} seconds"
    return f"{s // 60} Minute{'s' if s > 60 else ''}"


async def suggest_minutes(topic: str, prompt: str) -> int:
    """Auto length: the AI picks the playbook length (1/5/8/12/20 min) that fits the topic; 1 when it cannot."""
    try:
        data = await _json([{"role": "system", "content":
                             "You choose the length of a VOX-style documentary. Pick the shortest length that tells "
                             "this topic well. Lengths in minutes: 1, 5, 8, 12, 20. Return ONLY JSON: "
                             "{\"minutes\": <one of 1, 5, 8, 12, 20>}"},
                            {"role": "user", "content": f"REQUEST: {prompt.strip()}\nTOPIC: {topic.strip()}"}],
                           "engine.documentary.length", 400, "low")
        m = int(data.get("minutes") or 0)
    except (llm.LLMError, ValueError, TypeError) as e:
        log.warning("auto length failed: %s", e)
        return 1
    return m if m in LENGTHS else min(LENGTHS, key=lambda k: abs(k - m)) if m > 0 else 1


# ------------------------------------------------------------------------------------------------ the LLM
def _part(path: Path) -> dict:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"}}


async def _json(msgs: list[dict], stage: str, tokens: int = 8000, effort: str | None = "medium") -> dict:
    """Opus first; the reasoning chain when Opus is not available for this key."""
    if MODEL and MODEL != config.REASONING_MODEL:
        try:
            return await llm.chat_json(MODEL, msgs, max_tokens=tokens, reasoning_effort=effort, stage=stage)
        except llm.LLMError as e:
            log.warning("%s on %s failed (%s); using %s", stage, MODEL, str(e)[:160], config.REASONING_MODEL)
    return await llm.chat_json(config.REASONING_MODEL, msgs, max_tokens=tokens, reasoning_effort=effort, stage=stage)


APP_SCRIPT = """

---
## THIS APP (how your steps run here - follow the master prompt above for everything else)
The app handles STEP 1 (topics) and STEP 2 (duration) itself - never ask for a duration. You get the chosen TOPIC
and DURATION below - write STEP 3 now, to exactly that duration. When the input has NO real topic (e.g. "make a documentary", "documentary
ideas", "something about history"), do STEP 1 instead: return {"needs_topic": true, "topic_ideas": [50 titles]}.
Write in the language of the user's request. Return ONLY JSON:
{"needs_topic": false, "title": "...", "language": "en", "narrator_voice": "male|female", "hook": "...", "introduction": "...", "main_story": "...",
 "turning_point": "...", "big_picture": "...", "ending": "..."}
Each section is continuous narrative paragraphs for professional voice-over (the OUTPUT FORMAT of the master prompt)."""

SECTIONS = ("hook", "introduction", "main_story", "turning_point", "big_picture", "ending")
SHORTEN = """

---
## SHORTEN THIS SCRIPT
The script below is too long for its duration. Rewrite it to the TARGET word count: keep the same title, facts, story
order and all six sections, keep the hook and the ending strong, cut repetition and secondary detail, and keep
complete sentences. Return the same JSON shape (needs_topic false) with every section shortened."""


async def topic_ideas(prompt: str) -> list[str] | None:
    """Prompt 1, step 1: 50 titles when the request has no real topic; None when it has one."""
    data = await _json([{"role": "system", "content": PROMPTS[1] + APP_SCRIPT},
                        {"role": "user", "content": f"USER INPUT:\n{prompt.strip()}\n\nIf this names a real topic, "
                                                    "return only {\"needs_topic\": false}."}],
                       "engine.documentary.topics", 6000, "low")
    ideas = [str(x).strip() for x in data.get("topic_ideas") or [] if str(x).strip()]
    return ideas[:50] if data.get("needs_topic") and ideas else None


async def write_script(topic: str, minutes: float) -> dict:
    """Prompt 1, step 3: the documentary script, its length matched to the chosen duration."""
    lo, hi = word_range(minutes)
    user = (f"TOPIC: {topic.strip()}\nDURATION: {length_label(minutes)} ({lo}-{hi} words in all - "
            "never mention it)")
    data = await _json([{"role": "system", "content": PROMPTS[1] + APP_SCRIPT}, {"role": "user", "content": user}],
                       "engine.documentary.script", 16000 if minutes >= 12 else 9000)
    if not any(str(data.get(k) or "").strip() for k in SECTIONS):
        raise DocumentaryError("the script writer returned no script")
    for _ in range(2):  # too long for the chosen duration: tighten it (the film is as long as its narration)
        words = len(script_text(data).split())
        if words <= hi * 1.1:
            break
        log.info("script is %s words for %s min (%s-%s): shortening", words, minutes, lo, hi)
        try:
            short = await _json([{"role": "system", "content": PROMPTS[1] + APP_SCRIPT + SHORTEN},
                                 {"role": "user", "content": f"DURATION: {length_label(minutes)}"
                                  f"\nTARGET: {lo}-{hi} words in all (now {words})\n\nSCRIPT JSON:\n"
                                  + json.dumps({k: data.get(k) for k in ("title", "language", "narrator_voice")
                                                + SECTIONS}, ensure_ascii=False)}],
                                "engine.documentary.shorten", 16000 if minutes >= 12 else 9000)
        except (llm.LLMError, ValueError) as e:
            log.warning("shortening the script failed: %s", e)
            break
        n = len(script_text(short).split())
        if not n or n >= words:  # nothing better came back: keep what there is
            break
        data = {**data, **{k: short.get(k) for k in SECTIONS if str(short.get(k) or "").strip()}}
    data["minutes"] = minutes
    return data


def script_text(script: dict) -> str:
    return "\n\n".join(str(script.get(k) or "").strip() for k in SECTIONS if str(script.get(k) or "").strip())


_ABBR = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|U\.S|U\.K|No|Inc|Ltd|Co)\.$", re.I)


def sentences(text: str) -> list[str]:
    """Prompt 2, step 2: the script split into its sentences (every one becomes one illustration)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text or ""):
        para = " ".join(para.split())
        for piece in re.split(r"(?<=[.!?…])[\"”’)]?\s+(?=[\"“‘(]?[A-Z0-9])", para):
            piece = piece.strip()
            if not piece:
                continue
            if out and _ABBR.search(out[-1]) and out[-1].endswith("."):
                out[-1] = f"{out[-1]} {piece}"
            else:
                out.append(piece)
    return out


# ---- the look: the playbook always asks for cream paper; here every documentary gets its own paper family (chosen for
# its topic, never one of the last few used) and every chapter its own paper from it, so films and scenes differ.
# (Our own briefs - light / dark / accent paper, accent inks, type colour.)
LOOKS = {
    "cream_editorial": {"name": "Cream editorial", "light": "warm cream paper", "dark": "deep navy paper",
                        "accent": "VOX yellow paper", "inks": "coral red, deep navy, black", "type": "black and navy"},
    "kraft_ink": {"name": "Kraft & ink", "light": "brown kraft paper", "dark": "charcoal black paper",
                  "accent": "off-white newsprint", "inks": "rust orange, black, cream", "type": "black and cream"},
    "newsprint": {"name": "Newsprint", "light": "pale grey newsprint", "dark": "black paper",
                  "accent": "pale yellow paper", "inks": "signal red, black", "type": "black, red highlights"},
    "blueprint": {"name": "Blueprint night", "light": "pale blue paper", "dark": "deep blueprint-navy paper with a faint "
                  "grid", "accent": "cream paper", "inks": "warm yellow, white, sky blue", "type": "white and yellow"},
    "sage_archive": {"name": "Sage archive", "light": "soft sage-green paper", "dark": "dark forest-green paper",
                     "accent": "cream paper", "inks": "terracotta, mustard, ink black", "type": "cream and black"},
    "terracotta": {"name": "Terracotta sun", "light": "warm sand paper", "dark": "deep brown paper",
                   "accent": "terracotta paper", "inks": "teal, cream, burnt orange", "type": "cream and dark brown"},
    "noir": {"name": "Noir", "light": "bone-white paper", "dark": "charcoal paper", "accent": "black paper",
             "inks": "one blood red, white", "type": "white and red"},
    "pastel_modern": {"name": "Pastel modern", "light": "pale blush-pink paper", "dark": "muted navy paper",
                      "accent": "soft sky-blue paper", "inks": "coral, navy, butter yellow", "type": "navy and coral"},
    "ochre_desert": {"name": "Ochre desert", "light": "pale ochre paper", "dark": "dark umber paper",
                     "accent": "mustard paper", "inks": "teal, cream, brick red", "type": "umber and cream"},
    "teal_tech": {"name": "Teal tech", "light": "pale mint paper", "dark": "deep teal paper",
                  "accent": "off-white paper", "inks": "bright orange, white, ink black", "type": "white and orange"},
    "burgundy_luxe": {"name": "Burgundy luxe", "light": "ivory paper", "dark": "burgundy paper",
                      "accent": "black paper", "inks": "muted gold, cream", "type": "gold and ivory"},
    "sky_civic": {"name": "Sky civic", "light": "pale sky-blue paper", "dark": "dark slate paper",
                  "accent": "white paper", "inks": "civic yellow, red, navy", "type": "navy and white"},
}
CHAPTER_PAPER = {"hook": "dark", "introduction": "light", "main_story": "light", "turning_point": "accent",
                 "big_picture": "dark", "ending": "light"}
NEXT_PAPER = {"light": "accent", "accent": "light", "dark": "accent"}
RECENT_LOOKS = 5


def _looks_file() -> Path:
    return Path(config.STORAGE) / "vox_recent_looks.json"


def recent_looks() -> list[str]:
    try:
        return [k for k in json.loads(_looks_file().read_text()) if k in LOOKS]
    except (OSError, ValueError, TypeError):
        return []


def remember_look(key: str) -> None:
    try:
        _looks_file().parent.mkdir(parents=True, exist_ok=True)
        _looks_file().write_text(json.dumps(([key] + [k for k in recent_looks() if k != key])[:RECENT_LOOKS]))
    except OSError as e:
        log.warning("could not remember the look: %s", e)


async def choose_look(script: dict) -> str:
    """The paper family for this documentary: the one that suits its topic best, never one of the last few used."""
    import random
    fresh = [k for k in LOOKS if k not in recent_looks()] or list(LOOKS)
    options = "\n".join(f"- {k}: {LOOKS[k]['name']} ({LOOKS[k]['light']}, {LOOKS[k]['dark']}, {LOOKS[k]['accent']}; "
                         f"inks {LOOKS[k]['inks']})" for k in fresh)
    try:
        data = await _json([{"role": "user", "content": f"Documentary: {script.get('title') or ''}\n"
                             f"{script_text(script)[:1500]}\n\nPick the paper look that suits this topic and its mood "
                             f"best:\n{options}\nReturn ONLY JSON {{\"look\": \"<key>\"}}."}],
                           "engine.documentary.look", 400, "low")
        key = str(data.get("look") or "").strip()
    except (llm.LLMError, ValueError) as e:
        log.warning("choosing the look failed: %s", e)
        key = ""
    return key if key in fresh else random.choice(fresh)


def chapters(script: dict) -> list[str]:
    """The script section of every sentence of script_text(script), in order."""
    out = []
    for k in SECTIONS:
        out += [k] * len(sentences(str(script.get(k) or "")))
    n = len(sentences(script_text(script)))
    return out[:n] + ["main_story"] * (n - len(out))


def scene_papers(script: dict, look: str) -> list[str]:
    """Every scene's background paper: its chapter's paper, and in a long chapter every third scene the next one."""
    L, out, run, prev = LOOKS[look], [], 0, None
    for ch in chapters(script):
        run = run + 1 if ch == prev else 0
        prev = ch
        tone = CHAPTER_PAPER.get(ch, "light")
        if run and run % 3 == 2:
            tone = NEXT_PAPER[tone]
        out.append(L[tone])
    return out


APP_IMAGES = """

---
## THIS APP (how your steps run here - follow the master prompt above for everything else)
You already have the complete script (STEP 1 is done): convert the SENTENCES below - one scene per sentence, in order,
never merged, never skipped. Every image is 16:9 unless FORMAT says otherwise. Every Image Prompt is complete and
self-contained and ends with the master prompt's NEGATIVE PROMPT. The editorial title is drawn INTO the artwork (exact
spelling, in quotes in the prompt).
THE LOOK OF THIS DOCUMENTARY (it replaces the master prompt's always-cream BACKGROUND and its preferred colours):
{LOOK}
Every sentence below comes with its own [paper]: that scene's background IS that paper colour, filling the frame
(name it at the start of its Image Prompt). The papers change between chapters on purpose - keep the cut-out style,
inks and typography the same so it is still one documentary; the typography and inks must read clearly on that paper.
{BIBLE}
Return ONLY JSON: {"visual_bible": "the visual language of this documentary in 2-4 sentences (its papers, inks with
names, typography, cut-out treatment)", "scenes": [{"n": 1, "sentence": "...",
"core_idea": "...", "editorial_title": "1-4 WORDS", "visual_metaphor": "...", "image_prompt": "..."}]}"""
BATCH = 30


async def image_prompts(script: dict, sents: list[str], aspect: str, progress, name: str,
                        look: str = "cream_editorial") -> tuple[str, list[dict]]:
    """Prompt 2 for every sentence (in batches for long films, the first batch's visual bible locked for the rest)."""
    bible, scenes = "", []
    papers = scene_papers(script, look)
    papers += [LOOKS[look]["light"]] * (len(sents) - len(papers))
    L = LOOKS[look]
    look_text = (f"{L['name']}: light paper = {L['light']}, dark paper = {L['dark']}, accent paper = {L['accent']}; "
                 f"inks {L['inks']}; type in {L['type']}.")
    for b0 in range(0, len(sents), BATCH):
        part = sents[b0:b0 + BATCH]
        if len(sents) > BATCH:
            await progress(f"{name}: image prompts for sentences {b0 + 1}-{b0 + len(part)} of {len(sents)}")
        lock = (f"VISUAL BIBLE (locked by the earlier scenes - keep it exactly): {bible}" if bible else "")
        user = (f"TITLE: {script.get('title') or ''}\nFORMAT: {aspect}\nFULL SCRIPT (for context):\n"
                f"{script_text(script)[:20000]}\n\nSENTENCES (scene numbers {b0 + 1}-{b0 + len(part)}):\n"
                + "\n".join(f"{b0 + i + 1}. [paper: {papers[b0 + i]}] {s}" for i, s in enumerate(part)))
        data = await _json([{"role": "system", "content": PROMPTS[2] + APP_IMAGES.replace("{BIBLE}", lock)
                             .replace("{LOOK}", look_text)},
                            {"role": "user", "content": user}], "engine.documentary.image_prompts", 16000)
        bible = bible or str(data.get("visual_bible") or "").strip()
        got = [s for s in data.get("scenes") or [] if isinstance(s, dict)]
        for i, s in enumerate(part):  # one scene per sentence, whatever the model returned
            g = got[i] if i < len(got) else {}
            scenes.append({"n": b0 + i + 1, "sentence": s, "paper": papers[b0 + i],
                           "core_idea": str(g.get("core_idea") or ""),
                           "editorial_title": str(g.get("editorial_title") or "").strip()[:40],
                           "visual_metaphor": str(g.get("visual_metaphor") or ""),
                           "image_prompt": str(g.get("image_prompt") or "").strip()
                           or f"VOX-style editorial paper collage illustrating: {s}"})
    return bible, scenes


LAYERS = """

---
## THIS APP: plan the animation of the attached image (the master prompts above are the rules)
The image is scene {N} of the documentary: "{SENTENCE}". The animation is done in code by revealing RECTANGULAR
REGIONS of this exact image one after another over the empty paper, so the composition is preserved exactly. List the
image's visual layers in the master prompt's LAYER BUILD order (large geometric shape -> editorial title -> main
subject -> supporting objects -> icons / map / graph -> lines -> final emphasis), 3-7 layers, the background paper NOT
included (it is already there). For each, the tightest box around it in fractions of the image width and height
(x0, y0, x1, y1 from the top-left; boxes may overlap). Choose its entry from {ENTRIES} (with "from" for slides, drops
and wipes) and its idle motion after it lands from {IDLES} (mostly "none"). Then the transition into the NEXT scene
from {TRANSITIONS} and its direction.
Return ONLY JSON {"layers": [{"what": "...", "kind": "shape|title|subject|support|icon|map|graph|lines|accent",
"box": [x0, y0, x1, y1], "entry": "...", "from": "left|right|top|bottom", "idle": "..."}],
"transition": "...", "dir": "left|right|up|down"}"""


def _clean_layers(data: dict) -> dict:
    layers = []
    for l in (data.get("layers") or [])[:7]:
        if not isinstance(l, dict):
            continue
        try:
            x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in l.get("box")]
        except (TypeError, ValueError):
            continue
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            continue
        layers.append({"what": str(l.get("what") or "")[:80], "kind": str(l.get("kind") or "support"),
                       "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                       "entry": l.get("entry") if l.get("entry") in ENTRIES else "soft_pop",
                       "from": l.get("from") if l.get("from") in ("left", "right", "top", "bottom") else "bottom",
                       "idle": l.get("idle") if l.get("idle") in IDLES else "none"})
    if not layers:  # nothing usable: the whole picture is placed as one sheet of paper
        layers = [{"what": "the composition", "kind": "subject", "box": [0.04, 0.04, 0.96, 0.96],
                   "entry": "mask_reveal", "from": "left", "idle": "breathe"}]
    return {"layers": layers, "transition": data.get("transition") if data.get("transition") in TRANSITIONS
            else "paper_wipe", "dir": data.get("dir") if data.get("dir") in ("left", "right", "up", "down") else "left"}


async def plan_layers(img: Path, scene: dict) -> dict:
    """Prompts 3 and 4 for one image: Opus looks at it and plans its layer build and its transition."""
    rules = PROMPTS[3] + "\n\n" + PROMPTS[4]
    text = (LAYERS.replace("{N}", str(scene["n"])).replace("{SENTENCE}", scene["sentence"][:300])
            .replace("{ENTRIES}", ", ".join(ENTRIES)).replace("{IDLES}", ", ".join(IDLES))
            .replace("{TRANSITIONS}", ", ".join(TRANSITIONS)))
    try:
        data = await _json([{"role": "system", "content": rules + text},
                            {"role": "user", "content": [{"type": "text", "text": "The image:"}, _part(img)]}],
                           "engine.documentary.layers", 3000, "low")
    except (llm.LLMError, ValueError) as e:  # the scene is still animated, as one sheet
        log.warning("layer plan for scene %s failed: %s", scene["n"], e)
        data = {}
    return _clean_layers(data)


# ------------------------------------------------------------------------------------------------ images and voice
SAME_LOOK = (" Same documentary as the reference image: the same cut-out treatment, inks, typography style and paper "
             "texture - but THIS scene's own background paper colour (named above; it may differ from the reference) "
             "and its own content. Use the reference ONLY for style: never copy its background colour, title, "
             "captions, words, objects or layout.")


def _with_paper(scene: dict, aspect: str) -> str:
    """The scene's prompt with its background paper stated first (whatever the prompt writer did)."""
    shape = {"16:9": "Wide 16:9 landscape", "9:16": "Tall 9:16 vertical", "1:1": "Square 1:1",
             "4:5": "Vertical 4:5"}.get(aspect, "Wide 16:9 landscape")
    bg = f"BACKGROUND: {scene['paper']}, filling the whole frame. " if scene.get("paper") else ""
    return f"{bg}{scene['image_prompt']} {shape} composition."


async def paint(scenes: list[dict], aspect: str, film: Path, progress,
                name: str) -> tuple[list[str], list[str | None]]:
    """The first scene, then every other scene from its look (all at once); then each scene's empty paper, made in
    code from that scene's own paper colour. Returns ([plate path per scene], [image path or None per scene]) relative
    to the film page."""
    from . import story_engine
    from .vendor.flow import fal_helper
    (film / "img").mkdir(exist_ok=True)
    gate = asyncio.Semaphore(PARALLEL)

    async def gen(prompt: str, refs: list[str], dest: Path) -> str:
        """fal first; the shared fal call falls back to the LLM Router when fal cannot make it."""
        kw = {"aspect_ratio": aspect, "num_images": 1} | ({"image_urls": refs} if refs else {})
        async with gate:
            last = None
            for attempt in range(2):
                try:
                    url = story_engine._url(await story_engine._fal(fal_helper.generate_image, story_engine.fal_key(),
                                                                    story_engine.quality()["image_model"], prompt,
                                                                    **kw), "image")
                    await story_engine._download(url, dest)
                    return url
                except Exception as e:
                    last = e
                    log.warning("image %s failed (attempt %s): %s", dest.name, attempt + 1, e)
            raise DocumentaryError(f"image {dest.name}: {last}")

    await progress(f"{name}: painting scene 1 (it sets the look for all {len(scenes)})")
    first = await gen(_with_paper(scenes[0], aspect), [], film / "img" / "s001.png")
    await progress(f"{name}: painting the other {len(scenes) - 1} scenes, {PARALLEL} at a time")
    jobs = [gen(_with_paper(s, aspect) + SAME_LOOK, [first], film / "img" / f"s{s['n']:03d}.png")
            for s in scenes[1:]]
    got = await asyncio.gather(*jobs, return_exceptions=True)
    imgs: list[str | None] = ["img/s001.png"]
    for s, g in zip(scenes[1:], got):
        if isinstance(g, Exception):
            log.warning("scene %s image failed: %s", s["n"], g)
            imgs.append(None)
        else:
            imgs.append(f"img/s{s['n']:03d}.png")
    for im in imgs:  # every picture in the film's shape (some models only paint squares)
        if im:
            try:
                if await asyncio.to_thread(fit_aspect, film / im, aspect):
                    log.info("%s widened to %s with its own paper", im, aspect)
            except Exception as e:
                log.warning("could not fit %s to %s: %s", im, aspect, e)
    plates: list[str] = []  # each scene builds on its OWN empty paper (the papers change between chapters)
    last = "img/s001.png"
    for s, im in zip(scenes, imgs):
        if not im:
            plates.append(plates[-1] if plates else last)
            continue
        rel = f"img/p{s['n']:03d}.png"
        try:
            await asyncio.to_thread(paper_plate, film / im, film / rel, aspect)
            plates.append(rel)
        except Exception as e:
            log.warning("no paper plate for scene %s (%s): its picture is the background", s["n"], e)
            plates.append(im)
        last = plates[-1]
    return plates, imgs


FEMALE = re.compile(r"\b(female|woman|women's|girl|lady)\s+(narrator|voice|voice[- ]?over|narration)\b", re.I)
MALE = re.compile(r"\b(male|man|men's|boy)\s+(narrator|voice|voice[- ]?over|narration)\b", re.I)


def voice_gender(prompt: str, script: dict | None = None) -> str:
    """The narrator's voice: what the user asked for, else the script writer's pick, else male."""
    if FEMALE.search(prompt or ""):
        return "female"
    if MALE.search(prompt or ""):
        return "male"
    return "female" if str((script or {}).get("narrator_voice") or "").lower() == "female" else "male"


# ---- fitting the pictures: some image models ignore the aspect ratio, and an "empty paper" edit can come back full
def _ff(args: list[str]) -> bytes:
    import subprocess
    from .vendor.flow import video_merge
    ff = video_merge.find_ffmpeg() or "ffmpeg"
    out = subprocess.run([ff, "-loglevel", "error", "-y", *args], capture_output=True, timeout=120)
    if out.returncode != 0:
        raise DocumentaryError("ffmpeg: " + out.stderr.decode(errors="replace")[-200:])
    return out.stdout


def _size(path: Path) -> tuple[int, int]:
    import shutil
    import subprocess
    probe = shutil.which("ffprobe") or "ffprobe"
    out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def paper_colour(path: Path) -> str:
    """The average colour of the picture's outer edge (the paper), as 0xRRGGBB."""
    w, h = _size(path)
    t = max(4, min(w, h) // 40)
    px = []
    for crop in (f"{w}:{t}:0:0", f"{w}:{t}:0:{h - t}", f"{t}:{h}:0:0", f"{t}:{h}:{w - t}:0"):
        px.append(_ff(["-i", str(path), "-vf", f"crop={crop},scale=1:1:flags=area", "-frames:v", "1",
                       "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])[:3])
    r, g, b = (round(sum(p[i] for p in px) / len(px)) for i in range(3))
    return f"0x{r:02X}{g:02X}{b:02X}"


def busyness(path: Path) -> float:
    """How much is drawn on a picture: the spread of its brightness at 32x18 (empty paper is near 0)."""
    raw = _ff(["-i", str(path), "-vf", "scale=32:18:flags=area,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"])
    vals = list(raw[:576]) or [0]
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5


def fit_aspect(path: Path, aspect: str) -> bool:
    """Widen (or heighten) a picture to the film's aspect by extending its own paper colour, never cropping it.
    True when it was changed."""
    w, h = _size(path)
    aw, ah = oc.SIZES.get(aspect, (16, 9))
    if abs(w / h - aw / ah) < 0.03:
        return False
    W, H = (round(h * aw / ah), h) if w / h < aw / ah else (w, round(w * ah / aw))
    W, H = W + W % 2, H + H % 2
    tmp = path.with_suffix(".fit.png")
    f = max(8, round(0.06 * (w if W > w else h)))  # the picture's own edge fades into the paper (no seam)
    edge = f"min(X,W-1-X)/{f}" if W > w else f"min(Y,H-1-Y)/{f}"
    _ff(["-f", "lavfi", "-i", f"color=c={paper_colour(path)}:s={W}x{H}", "-i", str(path), "-filter_complex",
         f"[0]noise=alls=4:allf=u[bg];[1]format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*min(1,{edge})'[fg];"
         "[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto", "-frames:v", "1", str(tmp)])
    tmp.replace(path)
    return True


def paper_plate(src: Path, dest: Path, aspect: str) -> None:
    """An empty sheet of the documentary's paper, made in code from the first picture's paper colour (with grain)."""
    w, h = oc.SIZES.get(aspect, (1920, 1080))
    _ff(["-f", "lavfi", "-i", f"color=c={paper_colour(src)}:s={w}x{h}", "-vf", "noise=alls=5:allf=u",
         "-frames:v", "1", str(dest)])


async def narrate(scenes: list[dict], language: str, work: Path, progress, name: str,
                  gender: str = "male") -> tuple[list, list[str]]:
    """Every sentence through the voice model (Kokoro locally, ElevenLabs on fal when paid voices are on)."""
    from . import presenter_engine
    used: list[str] = []
    gate = asyncio.Semaphore(3)
    key = os.getenv("FAL_KEY", "")
    await progress(f"{name}: recording the narration ({len(scenes)} sentences)")

    async def one(s: dict) -> str | None:
        async with gate:
            try:
                return await presenter_engine.speak(s["sentence"], gender, language, key,
                                                    work / "vo" / f"vo_{s['n']:03d}", used)
            except Exception as e:  # a scene without its voice is still shown, at reading pace
                log.warning("narration of sentence %s failed: %s", s["n"], e)
                return None
    (work / "vo").mkdir(parents=True, exist_ok=True)
    return list(await asyncio.gather(*[one(s) for s in scenes])), used


def timeline(scenes: list[dict], durations: list[float | None]) -> tuple[list[dict], float]:
    """Each scene as long as its spoken sentence (a lead-in, the line, a breath), at least MIN_SCENE_S."""
    t, out = 0.0, []
    for i, (s, d) in enumerate(zip(scenes, durations)):
        spoken = float(d) if d else max(1.5, len(s["sentence"].split()) / oc.WORDS_PER_S)
        t0 = t
        lt0 = t0 + (LEAD_S if i == 0 else 0.2)
        t1 = max(t0 + MIN_SCENE_S, lt0 + spoken + GAP_S)
        out.append({"n": s["n"], "t0": round(t0, 3), "t1": round(t1, 3), "line_t0": round(lt0, 3),
                    "line_t1": round(lt0 + spoken, 3), "line": s["sentence"]})
        t = t1
    if out:
        out[-1]["t1"] = round(t + TAIL_S, 3)
    return out, (out[-1]["t1"] if out else TAIL_S)


# ------------------------------------------------------------------------------------------------ the film page
# Deterministic: render(t) draws frame t from the plan (no clocks, no randomness), as the renderer needs.
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;overflow:hidden;background:#000}canvas{display:block}</style></head><body>
<canvas id="film"></canvas>
<script type="module">
import { loadImages, cover, clamp, seg, ss } from '/kit/illustrated.js';
const SC = __SCENES__;
window.DUR = __DUR__; window.TIMELINE = SC;
const cv = document.getElementById('film'), W = innerWidth, H = innerHeight; cv.width = W; cv.height = H;
const g = cv.getContext('2d');
const all = await loadImages([...SC.map(s => s.plate), ...SC.map(s => s.img || s.plate)]);
const plates = all.slice(0, SC.length), pics = all.slice(SC.length);
const TR = 0.5;                                    // transition length (s)
const back = x => { const c = 1.15, k = c + 1; return 1 + k * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
const sceneAt = t => { for (let i = SC.length - 1; i >= 0; i--) if (t >= SC[i].t0) return i; return 0; };
// the image is drawn like `cover`, so a box in image fractions maps to these canvas coordinates
function fitOf(img) {
  const s = Math.max(W / img.width, H / img.height), dw = img.width * s, dh = img.height * s;
  return { s, ox: (W - dw) / 2, oy: (H - dh) / 2, dw, dh };
}
function region(c, img, box, alpha, dx, dy, sc, rot, clipFrac, dir) {
  const f = fitOf(img), [x0, y0, x1, y1] = box;
  let X = f.ox + x0 * f.dw, Y = f.oy + y0 * f.dh, Wd = (x1 - x0) * f.dw, Hd = (y1 - y0) * f.dh;
  c.save(); c.globalAlpha = alpha;
  const cx = X + Wd / 2, cy = Y + Hd / 2;
  c.translate(cx + dx, cy + dy); c.rotate(rot); c.scale(sc, sc); c.translate(-cx, -cy);
  c.beginPath();
  if (clipFrac < 1) {                               // a mask / wipe grows across the layer
    const p = Math.max(0, clipFrac);
    if (dir === 'right') c.rect(X + Wd * (1 - p), Y, Wd * p, Hd);
    else if (dir === 'top') c.rect(X, Y, Wd, Hd * p);
    else if (dir === 'bottom') c.rect(X, Y + Hd * (1 - p), Wd, Hd * p);
    else c.rect(X, Y, Wd * p, Hd);
  } else c.rect(X, Y, Wd, Hd);
  c.clip();
  c.drawImage(img, f.ox, f.oy, f.dw, f.dh);
  c.restore();
}
function enter(c, img, L, p, i) {                    // one layer entering, p 0..1 (locked at 1)
  if (p <= 0) return;
  const e = ss(p), o = back(Math.min(1, p)), d = L.from;
  const sx = d === 'left' ? -1 : d === 'right' ? 1 : 0, sy = d === 'top' ? -1 : d === 'bottom' ? 1 : 0;
  switch (L.entry) {
    case 'paper_slide': region(c, img, L.box, Math.min(1, p * 3), sx * (1 - e) * W * 0.06, sy * (1 - e) * H * 0.06, 1, 0, 1); break;
    case 'paper_drop': region(c, img, L.box, Math.min(1, p * 3), 0, -(1 - o) * H * 0.05, 1, 0, 1); break;
    case 'soft_pop': region(c, img, L.box, Math.min(1, p * 2.5), 0, 0, 0.95 + 0.05 * o, 0, 1); break;
    case 'scale': region(c, img, L.box, e, 0, 0, 0.95 + 0.05 * e, 0, 1); break;
    case 'unfold': region(c, img, L.box, 1, 0, 0, 1, 0, e, 'top'); break;
    case 'mask_reveal': region(c, img, L.box, 1, 0, 0, 1, 0, e, d === 'right' ? 'right' : 'left'); break;
    case 'wipe': region(c, img, L.box, 1, 0, 0, 1, 0, e, d); break;
    case 'rotate': region(c, img, L.box, Math.min(1, p * 2.5), 0, 0, 1, (1 - e) * (i % 2 ? 1 : -1) * 0.05, 1); break;
    default: region(c, img, L.box, e, 0, 0, 1, 0, 1);
  }
}
function idle(c, img, L, t, i) {                     // after the build: almost invisible life (2-4 px, 1 deg, 1%)
  const w = Math.sin(t * 1.3 + i * 1.7), w2 = Math.sin(t * 0.7 + i);
  switch (L.idle) {
    case 'flutter': case 'wave': case 'cloud': case 'smoke': region(c, img, L.box, 1, 2.5 * w, 1.2 * w2, 1, 0, 1); break;
    case 'needle': region(c, img, L.box, 1, 0, 0, 1, 0.012 * w, 1); break;
    case 'breathe': case 'vibrate': region(c, img, L.box, 1, 0, 0, 1 + 0.008 * w, 0, 1); break;
    case 'glow': case 'sparkle': case 'blink': case 'dust': {
      c.save(); c.globalCompositeOperation = 'lighter'; region(c, img, L.box, 0.05 * (0.5 + 0.5 * w), 0, 0, 1, 0, 1); c.restore(); break; }
  }
}
function scene(c, i, t) {                           // scene i at film time t (its own layer build, then the hold)
  const S = SC[i], img = pics[i], n = S.layers.length, local = t - S.t0, D = S.t1 - S.t0;
  const build = Math.min(6, Math.max(1.2, 0.5 * D)), slot = build / n;
  cover(c, plates[i], W, H);
  if (!S.img) return;
  if (local >= build) {                               // complete: the exact image, fixed, with micro-motion only
    const f = ss(seg(local, build, build + 0.35));
    S.layers.forEach((L, k) => enter(c, img, L, 1, k));
    c.save(); c.globalAlpha = f; cover(c, img, W, H); c.restore();
    if (f >= 1) S.layers.forEach((L, k) => idle(c, img, L, t, k));
    return;
  }
  S.layers.forEach((L, k) => enter(c, img, L, clamp((local - k * slot) / Math.max(0.35, slot * 0.9), 0, 1), k));
}
window.render = t => {
  const i = sceneAt(t), S = SC[i];
  scene(g, i, t);
  const next = i + 1 < SC.length ? i + 1 : -1;
  if (next >= 0 && t > S.t1 - TR) {                  // the transition: the next scene's sheet of paper takes the frame
    const p = ss(seg(t, S.t1 - TR, S.t1)), dir = S.dir || 'left', plate = plates[next];
    g.save();
    if (S.transition === 'push_zoom' || S.transition === 'zoom_out') {
      g.globalAlpha = p; cover(g, plate, W, H);
    } else {
      g.beginPath();
      if (dir === 'right') g.rect(0, 0, W * p, H); else if (dir === 'up') g.rect(0, H * (1 - p), W, H * p);
      else if (dir === 'down') g.rect(0, 0, W, H * p); else g.rect(W * (1 - p), 0, W * p, H);
      g.clip(); cover(g, plate, W, H);
      g.restore(); g.save();
      g.fillStyle = 'rgba(60,45,30,0.16)';           // the paper edge's soft shadow
      if (dir === 'right') g.fillRect(W * p, 0, 10, H); else if (dir === 'up') g.fillRect(0, H * (1 - p) - 10, W, 10);
      else if (dir === 'down') g.fillRect(0, H * p, W, 10); else g.fillRect(W * (1 - p) - 10, 0, 10, H);
    }
    g.restore();
  }
};
window.TEXTS = t => { window.render(t); return []; };
window.EV = SC.flatMap((S, i) => {
  const D = S.t1 - S.t0, build = Math.min(6, Math.max(1.2, 0.5 * D)), slot = build / S.layers.length;
  const ev = S.layers.map((L, k) => L.sfx ? { t: +(S.t0 + k * slot + 0.05).toFixed(3), type: L.sfx } : null).filter(Boolean);
  if (i + 1 < SC.length) ev.push({ t: +(S.t1 - TR).toFixed(3), type: 'whoosh' });
  return ev;
});
window.READY = true;
</script></body></html>"""


def page(tl: list[dict], plans: list[dict], imgs: list[str | None], plates: list[str] | str, dur: float) -> str:
    plates = [plates] * len(tl) if isinstance(plates, str) else list(plates)
    scenes = []
    for b, pl, im, pt in zip(tl, plans, imgs, plates):
        layers = [{**l, "sfx": ENTRY_SFX.get(l["entry"]) if k % 2 == 0 or l["kind"] in ("title", "subject") else None}
                  for k, l in enumerate(pl["layers"])] if im else []  # a scene without its image stays quiet
        scenes.append({"t0": b["t0"], "t1": b["t1"], "img": im, "plate": pt, "layers": layers,
                       "transition": pl["transition"], "dir": pl["dir"]})
    return PAGE.replace("__SCENES__", json.dumps(scenes, ensure_ascii=False)).replace("__DUR__", f"{dur:g}")


# ------------------------------------------------------------------------------------------------ the film
def srt(tl: list[dict]) -> str:
    return oc.srt(tl)


async def run(app: dict, handoff: dict, work: Path, progress) -> dict:
    """Make the documentary. Returns {"path", "duration_s", "title", "script", "scenes", "subtitles", ...}."""
    from . import presenter_engine
    from .vendor.flow import video_merge
    why = available()
    if why:
        raise DocumentaryError(why)
    name = app["name"]
    req = handoff.get("request") or {}
    aspect = req.get("aspect_ratio") if req.get("aspect_ratio") in oc.SIZES else "16:9"
    w, h = oc.SIZES[aspect]
    work.mkdir(parents=True, exist_ok=True)
    film = work / "film"
    film.mkdir(exist_ok=True)

    script = req.get("script") or {}
    if not any(str(script.get(k) or "").strip() for k in SECTIONS):
        minutes = float(req.get("minutes") or 1)
        await progress(f"{name}: writing the {length_label(minutes).lower()} script (Prompt 1)")
        script = await write_script(req.get("topic") or handoff.get("prompt") or "", minutes)
    sents = sentences(script_text(script))
    if not sents:
        raise DocumentaryError("the script has no sentences")
    await progress(f"{name}: '{script.get('title') or 'the documentary'}' - {len(sents)} sentences, one illustration "
                   "each (Prompt 2)")
    look = await choose_look(script)
    remember_look(look)
    await progress(f"{name}: the look - {LOOKS[look]['name']} ({LOOKS[look]['light']}, {LOOKS[look]['dark']}, "
                   f"{LOOKS[look]['accent']})")
    bible, scenes = await image_prompts(script, sents, aspect, progress, name, look)

    lang = {"en": "english", "es": "spanish", "fr": "french", "hi": "hindi", "ja": "japanese", "zh": "chinese",
            "pt": "portuguese", "it": "italian", "ko": "korean"}.get(str(script.get("language") or "en")[:2], "english")
    gender = req.get("voice_gender") or voice_gender(req.get("prompt") or "", script)
    voice_job = asyncio.create_task(narrate(scenes, lang, work, progress, name, gender))
    plates, imgs = await paint(scenes, aspect, film, progress, name)
    await progress(f"{name}: planning the paper-layer animation of every scene (Prompts 3 and 4)")
    gate = asyncio.Semaphore(PARALLEL)

    async def planned(s: dict, im: str | None) -> dict:
        if not im:
            return _clean_layers({})
        async with gate:
            return await plan_layers(film / im, s)
    plans = list(await asyncio.gather(*[planned(s, im) for s, im in zip(scenes, imgs)]))
    files, used = await voice_job
    ff = video_merge.find_ffmpeg()
    lengths = [await asyncio.to_thread(video_merge.probe_duration, ff, f) if f else None for f in files]
    tl, dur = timeline(scenes, lengths)

    (film / "index.html").write_text(page(tl, plans, imgs, plates, dur))
    await progress(f"{name}: checking the film page")
    probe = [min(dur - 0.05, b["t0"] + 0.4) for b in tl[:3]] + [min(dur - 0.05, b["t1"] - 0.6) for b in tl[:3]]
    _, errors = await oc.stills(film, probe, w, h, work / "stills")
    if errors:
        raise DocumentaryError("the film page failed to draw: " + "; ".join(errors[:3]))

    await progress(f"{name}: rendering {round(dur * FPS)} frames ({dur / 60:.1f} min)")
    raw = work / "picture.mp4"
    code, text = await oc._node(["core/render/video.mjs", str(film), "--fps", str(FPS), "--workers", "4",
                                 "--size", f"{w}x{h}", "--out", str(raw)], int(max(2400, dur * 12)))
    if code != 0 or not raw.exists():
        raise DocumentaryError("the render failed: " + "; ".join(oc.page_errors(text) or [text.strip()[-300:]]))
    code, text = await oc._node(["core/render/events.mjs", str(film), "--size", f"{w}x{h}",
                                 "--out", str(work / "events.json")], 600)
    page_ev = (json.loads((work / "events.json").read_text()).get("ev") or []) if code == 0 else []

    await progress(f"{name}: mixing the narration and paper sound effects (no music)")
    events = sorted([{"t": float(e["t"]), "type": str(e["type"])} for e in page_ev
                     if isinstance(e, dict) and str(e.get("type")) in oc.SFX], key=lambda e: e["t"])
    spec = {"dur": dur, "narration": [{"path": f, "t": b["line_t0"]} for f, b in zip(files, tl) if f],
            "events": events, "music": None}
    (work / "mix.json").write_text(json.dumps(spec))
    mixed = work / "mix.wav"
    res = await presenter_engine.run_local([presenter_engine.LOCAL_PY, oc.RUNTIME / "kit" / "mix.py", work / "mix.json",
                                            mixed], "The sound mix", timeout=1200)
    final = work / "final.mp4"
    proc = await asyncio.create_subprocess_exec("sh", str(oc.RUNTIME / "core" / "render" / "mux.sh"), str(raw),
                                                str(mixed), str(final), str(FPS), "0", cwd=str(oc.RUNTIME),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    if proc.returncode != 0 or not final.exists():
        raise DocumentaryError("mastering failed: " + out.decode(errors="replace").strip()[-300:])
    subs = work / "film.srt"
    subs.write_text(srt(tl))
    missing = sum(1 for im in imgs if not im)
    return {"path": str(final), "duration_s": await asyncio.to_thread(video_merge.probe_duration, ff, str(final)),
            "title": script.get("title") or "", "script": script_text(script), "visual_bible": bible,
            "scenes": [{**s, "image": im, "layers": len(pl["layers"]), "transition": pl["transition"]}
                       for s, im, pl in zip(scenes, imgs, plans)],
            "subtitles": str(subs), "voice": sorted(set(used)), "sfx": res.get("events", 0),
            "missing_images": missing, "minutes": script.get("minutes"), "look": LOOKS[look]["name"]}


def available() -> str | None:
    """None when the documentary can be made here; otherwise what is missing."""
    why = oc.available()
    if why:
        return why
    from . import router_images
    if not router_images.available() and not os.getenv("FAL_KEY"):
        return "neither LLM_ROUTER_API_KEY nor FAL_KEY is set (an image model paints every scene)"
    return None


# ------------------------------------------------------------------------------------------------ the plan
def build(run: dict) -> dict:
    """{"understanding", "plan"}: one step, the VOX Documentary, with the written script, topic and length."""
    from router_core import registry
    app = registry.apps()[APP_ID]
    st = run.get("settings") or {}
    doc = run.get("documentary") or {}
    script, minutes = doc.get("script") or {}, float(doc.get("minutes") or 1)
    topic = doc.get("topic") or run.get("prompt") or ""
    ar = st.get("aspect_ratio") or "16:9"
    n = len(sentences(script_text(script))) if script else None
    caps = ["documentary.editorial_film"]
    what = (f"“{script.get('title')}” - {length_label(minutes).lower()}, {n} sentences = {n} illustrations"
            if script else length_label(minutes).lower())
    step = {"step_id": "s1", "app_id": APP_ID, "subtask_ids": ["t_doc"], "capabilities_used": caps,
            "responsibility": ("Opus writes the script (Prompt 1) and one VOX editorial image prompt per sentence "
                               "(Prompt 2); the image model paints them; the voice model narrates every sentence; "
                               "Opus plans each image's paper-layer build and transition (Prompts 3-4) and code "
                               "animates it frame by frame - no video model; narration + paper SFX, no music."),
            "inputs": [{"source": "prompt", "format": "text"}], "outputs": ["video_file"],
            "handoff": {"app_id": APP_ID, "app_name": app["name"], "prompt": script_text(script) or topic,
                        "language": script.get("language") or "", "endpoint": "",
                        "request": {"prompt": run.get("prompt") or "", "topic": topic, "minutes": minutes,
                                    "aspect_ratio": ar, "script": script}, "notes": []}}
    understanding = {
        "detected_intent": "create_new", "user_goal": topic, "goal": topic,
        "requested_output": f"a VOX-style editorial documentary: {what}, {ar}",
        "production_type": ["documentary"],
        "assumptions": ["no video model: images + code animation", "one illustration per sentence (the playbook)"]
        + ([f"{n} images will be painted - a long documentary costs accordingly"] if n and n > 60 else []),
        "scene": {"narrative": True}, "constraints": {"duration_s": round(minutes * 60), "aspect_ratio": ar},
        "subtasks": [{"id": "t_doc", "group": "generate", "description": f"A VOX editorial documentary, {what}",
                      "required_capabilities": caps, "basis": "request"}],
        "direct": True, "confidence": 1.0}
    return {"understanding": understanding, "plan": {"feasible": True, "steps": [step], "direct": True}}
