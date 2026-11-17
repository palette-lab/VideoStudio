"""Presenter Studio: a person talking to camera with lip-synced speech (YouTube explainers, UGC ads, testimonials,
spokesperson videos).

Pipeline:
1. Script: the LLM writes what the presenter says (sized to the length, ~2.5 words per second), who the presenter is
   and where they sit, in the format's style (YouTube explainer, UGC ad, testimonial, spokesperson).
2. Presenter: the user's photo of the person, or a generated, fictional person (never a real or named one). With a
   product photo, the presenter is generated holding the product.
3. Voice: the user's own audio file when given, otherwise the script spoken by Kokoro (hexgrad, Apache-2.0) running
   locally on this machine's CPU - free.
4. Lip-sync: the photo becomes one 10-second "talking to camera" base clip (Kling image-to-video, 720p or more), then
   every speech chunk (2-10 s, cut at pauses) is lip-synced onto its own window of that clip with Kling LipSync
   (fal-ai/kling-video/lipsync/audio-to-video), falling back to Sync Lipsync 1.9 (fal-ai/sync-lipsync). The chunks
   play as the jump cuts creators use, with a punch-in on every other chunk.
5. The chunks are joined, each with its own audio, so lips and voice stay in sync; Effects Studio then adds
   creator-style effects for the format when no later step does.

Paid on fal: the presenter photo (Nano Banana Pro), the base clip (Kling) and the lip-sync (Kling LipSync, Sync
Lipsync 1.9). The voice stays local and free (Kokoro); paid voice (ElevenLabs) only when PRESENTER_PAID=on.
"""

import asyncio
import json
import logging
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
LOCAL_PY = LOCAL_DIR / "vendor-src" / "ditto-talkinghead" / ".venv" / "bin" / "python"   # the venv Kokoro runs in
KOKORO_RUN = LOCAL_DIR / "local_lipsync" / "kokoro_tts.py"
KOKORO_MODELS = LOCAL_DIR / "vendor-src" / "kokoro-models"
LOCAL_TIMEOUT_S = int(os.getenv("PRESENTER_LOCAL_TIMEOUT", "3600"))
# Lip-sync on fal: video + speech in, the same video with the mouth synced out. Kling first, Sync 1.9 as fallback.
LIPSYNC = {
    "kling_lipsync": {"endpoint": "fal-ai/kling-video/lipsync/audio-to-video", "label": "Kling LipSync"},
    "sync_lipsync": {"endpoint": "fal-ai/sync-lipsync", "label": "Sync Lipsync 1.9"},
}
LIPSYNC_CHAIN = ["kling_lipsync", "sync_lipsync"]
SYNC_MODEL = "lipsync-1.9.0-beta"
# Kling LipSync takes a 2-10 s video (720-1920 px a side) and 2-60 s of speech: chunks are 2-10 s, cut at pauses.
CHUNK_MAX_S = 10.0
CHUNK_MIN_S = 2.0
BASE_CLIP_S = 10          # the talking base clip every chunk takes a window of
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
QUALITY = {  # base clip models: Kling first (720p+ for the lip-sync), Seedance at 720p as the fallback
    "test": {"resolution": "720p", "max_total": 15, "default_total": 12,
             "base_models": ["kling_3_0_standard", "seedance_2_0"]},
    "full": {"resolution": "1080p", "max_total": 180, "default_total": 0,  # 0: the format's own default
             "base_models": ["kling_3_0_pro", "kling_3_0_standard"]},
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
    """None when the local voice (Kokoro) is installed; otherwise what is missing."""
    need = {"local Python": LOCAL_PY, "Kokoro runner": KOKORO_RUN,
            "Kokoro model": KOKORO_MODELS / "kokoro-v1.0.int8.onnx"}
    missing = [k for k, v in need.items() if not Path(v).exists()]
    return f"not installed here ({', '.join(missing)} missing)" if missing else None


def quality() -> dict:
    q = QUALITY["test"] if story_engine.quality() is story_engine.QUALITY["test"] else QUALITY["full"]
    own = [m.strip() for m in os.getenv("LIPSYNC_MODELS", "").split(",") if m.strip() in LIPSYNC]
    return {**q, "lipsync": own or list(LIPSYNC_CHAIN)}


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


def cut_points(total: float, pauses: list[float], max_s: float = CHUNK_MAX_S,
               min_s: float = CHUNK_MIN_S) -> list[float]:
    """Where to cut speech of `total` seconds into chunks of at most max_s: at the latest pause in the second half of
    each window, else hard at max_s, never leaving a last piece shorter than min_s. Returns the chunk boundaries,
    starting with 0 and ending with total."""
    cuts = [0.0]
    while total - cuts[-1] > max_s:
        lo, hi = cuts[-1] + max_s * 0.5, min(cuts[-1] + max_s, total - min_s)
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


def lipsync_args(model: str, video_url: str, audio_url: str) -> dict:
    """Each lip-sync model's request: the talking clip and the speech (already the same length)."""
    if model == "kling_lipsync":
        return {"video_url": video_url, "audio_url": audio_url}
    if model == "sync_lipsync":
        return {"video_url": video_url, "audio_url": audio_url, "model": SYNC_MODEL, "sync_mode": "cut_off"}
    raise PresenterError(f"Unknown lip-sync model {model}.")


def base_prompt(f: dict, setting: str) -> str:
    """The base clip: the presenter talking to camera in one steady framing (the lip-sync then sets the words)."""
    return (f"The person talks to the camera the whole time, mouth moving naturally as if speaking, {f['motion']}. "
            f"{setting} One continuous shot, the same framing throughout, no cuts, no zoom, the face always fully "
            "visible and facing the camera, nothing covers the mouth. No text, no captions.").replace("  ", " ")


async def base_clip(app: dict, image_url: str, prompt: str, ar: str, q: dict, key: str, work: Path, progress) -> str:
    """One talking base clip from the presenter photo (720p or more, as Kling LipSync needs), local file."""
    errors = []
    for m in q["base_models"]:
        try:
            await progress(f"{app['name']}: filming the presenter ({BASE_CLIP_S} s base clip, {m})")
            vid = await story_engine._fal(fal_helper.generate_video, key, m, prompt, duration=str(BASE_CLIP_S),
                                          aspect_ratio=ar, start_image=image_url, audio=False,
                                          resolution=q["resolution"] if m.startswith("seedance") else None)
            return await story_engine._download(story_engine._url(vid, "video"), work / "presenter_base.mp4")
        except Exception as e:  # refused or unavailable: the next model takes over
            errors.append(f"{m}: {_why(e)}")
            log.warning("presenter base clip on %s failed: %s", m, e)
    raise PresenterError("The presenter could not be filmed - " + "; ".join(errors))


def window(ff: str, base: str, base_s: float, i: int, need: float, out: str) -> str:
    """Chunk i's own stretch of the base clip, `need` seconds long (at least 2 s), so the parts don't move alike;
    re-encoded at 720 px or more on the short side (Kling LipSync's minimum)."""
    need = max(CHUNK_MIN_S, min(need, base_s))
    room = max(0.0, base_s - need)
    start = round((i * 3.7) % (room + 1e-6), 3) if room > 0.05 else 0.0
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", base, "-t", f"{need:.3f}",
          "-an", "-vf", "scale='if(lt(iw,ih),max(720,iw),-2)':'if(lt(iw,ih),-2,max(720,ih))',setsar=1",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", out], "Cutting the base clip")
    return out


def pad_speech(ff: str, audio: str, dur: float, out: str) -> str:
    """Speech shorter than the lip-sync minimum gets silence after it (the edit cuts at the speech's real end)."""
    if dur >= CHUNK_MIN_S + 0.05:
        return audio
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", audio, "-af", f"apad=whole_dur={CHUNK_MIN_S + 0.1}",
          "-ac", "1", "-ar", "44100", out], "Padding a short line")
    return out


