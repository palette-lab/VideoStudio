"""3D Motion Graphics Director: the user's "Universal 3D Motion Graphics & Mixed-Media Production" master prompt
(prompts/motion_graphics_3d_master.md, verbatim) plus this app's runtime section, as one agent of Story Intelligence.
Full 3D: Section 4 of the master prompt in every scene, with the words as 3D letters inside the scene. Chosen for education, finance, business, marketing, psychology, science and
technology concepts, study / learning topics and commercial explainers (and whenever the user asks for 3D motion
graphics) - stories with characters, documentaries of real footage, presenters and cooking keep their own agents.

The master prompt's stages map onto this app:
    Stage 1  topic ideas            only when the input has no real topic (the chat offers 5 titles to pick)
    Stage 2  script + visual beats  HOOK -> REFRAME -> CORE MECHANISM -> INSIGHT -> CLOSING TRUTH, <= 14 words a line
    Stage 3  palette lock           3 palette directions; the director picks the best fit, the user can switch
    Stage 4  keyframe prompts       one art-directed full-3D keyframe per beat, the words as 3D letters in it
    Stage 5  motion prompts         the 6-second timeline adapted to each scene's clip length + SFX + transitions
    Stage 6  capability analysis    the live app registry (engines/video_intelligence.apps_summary)
    Stage 7-8 generation + QC       the film engine: keyframe -> AI clip -> frame / clip AI checks (incl. the
                                    spelling of the 3D words) -> joined film -> narration, music, effects
Each STOP of the master prompt is the app's approval step: the user reviews, switches the palette, edits or
regenerates a scene, then approves the render.
"""

import json
import random
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

# Realistic art-direction families. Each run offers a random handful so the look changes from video to video; the
# director picks the one that suits the topic (or invents a fresh one), then derives the palettes from the topic.
ART_DIRECTIONS = [
    "editorial science still life: the real object on a seamless paper sweep, soft window light, print-magazine layout",
    "museum specimen display: the subject on a plinth in a gallery set, gallery spotlights, engraved-label typography",
    "tabletop miniature world: real handmade props at small scale, macro lens, tilt-shift depth, warm practical lights",
    "paper craft and cut-card dioramas: layered real card stock, visible paper fibre, folded and stacked forms",
    "architectural maquette: white card and wood scale models, raking sunlight, long precise shadows",
    "industrial product launch: the real product on machined stages, studio strip lights, technical callout type",
    "botanical / natural history: real plants, stones, soil and water, soft daylight, field-guide typography",
    "kitchen-table documentary: everyday objects on real surfaces (wood, linen, concrete), natural mixed light",
    "stop-motion clay and felt: tactile hand-made forms with real fingerprints and fibres, cosy practical lights",
    "brutalist concrete set: raw concrete blocks and steps, hard sun and deep shadow, heavy slab typography",
    "retro print and risograph: real printed paper textures, halftone posters standing in the set, ink-bleed type",
    "night-city practicals: real objects lit by street, sign and window light spilling in (no neon materials)",
    "workshop and blueprint: real tools, pencils, rulers and drafting paper, warm lamp light, hand-lettered notes",
    "luxury material study: real stone, leather, linen and brushed brass surfaces, low-key controlled light",
    "sports and motion lab: real equipment, chalk lines and track markings, fast hard light, condensed numerals",
    "children's picture-book set: real painted wood toys and paper, bright soft light, rounded friendly type",
]

# The user's master prompt, verbatim (prompts/motion_graphics_3d_master.md), plus this app's runtime section.
MASTER = (Path(__file__).resolve().parent.parent / "prompts" / "motion_graphics_3d_master.md").read_text(encoding="utf-8")

