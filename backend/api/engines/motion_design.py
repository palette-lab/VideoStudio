"""AI motion-design rules for the finishing pass (explainers, product films, exploded views, ads).

Condensed from prompts/motion_design_system.md (the "Palette VideoStudio - AI Motion Design Prompt System", itself
adapted from the SFERRO21 AI Edit Battle Guide). That file is the source of truth; this module carries the parts the
pipeline actually uses, sized to fit the prompts:

- DIRECTOR_RULES (the doc's Prompt A, brief builder): the graphics director classifies the footage into a treatment,
  sets a design language, a concept and an energy curve, and stays truthful about what the footage shows.
- CRAFT (the doc's Prompt B, execution): the level of animation craft Motion Studio must hit - timing, easing,
  typography finish, effect budget, transitions, motion blur, layout - in terms HyperFrames/GSAP can render.
- KIND_MOTION: how each overlay kind animates, including the electronics and exploded-view vocabulary.

The LLM only ever returns a structured plan; the renderer builds every graphic from validated kinds (doc section 5).
"""

TREATMENTS = {
    "product": "PRODUCT EXPLAINER / AD: the product is the hero - never cover its silhouette, controls, materials or "
               "branding. Product-anchored callouts with leader lines, measurement ticks, feature tags, a restrained "
               "light sweep or glint that matches the product's real lighting, spec chips only from the brief, a "
               "deliberate hero reveal and a polished closing composition.",
    "electronic_device": "ELECTRONIC / ELECTRICAL DEVICE: technical language that explains the visible device - circuit "
                         "traces and signal paths that flow in one clear direction, node pulses, connection lines, "
                         "callouts on visible ports, buttons, screens and chips, a fine grid and measurement marks. "
                         "Never fabricate a circuit, voltage, speed or internal architecture; no random sci-fi HUD "
                         "clutter - every technical line explains a real relationship or guides the eye.",
    "exploded_view": "EXPLODED VIEW: when the footage really separates parts, label each visible part (numbered, with "
                     "a leader line that stays on that part), alignment guides along the separation axis, directional "
                     "arrows, numbered assembly stages, timed to the real separation / reassembly moment. If the "
                     "footage does NOT separate parts, do not pretend: no part labels on a closed product.",
    "cinematic": "CINEMATIC STORY: protect the emotional rhythm, faces and action. Typography only where it "
                 "strengthens the story (a title moment, a closing line); motivated light transitions, no technical "
                 "labels or product callouts on narrative shots.",
    "speech_led": "SPEECH-LED / TALKING HEAD: kinetic words land on the real spoken words, counters and comparisons "
                  "for numbers that are said, faces and expressions always readable, graphics at the edges.",
    "graphic": "GRAPHIC / PROMOTIONAL: typography, data, split comparisons, icon systems and a branded end card.",
    "product_film": "PRODUCT FILM, NO PERSON (music-led): benefit tags pop in one by one AROUND the product (small "
                    "rounded pills in a colour taken from the packaging, short serif-italic words like 'Lightweight', "
                    "'Non-sticky'), an ingredient name with a one-line benefit at the top of texture / ingredient "
                    "shots, small sparkle or droplet accents, and an end card with the brand name on a clean or black "
                    "frame. Text never covers the product or its label; most shots stay clean.",
}

# Default treatment per genre (a hint; the director decides from the frames).
GENRE_TREATMENT = {"product_film": "product_film", "advertisement": "product", "ecommerce": "product", "unboxing": "product", "ugc_ad": "speech_led",
                   "exploded_view": "exploded_view", "explainer": "electronic_device", "banner": "graphic",
                   "food": "product", "real_estate": "cinematic", "cinematic": "cinematic", "fashion": "cinematic",
                   "action": "cinematic", "music_video": "cinematic", "documentary": "cinematic",
                   "travel": "cinematic", "animation": "graphic"}

# How much finishing a film gets. "full": creator effects AND motion design are always added - any film where a
# person talks or explains, and any product, device, ad or explainer film. "clean": action and cinematic films keep
# their picture clean and get only the narration, a typographic hook and a few title words (no callouts, no effects).
FULL_FINISH_GENRES = {"product_film", "ugc_ad", "unboxing", "exploded_view", "ecommerce", "banner", "advertisement", "explainer",
                      "food", "real_estate"}
FULL_FINISH_TYPES = {"commercial", "product_showcase", "product_animation", "explainer", "educational",
                     "talking_presenter", "social_short"}
