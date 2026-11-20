"""Background engines for Flow Video and Storyboard & Video: the apps' own planners and models, run inside Palette-Video.

Pipeline (the same order both apps follow): plan scenes with the app's own planner prompt -> one still per scene (with
the user's reference images) -> animate each still into a clip -> join the clips with ffmpeg. Nothing is shown in those
apps; every artefact comes back to Palette-Video. Model calls use fal (FAL_KEY); planning uses the Palette LLM gateway.

QUALITY "test" (default) keeps runs cheap: Seedance 2.0 Fast at 480p, at most 2 short scenes, no audio.
QUALITY "full" uses the app's defaults (Kling 3.0 Pro / Nano Banana Pro, 720p, native sound) and the full length.
"""

import asyncio
import contextvars
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import httpx

from router_core import config, llm

from . import approaches, effects_engine, finishing, flow_budget, food, genres, motion_design, music, planners, temporal
from .vendor.flow import errors as _errors, fal_helper, model_skills, video_merge

log = logging.getLogger("palette_video.engines")

# Clip models (all on fal). Kling is always Kling 4.0; every clip falls back down its chain when a model fails.
QUALITY = {
    "test": {"video_model": "seedance_2_0", "resolution": "480p", "image_model": "nano_banana_pro", "audio": False, "voice": True,
             "max_scenes": 2, "max_total": 10, "fallbacks": ["kling_4_0", "kling_3_0_standard"]},
    "full": {"video_model": "kling_4_0", "resolution": "720p", "image_model": "nano_banana_pro", "audio": True, "voice": True,
             "max_scenes": 20, "max_total": 120, "fallbacks": ["seedance_2_0", "kling_3_0_pro"]},
}
# 3D motion graphics on the AI video model: Seedance 2.0 films the keyframes, Kling 4.0 then Kling 3.0 Pro back it up.
MOTION_3D_MODELS = {"video_model": "seedance_2_0", "fallbacks": ["kling_4_0", "kling_3_0_pro"]}
# A provider refusing the input (e.g. Seedance on photoreal people) is retried on the next model, as the apps do.
REFUSAL_MARKERS = ("content_policy", "likeness", "partner_validation", "safety", "nsfw", "refus")
# Per-clip seconds each clip model accepts (from the apps' catalogs; Kling 4.0 takes 3-30 s, kept to 15 per clip).
CLIP_RANGE = {"seedance_2_0": (4, 15), "kling_4_0": (3, 15), "kling_3_0_pro": (3, 15), "kling_3_0_standard": (3, 15)}
# Voice-over: each scene's planner "script" line, spoken through fal (same FAL_KEY as the image/video models).
TTS_MODEL = os.getenv("TTS_MODEL", "fal-ai/elevenlabs/tts/multilingual-v2")
TTS_VOICE = os.getenv("TTS_VOICE", "Rachel")
ENGINE_APPS = {"pltt-flow-video-2", "pltt-storyboard-video-maker"}


class EngineError(RuntimeError):
    pass


# 3D motion graphics engines, picked per run in the plan (story_intel["motion_engine"]):
#   "opus_llm"     no video model: the Opus LLM plans each keyframe's motion and code renders it (engines/motion_code.py)
#   "video_model"  the AI clip models film every keyframe
# The AI video model (fal: Seedance 2.0, then Kling 4.0) is the default; MOTION_VIDEO_MODEL=off makes code animation
# the default instead. Either way it only changes the default for runs that did not pick one.
MOTION_ENGINES = {"opus_llm": "Opus LLM (code animation, no video model)", "video_model": "AI video model"}
MOTION_VIDEO_MODEL = os.getenv("MOTION_VIDEO_MODEL", "on").strip().lower()
OPUS_MOTION_NAME = "Opus LLM Motion (no video model)"  # what the user sees instead of "Storyboard & Video"


def default_motion_engine() -> str:
    return "opus_llm" if MOTION_VIDEO_MODEL in ("off", "0", "false", "no", "") else "video_model"


def code_motion(story_mode: str | None, engine: str | None = None) -> bool:
    if story_mode != "motion_graphics":
        return False
    return (engine if engine in MOTION_ENGINES else default_motion_engine()) == "opus_llm"


def quality(story_mode: str | None = None, engine: str | None = None) -> dict:
    """The run's quality preset; 3D motion graphics filmed by the AI video model use the 3D clip chain."""
    q = QUALITY.get(os.getenv("PALETTE_VIDEO_QUALITY", "test"), QUALITY["test"])
    if story_mode == "motion_graphics" and not code_motion(story_mode, engine):
        q = {**q, **MOTION_3D_MODELS}
    return q


def clip_models(q: dict) -> list[str]:
    """The clip model, then its fallbacks (no repeats). With photoreal people (`_no_seedance`) Seedance goes last: it
    refuses frames that may show a real person, so every clip was paid for twice (Seedance, then Kling)."""
    models = list(dict.fromkeys([q["video_model"], *q.get("fallbacks", [])]))
    if q.get("_no_seedance"):
        models = [m for m in models if not m.startswith("seedance")] + [m for m in models if m.startswith("seedance")]
    return models


_STYLISED = re.compile(r"anim|cartoon|pixar|disney|\b3d\b|cgi|clay|anime|manga|illustrat|styli[sz]ed|toon|painted|"
                       r"watercolou?r|sketch|comic|low[- ]poly|vector|chibi|storybook", re.I)
_HUMAN = re.compile(r"\b(male|female|man|woman|boy|girl|person|\d{1,2}[- ]?(year|yr)s?[- ]old|aged? \d{1,2})\b", re.I)


def photoreal_people(p: dict, cast: list[dict]) -> bool:
    """A film whose drawn characters are realistic-looking people (Seedance refuses those frames as real people)."""
    if _STYLISED.search(art_style(p)) or _STYLISED.search(str(p.get("master_prompt") or "")):
        return False
    return any(c.get("photo") or _HUMAN.search(str(c.get("desc") or "")) for c in cast)


def clip_resolution(model: str, q: dict) -> str | None:
    """The resolution field for a clip model: Seedance takes the quality's; Kling 4.0 only 720p or 1080p (its
    endpoint defaults to 1080p, the dearer one); the other Kling endpoints have no such field."""
    if model.startswith("seedance"):
        return q["resolution"]
    if model == "kling_4_0":
        return "1080p" if q["resolution"] in ("1080p", "4k") else "720p"
    return None


def fal_key() -> str:
    key = os.getenv("FAL_KEY", "")
    if not key:
        from . import router_images
        if router_images.available():  # images still work: the LLM Router paints them (see _fal)
            return ""
        raise EngineError("FAL_KEY is not set, so the video models cannot be called.")
    return key


# Paid model calls of the current job (images, clips, voice), counted for the usage panel. Set by the job.
fal_usage: contextvars.ContextVar[list | None] = contextvars.ContextVar("fal_usage", default=None)


def _count(kind: str, model: str = "") -> None:
    log_ = fal_usage.get()
    if log_ is not None:
        log_.append({"kind": kind, "model": model})


# fal out of credit or its key refused: images go straight to the LLM Router for a while instead of failing first.
FAL_DOWN_S = 600
_fal_down_until = 0.0
_FAL_DEAD = re.compile(r"exhausted balance|user is locked|invalid key|not authori[sz]ed|unauthori[sz]ed|forbidden|"
                       r"\b40[13]\b", re.I)


def _fal_is_down() -> bool:
    import time
    return time.time() < _fal_down_until


def _mark_fal_down(e: Exception) -> None:
    global _fal_down_until
    import time
    if _FAL_DEAD.search(str(e)):
        if not _fal_is_down():
            log.warning("fal is refusing this key (%s); images go through the LLM Router for %s s", str(e)[:160],
                        FAL_DOWN_S)
        _fal_down_until = time.time() + FAL_DOWN_S


async def _router_image(args: tuple, kw: dict) -> dict:
    """The same picture through the LLM Router, shaped like a fal result ({"images": [{"url"}]})."""
    from . import router_images
    prompt = args[2] if len(args) > 2 else kw.get("prompt", "")
    refs = list(kw.get("image_urls") or ([kw["image_url"]] if kw.get("image_url") else []))
    url = await router_images.generate(prompt, kw.get("aspect_ratio") or "16:9", refs)
    _count("image", "llm-router")
    return {"images": [{"url": url}]}


async def _fal(fn, *args, **kw):
    """The vendored helpers are blocking (fal_client.subscribe): run them in a thread. An image the fal call cannot
    make (no credit, a refused key, a provider error) is made by the LLM Router instead, when its key is set."""
    from . import router_images
    kind = {"generate_image": "image", "generate_video": "clip"}.get(getattr(fn, "__name__", ""), "other")
    if kind == "image" and router_images.available():
        if _fal_is_down() or not (args and args[0]):
            return await _router_image(args, kw)
        try:
            _count(kind, str(args[1]) if len(args) > 1 else "")
            return await asyncio.to_thread(fn, *args, **kw)
        except Exception as e:
            _mark_fal_down(e)
            log.warning("fal image failed (%s); trying the LLM Router", str(e)[:200])
            try:
                return await _router_image(args, kw)
            except router_images.RouterImageError as e2:
                raise EngineError(f"The image failed on fal ({str(e)[:160]}) and on the LLM Router ({e2})") from e
    _count(kind, str(args[1]) if len(args) > 1 else "")
    return await asyncio.to_thread(fn, *args, **kw)


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


async def _upload(path: str) -> str:
    """A local file as a URL the models can fetch (fal storage). When fal refuses and the LLM Router is set, a
    picture becomes an inline data: URL instead (the router takes base64 images)."""
    from . import router_images
    if not (router_images.available() and Path(path).suffix.lower() in IMAGE_SUFFIXES):
        return await asyncio.to_thread(fal_helper.client(fal_key()).upload_file, path)
    if not _fal_is_down() and os.getenv("FAL_KEY"):
        try:
            return await asyncio.to_thread(fal_helper.client(os.getenv("FAL_KEY")).upload_file, path)
        except Exception as e:
            _mark_fal_down(e)
            log.warning("fal upload of %s failed (%s); sending it inline to the LLM Router", Path(path).name, e)
    return await asyncio.to_thread(router_images.data_url, Path(path))


async def _download(url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in r.aiter_bytes():
                    fh.write(chunk)
    return str(dest)


async def archive_refs(photos: list[dict], scenes: list[dict], work: Path) -> dict[str, str]:
    """The archive photos the scenes pin, fetched with the research User-Agent (Wikimedia's robot policy) and
    uploaded for the image model: {photo id: url}. A photo that cannot be fetched is simply left out."""
    from .story_intel import research
    used = {str(a) for s in scenes for a in (s.get("archive") or [])}
    todo = [p for p in photos if p.get("id") in used and p.get("url")]

    async def one(p: dict) -> tuple[str, str] | None:
        dest = work / "archive" / f"{p['id']}{Path(p['url'].split('?')[0]).suffix or '.jpg'}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=research._ua()) as c:
                r = await c.get(p["url"])
                r.raise_for_status()
                dest.write_bytes(r.content)
            return p["id"], await _upload(str(dest))
        except Exception as e:
            log.warning("archive photo %s failed: %s", p.get("id"), e)
            return None

    return dict(x for x in await asyncio.gather(*(one(p) for p in todo)) if x)


def _archive_note(photos: list[dict], first: int) -> str:
    if not photos:
        return ""
    bits = [f"reference image {first + k} is a REAL ARCHIVE PHOTO ({p['what'][:140]})" for k, p in enumerate(photos)]
    return ("\nARCHIVE: " + "; ".join(bits) + ". Show each one as a physical print in this world, its picture "
            "reproduced EXACTLY as it is (same people, faces, composition, tones and grain) - never redrawn, "
            "recoloured, cropped into something else or given new faces.")


def _url(result, kind: str) -> str:
    if kind == "image":
        imgs = (result or {}).get("images") or []
        if imgs and imgs[0].get("url"):
            return imgs[0]["url"]
    else:
        v = (result or {}).get("video") or {}
        if v.get("url"):
            return v["url"]
    raise EngineError(f"The {kind} model returned no {kind}: {json.dumps(result, default=str)[:300]}")


def written_plan(story: dict, q: dict) -> dict:
    """The user's own scenes (engines/as_written.py) as the film plan, word for word: no planner, no critic, no
    rewrite. Each scene's text is its image prompt; its motion text (or the same text) is the clip prompt; its
    narration line is spoken. Durations as written, else as planned when the scenes were read."""
    scenes = []
    for st in ((story.get("plan") or {}).get("scenes") or [])[:q["max_scenes"]]:
        a = st.get("audio") if isinstance(st.get("audio"), dict) else {}
        line = str(a.get("line") or "").strip() if a.get("type") != "none" else ""
        image = str(st.get("what_happens") or "").strip()  # the scene text (or the user's own edit of it)
        motion = str(st.get("actions") or "").strip() or image
        scenes.append({"title": st.get("title") or f"Scene {len(scenes) + 1}", "image_prompt": image,
                       "motion_verbatim": motion, "script": line, "voice": "narration" if line else "none",
                       "duration": max(1, int(round(float(st.get("duration_s") or 6)))), "as_written": True})
    if not scenes:
        raise EngineError("There are no scenes to make.")
    total = sum(x["duration"] for x in scenes)
    return {"title": (story.get("plan") or {}).get("title") or "Your scenes", "story": "", "bible": {},
            "continuity": {}, "master_prompt": "", "scenes": scenes, "total": total, "requested_total": total,
            "music": "", "recipe": None, "n_max": len(scenes), "genre": "as_written", "genre_label": "Your scenes",
            "method": None, "edit_direction": None, "as_written": True}


async def plan(app_id: str, handoff: dict, refs: list[dict], q: dict, feedback: list[str] | None = None) -> dict:
    """The app's own planner, fitted to the run's length and clip model."""
    req = handoff.get("request") or {}
    genre = genres.detect(handoff.get("prompt") or "", req.get("production_type"))
    # Food: understand the recipe first (once per run, reused by the rewrite and every prompt after it).
    recipe = handoff.get("_recipe")
    if recipe is None and food.recipe.is_candidate(handoff.get("prompt") or "", genre):
        try:
            recipe = await food.recipe.understand(handoff.get("prompt") or "", refs)
        except Exception as e:  # the film is still planned the general way
            log.warning("recipe understanding failed: %s", e)
            recipe = {}
        handoff["_recipe"] = recipe
    cooking = bool(recipe and recipe.get("is_cooking_process") and recipe.get("workflow"))
    if cooking:
        genre = "food"
    elif genres.faceless(genre, handoff.get("prompt") or "", refs):
        genre = "product_film"  # no person: the product film playbook, music-led, ingredient-world sets
    story = None if cooking else handoff.get("_story")  # the approved Story Intelligence plan, scene by scene
    story_scenes = ((story or {}).get("plan") or {}).get("scenes") or []
    asked = req.get("total_duration") or req.get("duration")
    given = req.get("duration_given", True) and asked is not None  # older plans always carried the user's length
    story_len = sum(float(s.get("duration_s") or 0) for s in story_scenes)
    want = int(float(asked) if given
               else (story_len or (45 if cooking else genres.default_seconds(genre))))  # cooking needs ~45 s
    if not given and app_id == "pltt-storyboard-video-maker":
        want = max(20, min(60, want))  # the app's planned range
    total = min(want, q["max_total"])
    lo, hi = CLIP_RANGE[q["video_model"]]
    if app_id == "pltt-storyboard-video-maker":
        lo, hi = max(lo, planners.SB_SCENE_MIN), min(hi, planners.SB_SCENE_MAX)
    n_max = max(1, min(q["max_scenes"], total // lo))
    if story_scenes:  # every approved scene is rendered (as far as the quality's scene cap allows)
        n_max = max(1, min(q["max_scenes"], len(story_scenes)))
    method = None if cooking else (handoff.get("_method") or approaches.pick(genre, handoff.get("prompt") or ""))
    handoff["_method"] = method  # a rewrite after the review keeps the same method
    system, user = planners.prompt_for(app_id, handoff["prompt"], total, req.get("aspect_ratio") or "16:9",
                                       lo, hi, n_max, refs, q, genre, method, recipe if cooking else None,
                                       approved=story if story_scenes else None)
    if feedback:
        user += ("\n\nA REVIEW OF YOUR PREVIOUS PLAN FOUND THESE PROBLEMS. Write a new plan that fixes every one:\n- "
                 + "\n- ".join(feedback))
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    data, scenes = {}, []
    for attempt in range(2):  # one more try when the plan is empty or skips the story fields
        # Long films need room: a 60 s plan with every scene field plus the model's reasoning passes 6000 tokens.
        data = await llm.chat_json(config.REASONING_MODEL, msgs, max_tokens=24000, stage=f"engine.plan.{app_id}")
        scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and str(s.get("image_prompt") or "").strip()]
        missing = _missing_story_fields(scenes)
        if story_scenes and scenes and len(scenes) < min(n_max, len(story_scenes)):
            # the approved story is rendered whole: a planner that merged or dropped scenes is asked again
            missing = (f"{min(n_max, len(story_scenes))} approved scenes but only {len(scenes)} came back - one "
                       "scene object per approved scene, in order, none merged or dropped")
        if scenes and not missing:
            break
        if attempt == 0:
            log.warning("planner %s: %s; asking again", app_id, missing or "no scenes")
            msgs = msgs + [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)[:20000]},
                           {"role": "user", "content": (
                               "The plan is incomplete: " + (missing or "it has no usable scenes") + ". Return the "
                               "COMPLETE JSON again. Every scene object needs: title, duration, beat, weight, action "
                               "(timed beats of what physically happens), camera, start_state, end_state, link, "
                               "image_prompt, props, sound, voice and script, exactly as the SCENES and VOICE rules say.")}]
    if not scenes:
        raise EngineError("The planner returned no usable scenes.")
    scenes = [_flat(s) for s in scenes[:n_max]]
    dropped = max(0, len(story_scenes) - len(scenes)) if story_scenes else 0
    if dropped:  # said, never silent: the quality's scene limit cut the approved story
        log.warning("%s approved scenes do not fit the %s-scene limit: the last %s are not rendered",
                    len(story_scenes), n_max, dropped)
    seq = (story or {}).get("temporal") or {}
    for n, (s, st) in enumerate(zip(scenes, story_scenes), start=1):
        _sequence_fields(s, seq, n)
        if st.get("environment") and not s.get("environment"):  # where it happens: decides the frame handoff
            s["environment"] = str(st["environment"])[:300]
    for s, st in zip(scenes, story_scenes):  # words drawn INTO a frame (older 3D plans), so the AI checks spell them
        if st.get("in_scene_text"):
            s["in_scene_text"] = st["in_scene_text"]
        for k in ("hero", "items", "hit_word", "archive"):  # a cinematic documentary's scene: what the checks look for
            if st.get(k) and not s.get(k):
                s[k] = st[k]
        if st.get("host"):  # a documentary's on-camera host: their own lip-synced clip, their own voice
            a = st.get("audio") if isinstance(st.get("audio"), dict) else {}
            s["host"], s["voice"] = True, "on_camera"
            s["host_line"] = str(a.get("line") or s.get("script") or "").strip()
        a = st.get("audio") if isinstance(st.get("audio"), dict) else {}
        if (story or {}).get("mode") == "short_drama" and a.get("type") == "dialogue" and str(a.get("line") or "").strip():
            # a short drama's spoken line: the speaker on camera, their own voice lip-synced onto the clip
            s["voice"], s["speaker"], s["line"] = "on_camera", str(a.get("speaker") or "").strip(), str(a["line"]).strip()
            s["characters"] = f"{s['speaker']} {s.get('characters') or ''}".strip()
            s["script"] = ""
    durations = weighted_durations(scenes, total, lo, hi)
    for s, d in zip(scenes, durations):
        retime(s, d)
    return {"title": data.get("title") or "", "story": data.get("story") or "", "bible": data.get("bible") or {},
            "continuity": data.get("continuity") or {}, "master_prompt": str(data.get("master_prompt") or ""),
            "scenes": scenes, "total": sum(durations), "requested_total": want,
            "dropped_scenes": dropped,
            "music": str(data.get("music") or "")[:300],
            "recipe": recipe if cooking else None, "n_max": n_max,
            "genre": genre, "genre_label": genres.GENRES[genre]["label"],
            "method": {k: method[k] for k in ("id", "label", "family")} if method else None,
            "edit_direction": motion_design.clean_direction(data.get("edit_direction"), genre, len(scenes))}


