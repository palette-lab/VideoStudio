"""Presenter Studio: a person talking to camera with lip-synced speech (YouTube explainers, UGC ads, testimonials,
spokesperson videos).

Pipeline:
1. Script: the LLM writes what the presenter says (sized to the length, ~2.5 words per second), who the presenter is
   and where they sit, in the format's style (YouTube explainer, UGC ad, testimonial, spokesperson).
2. Presenter: the user's photo of the person, or a generated, fictional person (never a real or named one). With a
   product photo, the presenter is generated holding the product.
3. Voice: the user's own audio file when given, otherwise the script spoken by Kokoro (hexgrad, Apache-2.0) running
   locally on this machine's CPU - free.
4. Lip-sync: the photo and each audio chunk become a talking video with Ditto (Ant Group, Apache-2.0) running locally
   (Apple GPU or CPU) - free. Chunks are at most ~27 s, cut at pauses; every chunk starts from the same photo, so the
   cuts play as the jump cuts creators use, with a punch-in on every other chunk.
5. The chunks are joined, each with its own audio, so lips and voice stay in sync; Effects Studio then adds
   creator-style effects for the format when no later step does.

Only the presenter photo uses a paid model (Nano Banana Pro on fal, a few cents). Paid voice (ElevenLabs) and paid
lip-sync on fal (Kling AI Avatar v2, LongCat, InfiniteTalk) are used only when PRESENTER_PAID=on, as fallbacks after
the local models. The local models live in tools/ (see tools/local_lipsync/README.md).
"""

import asyncio
import json
import logging
import math
import os
import re
import subprocess
from pathlib import Path

from router_core import config, llm

from . import effects_engine, finishing, genres, music, story_engine
from .vendor.flow import fal_helper, video_merge

log = logging.getLogger("palette_video.presenter")

