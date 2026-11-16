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
from pathlib import Path

PALETTES = {
    1: {"name": "Signature Editorial",
        "spec": "warm off-white paper background (#F4EFE6) with a fine grey dashed grid (#CFC8BC); deep royal purple "
                "accents (#4B2A91, #6C3FD1); bold black (#141414) and purple typography; soft editorial shadows; "
                "premium photographic and 3D elements; exactly two faint diagonal background lines in low-contrast "
                "purple-grey"},
    2: {"name": "Dark Technical Minimal",
        "spec": "slate / charcoal background (#151A21, #1E252E); neon teal and cyan accents (#14E0C8, #2BC8F2); subtle "
                "luminous atmosphere; crisp white (#F5F7FA) and teal typography; brushed and polished metal 3D "
                "materials; technical geometric line elements"},
    3: {"name": "High-Contrast Academic",
        "spec": "pale cream / bone background (#F2EBDD) with a thin cross-dot grid (#D8CDB8); deep terracotta and "
                "burnt-orange shapes (#B4532A, #E07B39); charcoal (#2B2B2B) and orange typography; editorial "
                "cutouts; warm dimensional lighting"},
}

# The user's master prompt, verbatim (prompts/motion_graphics_3d_master.md), plus this app's runtime section.
MASTER = (Path(__file__).resolve().parent.parent / "prompts" / "motion_graphics_3d_master.md").read_text(encoding="utf-8")

APP_SECTION = """

---
## THIS APP (runtime information: how your stages run here - follow the master prompt above for everything else)

STAGES AND STOPS: here you write Stages 2, 3, 4, 5 and 6 in ONE response (as JSON below). The app then shows each
stage to the user at its approval step - script and beats, the palette (with buttons to switch to the other two),
every keyframe and motion prompt, and the production plan - and nothing is generated until the user approves. That
approval step is the master prompt's STOP / NEXT. Stage 1 only when the input has no real topic (see the end).
Stage 7-9 (generation, validation, delivery) is done by the app's film engine after approval.

FULL 3D (the user's direction for this studio): Section 4 is used in EVERY scene, not only when convenient.
- 4.2: every scene is a complete 3D environment (infinite studio, floating platforms, architectural or abstract
  dimensional space) with a defined foreground, middle ground and background - never a flat backdrop.
- 4.1 + 4.4: the hero is a real 3D object with purposeful 3D motion, and the camera moves in 3D (dolly, orbit, arc,
  crane, macro push-in, Z-axis) - camera movement never replaces object animation.
- 4.3: name the materials and the key / fill / rim lights, reflections, ambient occlusion and contact shadows.
- 4.5: the TYPOGRAPHY TEXT is built INTO the 3D scene as dimensional letters (extruded, metallic, glass, layered,
  passing behind or emerging from objects): write the exact words in quotes inside the keyframe prompt and say
  their material, size, position and how they sit in the space. Keep it to 1-3 words when possible (4 at most), so
  the image model spells them exactly; the app checks the spelling of every frame and redraws a wrong one.
- 4.6: exploded views only with components supported by the brief or the references.
- Section 5 (mixed media) appears only as 3D objects in the scene (paper-textured planes, extruded vector shapes,
  halftone panels standing in space, floating grid planes) - not as flat 2D overlays.
- The Stage 3 palettes become 3D worlds: the background colour is the studio floor / walls / sky of the space, the
  grid is a floor or wall plane, the accents are materials (e.g. royal purple glass, teal emissive, terracotta
  ceramic). Use ONLY the locked palette's colours; never invent extra accents (only real photos and the user's
  product keep their own colours).

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
Palettes (Stage 3): {PALETTES}

FACTS: state only what the RESEARCH given supports (cite it in fact_notes); no fake statistics.

Return ONLY JSON:
{"needs_topic": false, "topic_ideas": [],
 "title": "...", "category": "...", "objective": "...", "audience": "...", "approach": ["..."],
 "script": "the complete narration", "beats": [{"id": "B01", "line": "exact sentence", "role": "hook | reframe |
  mechanism | insight | closing"}],
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


def palette_rule(option: int | None) -> str:
    if option in PALETTES:
        return (f"LOCKED by the user: option {option} ({PALETTES[option]['name']}). Use it in every scene; never "
                "change it.")
    return ("choose the palette that fits this topic best and lock it for every scene (the user can switch it before "
            "the render).")


def messages(prompt: str, cls: dict, res: dict | None, files_note: str, apps: str, aspect_ratio,
             palette: int | None = None, keep_script: str | None = None) -> list[dict]:
    length = (f"Target {cls['duration_s']} seconds" if cls.get("duration_s") else "Target 15-60 seconds")
    system = MASTER + (APP_SECTION.replace("{LENGTH}", length).replace("{PALETTE_RULE}", palette_rule(palette))
                       .replace("{PALETTES}", json.dumps({k: v["name"] + ": " + v["spec"] for k, v in PALETTES.items()}))
                       .replace("{APPS}", apps or "(registry unavailable)"))
    user = [f"USER INPUT:\n{prompt.strip()}", f"FORMAT: {aspect_ratio or '9:16'}", f"FILES: {files_note}",
            f"UNDERSTANDING: {json.dumps({k: cls.get(k) for k in ('category', 'intent', 'subject', 'audience', 'tone', 'language')}, ensure_ascii=False)}"]
    if res:
        user.append("RESEARCH (the only facts you may state):\n" + json.dumps(
            {k: res.get(k) for k in ("summary", "angle", "facts", "timeline", "note")}, ensure_ascii=False)[:12000])
    if keep_script:
        user.append(f"KEEP THIS APPROVED SCRIPT WORD FOR WORD (only the palette changes):\n{keep_script}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]


def _timeline_text(m: dict) -> str:
    return "; ".join(f"{x.get('t')} s: {x.get('what')}" for x in (m.get("timeline") or []) if isinstance(x, dict))


def to_plan(d: dict) -> dict:
    """The director's output in the shared story-plan shape (so validation, the UI, edits, single-scene
    regeneration and the film engine all work), with the motion-graphics fields kept on each scene."""
    opt = (d.get("palette") or {}).get("option")
    pal = PALETTES.get(opt if isinstance(opt, int) else int(opt) if str(opt).isdigit() else 0)
    look = f"{pal['name']}: {pal['spec']}" if pal else ""
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
            "style": {"visual_style": "Full 3D motion graphics (the user's master prompt, Section 4 in every scene) - "
                                      + ", ".join(d.get("approach") or []),
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
                                "palettes": {k: v["name"] for k, v in PALETTES.items()},
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