# Effects Studio in the engine itself (not only as a Hybrid step): which kinds of effects suit each genre.
EFFECT_STYLE = {  # the job per genre; the effects themselves come from each film's own random set (effects_engine)
    "advertisement": "This is an ad (product treatment): cinematic, premium effects - a strong opening reveal, the "
                     "product's name or one big benefit as a big word in the brand colour, one hero-moment effect, "
                     "one grade or look - 2-4 effects, elegant, never cheap. The graphics pass adds callouts later, so "
                     "keep the product's key shots clean.",
    "ecommerce": "A product video (product treatment): the key benefit as a big word, a card or list for features, "
                 "a number effect only for a real number from the brief, a before/after if there is one - 2-4 effects.",
    "unboxing": "An unboxing: a reveal on the first look, a punch-in on the reaction, the product's name as a label, "
                "one look - 2-3 effects.",
    "exploded_view": "A product engineering film (exploded-view treatment): the parts and the graphics' part labels "
                     "are the show - at most 2 effects: a camera move on the key part, its name as a big word. Never "
                     "cover the moment the parts separate or reassemble; no face effects, no cards over the parts.",
    "food": "A cooking film (process treatment): the hands and the food are the show - at most 2-3 calm effects: a "
            "slow push on the hero moment (the cut, the pour, the first bite), the dish name as one big word, one warm "
            "look. Never cover the food while an action happens; no cards, checklists or social widgets.",
    "product_film": "A product film with NO person (product treatment): only effects that work on a product and "
                    "never need a face - an opening reveal or a slow camera push, one big word behind the product "
                    "for its name or key benefit, one look / grade - 1-3 effects, premium and calm. No cards, "
                    "checklists, messages or social widgets.",
    "ugc_ad": "A UGC creator ad (speech-led treatment): punchy creator-style effects - a hook effect in the first 2 s, "
              "a punch-in on the key word, the product name as a word or label, social proof or a list as a card, a "
              "call to action at the end - 3-5 effects.",
    "explainer": "An explainer (teaching treatment, device or product): cards or lists for the steps and parts, a "
                 "freeze or pointer on the key part, a number effect only for a real number, a myth-vs-fact effect "
                 "when there is one - 3-4 effects, each tied to what the narration says at that moment.",
}

STORY_FIELDS = ("beat", "action", "weight", "voice")
TEXT_FIELDS = ("beat", "action", "camera", "start_state", "end_state", "link", "image_prompt", "sound", "script", "title")


_SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*[-\u2013\u2014]\s*(\d+(?:\.\d+)?)\s*s\b")


def retime(s: dict, new: int) -> None:
    """Set a scene's duration and stretch the timed beats in its action ("2-4 s: ...") to the new length, so the
    beats always cover exactly the scene. The planner timed them for its own duration (or for 0..last beat)."""
    spans = [(float(a), float(b)) for a, b in _SPAN.findall(str(s.get("action") or ""))]
    old = max([b for _, b in spans], default=0.0) or float(s.get("duration") or new)
    s["duration"] = new
    if not spans or abs(old - new) < 0.05:
        return
    k = new / old
    fmt = lambda x: f"{x:.1f}".rstrip("0").rstrip(".")
    s["action"] = _SPAN.sub(lambda m: f"{fmt(float(m.group(1)) * k)}-{fmt(float(m.group(2)) * k)} s", s["action"])


def _flat(s: dict) -> dict:
    """Planner text fields sometimes come back as lists of beats: join them into one line."""
    for k in TEXT_FIELDS:
        if isinstance(s.get(k), list):
            s[k] = " ".join(str(x).strip() for x in s[k] if str(x).strip())
    return s


CRITIC_SYSTEM = """You are a demanding film director reviewing a scene plan for an AI-generated video BEFORE any
money is spent on rendering. Score it 0-100 and list concrete problems. Check, in this order:
1. Story: is there a real story with a character, a change and a payoff? Does every scene move it forward with a
   new event, or are some scenes just beauty shots, light sweeps, rotations or camera moves on a still subject?
2. Hook: does the first scene grab attention in its first 2-3 seconds?
3. Action: is every "action" filmable, physical and different between first and last frame (no emotion words)?
4. Continuity: does each scene's start_state follow from the previous scene's end_state? Same subject, wardrobe,
   props, light, left/right?
5. Weight: do the biggest moments get the most time (weight and duration)?
6. Voice: does each narration "script" fit its scene (about 2.5 words per second) and match what is on screen? Is
   an on-camera scene free of narration and a narration scene free of on-camera speech?
7. Clip load: every scene is ONE AI clip that can perform one main event. More than 3 timed beats, beats under
   1.5 s, or two events in one scene (start the race AND fall asleep; plate AND take the first bite) -> split it.
8. Frames: is every image_prompt a detailed first frame (who / what / where, look, set, light) and does "props"
   list every object with its count, so nothing extra (a second plate, another bottle, an extra animal) is drawn?
9. The brief and the genre playbook: is everything the user asked for there?
Return ONLY JSON: {"score": 0-100, "issues": ["scene N: problem -> how to fix", ...], "strengths": ["..."]}.
Score below 75 means the plan must be rewritten. Be specific; no generic advice.
(Scored review with feedback into a rewrite, adapted from VidForge's quality agent and pavo-ai-agent's reviewer.)"""
CRITIC_PASS = 75


CONSISTENCY = """The first {K} image(s) are the film's character references: for each character a passport-style
face portrait and a full-body front view. The LAST image is a frame from the film.
For every character from the references who appears in the frame, list what a viewer would notice is wrong:
- the face: missing, blank, smeared, melted, half-drawn, cut off by the frame edge when the character is the subject,
  hidden without reason, extra or missing eyes / nose / mouth, or a DIFFERENT face (shape, eyes, nose, mouth,
  eyebrows, skin tone, apparent age) from the portrait;
- the hair: missing, bald or cut off when the references have hair, or a different colour, length, style or hairline;
- body and wardrobe: different build, garments (colour, cut, length, hood, zips), footwear or accessories; an animal's
  size, colour or markings;
- a character duplicated, or a referenced character merged with someone else.
Ignore pose, expression, framing, lighting and background. Story bible: {BIBLE}
{SEVERITY}
Return JSON {"same": true|false, "differences": ["MAJOR: the girl's face is blank -> draw her full face as in the
portrait", "MINOR: her bracelet is missing", ...]}."""


IDENTITY = """Image 1 is a photo of a real person the user uploaded: the person who must appear in the film.
Image 2 is a frame from the film. If NO person appears in image 2, return {"same": true, "differences": []}.
Otherwise the main person in image 2 must look EXACTLY like image 1, as if photographed the same day: same face
(shape, eyes, nose, mouth, eyebrows), apparent age, skin tone, hair colour, hair length and hairstyle, and the same
outfit (garments, colours, cut) and accessories as far as they are visible. List every difference a viewer would
notice. Ignore only pose, expression, framing, lighting and background.
Return JSON {"same": true|false, "differences": ["looks about 15 years older than the photo", "rounder face", ...]}."""

SCENE_QA = """You check one AI-generated frame of a film BEFORE it is animated. Image 1 is the frame to check.{PREV}
It must be the {WHICH} frame of this scene:
{SPEC}
List only what a viewer would notice is WRONG in image 1:
- extra or duplicated objects: a second plate, bowl, pot, lid, cup, bottle, dropper, cap, knife or utensil that the
  scene does not have; the same object twice; objects that appear from nowhere;
- missing key objects, or a key object that changed (another vessel, plate, bottle, colour or size){PREVCHK};
- the wrong moment: the {WHICH} frame must show {MOMENT}, e.g. not the finished result in a first frame, not the
  raw state in a last frame; a step that has not happened yet already done (food plated, eaten, cut before the
  scene cuts it);
- food or objects in an impossible or wrong state (cut / cooked / opened when they should not be, or the reverse);
- wrong fit or scale: a lid smaller or larger than its pot or lying on the food instead of on the rim, a seal (dough,
  foil) that is not on the seam between lid and rim, a shallow pan where the scene needs a deep pot, food
  overflowing its vessel, a utensil or object the wrong size for the hands;
- people, hands and animals: a wrong count (extra hands or arms, three hands, a duplicate animal), extra or fused
  fingers, a body that changed;
- a collage or split image, text, labels or watermarks that were not asked for; when the scene names 3D words that
  must appear: a missing word, a misspelled or garbled letter, extra letters or words.
Ignore small differences in framing, light and background details. Do not invent problems.{SEVERITY}
Return ONLY JSON {"ok": true|false, "issues": ["MAJOR: two plates on the counter but the scene has none yet -> remove
the plates", ...]} with each issue as "MAJOR|MINOR: what is wrong -> how to fix it"."""
QA_MAX_ISSUES = 6

# Every check tags its issues: only a MAJOR one costs a redraw or a new take. On one film, 38 of 73 images and most of
# the clip retakes went to coin counts, a bracelet or which hand holds a glass, while the face drift stayed.
SEVERITY = ("\nStart every issue with MAJOR: or MINOR:. MAJOR = a viewer notices it at once and it breaks the film: "
            "another face or person, different hair, a different outfit or garment, a blank, smeared or cut-off face, a "
            "missing or extra person, limb or animal, a different place from the location plate, the wrong moment of the "
            "action, a collage or split image, "
            "required text garbled, something morphing. MINOR = everything else: how many small props there are "
            "(coins, cups, stones), which hand holds something, a small accessory or jewellery, a slightly different "
            "shade, a detail too small to see at this size.")
_SEV_TAG = re.compile(r"^\W*(major|minor)\W*\s*[:\-\u2013\u2014]\s*", re.I)


def tag_issue(text: str, default_major: bool | None = None) -> str:
    """An issue as "MAJOR: ..." or "MINOR: ...". Untagged: `default_major`, or (None) a face / hair / outfit word."""
    text = str(text).strip()
    m = _SEV_TAG.match(text)
    if m:
        return f"{m.group(1).upper()}: {text[m.end():].strip()}"
    major = bool(MAJOR_DRIFT.search(text)) if default_major is None else default_major
    return f"{'MAJOR' if major else 'MINOR'}: {text}"


def is_major(issue: str) -> bool:
    m = _SEV_TAG.match(str(issue))
    return m.group(1).lower() == "major" if m else bool(MAJOR_DRIFT.search(str(issue)))


def plain(issue: str) -> str:
    """The issue without its MAJOR / MINOR tag (for progress lines)."""
    return _SEV_TAG.sub("", str(issue), count=1)


def severity_score(issues: list[str]) -> tuple[int, int]:
    """(major, total): fewer MAJOR problems always wins."""
    return sum(1 for x in issues if is_major(x)), len(issues)


def _qa_spec(s: dict, which: str) -> str:
    """The scene as the checker needs it: what it shows, which objects are in it and what must already be true."""
    keys = (("title", "scene"), ("beat", "purpose"), ("action", "action"),
            ("start_state" if which == "first" else "end_state", "this frame shows"),
            ("props", "objects in the frame (with counts)" if which == "first"
             else "objects at the START of the scene (the action may bring in or remove only what it says)"), ("ingredients", "ingredients"),
            ("utensils", "utensils and cookware"), ("hands", "hands"), ("continuity", "continuity"),
            ("time_jump", "time jump before it"), ("hero", "the hero the camera lands on (lit; the rest dimmed)"),
            ("items", "board items (a designed board of prints and documents is intended, not a collage error)"),
            ("in_scene_text", "3D words that MUST appear in the frame, spelled exactly (and no other text)"))
    out = []
    for k, label in keys:
        v = s.get(k)
        if isinstance(v, (list, tuple)):
            v = "; ".join(str(x) for x in v if str(x).strip())
        elif isinstance(v, dict):
            v = "; ".join(f"{a}: {b}" for a, b in v.items())
        if str(v or "").strip():
            out.append(f"- {label}: {str(v).strip()[:700]}")
    return "\n".join(out) or f"- {s.get('image_prompt', '')[:900]}"


async def _qa_json(parts: list[dict], stage: str, note: dict | None = None, tokens: int = 1500) -> dict | None:
    """One vision check, retried once. None when both tries fail: the caller then lets the frame through (a check
    never blocks the film), but the scene is marked qa_unchecked so the report shows it was NOT checked."""
    last = None
    for _ in range(2):
        try:
            return await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": parts}], max_tokens=tokens,
                                       reasoning_effort="low", stage=stage)
        except Exception as e:  # noqa: BLE001 - any failure of the safety net
            if type(e).__name__ == "RunCancelled":
                raise
            last = e
    log.warning("%s failed twice (%s); this frame is NOT checked", stage, str(last)[:200])
    if note is not None:
        note.setdefault("qa_unchecked", []).append(stage.rsplit(".", 1)[-1])
    return None


async def scene_issues(image_url: str, s: dict, which: str = "first", prev_url: str | None = None,
                       product_url: str | None = None) -> list[str]:
    """AI analysis of one scene frame against its own scene plan ([] = it matches). Catches what the image model
    adds or forgets: extra plates and bowls, a second bottle, the end result drawn in the first frame, extra hands."""
    prev = ("\nImage 2 is the frame just before it in the film (the previous frame of this scene or the previous "
            "scene's last frame): objects that continue must look the same.") if prev_url else ""
    text = (SCENE_QA.replace("{SEVERITY}", SEVERITY).replace("{PREV}", prev).replace("{WHICH}", which)
            .replace("{SPEC}", _qa_spec(s, which))
            .replace("{PREVCHK}", " compared with image 2" if prev_url else "")
            .replace("{MOMENT}", "the moment the action BEGINS (its start state)" if which == "first"
                     else "the moment the action is COMPLETE (its end state)"))
    parts = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image_url}}]
    if prev_url:
        parts.append({"type": "image_url", "image_url": {"url": prev_url}})
    if product_url:
        parts[0]["text"] += (f"\nImage {len(parts)} is the user's real PRODUCT: wherever it appears in image 1 it must "
                             "have exactly that shape, cap / dropper / pump, colours and label; a different cap, a "
                             "second bottle or a loose extra cap is an issue.")
        parts.append({"type": "image_url", "image_url": {"url": product_url}})
    data = await _qa_json(parts, "engine.scene_qa", s)
    if data is None:
        return []
    issues = [tag_issue(x, default_major=True) for x in (data.get("issues") or []) if str(x).strip()]
    return [] if data.get("ok") is True and not issues else issues[:QA_MAX_ISSUES]


CLIP_QA = """These {N} images are frames from ONE AI-generated video clip, in time order (first to last). Watch it
like an editor deciding whether the clip can go into the film. The clip must show this scene:
{SPEC}
List only clear problems a viewer would notice:
- morphing: a person, animal, object, food or vessel changes shape, size, colour, species or identity during the
  clip; a face or hairstyle changes; a face goes blank, smears or loses its features; hair vanishes or is cut
  off; a product's cap, label or shape changes;
- objects or creatures appearing from nowhere, vanishing, or duplicating (a second plate, a second animal, an extra
  hand, a loose extra cap);
- a jump inside the clip: the place, background, time of day or camera setup suddenly changes, or the clip cuts to
  a different shot;
- the planned action is not performed or is skipped (e.g. the seal is never broken, the drops never reach the
  fingertips, the race start never happens), or it happens in an impossible way (steps skipped, instant results);
- broken physics or anatomy: things floating, passing through each other, extra or fused fingers, limbs bending
  wrongly; a lid that does not fit its pot or rests on the food, a seal not on the rim, objects the wrong size;
- text: 3D letters or words that morph, melt, re-spell, flicker or become unreadable during the clip.{PRODUCT}{CAST}
Ignore normal motion, light flicker, camera movement that the scene asks for, and small blur.{SEVERITY}
Return ONLY JSON {"ok": true|false, "issues": ["MAJOR|MINOR: what is wrong and when (frame N) -> how to fix it in the
next take", ...]}."""
CLIP_QA_FRAMES = 6
CLIP_RETAKES = 1  # one new take, and only for a MAJOR problem (face / hair / outfit drift, morphing, a jump)


def _clip_frames(path: str, n: int = CLIP_QA_FRAMES) -> list[dict]:
    """n frames spread over the clip, small JPEGs as image parts for the vision model."""
    ffmpeg = video_merge.find_ffmpeg()
    if not ffmpeg:
        return []
    dur = video_merge.probe_duration(ffmpeg, path) or 0
    if dur <= 0:
        return []
    out_dir = Path(path).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        f = out_dir / f"qa_{k}.jpg"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=-2:512", "-q:v", "4", str(f)], capture_output=True, timeout=60)
        if f.exists():
            parts.append(llm.image_part(f))
    return parts


CLIP_CAST = ("\nThe images after the clip's frames are the CHARACTER REFERENCES, in order: {NAMES}. Anyone of them "
             "in the clip must keep exactly that face, hair (colour, length, style), outfit (garments, colours, "
             "patterns), footwear and accessories in every frame - list any frame where they differ from their "
             "reference (e.g. a different dress colour, hair suddenly loose, another face).")