PRODUCT_WORDS = (" product", " device", " gadget", "phone", "laptop", "tablet", "headphone", "earbud", "speaker",
                 "smartwatch", "charger", "battery", "appliance", "electric", "electronic", "circuit", "serum",
                 "skincare", "cosmetic", " cream", " brand", " review", "explain", "tutorial", "how to", "how it works",
                 " demo", "unbox", " sneaker", " shoe", " ad ", " ad.", " ad,", " ads ", "advert", "commercial",
                 "promo", "launch")
CLEAN_KINDS = {"kinetic_text", "lower_third"}


def finish_level(genre: str | None, prompt: str = "", production_types=(), person_talks: bool = False) -> str:
    """'full' or 'clean' (see above). Someone talking or explaining always gets the full treatment."""
    if person_talks or (genre or "") in FULL_FINISH_GENRES or set(production_types or ()) & FULL_FINISH_TYPES:
        return "full"
    text = " " + " ".join((prompt or "").lower().split()) + " "
    return "full" if any(w in text for w in PRODUCT_WORDS) else "clean"


def finish_summary(level: str) -> str:
    return ("Effects and motion design: creator effects (punch-ins, pop words, highlights) plus a hook, callouts, "
            "labels and an end card, timed to what is said and shown." if level == "full" else
            "Clean finish: narration, a typographic hook and a few title words; no callouts or effects over the picture.")

DIRECTOR_RULES = """

MOTION DESIGN DIRECTION (applies whenever you add graphics):
1. Understand before designing. From the frames, story and narration decide what the footage REALLY contains and
   classify it: "treatment" = one of product | electronic_device | exploded_view | cinematic | speech_led | graphic
   (one primary; a scene may use another - list those in "scene_treatments"). The hint "treatment_hint" comes from
   the genre; override it when the frames say otherwise (e.g. an "explainer" about a kitchen knife is "product").
   Treatment vocabularies:
{TREATMENTS}
2. Design language: one coherent system - "design": {"accent": "#hex taken from the footage or the brand", "text":
   "#hex", "panel": "dark glass | light glass | none", "type": "display / support / annotation choice from system
   fonts", "lines": "line weight and corner language"}. One accent colour; never recolour the product or its logo.
3. "concept": ONE unifying visual idea (e.g. "precision revealed by thin cyan measurement lines"), and
   "energy_curve": the hook, the build, the peak (and why it is the peak), a breathing moment, the payoff - with times.
4. Three scales in each big graphic moment: PRIMARY (one dominant word, number or the product), SECONDARY
   (supporting lines, diagram, data), MICRO (ticks, part numbers, small labels) - one readable focal point.
5. Effect budget: a few polished, meaningful graphics beat many. Each signature treatment (light sweep, glow,
   circuit trace, hero type) at most twice in the film; keep the rest clean. Short clips get fewer layouts.
6. Truth: label only what is visible or given in the brief. Never invent specs, ports, components, certifications
   or claims. "circuit" only for an electronic device and only along visible parts; "part_label" / "guide" only on
   a scene whose frame shows parts actually separated.
Extra overlay kinds for these treatments (use the same fields as the others):
- "circuit": a signal path drawn as thin traces with node pulses running between two visible points of the device
  (target = the area it runs through; "detail" = what flows, e.g. "power", "audio signal").
- "part_label": a numbered label on ONE separated part of an exploded view (text = part name, value = its number).
- "guide": a thin dashed alignment axis along the separation direction of an exploded view.
- "compare": a split comparison panel (text = left side, detail = right side) - only facts from the brief.
- "sweep": a single specular light sweep across the product's surface in the target box.
Also return: "treatment", "scene_treatments": [{"scene": n, "treatment": "..."}], "design", "concept",
"energy_curve": [{"at": seconds, "beat": "hook | build | peak | breathe | payoff", "what": "..."}]."""

