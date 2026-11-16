"""Each app's own planner prompt (vendored verbatim) plus the user message in the shape that app sends."""

import re
from . import genres, motion_design
from .vendor.flow import model_skills
from .vendor.flow import planner_prompts as flow
from .vendor.storyboard import planner_prompts as storyboard

SB_SCENE_MIN, SB_SCENE_MAX = 3, 10  # Storyboard & Video's per-scene planning bounds


def _refs_block(refs: list[dict]) -> str:
    if not refs:
        return ""
    kind = {"character": "character", "product": "object", "garment": "object", "background": "background",
            "style_reference": "style", "logo": "object"}
    lines = ["", "REFERENCE IMAGES:"]
    for i, r in enumerate(refs, 1):
        lines.append(f"{i}. [{kind.get(r['role'], 'other')}] {r['filename']}: {r.get('summary') or 'see attached image'}")
    people = [i for i, r in enumerate(refs, 1) if r.get("role") == "character"]
    if people:
        lines.append(f"The person in reference {', '.join(map(str, people))} is a REAL person from the user's photo. "
                     "They appear exactly as in that photo in EVERY scene: same face, age, hair, same outfit and "
                     "accessories. In the bible and every image_prompt call them 'the person from the reference "
                     "photo, exactly as pictured' and describe only their action and the setting; never give them a "
                     "different age, face, outfit or look, even if the setting changes. The hairstyle changes only "
                     "when a scene SHOWS it changing (e.g. she ties her hair back before skincare); from then on it "
                     "stays that way and every later image_prompt says so.")
    return "\n".join(lines)


def prompt_for(app_id: str, brief: str, total: int, aspect: str, lo: int, hi: int, n_max: int, refs: list[dict],
               q: dict, genre: str = "cinematic", method: dict | None = None,
               recipe: dict | None = None, approved: dict | None = None) -> tuple[str, str]:
    """(system, user) for the app's planner."""
    if app_id == "pltt-flow-video-2":
        system = flow.PLAN_SYSTEM + (flow.PLAN_REFS_RULES if refs else "")
        system += model_skills.planner_brief({"id": q["video_model"], "durations": list(range(lo, hi + 1)),
                                              "end_frame": False, "audio": q["audio"]}, q["image_model"], q["audio"])
    else:
        system = storyboard.PLAN_SYSTEM + ("\n" + storyboard.PLAN_REFS_RULES if refs else "")
    if recipe:  # a cooking process: the recipe is the story (ChefVision rules replace the generic story rules)
        from .food import choreography
        story = choreography.planner_rules(recipe, n_max)
        book = genres.playbook(genre, total, choreography.COOKING_METHOD, voice="none")
    else:
        story = scene_rules()
        book = genres.playbook(genre, total, method)
    system += (book + story + voice_rules(q.get("audio", False))
               + motion_design.planning_rules(genre) + genres.openmontage_guides(genre, total))
    if recipe:
        pass  # the food rules already cover the real routine
    elif genre == "product_film":
        system += FACELESS_RULES
    elif (method or {}).get("id") == "routine" or (method is None and USE_WORDS.search(brief or "")):
        system += ROUTINE_RULES
    system += MUSIC_RULE
    if "STYLE LOCK" in (brief or "").upper():  # the Video Intelligence agent locked the look for the whole film
        from .video_intelligence import ENGINE_RULES
        system += ENGINE_RULES + " Copy the STYLE LOCK into the bible (grading, lighting, environment)."
    user = (f"Total duration: {total} seconds. Aspect ratio: {aspect}. Allowed per-scene duration: {lo}-{hi} seconds. "
            f"Use at most {n_max} scenes.\n\nBRIEF:\n{brief}{_refs_block(refs)}")
    if approved:  # the Story Intelligence plan the user approved
        from .story_intel import engine_block
        system += STORY_LOCK
        user += "\n\nAPPROVED STORY (render exactly this):\n" + engine_block(approved)
    return system, user


