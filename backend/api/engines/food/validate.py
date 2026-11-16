"""PromptQualityValidator + VisualContinuityManager: deterministic checks on a planned cooking film (no LLM call).

Problems found here are handed back to the planner for one rewrite; whatever remains is recorded in the plan.
"""

import json
import re

from . import library

REQUIRED = ("recipe_stage", "action", "start_state", "end_state", "ingredients", "hands", "camera", "state", "motion",
            "image_prompt", "props")
SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*[-\u2013\u2014]\s*(\d+(?:\.\d+)?)\s*s\b")
MAX_BEATS, MIN_BEAT_S = 3, 1.5
GENERIC = re.compile(r"\b(zoom(s|ing)? (in|out)|show(s|ing)? (the )?(ingredients|finished dish|final dish)|"
                     r"(camera )?(rotates|orbits|circles|pans) (slowly )?around|beauty shot|glides? over)\b", re.I)
# A scene action must contain at least one physical verb (hands / tools doing something to food).
VERBS = re.compile(r"\b(wash|rins|dr(y|ies|ied)|pat|peel|chop|dic|slic|cut|grat|zest|crush|minc|measur|scoop|level|"
                   r"open|crack|tap|separat|pour|tilt|sprinkl|sift|dollop|plac|scatter|spread|ladl|spiral|brush|layer|"
                   r"marinat|coat|season|whisk|beat|stir|fold|blend|knead|roll|shap|press|pinch|seal|heat|fry|fri|"
                   r"saut|toss|boil|simmer|steam|bak|roast|grill|sear|toast|melt|caramel|reduc|flip|turn|blanch|"
                   r"chill|lift|drain|rest|cool|unmould|unmold|loosen|slid|transfer|plat|garnish|drizzl|squeez|"
                   r"assembl|stack|wrap|bite|break|dip|dunk|lower|remov|set(s)? down|carr|insert|push|stretch|"
                   r"squash|mash|grind|pound|skewer|thread|fill|pip|frost|ic(e|ing)|dust|sieve|strain|serve|"
                   r"blend|shak|muddl|top|tear|crumbl|smooth|scrap|mix|knock|tip|cover|uncover|lid|draw|pull|hold|grip|"
                   r"guid|rock|pick|swirl|ladl|grab|nudg|lay|laid|press)\w*", re.I)
# form -> (words that mean it was reached, words that would undo it)
FORMS = {"cut": (("sliced", "chopped", "diced", "minced", "grated", "julienned", "cubed", "halved", "quartered",
                  "cut into", "crushed", "half", "halves", "bitten", "pieces", "portion"), ("whole", "uncut", "unpeeled", "unsliced")),
         "cracked": (("cracked", "beaten", "whisked", "separated"), ("in its shell", "uncracked", "whole egg", "in the shell")),
         "cooked": (("cooked", "fried", "baked", "roasted", "grilled", "seared", "boiled", "steamed", "toasted",
                     "browned", "caramelized", "parboiled"), ("raw", "uncooked"))}
TIMED_WORDS = re.compile(r"\b(bake[sd]?|baking(?! (tray|sheet|dish|paper|pan|tin|soda|powder))|roast(s|ed|ing)?|"
                         r"(into|in) the oven|proof(s|ed|ing)?|"
                         r"rises? (to|until)|set(s)? in the fridge|chill(s|ed|ing)? (in|for|until)|"
                         r"marinat(es|ed|ing) for|dum)\b", re.I)
DECLARED_CUT = re.compile(r"\b(meanwhile|cut (on|to|from|away)|hard cut|parallel|in the meantime|"
                          r"while .* (rests|cooks|soaks|simmers)|time jump|later|new station|switch(es)? to|"
                          r"next scene opens)\b", re.I)
STOP = {"the", "a", "an", "of", "on", "in", "into", "with", "and", "to", "at", "is", "are", "its", "it", "from", "by",
        "over", "under", "board", "bowl", "pan", "hand", "hands", "left", "right", "frame", "now", "still", "same"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}


