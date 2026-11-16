"""Story methods: so every video is not built the same way.

Each genre family has several genuinely different ways to tell the film (a routine, a POV, a before/after, a myth
vs fact...), each with its own weighted arc. A brief that clearly asks for one gets it; otherwise a method that was
not used in the last few films is picked, so two videos in a row never share the same structure.
"""

import json
import random
import re
from pathlib import Path

from router_core import config

# genre -> family of story methods (genres with one natural structure, like exploded views, keep their playbook)
FAMILY = {"product_film": "faceless", "ugc_ad": "product", "advertisement": "product", "ecommerce": "product", "unboxing": "product",
          "food": "product", "explainer": "explainer", "cinematic": "story", "action": "story",
          "documentary": "story", "animation": "story", "travel": "story"}

APPROACHES: dict[str, list[dict]] = {
    "product": [
        {"id": "routine", "label": "Real routine, step by step",
         "when": r"\b(how to (use|apply)|appl(y|ies|ying|ication)|demonstrat\w*|tutorial|routine|step[- ]by[- ]step|"
                 r"install\w*|set ?up)\b",
         "how": "the real way a person uses this product, in the real order, in one place, so a viewer could copy it",
         "arc": [("Hook: the finished result or the most satisfying moment of the routine, first 2 s", 12),
                 ("Prepare: what a real person does before using it (for this product)", 18),
                 ("Open / ready the product on screen", 14),
                 ("Texture or inside: a macro close-up of what it really looks or feels like", 14),
                 ("Use it the real way, the key step slowed down", 24),
                 ("Result and a quick recommendation, product closed in hand", 18)]},
        {"id": "problem_solution", "label": "Problem -> discovery -> proof",
         "how": "a real everyday problem, the product found and tried on camera, an honest reaction",
         "arc": [("Hook: something surprising in the first 2 s", 12), ("The problem, shown not told", 18),
                 ("Discovery: the product enters and is tried", 22), ("Proof: the moment it works, close", 28),
                 ("Recommendation with the product in hand", 20)]},
        {"id": "pov", "label": "POV / first person",
         "how": "shot as the viewer's own eyes (POV hands in frame, phone-camera height); text-style hooks like "
                "'POV: you finally found...'; the viewer IS the user",
         "arc": [("POV hook: the viewer's hands reach for the product", 15), ("First touch and first use, all POV", 30),
                 ("The small moment of delight, still POV", 25), ("Turn the camera: the creator reacts to the viewer", 15),
                 ("Call to action", 15)]},
        {"id": "before_after", "label": "Before / after transformation",
         "when": r"\bbefore\s*(and|&|/|vs\.?)\s*after\b|\btransformation\b",
         "how": "one strong contrast: the same person, place and framing before and after; a match cut or a swipe "
                "moment carries the change",
         "arc": [("Hook: flash of the 'after' first, then cut back", 12), ("Before: the honest starting point", 22),
                 ("The change: the product in use, compressed time", 26), ("After: same framing as before, held", 25),
                 ("Recommendation", 15)]},
        {"id": "myth_fact", "label": "Myth vs fact / 3 things",
         "how": "the creator busts a common myth or lists 3 quick things they wish they'd known; each point is one "
                "visual proof; fast and punchy",
         "arc": [("Hook: 'Stop doing this' or 'three things...'", 12), ("Point 1 with a visual proof", 22),
                 ("Point 2 with a visual proof", 22), ("Point 3 - the product as the answer", 26),
                 ("Recommendation", 18)]},
        {"id": "day_in_life", "label": "A day with it",
         "how": "the product woven into a real day (morning, on the go, evening), each moment a different place and "
                "light; the product is the constant",
         "arc": [("Hook: the end-of-day result or a time stamp", 12), ("Morning moment with the product", 22),
                 ("Midday moment, a different place", 22), ("Evening moment, the payoff", 26),
                 ("Recommendation", 18)]},
        {"id": "sensory", "label": "Sensory close-ups (ASMR)",
         "how": "few words, big sound and texture: macro shots of the product, its sound, its texture, slow and "
                "tactile; the creator appears only for the reaction and the line at the end",
         "arc": [("Hook: an extreme macro of the product's most tactile detail", 15),
                 ("Texture and sound, 2-3 macro moments", 35), ("Use on skin / in hand, slow", 25),
                 ("Reaction and one line to camera", 25)]},
    ],
    # Product films with no person, from the reference reels the user picked.
    "faceless": [
        {"id": "ingredient_world", "label": "Ingredient world",
         "when": r"\bingredient|botanical|natural|extract\w*",
         "how": "the product travels through worlds built from its own ingredients and benefits (water for hydration, "
                "leaves or moss for botanicals, fruit for its flavour, light for glow); every scene is one ingredient "
                "or benefit made physical around the product",
         "arc": [("Reveal: the product in the first ingredient world", 20), ("A second ingredient world", 20),
                 ("Texture macro: the formula itself", 20), ("Peak: the world blooms / splashes around it", 24),
                 ("Packshot for the brand end card", 16)]},
        {"id": "texture_ritual", "label": "Texture ritual (hands only)",
         "how": "a calm, tactile sequence of equal-length shots: a top-down flat lay of the product on its texture "
                "(foam, gel, cream), a hand holding it in its element (underwater light rays, mist), an extreme "
                "macro squeeze / drip of the formula, the product on a mirror pedestal with the texture cascading",
         "arc": [("Flat lay on its own texture, top-down", 25), ("Hand holds it in its element", 25),
                 ("Macro: the formula squeezed or dripped", 25), ("Pedestal packshot with texture cascade", 25)]},
        {"id": "cgi_hero", "label": "3D-style hero film",
         "when": r"\bcgi\b|\b3d\b|render\w*|levitat\w*|float\w*|premium|luxury",
         "how": "a premium CGI-style product film: a pedestal in window-shadow light, the bottle levitates and turns "
                "slowly in macro with droplets, a splash / particle burst wraps it, a surreal hero set (a moss island "
                "on still water, a stone in mist), then black with the brand name",
         "arc": [("Pedestal in window-shadow light", 18), ("Levitation and slow rotation, macro, droplets", 24),
                 ("Splash or particle burst around it", 20), ("Surreal hero set: the product's world", 24),
                 ("Black end card with the brand name", 14)]},
        {"id": "benefit_tags", "label": "Benefit tags",
         "when": r"\bbenefits?|features?|claims?|ingredients? list|why it works",
         "how": "the product is held steady or stands in a clean set while its benefits pop in as tags around it, "
                "then one shot per key ingredient or benefit (texture on the back of a hand, the effect on skin or "
                "material), each carrying one short label added in the edit",
         "arc": [("Product steady, benefits will pop around it", 25), ("Texture swatch on the back of a hand", 20),
                 ("Ingredient / benefit 1 made visible", 18), ("Ingredient / benefit 2 made visible", 18),
                 ("Packshot", 19)]},
        {"id": "hands_ritual", "label": "Hands-only routine",
         "when": r"\bhow to (use|apply)|appl(y|ies|ying|ication)|routine|tutorial|step[- ]by[- ]step",
         "how": "the real routine shot from above or POV with hands only: open, dispense, swatch, use - cut on each "
                "hand movement, satisfying sounds",
         "arc": [("Hands reach in and open it", 20), ("Dispense: the formula meets the hand", 25),
                 ("Swatch / spread in macro", 25), ("The result on hand or surface", 15), ("Packshot", 15)]},
        {"id": "unbox_flatlay", "label": "Flat-lay unboxing",
         "when": r"\bunbox\w*|packag\w*|gift\w*",
         "how": "top-down flat lay: hands open the package, lift the product out, arrange it with props from its "
                "world, a satisfying final layout",
         "arc": [("Closed package on a styled surface", 18), ("Hands open it, first reveal", 25),
                 ("The product lifted out, macro of the details", 25), ("Final styled flat lay", 32)]},
    ],
    "explainer": [
        {"id": "question_first", "label": "Question first",
         "how": "open on a question the viewer has, answer it step by step, end on the 'aha'",
         "arc": [("The question, visualised", 15), ("Step 1", 22), ("Step 2", 22), ("Step 3 / the key part", 23),
                 ("The aha and a one-line summary", 18)]},
        {"id": "zoom_in", "label": "Big picture -> zoom in",
         "how": "start wide on the whole thing, then zoom into the part that matters, layer by layer",
         "arc": [("The whole thing, wide", 15), ("Zoom to the first layer", 25), ("Zoom to the key part", 30),
                 ("Pull back: how it fits together", 30)]},
        {"id": "analogy", "label": "Everyday analogy",
         "how": "explain it through one everyday analogy the viewer already understands, cutting between the analogy "
                "and the real thing",
         "arc": [("The analogy, familiar", 18), ("The real thing, side by side", 22), ("Where the analogy maps", 30),
                 ("Where it breaks, and the real answer", 30)]},
        {"id": "myth_fact", "label": "Myth vs fact",
         "how": "a common wrong belief, why it is wrong, and what is really going on",
         "arc": [("The myth, stated and shown", 18), ("Why people believe it", 20), ("The real mechanism", 37),
                 ("The takeaway", 25)]},
    ],
    "story": [
        {"id": "linear", "label": "Straight through",
         "how": "a classic linear story: setup, rising problem, peak, release",
         "arc": [("Setup", 20), ("Rising problem", 25), ("Peak", 35), ("Release", 20)]},
        {"id": "in_medias_res", "label": "Start at the peak",
         "how": "open at the most intense moment, then jump back to how it got there, and return to finish it",
         "arc": [("The peak, cut off at its height", 15), ("Earlier: how it began", 25), ("Build back up", 25),
                 ("The peak again, now completed", 35)]},
        {"id": "real_time", "label": "One place, real time",
         "how": "one location, continuous time, few cuts; tension from what happens inside the frame",
         "arc": [("Arrival in the place", 20), ("Something changes", 30), ("It comes to a head", 30),
                 ("The quiet after", 20)]},
        {"id": "circular", "label": "Full circle",
         "how": "the last shot mirrors the first shot (same framing), but something has changed",
         "arc": [("Opening image", 18), ("The journey away", 27), ("The turn", 30),
                 ("Closing image, mirroring the first", 25)]},
    ],
}

