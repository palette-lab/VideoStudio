"""SceneChoreographyEngine + FoodPhysicsPromptEngine: the planner rules for a cooking film.

For a cooking process these replace the generic "STORY FIRST" scene rules: in a recipe video the recipe IS the story.
They carry the ChefVision scene design, micro-detail, physics and continuity rules, the recipe already understood
(engines/food/recipe.py) and the choreography of ONLY the actions that recipe uses (engines/food/library.py).
"""

from . import library
from .recipe import actions_used, summary

RULES = """

COOKING FILM (ChefVision): THE RECIPE IS THE STORY. This replaces the generic story rules: no invented characters,
conflicts or locations. Show how this dish is really made, step by step, so the viewer understands and feels every
important step. A beautiful final plate is not enough.

THE RECIPE (already understood - follow its ingredients, quantities, cookware and workflow order; adapt the scene
split to the length, never change the recipe):
{RECIPE}

THE REAL HANDWORK of this recipe (use these actions, with this choreography: hand, tool, ingredient, contact point,
movement, resistance and visible result):
{CHOREOGRAPHY}

SCENES:
- {COUNT}. Every scene shows ONE primary physical action (at most one or two tightly connected secondary actions)
  that ADVANCES the recipe and ends with a visible result. Follow the workflow order; split a complex action into two
  scenes rather than hiding it; merge only trivial steps. Include cracking, cutting, serving, plating and tasting
  when the recipe has them - they are often forgotten.
- Recommended progression (adapt it to the recipe: a salad has no oven, a soup needs simmering and ladling, a drink
  needs pouring): establish the work surface and the first prep action -> ingredient prep -> combine -> shape / fill
  / top -> cook with a believable heat transformation -> reveal and rest -> portion and plate -> hero finish
  (sauce, cross-section, first bite).
- NEVER a scene whose action is only "zoom in on the food", "show the ingredients", "show the finished dish",
  "camera rotates around the plate" or a beauty glide. The camera serves the hands and the food.
- Durations follow the action: a sprinkle 3 s; cracking, whisking, slicing, plating or a cheese pull 5-8 s.

EVERY scene object has these fields (in addition to title, duration, "weight" 1-5, voice, script, sound, change -
all required, "weight" included):
- "recipe_stage": the workflow stage it belongs to (its exact name from the recipe).
- "beat": one sentence - what this moment contributes to the recipe.
- "ingredients": the exact ingredients and quantities visible.  "hands": what each hand does and holds (the same
  pair of hands as the recipe's set).  "utensils": the tools and cookware in use.
- ONE CLIP, ONE SETUP: a scene stays in one place with one camera setup and the same vessels from its first to its
  last frame; no new plate, bowl or pot arrives during the clip unless the action carries it in by hand.
- "action": the primary action as 2-3 timed beats (never more, each at least 1.5 s; a clip cannot perform more) -
  its BEGINNING, its PROGRESSION and its COMPLETION (e.g. "0-1.5 s:
  the left hand holds the egg over the glass bowl and taps it once on the rim; 1.5-3 s: both thumbs open the shell
  and the white and yolk drop into the bowl; 3-4 s: the shell halves stay in the fingers above the bowl").
- "start_state" / "end_state": the food state before and after - exact positions and condition of every visible
  ingredient, vessel and hand. Scene N+1's start_state is EXACTLY scene N's end_state, unless "time_jump" says time
  passed or "link" labels a cut to a parallel prep ("meanwhile, on the board...").
- "link": how this scene connects to the previous one (the action carried through, a time jump, or a labelled cut).
- "time_jump": "" or the explicit time transition before this scene ("after 25 min in the oven"). Required whenever
  food cooks, bakes, rises, sets, rests, marinates or cools between the previous scene and this one.
- "texture": the food's visible surface and interior and how it transforms during the action.
- "camera": overhead, 45-degree close-up, macro or side angle - whichever shows the action best; static or a subtle
  controlled push-in; the camera never hides the action.  "lighting": the recipe's light, consistent.
- "state": {"ingredient name": "its state at the END of this scene"} for every ingredient used so far (e.g.
  {"onions": "sliced", "rice": "soaked"}). States only move forward: whole -> washed -> peeled -> cut -> mixed ->
  cooking -> cooked -> rested -> portioned -> plated -> eaten.
- "continuity": what must match the previous and next scene (vessel contents and fill level, cut size, colour,
  utensil, hand, plate).
- "motion": the image-to-video choreography of exactly this action:
  {"starting_state": "positions at the first frame", "action_sequence": ["hand and utensil movement in order",
  "contact, pressure, resistance, release", "ingredient movement, gravity, liquid or particle behaviour",
  "the visible transformation and where every object finishes"], "physics": {"gravity": "...",
  "fluid_or_material_behavior": "viscosity, stretch, fracture, resistance for THIS food", "contact": "..."},
  "food_response": "the gradual change in texture, colour, shape or position", "ending_state": "where every
  visible object and hand finishes"}
- "image_prompt": the FIRST frame, exactly the start_state frozen at the instant the action begins: one coherent
  photographic moment (not a collage), with the hands and utensils placed for the action. It shows the food BEFORE
  this scene's action (e.g. an empty handi with only the chicken at its base before the layering - never the
  finished layers).
- "props": every object visible in the first frame with its count and look - vessels, plates, bowls, utensils,
  lids, cloths, hands (e.g. ["1 wide steel handi with the marinated chicken at its base", "1 colander of parboiled
  rice", "1 brass bowl of birista", "2 hands"]). The image is drawn with EXACTLY these: no serving plate before
  the plating scene, no second plate, no extra bowls. The same vessel keeps the same shape, metal and size in
  every scene.

REALISM AND PHYSICS (only the cues that matter to each action; never invent effects):
- Hand anatomy and grip: which hand holds what, where the fingers rest, how the utensil is controlled.
- Contact and resistance: blade meets board, whisk touches bowl, dough resists, spatula slides beneath food.
- Material response: shell cracks, crust fractures, dough stretches, liquid flows with its real viscosity, powder
  disperses, sauce drips, food compresses slightly when it lands.
- Gravity and settling: things fall, pool, roll and stop; nothing floats.
- Heat: steam only on hot food, bubbling and sizzling only in hot fat or liquid, browning gradual; a cheese pull
  only with melted cheese; a crisp crack only on a brittle surface.
- Sound ("sound"): the real cues - shell crack, knife on board, whisking, sizzling, bubbling, crust crunch, plate
  contact. Voice: "none" for every scene (a music-led cooking reel) unless the brief asks for a voiceover.

CONTINUITY (hard rules):
- Ingredients never multiply, vanish, change species or go back to an earlier state; a cut ingredient stays cut, a
  cracked egg stays cracked, cooked food never turns raw. Track bowl and pan contents, fill levels and the order
  ingredients went in.
- The same hands (count, skin, nails, sleeves), utensils, cookware, plate, work surface, kitchen and light in every
  scene.
- Vessels and fit: the right vessel for the technique (dum and slow-cooked dishes in a deep heavy pot with the food
  well below the rim; frying in a pan; boiling in a deep pot), the same vessel in every scene. A lid has the same
  diameter as its vessel and rests on the RIM, never on the food; a seal (dough, foil) sits on the seam between lid
  and rim; food never overflows a vessel; utensils are true to scale with the hands.
- A transfer shows its path from source to destination; a portion never jumps from pan to plate. A cut really
  separates the food and the knife leaves the cut.
- Cooking, chilling, resting or cooling happens across an explicit "time_jump", never inside one real-time clip."""


