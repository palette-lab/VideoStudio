"""Application registry: the single source of truth about what each app can do.

Status of every fact: confirmed (owner confirmed) | unverified (initial assumption, usable for planning, flagged)
| unknown (never counts as supported) | not_applicable. Profiles can be updated in the JSON files without code changes.
"""

import json
from functools import lru_cache

from . import config

ASSET_TAGS = ["person", "character", "garment", "product", "scene", "logo", "text", "animal", "vehicle", "food", "diagram"]
FORMAT_MODALITY = {"video_file": "video", "image_file": "image", "audio_file": "audio", "storyboard_json": "storyboard", "text": "text"}
MODALITY_FORMAT = {"video": "video_file", "image": "image_file", "audio": "audio_file"}
SUPPORTED = ("confirmed", "unverified")
PROFILE_KEYS = ["shot_structures", "max_duration_s", "aspect_ratios", "motion_styles", "accepts_asset_roles", "audio_output"]


@lru_cache
def capabilities() -> dict[str, str]:
    return json.loads((config.REGISTRY_DIR / "capabilities.json").read_text())


def _normalise(app: dict) -> dict:
    status = dict(app.get("capability_status") or {})
    for c in app.get("capabilities", []):
        status.setdefault(c, "unverified")
    unknown = set(status) - set(capabilities())
    if unknown:
        raise ValueError(f"{app['app_id']} declares unknown capabilities {unknown}")
    app["capability_status"] = status
    # Only supported statuses count; "unknown" never does.
    app["capabilities"] = [c for c, s in status.items() if s in SUPPORTED]
    prof = app.get("profile") or {}
    app["profile"] = {k: prof.get(k) or {"value": None, "status": "unknown"} for k in PROFILE_KEYS}
    app.setdefault("input_modalities_status", "unverified")
    return app


@lru_cache
def apps() -> dict[str, dict]:
    out = {}
    for path in sorted((config.REGISTRY_DIR / "apps").glob("*.json")):
        app = _normalise(json.loads(path.read_text()))
        out[app["app_id"]] = app
    return out


def cap_status(app: dict, cap: str) -> str:
    return app.get("capability_status", {}).get(cap, "unknown" if cap not in app.get("capabilities", []) else "unverified")


def supports(app: dict, cap: str) -> bool:
    return cap_status(app, cap) in SUPPORTED


def fact(app: dict, key: str) -> tuple:
    """(value, status) of a profile attribute."""
    f = app.get("profile", {}).get(key) or {}
    return f.get("value"), f.get("status", "unknown")


def summary_for_llm() -> list[dict]:
    """What an LLM sees: registry facts only, with their status."""
    keys = ["app_id", "name", "description", "status", "capabilities", "capability_status", "input_modalities",
            "capability_requirements", "accepts_handoff", "emits", "profile", "best_for", "not_good_for"]
    return [{k: a.get(k) for k in keys} for a in apps().values()]


def apps_covering(caps: set[str]) -> list[str]:
    return [a["app_id"] for a in apps().values()
            if a["status"] == "available" and all(supports(a, c) for c in caps)]


def unavailable_capabilities(caps: set[str]) -> list[str]:
    """Capabilities no available app supports (unknown counts as unsupported)."""
    return sorted(c for c in caps if not any(a["status"] == "available" and supports(a, c) for a in apps().values()))