async def clip_issues(path: str, s: dict, product_url: str | None = None,
                      cast: list[dict] | None = None) -> list[str]:
    """AI analysis of one finished clip (frames across its whole length) against its scene plan and, when the scene
    has drawn characters, against their passport portraits ([] = usable)."""
    try:
        frames = await asyncio.to_thread(_clip_frames, path)
    except Exception as e:
        log.warning("clip frames failed: %s", e)
        return []
    if len(frames) < 2:
        return []
    spec = _qa_spec(s, "first") + (f"\n- ends on: {s['end_state']}" if s.get("end_state") else "")
    text = (CLIP_QA.replace("{SEVERITY}", SEVERITY).replace("{N}", str(len(frames))).replace("{SPEC}", spec)
            .replace("{PRODUCT}", "\nThe LAST image is the user's real product: wherever it appears in the clip it "
                              "must keep exactly that shape, cap and label." if product_url else ""))
    faces = []  # each character's face (passport) and whole outfit (full-body front)
    for c in (cast or [])[:MAX_ELEMENTS]:
        nm = c.get("name") or "the person"
        for label, u in (("face", c.get("passport")), ("full outfit", c.get("front"))):
            if u and u not in (x for _, x in faces):
                faces.append((f"{nm} ({label})", u))
    text = text.replace("{CAST}", CLIP_CAST.replace("{NAMES}", ", ".join(n for n, _ in faces)) if faces else "")
    text += temporal.clip_block(s.get("events") or [])  # which events, in which order (the sequence check)
    parts = [{"type": "text", "text": text}, *frames]
    parts += [{"type": "image_url", "image_url": {"url": u}} for _, u in faces]
    if product_url:
        parts.append({"type": "image_url", "image_url": {"url": product_url}})
    data = await _qa_json(parts, "engine.clip_qa", s)
    if data is None:
        return []
    issues = [tag_issue(x, default_major=True) for x in (data.get("issues") or []) if str(x).strip()]
    return [] if data.get("ok") is True and not issues else issues[:QA_MAX_ISSUES]


def use_end_frame(s: dict) -> bool:
    """Give the clip model the last frame only when the picture changes a little; a large change morphs."""
    return str(s.get("change") or "").strip().lower() not in ("large",)


def props_line(s: dict, last: bool = False) -> str:
    """The scene's object list as a hard rule for the image model (no extra plates, bowls or bottles)."""
    props = s.get("props")
    if isinstance(props, (list, tuple)):
        props = "; ".join(str(x) for x in props if str(x).strip())
    props = str(props or "").strip()
    if not props:
        return ""
    if last:
        return (f"\nOBJECTS: the scene starts with exactly these: {props}. The last frame has the same objects, plus "
                "only what the action itself brings in; nothing else, nothing duplicated.")
    return (f"\nOBJECTS IN THIS FRAME (exactly these, each exactly once, nothing else on the surface): {props}. "
            "No additional plates, bowls, pots, lids, cups, bottles, caps, droppers or utensils; nothing duplicated.")


PERSON_ROLES = {"character"}
PRODUCT_ROLES = {"product", "logo", "garment"}


def _ordered_refs(refs: list[dict]) -> list[dict]:
    """The uploaded person first, then the product, then everything else (the image model weighs early refs most)."""
    rank = lambda r: 0 if r.get("role") in PERSON_ROLES else 1 if r.get("role") in PRODUCT_ROLES else 2
    return sorted(refs, key=rank)


_REF_NUM = re.compile(r"\b(reference(?:\s+image|\s+photo)?s?)\s*#?(\d+)\b", re.I)


def _named_refs(text: str, upload_refs: list[dict]) -> str:
    """The planner numbers the user's uploads 1, 2... in upload order, but the image call puts the character views
    first - so "exactly as in reference 1" pointed the image model at the wrong picture (a portrait instead of the
    product). Name the upload instead; _refs_note says which image number it really is in this call."""
    def sub(m):
        k = int(m.group(2)) - 1
        if not 0 <= k < len(upload_refs):
            return m.group(0)
        role = upload_refs[k].get("role")
        what = "person" if role in PERSON_ROLES else role if role in PRODUCT_ROLES else "reference"
        return f"the uploaded {what} photo"
    return _REF_NUM.sub(sub, text or "")


def _shows_product(s: dict, upload_refs: list[dict]) -> bool:
    """Does this scene show the uploaded product (its references, or its prompt / objects naming it)?"""
    idx = {i + 1 for i, r in enumerate(upload_refs) if r.get("role") in PRODUCT_ROLES}
    if not idx:
        return False
    for x in s.get("references") or []:
        n = x.get("index", -1) + 1 if isinstance(x, dict) else int(x) if str(x).isdigit() else -1
        if n in idx:
            return True
    props = s.get("props")
    text = " ".join([str(s.get("image_prompt") or ""), str(s.get("action") or ""), str(s.get("end_state") or ""),
                     "; ".join(map(str, props)) if isinstance(props, (list, tuple)) else str(props or "")])
    return any(int(m.group(2)) in idx for m in _REF_NUM.finditer(text))


def _refs_note(refs: list[dict], first_index: int = 1) -> str:
    """Tell the image model what each uploaded reference image IS, so the person keeps their real face."""
    bits = []
    for n, r in enumerate(refs, start=first_index):
        role = r.get("role")
        if role in PERSON_ROLES:
            bits.append(f"reference image {n} is THE PERSON in this film (the uploaded person photo, a real person): in "
                        "EVERY scene they look exactly as in that photo - same face and facial features, apparent age, "
                        "skin tone, hair colour and length, and the same outfit and accessories; never a look-alike, "
                        "never a costume change; the hairstyle changes only where this scene shows it (hair tied back)")
        elif role in PRODUCT_ROLES:
            bits.append(f"reference image {n} is the {role} (the uploaded {role} photo): copy it exactly - the same "
                        "bottle / object silhouette and proportions, cap, collar and dropper, materials and colours, "
                        "and the label's layout, typeface and text; never a generic or redesigned version")
        elif role in ("background", "style_reference"):
            bits.append(f"reference image {n} is a {role.replace('_', ' ')}")
    if not bits:
        return ""
    return ("\nREFERENCE IMAGES: " + "; ".join(bits) + ". Where the text describes the person differently from "
            "their reference photo (age, face, hair, clothes), the photo wins.")


async def identity_issues(person_url: str, still_url: str, action: str = "", note: dict | None = None) -> list[str]:
    """Does the person in a still still look like the uploaded photo? ([] = yes, or no person in the frame)."""
    allowed = (f"\nThis scene's action (changes it SHOWS are allowed, e.g. hair tied back, a wet face): {action[:600]}"
               if action else "")
    parts = [{"type": "text", "text": IDENTITY + allowed}, {"type": "image_url", "image_url": {"url": person_url}},
             {"type": "image_url", "image_url": {"url": still_url}}]
    data = await _qa_json(parts, "engine.story.identity", note)
    if data is None:
        return []
    diffs = [str(d) for d in (data.get("differences") or []) if str(d).strip()]
    return [] if data.get("same") is True else (diffs or ["a different-looking person"])[:6]


PLACE_CHECK = """
The image just BEFORE the frame is this scene's LOCATION PLATE (the place, empty). Also list where the frame shows a
DIFFERENT place: other wall colour or material, other furniture or layout, other windows, floor or fixed props (a
change of light for another time of day is fine; the camera angle may differ)."""


async def consistency_issues(cast_urls: list[str] | str, still_url: str, p: dict,
                             place_url: str | None = None, note: dict | None = None) -> list[str]:
    """What a viewer would notice changed between the character references (and the scene's location plate) and a
    scene still ([] = consistent)."""
    sheets = [cast_urls] if isinstance(cast_urls, str) else list(cast_urls)
    text = (CONSISTENCY.replace("{SEVERITY}", SEVERITY).replace("{K}", str(len(sheets)))
            .replace("{BIBLE}", json.dumps(p.get("bible") or {}, ensure_ascii=False)[:1500])) + (PLACE_CHECK if place_url else "")
    parts = [{"type": "text", "text": text},
             *[{"type": "image_url", "image_url": {"url": u}} for u in sheets + ([place_url] if place_url else [])],
             {"type": "image_url", "image_url": {"url": still_url}}]
    data = await _qa_json(parts, "engine.story.consistency", note)
    if data is None:
        return []
    diffs = [tag_issue(d) for d in (data.get("differences") or []) if str(d).strip()]
    return [] if data.get("same") is True and not diffs else diffs[:6]


FACE_MATCH = """Image 1 is a frame from a film. The images after it are passport portraits, in order: {NAMES}.
For each of these characters whose face is visible in image 1 (not seen from behind, not fully hidden), judge whether
it is the SAME person as their portrait: face shape, jaw and cheeks, eyes, nose, mouth, eyebrows, hairline, hair
length and style, facial hair, skin tone and apparent age. Expression, lighting, wet or messy hair, tears and camera
angle are NOT differences.
Return ONLY JSON {"faces": [{"name": "...", "visible": true|false, "score": 0-10, "box": [x0, y0, x1, y1],
"differences": "short: what differs"}]} where score 10 = certainly the same person, 7 = the same person, 4 = a
look-alike, 0 = someone else, and box is that face in image 1 as fractions (0-1) of the image width and height."""
FACE_MIN = 7  # a face scored below this is "another person": a MAJOR drift that is redrawn
FACE_SMALL = 0.2  # a face narrower than this share of the frame is cropped and enlarged, then judged again


async def _local_image(url: str, dest: Path) -> Path:
    """A still (an http URL or a data: URL from the LLM Router) as a local file."""
    if url.startswith("data:"):
        import base64
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(url.split(",", 1)[1]))
        return dest
    return Path(await _download(url, dest))


def _crop_face(src: Path, box: list, dest: Path) -> Path | None:
    """The face box (fractions of the frame) with room around it, enlarged to 512 px tall; None without ffmpeg."""
    ffmpeg = video_merge.find_ffmpeg()
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
    except (TypeError, ValueError):
        return None
    if not ffmpeg or x1 <= x0 or y1 <= y0:
        return None
    w, h = x1 - x0, y1 - y0
    x0, y0 = max(0.0, x0 - w * 0.6), max(0.0, y0 - h * 0.6)
    w, h = min(1.0 - x0, w * 2.2), min(1.0 - y0, h * 2.2)
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-vf",
                        f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x0:.4f}:ih*{y0:.4f},scale=-2:512", "-q:v", "3", str(dest)],
                       capture_output=True, timeout=60)
    return dest if r.returncode == 0 and dest.exists() else None


async def _face_scores(frame_part: dict, faces: list[tuple[str, str]], note: dict | None) -> list[dict]:
    names = ", ".join(n for n, _ in faces)
    parts = [{"type": "text", "text": FACE_MATCH.replace("{NAMES}", names)}, frame_part,
             *[{"type": "image_url", "image_url": {"url": u}} for _, u in faces]]
    data = await _qa_json(parts, "engine.story.face", note, tokens=700)
    return [f for f in ((data or {}).get("faces") or []) if isinstance(f, dict)]


async def face_issues(who: list[dict], still_url: str, folder: Path, note: dict | None = None) -> list[str]:
    """A face-only identity check of a frame against each character's passport portrait, scored 0-10. A face that
    is small in the frame (a wide shot) is cropped, enlarged and scored again: at full-frame size the general check
    cannot see it, and those were the frames that drew another person. Returns MAJOR issues ([] = same faces)."""
    faces = [(c.get("name") or "the person", c["passport"]) for c in who if c.get("passport")][:MAX_CAST]
    if not faces:
        return []
    scored = await _face_scores({"type": "image_url", "image_url": {"url": still_url}}, faces, note)
    issues, local = [], None
    for f in scored:
        name = str(f.get("name") or "")
        ref = next((u for n, u in faces if n.lower() == name.lower()), None)
        if not f.get("visible") or not ref:
            continue
        try:
            score = float(f.get("score"))
        except (TypeError, ValueError):
            continue
        box, why = f.get("box"), str(f.get("differences") or "").strip()
        try:
            small = isinstance(box, list) and len(box) == 4 and float(box[2]) - float(box[0]) < FACE_SMALL
        except (TypeError, ValueError):
            small = False
        if small:  # judge the enlarged face, not a few pixels of it
            try:
                import uuid
                local = local or await _local_image(still_url, folder / f"frame_{uuid.uuid4().hex[:8]}.png")
                crop = await asyncio.to_thread(_crop_face, local, box, folder / f"face_{uuid.uuid4().hex[:8]}.jpg")
                if crop:
                    again = await _face_scores(llm.image_part(crop), [(name, ref)], note)
                    if again and again[0].get("visible") is not False:
                        score = float(again[0].get("score", score))
                        why = str(again[0].get("differences") or why).strip()
            except Exception as e:  # the full-frame score stands
                log.warning("face crop check failed: %s", e)
        if note is not None:
            note.setdefault("face_scores", []).append({"name": name, "score": score, "small": bool(small)})
        if score < FACE_MIN:
            issues.append(f"MAJOR: {name}'s face is a different person ({why or 'another face'}) -> draw exactly the "
                          "face of their passport portrait: the same face shape, eyes, nose, mouth, eyebrows, hair, "
                          "facial hair, skin tone and age")
    return issues


def story_scenes_approved(handoff: dict) -> bool:
    """The film comes from an approved Story Intelligence plan (reviewed, maybe revised, then approved)."""
    return bool((((handoff.get("_story") or {}).get("plan") or {}).get("scenes")))


async def critique(brief: str, p: dict) -> dict:
    """An LLM director scores the plan; below CRITIC_PASS its issues go back to the planner for one rewrite."""
    plan_view = {k: p.get(k) for k in ("title", "story", "genre_label", "total", "bible")}
    plan_view["scenes"] = [{k: v for k, v in sc.items() if k != "motion"} for sc in p.get("scenes") or []]  # unused here
    try:
        out = await llm.chat_json(config.REASONING_MODEL, [
            {"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": json.dumps({"brief": brief, "plan": plan_view}, ensure_ascii=False)}],
            max_tokens=12000, stage="engine.critic")
        score = int(out.get("score"))
    except Exception as e:  # the review is a quality gate, never a reason to fail the run
        log.warning("plan critic failed: %s", e)
        return {"score": None, "issues": [], "passed": True}
    issues = [str(x) for x in (out.get("issues") or []) if str(x).strip()][:12]
    return {"score": score, "issues": issues, "strengths": out.get("strengths") or [], "passed": score >= CRITIC_PASS}


def _missing_story_fields(scenes: list[dict]) -> str:
    """'' when the scenes carry the story fields; otherwise which ones most scenes lack."""
    if not scenes:
        return ""
    lacking = [f for f in STORY_FIELDS if sum(1 for s in scenes if not str(s.get(f) or "").strip()) * 2 > len(scenes)]
    return f"the scenes are missing {', '.join(lacking)}" if lacking else ""


def weighted_durations(scenes: list[dict], total: int, lo: int, hi: int) -> list[int]:
    """Whole seconds per scene that sum to `total`, shared by each beat's weight (1-5): the peak gets the most time.
    Without weights, the planner's own durations are kept (fitted to the total)."""
    w = []
    for s in scenes:
        try:
            w.append(min(5.0, max(1.0, float(s.get("weight")))))
        except (TypeError, ValueError):
            return model_skills.fit_durations([s.get("duration") for s in scenes], total, lo, hi)
    want = [total * x / sum(w) for x in w]
    out = [min(hi, max(lo, int(v))) for v in want]
    for _ in range(400):  # hand out / take back single seconds by weight, never leaving [lo, hi]
        diff = total - sum(out)
        if diff == 0:
            break
        if diff > 0:
            room = [i for i in range(len(out)) if out[i] < hi]
            if not room:
                break
            i = max(room, key=lambda k: (want[k] - out[k], w[k]))
            out[i] += 1
        else:
            room = [i for i in range(len(out)) if out[i] > lo]
            if not room:
                break
            i = max(room, key=lambda k: (out[k] - want[k], -w[k]))
            out[i] -= 1
    return out


def _style(bible: dict) -> str:
    keys = ("characters", "environment", "lighting", "camera", "grading")
    parts = [f"{k}: {bible[k]}" for k in keys if isinstance(bible, dict) and bible.get(k)]
    return ("\nGLOBAL STYLE (identical in every scene) - " + "; ".join(parts)) if parts else ""


def _locks(continuity: dict) -> str:
    """The continuity sheet as hard rules for every still: what must stay identical and what must never appear."""
    bits = []
    for e in (continuity or {}).get("entities") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        locked = "; ".join(str(x) for x in (e.get("locked") or [])[:12])
        never = "; ".join(str(x) for x in (e.get("never") or [])[:8])
        bits.append(f"{e['name']}" + (f" - always: {locked}" if locked else "") + (f" - never: {never}" if never else ""))
    return ("\nCONTINUITY (identical in every scene, from head to toe) - " + " | ".join(bits)) if bits else ""


def recurring_subject(p: dict) -> bool:
    """Does a person or animal appear in more than one scene? Then the film needs one locked look for them."""
    ents = (p.get("continuity") or {}).get("entities") or []
    if any(isinstance(e, dict) and e.get("kind") == "character" for e in ents):
        return True
    return bool(str((p.get("bible") or {}).get("characters") or "").strip()) and len(p.get("scenes") or []) > 1


# Character references (the user's direction): every person or animal in the film is drawn FIRST, before any
# scene, as three separate images - a passport-style face portrait (large and sharp, so the face and hair are never
# guessed), then a full-body FRONT and a full-body BACK view drawn from that portrait. A person the user uploaded is
# drawn from their photo. Each scene gets the references of the characters who are in it.
CAST_VIEWS = (
    ("passport", "3:4",
     "PASSPORT-STYLE ID PORTRAIT of {CHARACTER}: head and shoulders, facing the camera straight on, neutral expression, "
     "eyes open looking into the lens, the WHOLE face and the whole hairstyle visible and in sharp focus (face shape, "
     "eyes, nose, mouth, eyebrows, skin tone, hairline, parting, hair length, colour and texture), nothing covering "
     "the face, even soft frontal studio light, plain light-grey background, no text, no labels, {ART_STYLE}."),
    ("front", "9:16",
     "FULL-BODY FRONT VIEW of {CHARACTER}: exactly the character in the reference portrait - identical face, hair, "
     "skin tone and build - standing straight and facing the camera, arms relaxed, head to toe in frame including "
     "feet and footwear, the complete outfit and accessories clearly visible, the face fully drawn and sharp, even "
     "studio lighting, plain light-grey background, no text, no labels, {ART_STYLE}."),
    ("back", "9:16",
     "FULL-BODY BACK VIEW of {CHARACTER}: exactly the character in the reference portrait, turned fully away from the "
     "camera, head to toe in frame including feet, showing the back of the hairstyle (length, shape, how it is tied "
     "or falls), the back of the outfit, footwear and any bag or accessory, even studio lighting, plain light-grey "
     "background, no text, no labels, {ART_STYLE}."),
)
MAX_CAST = 4  # characters drawn; each brings 3 reference images to the scenes they are in
MAX_IMAGE_REFS = 14  # what the image models take as references in one call