RECENT_FILE = "recent_story_methods.json"
RECENT_KEEP = 3  # never repeat any of the last 3 methods of the same family unless the brief asks for it


def _recent_path() -> Path:
    return config.STORAGE / RECENT_FILE


def recent(family: str) -> list[str]:
    try:
        return list(json.loads(_recent_path().read_text()).get(family) or [])
    except Exception:
        return []


def remember(family: str, method_id: str, keep: int = 10) -> None:
    try:
        path = _recent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {}
        data[family] = ((data.get(family) or []) + [method_id])[-keep:]
        path.write_text(json.dumps(data))
    except Exception:  # variety is a nicety; never fail a film over it
        pass


def pick(genre: str, brief: str, rng: random.Random | None = None) -> dict | None:
    """The story method for this film: the one the brief asks for, else one not used in the last few films."""
    family = FAMILY.get(genre)
    if not family:
        return None
    options = APPROACHES[family]
    for o in options:
        if o.get("when") and re.search(o["when"], brief or "", re.I):
            return {**o, "family": family, "asked": True}
    used = recent(family)[-RECENT_KEEP:]
    fresh = [o for o in options if o["id"] not in used and o["id"] != "routine"] or \
            [o for o in options if o["id"] != "routine"]
    return {**(rng or random).choice(fresh), "family": family, "asked": False}


def playbook(method: dict, total: int) -> str:
    beats = "\n".join(f"  {i + 1}. {b} - about {round(total * w / 100)} s ({w}%)"
                      for i, (b, w) in enumerate(method["arc"]))
    why = "the brief asks for it" if method.get("asked") else "picked so this film is not built like the last ones"
    return f"""

STORY METHOD - {method['label']} ({why}): {method['how']}.
Build THIS film with this method (it replaces the genre's default arc; the brief still wins where it says otherwise).
Pick shots, openings and transitions that suit this method - do not fall back to a generic hook/problem/proof ad.
{beats}"""
