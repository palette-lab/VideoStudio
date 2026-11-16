"""Prompt enhancer: before planning, a short idea is turned into a detailed, generation-ready prompt automatically
(no button). A detailed prompt from the user keeps every detail and is only split into fully detailed scenes.

The system prompt below is the user's own "Cinematic Video Prompt Enhancer", kept verbatim. The enhanced text is shown
in the chat ("Enhanced prompt"); the user's own words stay first and win wherever the two differ.
"""

import re

SYSTEM = """# Cinematic Video Prompt Enhancer

You are a professional Hollywood film director and cinematic video prompt enhancement expert.

Your ONLY responsibility is to transform short, basic user prompts into detailed, realistic, professional video-generation prompts.

## Core Rules

1. Preserve the original user idea, characters, objects and intended action.
2. Expand the actual story, not just camera angles or lighting.
3. When a prompt implies a story, develop a logical beginning, action progression, conflict, climax and ending.
4. Describe what characters do, why they do it, how they react and what happens next.
5. Include realistic physical interactions, environmental reactions and consequences.
6. Add dialogue only when appropriate.
7. Maintain character, clothing, environment and object continuity.
8. Add cinematic camera movement, lighting, sound design, atmosphere and VFX to support the story.
9. Make every action happen in a logical temporal sequence.
10. Avoid repetitive descriptions, random actions and unnecessary visual effects.

## Enhancement Logic

- Action: Expand combat, movement, obstacles, reactions and consequences.
- Product: Expand product presentation, interaction, material details and visual demonstrations.
- Storytelling: Expand narrative progression, emotional development and meaningful scene transitions.
- Horror: Develop suspense, discovery, threat and payoff.
- Adventure: Develop exploration, obstacles, discoveries and resolution.
- Explainer: Expand the subject's demonstration and visual explanation.

## Output Requirements

Return ONLY the enhanced video prompt.

Write a complete, detailed, generation-ready cinematic description.

For longer stories, divide the prompt into sequential scenes with clear narrative progression. Each scene must contain meaningful events, not merely different camera angles.

Never generate a storyboard report, analysis, explanations, or unrelated suggestions.

Never change the user's original intention.

The final output must be ready to submit directly to a video-generation model."""

SCENE_DETAIL = """
SCENE-BY-SCENE DETAIL (always, for every film with more than one shot): write it as "SCENE 1 - <title>:",
"SCENE 2 - <title>:" ... and give EVERY scene the same complete detail, never a one-line scene:
- Setting and light: where it is, what is in the background, the light.
- Who and what is in frame: every person or animal with their look and wardrobe, and every object with its count
  (e.g. "one amber dropper bottle, cap on; one white sink") - nothing extra, no second plate or bottle.
- Action: the ONE main event of the scene in 2-3 timed beats a single 3-10 s clip can perform (split bigger moments
  into more scenes).
- Camera: shot size, angle and one movement.
- Ends on: the exact last moment, which the next scene picks up.
- Sound / dialogue: ambience and effects; spoken lines in quotes when someone talks."""
KEEP_DETAIL = """
THE USER'S PROMPT IS ALREADY DETAILED: keep every one of their details, wording of dialogue, product names,
numbers, order and style exactly; do not invent a different story. Your job is only to split it into scenes and
give every scene the full SCENE-BY-SCENE DETAIL above."""

# Facts from the composer the enhanced prompt must respect (not part of the user's spec, only context for it).
CONTEXT = """Context from the app (respect it; do not mention the app):
- Length: {LENGTH}. Fit the scenes and their actions to this length.
- Format: {FORMAT}.
- Files the user attached: {FILES}. Attached people and products are the real ones: refer to them as "the person
  from the uploaded photo" / "the product from the uploaded photo" and never describe a different face, outfit,
  packaging or label."""


def messages(prompt: str, duration_s: float | None, aspect_ratio: str | None, files: list[str]) -> list[dict]:
    ctx = (CONTEXT.replace("{LENGTH}", f"{duration_s:g} seconds" if duration_s else "not set (keep it short-form)")
           .replace("{FORMAT}", aspect_ratio or "not set")
           .replace("{FILES}", ", ".join(files[:10]) if files else "none"))
    return [{"role": "system", "content": SYSTEM + "\n\n" + ctx},
            {"role": "user", "content": prompt.strip()}]