APP_ID = "palette-presenter-studio"
ROOT = Path(__file__).resolve().parents[3]
LOCAL_DIR = Path(os.getenv("PRESENTER_LOCAL_DIR", ROOT / "tools"))
DITTO_REPO = LOCAL_DIR / "vendor-src" / "ditto-talkinghead"
LOCAL_PY = DITTO_REPO / ".venv" / "bin" / "python"   # one venv runs both Ditto and Kokoro
DITTO_RUN = LOCAL_DIR / "local_lipsync" / "ditto_run.py"
KOKORO_RUN = LOCAL_DIR / "local_lipsync" / "kokoro_tts.py"
KOKORO_MODELS = LOCAL_DIR / "vendor-src" / "kokoro-models"
LOCAL_DEVICE = os.getenv("PRESENTER_LOCAL_DEVICE", "auto")  # auto (Apple GPU if present) | mps | cpu
LOCAL_TIMEOUT_S = int(os.getenv("PRESENTER_LOCAL_TIMEOUT", "3600"))
# Ditto renders at the photo's own size; a smaller photo is much faster on a small Mac and the film is 720p/1080p.
LOCAL_MAX_SIDE = int(os.getenv("PRESENTER_LOCAL_MAX_SIDE", "1024"))
# Lip-sync models: the local one first and free; fal's (paid) only with PRESENTER_PAID=on.
LIPSYNC = {
    "ditto_local": {"kind": "local", "label": "Ditto (open source, on this machine)"},
    "kling_avatar_pro": {"endpoint": "fal-ai/kling-video/ai-avatar/v2/pro", "kind": "fal", "label": "Kling AI Avatar v2 Pro"},
    "kling_avatar": {"endpoint": "fal-ai/kling-video/ai-avatar/v2/standard", "kind": "fal", "label": "Kling AI Avatar v2"},
    "longcat": {"endpoint": "fal-ai/longcat-single-avatar/image-audio-to-video", "kind": "open",
                "label": "LongCat-Video-Avatar (open source, MIT)"},
    "infinitalk": {"endpoint": "fal-ai/infinitalk", "kind": "open", "label": "InfiniteTalk (open source, Apache-2.0)"},
}
INFINITALK_FPS = 25       # InfiniteTalk renders at 25 fps; num_frames 41..721 (~28.8 s)
LONGCAT_FIRST_S, LONGCAT_SEG_S = 5.8, 5.0   # LongCat: first segment ~5.8 s, each further one ~5 s (max 10)
CHUNK_MAX_S = 27.0        # every model above takes this in one call
VOICES = {"female": os.getenv("PRESENTER_VOICE_FEMALE", "Rachel"), "male": os.getenv("PRESENTER_VOICE_MALE", "Brian")}
# Kokoro's languages: (words the script's language may start with, espeak language code, female voice, male voice).
KOKORO_LANGS = [
    (("english (uk", "english (british", "british", "en-gb"), "en-gb", "bf_emma", "bm_george"),
    (("english", "en"), "en-us", "af_heart", "am_michael"),
    (("hindi", "hi"), "hi", "hf_alpha", "hm_omega"),
    (("spanish", "español", "es"), "es", "ef_dora", "em_alex"),
    (("french", "français", "fr"), "fr-fr", "ff_siwis", "ff_siwis"),
    (("italian", "it"), "it", "if_sara", "im_nicola"),
    (("japanese", "ja"), "ja", "jf_alpha", "jm_kumo"),
    (("portuguese", "pt"), "pt-br", "pf_dora", "pm_alex"),
    (("chinese", "mandarin", "zh"), "cmn", "zf_xiaoxiao", "zm_yunxi"),
]
WORDS_PER_S = 2.5
QUALITY = {
    "test": {"resolution": "480p", "max_total": 15, "default_total": 12,
             "paid_lipsync": ["kling_avatar", "longcat", "infinitalk"]},
    "full": {"resolution": "720p", "max_total": 180, "default_total": 0,  # 0: the format's own default
             "paid_lipsync": ["kling_avatar_pro", "longcat", "infinitalk"]},
}
FORMATS = {
    "youtube_explainer": {
        "label": "YouTube explainer", "default_s": 60, "effects": "explainer",
        "look": "medium close-up, presenter seated at a desk in a tidy home studio, soft key light from one side, warm "
                "practical lamp and shelves softly out of focus behind, eye-level camera, 50mm, shallow depth of field",
        "style": "Hook in the first sentence (a question or a surprising fact), then one idea per sentence in a clear "
                 "order, concrete examples, and a short wrap-up with a call to subscribe or comment. Friendly, "
                 "confident, conversational - like a good YouTuber, not a lecture.",
        "motion": "talking naturally to the camera, expressive face, subtle head movement, natural blinking, hands "
                  "occasionally gesturing to explain, steady locked-off camera",
    },
    "ugc_ad": {
        "label": "UGC ad", "default_s": 30, "effects": "ugc_ad",
        "look": "vertical selfie-style phone video frame at arm's length, real home (kitchen, bedroom or car), natural "
                "window light, slightly imperfect handheld framing, authentic and unpolished, not a studio",
        "style": "A real creator, not an actor: a scroll-stopping hook in the first 2 seconds, the problem they had, "
                 "how they found the product, what changed (specific and honest), and a casual recommendation with a "
                 "call to action. Short sentences, everyday words, genuine enthusiasm.",
        "motion": "talking excitedly to a phone camera, natural selfie-style handheld sway, animated expressions, "
                  "natural blinking, nodding, occasionally lifting the product toward the camera",
    },
    "testimonial": {
        "label": "Testimonial", "default_s": 30, "effects": "ugc_ad",
        "look": "medium close-up interview framing, presenter slightly off-centre, soft natural light, real "
                "environment softly out of focus, eye-level camera",
        "style": "A sincere first-person story: the situation before, the moment things changed, the result in "
                 "concrete terms, and why they recommend it.",
        "motion": "speaking sincerely to the camera, calm natural expressions, subtle head movement, natural blinking",
    },
    "spokesperson": {
        "label": "Spokesperson", "default_s": 30, "effects": "advertisement",
        "look": "medium shot, presenter standing in a clean modern space, professional soft lighting, eye-level camera, "
                "background softly out of focus",
        "style": "Clear, confident brand voice: hook, the key message with one or two proof points, and a call to "
                 "action.",
        "motion": "presenting confidently to the camera, open hand gestures, subtle head movement, natural blinking",
    },
}

