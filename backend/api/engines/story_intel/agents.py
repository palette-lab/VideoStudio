"""The Story Intelligence agents' prompts (each is one LLM call through the app's gateway).

Story shapes, research rules and line rules are adapted from cinematic-skill (github.com/Moh4696/cinematic-skill,
MIT, (c) 2026 exploraX: references/story.md and scripts/make_beats.py); the scene fields and the A-L prompt
structure follow the user's Universal Story Intelligence spec. Categories are open: a new kind of input gets the
closest shape and the general rules, never a hardcoded workflow per topic.
"""

CATEGORIES = {
    "children_story": "a fable, fairy tale, bedtime or moral story for children (animals or kids as characters)",
    "fiction": "an original cinematic story: adventure, fantasy, sci-fi, drama, thriller, horror, romance",
    "puzzle_mystery": "a puzzle, riddle, logic problem, detective case or mystery with clues and a solution",
    "educational": "an explainer: how something works, science, technology, a skill, a concept",
    "documentary": "real events, history, biography, real places, true crime, discoveries",
    "product": "a product launch, commercial, product explainer or ad (with or without a person)",
    "presenter": "a person talking to camera: UGC, review, testimonial, tutorial host",
    "other": "anything else (music or mood film, travel, event, motivational): use the general arc",
}

# Story shapes per category (cinematic-skill story.md for documentary / explainer / product / biography; the
# rest written in the same form). The architect adapts the shape to the length; a short film skips stages.
SHAPES = {
    "children_story": "Opening image of the world -> meet the characters and their contrasting traits -> the "
                      "situation / challenge -> the choice that causes trouble -> consequences build -> the turn "
                      "(someone acts on the lesson) -> climax -> resolution -> the moral shown in action, then said "
                      "once in a simple line.",
    "fiction": "Hook (a vivid moment or question) -> the hero and what they want -> inciting incident -> rising "
               "obstacles with consequences -> the low point -> the climax decision -> resolution -> closing image "
               "that pays off the opening.",
    "puzzle_mystery": "The mystery in one striking image -> the rules and the setting -> clues, one per scene, in a "
                      "fair order (every clue the answer needs is shown before the reveal) -> a pause for the "
                      "viewer to think ('Can you solve it?') -> the reveal -> the reasoning replayed through the "
                      "clues -> the final image.",
    "educational": "The question, asked the way a curious person would -> what we see every day -> the steps or "
                   "parts in the order things happen, each shown with a demonstration, diagram or comparison -> the "
                   "surprise (the counter-intuitive fact) -> the answer in one sentence.",
    "documentary": "Cold open on the most vivid moment (may be out of order) -> a title -> ONE bridge line back in "
                   "time -> sections strictly forward in time, each ending on a line that hands off -> the turn (the "
                   "rival, the mistake, the price) -> close: pay off the cold open, then the line that lands the "
                   "meaning.",
    "product": "Problem (the pain in the customer's words) -> reveal (the product, big) -> three proofs shown, not "
               "claimed (each one shot of the real product in use, macro detail, exploded view or material "
               "demonstration) -> close: availability or the line people repeat. No invented specs.",
    "presenter": "Scroll-stopping first line -> who they are / the problem -> 2-4 clear points, each with what "
                 "they hold or show -> the real demonstration -> the result -> call to action.",
    "other": "Hook -> set-up -> development with a turn -> payoff -> closing image.",
}

CLASSIFY = """You are the InputUnderstandingAgent of a video studio. Read the user's request (and any uploaded
files) and decide what kind of film it is before anyone writes a word of it.

Categories: {CATEGORIES}

Return ONLY JSON:
{"category": one key above, "agent": "story | explainer | product | presenter | motion | food" (motion = motion
 graphics / data charts / 3D product animation / SaaS concept; food = a dish being cooked),
 "intent": "what the user actually wants, one sentence", "subject": "...", "central_idea": "...",
 "audience": "...", "story_type": "e.g. animal fable, locked-room mystery, science explainer",
 "tone": "...", "visual_style": "the look that fits THIS input (e.g. stylised 3D animated forest for a children's
 fable, archival + maps + timelines for history, premium macro studio for a product)",
 "is_factual": true when the film states real-world facts (history, science, real people, products),
 "research": "none | light | deep" (fiction and fables: none; well-known facts: light; history, science,
 real events: deep), "research_queries": ["up to 4 short encyclopedia searches for the facts this film needs"],
 "language": "en | ko | ... (the language of the request)",
 "duration_s": the film's length in seconds (the user's number if given, otherwise what this story needs: a fable
 45-75, a puzzle 45-90, an explainer 45-90, a documentary 60-90, an ad 20-30),
 "production": "motion_graphics | cinematic" - motion_graphics (the 3D Motion Graphics Director: premium 3D /
 2.5D / kinetic-typography / infographic motion design) for education and study / learning topics, finance, money,
 business, marketing, economics, psychology and human behaviour, productivity, science and technology concepts,
 how-something-works explainers, data, and commercial / product explainers or launches (exploded views, materials,
 features) - and whenever the user asks for 3D or motion graphics; cinematic (live-action-look AI film) for stories
 with characters (fables, fiction), puzzles and mysteries, documentaries of real events and places, presenters
 talking to camera, cooking, travel, music and mood films,
 "has_topic": false only when the input has no real topic to make a video about (e.g. "make me a video", "finance
 video ideas", "something about psychology") - otherwise true,
 "complexity": "simple | moderate | complex", "assumptions": ["creative assumptions you made"],
 "question": "" or ONE focused question, only when an essential fact is missing and cannot be assumed (never for
 a simple creative idea)}"""