def _char_text(c) -> tuple[str, str]:
    """(name, description) of one bible character, whether the planner wrote a string or a dict."""
    if isinstance(c, dict):
        name = str(c.get("name") or c.get("who") or "").strip()
        rest = "; ".join(str(v) for k, v in c.items() if k not in ("name", "who") and str(v or "").strip()
                         and not isinstance(v, (dict, list)))
        return name, (f"{name}: {rest}" if name and rest else name or rest)
    text = str(c or "").strip()
    return "", text


NOT_SHOWN = re.compile(r"\b(never (shown|seen|on screen|appears)|not shown|not seen|off[- ]?screen|only (heard|"
                       r"through|as a voice|in the letter|on the phone)|unseen)\b", re.I)
_CHAR_SPLIT = re.compile(r"(?:^|(?<=[.;!?])\s+)([A-Z][A-Za-z'\u2019 -]{1,40}?(?: \([^)]{1,40}\))?):\s")


def _shown(desc: str) -> str | None:
    """The description of a character the film shows, without sentences about someone never shown; None when the
    character itself is never shown (its first sentence says so)."""
    sentences = re.split(r"(?<=[.!?])\s+", (desc or "").strip())
    if not sentences or not sentences[0] or NOT_SHOWN.search(sentences[0]):
        return None
    return " ".join(x for x in sentences if not NOT_SHOWN.search(x)).strip() or None


def split_characters(text: str) -> list[tuple[str, str]]:
    """A bible written as one string ("Meera: 28, ... Arjun: 30, ...") as [(name, "Name: description")]."""
    marks = list(_CHAR_SPLIT.finditer(text or ""))
    out = []
    for k, m in enumerate(marks):
        body = text[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(text)].strip()
        if body:
            out.append((m.group(1).strip(), f"{m.group(1).strip()}: {body}"))
    return out


def cast_list(p: dict, story: dict | None = None) -> list[dict]:
    """Every character the film SHOWS, one entry each, as {"name", "desc"}: the approved story's own character list
    first (structured: face, hair, wardrobe per person), then the planner's continuity entities, then its bible -
    where a bible written as one string is split per character, never drawn as one blended person. Characters the
    story never shows (a voice on the phone, a dead father in a letter) are not drawn."""
    out = []
    for c in ((story or {}).get("bible") or {}).get("characters") or []:
        if isinstance(c, dict) and c.get("name"):
            name, desc = _char_text(c)
            desc = _shown(desc)
            if desc:
                out.append({"name": name, "desc": desc})
    if out:
        return [{**c, "desc": c["desc"][:900]} for c in out[:MAX_CAST]]
    for e in (p.get("continuity") or {}).get("entities") or []:
        if isinstance(e, dict) and e.get("kind") == "character" and e.get("name"):
            locked = "; ".join(str(x) for x in (e.get("locked") or [])[:12])
            out.append({"name": str(e["name"]), "desc": f"{e['name']}" + (f": {locked}" if locked else "")})
    if not out:
        chars = (p.get("bible") or {}).get("characters")
        for c in (chars if isinstance(chars, list) else [chars]):
            parts = split_characters(c) if isinstance(c, str) else []
            for name, desc in (parts if len(parts) > 1 else [_char_text(c)]):
                desc = _shown(desc)
                if desc:
                    out.append({"name": name, "desc": desc})
    return [{**c, "desc": c["desc"][:900]} for c in out[:MAX_CAST]]


def cast_members(p: dict) -> list[str]:
    """One description per character (see cast_list)."""
    return [c["desc"] for c in cast_list(p)]


def has_cast(p: dict) -> bool:
    """Is there any person or animal in the film? Then they are drawn before the scenes."""
    return bool(cast_list(p))


def art_style(p: dict) -> str:
    """[ART STYLE] of the character references: the film's locked render style / grade, else photorealistic."""
    bible = p.get("bible") or {}
    bits = [str(bible.get(k)).strip() for k in ("grading", "style", "render_style") if bible.get(k)]
    return ", ".join(bits)[:400] or "photorealistic, true-to-life detail"


def view_prompt(template: str, character: str, style: str) -> str:
    return template.replace("{CHARACTER}", character.strip().rstrip(".")).replace("{ART_STYLE}", style.rstrip("."))


PERSON_WORDS = re.compile(r"\b(he|she|his|her|him|they|their|man|men|woman|women|girl|boy|child|children|kid|kids|"
                          r"person|people|host|presenter|creator|chef|hands?|face|mother|father|mom|dad|friend|couple|"
                          r"character|hero|heroine|student|customer|worker)\b", re.I)
GROUP_WORDS = re.compile(r"\b(they|them|their|people|men|women|children|kids|couple|friends|family|everyone|"
                         r"both|together|group|crowd)\b", re.I)
_NAME_STOP = {"the", "a", "an", "young", "old", "little", "small", "big", "mr", "mrs", "ms"}
# Animal / creature leads (a fox, a dragon, a robot): a scene that shows one without naming it still gets the cast's
# references, or the clip invents a new creature.
CREATURE_WORDS = re.compile(r"\b(animal|creature|fox|cat|kitten|dog|puppy|rabbit|bunny|bear|lion|tiger|wolf|bird|owl|"
                            r"duck|mouse|elephant|monkey|horse|pony|dragon|unicorn|robot|monster|fairy|alien)\b",
                            re.I)


def scene_cast(s: dict, cast: list[dict]) -> list[dict]:
    """The characters this scene shows: named in its plan, else everyone when it shows a person, else none."""
    if not cast:
        return []
    text = " ".join(str(s.get(k) or "") for k in ("title", "image_prompt", "action", "start_state", "end_state",
                                                    "props", "beat", "characters")).lower()
    named = []
    for c in cast:
        words = [w for w in re.findall(r"[a-z0-9]+", c["name"].lower()) if w not in _NAME_STOP and len(w) > 2]
        if words and any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
            named.append(c)
    if named:
        return named
    if any(c.get("photo") for c in cast) and PERSON_WORDS.search(text):
        return [c for c in cast if c.get("photo")] or cast
    if not PERSON_WORDS.search(text) and not CREATURE_WORDS.search(text):
        return []
    # Everyone's references for a scene that names nobody made the image model blend the faces: the lead only,
    # unless the scene is plainly about several people.
    return cast if GROUP_WORDS.search(text) else cast[:1]


PHOTO_PERSON = ("the exact person in the reference photo - the same face, apparent age, skin tone, hair and build, "
                "wearing the outfit from the photo")


LOOK_FRAME = ("LOOK FRAME (style reference) for a film. {MASTER} One establishing image of the film's world that fixes "
              "its look for every scene: the setting, background, materials and surfaces, light direction and quality, "
              "colour palette and grade, render style and lens. No action, no characters in motion, no text.{STYLE}{LOCKS}")

# All scene images are drawn at the same time, then all clips render at the same time; this caps the paid calls
# running together in each phase.
PARALLEL_SCENES = max(1, int(os.getenv("STORY_PARALLEL_SCENES", "10")))
CAST_REDRAWS = 2  # a still whose character drifted (face, hair, wardrobe) is redrawn up to this many times
CAST_FRESH = 2  # then, while the FACE, hair or outfit is still wrong, the frame is drawn again from scratch
# A difference a viewer reads as "another person" or "another outfit" (a necklace or a bag is minor).
MAJOR_DRIFT = re.compile(r"\b(face|facial|features|different (person|woman|man|girl|boy|character)|another (person|"
                         r"woman|man)|look-?alike|skin|older|younger|age|hair|hairstyle|bun|braid|beard|moustache|"
                         r"curl\w*|bald|saree|sari|kurta|outfit|dress|shirt|t-shirt|jacket|windbreaker|coat|raincoat|hoodie|sweater|"
                         r"pyjamas?|pajamas?|trousers|jeans|skirt|suit|uniform|wardrobe|clothes|clothing|garment|"
                         r"colou?r of (her|his|the) (saree|dress|shirt|kurta|outfit))\b", re.I)


def drift_score(drift: list[str]) -> tuple[int, int]:
    """(major, total) differences: a frame with fewer face / hair / outfit differences always wins. A checker's own
    MAJOR / MINOR tag decides; an untagged difference is major when it names the face, hair or outfit."""
    return severity_score(drift)


def _sound(s: dict) -> str:
    """The clip's own audio. Only an on-camera scene may contain speech; otherwise the narrator (or silence) is the
    only voice, so the clip is told to stay free of speech and the two never overlap."""
    sound = str(s.get("sound") or "").strip().rstrip(".")
    if _voice(s) == "on_camera":
        return f"Sound: {sound}" if sound else ""
    return f"Sound: {sound + '. ' if sound else ''}No speech, no voices, no dialogue"


def _voice(s: dict) -> str:
    v = str(s.get("voice") or "").strip().lower()
    if v in ("narration", "on_camera", "none"):
        return v
    return "narration" if str(s.get("script") or "").strip() else "none"


def _continuity(i: int, s: dict, scenes: list[dict], has_prev_still: bool, mode: str = "draw") -> str:
    """For scenes after the first: tie the still to the previous scene (its last frame and the link between them).
    mode "draw": same place, the shot starts right after it; "ref": a new place or a later time - only the people,
    their look and what they carry continue."""
    if i == 0:
        return ""
    prev = scenes[i - 1]
    bits = [f"\nCONTINUES FROM THE PREVIOUS SCENE, which ended on: {str(prev.get('end_state') or prev.get('image_prompt')).rstrip('. ')}"]
    if s.get("link"):
        bits.append(f"Link: {str(s['link']).rstrip('. ')}")
    if has_prev_still and mode == "ref":
        bits.append("The LAST reference image is the previous scene's LAST frame: every character keeps exactly that "
                    "face, hair, wardrobe and what they carry, and objects keep the state they ended in; this scene "
                    "is in the place and at the time it describes, so do not copy that frame's set or framing")
    elif has_prev_still:
        bits.append("The LAST reference image is the previous scene's LAST frame: this shot starts right after it - "
                    "same subject, wardrobe, hair, props in the same state and place, set, lighting and colour grade; "
                    "only the framing and the moment change")
    return ". ".join(bits) + "."


SAME_SHOT = re.compile(r"\b(continu\w*|same (moment|shot|take)|picks? up (right )?where|one (take|shot)|unbroken|"
                       r"no cut|seamless)\b", re.I)


def handoff_mode(prev: dict | None, s: dict, prev_has_end: bool) -> str:
    """How scene s takes over from the previous scene's last frame (every scene after the first does):
    "exact" - the same shot goes on: that frame IS this scene's first frame (the previous clip ends on it, this one
    starts on it: no visible cut); "draw" - same place and moment, new angle: drawn from that frame; "ref" - a new
    place or a later time: the frame keeps the people, their look and props."""
    if not prev:
        return ""
    if continues(prev, s):
        link = " ".join(str(s.get(k) or "") for k in ("link", "transition_in"))
        return "exact" if prev_has_end and SAME_SHOT.search(link) else "draw"
    return "ref"


def _cast_note(members: list[dict], first: int = 1) -> str:
    """What each character reference image is, numbered from `first` (they come first in image_urls)."""
    if not members:
        return ""
    bits, k = [], first
    for m in members:
        n = len(m["urls"])
        who = m.get("name") or ("the person" if m.get("photo") else "the character")
        bits.append(f"reference image{'s' if n > 1 else ''} {k}{f'-{k + n - 1}' if n > 1 else ''} "
                    f"{'are' if n > 1 else 'is'} {who} ({', '.join(m['views'])})")
        k += n
    return ("\nCHARACTER REFERENCES: " + "; ".join(bits) + ". Everyone who appears in this frame has exactly that face "
            "(face shape, eyes, nose, mouth, eyebrows, skin tone), that hair (colour, length, style, hairline) and that "
            "body, wardrobe, footwear and accessories, seen from this shot's angle. Every visible face is complete with "
            "all its features and every head has its full hair - never a blank, smeared, cropped or hidden face, never "
            "missing, cut-off or different hair. Ignore the references' grey background and standing pose."
            + _locked_look(members))


def _locked_look(members: list[dict]) -> str:
    """Each character's fixed look in words (face, hair, wardrobe from the bible), stated as overriding the scene's
    own description: a scene that says "navy pyjamas" must still draw the striped pair of the references."""
    locks = []
    for m in members:
        desc, name = str(m.get("desc") or "").strip()[:500], str(m.get("name") or "").strip()
        if m.get("photo"):  # the user's photo may show only the face: the outfit is the one drawn in their references
            locks.append(f"{name or 'the person'}: the face and hair of their photo, and exactly the outfit, footwear "
                         "and accessories of their full-body front and back references - no costume change")
        elif desc:  # the bible text often starts with the name already ("Mira: ...")
            locks.append(desc if name and desc.lower().startswith(name.lower()) else f"{name or 'the person'}: {desc}")
    return ("\nLOCKED LOOK - exactly this in EVERY scene, it wins over any other description in this prompt (same "
            "garments, pattern, colours, footwear, hair and face): " + " | ".join(locks) + ".") if locks else ""


# Places are drawn before the scenes too: one empty establishing plate per place, and every scene in that place is
# drawn inside it, so a bedroom does not become another bedroom in a later scene.
MAX_LOCATIONS = 4
LOCATIONS = """You split a film's scenes by the PLACE each one happens in, so every place can be drawn once as a
reference before the scenes. Same place at another time of day = the SAME place (only the light changes). A close-up
inside a place belongs to that place. Return ONLY JSON:
{"locations": [{"id": "L1", "name": "Eliot's attic bedroom", "plate": "the empty place for an establishing image:
  architecture, walls and their colours, floor, windows, furniture and where each piece stands, fixed props, light"}],
 "scenes": ["L1", "L1", "L2", ... one entry per scene in order, null for a scene with no recognisable place]}
At most {MAX} places; a place in only one scene still gets one."""
PLATE = ("LOCATION PLATE for a film: {DESC}. An empty establishing view of this place - an eye-level wide shot that "
         "shows its whole layout; no people, no animals, no text. {MASTER}{STYLE}{LOCKS}")


async def plan_locations(p: dict) -> dict:
    """{"locations": [{id, name, plate}], "scenes": [id | None per scene]} ({} when it cannot be planned)."""
    view = {"bible": {k: v for k, v in (p.get("bible") or {}).items() if k != "characters"},
            "scenes": [{"n": i + 1, "title": s.get("title"), "image_prompt": str(s.get("image_prompt") or "")[:600],
                        "start_state": s.get("start_state")} for i, s in enumerate(p.get("scenes") or [])]}
    try:
        data = await llm.chat_json(config.REASONING_MODEL, [
            {"role": "system", "content": LOCATIONS.replace("{MAX}", str(MAX_LOCATIONS))},
            {"role": "user", "content": json.dumps(view, ensure_ascii=False)[:20000]}],
            max_tokens=3000, reasoning_effort="low", stage="engine.story.locations")
    except Exception as e:  # the scenes still share the look frame
        log.warning("location plan failed: %s", e)
        return {}
    locs = [l for l in data.get("locations") or [] if isinstance(l, dict) and l.get("id") and l.get("plate")]
    locs = locs[:MAX_LOCATIONS]
    if not locs:
        return {}
    ids = {str(l["id"]) for l in locs}
    for l in locs:
        l["id"] = str(l["id"])
    scenes = [str(x) if isinstance(x, (str, int)) and str(x) in ids else None
              for x in (data.get("scenes") or [])][:len(p.get("scenes") or [])]
    scenes += [None] * (len(p.get("scenes") or []) - len(scenes))
    return {"locations": locs, "scenes": scenes} if locs else {}


def _motion(app_id: str, s: dict, q: dict, fix: str = "") -> str:
    """The clip prompt: what happens first (the action), then how the camera follows it, then the first and last
    frame (which tie the scene to its neighbours), then the sound. A retake puts the problems to avoid first."""
    lead = (f"One continuous shot in one place, no cuts, nothing morphs. AVOID (problems in the last take): {fix}. "
            if fix else "")
    if s.get("motion_verbatim"):  # the user's own scene: their words as written (a retake only adds what to avoid)
        return lead + s["motion_verbatim"]
    if s.get("motion_text"):  # a cooking scene: the ChefVision motion choreography
        text = lead + s["motion_text"] + (f". {_sound(s)}" if q.get("audio") else "")
        prompt, _neg = model_skills.finalize_motion(q["video_model"], text, sound=q["audio"])
        return prompt
    talks = (f"{s.get('speaker') or 'The speaker'} is talking the whole shot, mouth moving naturally as if speaking, "
             "face turned three-quarters to the camera and fully visible, nothing covers the mouth, small natural "
             "movements; nobody else speaks or faces the camera" if s.get("line") else "")
    bits = [s.get("sequence_note") or "", s.get("action") or "", talks,
            f"Camera: {s['camera']}" if s.get("camera") else "",
            f"Starts: {s['start_state']}" if s.get("start_state") else "",
            f"Ends: {s['end_state']}" if s.get("end_state") else "",
            _sound(s) if q.get("audio") else ""]
    text = lead + (". ".join(b.rstrip(".") for b in bits if b) or s["image_prompt"])
    prompt, _neg = model_skills.finalize_motion(q["video_model"], text, sound=q["audio"])
    return prompt


def _sequence_fields(s: dict, record: dict, n: int) -> None:
    """Scene n's events (for the clip check), its continuity brief (state at start / end, for the images) and the
    events in order (for the clip prompt), from the approved plan's temporal record."""
    events = temporal.scene_events_text(record, n)
    if not events:
        return
    row = next((r for r in record.get("scenes") or [] if r.get("n") == n), {})
    s["events"] = events
    s["continuity_brief"] = str(row.get("brief") or "")[:1500]
    s["sequence_note"] = ("In this order, each shown from start to end: "
                          + "; ".join(f"{k}. {e['description']}" for k, e in enumerate(events, start=1)))[:700]


def continues(prev: dict | None, s: dict) -> bool:
    """Does scene s pick up the very moment scene prev ended (same place, no cut in time)? Then its first frame is
    drawn from prev's real last frame, not only from words."""
    if not prev:
        return False
    link = " ".join(str(s.get(k) or "") for k in ("link", "transition_in"))
    if re.search(r"\b(time jump|later|meanwhile|next (day|morning|night)|years?|hours?|flash ?back|elsewhere|"
                 r"new (place|location)|montage|opening)\b", link, re.I):
        return False
    if flow_budget._place(prev) and flow_budget._place(s):  # both places known: the same place continues
        return not flow_budget.place_changed(prev, s)
    return bool(re.search(r"\b(continu\w*|same (moment|place|shot|room)|cut on (action|movement)|carried|picks? up|"
                          r"motion carried|reverse cut|over the shoulder)\b", link, re.I))


