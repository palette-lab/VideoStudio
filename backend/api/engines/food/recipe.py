"""FoodCategoryAnalyzer + RecipeUnderstandingEngine + CookingWorkflowPlanner, in ONE LLM call.

The model first understands the actual dish (cuisine, categories, ingredients, cookware, the real order of work), then
picks the library actions each stage really needs. The result is validated here and reused by every later stage
(scene planning, image prompts, motion prompts, validation) so the recipe is understood once.
"""

import json
import re

from router_core import config, llm

from .library import ACTIONS, CATEGORIES

# Cheap gate before the LLM call: does the brief talk about making or showing food at all?
FOOD_WORDS = re.compile(
    r"\b(recipe|cook\w*|bak(e|ed|es|ing)|chef|kitchen|food|dish|meal|cuisine|fry|fried|grill\w*|roast\w*|"
    r"biryani|curry|masala|paneer|dal|dosa|idli|samosa|roti|naan|pizza|pasta|lasagn\w*|risotto|burger|sandwich|"
    r"fries|noodles?|ramen|sushi|dumplings?|momos?|bao|kimchi|bibimbap|tacos?|burrito|salad|soup|stew|steak|"
    r"chicken|mutton|lamb|beef|pork|fish|prawns?|shrimp|egg(s)?|cake|cupcakes?|cookies?|brownies?|pastry|bread|"
    r"croissant|donuts?|doughnuts?|pie|tart|dessert|ice ?cream|chocolate|pancakes?|waffles?|smoothie|milkshake|"
    r"shake|juice|coffee|tea|latte|cocktail|mocktail|drink|beverage|snack|street food|fast food|breakfast|lunch|"
    r"dinner|brunch|vegan|vegetarian|tofu|rice|omelette|sauce|dough|batter)\b", re.I)

SYSTEM = """You are ChefVision AI's recipe analyst: a professional chef and culinary director. Before any video is
planned, understand the food.

1. Decide what the video is about: "is_cooking_process" is true when the video should show the food being prepared
   / made / assembled / poured (a recipe, a how-to, "making X", a cooking reel). It is false when it only shows a
   finished dish, a restaurant or a drink as a product or mood (e.g. "an ad for our cafe", "a burger commercial with
   people eating") - then return just the identification fields.
2. Identify the dish exactly (e.g. "Hyderabadi chicken dum biryani"), its cuisine, and its categories from: {CATS}.
3. Understand the REAL recipe the way a cook makes it: ingredients with realistic quantities for the stated
   servings (default 2-4), cookware, utensils, and the chronological workflow from raw ingredients to the served
   dish - including the small steps people forget (washing rice, cracking and separating eggs, heating the pan
   before oil, resting dough, draining fried food, cooling before unmoulding, cutting, plating, tasting).
4. For every workflow stage pick ONLY the action ids it really needs from this library: {ACTIONS}. Never force an
   action a recipe does not have (no oven for a salad, no cheese pull without melted cheese, no steam on cold food).
5. Never invent a critical ingredient or step you are unsure of: if the dish itself or a defining choice is
   unknown (e.g. "a cake" with no flavour is fine - pick a classic and say so; but "cook something" is not), list it
   in "missing_critical". Everything else you assume goes in "assumptions".

Return ONLY JSON:
{"is_cooking_process": true,
 "dish": "...", "cuisine": "...", "categories": ["..."], "diet": "vegetarian | non_vegetarian | vegan | unknown",
 "servings": "...",
 "ingredients": [{"name": "basmati rice", "quantity": "2 cups", "initial_state": "dry grains in a bowl"}],
 "cookware": ["heavy-bottomed pot", "..."], "utensils": ["chef's knife", "..."],
 "set": {"surface": "the work surface", "kitchen": "the background and props", "light": "direction, softness, colour
         temperature", "hands": "one consistent pair of hands: skin tone, nails, sleeves, any ring"},
 "workflow": [{"stage": "Rinse and soak the rice", "actions": ["rinse_grain", "soak"], "ingredients": ["basmati rice"],
               "result": "the rice soaked and plump", "time_jump": "30 min soak or ''"}],
 "hero_moments": ["only textures this recipe really has, e.g. layered cross-section when the pot is opened"],
 "sensory": ["real cues: sizzling onions, steam when the lid lifts, ..."],
 "missing_critical": [], "assumptions": ["..."]}"""


