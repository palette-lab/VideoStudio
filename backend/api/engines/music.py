"""Music bed for the films: composed locally with MusicGen (tools/local_music, free, on this Mac) and mixed under the
film. Music-led films (product films without a person, cinematic films) get it up front; films with narration or
someone talking get it lower, and it ducks automatically whenever the voice speaks.

The planner writes the music prompt ("music" in its plan) from the film's mood; when it gives none, the genre's
default is used. The user can switch it off by saying "no music" in the brief.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from .vendor.flow import video_merge

log = logging.getLogger("palette_video.engines")

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(os.getenv("MUSIC_LOCAL_DIR", ROOT / "tools"))
PY = TOOLS / "vendor-src" / "ditto-talkinghead" / ".venv" / "bin" / "python"  # the shared local venv (has torch)
RUNNER = TOOLS / "local_music" / "musicgen_run.py"
TIMEOUT_S = int(os.getenv("MUSIC_TIMEOUT", "1800"))
OFF = re.compile(r"\b(no|without)\s+(background\s+)?music\b|\bmusic\s*:\s*none\b|\bsilent\b", re.I)

# Default bed per genre: instrumental, no vocals (MusicGen sings badly), tempo for the cut rhythm.
GENRE_MUSIC = {
    "product_film": "minimal premium instrumental, soft airy synth pads, light glassy plucks, gentle deep pulse, "
                    "clean and modern, 100 bpm",
    "ugc_ad": "upbeat light instrumental pop, bright plucked guitar, soft claps, feel-good, 110 bpm",
    "advertisement": "cinematic modern instrumental, warm piano and strings, building percussion, uplifting, 96 bpm",
    "ecommerce": "bright modern instrumental, crisp percussion, warm synth chords, confident, 112 bpm",
    "unboxing": "playful light instrumental, plucky synths and soft percussion, curious, 105 bpm",
    "exploded_view": "precise electronic instrumental, ticking percussion, deep synth bass, technical, 100 bpm",
    "explainer": "light curious instrumental, soft marimba and piano, unobtrusive, 95 bpm",
    "fashion": "stylish deep house instrumental, minimal beat, chic, 118 bpm",
    "food": "warm acoustic instrumental, light guitar and percussion, cosy, 100 bpm",
    "travel": "airy uplifting instrumental, acoustic guitar and soft drums, wanderlust, 105 bpm",
    "real_estate": "elegant calm instrumental, piano and soft strings, spacious, 85 bpm",
    "action": "intense cinematic instrumental, driving drums and low strings, tension, 130 bpm",
    "documentary": "reflective instrumental, piano and soft strings, sincere, 80 bpm",
    "music_video": "energetic instrumental, full beat and bass, 120 bpm",
    "animation": "whimsical orchestral instrumental, light woodwinds and pizzicato, 100 bpm",
    "banner": "short punchy modern instrumental, bright synth stab and beat, 120 bpm",
    "cinematic": "emotional cinematic instrumental, piano and strings, slow build, 80 bpm",
}
NO_VOCALS = ", instrumental only, no vocals, no singing"


def available() -> str | None:
    if not PY.exists():
        return "the local model environment is missing (run tools/local_lipsync/setup.sh, then tools/local_music/setup.sh)"
    if not RUNNER.exists():
        return f"the music runner is missing at {RUNNER}"
    return None


def wanted(brief: str, audio_mode: str | None = None) -> bool:
    return not OFF.search(brief or "") and audio_mode != "silent"


def prompt_for(plan: dict) -> str:
    """The planner's music line when it wrote one, else the genre's default; always instrumental."""
    text = str(plan.get("music") or "").strip() or GENRE_MUSIC.get(plan.get("genre") or "", GENRE_MUSIC["cinematic"])
    text = re.sub(r"\b(with\s+)?([a-z-]+\s+)?(vocals?|singing|singers?|lyrics?)\b", "", text, flags=re.I)
    text = re.sub(r"\s*,(\s*,)*\s*", ", ", re.sub(r"\s{2,}", " ", text)).strip(" ,.")
    return text[:300] + NO_VOCALS


async def compose(prompt: str, seconds: float, out: Path) -> str:
    """Render the music with MusicGen locally; returns the wav path (raises on failure)."""
    why = available()
    if why:
        raise RuntimeError(why)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(str(PY), str(RUNNER), "--prompt", prompt, "--seconds",
                                                f"{seconds:.1f}", "--out", str(out),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"music took longer than {TIMEOUT_S} s")
    lines = raw.decode(errors="replace").strip().splitlines()
    try:
        res = json.loads(lines[-1])
    except (ValueError, IndexError):
        res = {"ok": False, "error": " | ".join(lines[-3:])[:300]}
    if not res.get("ok") or not out.exists():
        raise RuntimeError(res.get("error") or "music generation failed")
    return str(out)


def mix_filter(has_audio: bool, voiced: bool, total: float) -> str:
    """ffmpeg filter: the music bed under the film's own audio. With a voice, the music plays full whenever nobody is
    talking and drops well under the voice while someone speaks (sidechain ducking keyed on the film's audio)."""
    fade_out = max(0.0, total - 1.8)
    bed = (f"[1:a]aresample=48000,atrim=end={total:.3f},afade=t=in:d=0.4,afade=t=out:st={fade_out:.3f}:d=1.8,"
           f"volume={'0.7' if voiced else '0.85'}[m]")
    if not has_audio:
        return bed + ";[m]apad[mix]"
    if voiced:  # the film's audio (voice) is the key: about -18 dB under speech, back up in the pauses
        return (bed + ";[0:a]aresample=48000,asplit=2[a][key];"
                "[m][key]sidechaincompress=threshold=0.02:ratio=20:attack=10:release=600:makeup=1[md];"
                "[a][md]amix=inputs=2:normalize=0:duration=first[mix]")
    return bed + ";[0:a]aresample=48000,volume=0.55[a];[a][m]amix=inputs=2:normalize=0:duration=first[mix]"


async def mix(video: str, music_wav: str, voiced: bool, out: Path) -> str:
    ffmpeg = video_merge.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    total = await asyncio.to_thread(video_merge.probe_duration, ffmpeg, video)
    has_audio = await asyncio.to_thread(video_merge.has_audio_stream, ffmpeg, video)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video, "-i", music_wav,
           "-filter_complex", mix_filter(has_audio, voiced, total), "-map", "0:v", "-map", "[mix]", "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError("mixing the music failed: " + (proc.stderr or "")[-300:])
    return str(out)


async def add_to(video: str, brief: str, genre: str | None, voiced: bool, work: Path, progress, label: str,
                 prompt: str | None = None) -> tuple[str, str | None]:
    """Compose and mix a bed onto a finished video (no prompt needed from the user). Returns (path, music prompt);
    the video is returned unchanged when music is off, unavailable or fails."""
    if not wanted(brief):
        return video, None
    why = available()
    if why:
        await progress(f"{label}: no music ({why})")
        return video, None
    ffmpeg = video_merge.find_ffmpeg()
    try:
        work.mkdir(parents=True, exist_ok=True)
        total = await asyncio.to_thread(video_merge.probe_duration, ffmpeg, video)
        text = prompt or prompt_for({"genre": genre})
        await progress(f"{label}: composing the music")
        wav = await compose(text, total + 1.0, work / "music.wav")
        await progress(f"{label}: mixing the music")
        return await mix(video, wav, voiced, work / "with_music.mp4"), text
    except Exception as e:  # music is a finishing touch: the video is still delivered
        log.warning("music failed: %s", e)
        await progress(f"{label}: music could not be added ({str(e)[:140]})")
        return video, None