CRAFT = """
MOTION CRAFT (broadcast / product-film studio level - every frame intentionally designed; 30 fps):
- Entrances 8-14 frames (0.27-0.47 s) with a strong ease-out (expo.out / power4.out); exits 5-8 frames (0.17-0.27 s)
  with ease-in; transitions 6-12 frames. Linear only for counters and continuous drifts.
- Springs for panels and big type: slight overshoot only (3-6%, e.g. back.out(1.2)); 2-3 frames of anticipation
  before decisive moves; secondary elements settle 3-8 frames after the primary (follow-through).
- Stagger 1-2 frames per character or word, 2-4 frames between related objects. Heavier objects move with more inertia.
- Typography as designed objects: mask reveals (clip-path), per-word offsets, scale pushes, line builds. Finish
  hierarchy: BASE (crisp clean type) for most text, EMPHASIS (one treatment: tight glow, sheen, self-drawing
  underline or outline echo) for a key word, HERO (layered) only for the 1-2 strongest moments. A glow blooms on
  impact and settles over 6-10 frames; a sheen passes once; never a glow haze over the whole frame.
- Numbers behave like values: count up / roll with GSAP, ease into the final value, keep the final value exact.
- Leader lines and traces draw themselves (stroke-dashoffset) from the anchor point outward, then the label types
  or masks in; labels stay attached to their target box for their whole life.
- Graphic passages keep developing about every 0.5 s (staged reveals, secondary motion, a 1-3% scale drift during
  holds) - but cinematic footage may hold still on purpose.
- Fast-moving panels and type may use a short motion-blur feel (a brief scaleX stretch or blur filter on the move
  only); settled text and fine labels stay perfectly crisp.
- Transitions need a reason: match move, mask wipe, light sweep, panel resize; a glitch only as a rare accent.
- The footage is the co-lead: never leave it in a tiny window, never cover faces, eyes, mouths, the product's
  silhouette or its key details. Text that quotes speech uses the exact words; no permanent subtitle strip."""

KIND_MOTION = {
    "circuit": "thin 2 px traces in the accent colour draw themselves along right-angled paths through the target box "
               "(stroke-dashoffset), small round nodes pulse once as the signal passes, a soft glow travels along the "
               "path in ONE direction, then the trace fades; a tiny label names what flows",
    "part_label": "a numbered round chip snaps onto the part inside the target box, a leader line draws out to the part "
                  "name, both stay locked to that part and leave together",
    "guide": "a thin dashed axis line grows along the separation direction through the target box with small "
             "spacing ticks, holds while the parts are apart, then retracts",
    "compare": "a clean split panel: two halves slide in from opposite sides with a thin divider, each side's text "
               "masks in, the stronger side gets the accent colour",
    "sweep": "one soft diagonal specular gradient passes once across the target box (screen blend, max 40% opacity), "
             "with a tiny glint at its brightest point",
}


def director_rules() -> str:
    return DIRECTOR_RULES.replace("{TREATMENTS}", "\n".join(f"   - {k}: {v}" for k, v in TREATMENTS.items()))


def treatment_hint(genre: str | None) -> str:
    return GENRE_TREATMENT.get(genre or "", "graphic")


# Section 6 of the motion design document (effect registry families), as styles Motion Studio (HTML/GSAP) can render.
# Every film draws its own signature from these, leaning away from recent films, so the graphics don't all look alike.
TYPE_STYLES = ["kinetic word reveal (words rise in one by one)", "character stagger (letters cascade in)",
               "mask reveal (text wipes in behind a clip-path edge)", "tracking / weight change (letters breathe "
               "wider and bolder on the key word)", "scale push (the word lands big and settles)",
               "line-by-line build", "stroke / outline type that fills in", "underline that draws itself",
               "light sweep across the type", "subtle extrusion with a soft long shadow",
               "rolling number counter for real numbers", "text-to-shape transition (a word morphs into a "
               "line, ring or bar that becomes the next graphic)"]
ACCENT_STYLES = ["tight glow / bloom on the key graphic", "a single light sweep", "specular sheen on the product",
                 "a restrained lens flare at the peak", "motion streaks on fast moves", "a shockwave ring on impact",
                 "an impact flash at the peak cut", "a small particle burst on the reveal", "fine film grain",
                 "a soft vignette that focuses the subject", "motion trails behind moving labels",
                 "a depth-of-field accent behind the type"]
TRANSITION_STYLES = ["split screen for comparisons", "video panel transform (the footage shrinks into a panel)",
                     "parallax layers", "2.5D layered move", "mask wipe between moments", "motion wipe",
                     "whip transition", "light transition (a bloom carries the cut)", "shape transformation",
                     "camera push / pull into the graphic"]
TECH_STYLES = ["target marker that locks on", "connection map between parts", "schematic overlay",
               "data-flow arrows", "technical panel with fine grid", "part sequence markers"]
ACCENT_KINDS = ("flash", "light_sweep", "whip_lines", "glow_pulse", "shockwave_ring", "particle_burst",
                "motion_streak", "impact_flash", "sheen", "light_transition", "mask_wipe")