class Checkpoint:
    """What a film step has already made (the reviewed plan, the character sheets and places, each scene's images and
    clip, the recorded voice), kept on disk so a Retry continues where the step failed instead of starting over.
    One file per run + step + plan revision (handoff["_resume"]). It is read back only when the user pressed Retry on
    the failed step (handoff["_resume_load"]); every other start, or a different request (other prompt, story,
    quality), begins fresh and overwrites it. Without a resume folder every get() is None and put() does nothing."""

    def __init__(self, folder: str | None, key_data: dict, load: bool = True):
        import hashlib
        self.path = Path(folder) / "checkpoint.json" if folder else None
        self.key = hashlib.sha256(json.dumps(key_data, sort_keys=True, default=str).encode()).hexdigest()[:16]
        self.data: dict = {}
        if load and self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                if saved.get("key") == self.key:
                    self.data = saved.get("data") or {}
            except (OSError, ValueError) as e:
                log.warning("checkpoint %s unreadable (%s); starting fresh", self.path, e)

    def get(self, name: str, default=None):
        return self.data.get(name, default)

    def put(self, name: str, value) -> None:
        if not self.path:
            return
        self.data[name] = json.loads(json.dumps(value, default=str))  # a snapshot, not a live reference
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": self.key, "data": self.data}, ensure_ascii=False))
            tmp.replace(self.path)
        except OSError as e:  # resuming is a convenience; the film never fails because of it
            log.warning("checkpoint not saved (%s)", e)


def _files_exist(paths: list | None) -> bool:
    return bool(paths) and all(p is None or Path(p).exists() for p in paths)