# The story method for a cooking film (replaces the food genre's ad arc): the ChefVision scene progression.
COOKING_METHOD = {"id": "cooking_process", "label": "Cooking process (ChefVision)", "asked": True,
                  "how": "the dish made for real, from raw ingredients to the first bite, one physical action per scene",
                  "arc": [("Establish the work surface and the first preparation action", 8),
                          ("Ingredient prep: wash, peel, cut, crack, measure, open", 20),
                          ("Combine: add in order; whisk, stir, fold, knead or layer", 16),
                          ("Shape, fill or top before cooking", 10),
                          ("Cook with a believable heat transformation", 16),
                          ("Reveal and rest", 8), ("Portion and plate", 12),
                          ("Hero finish: sauce, cross-section or first bite", 10)]}


def count_rule(n_max: int) -> str:
    lo = min(8, n_max)
    hi = min(12, n_max)
    if hi <= lo:
        return f"Use {n_max} scene{'s' if n_max > 1 else ''} (the length allows no more): the most important actions"
    return f"Use {lo}-{hi} scenes by default (more, up to {n_max}, only when the recipe truly needs more steps)"


def planner_rules(recipe: dict, n_max: int) -> str:
    return (RULES.replace("{RECIPE}", summary(recipe))
            .replace("{CHOREOGRAPHY}", library.choreography(actions_used(recipe)) or "(choose from the recipe)")
            .replace("{COUNT}", count_rule(n_max)))