def pick_signature(treatment: str | None = None, rng=None) -> dict:
    """This film's motion-design signature: 2 typography styles, 2 accents, 1 transition (+1 technical graphic for
    products and devices), drawn at random but away from what recent films used."""
    import random
    from . import approaches
    rng = rng or random
    recent = approaches.recent("motion")[-30:]

    def take(pool, n):
        w = [1.0 / (1 + 3 * recent.count(x)) for x in pool]
        out = []
        for _ in range(n):
            items = [(x, wx) for x, wx in zip(pool, w) if x not in out]
            out.append(rng.choices([x for x, _ in items], weights=[wx for _, wx in items])[0])
        return out
    sig = {"type": take(TYPE_STYLES, 2), "accents": take(ACCENT_STYLES, 2), "transition": take(TRANSITION_STYLES, 1)}
    if treatment in ("product", "electronic_device", "exploded_view"):
        sig["tech"] = take(TECH_STYLES, 1)
    return sig


def remember_signature(sig: dict) -> None:
    from . import approaches
    for v in sig.values():
        for x in v:
            approaches.remember("motion", x, keep=60)


def signature_rules(sig: dict) -> str:
    parts = [f"typography: {'; '.join(sig['type'])}", f"accents: {'; '.join(sig['accents'])}",
             f"transition: {'; '.join(sig['transition'])}"] + ([f"technical graphic: {sig['tech'][0]}"]
                                                                 if sig.get("tech") else [])
    return ("\n\nTHIS FILM'S SIGNATURE (picked so every film looks different - build the design language around it, "
            "still within the effect budget and still truthful): " + " | ".join(parts) + ". Accent kinds you may use: "
            + " | ".join(ACCENT_KINDS) + ".")


def brief_header(gp: dict) -> list[str]:
    """The director's art direction as the first lines of the Motion Studio brief."""
    out = []
    t = str(gp.get("treatment") or "")
    if t in TREATMENTS:
        out.append(f"TREATMENT: {TREATMENTS[t]}")
    per = [f"scene {s.get('scene')}: {s.get('treatment')}" for s in gp.get("scene_treatments") or []
           if isinstance(s, dict) and s.get("treatment") in TREATMENTS and s.get("treatment") != t]
    if per:
        out.append("Scene treatments that differ: " + "; ".join(per) + ".")
    if gp.get("concept"):
        out.append(f"CONCEPT (one unifying idea, carry it through every graphic): {gp['concept']}")
    sig = gp.get("signature") or {}
    if sig:
        out.append("SIGNATURE STYLE (use these, they make this film different from the last ones): typography - "
                   + "; ".join(sig.get("type") or []) + ". Accents - " + "; ".join(sig.get("accents") or [])
                   + ". Transition - " + "; ".join(sig.get("transition") or [])
                   + (". Technical graphic - " + sig["tech"][0] if sig.get("tech") else "") + ".")
    d = gp.get("design") or {}
    if isinstance(d, dict) and d:
        out.append("DESIGN LANGUAGE: " + "; ".join(f"{k}: {v}" for k, v in d.items() if v) + ".")
    curve = [f"{float(c.get('at') or 0):.1f} s {c.get('beat')}: {c.get('what')}" for c in gp.get("energy_curve") or []
             if isinstance(c, dict) and c.get("beat")]
    if curve:
        out.append("ENERGY CURVE (biggest motion at the peak, calm at the breathing moment): " + " | ".join(curve))
    out.append(CRAFT.strip())
    return out


# ------------------------------------------------------------------ planning: the edit is designed with the script
# The doc's Prompt A, moved up to the planner: the film is written and shot FOR its finishing edit, and the creative
# direction it picks (treatment, concept, accent, energy curve, planned moments) is handed to the effects and graphics
# passes, so all three stages work from one direction instead of guessing separately.

PLANNING_RULES = """

EDIT-READY FILM (motion design is added after the footage; plan the film for it - this is a professional edit, not
an afterthought):
1. Creative direction first. Decide the film's treatment (one of: {TREATMENT_LIST}; the genre suggests
   "{HINT}"), ONE unifying visual concept, an accent colour taken from the product, brand or world of the film, and
   an energy curve over the scenes: hook -> build -> peak -> breathe -> payoff.
2. Shoot for the edit:
   - Scene 1 is the hook: an image that reads in under 2 seconds and makes the viewer stay.
   - When there is a product, at least one scene holds it large, sharp and STEADY for 2-3 seconds (no fast camera
     move, no spin) so labels, callouts or a measurement can sit on it.
   - Compose with clean negative space (about a third of the frame, the side away from faces and the product) where
     words and graphics will go; never frame so tight that text must cover a face or the product.
   - One calm breathing beat before or after the peak. The last scene is a clean closing frame / packshot with room
     for the end card.
   - An exploded view or part labels only when the scene really shows the parts separating. Never plan graphics to
     fake what the footage does not show.
3. Write the script for the edit: every spoken line carries ONE short key word, name or real number the edit can
   animate (from the brief only; never invent specs, prices or claims). Scene 1's line is the hook; the last
   scene's line is the payoff / call to action.
4. Return, next to "scenes", a top-level "edit_direction":
   {"treatment": "...", "concept": "one sentence", "design": {"accent": "#hex", "text": "#hex",
    "panel": "dark glass | light glass | none"},
    "energy_curve": [{"scene": 1, "beat": "hook | build | peak | breathe | payoff", "what": "..."}],
    "graphic_moments": [{"scene": n, "kind": "{KIND_LIST}", "text": "the words or value", "why": "..."}],
    "effect_moments": [{"scene": n, "idea": "the creator-style effect idea, e.g. a big word on the product name"}]}
   Few, meaningful moments (a short film: 2-4 graphic moments, 2-3 effect moments)."""

