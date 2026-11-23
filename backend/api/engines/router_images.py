"""Images through the LLM Router's native media API (router-api.pltt.ai): the router holds the provider keys and
bills them, so the app needs no fal balance of its own.

    POST {origin}/image        text to image     (no inputs)
    POST {origin}/image/edit   image(s) to image (inputs: reference images by URL)

Both answer with Server-Sent Events; the picture arrives in an `image_done` event (`data.url`), a failure in an
`error` event. A provider behind one model can be out of credit while another works, so each call walks a short
chain of models and takes the first picture any of them returns.
"""

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("palette_video.router_images")

DEFAULT_ORIGIN = "https://router-api.pltt.ai"
# Nano Banana Pro first (fal, the router's keys), then Gemini direct, then Nano Banana 2.
TEXT_MODELS = [m.strip() for m in os.getenv("ROUTER_IMAGE_MODELS", "image-production,image-staging,image-default")
               .split(",") if m.strip()]
# Edits: Nano Banana Pro, Nano Banana 2, Gemini direct (no fal at all), then Runware's Nano Banana (another provider).
EDIT_MODELS = [m.strip() for m in os.getenv("ROUTER_EDIT_MODELS",
                                            "image-edit-production,image-edit-default,image-edit-staging,"
                                            "palette-runware-fallback-image")
               .split(",") if m.strip()]
# How each provider takes an inline (base64) picture - checked against the router 2026-11-20: the fal-backed models
# reject the "base64" field but read a data: URL in "url"; Gemini direct is the other way round; Runware takes only
# http(s) URLs (it is skipped for a call with an inline picture).
BASE64_FIELD = {m.strip() for m in os.getenv("ROUTER_BASE64_MODELS", "image-edit-staging").split(",") if m.strip()}
URL_ONLY = {m.strip() for m in os.getenv("ROUTER_URL_ONLY_MODELS", "palette-runware-fallback-image").split(",") if m.strip()}
TIMEOUT_S = 300


class RouterImageError(RuntimeError):
    pass


def key() -> str:
    return os.getenv("LLM_ROUTER_API_KEY", "").strip()


def available() -> bool:
    return bool(key())


def origin() -> str:
    """The router's origin, whatever path LLM_ROUTER_BASE_URL was written with (e.g. .../image/edit)."""
    raw = os.getenv("LLM_ROUTER_BASE_URL", "").strip() or DEFAULT_ORIGIN
    u = urlsplit(raw if "://" in raw else f"https://{raw}")
    return f"{u.scheme}://{u.netloc}" if u.netloc else DEFAULT_ORIGIN


def data_url(path: Path, max_side: int = 1536) -> str:
    """A local picture as an inline data: URL (JPEG, at most max_side px), for when it cannot be uploaded anywhere."""
    import base64
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "ref.jpg"
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-vf",
                            f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease",
                            "-q:v", "3", "-frames:v", "1", str(out)], capture_output=True, timeout=120)
        raw, mime = (out.read_bytes(), "image/jpeg") if r.returncode == 0 and out.exists() else \
            (path.read_bytes(), "image/png" if path.suffix.lower() == ".png" else "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _input(ref: str, i: int, model: str = "") -> dict:
    role = "style_reference" if i else "subject"
    if ref.startswith("data:") and model in BASE64_FIELD:  # an inline picture, in the form this provider reads
        head, _, b64 = ref.partition(",")
        return {"type": "image", "base64": b64, "mime_type": head[5:].split(";")[0] or "image/png", "role": role}
    return {"type": "image", "url": ref, "role": role}  # an http(s) URL, or a data: URL for the fal-backed models


def _body(prompt: str, model: str, aspect: str, refs: list[str]) -> dict:
    # aspect_ratio / num_images at the top level: the router translates them for whichever provider answers
    body = {"prompt": prompt, "model": model, "num_images": 1}
    if aspect and ":" in aspect:
        body["aspect_ratio"] = aspect
    if refs:
        body["inputs"] = [_input(u, i, model) for i, u in enumerate(refs)]
    return body


def parse_events(lines) -> tuple[str | None, str | None]:
    """(image url, error message) from the SSE `data:` lines of one job."""
    err = None
    for line in lines:
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        data = ev.get("data") or {}
        kind = ev.get("event") or data.get("type")
        if kind == "image_done" and data.get("url"):
            return data["url"], None
        if kind == "error":
            err = str(data.get("message") or data.get("error") or "the router reported an error")[:300]
    return None, err or "the router finished without an image"


async def _one(client: httpx.AsyncClient, path: str, body: dict) -> str:
    async with client.stream("POST", f"{origin()}{path}", json=body,
                             headers={"Authorization": f"Bearer {key()}", "Accept": "text/event-stream"}) as r:
        if r.status_code != 200:
            raise RouterImageError(f"{body['model']}: HTTP {r.status_code} {(await r.aread()).decode()[:200]}")
        lines = []
        async for line in r.aiter_lines():
            lines.append(line)
            if line.startswith("data:") and ('"image_done"' in line or '"event":"done"' in line
                                             or '"event":"error"' in line):
                url, err = parse_events(lines)
                if url or '"event":"error"' in line or '"event":"done"' in line:
                    break
        url, err = parse_events(lines)
        if not url:
            raise RouterImageError(f"{body['model']}: {err}")
        return url


async def generate(prompt: str, aspect: str = "16:9", refs: list[str] | None = None,
                   dest: Path | None = None) -> str:
    """One picture: text to image, or with reference image URLs an edit. Returns its URL (and saves it to dest)."""
    if not available():
        raise RouterImageError("LLM_ROUTER_API_KEY is not set")
    refs = [u for u in refs or [] if u]
    path, models = ("/image/edit", EDIT_MODELS) if refs else ("/image", TEXT_MODELS)
    problems = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=20)) as client:
        for model in models:
            if model in URL_ONLY and any(u.startswith("data:") for u in refs):
                problems.append(f"{model}: skipped (it takes only http(s) picture URLs)")
                continue
            try:
                url = await _one(client, path, _body(prompt, model, aspect, refs))
                if dest is not None:  # a picture that cannot be fetched counts as this model failing: try the next
                    r = await client.get(url, follow_redirects=True)
                    r.raise_for_status()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.content)
            except (RouterImageError, httpx.HTTPError) as e:
                problems.append(str(e)[:200])
                log.warning("router image on %s failed: %s", model, e)
                continue
            return url
    raise RouterImageError("no router image model made the picture: " + " | ".join(problems))
