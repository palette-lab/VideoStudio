"""Validated data structures: the production brief (what the user wants) and the decision object (what will run).

Values outside the fixed vocabularies are coerced to a safe default instead of being trusted.
"""

from pydantic import BaseModel, Field, field_validator, model_validator

INTENTS = ["create_new", "animate_asset", "edit_existing", "recreate_reference", "unclear"]
PRODUCTION_TYPES = ["commercial", "product_showcase", "product_animation", "cinematic", "storytelling", "fashion",
                    "documentary", "educational", "explainer", "animation", "social_short", "music_video",
                    "editing", "reference_recreation", "talking_presenter", "other"]
SHOT_STRUCTURES = ["single_continuous", "multi_scene", "unspecified"]
ASSET_ROLES = ["product", "character", "garment", "background", "style_reference", "motion_reference",
               "footage_to_edit", "audio_track", "logo", "unclear"]
ASSET_USES = ["must_appear", "style_only", "edit_source", "audio_source", "reference_only", "unclear"]
MOTION_KINDS = ["realistic", "programmatic_precise"]
PROGRAMMATIC_ELEMENTS = ["exploded_view", "product_rotation", "spec_callouts", "kinetic_text", "data_visualization"]
INTEGRATIONS = ["overlay_on_footage", "separate_segments", "standalone", "unspecified"]
AUDIO_STATES = ["requested", "excluded", "unspecified"]
EDIT_OPERATIONS = ["trim", "enhance", "captions", "audio_mix", "color_grade", "reformat", "assemble", "transitions",
                   "effects"]


def _norm(v):
    return v.strip().lower().replace(" ", "_").replace("-", "_") if isinstance(v, str) else v


def _one_of(allowed, default):
    def check(v):
        v = _norm(v)
        return v if v in allowed else default
    return check


def _subset(allowed):
    def check(v):
        if isinstance(v, str):
            v = [v]
        out = []
        for x in v or []:
            x = _norm(x)
            if x in allowed and x not in out:
                out.append(x)
        return out
    return check