APP_SECTION = """

---
## THIS APP (runtime information: how your stages run here - follow the master prompt above for everything else)

STAGES AND STOPS: here you write Stages 2, 3, 4, 5 and 6 in ONE response (as JSON below). The app then shows each
stage to the user at its approval step - script and beats, the art direction and palette (with buttons to switch to the other two),
every keyframe and motion prompt, and the production plan - and nothing is generated until the user approves. That
approval step is the master prompt's STOP / NEXT. Stage 1 only when the input has no real topic (see the end).
Stage 7-9 (generation, validation, delivery) is done by the app's film engine after approval.

FULL 3D (the user's direction for this studio): Section 4 is used in EVERY scene, not only when convenient.
- 4.2: every scene is a complete 3D environment (infinite studio, floating platforms, architectural or abstract
  dimensional space) with a defined foreground, middle ground and background - never a flat backdrop.
- 4.1 + 4.4: the hero is a real 3D object with purposeful 3D motion, and the camera moves in 3D (dolly, orbit, arc,
  crane, macro push-in, Z-axis) - camera movement never replaces object animation.
- 4.3: name the materials and the key / fill / rim lights, reflections, ambient occlusion and contact shadows.
- 4.5: the TYPOGRAPHY TEXT is built INTO the 3D scene as dimensional letters (solid extruded, layered, printed or
  painted, passing behind or emerging from objects - see REALISTIC LOOK for the material): write the exact words in quotes inside the keyframe prompt and say
  their material, size, position and how they sit in the space. Keep it to 1-3 words when possible (4 at most), so
  the image model spells them exactly; the app checks the spelling of every frame and redraws a wrong one.
- 4.6: exploded views only with components supported by the brief or the references.
- Section 5 (mixed media) appears only as 3D objects in the scene (paper-textured planes, extruded vector shapes,
  halftone panels standing in space, floating grid planes) - not as flat 2D overlays.
- The Stage 3 palettes become 3D worlds: the background colour is the studio floor / walls / sky of the space, the
  grid is a floor or wall plane, the accents are real materials (e.g. royal purple matte paint, teal powder-coated
  metal, terracotta ceramic). Use ONLY the locked palette's colours; never invent extra accents (only real photos and the user's
  product keep their own colours).

ART DIRECTION - DIFFERENT FOR EVERY VIDEO (the user's direction): there is no house style. Design a look for THIS
topic, as a creative director would, so that two videos on different topics never look alike - and two videos on
the same topic still get their own take. Start from the subject's real world (its real colours, materials, places,
tools and culture), pick the best fitting option from STYLE OPTIONS FOR THIS VIDEO below (or blend two, or invent a
better one), and describe it in "art_direction". Then write THREE palettes for this topic (Stage 3): three
genuinely different colour directions drawn from the subject (not generic presets), each with 3-5 HEX colours
(background first, then accents, then type). Every scene keeps the chosen art direction and the locked palette -
the variety is between videos, never inside one film.
STYLE OPTIONS FOR THIS VIDEO: {STYLES}

REALISTIC LOOK (the user's direction; it overrides the material and typography lists in 4.3 and 4.5, whatever the
art direction):
- Every keyframe reads as a real photograph of a real, physical set - not a stylised CGI toy. The hero looks like
  the REAL thing, true to its real material, shape, colour and surface detail (e.g. a brain is anatomically
  accurate matte organic tissue; a coin is worn real metal; a plant is a living plant; a phone is the real device).
  Accents may take the palette's colours, but the hero keeps its real substance.
- NEVER make the hero, the letters or the set out of glass, crystal, clear or tinted acrylic, transparent resin,
  jelly, liquid metal, polished chrome, mirror finishes, marble, iridescent / holographic / pearlescent films or
  glowing neon edges - unless the USER INPUT explicitly asks for that material.
- Light it like a real photo shoot that suits the art direction (soft key and fill, hard sun, practicals, low-key
  spot...), with natural contact shadows, ambient occlusion and real depth of field. No random reflections,
  lens-flare sparkles or plastic sheen.
- Typography fits the art direction (choose the typeface style, weight and placement for THIS video: condensed
  sans, slab, serif, rounded, hand-lettered, engraved...), made of a real solid material - matte paint, paper,
  card, wood, stone, metal plate, printed ink - never glass, chrome or neon.
- Mixed-media accents come from the art direction and stay tactile and real (paper, card, print, string, tape,
  chalk, the subject's own tools).
- Write it into every keyframe_prompt: "photorealistic, real <material> texture, <the art direction's photography
  style>".

MOTION PROMPTS (Stage 5): the clip model (Kling / Seedance) animates each keyframe for 3-10 s. Adapt the six-second
timeline to the scene's real length. The 3D letters stay rigid, solid and readable for the whole clip (they may move
as one object - rise, rotate a little, slide behind the hero - but never morph, melt, re-spell or flicker). SFX only
inside the clips: the narration is recorded separately.

CAPABILITIES (Stage 6) - only what these apps can really do; never invent capabilities:
{APPS}
Here every 3D scene is "photorealistic 3D appearance generated directly by a video model" (Stage 6, method 1):
keyframe image (Nano Banana Pro) -> image-to-video clip (Kling 3.0 Pro / Seedance) -> AI frame and clip checks ->
joined film -> narration, music, effects. Say so in "production" and name the honest limitations (no editable 3D
object; very small or long text may come out misspelled and is redrawn).

LENGTH: {LENGTH}. PALETTE: {PALETTE_RULE}

FACTS: state only what the RESEARCH given supports (cite it in fact_notes); no fake statistics.

Return ONLY JSON:
{"needs_topic": false, "topic_ideas": [],
 "title": "...", "category": "...", "objective": "...", "audience": "...", "approach": ["..."],
 "script": "the complete narration", "beats": [{"id": "B01", "line": "exact sentence", "role": "hook | reframe |
  mechanism | insight | closing"}],
 "art_direction": {"name": "a short name for this video's look", "concept": "why this look suits the topic",
  "set": "the physical world the scenes live in", "materials": "real materials of hero, set and letters",
  "typography": "typeface style, weight, material and placement", "lighting": "the photography lighting",
  "camera": "lens and camera language", "texture": "grain, print, surface character"},
 "palettes": [{"option": 1, "name": "...", "spec": "background, accents, type and materials in words",
  "hex": ["#bg", "#accent", "#type"]}, {"option": 2, ...}, {"option": 3, ...}],
 "palette": {"option": 1 | 2 | 3, "name": "...", "why": "why it fits this topic"},
 "scenes": [{"n": 1, "beat": "B01", "script_line": "exact sentence", "duration_s": 3-10 (about the time to say the
  line plus a beat to read the type), "visual_concept": "what the viewer sees", "hero_subject": "main subject and
  visual metaphor", "keyframe_prompt": "the complete TEXT-TO-IMAGE PROMPT (100-170 words): the full 3D scene, with
  the typography words in quotes as 3D letters", "typography_text": "1-4 exact words from the line",
  "typography_style": "3D letter material, weight, scale, position, how it sits in the space",
  "three_d_elements": "geometry, materials, depth, lighting", "graphic_elements": "mixed-media accents as 3D
  objects", "composition": "foreground / middle ground / background and placement", "lighting": "...",
  "color": "the locked palette for this frame",
  "motion": {"style": "...", "hero_object_motion": "...", "three_d_animation": "...", "typography_motion": "...",
   "graphic_motion": "...", "background_motion": "...", "camera": "...", "lighting": "...", "sfx": "timed SFX",
   "transition_out": "how it connects to the next scene",
   "timeline": [{"t": "0.0-0.5", "what": "OPEN ..."}, {"t": "...", "what": "KINETIC BUILD ..."},
                {"t": "...", "what": "MICRO-MOTION AND HOLD ..."}, {"t": "...", "what": "OUTRO ..."}]},
  "method": "ai_clip", "end_frame": "the final composition handed to the next scene"}],
 "connection": "S01 -> S02 -> ...",
 "production": {"application": "...", "model": "...", "method": "...", "resolution": "...", "aspect_ratio": "...",
  "audio": "...", "limitations": ["honest compromises"]},
 "fact_notes": [{"claim": "...", "source": "..."}]}
STAGE 1 here: when the input has NO real topic (e.g. "make me a video", "finance video ideas", "something about
psychology"), return {"needs_topic": true, "topic_ideas": ["VIDEO TITLE 01", ..., "VIDEO TITLE 05"]} and leave the
rest empty. The five must be DIFFERENT angles across the domain (e.g. for finance: a spending habit, an investing
idea, a history story, a money myth, a behavioural trap) - never five variations of one subject."""