SCRIPT_SYSTEM = """You write and cast talking-to-camera videos: YouTube explainers, UGC ads, testimonials and
spokesperson videos. The presenter is filmed in ONE continuous framing and speaks every word on camera.

Return ONLY JSON:
{"format": "youtube_explainer" | "ugc_ad" | "testimonial" | "spokesperson",
 "title": "2-5 word title in the script's language",
 "language": "the language the presenter speaks (the user's language unless they ask for another)",
 "voice_gender": "female" | "male",
 "presenter": "the presenter's look for a photo: apparent age range, build, hair, skin tone, wardrobe; a fictional person",
 "setting": "where they are, one sentence, fitting the format",
 "product_in_hand": "the product the presenter holds on camera, described for a photo (shape, material, colour), or \"\" when they hold nothing",
 "segments": [{"text": "what the presenter says", "mood": "one or two words, e.g. curious, excited, serious"}]}

Rules:
- The segments, read aloud at about 2.5 words per second, fill the requested length (within 10 %).
- Each segment is 1-3 sentences and at most 60 words; split at natural pauses.
- Write speech, not prose: short sentences, contractions, no lists, no stage directions, no emojis, no hashtags,
  no URLs, no text in brackets. Spell numbers the way they are said.
- Only facts the brief gives: never invent prices, statistics, awards or claims about the product. Keep claims
  modest and honest.
- When a photo of the presenter is given, describe that person as they look; never name or identify a real person.
- When the brief names a real person or celebrity as the presenter, cast a fictional look-alike-free presenter instead.
- If the user provides their own audio, return an empty "segments" list (their recording is the speech)."""


class PresenterError(RuntimeError):
    pass


def paid_allowed() -> bool:
    return os.getenv("PRESENTER_PAID", "off").strip().lower() in ("on", "1", "true", "yes")


def local_ready() -> str | None:
    """None when the local models are installed; otherwise what is missing."""
    need = {"local Python": LOCAL_PY, "Ditto runner": DITTO_RUN, "Kokoro runner": KOKORO_RUN,
            "Ditto models": DITTO_REPO / "checkpoints" / "ditto_pytorch" / "models" / "lmdm_v0.4_hubert.pth",
            "Kokoro model": KOKORO_MODELS / "kokoro-v1.0.int8.onnx"}
    missing = [k for k, v in need.items() if not Path(v).exists()]
    return f"not installed here ({', '.join(missing)} missing)" if missing else None


def quality() -> dict:
    q = QUALITY["test"] if story_engine.quality() is story_engine.QUALITY["test"] else QUALITY["full"]
    chain = ["ditto_local"] + (q["paid_lipsync"] if paid_allowed() else [])
    own = [m.strip() for m in os.getenv("LIPSYNC_MODELS", "").split(",") if m.strip() in LIPSYNC]
    return {**q, "lipsync": own or chain}


async def _subscribe(endpoint: str, args: dict, key: str) -> dict:
    import fal_client
    os.environ.setdefault("FAL_KEY", key)
    return await asyncio.to_thread(fal_client.subscribe, endpoint, arguments=args)


def _why(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:200]}"


def detect_format(prompt: str, production_types: list[str] | None = None) -> str:
    t = " " + (prompt or "").lower() + " "
    if any(w in t for w in ("ugc", "user generated", "user-generated", "creator ad", "tiktok", "influencer", "review video",
                             " ad ", " advert", "selfie")):
        return "ugc_ad"
    if "testimonial" in t:
        return "testimonial"
    if any(w in t for w in ("spokesperson", "brand presenter", "announcement")):
        return "spokesperson"
    return "youtube_explainer"


