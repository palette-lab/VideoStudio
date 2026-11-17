"""Deterministic requirement compiler: structured brief fields -> required capabilities.

Rules operate on validated brief fields only (never on prompt keywords). Every capability carries the reason/field
that required it. Capabilities are grouped in the order a production happens: reference -> generate -> motion -> edit.
"""

from . import registry
from .schemas import Brief, Question

GROUP_ORDER = ["reference", "generate", "motion", "edit", "finish"]
VISIBLE_ROLES = {"product", "character", "garment", "background", "logo"}
EDIT_CAPS = {
    "trim": "edit.trim_and_cut", "enhance": "edit.enhance_quality", "captions": "edit.captions_subtitles",
    "audio_mix": "edit.audio_mix", "color_grade": "edit.color_grade", "reformat": "edit.reformat_aspect",
    "assemble": "edit.assemble_clips", "transitions": "edit.transitions",
}
FINISH_CAP = "edit.explainer_effects"
EXPLAINER_TYPES = {"explainer", "educational"}
CREATOR_TYPES = {"social_short"}  # creator/UGC-style shorts get finishing effects without being asked
PROGRAMMATIC_CAPS = {
    "exploded_view": "motion.exploded_view", "product_rotation": "motion.product_animation",
    "spec_callouts": "motion.product_animation", "kinetic_text": "motion.kinetic_typography",
    "data_visualization": "motion.data_visualization",
}
CREATING = {"create_new", "animate_asset", "recreate_reference"}