DIRECTOR = MASTER + APP_SECTION


def palette_rule(option: int | None, palettes: dict | None = None) -> str:
    pals = palettes or PALETTES
    if option in pals:
        return (f"LOCKED by the user: option {option} ({pals[option]['name']}). Use it in every scene; never "
                "change it.")
    return ("choose the palette that fits this topic best and lock it for every scene (the user can switch it before "
            "the render).")


def style_options(k: int = 4, rng: random.Random | None = None) -> list[str]:
    """A random handful of art directions for this run, so each video starts from a different place."""
    return (rng or random).sample(ART_DIRECTIONS, k)


def messages(prompt: str, cls: dict, res: dict | None, files_note: str, apps: str, aspect_ratio,
             palette: int | None = None, keep_script: str | None = None, keep_style: dict | None = None) -> list[dict]:
    length = (f"Target {cls['duration_s']} seconds" if cls.get("duration_s") else "Target 15-60 seconds")
    pals = (keep_style or {}).get("palettes")
    styles = "\n".join(f"- {x}" for x in style_options())
    system = MASTER + (APP_SECTION.replace("{LENGTH}", length).replace("{PALETTE_RULE}", palette_rule(palette, pals))
                       .replace("{STYLES}", "\n" + styles)
                       .replace("{APPS}", apps or "(registry unavailable)"))
    user = [f"USER INPUT:\n{prompt.strip()}", f"FORMAT: {aspect_ratio or '9:16'}", f"FILES: {files_note}",
            f"UNDERSTANDING: {json.dumps({k: cls.get(k) for k in ('category', 'intent', 'subject', 'audience', 'tone', 'language')}, ensure_ascii=False)}"]
    if res:
        user.append("RESEARCH (the only facts you may state):\n" + json.dumps(
            {k: res.get(k) for k in ("summary", "angle", "facts", "timeline", "note")}, ensure_ascii=False)[:12000])
    if keep_script:
        user.append(f"KEEP THIS APPROVED SCRIPT WORD FOR WORD (only the palette changes):\n{keep_script}")
    if keep_style:
        user.append("KEEP THIS ART DIRECTION AND THESE THREE PALETTES exactly (return them unchanged); only the locked "
                    "palette changes:\n" + json.dumps(keep_style, ensure_ascii=False))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]


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
            # Full 3D: the words are 3D letters inside the keyframe (master prompt 4.5), not a flat overlay added later.
            "in_scene_text": s.get("typography_text") or "", "on_screen_text": "",
            "typography_style": s.get("typography_style") or "",
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
            "style": {"visual_style": "Full 3D motion graphics (the user's master prompt, Section 4 in every scene)"
                                      + (f" - {art['name']}" if art.get("name") else "")
                                      + (" - " + ", ".join(d.get("approach") or []) if d.get("approach") else ""),
                      "look": look, "palette_option": opt,
                      "effects_plan": [{"scene": n, "effect": (sc.get("motion") or {}).get("typography_motion") or "",
                                        "why": f"typography: {sc.get('typography_text') or ''}"}
                                       for n, sc in enumerate(d.get("scenes") or [], start=1) if isinstance(sc, dict)]},
            "audio_plan": {"music": "none inside the clips (SFX only); a light bed may be mixed under the narration",
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


# Stylised materials the user does not want unless they ask for them (the REALISTIC LOOK rule above).
STYLIZED = re.compile(r"\b(glass(y)?|crystal(line)?|acrylic|resin|translucent|transparent|see-through|jelly|"
                      r"liquid[- ]metal|chrome|chromed|mirror(ed)?|marble|iridescen\w*|holograph\w*|pearlescen\w*|"
                      r"neon)\b", re.I)


def wants_stylized(prompt: str) -> bool:
    """The user asked for glass / chrome / marble etc. themselves, so the realism rule steps aside."""
    return bool(STYLIZED.search(prompt or ""))


def lint(plan: dict, stylized_ok: bool = False) -> list[dict]:
    """Stage-2 / Stage-4 rules the director must keep (on top of the shared scene rules)."""
    out = []
    for s in plan.get("scenes") or []:
        mg = s.get("motion_gfx") or {}
        if not stylized_ok:
            look = " ".join(str(mg.get(k) or "") for k in ("keyframe_prompt", "typography_style", "three_d_elements"))
            bad = sorted({m.group(0).lower() for m in STYLIZED.finditer(look)})
            if bad:
                out.append({"scene": s["n"], "area": "prompts",
                            "problem": f"stylised materials ({', '.join(bad)}) instead of a realistic look",
                            "fix": "real-world materials true to the subject, matte letters, natural studio "
                                   "photography (REALISTIC LOOK)"})
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