async def run(app: dict, handoff: dict, assets: dict, work: Path, progress) -> dict:
    """Make the film. Returns {"path", "duration_s", "plan", "scenes":[{title, still_url, clip_url, duration}]}."""
    app_id = app["app_id"]
    _st = handoff.get("_story") or {}
    if code_motion(_st.get("mode"), _st.get("motion_engine")):  # Opus animates it: say so in every message
        app = {**app, "name": OPUS_MOTION_NAME}
    q = dict(quality(_st.get("mode"), _st.get("motion_engine")))  # its own copy: a run may reorder its clip chain
    key = fal_key()
    work.mkdir(parents=True, exist_ok=True)
    refs = []
    for aid, a in assets.items():
        if a.get("modality") == "image" and a.get("path") and a.get("role") not in ("audio_track",):
            await progress(f"{app['name']}: uploading reference {a['filename']}")
            refs.append({"asset_id": aid, "filename": a["filename"], "role": a.get("role") or "unclear",
                         "summary": (a.get("summary") or "")[:400], "url": await _upload(a["path"])})
    ck = Checkpoint(handoff.get("_resume"), {
        "prompt": handoff.get("prompt"), "request": handoff.get("request"), "quality": q,
        "story": ((handoff.get("_story") or {}).get("plan") or {}).get("scenes"),
        "motion_engine": _st.get("motion_engine"), "recipe": bool(handoff.get("_recipe")),
        "refs": sorted(r["asset_id"] for r in refs)}, load=bool(handoff.get("_resume_load")))
    resumed = ck.get("plan")
    if resumed:
        await progress(f"{app['name']}: resuming - reusing the reviewed plan"
                       + (f", {len(ck.get('scenes') or {})} scene image(s)" if ck.get("scenes") else "")
                       + (f" and {len(ck.get('clips') or {})} clip(s)" if ck.get("clips") else "")
                       + " from the failed attempt")
        p = resumed
    elif _st.get("mode") == "as_written":  # the user's own scenes: no planner, nothing rewritten
        await progress(f"{app['name']}: your {len(((_st.get('plan') or {}).get('scenes') or []))} scenes, exactly as written")
        p = written_plan(_st, q)
    else:
        await progress(f"{app['name']}: planning the story")
        p = await plan(app_id, handoff, refs, q)
    from . import lang as lang_  # one language for voice, hook and words: the user's own
    language = lang_.detect(str(handoff.get("prompt") or "").split("\n\nDetailed version")[0],
                            ((handoff.get("_story") or {}).get("classification") or {}).get("language")
                            or handoff.get("language"))
    p["language"] = language
    if resumed:  # already reviewed (and rewritten if it had to be) on the earlier attempt
        review = p.get("review") or {"score": None, "passed": True, "issues": [], "kind": "resumed"}
    elif p.get("recipe"):  # a cooking film: the ChefVision quality and continuity gate instead of the story critic
        rec = p["recipe"]
        await progress(f"{app['name']}: {rec['dish'] or 'the recipe'} - {len(rec['workflow'])} cooking stages; "
                       "checking the scenes")
        issues = food.validate.check(p["scenes"], rec, p["n_max"])
        if issues:
            await progress(f"{app['name']}: fixing {len(issues)} scene problem{'s' if len(issues) > 1 else ''}")
            p2 = await plan(app_id, handoff, refs, q, feedback=issues)
            issues2 = food.validate.check(p2["scenes"], rec, p2["n_max"])
            if len(issues2) <= len(issues):
                p, issues = p2, issues2
        review = {"score": None, "passed": not issues, "issues": issues, "kind": "food"}
    else:
        await progress(f"{app['name']}: reviewing the story before rendering")
        if story_scenes_approved(handoff):  # Story Intelligence already reviewed and the user approved it
            review = {"score": None, "passed": True, "issues": [], "kind": "approved_story"}
        else:
            review = await critique(handoff.get("prompt") or "", p)
    if not resumed and not review["passed"] and review.get("kind") != "food":
        await progress(f"{app['name']}: the review scored {review['score']}/100, rewriting the plan")
        p2 = await plan(app_id, handoff, refs, q, feedback=review["issues"])
        review2 = await critique(handoff.get("prompt") or "", p2)
        if (review2["score"] or 0) >= (review["score"] or 0):
            p, review = p2, {**review2, "first_score": review["score"]}
    p["review"] = review
    p["language"] = language  # a rewritten plan (critic or food fix) is a new dict: it needs the language again
    if not resumed:
        ck.put("plan", p)  # before narration: its retiming is redone from the recorded voice on every attempt
    if p.get("method") and not resumed:
        approaches.remember(p["method"]["family"], p["method"]["id"])
        await progress(f"{app['name']}: story method - {p['method']['label']}")
    ar = (handoff.get("request") or {}).get("aspect_ratio") or "16:9"
    lo, hi = CLIP_RANGE.get(q["video_model"], (3, 15))

    # Narration first (adapted from VideoAgent, MIT): record each narration line before any video is made, then
    # give its scene enough time to say it, so lines end with their scene instead of being cut or rushed.
    lines = [str(s.get("script") or "").strip() if _voice(s) == "narration" else "" for s in p["scenes"]]
    vo_files: list[str | None] = [None] * len(lines)
    # A documentary presented by a host: the narrator IS the host (the same voice over the board scenes).
    host = ((handoff.get("_story") or {}).get("plan") or {}).get("host") or {}
    host_gender = "male" if str(host.get("voice_gender")).lower() == "male" else "female"
    host_at = [i for i, s in enumerate(p["scenes"]) if s.get("host") and s.get("host_line")]
    if q.get("voice") and any(lines):
        await progress(f"{app['name']}: recording the narration")
        voices: list[str] = []
        lang = p["language"]
        try:
            kept = ck.get("narration") or {}
            if kept.get("lines") == lines and _files_exist(kept.get("files")) and any(kept.get("files") or []):
                vo_files, voices = kept["files"], kept.get("voices") or []  # recorded on the earlier attempt
            else:
                vo_files = await record_narration(lines, work, key, lang, voices, host_gender)
                ck.put("narration", {"lines": lines, "files": vo_files, "voices": voices})
            p["narration_voice"] = sorted(set(voices))
        except Exception as e:  # no voice available: the film is still made, music-led, and the user is told why
            log.warning("narration failed: %s", e)
            await progress(f"{app['name']}: no narration ({str(e)[:160]})")
            vo_files = [None] * len(lines)
        fitted = fit_to_narration([s["duration"] for s in p["scenes"]], vo_files, lo, hi)
        for s, d in zip(p["scenes"], fitted):
            retime(s, d)
        p["total"] = sum(fitted)
    # A short drama: every spoken line recorded first in its character's own voice, its shot made long enough for it.
    story_mode = (handoff.get("_story") or {}).get("mode")
    talk: list[str | None] = [None] * len(p["scenes"])
    if story_mode == "short_drama" and q.get("voice") and any(s.get("line") for s in p["scenes"]):
        k = len([s for s in p["scenes"] if s.get("line")])
        await progress(f"{app['name']}: recording {k} line{'s' if k > 1 else ''}, each in its character's own voice")
        kept = ck.get("dialogue") or {}
        if _files_exist(kept.get("files")) and any(kept.get("files") or []):
            talk = kept["files"]  # recorded on the earlier attempt
        else:
            talk = await record_dialogue(p["scenes"], ((handoff.get("_story") or {}).get("plan") or {}).get("voices") or {},
                                         work, key, ((handoff.get("_story") or {}).get("classification") or {}).get("language")
                                         or "english")
            ck.put("dialogue", {"files": talk})
        if not any(talk):
            await progress(f"{app['name']}: no voice could be recorded; the lines stay as subtitles")
        fitted = fit_to_narration([s["duration"] for s in p["scenes"]], talk, lo, hi)
        for s, d in zip(p["scenes"], fitted):
            retime(s, d)
        p["total"] = sum(fitted)
    # Music: composed locally while the scenes render (free, MusicGen on this Mac), mixed in after the narration.
    music_job = None
    req = handoff.get("request") or {}
    if story_mode == "motion_graphics":  # the motion playbook: no background music, SFX and narration only
        await progress(f"{app['name']}: no music (motion graphics: narration and SFX only)")
    elif q.get("music", True) and music.wanted(handoff.get("prompt") or "", req.get("audio_mode")):
        why = music.available()
        if why:
            await progress(f"{app['name']}: no music ({why})")
        else:
            p["music_prompt"] = music.prompt_for(p)
            music_job = asyncio.create_task(music.compose(p["music_prompt"], float(p["total"]) + 1.5,
                                                          work / "music.wav"))
    if p.get("recipe"):  # every scene gets its ChefVision image JSON and motion JSON (the prompts the models get)
        food.finalize(p["scenes"], p["recipe"], ar)
    coded = code_motion(story_mode, (handoff.get("_story") or {}).get("motion_engine"))
    if coded:
        await progress(f"{app['name']}: no video model - every keyframe is animated in code from an LLM motion plan")
    style = _style(p["bible"]) + _locks(p.get("continuity") or {})
    upload_refs = list(refs)  # the planner's "reference N" is upload order
    refs = _ordered_refs(refs)
    ref_urls = [r["url"] for r in refs][:7]
    person_url = next((r["url"] for r in refs if r.get("role") in PERSON_ROLES), None)
    product_url = next((r["url"] for r in refs if r.get("role") in PRODUCT_ROLES), None)

    # One locked look (the planner's master_prompt): with no reference images of their own, recurring characters are
    # drawn once, head to toe, and every still is made from that sheet, so wardrobe, footwear and animals stay the same.
    # Consistency first, before any scene starts: one locked look the scenes are all drawn from, so they can be
    # made in parallel. With no reference images of their own, recurring characters are drawn once, head to toe (the
    # character sheet: wardrobe, footwear and animals stay the same); and one look frame fixes the world (set, light,
    # palette, materials, render style), so no scene's background or grade drifts.
    master = p.get("master_prompt") or ""
    locks = _locks(p.get("continuity") or {})

    async def sheet(template: str, what: str, urls: list[str]) -> str | None:
        try:
            kw = {"aspect_ratio": ar, "num_images": 1} | ({"image_urls": urls} if urls else {})
            return _url(await _fal(fal_helper.generate_image, key, q["image_model"],
                                   template.replace("{MASTER}", master).replace("{STYLE}", _style(p["bible"]))
                                   .replace("{LOCKS}", locks), **kw), "image")
        except Exception as e:  # without it the scenes still share the style text and the references
            log.warning("%s failed: %s", what, e)
            return None

    async def view(template: str, aspect: str, character: str, urls: list[str], label: str,
                   photo: str | None) -> str | None:
        """One character reference image; drawn from the user's photo it must still be them (one redraw)."""
        prompt = view_prompt(template, character, art_style(p))
        kw = {"aspect_ratio": aspect, "num_images": 1} | ({"image_urls": urls} if urls else {})
        try:
            url = _url(await _fal(fal_helper.generate_image, key, q["image_model"], prompt, **kw), "image")
            if photo:
                drift = await identity_issues(photo, url)
                if drift:
                    await progress(f"{app['name']}: {label} does not match your photo ({drift[0][:80]}), redrawing")
                    url = _url(await _fal(fal_helper.generate_image, key, q["image_model"], prompt
                                          + " FIX: exactly the person in the reference photo; differences to remove: "
                                          + "; ".join(drift), **kw), "image")
            return url
        except Exception as e:  # without it the scenes still share the written look and the references
            log.warning("%s failed: %s", label, e)
            return None

    async def character(member: dict) -> dict | None:
        """Passport portrait first (from the user's photo when there is one), then front and back from it."""
        if member.get("urls"):  # drawn in an earlier episode of the series: the same face, not a new drawing
            return member
        photo = member.get("photo")
        who = member.get("name") or ("your person" if photo else "the character")
        passport = await view(CAST_VIEWS[0][2], CAST_VIEWS[0][1], member["desc"], [photo] if photo else [],
                              f"{who}'s passport portrait", photo)
        if not passport:
            return None
        # Front first, then back FROM the front: drawn apart, each view invented its own outfit (the passport shows
        # only the shoulders) and the scenes got two references that disagree on the clothes.
        base = [passport] + ([photo] if photo else [])
        (fv, fa, ft), (bv, ba, bt) = CAST_VIEWS[1], CAST_VIEWS[2]
        front = await view(ft, fa, member["desc"], base, f"{who}'s {fv} view", photo)
        back_prompt = bt + (" The SECOND reference image is the full-body front view: exactly that outfit, footwear, "
                            "accessories and hair, seen from behind." if front else "")
        back = await view(back_prompt, ba, member["desc"],
                          [passport] + ([front] if front else []) + ([photo] if photo else []),
                          f"{who}'s {bv} view", photo)
        views = [(v, u) for v, u in zip(("passport portrait", "full-body front", "full-body back"),
                                        (passport, front, back)) if u]
        return {**member, "urls": [u for _, u in views], "views": [v for v, _ in views],
                "passport": passport, "front": front, "back": back}

    # Every person or animal in the film is drawn first: the user's uploaded person from their photo (it stands for
    # the first character), the others from the plan's description.
    # A documentary never draws a generated face for a real person: its people are archive photos (or the user's).
    members = [] if story_mode == "cinematic_doc" else cast_list(p, (handoff.get("_story") or {}).get("plan"))
    series = (handoff.get("_story") or {}).get("series") if story_mode == "short_drama" else None
    if series and series.get("cast"):
        members = reuse_cast(members, series["cast"])
    if person_url:
        first = members[0] if members else {"name": "", "desc": ""}
        desc = PHOTO_PERSON + (f" ({first['desc']})" if first.get("desc") else "")
        members = [{"name": first.get("name") or "", "desc": desc, "photo": person_url}] + members[1:]
    members = members[:MAX_CAST]
    await progress(f"{app['name']}: locking the look"
                   + (f" and drawing {len(members)} character{'s' if len(members) > 1 else ''} (passport portrait, "
                      "front and back) before the scenes" if members else " before the scenes"))
    setup = ck.get("setup")
    if setup and len(setup.get("drawn") or []) == len(members):  # drawn on the earlier attempt
        places, drawn, look, plates = setup["places"], setup["drawn"], setup["look"], setup["plates"]
        locs = places.get("locations") or []
    else:
        places = ({} if story_mode in ("cinematic_doc", "as_written") or p.get("recipe")
                  else await plan_locations(p))
        locs = places.get("locations") or []
        if locs:
            await progress(f"{app['name']}: drawing {len(locs)} location{'s' if len(locs) > 1 else ''} before the scenes ("
                           + ", ".join(l.get("name") or l["id"] for l in locs) + ")")
        got = await asyncio.gather(
            *[character(m) for m in members],
            sheet(LOOK_FRAME, "look frame", ref_urls[:3]) if not locs and story_mode != "as_written"
            else asyncio.sleep(0, None),
            *[sheet(PLATE.replace("{DESC}", str(l["plate"])[:900]), f"location {l['id']}", ref_urls[:3]) for l in locs])
        drawn, look, plates = list(got[:len(members)]), got[len(members)], list(got[len(members) + 1:])
        ck.put("setup", {"places": places, "drawn": drawn, "look": look, "plates": plates})
        ck.put("scenes", {})  # new character sheets: images and clips made from the old ones are not reused
        ck.put("clips", {})
    plate_of = {l["id"]: (l, u) for l, u in zip(locs, plates) if u}
    scene_place = [plate_of.get(x) for x in places.get("scenes") or [None] * len(p["scenes"])]
    if plate_of:
        p["locations"] = [{"name": l.get("name"), "plate_url": u} for l, u in plate_of.values()]
    cast = [c for c in drawn if c and c["urls"]]
    casts = [u for c in cast for u in c["urls"]]
    if cast:
        p["cast_sheet_url"], p["cast_sheets"] = cast[0]["passport"], casts
        p["cast"] = [{k: c.get(k) for k in ("name", "desc", "passport", "front", "back")} for c in cast]
        if isinstance(series, dict):  # kept on the run's story: the next episode draws nobody twice
            series["cast"] = remember_cast(series.get("cast") or [], cast)
    if look:
        p["look_frame_url"] = look
    if cast and any(m.startswith("seedance") for m in clip_models(q)[:1]) and photoreal_people(p, cast):
        q["_no_seedance"] = True
        await progress(f"{app['name']}: realistic people in this film - clips go to "
                       f"{clip_models(q)[0]} first (Seedance refuses frames that may show a real person)")

    def place_of(i: int) -> tuple[dict, str] | None:
        return scene_place[i] if i < len(scene_place) else None

    def anchor_for(s: dict, i: int) -> tuple[list[dict], list[str]]:
        """The scene's own characters' references, then its location plate (else the look frame); the image models
        weigh early references most."""
        who = scene_cast(s, cast)
        urls = [u for c in who for u in c["urls"]][:MAX_IMAGE_REFS - 1]
        pl = place_of(i)
        return who, urls + ([pl[1]] if pl else [look] if look else [])

    def look_note(n: int, i: int) -> str:
        pl = place_of(i)
        if pl:
            return (f"\nReference image {n} is this scene's LOCATION PLATE ({pl[0].get('name') or 'the place'}): this "
                    "shot happens INSIDE that same place - the same architecture, walls and their colour, floor, "
                    "windows, furniture and its layout, fixed props, materials and render style - seen from this "
                    "scene's camera angle. Only the light may differ if this scene's time of day differs.")
        return (f"\nReference image {n} is the film's LOOK FRAME: the same world - setting, background, materials, "
                "light, colour palette and grade, render style - in this shot, unless this scene's plan moves to a "
                "new place." if look else "")

    # The scenes as they are really made (stills, then clips), for the live view while the film renders.
    board = [{"title": s.get("title") or f"Scene {i + 1}", "duration": s["duration"], "still_url": None,
              "clip_url": None} for i, s in enumerate(p["scenes"])]
    show = getattr(progress, "live", None)

    async def publish():
        if show:
            try:
                await show({"scenes": board, "title": p.get("title") or ""})
            except Exception as e:  # the live view is a nicety; never stop the film for it
                log.warning("live update failed: %s", e)

    await publish()
    async def draw(prompt: str, img_kw: dict, s: dict, label: str, which: str = "first",
                   prev_url: str | None = None) -> str:
        """One image; with an uploaded person it must look like their photo (up to 2 redraws, the best is kept).
        Then the AI analyses the frame against its scene plan and redraws it once when it found problems."""
        img = _url(await _fal(fal_helper.generate_image, key, q["image_model"], prompt, **img_kw), "image")
        best_n = None
        if person_url:
            best = img
            for attempt in range(3):
                drift = await identity_issues(person_url, img, s.get("action") or "", note=s)
                if best_n is None or len(drift) < best_n:
                    best, best_n = img, len(drift)
                if not drift or attempt == 2:
                    break
                await progress(f"{app['name']}: {label} does not match your photo ({drift[0][:80]}), redrawing")
                # Every reference stays, in the same order (the prompt numbers them): with only the photo the face
                # came back but the outfit, back of the hair and the place were invented again.
                urls = list(img_kw.get("image_urls") or [])
                if person_url not in urls:
                    urls = urls[:MAX_IMAGE_REFS - 1] + [person_url]
                fix = prompt + (f"\nFIX: the person must look exactly like reference image {urls.index(person_url) + 1} "
                                "(the user's photo), wearing the outfit of their character references. "
                                "Differences to remove: " + "; ".join(drift))
                img = _url(await _fal(fal_helper.generate_image, key, q["image_model"], fix, **{**img_kw, "image_urls": urls}), "image")
                s.setdefault("identity_fixed", []).extend(drift)
            img = best
        if not q.get("scene_qa", True):
            return img
        await progress(f"{app['name']}: AI checking {label}")
        issues = await scene_issues(img, s, which, prev_url, product_url)
        rec = {"issues": issues, "redrawn": False}
        majors = [x for x in issues if is_major(x)]
        if majors:  # a coin count or the hand holding a glass is not worth a new image (MINOR issues stay)
            await progress(f"{app['name']}: {label} - {plain(majors[0])[:90]}; redrawing")
            fix = prompt + "\nFIX THESE PROBLEMS (a check of the previous attempt found them): " + "; ".join(
                plain(x) for x in majors + [x for x in issues if x not in majors])
            try:
                img2 = _url(await _fal(fal_helper.generate_image, key, q["image_model"], fix, **img_kw), "image")
                issues2 = await scene_issues(img2, s, which, prev_url, product_url)
                # A redraw that fixes the scene must not lose the user's face again.
                face_ok = not person_url or len(await identity_issues(person_url, img2, s.get("action") or "")) <= (best_n or 0)
                if severity_score(issues2) < severity_score(issues) and face_ok:
                    img, rec = img2, {"issues": issues2, "redrawn": True, "fixed": issues}
            except Exception as e:  # the first image is still usable
                log.warning("%s redraw failed: %s", label, e)
        s.setdefault("qa", {})[which] = rec
        return img

    # Last frames (adapted from ViMax, MIT): the end of each scene is drawn from its first frame, so the clip performs
    # the action from A to B, with the same character references as its first frame.
    async def end_frame(i: int, s: dict, still: str) -> str | None:
        if not s.get("end_state"):
            return None
        who, anchor = anchor_for(s, i)
        await progress(f"{app['name']}: scene {i + 1}/{len(p['scenes'])} last frame")
        prod = next((r for r in refs if r.get("role") in PRODUCT_ROLES), None) if _shows_product(s, upload_refs) else None
        rest = [r for r in refs if r is not prod]
        off = 1 if prod else 0
        prompt = (_named_refs(f"The LAST frame of the same shot, a moment later, after this action: {s.get('action') or ''}. "
                              f"It shows exactly: {s['end_state']}. ", upload_refs)
                  + "The FIRST reference image is the first frame of the shot: "
                  "same people, wardrobe, product, set, lighting and colour grade; only what the action changed is "
                  "different; no object is added or duplicated unless the action adds it."
                  + (_refs_note([prod], 2) if prod else "")
                  + _cast_note(who, first=2 + off)
                  + _named_refs(("\n" + s["continuity_brief"]) if s.get("continuity_brief") else "", upload_refs)
                  + _named_refs(props_line(s, last=True), upload_refs) + _refs_note(rest[:3], 2 + len(anchor) + off) + style)
        try:
            img_kw = {"aspect_ratio": ar, "num_images": 1,
                      "image_urls": ([still] + ([prod["url"]] if prod else []) + anchor + [r["url"] for r in rest][:3])[:MAX_IMAGE_REFS]}
            last = await draw(prompt, img_kw, s, f"scene {i + 1} last frame", which="last", prev_url=still)
            # the clip interpolates towards this frame: a drifted face or outfit here drifts the whole clip
            return await match_cast(i, s, last, img_kw, who, "last", prompt)
        except Exception as e:  # a missing end frame only loses the A-to-B guide; the clip still runs
            log.warning("end frame for scene %s failed: %s", i + 1, e)
            return None

    # Archive photos (cinematic-skill): the real photos a documentary's scenes pin as prints, with their credits.
    from .story_intel import research as _research
    photos = (handoff.get("_story") or {}).get("archive") or []
    archive: dict[int, list[dict]] = {}
    credits = ""
    if photos and any(s.get("archive") for s in p["scenes"]):
        await progress(f"{app['name']}: fetching the archive photos the scenes pin")
        got = await archive_refs(photos, p["scenes"], work)
        by_id = {ph["id"]: {**ph, "fal_url": got[ph["id"]]} for ph in photos if ph["id"] in got}
        for i, s in enumerate(p["scenes"]):
            archive[i] = [by_id[str(a)] for a in s.get("archive") or [] if str(a) in by_id]
        credits = _research.credits_md(list(by_id.values()))
        p["credits"] = credits

    # The user's order: ALL scene images first, drawn at the same time (each from its characters' passport / front /
    # back references, the look frame and the user's references; the written link keeps the cuts continuous), then
    # ALL clips rendered at the same time from those images.
    stills: list[str | None] = [None] * len(p["scenes"])
    ends: list[str | None] = [None] * len(p["scenes"])
    gate = asyncio.Semaphore(PARALLEL_SCENES)

    async def first_frame(i: int, s: dict, prev_frame: str | None = None, mode: str = "draw") -> str:
        await progress(f"{app['name']}: scene {i + 1}/{len(p['scenes'])} still"
                       + (" (from the previous scene's last frame)" if prev_frame else ""))
        who, anchor = anchor_for(s, i)
        img_kw = {"aspect_ratio": ar, "num_images": 1}
        # A scene that shows the product gets its photo FIRST (the models copy early references most closely).
        prod = next((r for r in refs if r.get("role") in PRODUCT_ROLES), None) if _shows_product(s, upload_refs) else None
        lead = [prod["url"]] if prod else []
        rest = [r for r in refs if r is not prod]
        off = len(lead)
        arch = [a for a in archive.get(i, [])][:MAX_IMAGE_REFS - len(anchor) - off]
        urls = (lead + anchor + [a["fal_url"] for a in arch] + [r["url"] for r in rest][:5])[:MAX_IMAGE_REFS - (1 if prev_frame else 0)]
        if prev_frame:  # the continuity handoff: the picture the previous scene really ended on, as the LAST ref
            urls.append(prev_frame)
            s["handoff_from"] = i  # 1-based number of the scene it continues
        if urls:
            img_kw["image_urls"] = urls
        brief = ("\n" + s["continuity_brief"]) if s.get("continuity_brief") else ""
        lead_note = _refs_note([prod], 1) if prod else ""
        if s.get("as_written"):  # the user's words exactly; only what their own reference images are is added
            prompt = (_named_refs(s["image_prompt"], upload_refs) + lead_note + _cast_note(who, 1 + off)
                      + _refs_note(rest[:5], len(anchor) + len(arch) + off + 1))
        else:
            prompt = (_named_refs(s["image_prompt"] + props_line(s), upload_refs) + lead_note + _cast_note(who, 1 + off)
                      + look_note(len(anchor) + off, i) + _archive_note(arch, len(anchor) + off + 1)
                      + _refs_note(rest[:5], len(anchor) + len(arch) + off + 1)
                      + _named_refs(brief, upload_refs) + _continuity(i, s, p["scenes"], bool(prev_frame), mode) + style)
        s["still_prompt"] = prompt
        s["cast_in_scene"] = [c.get("name") or "the person" for c in who]
        still = await draw(prompt, img_kw, s, f"scene {i + 1}", which="first")
        return await match_cast(i, s, still, img_kw, who, "first", prompt)

    async def match_cast(i: int, s: dict, still: str, img_kw: dict, who: list[dict], which: str,
                         prompt: str = "") -> str:
        """A vision check of a frame against the scene's characters and place; edited until they match. While a
        face, hair or outfit still differs after the edits, the frame is drawn again from scratch (with the
        character references) and the closest one is kept: no clip is made from a frame with another person."""
        refs_who = [u for c in who for u in c["urls"]]
        # The check sees each character's face and whole outfit (passport + front): the back view only cost tokens.
        check_refs = [u for c in who for u in dict.fromkeys((c.get("passport"), c.get("front"))) if u] or refs_who
        pl = place_of(i)
        frame = "" if which == "first" else " last frame"
        faces_dir = work / "face_check"

        async def check(img: str) -> list[str]:
            general, faces = await asyncio.gather(
                consistency_issues(check_refs, img, p, pl[1] if pl else None, note=s),
                face_issues(who, img, faces_dir, note=s))
            return faces + [d for d in general if not (faces and is_major(d) and "face" in d.lower())]

        if refs_who or pl:
            drift = await check(still)
            fixed: list[str] = []
            for _ in range(CAST_REDRAWS):
                if not drift_score(drift)[0]:  # only a MAJOR difference (face, hair, outfit, place) is edited
                    break
                major = [d for d in drift if is_major(d)]
                await progress(f"{app['name']}: scene {i + 1}{frame} character/place check - {plain(major[0])[:80]}; "
                               "fixing it")
                fixed += drift
                # An EDIT of this frame: its composition, pose and camera stay; only the differences change. Drawing
                # it again from the same text brings the same drift back.
                fix = ("EDIT reference image 1 - this scene's frame. Keep its composition, camera, poses, action and "
                       "everything else exactly as it is; change ONLY these differences so the characters match their "
                       "references and the place matches its location plate: " + "; ".join(plain(d) for d in drift) + "."
                       + _cast_note(who, first=2)
                       + (look_note(2 + len(refs_who), i) if pl else ""))
                edit_kw = {**img_kw, "image_urls": ([still] + refs_who + ([pl[1]] if pl else []))[:MAX_IMAGE_REFS]}
                try:
                    again = _url(await _fal(fal_helper.generate_image, key, q["image_model"], fix, **edit_kw), "image")
                except Exception as e:  # the earlier still is still usable
                    log.warning("scene %s character fix failed: %s", i + 1, e)
                    break
                drift2 = await check(again)
                if drift_score(drift2) <= drift_score(drift):
                    still, drift = again, drift2
            for k in range(CAST_FRESH if refs_who and prompt else 0):
                if not drift_score(drift)[0]:
                    break
                major = [d for d in drift if is_major(d)]
                await progress(f"{app['name']}: scene {i + 1}{frame} still shows a different look ({plain(major[0])[:70]}); "
                               f"drawing it again from the character references ({k + 1}/{CAST_FRESH})")
                fixed += drift
                redo = (prompt + "\nMOST IMPORTANT: every character is EXACTLY the person in their reference images - "
                        "the same face (shape, eyes, nose, mouth, eyebrows, skin tone, age), the same hair and the same "
                        "outfit and colours. Avoid: " + "; ".join(plain(d) for d in major) + ".")
                try:
                    again = _url(await _fal(fal_helper.generate_image, key, q["image_model"], redo, **img_kw), "image")
                except Exception as e:  # the closest frame so far is still usable
                    log.warning("scene %s fresh character redraw failed: %s", i + 1, e)
                    break
                drift2 = await check(again)
                if drift_score(drift2) < drift_score(drift):
                    still, drift = again, drift2
            suffix = "" if which == "first" else "_last"
            if fixed:
                s["consistency_fixed" + suffix] = fixed
            if drift:
                s["consistency_left" + suffix] = drift
        return still

    drawn_evt = [asyncio.Event() for _ in p["scenes"]]

    async def frames(i: int, s: dict) -> None:
        try:
            await _frames(i, s)
        finally:  # set even on failure: a scene waiting for this one never hangs
            drawn_evt[i].set()

    async def _frames(i: int, s: dict) -> None:
        kept = (ck.get("scenes") or {}).get(str(i))
        if kept and kept.get("still"):  # drawn (and checked) on the earlier attempt
            stills[i], ends[i] = kept["still"], kept.get("end")
            s.update({k: v for k, v in (kept.get("scene") or {}).items() if k not in ("duration",)})
            board[i]["still_url"] = stills[i]
            await publish()
            return
        prev_frame, mode = None, ""
        if i > 0 and (i - 1) in board_at and not coded:
            # Every scene takes over from the frame the previous scene really ended on. Waited for OUTSIDE the
            # gate: the scene it waits on may still need a gate slot.
            await drawn_evt[i - 1].wait()
            prev_frame = ends[i - 1] or stills[i - 1]
            mode = handoff_mode(p["scenes"][i - 1], s, bool(ends[i - 1])) if prev_frame else ""
            s["handoff"] = mode or None
        if mode == "exact":  # the same shot goes on: the previous scene's last frame IS this scene's first frame
            stills[i] = still = prev_frame
            s["handoff_from"] = i
            await progress(f"{app['name']}: scene {i + 1}/{len(p['scenes'])} starts on scene {i}'s last frame")
            board[i]["still_url"] = still
            await publish()
            async with gate:
                ends[i] = await end_frame(i, s, still)
            saved = ck.get("scenes") or {}
            saved[str(i)] = {"still": stills[i], "end": ends[i], "scene": s}
            ck.put("scenes", saved)
            return
        async with gate:
            stills[i] = still = await first_frame(i, s, prev_frame, mode or "draw")
            board[i]["still_url"] = still
            await publish()
            if not coded:  # code animation needs no last frame: one image call less per scene
                ends[i] = await end_frame(i, s, still)
        saved = ck.get("scenes") or {}
        saved[str(i)] = {"still": stills[i], "end": ends[i], "scene": s}
        ck.put("scenes", saved)

    n = len(p["scenes"])
    hosted: dict[int, str] = {}
    host_job = None
    if host_at:  # the documentary's host is filmed and lip-synced while the board images are drawn
        from . import presenter_engine
        lang = p["language"]
        await progress(f"{app['name']}: filming the host for scene{'s' if len(host_at) > 1 else ''} "
                       + ", ".join(str(i + 1) for i in host_at))
        host_job = asyncio.create_task(presenter_engine.host_clips(
            app, host, [p["scenes"][i]["host_line"] for i in host_at], ar, work / "host", progress, lang))
    board_at = [i for i in range(n) if i not in host_at]
    await progress(f"{app['name']}: drawing all {len(board_at)} scene images in parallel")
    try:
        await all_or_nothing([frames(i, p["scenes"][i]) for i in board_at])
    except BaseException:
        if host_job:
            host_job.cancel()
        raise
    if host_job:
        try:
            hosted = dict(zip(host_at, await host_job))
        except Exception as e:  # no lip-sync: the host's lines become narrated board scenes, the film still finishes
            log.warning("documentary host failed: %s", e)
            await progress(f"{app['name']}: the host could not be filmed ({str(e)[:120]}); narrating those scenes")
            for i in host_at:
                sc = p["scenes"][i]
                sc["voice"], sc["script"], sc["host"] = "narration", sc["host_line"], False
                sc["image_prompt"] = ("Wide view of the film's whole board - every cluster of prints, documents and the "
                                      "map softly lit, the camera pulled back; no people, no faces, no text. "
                                      + str(sc.get("title") or ""))
                lines[i] = sc["host_line"]
            if q.get("voice"):
                extra = await record_narration([lines[i] if i in host_at else "" for i in range(n)], work,
                                               key, lang, [], host_gender)
                for i in host_at:
                    vo_files[i] = extra[i]
            await all_or_nothing([frames(i, p["scenes"][i]) for i in host_at])
    await progress(f"{app['name']}: all images ready - "
                   + (f"animating {n} scenes in code (no video model)" if coded
                      else f"rendering {n - len(hosted)} clips in parallel"))

    async def scene(i: int, s: dict) -> dict:
        kept = (ck.get("clips") or {}).get(str(i))
        if kept and kept.get("path") and Path(kept["path"]).exists() and kept.get("still_url") == stills[i]:
            board[i].update(clip_url=kept.get("clip_url"), model=kept.get("model"))
            await publish()
            return kept  # rendered (and checked) on the earlier attempt
        async with gate:
            out = await _scene(i, s)
        saved = ck.get("clips") or {}
        saved[str(i)] = out
        ck.put("clips", saved)
        return out

    async def speak_on_camera(i: int, s: dict, path: str) -> str:
        from . import presenter_engine
        try:
            out, model = await presenter_engine.dialogue_clip(app, path, talk[i], ar, q["resolution"], work / "lines",
                                                              f"line_{i + 1:02d}", progress)
            s["lipsync"] = model
            return out
        except Exception as e:  # no lip-sync: the line still plays over its shot, the film still finishes
            log.warning("lip-sync of scene %s failed: %s", i + 1, e)
            await progress(f"{app['name']}: scene {i + 1} could not be lip-synced ({str(e)[:100]}); "
                           "the line plays over the shot")
            s["lipsync"] = None
            return await asyncio.to_thread(presenter_engine.voice_over, video_merge.find_ffmpeg(), path, talk[i],
                                           str(work / f"line_{i + 1:02d}_vo.mp4"))

    async def _scene(i: int, s: dict) -> dict:
        if i in hosted:  # the host on camera, already lip-synced with their own voice
            board[i].update(clip_url=None, model="host")
            return {"title": s.get("title") or f"Scene {i + 1}", "duration": s["duration"], "still_url": None,
                    "beat": s.get("beat") or "", "action": "the host speaks to camera", "link": s.get("link") or "",
                    "end_still_url": None, "change": "", "image_prompt": "", "motion_prompt": "", "props": "",
                    "qa": {}, "clip_url": None, "model": "host (lip-sync)", "host": True,
                    "script": s.get("host_line") or "", "path": hosted[i]}
        still = stills[i]
        if coded:  # no video model: the LLM plans the keyframe's motion, code renders it
            from . import motion_code
            await progress(f"{app['name']}: scene {i + 1}/{len(p['scenes'])} - planning and animating in code")
            src = await _download(still, work / "motion" / f"key_{i + 1:02d}.png")
            path, plan = await motion_code.animate(Path(src), s, float(s["duration"]), ar,
                                                   work / "motion" / f"scene_{i + 1:02d}", i + 1)
            used = "code animation (LLM-planned" + (", camera move only)" if plan.get("fallback") else ")")
            s["motion_plan"] = {"camera": plan["camera"], "layers": len(plan["layers"])}
            board[i].update(clip_url=None, model=used)
            await publish()
            return {"title": s.get("title") or f"Scene {i + 1}", "duration": s["duration"], "still_url": still,
                    "beat": s.get("beat") or "", "action": s.get("action") or "", "link": s.get("link") or "",
                    "end_still_url": None, "change": s.get("change") or "",
                    "image_prompt": s.get("still_prompt") or s.get("image_prompt") or "",
                    "motion_prompt": s.get("motion_prompt") or "", "props": s.get("props") or "",
                    "qa": s.get("qa") or {}, "clip_url": None, "model": used, "path": path}
        # A last frame very different from the first makes the clip model MORPH one into the other: for a large
        # change the last frame only guides the next scene; the clip performs the action from the prompt.
        nxt = p["scenes"][i + 1] if i + 1 < len(p["scenes"]) else {}
        if ends[i] and nxt.get("handoff") == "exact":  # the next clip starts on this frame: land on it exactly
            end = ends[i]
        else:
            end = ends[i] if use_end_frame(s) else None
        if end and s.get("consistency_left_last") and nxt.get("handoff") != "exact":  # a drifted last frame would pull the whole clip towards it
            log.info("scene %s: last frame still drifts (%s); the clip runs without it", i + 1,
                     s["consistency_left_last"][0][:120])
            s["end_frame_dropped"] = True
            end = None
        who = scene_cast(s, cast)
        clip, used = await _clip(app, i, len(p["scenes"]), s, still, ar, q, key, progress, end=end, cast=who)
        path = await _download(clip, work / f"scene_{i + 1:02d}.mp4")
        if q.get("clip_qa", True):
            await progress(f"{app['name']}: AI checking scene {i + 1} clip")
            issues = await clip_issues(path, s, product_url, cast=who)
            rec = {"issues": issues, "retaken": False}
            for take in range(2, 2 + CLIP_RETAKES):  # a new take only for a MAJOR problem; fewer majors wins
                majors = [x for x in issues if is_major(x)]
                if not majors:
                    break
                await progress(f"{app['name']}: scene {i + 1} clip - {plain(majors[0])[:90]}; making take {take}")
                try:
                    morph = any(w in " ".join(majors).lower() for w in ("morph", "jump", "cuts to", "changes shape",
                                                                        "different face", "outfit", "hair",
                                                                        "action_skipped", "wrong_event_order"))
                    clip2, used2 = await _clip(app, i, len(p["scenes"]), s, still, ar, q, key, progress,
                                               end=None if morph else end, fix="; ".join(plain(x) for x in issues),
                                               cast=who)
                    path2 = await _download(clip2, work / f"scene_{i + 1:02d}_take{take}.mp4")
                    issues2 = await clip_issues(path2, s, product_url, cast=who)
                    if severity_score(issues2) < severity_score(issues):
                        rec = {"issues": issues2, "retaken": True, "fixed": (rec.get("fixed") or []) + issues}
                        clip, used, path, issues = clip2, used2, path2, issues2
                except Exception as e:  # the best take so far is still usable
                    log.warning("scene %s take %s failed: %s", i + 1, take, e)
                    break
            if s.get("events"):  # the sequence verdict: shown per scene, a FAIL offers a re-render of that scene
                rec["sequence"] = temporal.verdict(rec.get("issues"), "clip_qa" not in (s.get("qa_unchecked") or []))
            s.setdefault("qa", {})["clip"] = rec
        if talk[i]:  # a short drama's spoken line, in the speaker's voice, lip-synced onto this clip
            path = await speak_on_camera(i, s, path)
        board[i].update(clip_url=clip, model=used)
        await publish()
        return {"title": s.get("title") or f"Scene {i + 1}", "duration": s["duration"], "still_url": still,
                "beat": s.get("beat") or "", "action": s.get("action") or "", "link": s.get("link") or "",
                "end_still_url": ends[i], "change": s.get("change") or "",
                "image_prompt": s.get("still_prompt") or s.get("image_prompt") or "",
                "motion_prompt": s.get("motion_prompt") or "", "props": s.get("props") or "",
                "qa": s.get("qa") or {}, "clip_url": clip, "model": used, "path": path,
                "events": [e["id"] for e in s.get("events") or []], "handoff_from": s.get("handoff_from"),
                "handoff": s.get("handoff")}

    done = await all_or_nothing([scene(i, s) for i, s in enumerate(p["scenes"])])
    await progress(f"{app['name']}: joining {len(done)} clips")
    final = await join([d["path"] for d in done], work / "final.mp4", ar,
                       "1080p" if coded else q["resolution"])  # code clips cost nothing at full size
    if any(vo_files):
        await progress(f"{app['name']}: mixing the narration")
        final = await add_voiceover(final, [d["path"] for d in done], vo_files,
                                    [_voice(s) == "on_camera" for s in p["scenes"]])
    # Someone talking or explaining, a product, a device or an ad: effects AND motion design, always.
    # Action / cinematic films: only the narration, a typographic hook and title words (clean picture).
    level = motion_design.finish_level(p.get("genre"), handoff.get("prompt") or "",
                                       (handoff.get("request") or {}).get("production_type") or [],
                                       person_talks=any(_voice(s) == "on_camera" for s in p["scenes"]))
    if story_mode == "cinematic_doc":
        level = "documentary"
    if story_mode in ("short_drama", "as_written"):  # as written: only the user's own lines (subtitles), no added text
        level = "drama"
    await progress(f"{app['name']}: finishing - {motion_design.finish_summary(level)}")
    if music_job:
        await progress(f"{app['name']}: mixing the music")
        try:
            voiced = any(lines) or any(_voice(s) == "on_camera" for s in p["scenes"])
            final = await music.mix(final, await music_job, voiced, work / "final_music.mp4")
            p["music_added"] = True
        except Exception as e:  # music is a finishing touch: deliver the film without it
            log.warning("music failed: %s", e)
            await progress(f"{app['name']}: music could not be added ({str(e)[:140]})")
    effects = None
    graphics = None
    clean = text = None
    editable = story_mode in ("motion_graphics", "cinematic_doc", "short_drama")  # 3D and documentaries: every word stays editable
    style = EFFECT_STYLE.get(p.get("genre", "")) or (
        (EFFECT_STYLE["ugc_ad"] if any(_voice(s) == "on_camera" for s in p["scenes"]) else EFFECT_STYLE["advertisement"])
        if level == "full" else None)
    if editable:  # the 3D key words, hook, callouts and captions as an editable text layer on the clean film
        from . import text_layer
        ff0 = video_merge.find_ffmpeg()
        spans0, t0 = [], 0.0
        for d in done:
            L = await asyncio.to_thread(video_merge.probe_duration, ff0, d["path"]) if ff0 else float(d["duration"])
            spans0.append((t0, t0 + L))
            t0 += L
        story_scenes = (((handoff.get("_story") or {}).get("plan") or {}).get("scenes") or [])
        finished = await text_layer.finish(app, handoff.get("prompt") or "", p, final, [d["path"] for d in done],
                                           lines, ar, work / "finish", progress, effects_style=style, level=level,
                                           scene_words=story_scenes, spans=spans0,
                                           do_effects=q.get("effects", True) and handoff.get("effects", True)
                                           and level not in ("documentary", "drama"),
                                           do_graphics=q.get("graphics", True) and handoff.get("graphics", True)
                                           and level not in ("documentary", "drama"),
                                           captions_on=any(lines) or any(talk),
                                           words_style="flat" if story_mode == "cinematic_doc" else "3d")
        final, clean, text = finished["path"], finished["clean"], finished["text_layer"]
        effects, graphics = finished["effects"], finished["graphics"]
    if not editable and q.get("effects", True) and handoff.get("effects", True) and effects_engine.available() is None:
        try:  # Effects Studio first (on the footage, keeps the audio); graphics go on top and avoid its moments
            # Every film with speech gets captions; a clean (cinematic / action) film gets ONLY captions.
            effects = await effects_engine.run({**app, "name": f"{app['name']} effects"},
                                               {"prompt": handoff.get("prompt") or "", "effects_style": style,
                                                "captions_only": not style,
                                                "allow_none": level != "full",
                                                "planned_moments": motion_design.planned_moments(
                                                    p.get("edit_direction") or {}, scene_spans(p["scenes"]))},
                                               final, work / "effects", progress)
            final = effects["path"]
        except Exception as e:  # effects are a finishing touch: deliver the film without them
            log.warning("effects pass failed: %s", e)
            effects = {"error": str(e)[:300], "plan": []}
    busy = [(float(e["start"]), float(e["end"]), e["fx"]) for e in (effects or {}).get("plan") or []]
    if not editable and q.get("graphics", True) and handoff.get("graphics", True):  # the director decides
        try:
            graphics = await finishing.run(app, handoff.get("prompt") or "", p, final, [d["path"] for d in done],
                                           lines, ar, work / "graphics", progress, q, busy=busy, level=level,
                                           captions=(effects or {}).get("captions"))
            final = graphics["path"]
        except Exception as e:  # graphics are a finishing touch: the film without them is still delivered
            log.warning("graphics pass failed: %s", e)
            await progress(f"{app['name']}: graphics could not be added ({str(e)[:160]}); delivering the film without them")
            graphics = {"error": str(e)[:300]}
    ff = video_merge.find_ffmpeg()
    lengths = [await asyncio.to_thread(video_merge.probe_duration, ff, d["path"]) if ff else float(d["duration"])
               for d in done]
    spans, t = [], 0.0
    for L in lengths:  # where each scene sits in the finished film (the join keeps the clips' real lengths)
        spans.append([round(t, 3), round(t + L, 3)])
        t += L
    return {"path": final, "duration_s": await asyncio.to_thread(_probe, final), "plan": p,
            "voiceover": [l for l in lines if l] if q.get("voice") else [],
            "voice": [_voice(s) for s in p["scenes"]], "graphics": graphics, "effects": effects, "finish_level": level,
            "spans": spans, "aspect_ratio": ar, "clean": clean, "text_layer": text, "credits": credits,
            "clip_models": clip_models(q),
            "scenes": [{**{k: v for k, v in d.items() if k != "path"}, "local_clip": d["path"]} for d in done]}