STORY_LOCK = """

APPROVED STORY (this overrides the story and scene rules above wherever they differ): the user approved a complete
scene-by-scene plan, given after the brief. Render it, do not rewrite it:
- One scene object per approved scene, in the same order, with the same title, the same event and its duration
  (adjust a duration by at most 1 s to fit the allowed range). Never add, merge, drop or reorder scenes, unless the
  scene limit is lower than the number of approved scenes: then keep the most important ones in order.
- "beat" = the scene's purpose; "action" = its timed beats; "camera" = its framing, lens and movement;
  "start_state" / "end_state" / "props" = the approved ones; "link" = its transition_in.
- "image_prompt" = the first frame drawn from the approved video_prompt (subject, environment, positioning,
  layers, lighting, visual design) frozen at start_state, 60-120 words, with every character exactly as in the
  bible (species, size, colours, markings, wardrobe).
- Copy the bible into "bible" (characters, environment, lighting, camera, grading) and into "continuity".
- Voice: "narration" with its line as "script" when the approved audio type is narration; "on_camera" with the line
  in "sound" for dialogue; "none" otherwise. "sound" also carries the approved sfx.
- Full 3D motion graphics plans (scenes with a "keyframe_prompt"): "image_prompt" = that keyframe prompt word for
  word (the 3D environment, the hero, materials, lights, the locked palette) INCLUDING its quoted "in_scene_text" as
  dimensional 3D letters - spelled exactly, no other letters, words or numbers anywhere. Those words are NOT repeated
  as edit_direction graphic_moments (no flat text overlay on a full-3D scene); edit_direction.design uses the
  palette's HEX colours. "action" keeps the 3D letters rigid and readable for the whole clip."""


USE_WORDS = re.compile(r"\b(how to (use|apply)|appl(y|ies|ying|ication)|demonstrat\w*|tutorial|routine|"
                       r"step[- ]by[- ]step|install\w*|set ?up)\b", re.I)

ROUTINE_RULES = """

PRODUCT IN USE (the brief shows how a product is used): show the REAL routine a person follows, in the real order,
as one continuous sequence in one place, so a viewer could copy it. Every step is a physical, filmable action:
Work out the real routine for THIS product (the examples below are only examples - do not copy them to products
they do not fit, and vary the shots and settings from film to film):
- Prepare first: what a real person does before using it. Skincare: wash the face at the sink with water, pat dry
  with a towel, tie or clip the hair back away from the face. A device: take it out, plug in, switch on.
- Open / ready the product on screen: twist off the cap, lift the dropper, press the pump, peel the seal.
- Show the texture or the inside: a drop on a fingertip or the back of the hand in close-up, the cream's swirl.
- Use it the real way: e.g. dot serum on forehead, cheeks and chin, then spread with the fingertips in gentle
  upward strokes and pat it in; never a dropper squeezed straight across the face if people do not do that.
- Show the result, then close the product (cap back on) before the final recommendation.
Keep a hook before the routine and the call to action after it. Objects keep their state between scenes: an open
cap stays open, a bottle stays where it was put, wet skin stays dewy, tied hair stays tied, until a scene shows it
change. Each scene's start_state is EXACTLY the previous scene's end_state (same place, same hands, same props)."""


MUSIC_RULE = """

MUSIC: also return a top-level "music": one line for an instrumental music bed that fits this film - genre, mood,
2-3 instruments, tempo in bpm (cut rhythm: the scene changes should feel on the beat), e.g. "minimal premium
electronic, airy pads, glassy plucks, soft deep pulse, 100 bpm". No artist or song names, no vocals."""