async def lipsync(video: str, audio: str, q: dict, key: str, out: Path, on_fallback=None) -> tuple[str, str]:
    """Talking video file for one chunk: its window of the base clip lip-synced to its speech, Kling first and
    Sync Lipsync 1.9 as the fallback. Returns (local path, model used)."""
    video_url = await story_engine._upload(video)
    audio_url = await story_engine._upload(audio)
    errors = []
    for m in q["lipsync"]:
        spec = LIPSYNC[m]
        try:
            res = await _subscribe(spec["endpoint"], lipsync_args(m, video_url, audio_url), key)
            return await story_engine._download(story_engine._url(res, "video"), out), m
        except Exception as e:  # unavailable, refused or broken: the next model takes over
            errors.append(f"{spec['label']}: {_why(e)}")
            log.warning("lip-sync %s failed: %s", m, e)
            if on_fallback and m != q["lipsync"][-1]:
                await on_fallback(spec["label"], e)
    raise PresenterError("Every lip-sync model failed - " + "; ".join(errors))


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
    for aid, a in assets.items():
        if not a.get("path"):
            continue
        role = a.get("role") or "unclear"
        if a.get("modality") == "audio" or role == "audio_track":
            own_audio = own_audio or a["path"]
        elif a.get("modality") == "image":
            await progress(f"{app['name']}: uploading {a['filename']}")
            url = await story_engine._upload(a["path"])
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

    # The presenter on film: one talking base clip from the photo; every chunk is lip-synced onto its own window of it.
    if not str(image_url).startswith("http"):
        image_url = await story_engine._upload(image_url)
    base = await base_clip(app, image_url, base_prompt(f, str(script.get("setting") or "")), ar, q, key, work, progress)
    base_s = await asyncio.to_thread(video_merge.probe_duration, ff, base)

    n = len(chunks)
    done = [0]

    models_used: list[str] = []

    async def one(i: int, audio: str, mood: str) -> str:
        async def fell_back(label, e):
            await progress(f"{app['name']}: part {i + 1}: {label} failed ({_why(e)[:80]}); trying the next model")

        dur = await asyncio.to_thread(video_merge.probe_duration, ff, audio)
        speech_in = await asyncio.to_thread(pad_speech, ff, audio, dur, str(work / f"speech_in_{i + 1:02d}.wav"))
        clip_in = await asyncio.to_thread(window, ff, base, base_s, i, max(dur, CHUNK_MIN_S),
                                          str(work / f"base_{i + 1:02d}.mp4"))
        path, used = await lipsync(clip_in, speech_in, q, key, work / f"talk_{i + 1:02d}.mp4", fell_back)
        models_used.append(used)
        done[0] += 1
        await progress(f"{app['name']}: lip-sync {done[0]}/{n} done ({LIPSYNC[used]['label']})")
        return path

    await progress(f"{app['name']}: lip-syncing {n} part{'s' if n > 1 else ''} with {LIPSYNC[q['lipsync'][0]]['label']}")
    clips = await asyncio.gather(*(one(i, c, m) for i, (c, m) in enumerate(zip(chunks, chunk_moods))))

    await progress(f"{app['name']}: editing")
    w, h = video_merge.dimensions_for(ar, "1080p" if q["resolution"] == "1080p" else "720p")
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