def check(scenes: list[dict], recipe: dict, n_max: int) -> list[str]:
    """Every problem a viewer (or the clip model) would trip over, as short fixable sentences."""
    issues: list[str] = []
    want_min = min(8, n_max)
    if len(scenes) < want_min:
        issues.append(f"Only {len(scenes)} scenes: use at least {want_min} so the real preparation steps are shown.")
    names = {i["name"].lower() for i in recipe.get("ingredients") or []}
    reached: dict[str, dict[str, tuple[int, str]]] = {}
    for n, s in enumerate(scenes, start=1):
        miss = [f for f in REQUIRED if not s.get(f)]
        if miss:
            issues.append(f"Scene {n}: missing {', '.join(miss)}.")
        action = str(s.get("action") or "")
        if GENERIC.search(action) and not VERBS.search(GENERIC.sub("", action)):
            issues.append(f"Scene {n}: the action is only a camera move or a display ({action[:80]}); give it a real "
                          "cooking action that advances the recipe.")
        elif action and not VERBS.search(action):
            issues.append(f"Scene {n}: no physical food action in \"{action[:80]}\" - say what the hands and tools do.")
        spans = [(float(a), float(b)) for a, b in SPAN.findall(action)]
        short = [b - a for a, b in spans if b - a < MIN_BEAT_S - 0.05]
        if len(spans) > MAX_BEATS or len(short) > 1:
            issues.append(f"Scene {n}: {len(spans)} timed beats ({len(short)} under {MIN_BEAT_S:g} s) is more than one "
                          "AI clip can perform - keep 2-3 beats of at least 1.5 s and move the rest to its own scene.")
        m = s.get("motion")
        if isinstance(m, dict) and len([a for a in m.get("action_sequence") or [] if str(a).strip()]) < 2:
            issues.append(f"Scene {n}: motion.action_sequence needs the movement in order (start, contact, result).")
        # VisualContinuityManager: a form an ingredient reached never reverts (the PDF's hard rules: a cut ingredient
        # stays cut, a cracked egg stays cracked, cooked food never turns raw). Combining a cooked part into a batter,
        # layer or burger is NOT a reversion, so states are compared by form, not by a single ladder.
        state = s.get("state") if isinstance(s.get("state"), dict) else {}
        for ing, st in state.items():
            key, text = str(ing).lower().strip(), f" {str(st).lower()} "
            had = reached.setdefault(key, {})
            for form, (made, undone) in FORMS.items():
                if form in had and any(f" {w}" in text for w in undone) and not any(f" {w}" in text for w in made):
                    issues.append(f"Scene {n}: {ing} was {form} in scene {had[form][0]} (\"{had[form][1]}\") but is "
                                  f"\"{st}\" here - a {form} ingredient never goes back.")
                if form not in had and any(f" {w}" in text for w in made):
                    had[form] = (n, str(st))
        # cooked / risen / set results need an explicit time transition
        cooked_words = FORMS["cooked"][0]
        start_cooked = any(f" {w}" in f" {str(s.get('start_state')).lower()}" for w in cooked_words)
        end_cooked = any(f" {w}" in f" {str(s.get('end_state')).lower()}" for w in cooked_words + ("golden", "set"))
        if TIMED_WORDS.search(action) and end_cooked and not start_cooked and not s.get("time_jump"):
            issues.append(f"Scene {n}: the food goes from \"{str(s.get('start_state'))[:50]}\" to cooked inside one "
                          "real-time clip - split it and add a \"time_jump\" (e.g. after 25 min in the oven).")
    # scene-to-scene: the next start continues the previous end (or there is a declared time jump)
    for n in range(1, len(scenes)):
        a, b = scenes[n - 1], scenes[n]
        if b.get("time_jump") or DECLARED_CUT.search(str(b.get("link") or "")):
            continue  # a declared time jump or a labelled cut to a parallel prep
        if not (_words(a.get("end_state")) & _words(b.get("start_state"))):
            issues.append(f"Scene {n + 1}: its start_state does not continue scene {n}'s end_state - pick up exactly "
                          "where it ended (same food, vessel and hands) or declare a time_jump.")
    # finishing steps the recipe has must not be forgotten
    planned = " ".join(str(s.get("action") or "") + " " + str(s.get("recipe_stage") or "") for s in scenes).lower()
    wanted = {a for st in recipe.get("workflow") or [] for a in st.get("actions") or []} & library.FINISHING
    words = {"cut_portion": ("cut", "slice", "halve", "break", "split", "cross-section", "presses through",
                             "pierce"), "lift_transfer": ("lift", "transfer", "slide"),
             "plate": ("plat", "serv", "onto the dish", "into the bowl"), "scoop": ("scoop", "ladle"),
             "first_bite": ("bite", "fork", "spoon", "taste"), "pour_drink": "pour",
             "garnish": ("garnish", "sprinkl", "scatter", "herb", "coriander", "cilantro", "parsley", "sesame",
                         "spring onion", "dust", "zest", "chili flakes", "wasabi", "pickled ginger", "lime wedge",
                         "lemon wedge", "mint", "basil", "berries", "icing sugar"), "drizzle": ("drizzl", "squeez", "swirl")}
    for a in sorted(wanted):
        w = words[a]
        if not any(x in planned for x in ((w,) if isinstance(w, str) else w)):
            issues.append(f"The recipe has \"{a.replace('_', ' ')}\" but no scene shows it - add it.")
    # every recipe stage is covered (merged stages are fine when their names appear)
    stages = [st["stage"] for st in recipe.get("workflow") or []]
    covered = " ".join(str(s.get("recipe_stage") or "") for s in scenes).lower()
    skipped = [st for st in stages if not (_words(st) & _words(covered))]
    if len(stages) >= 3 and len(skipped) > max(1, len(stages) // 3):
        issues.append("These recipe stages have no scene: " + "; ".join(skipped[:6]) + ".")
    return issues


def json_valid(objs: list[dict]) -> bool:
    try:
        for o in objs:
            json.loads(json.dumps(o))
        return True
    except (TypeError, ValueError):
        return False