async def write_script(handoff: dict, fmt: str, total: int, refs: list[dict], own_audio: bool) -> dict:
    f = FORMATS[fmt]
    user = {
        "brief": handoff.get("prompt") or "",
        "suggested_format": fmt,
        "format_style": f["style"],
        "length_seconds": total,
        "target_words": int(total * WORDS_PER_S),
        "language_hint": handoff.get("language") or "",
        "uploaded_images": [{"role": r["role"], "what_it_shows": r["summary"]} for r in refs],
        "user_provides_own_audio": own_audio,
    }
    data = await llm.chat_json(config.REASONING_MODEL, [{"role": "system", "content": SCRIPT_SYSTEM},
                                                        {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
                               max_tokens=8000, stage="engine.presenter.script")
    segs = [{"text": str(s.get("text") or "").strip(), "mood": str(s.get("mood") or "").strip()}
            for s in (data.get("segments") or []) if isinstance(s, dict) and str(s.get("text") or "").strip()]
    if not segs and not own_audio:
        raise PresenterError("The script writer returned no lines.")
    data["segments"] = segs
    data["format"] = data.get("format") if data.get("format") in FORMATS else fmt
    data["voice_gender"] = "male" if str(data.get("voice_gender")).lower() == "male" else "female"
    return data


def _presenter_prompt(script: dict, fmt: str, with_person_ref: bool, with_product: bool) -> str:
    f = FORMATS[fmt]
    who = ("the person in the reference photo, keeping their face, hair, skin tone and build exactly"
           if with_person_ref else
           f"a fictional person (not a celebrity, not any real or named person): {script.get('presenter') or 'a friendly adult presenter'}")
    held = str(script.get("product_in_hand") or "").strip()
    prod = (" They hold the product from the reference photo naturally in one hand at chest height, label toward the "
            "camera, product exactly as in its photo." if with_product else
            f" They hold {held} naturally in one hand at chest height, clearly visible, no readable brand text."
            if held else "")
    return (f"Photorealistic photo of {who}. {script.get('setting') or ''} Framing and light: {f['look']}. "
            f"They look straight into the camera lens with a relaxed, natural expression, mouth gently closed, face "
            f"fully visible and well lit, head and shoulders clearly in frame.{prod} Real skin texture, natural "
            "imperfections, true-to-life colours. No text, no captions, no watermark, no logos added.")


async def presenter_image(app, script, fmt, ar, people: list[str], products: list[str], key, progress) -> str:
    """The presenter's photo: the user's own (as is when nothing has to change), else generated."""
    if people and not products:
        return people[0]
    await progress(f"{app['name']}: {'placing the product with your presenter' if people else 'casting the presenter'}")
    refs = (people[:1] + products[:3])
    prompt = _presenter_prompt(script, fmt, bool(people), bool(products))
    kw = {"aspect_ratio": ar, "num_images": 1, **({"image_urls": refs} if refs else {})}
    res = await story_engine._fal(fal_helper.generate_image, key, "nano_banana_pro", prompt, **kw)
    return story_engine._url(res, "image")


def _audio_url(res) -> str:
    url = ((res or {}).get("audio") or {}).get("url")
    if not url:
        raise PresenterError(f"The voice model returned no audio: {json.dumps(res, default=str)[:300]}")
    return url


def kokoro_voice(language: str, gender: str) -> tuple[str, str] | None:
    """(espeak language code, voice) for a script language Kokoro speaks; None for one it does not (e.g. Telugu)."""
    lang = (language or "english").strip().lower()
    for words, code, female, male in KOKORO_LANGS:
        if any(lang == w or lang.startswith(w + " ") or lang.startswith(w + "-") or lang.startswith(w + "(")
               or (len(w) > 2 and lang.startswith(w)) for w in words):
            return code, (male if gender == "male" else female)
    return None


async def run_local(cmd: list[str], what: str, timeout: int = LOCAL_TIMEOUT_S) -> dict:
    """Run a local model script; its last stdout line is a JSON result."""
    proc = await asyncio.create_subprocess_exec(*map(str, cmd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise PresenterError(f"{what} took longer than {timeout} s.")
    lines = [l for l in out.decode(errors="replace").splitlines() if l.startswith("{")]
    if proc.returncode != 0 or not lines:
        raise PresenterError(f"{what} failed: {(err.decode(errors='replace') or '').strip()[-400:]}")
    return json.loads(lines[-1])


async def speak(text: str, gender: str, language: str, key: str, dest: Path, used: list[str]) -> str:
    """One line of speech as a local file: Kokoro on this machine (free); ElevenLabs on fal only when paid is on."""
    kv = kokoro_voice(language, gender)
    problems = []
    if kv and local_ready() is None:
        try:
            await run_local([LOCAL_PY, KOKORO_RUN, "--models", KOKORO_MODELS, "--lang", kv[0], "--voice", kv[1],
                             "--text", text, "--out", dest.with_suffix(".wav")], "Kokoro", timeout=600)
            used.append("Kokoro (open source, local)")
            return str(dest.with_suffix(".wav"))
        except PresenterError as e:
            problems.append(str(e))
    else:
        problems.append(f"Kokoro does not speak {language}" if not kv else f"Kokoro is {local_ready()}")
    if not paid_allowed():
        raise PresenterError(f"No free voice for this script: {'; '.join(problems)}. Upload your own recording, or "
                             "set PRESENTER_PAID=on to use ElevenLabs.")
    res = await _subscribe(story_engine.TTS_MODEL, {"text": text, "voice": VOICES[gender]}, key)
    used.append("ElevenLabs")
    return await story_engine._download(_audio_url(res), dest.with_suffix(".mp3"))


def _ffmpeg() -> str:
    ff = video_merge.find_ffmpeg()
    if not ff:
        raise PresenterError("ffmpeg is not installed where VideoStudio runs.")
    return ff


def _run(cmd: list[str], what: str) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise PresenterError(f"{what} failed: {(p.stderr or '').strip()[-400:]}")


_SIL = re.compile(r"silence_(start|end): (-?\d+(?:\.\d+)?)")


def silences(ff: str, path: str) -> list[float]:
    """Midpoints of the pauses in the speech."""
    p = subprocess.run([ff, "-hide_banner", "-nostats", "-i", path, "-af", "silencedetect=noise=-35dB:d=0.2",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=300)
    out, start = [], None
    for kind, t in _SIL.findall(p.stderr or ""):
        if kind == "start":
            start = float(t)
        elif start is not None:
            out.append((start + float(t)) / 2)
            start = None
    return out


def cut_points(total: float, pauses: list[float], max_s: float = CHUNK_MAX_S) -> list[float]:
    """Where to cut speech of `total` seconds into chunks of at most max_s: at the latest pause in the second half of
    each window, else hard at max_s. Returns the chunk boundaries, starting with 0 and ending with total."""
    cuts = [0.0]
    while total - cuts[-1] > max_s:
        lo, hi = cuts[-1] + max_s * 0.5, cuts[-1] + max_s
        inside = [p for p in pauses if lo <= p <= hi]
        cuts.append(round(max(inside) if inside else hi, 3))
    cuts.append(round(total, 3))
    return cuts


async def chunk_audio(path: str, work: Path, tag: str) -> list[str]:
    """Split one speech file into lip-sync-sized chunks at its pauses (one chunk when it already fits)."""
    ff = _ffmpeg()
    total = await asyncio.to_thread(video_merge.probe_duration, ff, path)
    if total <= CHUNK_MAX_S:
        return [path]
    cuts = cut_points(total, await asyncio.to_thread(silences, ff, path))
    out = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        dest = work / f"{tag}_{i + 1:02d}.wav"
        await asyncio.to_thread(_run, [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-ss", f"{a:.3f}",
                                       "-to", f"{b:.3f}", "-ac", "1", "-ar", "44100", str(dest)], "Cutting the speech")
        out.append(str(dest))
    return out


def lipsync_args(model: str, image_url: str, audio_url: str, dur: float, prompt: str, resolution: str) -> dict:
    """Each model's own request shape, sized to the chunk's speech."""
    base = {"image_url": image_url, "audio_url": audio_url}
    if model.startswith("kling_avatar"):
        return {**base, "prompt": prompt}
    if model == "longcat":
        segs = 1 + max(0, math.ceil((dur - LONGCAT_FIRST_S) / LONGCAT_SEG_S))
        return {**base, "prompt": prompt, "num_segments": min(10, segs), "resolution": resolution}
    if model == "infinitalk":
        frames = max(41, min(721, math.ceil(dur * INFINITALK_FPS) + 4))
        return {**base, "prompt": prompt, "num_frames": frames, "resolution": resolution, "acceleration": "regular"}
    raise PresenterError(f"Unknown lip-sync model {model}.")


_LOCAL_LOCK = asyncio.Lock()  # one local render at a time: Ditto needs most of an 8 GB Mac's memory


async def lipsync(image_url: str, image_path: str, audio: str, prompt: str, q: dict, key: str, out: Path,
                  on_fallback=None) -> tuple[str, str]:
    """Talking video file for one chunk: Ditto on this machine first, then (if paid is on) fal's models.
    Returns (local path, model used)."""
    dur = await asyncio.to_thread(video_merge.probe_duration, _ffmpeg(), audio)
    errors, audio_url = [], None
    for m in q["lipsync"]:
        spec = LIPSYNC[m]
        try:
            if spec["kind"] == "local":
                why = local_ready()
                if why:
                    raise PresenterError(f"Ditto is {why}")
                async with _LOCAL_LOCK:
                    await run_local([LOCAL_PY, DITTO_RUN, "--repo", DITTO_REPO, "--image", image_path,
                                     "--audio", audio, "--out", out, "--device", LOCAL_DEVICE], "Ditto")
                return str(out), m
            audio_url = audio_url or await story_engine._upload(audio)
            res = await _subscribe(spec["endpoint"], lipsync_args(m, image_url, audio_url, dur, prompt, q["resolution"]), key)
            return await story_engine._download(story_engine._url(res, "video"), out), m
        except Exception as e:  # not installed, unavailable, refused or broken: the next model takes over
            errors.append(f"{spec['label']}: {_why(e)}")
            log.warning("lip-sync %s failed: %s", m, e)
            if on_fallback and m != q["lipsync"][-1]:
                await on_fallback(spec["label"], e)
    hint = "" if paid_allowed() else " (paid fal models are off; set PRESENTER_PAID=on to fall back to them)"
    raise PresenterError("Every lip-sync model failed - " + "; ".join(errors) + hint)


def fit_clip(ff: str, clip: str, audio: str, out: str, w: int, h: int, punch_in: bool) -> None:
    """The clip at the film's size with exactly its own speech: held on its last frame if a few frames short, cut at
    the end of the speech, and (on every other chunk) pushed in 12 % for a creator-style jump cut."""
    dur = video_merge.probe_duration(ff, audio)
    crop = "crop=iw/1.12:ih/1.12," if punch_in else ""
    vf = (f"[0:v]{crop}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=30,"
          f"tpad=stop_mode=clone:stop_duration=3[v]")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", clip, "-i", audio, "-filter_complex", vf,
          "-map", "[v]", "-map", "1:a", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out], "Syncing a chunk")


async def run(app: dict, handoff: dict, assets: dict, work: Path, progress) -> dict:
    """Make the talking video. Returns {"path", "duration_s", "title", "format", "script", "presenter_url", "chunks"}."""
    key = story_engine.fal_key()
    q = quality()
    ff = _ffmpeg()
    work.mkdir(parents=True, exist_ok=True)
    req = handoff.get("request") or {}
    ar = req.get("aspect_ratio") or None

    people, products, refs, own_audio = [], [], [], None
    local_photo = {}  # uploaded url -> the file on disk
    for aid, a in assets.items():
        if not a.get("path"):
            continue
        role = a.get("role") or "unclear"
        if a.get("modality") == "audio" or role == "audio_track":
            own_audio = own_audio or a["path"]
        elif a.get("modality") == "image":
            await progress(f"{app['name']}: uploading {a['filename']}")
            url = await story_engine._upload(a["path"])
            local_photo[url] = a["path"]
            refs.append({"role": role, "summary": (a.get("summary") or "")[:400]})
            summary = (a.get("summary") or "").lower()
            if role == "character" or (role == "unclear" and any(w in summary for w in ("person", "man", "woman", "face"))):
                people.append(url)
            elif role in ("product", "logo"):
                products.append(url)

    fmt = detect_format(handoff.get("prompt") or "", req.get("production_type"))
    ar = ar or ("9:16" if fmt == "ugc_ad" else "16:9")
    given = req.get("duration_given") and req.get("duration")
    total = int(given or q["default_total"] or FORMATS[fmt]["default_s"])
    total = max(5, min(total, q["max_total"]))

    await progress(f"{app['name']}: writing the script")
    script = await write_script(handoff, fmt, total, refs, bool(own_audio))
    fmt = script["format"]
    f = FORMATS[fmt]

    image_url = await presenter_image(app, script, fmt, ar, people, products, key, progress)
    show = getattr(progress, "live", None)
    if show and str(image_url).startswith("http"):  # the live view shows the real presenter while they are lip-synced
        try:
            await show({"presenter_url": image_url, "title": script.get("title") or ""})
        except Exception as e:
            log.warning("live update failed: %s", e)
    image_path = local_photo.get(image_url) or await story_engine._download(image_url, work / "presenter.png")
    small = work / "presenter_local.png"  # the local lip-sync's input: at most LOCAL_MAX_SIDE px, even sides
    await asyncio.to_thread(_run, [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", image_path, "-vf",
                                   f"scale='min({LOCAL_MAX_SIDE},iw)':'min({LOCAL_MAX_SIDE},ih)'"
                                   ":force_original_aspect_ratio=decrease:force_divisible_by=2", str(small)],
                            "Preparing the presenter photo")
    image_path = str(small)

    # Speech: the user's recording, or one voice line per segment.
    if own_audio:
        await progress(f"{app['name']}: preparing your audio")
        speech = [own_audio]
        moods = [""]
    else:
        await progress(f"{app['name']}: recording the voice ({len(script['segments'])} lines)")
        voices_used: list[str] = []
        speech = []
        for i, s in enumerate(script["segments"]):  # one at a time: the local voice shares the CPU
            speech.append(await speak(s["text"], script["voice_gender"], str(script.get("language") or ""), key,
                                      work / f"line_{i + 1:02d}", voices_used))
        moods = [s["mood"] for s in script["segments"]]
    chunks, chunk_moods = [], []
    for i, (sp, mood) in enumerate(zip(speech, moods)):
        parts = await chunk_audio(sp, work, f"speech_{i + 1:02d}")
        chunks += parts
        chunk_moods += [mood] * len(parts)
    if not own_audio:  # a short test run keeps only what fits its budget
        kept, t = [], 0.0
        for c, m in zip(chunks, chunk_moods):
            d = await asyncio.to_thread(video_merge.probe_duration, ff, c)
            if kept and t + d > q["max_total"] + 3:
                break
            kept.append((c, m))
            t += d
        chunks, chunk_moods = [c for c, _ in kept], [m for _, m in kept]

    # Lip-sync every chunk from the same photo (local renders one at a time, fal ones in parallel).
    n = len(chunks)
    done = [0]

    models_used: list[str] = []

    async def one(i: int, audio: str, mood: str) -> str:
        prompt = f"{f['motion']}{', ' + mood if mood else ''}. {script.get('setting') or ''}".strip()

        async def fell_back(label, e):
            await progress(f"{app['name']}: part {i + 1}: {label} failed ({_why(e)[:80]}); trying the next model")

        path, used = await lipsync(image_url, image_path, audio, prompt, q, key, work / f"talk_{i + 1:02d}.mp4",
                                   fell_back)
        models_used.append(used)
        done[0] += 1
        await progress(f"{app['name']}: lip-sync {done[0]}/{n} done ({LIPSYNC[used]['label']})")
        return path

    await progress(f"{app['name']}: lip-syncing {n} part{'s' if n > 1 else ''} with {LIPSYNC[q['lipsync'][0]]['label']}")
    clips = await asyncio.gather(*(one(i, c, m) for i, (c, m) in enumerate(zip(chunks, chunk_moods))))

    await progress(f"{app['name']}: editing")
    w, h = video_merge.dimensions_for(ar, "1080p" if q["resolution"] == "720p" else "720p")
    fitted = []
    for i, (clip, audio) in enumerate(zip(clips, chunks)):
        out = str(work / f"fit_{i + 1:02d}.mp4")
        await asyncio.to_thread(fit_clip, ff, clip, audio, out, w, h, punch_in=i % 2 == 1)
        fitted.append(out)
    final = str(work / "presenter.mp4")
    if len(fitted) == 1:
        os.replace(fitted[0], final)
    else:
        await asyncio.to_thread(video_merge.merge_videos, ff, fitted, final, w, h)

    # Music under the presenter (no prompt needed): low while they talk, back up in every pause.
    final, music_prompt = await music.add_to(final, handoff.get("prompt") or "", f["effects"], True,
                                             work / "music", progress, app["name"])
    effects = None
    # A person talking to camera always gets effects and motion design (motion_design.finish_level).
    style = story_engine.EFFECT_STYLE.get(f["effects"]) or story_engine.EFFECT_STYLE["ugc_ad"]
    if handoff.get("effects", True) and effects_engine.available() is None:
        try:
            effects = await effects_engine.run({**app, "name": f"{app['name']} effects"},
                                               {"prompt": handoff.get("prompt") or "", "effects_style": style,
                                                "allow_none": False}, final, work / "effects", progress)
            if not effects.get("skipped"):
                final = effects["path"]
        except Exception as e:  # effects are a finishing touch
            log.warning("presenter effects failed: %s", e)
            effects = {"error": str(e)[:300], "plan": []}

    # Motion design after the effects, decided from the finished video: the graphics director looks at it and adds
    # product callouts, device diagrams or kinetic type only when they help (it returns "no graphics" otherwise).
    graphics = None
    if q.get("graphics", True) and handoff.get("graphics", True):
        genre = f["effects"] if f["effects"] in genres.GENRES else genres.detect(handoff.get("prompt") or "")
        lines = [s["text"] for s in script["segments"]] if not own_audio else []
        film = {"title": script.get("title") or "", "story": handoff.get("prompt") or "", "genre": genre,
                "genre_label": f["label"],
                "scenes": [{"title": f"Part {i + 1}", "beat": lines[i] if i < len(lines) else "",
                            "action": "the presenter talks to camera"} for i in range(len(fitted))]}
        busy = [(float(e["start"]), float(e["end"]), e["fx"]) for e in (effects or {}).get("plan") or []]
        try:
            graphics = await finishing.run(app, handoff.get("prompt") or "", film, final, fitted, lines, ar,
                                           work / "graphics", progress, q, busy=busy, level="full",
                                           captions=(effects or {}).get("captions"))
            final = graphics["path"]
        except Exception as e:  # graphics are a finishing touch: the talking video is still delivered
            log.warning("presenter graphics failed: %s", e)
            await progress(f"{app['name']}: motion graphics could not be added ({str(e)[:160]}); delivering without them")
            graphics = {"error": str(e)[:300]}
    return {"path": final, "graphics": graphics, "duration_s": round(await asyncio.to_thread(video_merge.probe_duration, ff, final), 2),
            "title": script.get("title") or "", "format": fmt, "format_label": f["label"],
            "script": [s["text"] for s in script["segments"]] if not own_audio else [],
            "presenter_url": image_url, "generated_presenter": not (people and not products),
            "chunks": n, "aspect_ratio": ar, "effects": effects, "music": music_prompt,
            "lipsync_models": sorted({LIPSYNC[m]["label"] for m in models_used}),
            "voice_models": sorted(set(voices_used)) if not own_audio else ["your recording"]}