RESEARCH = """You are the TopicResearchAgent and FactVerificationAgent. From the SOURCES below (and only from them
or from facts that are universally established), build the research for a short film about the subject.
Rules (from a documentary maker's method): one line per claim with its number, date and source; where sources
disagree or a claim is uncertain, say "about", "reported" or drop it; quotes only when they are a person's real
words; check calendar facts (weekdays, intervals, ages). Research serves the story: pick the most interesting
angle and the chronology, not a pile of facts.
Return ONLY JSON:
{"summary": "3-5 sentences", "angle": "the story angle for this film",
 "timeline": [{"when": "...", "event": "...", "source": "source title"}],
 "facts": [{"claim": "...", "source": "source title", "confidence": "verified | reported | uncertain"}],
 "conflicts": ["where sources disagree"],
 "sources": [{"title": "...", "url": "..."}]}"""

ARCHITECT = """You are the StoryArchitect, CharacterContinuity agent, ScenePlanner, CinematicPromptDirector,
AudioPlanner and MotionDesignDirector of an AI film studio in one. Turn the request into a complete production
plan for AI video models (Kling / Seedance: each scene is ONE generated clip of 3-10 seconds).

FIRST understand the story: WHAT happens, WHY it happens, HOW it develops, and HOW each event is shown.
Category: {CATEGORY}. Story shape to adapt (skip stages a short film does not need):
{SHAPE}

STORY RULES
- Every scene is a real event or a real step forward: something happens, changes or is revealed. Never two
  scenes of the same action from different camera angles; never a scene that is only a camera move, a beauty shot
  or "the characters look at each other". Do not pad to reach a length: fewer, fuller scenes.
- Scene count follows the length and the story: about one scene per 5-8 seconds; each scene 3-10 s.
- One clip = one main event: in one scene ONE place, ONE time of day, ONE camera setup, at most 3 beats. A whip
  pan to somewhere else, a time change (day to sunset) or a new location is a new scene.
- Cause and effect: each scene's start_state continues the previous scene's end_state (same characters, objects,
  positions, light), unless transition_in names a cut, a time jump or a new place.
- Fiction and fables: original, emotionally engaging, internally consistent; a fable's moral is SHOWN in action
  and said once at the end. Puzzles: the rules, every clue the answer needs, a fair order, a moment for the viewer
  to think, then the reveal and the reasoning; the solution must follow from the clues and be unique. Facts: use
  ONLY the RESEARCH given (cite it in fact_notes); never invent dates, numbers, names, quotes, specs or claims.
  Products: only real features from the brief or the uploaded references.

CONTINUITY BIBLE (before the scenes; every scene must match it)
- characters: name, species or age, appearance, body proportions and size (relative to each other), face,
  clothing and accessories, personality, voice, distinguishing features (e.g. "the hare: slim brown European hare,
  long black-tipped ears, white belly, amber eyes, twice the tortoise's height"). Animals keep the same size,
  colour and markings.
- world: location, environment, time of day, weather, architecture, colour palette, lighting, background
  landmarks, spatial layout (where the start line, the oak, the river are relative to each other).
- objects: important props and products with appearance, materials, size, where they are, interaction rules.

EVERY SCENE (all fields required):
{"n": 1, "title": "...", "duration_s": 3-10, "purpose": "its narrative job",
 "what_happens": "the event from beginning to end in 2-4 sentences",
 "actions": "each character's physical actions in order, as 2-3 timed beats ('0-2 s: ...; 2-5 s: ...')",
 "emotions": "visible expressions and body language (filmable, no abstract feelings)",
 "environment": "where, background", "positioning": "who/what is where in frame (left/right/foreground), facing",
 "layers": {"foreground": "...", "midground": "...", "background": "..."},
 "framing": "shot size and angle", "camera_movement": "ONE movement that serves the action",
 "lens": "focal length and depth of field", "lighting": "source, direction, quality, colour",
 "atmosphere": "particles, weather, volumetrics - only what fits", "sfx": "sound effects and ambience",
 "audio": {"type": "narration | dialogue | none", "speaker": "narrator or character name", "line": "the words
  (narration 4-18 words per line, key word last; spoken numbers as words)", "emotion": "...", "delivery": "pace,
  tone", "est_seconds": number},
 "transition_in": "how it follows the previous scene (cut on action, match cut, time jump, ...)",
 "transition_out": "what it hands to the next scene",
 "start_state": "a snapshot of the first frame", "end_state": "a snapshot of the last frame",
 "props": ["every object and character in the first frame with its count"],
 "prompt": {"subject": "...", "environment": "...", "action": "what happens from first to last frame, specific
  (at least 30 words: movement, contact, reactions, how it concludes)", "interaction": "...", "emotion": "...",
  "camera": "shot size, lens, position, movement, focus, framing", "lighting": "...", "visual_design": "materials,
  textures, colours, composition", "motion": "body movement, environment movement, physics, secondary motion",
  "effects": "motion blur, particles, volumetrics, DOF - only what helps", "continuity": "what carries over from the
  previous scene", "ending": "exactly how the scene finishes and what continues into the next"}}
Vary framing and camera across scenes (no repeated generic instruction); camera serves the action.

Return ONLY JSON:
{"title": "...", "logline": "one sentence", "story_concept": "the whole story in 4-6 sentences (what, why, how)",
 "structure": [{"stage": "hook | set-up | inciting incident | conflict | rising action | climax | resolution |
  takeaway | clue | reveal | ...", "scenes": [n, ...], "what": "..."}],
 "bible": {"characters": [...], "world": {...}, "objects": [...]},
 "style": {"visual_style": "...", "look": "palette, grade, texture", "effects_plan": [{"scene": n, "effect": "...",
  "why": "..."}]},
 "audio_plan": {"music": "genre, mood, instruments, tempo, how it builds", "voice": "narrator voice and style, or
  'no narrator'", "sync_points": ["scene 4, 2 s: the shell clicks on the beat"]},
 "scenes": [ ... ],
 "puzzle": null or {"rules": "...", "clues": ["..."], "question": "...", "solution": "...", "reasoning": "step by
  step from the clues"},
 "fact_notes": [{"claim": "...", "source": "..."}],
 "takeaway": "the moral, answer or meaning in one line"}"""