FACELESS_RULES = """

PRODUCT FILM WITHOUT A PERSON (studied from top product reels): the product is the only star.
- No people and no faces. Hands and forearms at most, only when they handle the product, with the same skin tone,
  manicure and sleeves in every shot.
- Music-led: every scene's "voice" is "none" and "script" is "" unless the brief asks for a voiceover. "sound" is
  ASMR and sound design that fits the shot (pour, drip, fizz, splash, cap click, soft whoosh), no speech.
- 4-6 scenes of 3-4 s, ONE visual idea each, every cut on a movement (a turn, a drop landing, a burst).
- Build the sets, props and light from the packaging's own colours (a green tube -> sage greens and water; an amber
  bottle -> warm stone, moss, dusk). One hero light idea for the film: window / gobo shadows, underwater caustics
  and light rays, or a single rim light on dark.
- Every scene makes ONE ingredient or benefit physical (only those in the brief or readable on the pack - never
  invent claims): water and bubbles for hydration, foam for cleansing, leaves or moss for botanicals, fruit for
  its flavour, light and sparkle for glow.
- Texture is the money shot: at least one extreme macro of the formula itself (a drop, a swirl, a squeeze, foam).
- The product is the reference image: the same shape, colours and label in every scene, label readable and
  front-facing in the hero and packshot scenes, never morphing. No other text, logos or words in any image_prompt.
- The last scene is a steady, front-facing packshot with clean negative space for the brand end card."""


def scene_rules() -> str:
    """Every scene is a real piece of action that picks up where the previous scene ended, not a camera move."""
    return """

STORY FIRST (this overrides any format convention above, including product-ad beats):
- The film is a STORY with real events, not a sequence of beauty shots. Before writing scenes, decide: who the
  main character is (a person, or an animal / creature when the brief calls for it), what they want or what
  situation they are in, what changes, and how it pays off. Write that arc in "story".
- Every scene is one story beat that moves it forward: set-up, a problem or tension, a turn, a payoff. Ask "what
  happens next?" - each scene answers it with a new event in a new moment. Cut a scene that only shows something
  looking nice.
- For a product or brand film the product is used INSIDE the story by the character, at the moment it matters
  (e.g. a commuter in a loud crowded train puts on the headphones and the world turns calm; a runner in the rain,
  a parent at night, a traveller on a plane). Show the situation before and the change after.
- At most ONE scene in the film may be a product-only shot (the closing packshot), and only for an ad. Light
  sweeps, slow rotations, floating products, reveals in a black void and close-up glides over surfaces are NOT
  action and NOT story: do not build scenes from them.
- Real locations with life in them (streets, homes, trains, cafes, nature, offices) rather than an empty studio
  void, unless the brief asks for a studio.

SCENES (add these fields to EVERY scene object, overriding any rule above about them):
- "beat": the story beat in one line: what happens in the story here and why it matters (e.g. "the noise of the
  train overwhelms her", "she finds calm"). Each scene's beat is different from the others.
- "weight": 1-5, how much this beat matters to the story (5 = the emotional peak / key reveal, 1 = a quick
  transition). Scene durations follow the weights: the big moments get the most screen time.
- ONE CLIP, ONE EVENT: each scene is one AI video clip of 3-10 s and can perform only ONE main event (plus its
  small lead-in and result). A race start AND a nap, or plating AND the first bite, are two scenes. Never more than
  3 timed beats in a scene, each at least 1.5 s. Inside one scene: ONE place, ONE time of day, ONE camera setup -
  no whip pans to another place, no intercutting, no "meanwhile", no day turning to sunset, no close-up that
  becomes a wide; those are new scenes. Characters already in the shot stay in it, nobody new appears from
  nowhere, and every creature keeps its size, colour and markings.
- "action": what visibly HAPPENS in this clip, in English, as 2-3 short beats in order, with the subject doing or
  changing something real: a person moves, turns, picks up, puts on, reacts; an object opens, rotates, lights up,
  splashes, unfolds; the environment changes (light sweeps across, rain starts, a door opens). Name the beats with
  their timing in the scene (e.g. "0-2 s: she lifts the headphones from the case; 2-4 s: settles them over her ears;
  4-5 s: closes her eyes as the street noise fades"). Something must be different between the first and the last
  frame. A camera move on a still subject is NEVER an action on its own.
- "camera": shot size, angle, lens and ONE movement that serves the action (follow it, reveal it, push in on its peak).
  Vary it across the film: no two scenes in a row with the same shot size and the same movement.
- "start_state" / "end_state": the first and the last frame. Scene 1's start_state opens the film; every later
  scene's "start_state" MUST pick up from the previous scene's "end_state": the same subject, the same wardrobe,
  props, set and light, continuing the same moment or the next moment of it (a closer angle of the same moment, the
  motion carried through the cut, or a match cut on the same shape or movement).
- "link": one short line saying how this scene connects to the previous one (e.g. "continues her motion: the hand
  that closed the case now lifts the headphones", "match cut: the spinning ear cup becomes the spinning vinyl").
  Scene 1: "opening".
- "image_prompt": the scene's FIRST frame, exactly the start_state, frozen at the moment the action begins (not
  after it). A detailed shot description of its own (60-120 words): who and what is where in the frame (left /
  right / foreground / background), wardrobe and look, the set, the light, the lens and framing - never just a
  title or a summary of the story.
- "props": every object and every person / animal visible in the first frame, each with its count and look (e.g.
  ["1 woman in a black blazer and coral blouse", "1 amber glass dropper bottle, cap on", "1 white sink"]). The image
  is drawn with EXACTLY these and nothing else, so list everything that must be there and nothing that is not there
  yet (no plate before the scene that plates, no second bottle, no extra animals).
- "change": how much the picture changes between the first and the last frame: "small" (expression, a hand
  gesture, a slight pan), "medium" (someone turns, stands, enters; the product opens), "large" (a new position or
  pose, a big camera move, wide to close-up). Prefer small and medium: the clip model travels from the first to
  the last frame, and a large jump between them makes things morph. A large change is better split in two scenes.
- Filmable writing (adapted from ViMax, MIT): describe only what a camera can see. Concrete actions instead of
  emotion words ("she bites her lip, her hands trembling", not "she feels nervous"); no metaphors. start_state and
  end_state are pure snapshots of one instant ("he is about to stand up" is not a snapshot). In "action" refer to
  people by visible features (e.g. "the woman in the green coat"), not only by name. Re-state key objects and
  people in every scene; repetition is fine. Say where things are in the frame (left / right / foreground) and
  which way people face, and keep left/right consistent between scenes."""