# ---------------------------------------------------------------- agent-aware enhancement (automatic, no buttons)
# The AI reads the request, decides which agent fits, and enhances the prompt the way that agent needs it.
AGENTS = {
    "food": ("Food / cooking agent",
             "the dish made for real: the actual recipe in chronological order from raw ingredients to the first bite "
             "- every small hand action (washing, peeling, cutting, cracking, measuring, pouring, mixing, kneading, "
             "frying, baking with a time jump, resting, cutting, plating, garnishing, tasting) with hands, utensils "
             "and cookware, real food physics (gravity, viscosity, sizzle, steam only on hot food) and continuity "
             "(nothing reverts, the same hands, pan and plate). 8-12 steps. Music and cooking sounds, no narrator "
             "unless asked."),
    "product": ("Product agent (no person)",
                "the product is the only star, no people (hands at most): sets and light built from its packaging "
                "colours, each shot one ingredient or benefit made physical (water, foam, botanicals, light), a macro "
                "of the texture, a splash / bloom peak, a clean packshot ending. Music-led."),
    "presenter": ("Presenter agent",
                  "a person talks to the camera: a scroll-stopping first line, 2-4 clear points in natural spoken "
                  "language, what they hold or show while they say each one, a call to action; their look stays "
                  "exactly as in any uploaded photo."),
    "explainer": ("Explainer agent",
                  "the subject demonstrated and explained step by step: what it is, how it works, the key parts or "
                  "steps in order, a one-line takeaway; visuals that show each idea."),
    "motion": ("Video Intelligence agent (motion graphics / 3D)",
               "motion graphics and 3D: kinetic typography, animated diagrams and infographics, data charts, 2.5D "
               "layered parallax, CGI-style 3D product animation (rotation, macro materials, floating or exploded "
               "parts, cinematic studio light) and tech / SaaS / abstract-concept explainers - under one STYLE LOCK "
               "(HEX palette, type, light, materials, motion principles)."),
    "story": ("Story agent",
              "a cinematic story: characters, what they want, the obstacle, the climax and the ending, each scene a "
              "real event, with camera, light, sound and atmosphere in service of the story."),
}

AGENT_RULES = """

FIRST decide which agent fits the user's request best (from their words and any files), then enhance the prompt
the way THAT agent needs it:
{AGENTS}
A recipe, dish or drink being made -> "food". Motion graphics, kinetic text, diagrams, data / charts, a tech, SaaS or
abstract concept, or a product best shown as a 3D / CGI studio animation (floating, rotating, exploding into parts,
macro materials) -> "motion", even when the user never wrote "3D" or "motion graphics". A real-world product reel
with no person (ingredient worlds, hands, textures) -> "product". Someone talking to the camera (UGC, review,
testimonial, YouTube host) -> "presenter". Teaching how something works with real footage -> "explainer".
Otherwise -> "story". Never change what the user asked for; keep every number exactly.

OUTPUT FORMAT FOR THIS APP: return ONLY a JSON object (it replaces the plain-text output rule above; the
"enhanced_prompt" value itself follows every Output Requirement above):
{"agent": "food | motion | product | presenter | explainer | story", "why": "one short reason",
 "enhanced_prompt": "the complete, generation-ready enhanced prompt"}"""

RECIPE_CONTEXT = """

THE RECIPE (already understood by the food agent - the enhanced prompt must follow these ingredients and this
order of stages exactly; agent is "food"):
{RECIPE}"""


def auto_messages(prompt: str, duration_s: float | None, aspect_ratio: str | None, files: list[str],
                  recipe: dict | None = None) -> list[dict]:
    msgs = messages(prompt, duration_s, aspect_ratio, files)
    guides = "\n".join(f"- {k}: {label} - {text}" for k, (label, text) in AGENTS.items())
    msgs[0]["content"] += "\n\n" + SCENE_DETAIL + AGENT_RULES.replace("{AGENTS}", guides)
    if len((prompt or "").split()) >= DETAILED_WORDS:  # the user's own detailed brief: kept, only split and detailed
        msgs[0]["content"] += "\n\n" + KEEP_DETAIL
    if recipe:
        from .food.recipe import summary
        msgs[0]["content"] += RECIPE_CONTEXT.replace("{RECIPE}", summary(recipe))
    return msgs


def parse_auto(data, original: str, recipe: dict | None = None) -> dict:
    """The agent and the enhanced prompt from the JSON reply (unknown agent -> story; empty -> the user's words)."""
    data = data if isinstance(data, dict) else {}
    agent = str(data.get("agent") or "").strip().lower()
    agent = "food" if recipe else (agent if agent in AGENTS else "story")
    text = clean(str(data.get("enhanced_prompt") or ""))
    return {"agent": agent, "label": AGENTS[agent][0], "why": str(data.get("why") or "")[:200],
            "prompt": text or original.strip()}


def clean(text: str) -> str:
    """Only the prompt: no code fences, no 'Enhanced prompt:' label, no wrapping quotes."""
    t = re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip())
    t = re.sub(r"^(\*\*)?\s*(enhanced (video )?prompt|prompt)\s*:?\s*(\*\*)?\s*\n?", "", t, flags=re.I)
    t = t.strip()
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


# Auto-enhance before planning: only short creative ideas. Edits of the user's own video, data charts and prompts
# that are already detailed go to the planner exactly as written (the enhancer would invent a story for them).
SKIP = re.compile(r"\b(my|this|the attached|uploaded) (video|clip|footage|recording)\b|\b(trim|cut the pauses|"
                  r"reformat|resize|crop|colou?r grade)\b|\badd (captions|subtitles|effects|music) to\b", re.I)
DETAILED_WORDS = 60  # a prompt this long is the user's own detailed brief: kept word for word, only split into scenes


def should_auto_enhance(prompt: str) -> str | None:
    """None when the prompt should be enhanced; otherwise why it is kept as written."""
    p = (prompt or "").strip()
    if not p:
        return "no text to enhance"
    if SKIP.search(p):
        return "an edit of your own video: kept exactly as written"
    return None
