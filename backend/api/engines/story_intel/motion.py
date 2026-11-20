"""Motion Graphics Director: the user's "Aiplaybook - Viral Motion Explainer Video" master prompt
(prompts/motion_explainer_master.md, verbatim) plus this app's runtime section, as one agent of Story Intelligence.
Mixed-media kinetic editorial collage (realistic 3D objects, halftone cutouts, 2.5D floating grids, kinetic
typography), 9:16, 6-second scenes. Chosen for anything motion-graphics related: psychology, motivation, IT and
finance, education, marketing, behavioural design, science / technology concepts and commercial explainers (and
whenever the user asks for motion graphics) - stories with characters, documentaries, presenters and cooking keep
their own agents.

The master prompt's stages map onto this app:
    Stage 1  topic ideas            only when the input has no real topic (the chat offers 5 titles to pick)
    Stage 2  narration script       Hook -> Reframe -> Core Mechanism -> Insight -> Echoing Closing Line, <= 14 words
    Stage 3  palette lock           the playbook's 3 background options; the director picks one, the user can switch
    Stage 4  keyframe posters       one Nano Banana keyframe per sentence, with no text in it
    Stage 5  motion prompts         the 6-second timeline per scene + SFX
    after    generation             the film engine: keyframe -> animated in code from an LLM motion plan, no
                                    video model (engines/motion_code.py; MOTION_VIDEO_MODEL=on for AI clips) ->
                                    joined film -> narration voice-over (TTS) -> the words as an editable text layer
Each STOP of the master prompt is the app's approval step: the user reviews, switches the palette, edits or
regenerates a scene, then approves the render.
"""

import json
import re
from pathlib import Path

# Fallback only: used when the director returns no palettes of its own. Every video normally gets three palette
# directions invented for its topic (Stage 3), so no two videos share one fixed look.
PALETTES = {
    1: {"name": "Signature Editorial", "hex": ["#F4EFE6", "#4B2A91", "#141414"],
        "spec": "warm off-white paper background (#F4EFE6) with a fine grey dashed grid (#CFC8BC); deep royal purple "
                "accents (#4B2A91, #6C3FD1); bold black (#141414) and purple typography; soft editorial shadows; "
                "premium photographic and 3D elements; exactly two faint diagonal background lines in low-contrast "
                "purple-grey"},
    2: {"name": "Dark Technical Minimal", "hex": ["#151A21", "#14E0C8", "#F5F7FA"],
        "spec": "slate / charcoal background (#151A21, #1E252E); neon teal and cyan accents (#14E0C8, #2BC8F2); subtle "
                "luminous atmosphere; crisp white (#F5F7FA) and teal typography; real matte and brushed-metal "
                "materials lit like a product photo; technical geometric line elements"},
    3: {"name": "High-Contrast Academic", "hex": ["#F2EBDD", "#B4532A", "#2B2B2B"],
        "spec": "pale cream / bone background (#F2EBDD) with a thin cross-dot grid (#D8CDB8); deep terracotta and "
                "burnt-orange shapes (#B4532A, #E07B39); charcoal (#2B2B2B) and orange typography; editorial "
                "cutouts; warm dimensional lighting"},
}

# The user's master prompt, verbatim (prompts/motion_explainer_master.md), plus this app's runtime section.
MASTER_FILE = Path(__file__).resolve().parent.parent / "prompts" / "motion_explainer_master.md"
MASTER = MASTER_FILE.read_text(encoding="utf-8")