def compile_brief(brief: Brief, assets: dict, presenter_mode: bool = False) -> dict:
    reqs: list[dict] = []
    notes: list[str] = []
    conflicts: list[str] = []

    def add(cap, group, reason, field):
        if not any(r["capability"] == cap for r in reqs):
            reqs.append({"capability": cap, "group": group, "reason": reason, "field": field})

    roles = {r.asset_id: r for r in brief.asset_roles if r.asset_id in assets}
    modality = {aid: assets[aid]["modality"] for aid in assets}
    footage = [a for a, r in roles.items() if r.role == "footage_to_edit" and modality[a] == "video"]
    motion_refs = [a for a, r in roles.items() if r.role == "motion_reference"]
    types = set(brief.production_type)
    intent = brief.detected_intent
    creating = intent in CREATING
    prog = brief.motion.programmatic_elements
    prog_only = bool(prog) and "realistic" not in brief.motion.kinds and (
        brief.motion.integration == "standalone" or "programmatic_precise" in brief.motion.kinds)
    realistic = creating and not prog_only
    fashion = "fashion" in types or any(r.role == "garment" for r in roles.values())
    multi = brief.scene.shot_structure == "multi_scene" or (brief.scene.scene_count or 0) > 1

    # Reference recreation
    if intent == "recreate_reference" or "reference_recreation" in types or motion_refs:
        add("story.reference_shot_breakdown", "reference", "recreate the structure and timing of a reference video",
            "detected_intent / asset_roles.motion_reference")

    # A talking head can't show an unboxing, close-ups or the product in use: when a creator / UGC video needs several
    # scenes it is a storyboarded film (the creator kept consistent), unless the user picked Presenter Studio. A host
    # explaining something (explainer, educational, testimonial) stays a talking head even when the analysis split the
    # talk into "scenes" - Presenter Studio cuts it into parts itself.
    shows_more = bool(types & {"commercial", "social_short"}) and not (types & {"explainer", "educational"})
    creator_film = creating and "talking_presenter" in types and multi and shows_more and not presenter_mode
    presenter = creating and "talking_presenter" in types and not creator_film
    if creator_film:
        notes.append("Several scenes are needed (not just someone talking to camera), so it is made as a storyboarded "
                     "film with the same creator in every scene.")

    # New realistic footage
    if presenter:  # a person talking to camera: one lip-synced presenter, not a scene-by-scene film
        add("presenter.talking_head", "generate", "a presenter talking to camera with lip-synced speech",
            "production_type")
    elif realistic:
        if fashion:
            add("fashion.garment_film", "generate", "fashion production that presents garments", "production_type")
            if any(r.role == "character" for r in roles.values()):
                add("fashion.model_garment_fit", "generate", "a model/character wears the garments", "asset_roles")
            if multi and brief.scene.consistency:
                add("story.character_consistency", "generate", "keep looks consistent across scenes", "scene.consistency")
        elif multi:
            add("story.multi_scene_storyboard", "generate", "several scenes in sequence", "scene.shot_structure")
            add("video.from_storyboard", "generate", "produce the scenes as video", "scene.shot_structure")
            if brief.scene.consistency or creator_film:
                add("story.character_consistency", "generate", "same characters/objects in every scene", "scene.consistency")
        else:
            shown = [a for a, r in roles.items() if modality[a] == "image" and r.role in VISIBLE_ROLES
                     and r.use in ("must_appear", "unclear")]
            if shown:
                add("video.image_to_video", "generate", "bring the uploaded image(s) to life", "asset_roles")
            else:
                add("video.text_to_video", "generate", "new footage from the description", "detected_intent")

    # Programmatic animation
    if prog == ["kinetic_text"] and brief.audio.subtitles == "requested" and intent == "edit_existing":
        prog = []  # subtitles on existing footage are captions, not designed typography
        notes.append("Subtitles are handled as captions, not as animated typography.")
    if presenter and prog:  # Presenter Studio sets every word itself as an editable text layer (text_layer.py)
        notes.append("Titles, key words and captions are added by Presenter Studio as editable text.")
        prog = []
    for el in prog:
        add(PROGRAMMATIC_CAPS[el], "motion", f"precise programmatic {el.replace('_', ' ')}", "motion.programmatic_elements")
    has_footage = realistic or bool(footage)
    if prog and has_footage:
        if brief.motion.integration == "separate_segments":
            add("edit.assemble_clips", "edit", "join animation segments with the footage", "motion.integration")
            over = [e for e in brief.motion.overlay_elements if e in prog]
            if over:  # ads often mix both: own animated scenes AND graphics over the filmed shots
                add("motion.overlay_graphics", "motion",
                    f"{', '.join(e.replace('_', ' ') for e in over)} placed on top of the footage", "motion.overlay_elements")
        else:
            add("motion.overlay_graphics", "motion", "graphics placed on top of the footage", "motion.integration")
            if brief.motion.integration == "unspecified":
                notes.append("Programmatic graphics will be placed on top of the footage (assumed; not stated).")

    # Editing and audio
    ops = list(brief.editing.operations)
    if creating and not footage and brief.motion.integration != "separate_segments":
        # Transitions/assembly only exist when separate clips are joined; inside a new production they are its job.
        for op in ("transitions", "assemble"):
            if op in ops:
                ops.remove(op)
                notes.append(f"'{op}' is handled inside the production itself (no separate clips to join).")
    if brief.audio.subtitles == "requested" and "captions" not in ops:
        ops.append("captions")
    # Presenter Studio makes the whole soundtrack itself (the voice, then a music bed ducked under it), so a presenter
    # video never needs a separate audio-mix step (a later editor step would fail: there is no uploaded track to mix).
    mix_keys = () if presenter else ("voiceover", "music", "sound_effects")
    if any(getattr(brief.audio, k) == "requested" for k in mix_keys) and "audio_mix" not in ops:
        ops.append("audio_mix")
    if brief.audio.subtitles == "excluded" and "captions" in brief.editing.operations:
        conflicts.append("Subtitles are both requested and excluded.")
        ops.remove("captions")
    wants_effects = "effects" in ops
    ops = [o for o in ops if o != "effects"]
    for op in ops:
        add(EDIT_CAPS[op], "edit", f"{op.replace('_', ' ')} on the footage", "editing.operations / audio")
    # Finishing effects run last, on the finished video: asked for, or automatic for explanation videos.
    if FINISH_CAP in registry.capabilities() and (reqs or footage) and not presenter and (
            wants_effects or types & EXPLAINER_TYPES or creator_film or (creating and types & CREATOR_TYPES)):
        add(FINISH_CAP, "finish", "finishing effects for an explanation video, picked from what is said",
            "editing.operations" if wants_effects else "production_type")

    # Conflicts that change the production
    if brief.scene.shot_structure == "single_continuous" and (brief.scene.scene_count or 0) > 1:
        conflicts.append(f"One continuous shot was requested, but also {brief.scene.scene_count} scenes.")
    for c in brief.conflicts:
        if c not in conflicts:
            conflicts.append(c)

    subtasks = []
    for g in GROUP_ORDER:
        caps = [r["capability"] for r in reqs if r["group"] == g]
        if caps:
            subtasks.append({"id": f"t_{g}", "group": g, "description": _group_label(g),
                             "required_capabilities": caps, "basis": "compiled"})

    vocab = registry.capabilities()
    compiled = {r["capability"] for r in reqs}
    suggested = {c for c in brief.suggested_capabilities if c in vocab}
    return {
        "requirements": reqs,
        "subtasks": subtasks,
        "notes": notes,
        "conflicts": conflicts,
        "footage_assets": footage,
        "cross_check": {"ai_only": sorted(suggested - compiled), "compiler_only": sorted(compiled - suggested),
                        "agree": suggested == compiled},
    }


