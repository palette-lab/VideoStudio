"""ChefVision food engine: understand the food first, plan the cooking process second, enhance the prompts third.

    recipe.understand()        FoodCategoryAnalyzer + RecipeUnderstandingEngine + CookingWorkflowPlanner (1 LLM call)
    library                    FoodActionLibrary (choreography + physics per action, state progression)
    choreography.planner_rules SceneChoreographyEngine + FoodPhysicsPromptEngine (rules for the existing planner call)
    prompts                    ImagePromptEnhancer + VideoMotionPromptEnhancer (scene -> image JSON / motion JSON)
    validate.check             PromptQualityValidator + VisualContinuityManager (deterministic)

Source: ChefVision_AI_Enhanced_Food_Cinematic_Master_Prompt.pdf, turned into structured rules and data.
"""

from . import choreography, library, prompts, recipe, validate  # noqa: F401


def finalize(scenes: list[dict], recipe_: dict, aspect_ratio: str) -> None:
    """Attach the image / motion JSON to each scene and swap in the enhanced prompts the models receive."""
    for n, s in enumerate(scenes, start=1):
        s["image_json"] = prompts.image_json(s, n, recipe_, aspect_ratio)
        s["motion_json"] = prompts.motion_json(s, n)
        s["image_prompt"] = prompts.image_prompt_text(s, n, recipe_, aspect_ratio)
        s["motion_text"] = prompts.motion_prompt_text(s, n)
