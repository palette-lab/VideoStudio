"""Drives the Three.js renderer in tools/render3d: stills for a quick look, then the RGB video and every control pass
(depth, normal, character masks, object masks) from the same frames, plus per-frame metadata (camera, positions, pose
keypoints, screen boxes, active events)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUNTIME = Path(os.getenv("RENDER3D_DIR", ROOT / "tools" / "render3d"))
CHROME = os.getenv("RENDER3D_CHROME", os.getenv("OPUSCAR_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
PASSES = ("rgb", "depth", "normal", "mask_characters", "mask_objects")
RENDER_TIMEOUT_S = int(os.getenv("RENDER3D_TIMEOUT_S", "3600"))
MODEL_EXT = {".glb", ".gltf", ".fbx", ".obj"}


class RenderError(RuntimeError):
    pass


def available() -> str | None:
    """None when the renderer can run here; otherwise what is missing."""
    if not (RUNTIME / "render.mjs").exists() or not (RUNTIME / "scene" / "engine.js").exists():
        return f"the 3D renderer is not installed ({RUNTIME})"
    for pkg in ("three", "playwright-core"):
        if not (RUNTIME / "node_modules" / pkg).exists():
            return f"{pkg} is not installed (cd {RUNTIME} && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install)"
    if not shutil.which("node"):
        return "Node.js is not installed"
    if not shutil.which("ffmpeg"):
        return "ffmpeg is not installed"
    if not os.path.exists(CHROME):
        return f"Chrome is not at {CHROME} (set RENDER3D_CHROME)"
    return None


async def _node(args: list[str], timeout: int) -> tuple[int, str]:
    env = {**os.environ, "RENDER3D_CHROME": CHROME}
    proc = await asyncio.create_subprocess_exec("node", str(RUNTIME / "render.mjs"), *args, cwd=str(RUNTIME), env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out.decode(errors="replace")


def _error(text: str) -> str:
    m = re.search(r"RENDER3D_ERROR (.*)", text)
    return (m.group(1) if m else text.strip()[-500:])[:600]


def write_spec(film: Path, spec: dict, assets: dict[str, str] | None = None) -> Path:
    """The film folder the renderer reads: spec.json and any model files the props use (assets/<name>)."""
    film.mkdir(parents=True, exist_ok=True)
    for name, src in (assets or {}).items():
        dest = film / "assets" / name
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            shutil.copyfile(src, dest)
    (film / "spec.json").write_text(json.dumps(spec, ensure_ascii=False))
    return film


async def stills(film: Path, times: list[float], out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("still_*.jpg"):
        f.unlink()
    code, text = await _node([str(film), "--out", str(out), "--stills", ",".join(f"{t:.2f}" for t in times)], 900)
    if code != 0:
        raise RenderError("the 3D scene failed to draw: " + _error(text))
    return sorted(out.glob("still_*.jpg"), key=lambda p: float(p.stem[6:]))


async def render(film: Path, out: Path, passes: tuple[str, ...] = PASSES) -> dict:
    """Every pass as its own MP4 (same size, rate and frame count) + metadata.json + info.json."""
    if out.exists():
        shutil.rmtree(out)
    code, text = await _node([str(film), "--out", str(out), "--passes", ",".join(passes)], RENDER_TIMEOUT_S)
    if code != 0:
        raise RenderError("the 3D render failed: " + _error(text))
    files = {p: str(out / f"{p}.mp4") for p in passes if (out / f"{p}.mp4").exists()}
    if "rgb" not in files:
        raise RenderError("the 3D render made no RGB video")
    meta = json.loads((out / "metadata.json").read_text())
    info = json.loads((out / "info.json").read_text())
    warn = (out / "warnings.txt").read_text() if (out / "warnings.txt").exists() else ""
    return {"passes": files, "metadata": str(out / "metadata.json"), "info": info, "meta": meta, "warnings": warn}


def probe(path: str) -> dict:
    """{"width","height","fps","frames","duration","audio"} from ffprobe (zeros when it cannot read the file)."""
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,width,height,r_frame_rate,nb_frames:format=duration", "-of", "json", path],
                           capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
    except Exception:  # noqa: BLE001
        return {"width": 0, "height": 0, "fps": 0.0, "frames": 0, "duration": 0.0, "audio": False}
    v = next((s for s in d.get("streams") or [] if s.get("codec_type") == "video"), {})
    num, _, den = str(v.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "fps": round(fps, 3),
            "frames": int(v.get("nb_frames") or 0), "duration": float((d.get("format") or {}).get("duration") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in d.get("streams") or [])}
