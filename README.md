# Palette-Video

A Palette app that understands a video request (text, images, video, audio), works out which skills it needs, picks the
right studio app or chain of apps, validates the plan, and runs it after you approve. Built with `@palettelab/cli` and
`@palettelab/sdk`.

```bash
npm install
npx @palettelab/cli dev                      # local simulator: app http://localhost:7321
npx @palettelab/cli test                     # contract checks (manifest, bundle, imports, permissions, migrations)
npx @palettelab/cli package                  # dist/palette-video-<version>.tar.gz
npx @palettelab/cli dev --sandbox --env staging   # real Palette OS preview (needs `pltt login`)
.venv/bin/python -m pytest backend/tests -q  # routing core tests (offline)
```

## Layout

| Path | What |
|---|---|
| `palette-plugin.json` | Manifest: database + migrations, background jobs, file uploads, `llm` + `storage` platform services |
| `backend/api/main.py` | Palette routes (all permission-gated) and the two durable jobs `palette_video.analyze` / `.execute` |
| `backend/api/models.py`, `backend/migrations/` | `palette_video__runs`, org-scoped with row-level security |
| `backend/api/router_core/` | The routing brain (copied from the Video Router; no link back to that folder) |
| `backend/api/router_core/registry/` | One JSON profile per studio app + the capability vocabulary |
| `frontend/src/` | React UI on `@palettelab/sdk` (requests go through `usePlatform().apiFetch`) |

## Models

With `PALETTE_API_KEY` in `.env` (uploaded as an encrypted plugin secret on publish) the Palette LLM gateway is used,
with vision and speech for uploaded files. Without it the app uses the platform's own models through `ctx.llm`
(text understanding only; under `pltt dev` that needs `OPENAI_API_KEY`).

## Editing your own footage

Inside VideoStudio an editing step on existing clips does only what really runs here: **join** clips in order,
**fit** them to a format (9:16, 16:9, ...), **mix in an uploaded audio track**, and **burn in captions** (Effects
Studio). Trimming, transitions, colour grading and quality enhancement are not done here: the registry marks them
unknown, so the router says "not possible" up front, and a step that still asks for them fails with a clear message
instead of a fake success (`backend/api/engines/adapter.py`).

## The apps it routes to

Five studio apps run for real as engines inside VideoStudio (registry: `backend/api/router_core/registry/apps/`):

| App | Plugin id | What it makes |
|---|---|---|
| Flow Video | `pltt-flow-video-2` | single AI clips and multi-scene films (3-120 s), consistency from references, fashion |
| Storyboard & Video | `pltt-storyboard-video-maker` | 20-60 s multi-scene stories and ads, narration + music |
| Motion Studio | `palette-motion-studio` | kinetic type, charts, diagrams, graphics over footage (HyperFrames, local) |
| Effects Studio | `palette-effects-studio` | creator-style effects and captions on a finished video (local) |
| Presenter Studio | `palette-presenter-studio` | lip-synced talking-to-camera videos (Kokoro + Ditto, local) |

`palette-video-smart` is listed but disabled (a mock: its renderer is not embedded). The router's logic tests use a
fixed fixture registry (`backend/tests/fixtures/registry_dummy`); `tests/test_real_registry.py` checks routing
against the real apps.

## Story Intelligence (any topic -> a directed film)

Before routing, `backend/api/engines/story_intel/` turns any input (a one-line idea, a script, a PDF/TXT document,
images) into a production plan the user can review, edit scene by scene, regenerate one scene of, then approve:

1. **Understand**: category (children's story, fiction, puzzle, educational, documentary, product, presenter, other),
   intent, audience, tone, style, research depth, length, and fiction vs fact.
2. **Research** (factual topics and uploaded documents): English Wikipedia (free, no key; set `RESEARCH_CONTACT` to
   put your contact in the User-Agent) -> facts with sources, timeline, conflicts. Fiction gets none.
3. **Story architect + scene director**: story shape per category, continuity bible (characters, world, objects),
   every scene with its event, actions (<= 3 beats, one place, one time of day), camera, light, sound, voice line,
   start/end state, object list and a full A-L video prompt.
4. **Validation**: rule checks (repeats, camera-only scenes, weak prompts, line length, timing, chronology, sources)
   plus an LLM review; puzzles are solved independently to check the answer; one revision when it fails.
5. **3D Motion Graphics Director** (education, finance, business, science, study and commercial explainers):
   HOOK -> REFRAME -> MECHANISM -> INSIGHT -> CLOSING TRUTH script (<= 14 words a line), a locked palette (three
   directions, switchable before the render), one art-directed keyframe and a timed motion prompt per beat; the
   exact on-screen words are added by the motion-design pass, never drawn by the image model. A request without a
   topic first offers five topic ideas to pick from.

Method credits: [cinematic-skill](https://github.com/Moh4696/cinematic-skill) (MIT, (c) 2026 exploraX): story
shapes, research rules and the narration lint. Its Skia renderer is not used; this app generates AI video clips.

## While and after it renders

- **AI checks**: every scene's first and last frame and every finished clip are checked by a vision model against the
  scene plan (extra or duplicated objects, wrong moment, wrong fit or scale, morphing, things appearing from nowhere,
  the product changing); a failed frame is redrawn and a failed clip retaken once, and the better one is kept.
- **Cancel** stops a run at its next save point (between steps, scenes and model calls); what was made is kept.
- **Re-render one scene** of a finished film: a new take (optionally with an instruction) is spliced over exactly
  that scene's time; narration, music and the other scenes stay. Earlier versions are kept in storage.
- **Usage**: each run shows its LLM calls and tokens and how many images, clips and voice lines were generated.

## Motion Studio (programmatic motion)

`palette-motion-studio` fills the programmatic gap: the Palette LLM writes a HyperFrames composition (HTML + a paused
GSAP timeline) and HyperFrames (HeyGen, Apache-2.0, pinned 0.9.7 in `tools/hyperframes`, `npm install` there) renders it
with headless Chrome + ffmpeg. Quality gates: `hyperframes check --json` (lint/runtime block; layout/contrast go back to
the LLM, up to 2 repairs), then a vision review of rendered frames with one repair and re-render. Charts with numbers use a
tested template (`engines/templates/bar_chart.py`: vertical or horizontal bars, labels, highlight tag, end card), so the
LLM only extracts the data. Kinetic text, charts and graphics over AI footage are confirmed; product turntables and
stylised exploded views are unverified. Renders always use full size (short side 1080 px): HyperFrames 0.9.7 drops small
text and misplaces bottom-anchored layers on smaller frames. Hosted Palette needs the runtime, Chrome and ffmpeg on the server.

## Story engine (Flow Video and Storyboard & Video)

Each film is planned as a story, not a list of shots:

1. **Genre playbook** (`backend/api/engines/genres.py`): the kind of video (ad, UGC, unboxing, exploded view,
   e-commerce, fashion, banner, action, animation, explainer, music video, documentary, travel, food, real estate,
   cinematic) is detected from the request; its arc gives weighted beats, signature shots, a default length when the
   user gives none (10-60 s) and the default voice.
2. **Planner**: the app's own planner prompt plus story-first, scene and voice rules. Every scene has a beat, a
   weight (1-5), timed physical action, camera, start/end snapshots, a link to the previous scene and one voice
   (narrator, on-camera speech, or none - never both). Scene lengths follow the weights.
3. **Story review**: an LLM director scores the plan (story, hook, action, continuity, weight, voice); below 75 the
   issues go back to the planner for one rewrite. The better plan is kept.
4. **Narration first**: narration lines are recorded (free local Kokoro voice; ElevenLabs on fal only with
   `PRESENTER_PAID=on` or a language Kokoro does not speak) before any video; scenes are lengthened
   to fit their line, and the timed beats are rescaled to the new length.
5. **Frames**: first frames are made in order with the previous scene's frame as a reference; scenes that change
   a lot also get a last frame, and the clip model (Kling 3.0 Pro / Seedance) animates from first to last frame.

### Credits

Techniques adapted (ideas and short rules, rewritten; no code copied) from MIT-licensed projects:
[ViMax](https://github.com/HKUDS/ViMax) (first/last frames by amount of change, filmable writing rules),
[VideoAgent](https://github.com/HKUDS/VideoAgent) (narration first, picture timed to it),
[VidForge](https://github.com/WANGLEVY9/VidForge) (scored review with feedback into a rewrite).
[pavo-ai-agent](https://github.com/yanzhao77/pavo-ai-agent) has no licence file, so only its general idea (a
reviewer before rendering) informed the design; nothing from it is used.
[OpenMontage](https://github.com/OpenMontageInc/OpenMontage) (AGPL-3.0) director guides are read at runtime, unmodified,
from a separate checkout (`OPENMONTAGE_GUIDES_DIR`) and given to the planner as reference; nothing is copied in.

## Presenter Studio (lip-synced talking videos)

`palette-presenter-studio` (`backend/api/engines/presenter_engine.py`) makes talking-to-camera videos: YouTube
explainers, UGC ads, testimonials and spokesperson videos. Picked in Auto when the brief's production type is
`talking_presenter`, or directly with the **Presenter** mode.

Free by default: the voice (Kokoro) and the lip-sync (Ditto) run on this machine; only the presenter photo uses a paid
model (Nano Banana Pro on fal). Install the local models with `tools/local_lipsync/setup.sh` (~3.7 GB; details in
`tools/local_lipsync/README.md`). `PRESENTER_PAID=on` adds paid fal models (ElevenLabs; Kling AI Avatar v2, LongCat,
InfiniteTalk) as fallbacks after the local ones.

1. **Script**: the LLM writes what the presenter says (~2.5 words/s for the requested length) in the format's style.
2. **Presenter**: the user's photo of the person; otherwise a fictional person is generated (never a real or named
   person). With a product photo the presenter is generated holding the product.
3. **Voice**: the user's uploaded recording, or the script spoken by Kokoro (no Telugu: upload a recording).
4. **Lip-sync**: speech is cut at pauses into parts of at most 27 s; each part is lip-synced from the same photo and
   the parts are joined as creator-style jump cuts (12 % punch-in on every other part), each with its own audio.
5. Effects Studio adds the format's effects (ugc_ad / explainer styles) unless a later step does.

Settings: `LIPSYNC_MODELS` (comma list of `ditto_local`, `longcat`, `infinitalk`, `kling_avatar`, `kling_avatar_pro`)
overrides the order; `PRESENTER_VOICE_FEMALE` / `PRESENTER_VOICE_MALE` set the ElevenLabs voices (default Rachel / Brian). Test
quality: 480p, up to 15 s. Full: 720p, up to 180 s.