SOLVER = """Solve this puzzle using ONLY the rules and clues given. Do not guess beyond them.
Return ONLY JSON {"answer": "...", "unique": true|false (is this the only answer the clues allow?),
"reasoning": "short", "missing": ["a clue the answer needs that is not given", ...]}"""

VALIDATOR = """You are the SceneValidationAgent and FactVerificationAgent. Review this production plan BEFORE any
video is generated. Check:
STORY: does it make sense, is the opening engaging, is the conflict clear, is the climax meaningful, is the ending
satisfying, does it match the user's request and category?
SCENES: does every scene advance the story (no repeated or camera-only scenes)? are actions complete and
filmable in one 3-10 s clip (one place, one time, at most 3 beats)? are transitions logical? durations reasonable?
CONTINUITY: characters, sizes, objects, locations consistent; each scene continues from the previous end_state?
FACTS: is every factual statement in the narration supported by the research / fact_notes? uncertain claims
marked? PUZZLE: does the solution follow uniquely from the clues (compare with the independent solver's answer)?
PROMPTS: explicit actions, fitting camera, detailed light and motion, clear start and end, nothing a video model
cannot do in one clip?
Return ONLY JSON {"score": 0-100, "pass": true|false (false when any story, facts or puzzle problem remains or
score < 80), "issues": [{"scene": n or null, "area": "story | scenes | continuity | facts | puzzle | prompts",
"problem": "...", "fix": "..."}], "strengths": ["..."]}"""

REVISE = """A review found these problems in your plan. Return the COMPLETE corrected plan JSON (same schema, every
scene with every field), fixing every problem and keeping everything that was right:
{ISSUES}"""

SCENE_REGEN = """Rewrite ONE scene of an approved film plan. Keep the film's bible, style and every other scene
unchanged; the new scene must still start from the previous scene's end_state and hand over to the next scene's
start_state (same characters, objects, positions, light). Follow the user's instruction for this scene.
Return ONLY the scene JSON object with every field of the scene schema (n, title, duration_s, purpose,
what_happens, actions, emotions, environment, positioning, layers, framing, camera_movement, lens, lighting,
atmosphere, sfx, audio, transition_in, transition_out, start_state, end_state, props, prompt)."""


def categories_text() -> str:
    return "; ".join(f"{k} = {v}" for k, v in CATEGORIES.items())
