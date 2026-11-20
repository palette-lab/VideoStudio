"""The video-to-video transformation: the 3D reference (RGB) becomes photoreal footage.

PRIMARY  Kling 4.0, fal `kling/v4/video-to-video/edit`: the reference is `video_url` (@Video1 in the prompt), 3-30 s,
         at least 700 px on each side, 24-60 fps; the output follows the source's length and aspect ratio.
FALLBACK Seedance 2.0, fal `bytedance/seedance-2.0/reference-to-video`: the reference rides in `video_urls` (@Video1);
         2-15 s per call and 640x640 to 834x1112 pixels, so longer films go in chunks cut at shot boundaries.
(Both checked against fal's OpenAPI schemas on 2026-11-20.)

Policy: Kling is always tried first. Seedance runs only when Kling fails - refused input, an unsupported format, an
unavailable or erroring service, or no result within the (long) timeout - never because Kling is merely slow, and
never alongside it. Depth, normal and mask passes are NOT sent: neither endpoint has a field for control videos.
The provider order can be changed only through RENDER3D_PRIMARY / RENDER3D_FALLBACK.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger("palette_video.render3d")

ENDPOINTS = {
    "kling_4_0": "kling/v4/video-to-video/edit",
    "seedance_2_0": "bytedance/seedance-2.0/reference-to-video",
    "seedance_2_0_fast": "bytedance/seedance-2.0/fast/reference-to-video",
}
LABELS = {"kling_4_0": "Kling 4.0 (video-to-video edit)", "seedance_2_0": "Seedance 2.0 (reference-to-video)",
          "seedance_2_0_fast": "Seedance 2.0 Fast (reference-to-video)"}
LIMITS = {  # seconds per call, pixel bounds of the input video
    "kling": {"min_s": 3.0, "max_s": 30.0, "min_side": 700, "max_side": 4553, "fps": (24, 60)},
    "seedance": {"min_s": 2.0, "max_s": 15.0, "min_px": 640 * 640, "max_px": 834 * 1112},
}
TIMEOUT_S = float(os.getenv("RENDER3D_PROVIDER_TIMEOUT_S", "1800"))  # a failure only after this long, never "slow"
TRANSIENT = re.compile(r"\b(5\d\d|timeout|timed out|temporar|connection|reset by peer|overloaded|try again)\b", re.I)


# The fal account itself refused (out of credit, locked, wrong key): every fal model shares it, so no fallback helps.
ACCOUNT = re.compile(r"exhausted balance|user is locked|top up|invalid key|not authori[sz]ed|unauthori[sz]ed|forbidden|"
                     r"\b40[13]\b", re.I)
ACCOUNT_HELP = ("the fal.ai account refused the request (out of credit or the key is locked/invalid) - top up at "
                "fal.ai/dashboard/billing or set a working FAL_KEY; Kling 4.0 and Seedance 2.0 both run on fal")


class TransformError(RuntimeError):
    def __init__(self, msg: str, attempts: list[dict] | None = None, account: bool = False):
        super().__init__(msg)
        self.attempts = attempts or []
        self.account = account  # the provider account, not the request, is the problem


def order() -> list[str]:
    """[primary, fallback] - Kling first unless explicitly configured otherwise."""
    p = os.getenv("RENDER3D_PRIMARY", "kling_4_0")
    f = os.getenv("RENDER3D_FALLBACK", "seedance_2_0")
    return [m for m in dict.fromkeys([p, f]) if m in ENDPOINTS and m != "off"]


def family(model: str) -> str:
    return "kling" if model.startswith("kling") else "seedance"


# ------------------------------------------------------------------------------------------------ inputs
def _ff(args: list[str], timeout: int = 900) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise TransformError(f"ffmpeg failed: {(r.stderr or '').strip()[-300:]}")


def fit_size(w: int, h: int, model: str) -> tuple[int, int]:
    """The input size a model accepts, keeping the aspect ratio (even numbers for H.264)."""
    import math
    down = lambda x: max(2, int(math.floor(x / 2)) * 2)
    up = lambda x: max(2, int(math.ceil(x / 2)) * 2)
    if family(model) == "kling":
        k = max(1.0, LIMITS["kling"]["min_side"] / min(w, h))
        k = min(k, LIMITS["kling"]["max_side"] / max(w, h))
        return (up(w * k), up(h * k)) if k > 1 else (down(w * k), down(h * k))
    lo, hi, px = LIMITS["seedance"]["min_px"], LIMITS["seedance"]["max_px"], w * h
    if px > hi:
        k = (hi / px) ** 0.5
        return down(w * k), down(h * k)
    if px < lo:
        k = (lo / px) ** 0.5
        return up(w * k), up(h * k)
    return down(w), down(h)


def prepare(src: str, model: str, dest: Path, t0: float = 0.0, t1: float | None = None) -> str:
    """The reference (or a window of it) conformed to what the model's endpoint accepts."""
    from .renderer import probe
    pr = probe(src)
    w, h = fit_size(pr["width"], pr["height"], model)
    fps = pr["fps"] or 24
    lo_fps, hi_fps = LIMITS["kling"]["fps"]
    fps = min(max(fps, lo_fps), hi_fps) if family(model) == "kling" else fps
    end = pr["duration"] if t1 is None else min(t1, pr["duration"])
    length = max(0.1, end - t0)
    min_s = LIMITS[family(model)]["min_s"]
    pad = max(0.0, min_s - length)  # too short for the endpoint: hold the last frame (trimmed off again afterwards)
    vf = f"scale={w}:{h}:flags=lanczos,fps={fps:g}" + (f",tpad=stop_mode=clone:stop_duration={pad + 0.05:.3f}" if pad else "")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ff(["-ss", f"{t0:.3f}", "-t", f"{length:.3f}", "-i", src, "-t", f"{length + pad + (0.05 if pad else 0):.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium",
         "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)])
    return str(dest)


def chunks(duration: float, shots: list[dict], max_s: float) -> list[tuple[float, float]]:
    """Windows of at most max_s seconds covering the film, cut at shot boundaries where possible."""
    if duration <= max_s + 1e-6:
        return [(0.0, duration)]
    cuts = sorted({round(float(s["t0"]), 3) for s in shots if 0 < float(s["t0"]) < duration})
    out, start = [], 0.0
    while duration - start > max_s + 1e-6:
        fits = [c for c in cuts if start + LIMITS["seedance"]["min_s"] <= c <= start + max_s]
        end = fits[-1] if fits else start + max_s
        if duration - end < LIMITS["seedance"]["min_s"]:  # never leave a tail too short for a call
            end = duration - LIMITS["seedance"]["min_s"]
        out.append((start, end))
        start = end
    out.append((start, duration))
    return out


# ------------------------------------------------------------------------------------------------ calls
def request(model: str, prompt: str, video_url: str, *, seconds: float, aspect: str, resolution: str,
            image_urls: list[str] | None = None) -> tuple[str, dict]:
    """(endpoint, arguments) exactly as the endpoint's schema names them."""
    if family(model) == "kling":
        args = {"prompt": prompt[:2500], "video_url": video_url, "resolution": "1080p" if resolution in ("1080p", "4k") else "720p",
                "keep_audio": False}
        if image_urls:
            args["image_urls"] = image_urls[:4]
        return ENDPOINTS[model], args
    dur = str(int(min(15, max(4, round(seconds)))))
    args = {"prompt": prompt[:2500], "video_urls": [video_url], "duration": dur, "generate_audio": True,
            "resolution": "1080p" if resolution in ("1080p", "4k") and model == "seedance_2_0" else "720p",
            "aspect_ratio": aspect if aspect in ("16:9", "9:16", "1:1") else "auto"}
    if image_urls:
        args["image_urls"] = image_urls[:8]
    return ENDPOINTS[model], args


async def _subscribe(endpoint: str, args: dict, key: str) -> dict:
    from ..vendor.flow import fal_helper
    client = fal_helper.client(key)
    return await asyncio.wait_for(asyncio.to_thread(client.subscribe, endpoint, arguments=args, with_logs=True,
                                                    client_timeout=TIMEOUT_S), TIMEOUT_S + 60)


def _video_url(res) -> str:
    v = (res or {}).get("video") if isinstance(res, dict) else None
    url = v.get("url") if isinstance(v, dict) else v if isinstance(v, str) else None
    if not url:
        raise TransformError(f"the provider returned no video ({str(res)[:200]})")
    return url


async def transform(ref_path: str, prompt: str, work: Path, *, aspect: str, resolution: str, shots: list[dict],
                    image_paths: list[str] | None = None, progress=None, label: str = "",
                    subscribe=None, upload=None, download=None, models: list[str] | None = None) -> dict:
    """Kling first; Seedance only if Kling fails. Returns {"path", "model", "label", "attempts", "chunks"}.
    `subscribe`, `upload`, `download` are injectable for tests (the defaults call fal)."""
    from .. import story_engine as se
    from .renderer import probe
    subscribe = subscribe or (lambda ep, a: _subscribe(ep, a, se.fal_key()))
    upload = upload or se._upload
    download = download or se._download
    prog = progress or (lambda m: asyncio.sleep(0))
    work.mkdir(parents=True, exist_ok=True)
    total = probe(ref_path)["duration"]
    try:
        imgs = [await upload(p) for p in (image_paths or [])[:4]]
    except Exception as e:  # noqa: BLE001
        if ACCOUNT.search(f"{type(e).__name__}: {e}"):
            raise TransformError(ACCOUNT_HELP, [{"model": m, "role": "primary" if k == 0 else "fallback", "status": "skipped",
                                                 "reason": "the reference images could not be uploaded to fal"}
                                                for k, m in enumerate(models or order())], account=True) from e
        raise TransformError(f"the reference images could not be uploaded: {type(e).__name__}: {str(e)[:200]}") from e
    attempts: list[dict] = []
    chain = models or order()
    if not chain:
        raise TransformError("no video-to-video model is configured (RENDER3D_PRIMARY / RENDER3D_FALLBACK)")
    for n, model in enumerate(chain):
        fam = family(model)
        role = "primary" if n == 0 else "fallback"
        wins = chunks(total, shots, LIMITS[fam]["max_s"])
        if fam == "kling" and total > LIMITS["kling"]["max_s"] + 0.05:
            attempts.append({"model": model, "role": role, "status": "skipped",
                             "reason": f"the film is {total:.1f} s; Kling 4.0 edits at most 30 s"})
            continue
        try:
            outs = []
            for k, (a, b) in enumerate(wins):
                src = prepare(ref_path, model, work / f"in_{model}_{k}.mp4", a, b)
                await prog(f"{label}{LABELS[model]}: uploading the 3D reference"
                           + (f" (part {k + 1}/{len(wins)})" if len(wins) > 1 else ""))
                url = await upload(src)
                ep, args = request(model, prompt, url, seconds=b - a, aspect=aspect, resolution=resolution, image_urls=imgs)
                tries = 2 if fam == "kling" else 1  # one retry for a transient Kling error before falling back
                for attempt in range(tries):
                    t0 = time.time()
                    await prog(f"{label}{LABELS[model]}: turning the 3D reference into realistic footage"
                               + (f" (part {k + 1}/{len(wins)})" if len(wins) > 1 else "") + (" - retry" if attempt else ""))
                    try:
                        se._count("clip", model)
                        res = await subscribe(ep, args)
                        break
                    except Exception as e:  # noqa: BLE001
                        if type(e).__name__ == "RunCancelled":
                            raise
                        attempts.append({"model": model, "role": role, "status": "error", "part": k + 1,
                                         "seconds": round(time.time() - t0, 1), "reason": f"{type(e).__name__}: {str(e)[:300]}"})
                        if attempt + 1 >= tries or not TRANSIENT.search(str(e)):
                            raise
                url_out = _video_url(res)
                dest = work / f"out_{model}_{k}.mp4"
                await download(url_out, dest)
                outs.append({"path": str(dest), "t0": a, "t1": b, "url": url_out, "src_s": probe(src)["duration"]})
            path = _assemble(outs, work / f"realistic_{model}.mp4", probe(ref_path))
            attempts.append({"model": model, "role": role, "status": "succeeded", "parts": len(outs)})
            return {"path": path, "model": model, "label": LABELS[model], "attempts": attempts, "chunks": outs,
                    "fallback_used": n > 0}
        except Exception as e:  # noqa: BLE001 - this model failed: the next in the chain takes over
            if type(e).__name__ in ("RunCancelled", "CancelledError"):
                raise
            if not attempts or attempts[-1].get("model") != model or attempts[-1].get("status") == "succeeded":
                attempts.append({"model": model, "role": role, "status": "error", "reason": f"{type(e).__name__}: {str(e)[:300]}"})
            if ACCOUNT.search(f"{type(e).__name__}: {e}"):  # the same fal account backs every model: stop here
                attempts[-1]["account"] = True
                for m in chain[n + 1:]:
                    attempts.append({"model": m, "role": "fallback", "status": "skipped",
                                     "reason": "not tried: it runs on the same fal account"})
                raise TransformError(ACCOUNT_HELP, attempts, account=True) from e
            log.warning("render3d: %s failed (%s)%s", model, str(e)[:200], "; trying the fallback" if n + 1 < len(chain) else "")
            if n + 1 < len(chain):
                await prog(f"{label}{LABELS[model]} failed ({str(e)[:120]}); using the fallback {LABELS[chain[n + 1]]}")
    raise TransformError("every video-to-video model failed: " + "; ".join(
        f"{a['model']}: {a.get('reason', a['status'])}" for a in attempts if a["status"] != "succeeded")[:600], attempts)


def _assemble(outs: list[dict], dest: Path, ref: dict) -> str:
    """The parts back to back, each time-fitted to its window of the reference (same clock as the 3D film)."""
    fitted = []
    for k, o in enumerate(outs):
        from .renderer import probe
        pr = probe(o["path"])
        want = o["t1"] - o["t0"]
        f = dest.with_name(f"{dest.stem}_fit{k}.mp4")
        src_s = o.get("src_s") or want  # the input sent (a short window was padded): map onto it, then trim
        speed = (pr["duration"] / src_s) if pr["duration"] and src_s else 1.0
        vf = f"setpts=PTS/{speed:.6f},fps={ref['fps'] or 24:g},scale={pr['width'] or ref['width']}:{pr['height'] or ref['height']}"
        args = ["-i", o["path"], "-filter_complex", f"[0:v]{vf}[v]" + (f";[0:a]atempo={min(2, max(0.5, speed)):.6f}[a]" if pr["audio"] else ""),
                "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "160k"] if pr["audio"] else []) + \
               ["-t", f"{want:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(f)]
        _ff(args)
        fitted.append(str(f))
    if len(fitted) == 1:
        os.replace(fitted[0], dest)
        return str(dest)
    from .renderer import probe
    size = probe(fitted[0])
    audio = all(probe(p)["audio"] for p in fitted)
    inputs = sum([["-i", p] for p in fitted], [])
    streams = "".join(f"[{i}:v]scale={size['width']}:{size['height']},setsar=1[v{i}];" for i in range(len(fitted)))
    cat = "".join(f"[v{i}]" + (f"[{i}:a]" if audio else "") for i in range(len(fitted)))
    _ff(inputs + ["-filter_complex", f"{streams}{cat}concat=n={len(fitted)}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""),
                  "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac"] if audio else []) +
        ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)])
    return str(dest)