def _group_label(g):
    return {"reference": "Understand the reference video", "generate": "Produce the footage",
            "motion": "Programmatic animation", "edit": "Edit and finish", "finish": "Finishing effects"}[g]


def rule_questions(brief: Brief, compiled: dict, prompt: str, assets: dict) -> list[Question]:
    """Deterministic clarification triggers. Each question has answer options."""
    qs: list[Question] = []
    mods = {a["modality"] for a in assets.values()}
    if compiled["conflicts"]:
        qs.append(Question(id="conflict", source="rule", kind="choice", question=compiled["conflicts"][0] + " Which do you want?",
                           why="These requirements contradict each other and lead to different productions.",
                           options=_conflict_options(brief)))
    if not prompt.strip() and assets and brief.detected_intent == "unclear":
        opts = []
        if "image" in mods:
            opts += ["Animate it into one short clip", "Make a multi-scene video from it", "Product animation / exploded view"]
        if "video" in mods:
            opts += ["Edit or enhance this footage", "Recreate this video's style"]
        if "audio" in mods:
            opts += ["Make a video for this audio"]
        qs.append(Question(id="outcome", source="rule", kind="choice", options=opts[:4],
                           question="What would you like to do with the uploaded file(s)?",
                           why="Files were uploaded without a description of the result you want."))
    elif brief.detected_intent == "unclear" and not qs:
        q = next((m for m in brief.missing_info if m.blocking), None)
        qs.append(q or Question(id="outcome", source="rule", question="What should the video be about, and what is it for?",
                                why="The request does not say what to produce."))
    if brief.detected_intent == "edit_existing" and not compiled["footage_assets"] and "video" not in mods:
        qs.append(Question(id="upload_footage", source="rule", kind="upload", question="Please upload the video you want edited.",
                           why="Editing needs the original footage."))
    if brief.detected_intent == "edit_existing" and compiled["footage_assets"] and not any(
            r["group"] == "edit" for r in compiled["requirements"]):
        qs.append(Question(id="edit_ops", source="rule", kind="choice", question="What should be done to the footage?",
                           why="No edit was specified.", options=["Trim / shorten", "Add subtitles", "Enhance quality",
                                                                  "Change aspect ratio"]))
    return qs[:2]


def _conflict_options(brief: Brief) -> list[str]:
    q = next((m for m in brief.missing_info if m.options), None)
    if q:
        return q.options[:4]
    if brief.scene.shot_structure == "single_continuous" and (brief.scene.scene_count or 0) > 1:
        return ["One continuous shot", f"{brief.scene.scene_count} separate scenes"]
    return ["Keep the first requirement", "Keep the second requirement"]