APP_SECTION = """

---
## THIS APP (runtime information: how your stages run here - follow the master prompt above for everything else)

STAGES AND STOPS: here you write Stages 2, 3, 4 and 5 in ONE response (as JSON below). The app then shows each stage
to the user at its approval step - the script, the locked background option (with buttons to switch to the other
two), every keyframe and motion prompt - and nothing is generated until the user approves. That approval step is
the master prompt's STOP / "next". Stage 1 only when the input has no real topic (see the end).

THE USER'S DIRECTIONS FOR THIS APP (they override the master prompt where they differ):
- VOICE-OVER ON: each script sentence is spoken by a separate narration voice (text-to-speech) laid over the film.
  The clips themselves stay free of speech: "No voice over" in Stage 5 means no voice INSIDE the clip.
- NO BACKGROUND MUSIC: the clips carry SFX only (paper whooshes, UI clicks, soft thuds, air swells).
- TYPOGRAPHY IS AN EDITABLE TEXT LAYER: the 1-4 words are NOT drawn by the image or video model. The app sets them on
  top of the finished film (the user can retype, move and restyle them). So the keyframe_prompt contains NO letters,
  words, numbers, logos or signage - end it with "no text, no letters, no logos". Leave clean space where the words
  will sit (say where in "composition", e.g. "upper third kept clear for the headline"). Give the words in
  "typography_text" and their hierarchy (Sans-Serif Bold / Editorial Italic Serif, colour from the locked option) in
  "typography_style". The ghost text and typography motion go in "typography_motion" for the text layer.
- Platform and AI tool icons (Instagram, TikTok, YouTube, ChatGPT...) are drawn as generic rounded app-icon shapes in
  their brand colour, without letters or logos.

ONE SCENE PER SENTENCE (Stage 4): every script sentence becomes one scene - its keyframe poster (Stage 4) and its
6-second motion prompt (Stage 5). duration_s is 6 unless the sentence needs longer to be said (then 7-8).

KEYFRAMES (Stage 4): the image model is Nano Banana Pro. Each keyframe_prompt (100-170 words) is self-contained:
the exact locked Stage 3 background and grid, 1 distinct primary hero object (3D or mixed-media cutout), at most 2
supporting vector / collage accents, controlled asymmetry, editorial lighting and drop shadows, the 2 faint diagonal
background lines in the background-derived colour, 9:16 composition, premium, ultra-detailed, 8K.

MOTION (Stage 5): the clip model (Kling 3.0 Pro / Seedance) animates each keyframe. Write the four-part timeline
(0.0-0.5 / 0.5-3.5 / 3.5-4.7 / 4.7-6.0, stretched to the scene's length) for the hero, the vector accents, the
background lines and the camera. The clip shows NO text at all; keep the space planned for the words clear.

PALETTE (Stage 3): return the master prompt's three options as "palettes" (option 1 Signature Editorial, 2 Dark
Technical/Minimal, 3 High-Contrast Academic), each with 3-5 HEX colours (background first, then accents, then type).
{PALETTE_RULE} Use ONLY the locked option's colours in every scene.

LENGTH: {LENGTH}. FORMAT: 9:16 unless FORMAT below says otherwise.

FACTS: state only what the RESEARCH given supports (cite it in fact_notes); no fake statistics.

CAPABILITIES - only what these apps can really do:
{APPS}

Return ONLY JSON:
{"needs_topic": false, "topic_ideas": [],
 "title": "...", "category": "...", "objective": "...", "audience": "...", "approach": ["..."],
 "script": "the complete narration", "beats": [{"id": "B01", "line": "exact sentence", "role": "hook | reframe |
  mechanism | example | insight | closing"}],
 "art_direction": {"name": "Mixed-Media Kinetic Editorial Collage", "concept": "how the style serves this topic",
  "set": "background, grid and framing accents", "materials": "hero objects and cutout treatments",
  "typography": "Sans-Serif Bold / Editorial Italic Serif hierarchy", "lighting": "editorial lighting and drop
  shadows", "camera": "locked-off with subtle push / pull or Z drift", "texture": "paper grain, halftone"},
 "palettes": [{"option": 1, "name": "Signature Editorial", "spec": "...", "hex": ["#bg", "#accent", "#type"]},
  {"option": 2, "name": "Dark Technical/Minimal", ...}, {"option": 3, "name": "High-Contrast Academic", ...}],
 "palette": {"option": 1 | 2 | 3, "name": "...", "why": "why it fits this topic"},
 "scenes": [{"n": 1, "beat": "B01", "script_line": "exact sentence", "duration_s": 6,
  "visual_concept": "what the viewer sees", "hero_subject": "the hero object and the idea it stands for",
  "keyframe_prompt": "the complete TEXT-TO-IMAGE PROMPT with NO text, letters or logos in it",
  "typography_text": "1-4 exact words from the line (the editable text layer)",
  "typography_style": "hierarchy, colour, where it sits in the frame",
  "three_d_elements": "hero object, materials, depth", "graphic_elements": "the max 2 vector / collage accents",
  "composition": "placement, asymmetry and the clear space for the words", "lighting": "...",
  "color": "the locked option for this frame",
  "motion": {"style": "e.g. 2.5D Mixed-Media Floating Grid", "hero_object_motion": "...", "three_d_animation": "...",
   "typography_motion": "...", "graphic_motion": "...", "background_motion": "exactly 2 soft diagonal lines ...",
   "camera": "Static / Slow 5% Push-in / Subtle Parallax Drift", "lighting": "...", "sfx": "timed SFX track",
   "transition_out": "how it hands over to the next scene",
   "timeline": [{"t": "0.0-0.5", "what": "Clean Grid Base ..."}, {"t": "0.5-3.5", "what": "Kinetic Entrance ..."},
                {"t": "3.5-4.7", "what": "Micro-Motion and Hold ..."}, {"t": "4.7-6.0", "what": "Outro ..."}]},
  "method": "ai_clip", "end_frame": "the final composition handed to the next scene"}],
 "connection": "S01 -> S02 -> ...",
 "production": {"application": "...", "model": "...", "method": "...", "resolution": "...", "aspect_ratio": "9:16",
  "audio": "narration voice-over + clip SFX, no music", "limitations": ["honest compromises"]},
 "fact_notes": [{"claim": "...", "source": "..."}]}
STAGE 1 here: when the input has NO real topic (e.g. "make me a video", "finance video ideas", "something about
psychology"), return {"needs_topic": true, "topic_ideas": ["VIDEO TITLE 01", ..., "VIDEO TITLE 05"]} using the
PROVEN VIRAL TOPIC ANGLES, and leave the rest empty. The five must be DIFFERENT angles across the niche."""