def is_candidate(brief: str, genre: str | None) -> bool:
    return genre == "food" or bool(FOOD_WORDS.search(brief or ""))


def clean(data: dict) -> dict:
    """Validate the analyst's JSON: known categories and actions only, non-empty stages, sane lists."""
    out = {k: data.get(k) for k in ("dish", "cuisine", "diet", "servings")}
    out = {k: str(v or "").strip()[:200] for k, v in out.items()}
    out["is_cooking_process"] = bool(data.get("is_cooking_process"))
    out["categories"] = [c for c in (data.get("categories") or []) if c in CATEGORIES][:6] or ["Other"]
    out["ingredients"] = [{"name": str(i.get("name") or "")[:80], "quantity": str(i.get("quantity") or "")[:40],
                           "initial_state": str(i.get("initial_state") or "")[:120]}
                          for i in (data.get("ingredients") or []) if isinstance(i, dict) and i.get("name")][:40]
    for k in ("cookware", "utensils", "hero_moments", "sensory", "missing_critical", "assumptions"):
        out[k] = [str(x)[:200] for x in (data.get(k) or []) if str(x).strip()][:20]
    s = data.get("set") if isinstance(data.get("set"), dict) else {}
    out["set"] = {k: str(s.get(k) or "")[:300] for k in ("surface", "kitchen", "light", "hands")}
    stages, unknown = [], []
    for st in data.get("workflow") or []:
        if not isinstance(st, dict) or not st.get("stage"):
            continue
        acts = [a for a in (st.get("actions") or []) if a in ACTIONS]
        unknown += [a for a in (st.get("actions") or []) if a not in ACTIONS]
        stages.append({"stage": str(st["stage"])[:120], "actions": acts,
                       "ingredients": [str(x)[:80] for x in (st.get("ingredients") or [])][:12],
                       "result": str(st.get("result") or "")[:200], "time_jump": str(st.get("time_jump") or "")[:80]})
    out["workflow"] = stages[:24]
    out["unknown_actions"] = sorted(set(unknown))[:20]
    return out


def actions_used(recipe: dict) -> list[str]:
    return list(dict.fromkeys(a for st in recipe.get("workflow") or [] for a in st["actions"]))


async def understand(brief: str, refs: list[dict] | None = None, chat=None) -> dict:
    """The recipe, understood once (one LLM call)."""
    system = (SYSTEM.replace("{CATS}", ", ".join(CATEGORIES))
              .replace("{ACTIONS}", ", ".join(ACTIONS)))
    files = "; ".join(f"{r.get('role')}: {r.get('summary') or r.get('filename')}" for r in refs or [])
    user = f"BRIEF:\n{brief}" + (f"\n\nUPLOADED FILES: {files}" if files else "")
    data = await (chat or llm.chat_json)(config.REASONING_MODEL, [{"role": "system", "content": system},
                                                                  {"role": "user", "content": user}],
                                         max_tokens=12000, stage="engine.food.recipe")
    return clean(data if isinstance(data, dict) else {})


def summary(recipe: dict) -> str:
    """The recipe as compact JSON for the planner (the understanding is reused, not redone)."""
    keep = {k: recipe[k] for k in ("dish", "cuisine", "categories", "diet", "servings", "ingredients", "cookware",
                                   "utensils", "set", "workflow", "hero_moments", "sensory", "assumptions")}
    return json.dumps(keep, ensure_ascii=False)