def voice_rules(clip_audio: bool) -> str:
    """One voice per scene: a narrator (spoken over the clip) or a person speaking on camera (the clip's own audio),
    never both, so two voices never talk over each other. The planner picks what suits the film."""
    on_cam = ('"on_camera" = a person in the shot speaks the line, lip-synced, in the clip\'s own audio: for dialogue, '
              'interviews, testimonials, a presenter talking to camera. ') if clip_audio else ""
    options = '"narration" | "on_camera" | "none"' if clip_audio else '"narration" | "none"'
    return f"""

VOICE (add these fields to EVERY scene object, overriding any rule above about them):
- "voice": {options}. Decide for the whole film first, from its format and the brief: "narration" = an off-screen
  voice-over (ads, product films, explainers, travel, documentaries, stories told by a narrator). {on_cam}"none" =
  no voice (music videos, pure mood or visual films, or the brief asks for no voice). Use the same choice across the
  film unless the story clearly needs a change (e.g. a narrated ad that ends on one spoken line from the presenter).
- "script": the voice-over line for a "narration" scene: 1-2 sentences, in the brief's language, short enough to
  speak comfortably in the scene's duration (about 2.5 words per second). "" for "on_camera" and "none" scenes.
- "sound": the clip's own audio: ambience and sound effects. For an "on_camera" scene also the spoken line, in
  quotes, with the speaker and language. For "narration" and "none" scenes NO speech, voices or dialogue at all.
  Never music.
- Never both in one scene: a "narration" scene has no on-camera speech, an "on_camera" scene has no script."""