DIRECTOR = MASTER + APP_SECTION


def palette_rule(option: int | None, palettes: dict | None = None) -> str:
    pals = palettes or PALETTES
    if option in pals:
        return (f"LOCKED by the user: option {option} ({pals[option]['name']}). Use it in every scene; never "
                "change it.")
    return ("choose the palette that fits this topic best and lock it for every scene (the user can switch it before "
            "the render).")


def messages(prompt: str, cls: dict, res: dict | None, files_note: str, apps: str, aspect_ratio,
             palette: int | None = None, keep_script: str | None = None, keep_style: dict | None = None) -> list[dict]:
    length = (f"Target {cls['duration_s']} seconds" if cls.get("duration_s") else "Target 15-30 seconds")
    pals = (keep_style or {}).get("palettes")
    system = MASTER + (APP_SECTION.replace("{LENGTH}", length).replace("{PALETTE_RULE}", palette_rule(palette, pals))
                       .replace("{APPS}", apps or "(registry unavailable)"))
    user = [f"USER INPUT:\n{prompt.strip()}", f"FORMAT: {aspect_ratio or '9:16'}", f"FILES: {files_note}",
            f"UNDERSTANDING: {json.dumps({k: cls.get(k) for k in ('category', 'intent', 'subject', 'audience', 'tone', 'language')}, ensure_ascii=False)}"]
    if res:
        user.append("RESEARCH (the only facts you may state):\n" + json.dumps(
            {k: res.get(k) for k in ("summary", "angle", "facts", "timeline", "note")}, ensure_ascii=False)[:12000])
    from .. import lang
    system += lang.rule(cls.get("language") or "english")
    if keep_script:
        user.append(f"KEEP THIS APPROVED SCRIPT WORD FOR WORD (only the palette changes):\n{keep_script}")
    if keep_style:
        user.append("KEEP THIS ART DIRECTION AND THESE THREE PALETTES exactly (return them unchanged); only the locked "
                    "palette changes:\n" + json.dumps(keep_style, ensure_ascii=False))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]


# A request that names motion graphics goes to this director in any mode (the user's direction).
MOTION_ASK = re.compile(r"\b(motion[- ]?graphics?|motion[- ]design(ed)?|motion explainer|kinetic typography|mograph|"
                        r"animated explainer|motion[- ]?gfx)\b", re.I)


def asks_for_motion(prompt: str) -> bool:
    return bool(MOTION_ASK.search(prompt or ""))


HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def palettes_of(d: dict) -> dict[int, dict]:
    """The three palettes the director wrote for this video (Stage 3), or the fallback set when it wrote none."""
    out = {}
    for i, p in enumerate([x for x in d.get("palettes") or [] if isinstance(x, dict)][:3], start=1):
        hexes = [h.strip() for h in (p.get("hex") or []) if isinstance(h, str) and HEX.match(h.strip())][:5]
        if p.get("name"):
            out[i] = {"name": str(p["name"])[:60], "spec": str(p.get("spec") or "")[:600], "hex": hexes}
    return out if len(out) >= 2 else {k: dict(v) for k, v in PALETTES.items()}


def keep_style(plan: dict) -> dict:
    """The approved look (art direction + this video's palettes) to keep through a palette switch."""
    mg = plan.get("motion_graphics") or {}
    pals = {int(k): v for k, v in (mg.get("palette_specs") or {}).items()}
    return {"art_direction": mg.get("art_direction") or {},
            "palettes": pals or {k: dict(v) for k, v in PALETTES.items()}}


def _timeline_text(m: dict) -> str:
    return "; ".join(f"{x.get('t')} s: {x.get('what')}" for x in (m.get("timeline") or []) if isinstance(x, dict))


def to_plan(d: dict) -> dict:
    """The director's output in the shared story-plan shape (so validation, the UI, edits, single-scene
    regeneration and the film engine all work), with the motion-graphics fields kept on each scene."""
    pals = palettes_of(d)
    opt = (d.get("palette") or {}).get("option")
    opt = opt if isinstance(opt, int) else int(opt) if str(opt).isdigit() else None
    pal = pals.get(opt) if opt else None
    art = d.get("art_direction") if isinstance(d.get("art_direction"), dict) else {}
    art_text = ", ".join(str(art[k]) for k in ("set", "materials", "typography", "lighting", "texture") if art.get(k))
    look = "; ".join(x for x in (f"{pal['name']}: {pal['spec']}" if pal else "",
                                 f"art direction {art.get('name')}: {art_text}" if art.get("name") else "") if x)
    scenes = []
    for n, s in enumerate([x for x in d.get("scenes") or [] if isinstance(x, dict)], start=1):
        m = s.get("motion") if isinstance(s.get("motion"), dict) else {}
        line = str(s.get("script_line") or "").strip()
        scenes.append({
            "n": n, "title": f"{s.get('beat') or f'B{n:02d}'} - {s.get('typography_text') or s.get('hero_subject') or ''}"[:80],
            "duration_s": s.get("duration_s"), "purpose": s.get("visual_concept") or "",
            "what_happens": " ".join(x for x in (s.get("visual_concept"), m.get("hero_object_motion")) if x),
            "actions": _timeline_text(m) or str(m.get("hero_object_motion") or ""),
            "emotions": "", "environment": s.get("composition") or "", "positioning": s.get("composition") or "",
            "layers": s.get("composition") or "", "framing": str(m.get("camera") or "")[:200],
            "camera_movement": m.get("camera") or "", "lens": "", "lighting": s.get("lighting") or "",
            "atmosphere": s.get("graphic_elements") or "", "sfx": m.get("sfx") or "",
            "audio": {"type": "narration" if line else "none", "speaker": "Narrator", "line": line},
            "transition_in": "cut on the previous scene's outro" if n > 1 else "opening",
            "transition_out": m.get("transition_out") or "",
            "start_state": s.get("keyframe_prompt") or "", "end_state": s.get("end_frame") or m.get("transition_out") or "",
            "props": s.get("three_d_elements") or "",
            # The words are an editable text layer over the film (text_layer.py), never drawn into the keyframe.
            "in_scene_text": "", "overlay_text": s.get("typography_text") or "", "on_screen_text": "",
            "typography_style": s.get("typography_style") or "",
            "events": s.get("events") or [],  # the temporal sequence ids this beat shows
            "prompt": {"subject": s.get("hero_subject") or "", "environment": s.get("composition") or "",
                       "action": " ".join(x for x in (m.get("hero_object_motion"), m.get("three_d_animation")) if x),
                       "interaction": m.get("graphic_motion") or "", "emotion": s.get("visual_concept") or "",
                       "camera": m.get("camera") or "", "lighting": m.get("lighting") or s.get("lighting") or "",
                       "visual_design": " ".join(x for x in (s.get("three_d_elements"), s.get("color")) if x),
                       "motion": _timeline_text(m), "effects": m.get("background_motion") or "",
                       "continuity": f"locked palette - {look}" if look else "", "ending": m.get("transition_out") or ""},
            "motion_gfx": {k: s.get(k) for k in ("beat", "script_line", "visual_concept", "hero_subject",
                                                  "keyframe_prompt", "typography_text", "typography_style",
                                                  "three_d_elements", "graphic_elements", "composition", "lighting",
                                                  "color", "motion", "method")}})
    return {"title": d.get("title") or "", "logline": d.get("objective") or "",
            "story_concept": d.get("script") or "",
            "structure": [{"stage": b.get("role"), "scenes": [i + 1], "what": b.get("line")}
                          for i, b in enumerate(d.get("beats") or []) if isinstance(b, dict)],
            "bible": {"characters": [], "objects": [{"name": "hero subjects",
                                                     "appearance": "; ".join(str(s.get("hero_subject") or "")
                                                                             for s in d.get("scenes") or [])[:1500]}],
                      "world": {"palette": look, "background": "locked palette background, grid and accents",
                                "lighting": "premium directional key, soft fill, rim light, contact shadows"}},
            "style": {"visual_style": "Mixed-media kinetic editorial collage motion graphics (the user's playbook)"
                                      + (f" - {art['name']}" if art.get("name") else "")
                                      + (" - " + ", ".join(d.get("approach") or []) if d.get("approach") else ""),
                      "look": look, "palette_option": opt,
                      "effects_plan": [{"scene": n, "effect": (sc.get("motion") or {}).get("typography_motion") or "",
                                        "why": f"typography: {sc.get('typography_text') or ''}"}
                                       for n, sc in enumerate(d.get("scenes") or [], start=1) if isinstance(sc, dict)]},
            "audio_plan": {"music": "none (the playbook: no background music; SFX only inside the clips)",
                           "voice": "separate narration, one line per scene",
                           "sync_points": [f"scene {n}: {(sc.get('motion') or {}).get('sfx')}" for n, sc in
                                           enumerate(d.get("scenes") or [], start=1)
                                           if isinstance(sc, dict) and (sc.get("motion") or {}).get("sfx")][:12]},
            "scenes": scenes, "puzzle": None, "fact_notes": d.get("fact_notes") or [],
            "takeaway": next((b.get("line") for b in reversed(d.get("beats") or [])
                              if isinstance(b, dict) and b.get("role") == "closing"), ""),
            "motion_graphics": {"script": d.get("script"), "beats": d.get("beats") or [], "palette": d.get("palette"),
                                "palettes": {k: v["name"] for k, v in pals.items()},
                                "palette_swatches": {k: v.get("hex") or [] for k, v in pals.items()},
                                "palette_specs": pals, "art_direction": art,
                                "connection": d.get("connection") or " -> ".join(f"S{i:02d}" for i in range(1, len(scenes) + 1)),
                                "production": d.get("production") or {}, "approach": d.get("approach") or [],
                                "category": d.get("category"), "audience": d.get("audience")}}