def film_cast(out: dict) -> list[dict]:
    """The finished film's drawn characters (plan["cast"]) in the shape scene_cast / _cast_note use."""
    members = []
    for c in ((out.get("plan") or {}).get("cast") or []):
        views = [(v, c.get(k)) for v, k in (("passport portrait", "passport"), ("full-body front", "front"),
                                             ("full-body back", "back")) if c.get(k)]
        if views:
            members.append({**c, "name": c.get("name") or "", "urls": [u for _, u in views],
                            "views": [v for v, _ in views]})
    return members


async def rerender_scene(out: dict, n: int, instruction: str, work: Path, progress) -> dict:
    """A new take of ONE scene of a finished film: a new first frame (from the previous scene's last frame, so the
    cut still matches) and a new clip, both AI-checked, spliced into the finished film over exactly that scene's
    time span. The narration, music and the other scenes (with their graphics) stay as they were.
    Returns {"path", "scene": the updated scene entry}."""
    q, key = dict(quality()), fal_key()
    if out.get("clip_models"):  # the same clip chain the film was made with (3D motion: Seedance 2.0 first)
        q = {**q, "video_model": out["clip_models"][0], "fallbacks": out["clip_models"][1:]}
    scenes, spans = out.get("scenes") or [], out.get("spans") or []
    if not (1 <= n <= len(scenes)) or len(spans) != len(scenes):
        raise EngineError("this film has no scene timing to replace a scene in")
    s, (a, b) = dict(scenes[n - 1]), spans[n - 1]
    ar = out.get("aspect_ratio") or "16:9"
    work.mkdir(parents=True, exist_ok=True)
    prev = (scenes[n - 2].get("end_still_url") or scenes[n - 2].get("still_url")) if n > 1 else None
    # The film's character sheets come first (as in the first render), so the new take keeps face, hair and outfit.
    who = scene_cast(s, film_cast(out))
    sheets = [u for c in who for u in c["urls"]][:MAX_IMAGE_REFS - 2]
    refs = sheets + [u for u in (prev, s.get("still_url")) if u]
    ask = f"\nCHANGE IN THIS NEW TAKE (the user asked): {instruction.strip()}" if instruction.strip() else ""
    prompt = (s.get("image_prompt") or s.get("action") or s.get("title") or "") + ask + _cast_note(who) + (
        "\nThe LAST reference image is this scene's previous version: same characters, set, light and style."
        if s.get("still_url") else "")
    kw = {"aspect_ratio": ar, "num_images": 1, **({"image_urls": refs} if refs else {})}
    await progress(f"Scene {n}: new first frame")
    still = _url(await _fal(fal_helper.generate_image, key, q["image_model"], prompt, **kw), "image")
    if sheets:  # the same character check (and edit-fix) as the first render
        p = out.get("plan") or {}
        drift = await consistency_issues(sheets, still, p, note=s)
        for _ in range(CAST_REDRAWS):
            if not drift:
                break
            await progress(f"Scene {n}: character check - {drift[0][:80]}; fixing it")
            fix = ("EDIT reference image 1 - this scene's frame. Keep its composition, camera, poses and action; change "
                   "ONLY these differences so the characters match their references: " + "; ".join(drift) + "."
                   + _cast_note(who, first=2))
            try:
                again = _url(await _fal(fal_helper.generate_image, key, q["image_model"], fix,
                                        **{**kw, "image_urls": ([still] + sheets)[:MAX_IMAGE_REFS]}), "image")
            except Exception as e:  # the earlier still is still usable
                log.warning("scene %s character fix failed: %s", n, e)
                break
            drift2 = await consistency_issues(sheets, again, p, note=s)
            if len(drift2) <= len(drift):
                still, drift = again, drift2
        if drift:
            s["consistency_left"] = drift
    issues = await scene_issues(still, s, "first", prev)
    if issues:
        await progress(f"Scene {n}: {issues[0][:90]}; redrawing")
        still2 = _url(await _fal(fal_helper.generate_image, key, q["image_model"],
                                 prompt + "\nFIX THESE PROBLEMS: " + "; ".join(issues), **kw), "image")
        issues2 = await scene_issues(still2, s, "first", prev)
        if len(issues2) < len(issues):
            still, issues = still2, issues2
    span = b - a
    models = clip_models(q)
    motion = (f"{instruction.strip()}. " if instruction.strip() else "") + (s.get("motion_prompt") or s.get("action") or "")
    clip_url, used, path, clip_issues_left, errors = None, None, None, [], []
    for take in range(2):
        for m in models:
            lo, hi = CLIP_RANGE.get(m, (3, 15))
            dur = max(lo, min(hi, int(-(-span // 1))))  # at least the span; the splice trims to it exactly
            await progress(f"Scene {n}: new clip ({dur} s, {m}){' - second take' if take else ''}")
            try:
                fix = (f"One continuous shot, nothing morphs. AVOID: {'; '.join(clip_issues_left)}. " if take else "")
                base = dict(duration=str(dur), aspect_ratio=ar, resolution=clip_resolution(m, q), audio=False)
                rrefs, rnote = clip_references(m, still, who)
                els, enote = clip_elements(m, who)
                vid = None
                if rrefs or els:  # the characters ride along (Seedance references / Kling elements); plain if refused
                    try:
                        vid = (await _fal(fal_helper.generate_video, key, m, (rnote + " " + fix + motion)[:2400],
                                          image_references=rrefs, **base) if rrefs else
                               await _fal(fal_helper.generate_video, key, m, (enote + ". " + fix + motion)[:2400],
                                          elements=els, start_image=still, multi_shot=False, **base))
                    except Exception as e:
                        if type(e).__name__ == "RunCancelled" or any(k in str(e).lower() for k in REFUSAL_MARKERS):
                            raise
                        log.warning("scene %s new take with characters failed on %s (%s); plain clip", n, m,
                                    str(e)[:200])
                if vid is None:
                    vid = await _fal(fal_helper.generate_video, key, m, (fix + motion)[:2400], start_image=still,
                                     multi_shot=not who, **base)
                clip_url, used = _url(vid, "video"), m
                break
            except Exception as e:  # refused, unavailable or failed: the next model in the chain takes over
                if type(e).__name__ == "RunCancelled":
                    raise
                errors.append(f"{m}: {str(e)[:160]}")
                log.warning("scene %s new take on %s failed (%s); trying the next clip model", n, m, str(e)[:200])
        if not clip_url:
            raise EngineError(f"every clip model failed the new take of scene {n}: " + "; ".join(errors[-3:]))
        path = await _download(clip_url, work / f"scene_{n:02d}_new{take + 1}.mp4")
        found = await clip_issues(path, s, cast=who)
        if not found or (take and len(found) >= len(clip_issues_left)):
            clip_issues_left = found if not take or len(found) < len(clip_issues_left) else clip_issues_left
            break
        clip_issues_left = found
        await progress(f"Scene {n}: {found[0][:90]}; one more take")
    await progress(f"Scene {n}: splicing the new take into the film")
    final = await asyncio.to_thread(splice, out["path"], path, a, b, str(work / f"film_scene{n}.mp4"))
    s.update(still_url=still, clip_url=clip_url, model=used, local_clip=path,
             qa={"first": {"issues": issues}, "clip": {"issues": clip_issues_left}},
             rerendered=int(s.get("rerendered") or 0) + 1, instruction=instruction.strip())
    return {"path": final, "scene": s}


def splice(film: str, clip: str, a: float, b: float, out: str) -> str:
    """The film with [a, b) replaced by the clip (scaled to the film's frame, trimmed or held to exactly b - a).
    The film's own audio track is kept as it is, so narration and music stay in sync."""
    ff = video_merge.find_ffmpeg()
    if not ff:
        raise EngineError("ffmpeg is not installed where VideoStudio runs.")
    probe = subprocess.run([ff.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate", "-of", "csv=p=0", film],
                           capture_output=True, text=True, timeout=60).stdout.strip().split(",")
    w, h = int(probe[0]), int(probe[1])
    num, _, den = probe[2].partition("/")
    fps = round(float(num) / float(den or 1), 3)
    d = b - a
    fc = (f"[0:v]trim=0:{a:.3f},setpts=PTS-STARTPTS[v0];"
          f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
          f"tpad=stop_mode=clone:stop_duration={d:.3f},trim=duration={d:.3f},setpts=PTS-STARTPTS[v1];"
          f"[0:v]trim=start={b:.3f},setpts=PTS-STARTPTS[v2];[v0][v1][v2]concat=n=3:v=1:a=0[v]")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", film, "-i", clip, "-filter_complex", fc,
           "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise EngineError(f"Splicing the new scene failed: {(r.stderr or '').strip()[-400:]}")
    return out


async def _none() -> None:
    return None


_STOP_ALL = ("RunCancelled", "RunDeleted", "CancelledError")


async def all_or_nothing(coros: list) -> list:
    """Run the scenes in parallel. When the user cancels (or deletes the run) the others stop at once. When one scene
    fails on a model error, the others still finish: their fal jobs are already paid for and running, and what they
    make is kept in the checkpoint, so a Retry only redoes the scene that failed. The first error is raised after."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    first: BaseException | None = None
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                e = None if t.cancelled() else t.exception()
                if e is None:
                    continue
                if type(e).__name__ in _STOP_ALL or not isinstance(e, Exception):
                    raise e
                first = first or e
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if first:
        raise first
    return [t.result() for t in tasks]


async def _tts(text: str, key: str) -> str:
    """One narration line through fal's ElevenLabs TTS; returns the audio URL."""
    _count("voice", TTS_MODEL)
    res = await asyncio.to_thread(fal_helper.client(key).subscribe, TTS_MODEL,
                                  arguments={"text": text, "voice": TTS_VOICE}, client_timeout=fal_helper.FAL_TIMEOUT)
    url = ((res or {}).get("audio") or {}).get("url")
    if not url:
        raise EngineError(f"The voice model returned no audio: {json.dumps(res, default=str)[:300]}")
    return url


def _voice_of(name: str, voices: dict) -> dict:
    """A drama character's voice ({"gender", "voice"}) by name: exact, then a name that contains the other."""
    key = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    norm = {re.sub(r"[^a-z0-9]+", " ", str(k).lower()).strip(): v for k, v in (voices or {}).items()}
    if key in norm:
        return norm[key]
    return next((v for k, v in norm.items() if key and k and (key in k or k in key)), {"gender": "female"})


async def record_dialogue(scenes: list[dict], voices: dict, work: Path, key: str,
                          language: str = "english") -> list[str | None]:
    """One audio file per spoken line of a short drama, each in its speaker's own voice (None for scenes without a
    line, or a line no voice could say: it stays a subtitle)."""
    from . import presenter_engine  # (imports this module: loaded here, not at the top)
    gate = asyncio.Semaphore(2)  # Kokoro runs on this machine: a couple of lines at a time

    async def one(i: int, s: dict) -> str | None:
        line = str(s.get("line") or "").strip()
        if not line:
            return None
        v = _voice_of(s.get("speaker"), voices)
        async with gate:
            used: list[str] = []
            try:
                path = await presenter_engine.speak(line, v.get("gender") or "female", language or "english", key,
                                                    work / f"line_{i + 1:02d}", used, voice=v.get("voice"))
            except Exception as e:
                log.warning("line %s could not be spoken: %s", i + 1, e)
                return None
            if "ElevenLabs" in used:
                _count("voice", TTS_MODEL)
            s["voice_used"] = v.get("voice") or used[-1] if used else None
            return path
    return list(await asyncio.gather(*(one(i, s) for i, s in enumerate(scenes))))


def reuse_cast(members: list[dict], drawn: list[dict]) -> list[dict]:
    """A series' next episode: characters drawn in an earlier episode keep their passport / front / back images;
    new characters are drawn as usual."""
    by = {re.sub(r"[^a-z0-9]+", " ", str(c.get("name") or "").lower()).strip(): c for c in drawn or []}
    out = []
    for m in members:
        c = by.get(re.sub(r"[^a-z0-9]+", " ", str(m.get("name") or "").lower()).strip())
        if c and c.get("passport") and not m.get("photo"):
            views = [(v, c.get(k)) for v, k in (("passport portrait", "passport"), ("full-body front", "front"),
                                                ("full-body back", "back")) if c.get(k)]
            m = {**m, "urls": [u for _, u in views], "views": [v for v, _ in views], "passport": c["passport"],
                 "front": c.get("front"), "back": c.get("back")}
        out.append(m)
    return out


def remember_cast(old: list[dict], cast: list[dict]) -> list[dict]:
    """The series' drawn characters after this episode (this episode's drawings added, earlier ones kept)."""
    keep = {str(c.get("name") or "").lower(): {k: c.get(k) for k in ("name", "desc", "passport", "front", "back")}
            for c in old or []}
    for c in cast:
        if c.get("name") and not c.get("photo"):
            keep[str(c["name"]).lower()] = {k: c.get(k) for k in ("name", "desc", "passport", "front", "back")}
    return list(keep.values())


async def record_narration(lines: list[str], work: Path, key: str, language: str = "english",
                           used: list[str] | None = None, gender: str = "female") -> list[str | None]:
    """One audio file per narration line (None for scenes without narration). Free first: the local Kokoro voice
    (the same one Presenter Studio uses); ElevenLabs on fal only when paid voices are switched on
    (PRESENTER_PAID=on) or Kokoro does not speak the language."""
    from . import presenter_engine  # (imports this module: loaded here, not at the top)
    used = used if used is not None else []
    gate = asyncio.Semaphore(2)  # Kokoro runs on this machine: a couple of lines at a time

    async def one(i: int, line: str) -> str | None:
        if not line:
            return None
        async with gate:
            before = len(used)
            path = await presenter_engine.speak(line, gender, language or "english", key, work / f"vo_{i + 1:02d}", used)
            if any(u == "ElevenLabs" for u in used[before:]):
                _count("voice", TTS_MODEL)
            return path
    return list(await asyncio.gather(*(one(i, l) for i, l in enumerate(lines))))


def fit_to_narration(durations: list[int], vo_files: list[str | None], lo: int, hi: int) -> list[int]:
    """Lengthen a scene whose narration needs more time than planned (line + 0.8 s to breathe), within [lo, hi].
    Scenes only grow: the story's weighting stays, and a line still too long is sped up a little when mixed."""
    ffmpeg = video_merge.find_ffmpeg()
    out = []
    for d, f in zip(durations, vo_files):
        need = 0
        if f and ffmpeg:
            need = int(-(-(video_merge.probe_duration(ffmpeg, f) + 0.8) // 1))
        out.append(max(lo, min(hi, max(int(d), need))))
    return out


def place_narration(starts: list[float], ends: list[float], lengths: list[float | None], on_camera: list[bool],
                    total: float, gap: float = 0.25, max_rate: float = 1.3) -> list[tuple[float, float, float] | None]:
    """Where each narration line plays: (start, speed, play_until) or None. Lines never overlap each other (a line
    starts no earlier than the previous one ends + gap) and never run into an on-camera scene's speech or past the
    end: a line that still does not fit at max_rate is faded out at that boundary."""
    out, prev_end = [], 0.0
    for i, length in enumerate(lengths):
        if not length:
            out.append(None)
            continue
        start = max(starts[i] + 0.15, prev_end + gap)
        nxt = next((starts[j] for j in range(i + 1, len(starts)) if on_camera[j]), total)  # hard wall: speech/end
        nxt_line = next((starts[j] + 0.15 for j in range(i + 1, len(lengths)) if lengths[j]), nxt)
        soft = min(ends[i], nxt) - 0.2  # finish inside its own scene when possible
        hard = min(nxt, nxt_line) - gap
        room = max(soft - start, 0.3)
        rate = min(max_rate, max(1.0, length / room))
        if start + length / rate > hard:  # still too long for the next line's start: use the full max speed
            rate = min(max_rate, max(rate, length / max(hard - start, 0.3)))
        until = min(start + length / rate, nxt - 0.1)
        if start >= until:
            out.append(None)
            continue
        out.append((round(start, 3), round(rate, 3), round(until, 3)))
        prev_end = until
    return out


async def add_voiceover(video: str, scene_paths: list[str], vo_files: list[str | None],
                        on_camera: list[bool] | None = None) -> str:
    """Speak each narration scene's line at that scene's start, over the film's own sound, which is turned down only
    while the narrator speaks (on-camera scenes have no narration, so their speech plays at full level)."""
    ffmpeg = video_merge.find_ffmpeg()
    if not ffmpeg:
        raise EngineError("ffmpeg is not installed where VideoStudio runs.")
    starts, t = [], 0.0
    for sp in scene_paths:
        starts.append(t)
        t += await asyncio.to_thread(video_merge.probe_duration, ffmpeg, sp)
    ends = starts[1:] + [t]
    work = Path(video).parent
    total = await asyncio.to_thread(video_merge.probe_duration, ffmpeg, video)
    lengths = [await asyncio.to_thread(video_merge.probe_duration, ffmpeg, f) if f else None for f in vo_files]
    placed = place_narration(starts, ends, lengths, on_camera or [False] * len(vo_files), total)
    vo = [(f, pl) for f, pl in zip(vo_files, placed) if f and pl]
    spans = []
    has_audio = await asyncio.to_thread(video_merge.has_audio_stream, ffmpeg, video)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video]
    parts, labels = [], []
    for n, (path, (start, rate, until)) in enumerate(vo, start=1):
        cmd += ["-i", path]
        tempo = f"atempo={rate:.3f}," if rate > 1.0 else ""
        play = until - start
        cut = f"atrim=end={play:.3f},afade=t=out:st={max(0.0, play - 0.25):.3f}:d=0.25,"  # never past its slot
        ms = int(start * 1000)
        spans.append((start, until + 0.3))
        parts.append(f"[{n}:a]{tempo}{cut}aresample=48000,adelay={ms}|{ms}[v{n}]")
        labels.append(f"[v{n}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest[vo]")
    if has_audio:  # keep the clips' ambience under the voice
        duck = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans)
        parts.append(f"[0:a]aresample=48000,volume=0.3:enable='{duck}'[bg]")
        parts.append("[bg][vo]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        parts.append("[vo]apad[mix]")
    out = work / "final_vo.mp4"
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[mix]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise EngineError(f"Adding the voice-over failed: {(proc.stderr or '').strip()[-500:]}")
    return str(out)


MAX_ELEMENTS = 3  # characters bound to one clip as Kling elements


def clip_elements(model: str, members: list[dict] | None) -> tuple[list[dict], str]:
    """Kling elements for the scene's characters (passport portrait + front / back views), so the clip model keeps
    their face, hair and outfit when they move or turn - the start frame alone shows one angle only.
    ([], "") for a model without elements or a scene without drawn characters."""
    if not members or not model.startswith("kling") or model == "kling_4_0":
        return [], ""
    els, names = [], []
    for m in members:
        frontal = m.get("passport") or m.get("front")
        more = [u for u in (m.get("front"), m.get("back")) if u and u != frontal][:3]
        if not frontal or not more:  # Kling needs the frontal image and 1-3 more angles
            continue
        els.append({"frontal_image": frontal, "reference_images": more})
        names.append(m.get("name") or ("the person" if m.get("photo") else "the character"))
        if len(els) == MAX_ELEMENTS:
            break
    if not els:
        return [], ""
    note = ("Characters: " + "; ".join(f"@Element{k} is {nm}" for k, nm in enumerate(names, 1))
            + " - the same face, hair, outfit and footwear in every frame, from every angle")
    return els, note


REF_MODELS = set(fal_helper._VIDEO_REF2V_MODELS)


def clip_references(model: str, still: str, members: list[dict] | None) -> tuple[list[str], str]:
    """Seedance reference mode for a scene with drawn characters: the scene's still as @Image1 (the opening frame) and
    each character's passport / front / back views after it, so the clip keeps their face, hair and outfit when they
    move or turn. ([], "") for a model without reference mode or a scene without drawn characters."""
    if model not in REF_MODELS or not still or not members:
        return [], ""
    urls, bits, k = [still], [], 2
    for m in members:
        views = [u for u in dict.fromkeys((m.get("passport"), m.get("front"), m.get("back"))) if u]
        views = views[:fal_helper.REF2V_MAX_IMAGES - len(urls)]
        if not views:
            continue
        name = m.get("name") or ("the person" if m.get("photo") else "the character")
        bits.append(f"@Image{k}" + (f"-@Image{k + len(views) - 1}" if len(views) > 1 else "") + f" {'are' if len(views) > 1 else 'is'} {name}")
        urls += views
        k += len(views)
        if len(urls) >= fal_helper.REF2V_MAX_IMAGES:
            break
    if not bits:
        return [], ""
    note = ("@Image1 is the opening frame of this shot: start exactly on it - same composition, camera, set, light and "
            "people. Character references: " + "; ".join(bits) + " - the same face, hair (colour, length, style), "
            "outfit, colours, footwear and accessories in every frame, from every angle; never a costume or hair change.")
    return urls, note


async def _clip(app, i, n, s, still, ar, q, key, progress, end: str | None = None, fix: str = "",
                cast: list[dict] | None = None) -> tuple[str, str]:
    """Animate the still on the quality's clip model, then on its fallbacks if a model refuses the input. On Kling
    the scene's characters ride along as elements (their reference views)."""
    models = clip_models(q)
    if cast and any(c.get("passport") or c.get("front") for c in cast):
        # A scene with drawn characters: the models that take their reference sheets go first (Seedance reference
        # mode, Kling 3 elements); a start frame alone lets the face change as soon as the person moves.
        takes_refs = lambda m: m in REF_MODELS or (m.startswith("kling") and m != "kling_4_0")
        models = sorted(models, key=lambda m: 0 if takes_refs(m) else 1)
    last = None
    for m in models:
        lo, hi = CLIP_RANGE.get(m, (3, 15))
        dur = max(lo, min(hi, int(s["duration"])))
        note = "" if m == models[0] else f", retrying on {m} because {models[models.index(m) - 1]} failed"
        await progress(f"{app['name']}: scene {i + 1}/{n} clip ({dur} s{', start and end frame' if end else ''}{note})")
        els, who = clip_elements(m, cast)
        rrefs, rnote = clip_references(m, still, cast)
        try:
            s["motion_prompt"] = _motion(app["app_id"], s, {**q, "video_model": m}, fix)
            # A multi-shot clip draws every new shot from text alone: new angle, new face. One shot with characters.
            kw = dict(duration=str(dur), aspect_ratio=ar,
                      resolution=clip_resolution(m, q),
                      audio=q["audio"], start_image=still, multi_shot=not cast, **({"end_image": end} if end else {}))
            if rrefs:  # Seedance reference mode: the still and the character sheets (no start/end frame fields)
                try:
                    rkw = {k: v for k, v in kw.items() if k not in ("start_image", "end_image", "multi_shot")}
                    vid = await _fal(fal_helper.generate_video, key, m, f"{rnote} {s['motion_prompt']}",
                                     image_references=rrefs, **rkw)
                    s["clip_refs"] = len(rrefs)
                    return _url(vid, "video"), m
                except Exception as e:  # a consistency aid: an endpoint failure still gets the plain clip
                    if (type(e).__name__ == "RunCancelled" or any(k in str(e).lower() for k in REFUSAL_MARKERS)
                            or _errors.refusal_reason(e, m)):  # the same frame is refused again without refs
                        raise
                    log.warning("scene %s clip with character references failed on %s (%s); retrying without them",
                                i + 1, m, str(e)[:200])
            if els:
                try:
                    vid = await _fal(fal_helper.generate_video, key, m, f"{who}. {s['motion_prompt']}",
                                     elements=els, **kw)
                    s["clip_elements"] = len(els)
                    return _url(vid, "video"), m
                except Exception as e:  # a consistency aid: an endpoint rejecting elements still gets the plain clip
                    if any(k in str(e).lower() for k in REFUSAL_MARKERS):
                        raise
                    log.warning("scene %s clip with elements failed on %s (%s); retrying without them",
                                i + 1, m, str(e)[:200])
            vid = await _fal(fal_helper.generate_video, key, m, s["motion_prompt"], **kw)
            return _url(vid, "video"), m
        except Exception as e:  # refused, unavailable or failed: the next model in the chain takes over
            if type(e).__name__ == "RunCancelled":
                raise
            last = e
            if (m.startswith("seedance") and _errors.refusal_reason(e, m) == "real_person"
                    and not any(q is v for v in (*QUALITY.values(), MOTION_3D_MODELS))):  # never a shared preset
                q["_no_seedance"] = True  # the other scenes and takes of this film skip the refusal
            log.warning("scene %s clip on %s failed (%s); trying the next clip model", i + 1, m, str(e)[:200])
    raise EngineError(f"Every clip model failed scene {i + 1}: {str(last)[:300]}")


def _add_silence(ffmpeg: str, path: str) -> str:
    """The clip with a silent stereo track (the join keeps sound only when every clip has some)."""
    out = str(Path(path).with_name(Path(path).stem + "_snd.mp4"))
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-shortest", out], capture_output=True, timeout=300, check=True)
    return out


async def join(paths: list[str], out: Path, aspect_ratio: str, resolution: str) -> str:
    ffmpeg = video_merge.find_ffmpeg()
    if not ffmpeg:
        raise EngineError("ffmpeg is not installed where VideoStudio runs.")
    w, h = video_merge.dimensions_for(aspect_ratio, resolution)
    with_sound = [await asyncio.to_thread(video_merge.has_audio_stream, ffmpeg, p) for p in paths]
    if any(with_sound) and not all(with_sound):  # a host's speech next to silent clips: give those a silent track,
        paths = [p if a else await asyncio.to_thread(_add_silence, ffmpeg, p) for p, a in zip(paths, with_sound)]
    if len(paths) == 1:
        await asyncio.to_thread(video_merge.reencode_scene_video, ffmpeg, paths[0], str(out), w, h)
    else:
        await asyncio.to_thread(video_merge.merge_videos, ffmpeg, paths, str(out), w, h)
    return str(out)


def scene_spans(scenes: list[dict]) -> list[tuple[float, float]]:
    """(start, end) seconds of each planned scene, from their durations."""
    out, t = [], 0.0
    for sc in scenes:
        d = float(sc.get("duration") or 0)
        out.append((t, t + d))
        t += d
    return out


def _probe(path: str) -> float:
    ffmpeg = video_merge.find_ffmpeg()
    return round(video_merge.probe_duration(ffmpeg, path), 2) if ffmpeg else 0.0
