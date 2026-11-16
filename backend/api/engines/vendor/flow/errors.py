# Vendored from palette-lab/pltt-os-apps-rinsan flow_video/backend/api/core/errors.py (v0.0.35).
"""Standard, user-facing failure messages.

Whatever a provider, ffmpeg or an HTTP call raised, the Item stores (and the
UI shows) one of these short messages; the raw text goes to the server log.
The frontend shows them as they are (translated through lib/i18n).
"""

from __future__ import annotations

import re
from typing import Optional

GENERIC = "Something went wrong. Please try again."
BLOCKED = "This was blocked by the content filter. Try a different prompt or image."
REAL_PEOPLE = ("Seedance can't animate photos of real people — that's its safety rule, not a problem with your image. "
               "Switch to Kling 4.0 and retry.")
TIMED_OUT = "This took too long and was stopped. Please try again."
INTERRUPTED = "This was interrupted. Please try again."
CREDITS = "Your workspace is out of credits. Please contact your admin."
BUSY = "The service is busy right now. Please wait a moment and try again."
NETWORK = "Couldn't connect. Check your internet connection and try again."
NOT_CONNECTED = "Your account isn't connected. Please reconnect and try again."
INVALID = "Some settings or files aren't supported. Please adjust them and try again."
LOST_SUBMIT = "The request may have started, but its answer was lost. Retry to generate it again."
UNAVAILABLE = "This isn't available right now. Please try again later."

STANDARD = {GENERIC, BLOCKED, REAL_PEOPLE, TIMED_OUT, INTERRUPTED, CREDITS, BUSY, NETWORK, NOT_CONNECTED, INVALID, UNAVAILABLE, LOST_SUBMIT}

# A provider refusing the input — expected, not an app error: logged as "refused" (WARNING), never ERROR.
# Seedance (ByteDance) refuses start images that may show a real person: InputImageSensitiveContentDetected
# .PrivacyInformation, "...may contain real person" (fal: "...may contain likenesses of real people...").
_REAL_PERSON = re.compile(r"privacy ?information|\breal[ _-]?(?:person|people)\b|likeness"
                          r"|can'?t (?:use images|animate photos) of real people")
# Means a real person only on Seedance (elsewhere it is an ordinary validation error).
_SEEDANCE_ONLY = re.compile(r"partner_validation")
_CONTENT_POLICY = re.compile(r"sensitive ?content|content[ _-]?(?:policy|filter)|nsfw|moderat")
REAL_PEOPLE_MODELS = ("seedance",)

# First match wins — order matters (a content-filter 400 is BLOCKED, not INVALID).
_RULES: list[tuple[str, re.Pattern]] = [
    (BLOCKED, re.compile(r"content[ _-]?(policy|filter)|nsfw|safety|moderat|flagged|inappropriate|blocked")),
    (CREDITS, re.compile(r"credit|usage[_ ]exhausted|quota|billing|budget|wallet|spend(ing)? cap|insufficient (funds|balance|credit)|\b402\b")),
    (BUSY, re.compile(r"\b429\b|rate[ _-]?limit|too many requests|overloaded|capacity|busy")),
    (TIMED_OUT, re.compile(r"timed? ?out|timeout|took too long|deadline")),
    (INTERRUPTED, re.compile(r"interrupt|cancel")),
    (NETWORK, re.compile(r"couldn'?t connect|network|econn|socket|connection (reset|refused|closed)|transport")),
    (NOT_CONNECTED, re.compile(r"not connected|isn'?t connected|reconnect|oauth|token (expired|revoked)|invalid_grant|unauthori[sz]ed|\b40[13]\b")),
    (INVALID, re.compile(r"\b4(00|13|15|22)\b|unprocessable|invalid|unsupported|not supported|aren'?t supported|validation|too large|not reachable|bad request")),
    (UNAVAILABLE, re.compile(r"not configured|missing .*key|ffmpeg|\b50[23]\b|unavailable|not available|isn'?t available")),
]


def error_text(raw: object) -> str:
    """All an error says: its text, its `error_type` and an HTTP error's response body (httpx and fal leave the
    body out of str())."""
    parts = [str(raw or "")]
    error_type = getattr(raw, "error_type", None)
    if error_type:
        parts.append(str(error_type))
    response = getattr(raw, "response", None)
    if response is not None:
        try:
            parts.append(str(response.text)[:2000])
        except Exception:  # noqa: BLE001 — a streamed or closed body
            pass
    return " ".join(p for p in parts if p)


def _seedance(model: Optional[str]) -> bool:
    return any(m in (model or "").lower() for m in REAL_PEOPLE_MODELS)


def refusal_reason(raw: object, model: Optional[str] = None) -> Optional[str]:
    """"real_person" / "content_policy" when a provider refused the input (the same input would be refused again),
    else None. A real-person refusal is Seedance's: on another known model it counts as a content block."""
    text = error_text(raw).lower()
    if _REAL_PERSON.search(text) or (_seedance(model) and _SEEDANCE_ONLY.search(text)):
        return "real_person" if not model or _seedance(model) else "content_policy"
    if _CONTENT_POLICY.search(text):
        return "content_policy"
    return None


def friendly_error(raw: object, *, clip: bool = False, model: Optional[str] = None) -> str:
    """The standard message for any raw error text (or exception). A real-person refusal points to another video
    model only for a clip; anywhere else it reads as a content block."""
    text = error_text(raw).strip()
    if text in STANDARD:
        return text
    reason = refusal_reason(text, model)
    if reason == "real_person":
        return REAL_PEOPLE if clip else BLOCKED
    if reason == "content_policy":
        return BLOCKED
    lowered = text.lower()
    for message, pattern in _RULES:
        if pattern.search(lowered):
            return message
    return GENERIC