DIRECTION_KINDS = ("callout", "measure", "spec_badge", "kinetic_text", "lower_third", "label", "step", "arrow",
                   "highlight", "circuit", "part_label", "guide", "compare", "sweep")
BEATS = {"hook", "build", "peak", "breathe", "payoff"}


def planning_rules(genre: str | None) -> str:
    return (PLANNING_RULES.replace("{TREATMENT_LIST}", " | ".join(TREATMENTS)).replace("{HINT}", treatment_hint(genre))
            .replace("{KIND_LIST}", " | ".join(DIRECTION_KINDS)))


def _scene_no(v, n: int) -> int | None:
    try:
        s = int(v)
    except (TypeError, ValueError):
        return None
    return s if 1 <= s <= n else None


def clean_direction(raw, genre: str | None, n_scenes: int) -> dict:
    """The planner's edit_direction, validated: known treatment, scenes that exist, allowed kinds, short lists."""
    d = raw if isinstance(raw, dict) else {}
    t = str(d.get("treatment") or "").strip().lower()
    out = {"treatment": t if t in TREATMENTS else treatment_hint(genre), "concept": str(d.get("concept") or "")[:300]}
    design = d.get("design") if isinstance(d.get("design"), dict) else {}
    out["design"] = {k: str(design[k])[:60] for k in ("accent", "text", "panel") if design.get(k)}
    curve = []
    for c in d.get("energy_curve") or []:
        if isinstance(c, dict) and str(c.get("beat") or "").lower() in BEATS and _scene_no(c.get("scene"), n_scenes):
            curve.append({"scene": _scene_no(c["scene"], n_scenes), "beat": str(c["beat"]).lower(),
                          "what": str(c.get("what") or "")[:200]})
    out["energy_curve"] = curve[:8]
    out["graphic_moments"] = [
        {"scene": _scene_no(g.get("scene"), n_scenes), "kind": g["kind"], "text": str(g.get("text") or "")[:120],
         "why": str(g.get("why") or "")[:200]}
        for g in d.get("graphic_moments") or []
        if isinstance(g, dict) and g.get("kind") in DIRECTION_KINDS and _scene_no(g.get("scene"), n_scenes)][:6]
    out["effect_moments"] = [
        {"scene": _scene_no(e.get("scene"), n_scenes), "idea": str(e.get("idea") or "")[:200]}
        for e in d.get("effect_moments") or []
        if isinstance(e, dict) and e.get("idea") and _scene_no(e.get("scene"), n_scenes)][:5]
    return out


def planned_moments(direction: dict, spans: list[tuple[float, float]]) -> str:
    """The planned direction as a timed block for the effects editor (scene numbers -> seconds)."""
    if not direction:
        return ""

    def at(n):
        a, b = spans[n - 1] if 0 < n <= len(spans) else (0.0, 0.0)
        return f"scene {n} ({a:.1f}-{b:.1f} s)"

    lines = [f"PLANNED DIRECTION (decided with the script; follow it unless the video shows otherwise): treatment "
             f"{direction.get('treatment')}"]
    if direction.get("concept"):
        lines.append(f"Concept: {direction['concept']}")
    if (direction.get("design") or {}).get("accent"):
        lines.append(f"Accent colour for every coloured effect: {direction['design']['accent']}")
    lines += [f"- {at(c['scene'])}: {c['beat']} - {c['what']}" for c in direction.get("energy_curve") or []]
    lines += [f"- effect idea at {at(e['scene'])}: {e['idea']}" for e in direction.get("effect_moments") or []]
    return "\n".join(lines)