def lint(plan: dict) -> list[dict]:
    """Stage-2 / Stage-4 rules the director must keep (on top of the shared scene rules)."""
    out = []
    for s in plan.get("scenes") or []:
        mg = s.get("motion_gfx") or {}
        line = str(mg.get("script_line") or "")
        if len(line.split()) > 14:
            out.append({"scene": s["n"], "area": "scenes", "problem": f"the line has {len(line.split())} words",
                        "fix": "at most 14 words per sentence in this format"})
        words = str(mg.get("typography_text") or "").strip()
        if words:
            if len(words.split()) > 4:
                out.append({"scene": s["n"], "area": "prompts", "problem": f"typography \"{words}\" is over 4 words",
                            "fix": "1-4 high-impact words"})
            norm = lambda x: [w.strip(".,!?;:'\"()").lower() for w in x.split()]
            if not set(norm(words)) <= set(norm(line)):
                out.append({"scene": s["n"], "area": "prompts",
                            "problem": f"typography \"{words}\" is not taken from the line",
                            "fix": "use exact words from the narration line"})
        if not str(mg.get("keyframe_prompt") or "").strip():
            out.append({"scene": s["n"], "area": "prompts", "problem": "no keyframe prompt",
                        "fix": "write the text-to-image keyframe"})
    return out
