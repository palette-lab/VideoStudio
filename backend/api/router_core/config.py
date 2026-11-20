"""Configuration for the routing core inside the Palette app.

Nothing is read from the repository layout or a .env file here: the Palette backend calls `configure(...)` with
values from `ctx.secret(...)` / `ctx.config_value(...)` at the start of every job, so the same code runs under
`pltt dev` and in the hosted runtime. Keys never reach the browser.
"""

import os
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REGISTRY_DIR = PACKAGE / "registry"
# Scratch space for frames, renders and downloaded inputs. Durable files go to ctx.storage, never only here.
STORAGE = Path(os.getenv("VIDEO_ROUTER_WORK_DIR", Path(tempfile.gettempdir()) / "palette-video"))
# Optional: a local OpenMontage checkout for real editing (AGPL-3.0, run unmodified as a separate process).
OPENMONTAGE_DIR = Path(os.getenv("OPENMONTAGE_DIR", "/nonexistent-openmontage"))

LLM_BASE_URL = "https://llm.pltt.dev"
LLM_API_KEY = ""
REASONING_MODEL = os.getenv("REASONING_MODEL", "palette-llm-max")
VISION_MODEL = os.getenv("VISION_MODEL", "palette-vlm")            # fast vision: Normal / Hybrid
DEEP_VISION_MODEL = os.getenv("DEEP_VISION_MODEL", "palette-llm-max")  # native image input: AI Analyze
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "high")              # low | medium | high | xhigh
STT_MODEL = os.getenv("STT_MODEL", "palette-stt")
MOCK_STEP_SECONDS = float(os.getenv("MOCK_STEP_SECONDS", "2"))
# Which questions the router may ask: "uploads" = only for files it needs (mentioned but missing, or required by the
# only capable app); everything else uses sensible defaults, listed as assumptions. "all" = every clarification.
ASK_POLICY = os.getenv("ROUTER_ASK", "uploads")
PRICE_IN_PER_1K = None
PRICE_OUT_PER_1K = None


def _clean_base(url: str) -> str:
    url = (url or "https://llm.pltt.dev").rstrip("/")
    # A console page URL is often pasted here (playground, docs, org settings); the API lives at the root.
    url = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    for marker in ("/docs", "/playground", "/console", "/v1"):
        if marker in url:
            url = url[: url.index(marker)]
    return url.rstrip("/")


# The Motion Graphics Director always writes on Opus (falls back to the reasoning chain when it is unavailable).
MOTION_MODEL = os.getenv("MOTION_LLM_MODEL", "anthropic/claude-opus-5.5")
# How long one model call may take, seconds. The long writers (a documentary or drama architect, a revise, the motion
# director) take 4-5 minutes on Opus, so the default leaves plenty of room. Speech-to-text has its own limit.
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "900"))
STT_TIMEOUT_S = float(os.getenv("STT_TIMEOUT_S", "600"))
PREFERRED_MODEL = os.getenv("PALETTE_LLM_MODEL", "")  # e.g. anthropic/claude-opus-5.5 (falls back to palette-llm-max)


def configure(*, api_key: str | None = None, base_url: str | None = None, openmontage_dir: str | None = None,
              model: str | None = None) -> None:
    """Set runtime values (called by the Palette backend with ctx.secret / ctx.config_value results)."""
    global LLM_API_KEY, LLM_BASE_URL, OPENMONTAGE_DIR, PREFERRED_MODEL
    if model is not None:
        PREFERRED_MODEL = model.strip()
    if api_key is not None:
        LLM_API_KEY = api_key
    if base_url:
        LLM_BASE_URL = _clean_base(base_url)
    if openmontage_dir:
        OPENMONTAGE_DIR = Path(openmontage_dir)