def _text(v):
    """LLMs sometimes return a list or number where text is expected; join instead of failing."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if str(x).strip())
    return str(v)


def _str_list(v):
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(x) for x in (v or []) if str(x).strip()]


class Question(BaseModel):
    id: str = ""
    question: str
    why: str = ""
    options: list[str] = Field(default_factory=list)
    blocking: bool = True
    affects: str = "routing"  # routing (changes which production/app) | content (details the app will need)
    kind: str = "answer"  # answer | choice | upload
    source: str = "ai"  # ai | rule

    _opts = field_validator("options", mode="before")(_str_list)
    _txt = field_validator("question", "why", mode="before")(_text)
    _aff = field_validator("affects", mode="before")(_one_of(["routing", "content"], "routing"))


class AssetRole(BaseModel):
    asset_id: str
    role: str = "unclear"
    use: str = "unclear"
    note: str = ""

    _txt = field_validator("note", mode="before")(_text)
    _role = field_validator("role", mode="before")(_one_of(ASSET_ROLES, "unclear"))
    _use = field_validator("use", mode="before")(_one_of(ASSET_USES, "unclear"))


class Scene(BaseModel):
    shot_structure: str = "unspecified"
    scene_count: int | None = None
    narrative: bool = False
    consistency: list[str] = Field(default_factory=list)

    _shot = field_validator("shot_structure", mode="before")(_one_of(SHOT_STRUCTURES, "unspecified"))
    _cons = field_validator("consistency", mode="before")(_str_list)

    @field_validator("scene_count", mode="before")
    @classmethod
    def _count(cls, v):
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None


class Visual(BaseModel):
    style: str = ""
    lighting: str = ""
    camera: list[str] = Field(default_factory=list)
    composition: str = ""

    _cam = field_validator("camera", mode="before")(_str_list)
    _txt = field_validator("style", "lighting", "composition", mode="before")(_text)


class Motion(BaseModel):
    kinds: list[str] = Field(default_factory=list)
    programmatic_elements: list[str] = Field(default_factory=list)
    integration: str = "unspecified"
    overlay_elements: list[str] = Field(default_factory=list)  # placed on top of realistic shots, whatever integration says
    details: list[str] = Field(default_factory=list)

    _kinds = field_validator("kinds", mode="before")(_subset(MOTION_KINDS))
    _prog = field_validator("programmatic_elements", "overlay_elements", mode="before")(_subset(PROGRAMMATIC_ELEMENTS))
    _int = field_validator("integration", mode="before")(_one_of(INTEGRATIONS, "unspecified"))
    _det = field_validator("details", mode="before")(_str_list)

    @model_validator(mode="after")
    def _overlays_are_programmatic(self):
        # Anything placed on top of footage is programmatic animation, even if the model listed it only once.
        self.programmatic_elements += [e for e in self.overlay_elements if e not in self.programmatic_elements]
        if self.overlay_elements and "programmatic_precise" not in self.kinds:
            self.kinds.append("programmatic_precise")
        return self


class Audio(BaseModel):
    voiceover: str = "unspecified"
    music: str = "unspecified"
    subtitles: str = "unspecified"
    sound_effects: str = "unspecified"

    _v = field_validator("voiceover", "music", "subtitles", "sound_effects", mode="before")(
        _one_of(AUDIO_STATES, "unspecified"))

    def requested(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if v == "requested"]

    def excluded(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if v == "excluded"]


class Editing(BaseModel):
    operations: list[str] = Field(default_factory=list)

    _ops = field_validator("operations", mode="before")(_subset(EDIT_OPERATIONS))


class Constraints(BaseModel):
    duration_s: float | None = None
    aspect_ratio: str | None = None
    must_include: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)

    _lists = field_validator("must_include", "exclusions", mode="before")(_str_list)

    @field_validator("duration_s", mode="before")
    @classmethod
    def _dur(cls, v):
        try:
            d = float(v)
            return d if d > 0 else None
        except (TypeError, ValueError):
            return None

    @field_validator("aspect_ratio", mode="before")
    @classmethod
    def _aspect(cls, v):
        if not isinstance(v, str) or not v.strip():
            return None
        v = v.strip().replace("/", ":").replace("x", ":")
        return v if ":" in v else None


class Evidence(BaseModel):
    field: str = ""
    source: str = "prompt"  # prompt | asset | inferred
    quote: str = ""

    _txt = field_validator("field", "source", "quote", mode="before")(_text)


class Brief(BaseModel):
    detected_intent: str = "unclear"
    user_goal: str = ""
    requested_output: str = ""
    production_type: list[str] = Field(default_factory=list)
    asset_roles: list[AssetRole] = Field(default_factory=list)
    scene: Scene = Field(default_factory=Scene)
    visual: Visual = Field(default_factory=Visual)
    motion: Motion = Field(default_factory=Motion)
    audio: Audio = Field(default_factory=Audio)
    editing: Editing = Field(default_factory=Editing)
    constraints: Constraints = Field(default_factory=Constraints)
    conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    suggested_capabilities: list[str] = Field(default_factory=list)
    missing_info: list[Question] = Field(default_factory=list)
    refers_to_uploads: bool = False  # the user talks about files they uploaded/attached
    confidence: float = 0.5

    _intent = field_validator("detected_intent", mode="before")(_one_of(INTENTS, "unclear"))
    _txt = field_validator("user_goal", "requested_output", mode="before")(_text)
    _types = field_validator("production_type", mode="before")(_subset(PRODUCTION_TYPES))
    _lists = field_validator("conflicts", "assumptions", "suggested_capabilities", mode="before")(_str_list)

    @field_validator("refers_to_uploads", mode="before")
    @classmethod
    def _bool(cls, v):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("missing_info", "evidence", "asset_roles", mode="before")
    @classmethod
    def _drop_bad(cls, v):
        return [x for x in (v or []) if isinstance(x, dict)]

    @field_validator("missing_info", mode="after")
    @classmethod
    def _need_text(cls, v):
        return [q for q in v if q.question.strip()]

    def role_of(self, asset_id: str) -> AssetRole:
        for r in self.asset_roles:
            if r.asset_id == asset_id:
                return r
        return AssetRole(asset_id=asset_id)


class WorkflowStep(BaseModel):
    step_id: str
    app_id: str
    app_name: str
    responsibility: str
    capabilities: list[str]
    inputs: list[dict]
    outputs: list[str]


class Decision(BaseModel):
    detected_intent: str
    user_goal: str
    requested_output: str
    input_assets: list[dict]
    asset_roles: list[AssetRole]
    production_type: list[str]
    scene_requirements: Scene
    visual_requirements: Visual
    motion_requirements: Motion
    audio_requirements: Audio
    editing_requirements: Editing
    constraints: Constraints
    required_capabilities: list[dict]
    selected_applications: list[str]
    selection_reasons: list[str]
    workflow_steps: list[WorkflowStep]
    handoff_requirements: list[dict]
    unsupported_requirements: list[dict]
    unconfirmed_requirements: list[dict]
    confidence: float
    confidence_breakdown: dict
    clarification_required: bool
    questions: list[Question]
    execution_ready: bool
    readiness_blockers: list[str]
