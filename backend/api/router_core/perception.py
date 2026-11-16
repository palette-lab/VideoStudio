"""Multimodal perception: turns each uploaded file into a structured report. Originals are never modified."""

import asyncio
import json
import re
import subprocess
from pathlib import Path

from . import config, llm, registry

IMAGE_PROMPT = f"""You are the perception stage of a video-application router. Describe this image factually.
Only report what is visible. If unsure, say so in "uncertain". Return JSON:
{{"summary": "one sentence",
  "tags": [subset of {registry.ASSET_TAGS} that clearly apply],
  "people": ["short description of each visible person"],
  "garments": ["each visible clothing item with colour/material"],
  "products": ["each product, with visible components if relevant"],
  "objects": ["other notable objects"],
  "scene": "setting / background",
  "text_in_image": ["visible text or logos"],
  "style": "photo / render / illustration / sketch, lighting, mood",
  "uncertain": ["things you could not determine"]}}"""

VIDEO_PROMPT = f"""You are the perception stage of a video-application router. These frames were sampled from ONE video,
one per detected shot, labelled with timestamps. Describe the video factually; do not invent what is not visible. Return JSON:
{{"summary": "one or two sentences",
  "tags": [subset of {registry.ASSET_TAGS} that clearly apply],
  "shots": [{{"start_s": number, "end_s": number, "description": "what happens", "camera": "framing / movement if inferable"}}],
  "people": ["..."], "garments": ["..."], "products": ["..."],
  "actions": ["main actions"],
  "style": "live action / animation / screen recording, look and pacing",
  "quality_notes": ["visible problems: blur, noise, shake, dark, low resolution"],
  "uncertain": ["things you could not determine from the frames"]}}"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def probe(path: Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
              "-of", "json", str(path)])
    if r.returncode != 0:
        return {}
    data = json.loads(r.stdout or "{}")
    out = {"duration_s": round(float(data.get("format", {}).get("duration", 0) or 0), 2), "streams": []}
    for s in data.get("streams", []):
        out["streams"].append(s.get("codec_type"))
        if s.get("codec_type") == "video" and "width" in s:
            out.update(width=s["width"], height=s["height"], fps=s.get("r_frame_rate"))
    return out


def _to_jpeg(src: Path, dst: Path, at: float | None = None) -> bool:
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if at is not None:
        cmd += ["-ss", f"{at:.2f}"]
    cmd += ["-i", str(src), "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", "-q:v", "3", str(dst)]
    return _run(cmd).returncode == 0 and dst.exists()


def detect_cuts(path: Path, threshold: float = 0.35) -> list[float]:
    r = _run(["ffmpeg", "-v", "info", "-i", str(path), "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    return sorted({round(float(m), 2) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)})


def _vision(deep: bool) -> dict:
    return {"model": config.DEEP_VISION_MODEL, "effort": "high"} if deep else {"model": config.VISION_MODEL, "effort": "low"}


async def perceive_image(asset: dict, work: Path, deep: bool = False) -> dict:
    jpg = work / f"{asset['id']}_view.jpg"
    if not _to_jpeg(Path(asset["path"]), jpg):
        return {"error": "Could not decode this image."}
    v = _vision(deep)
    report = await llm.chat_json(v["model"], [{"role": "user", "content": [
        {"type": "text", "text": IMAGE_PROMPT}, llm.image_part(jpg)]}], max_tokens=3000, reasoning_effort=v["effort"],
        stage="perception.image")
    report["model"] = v["model"]
    report["tags"] = [t for t in report.get("tags", []) if t in registry.ASSET_TAGS]
    return report


async def perceive_video(asset: dict, work: Path, deep: bool) -> dict:
    src = Path(asset["path"])
    meta = probe(src)
    duration = meta.get("duration_s", 0)
    if not duration or "video" not in meta.get("streams", []):
        return {"error": "Could not read this video.", "metadata": meta}
    cuts = await asyncio.to_thread(detect_cuts, src)
    bounds = [0.0] + [c for c in cuts if 0.3 < c < duration - 0.3] + [duration]
    shots = list(zip(bounds[:-1], bounds[1:]))
    limit = 12 if deep else 6
    if len(shots) > limit:  # keep evenly spaced shots
        step = len(shots) / limit
        shots = [shots[int(i * step)] for i in range(limit)]
    content = [{"type": "text", "text": VIDEO_PROMPT + f"\nVideo duration: {duration}s. Detected cuts at: {cuts[:50]}"}]
    for i, (a, b) in enumerate(shots):
        jpg = work / f"{asset['id']}_shot{i}.jpg"
        if _to_jpeg(src, jpg, at=(a + b) / 2):
            content.append({"type": "text", "text": f"Shot {i + 1}: {a:.2f}s–{b:.2f}s (frame at {(a + b) / 2:.2f}s)"})
            content.append(llm.image_part(jpg))
    v = _vision(deep)
    report = await llm.chat_json(v["model"], [{"role": "user", "content": content}], max_tokens=5000, reasoning_effort=v["effort"],
                                 stage="perception.video")
    report["model"] = v["model"]
    report["tags"] = [t for t in report.get("tags", []) if t in registry.ASSET_TAGS]
    report["metadata"] = meta
    report["detected_cuts_s"] = cuts
    if "audio" in meta.get("streams", []):
        report["speech"] = await _transcribe_track(src, work, asset["id"])
    return report


async def _transcribe_track(src: Path, work: Path, aid: str) -> dict:
    wav = work / f"{aid}_audio.wav"
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", "600", "-ac", "1", "-ar", "16000", str(wav)])
    if r.returncode != 0:
        return {"error": "Could not extract audio."}
    try:
        t = await llm.transcribe(wav)
        text = (t.get("text") or "").strip()
        # The gateway's "language" field always reports "ko" (verified with English speech), so it is not used.
        segments = [{"start_s": g.get("start"), "end_s": g.get("end"), "text": g.get("text", "").strip()}
                    for g in t.get("segments", [])][:200]
        return {"transcript": text, "segments": segments, "has_speech": bool(text)}
    except llm.LLMError as e:
        return {"error": str(e)}


async def perceive_audio(asset: dict, work: Path) -> dict:
    meta = probe(Path(asset["path"]))
    if not meta.get("duration_s"):
        return {"error": "Could not read this audio file.", "metadata": meta}
    speech = await _transcribe_track(Path(asset["path"]), work, asset["id"])
    summary = (f"Speech: \"{speech['transcript'][:200]}\"" if speech.get("has_speech")
               else "No speech detected (likely music or sound effects; genre not analysed).")
    return {"summary": f"{meta['duration_s']}s audio. {summary}", "tags": [], "metadata": meta, "speech": speech,
            "uncertain": [] if speech.get("has_speech") else ["music style / mood was not analysed"]}


async def perceive(asset: dict, work: Path, deep: bool = False) -> dict:
    try:
        if asset["modality"] == "image":
            return await perceive_image(asset, work, deep)
        if asset["modality"] == "video":
            return await perceive_video(asset, work, deep)
        if asset["modality"] == "audio":
            return await perceive_audio(asset, work)
        return {"error": f"Unsupported file type: {asset.get('content_type')}"}
    except llm.LLMError as e:
        return {"error": f"Analysis failed: {e}"}
