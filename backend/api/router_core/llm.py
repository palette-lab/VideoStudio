"""Client for the OpenAI-compatible Palette LLM gateway (verified: chat, vision, transcription).

Every call is recorded (model, stage, latency, tokens, success) into the usage log bound to the current run/eval.
An optional on-disk cache (used by the evaluation runner) returns identical results for identical calls.
"""

import base64
import contextvars
import hashlib
import json
import logging
import re
import time
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("router.llm")

usage_log: contextvars.ContextVar[list | None] = contextvars.ContextVar("usage_log", default=None)
cache_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar("cache_dir", default=None)


class LLMError(RuntimeError):
    pass


def image_part(path: str | Path) -> dict:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


# Model chain (PALETTE_LLM_MODEL, comma-separated, e.g. "anthropic/claude-opus-5.5, palette-llm"): the first model
# that is available answers every palette-llm-max call; palette-llm-max itself is always the last resort. A model the
# gateway says is not available for this key/organization is skipped for VENDOR_RETRY_S and then tried again, so
# switching a model on in the console takes effect without a restart.
UPGRADE_FROM = "palette-llm-max"
VENDOR_RETRY_S = 600
_down_until: dict[str, float] = {}


def chain() -> list[str]:
    models = [m.strip() for m in (config.PREFERRED_MODEL or "").split(",") if m.strip()]
    return list(dict.fromkeys(models + [UPGRADE_FROM]))


def pick_model(model: str, skip: set[str] | frozenset = frozenset()) -> str:
    if model != UPGRADE_FROM:
        return model
    now = time.time()
    return next((m for m in chain() if m not in skip and _down_until.get(m, 0) <= now), UPGRADE_FROM)


def _unavailable(status: int, text: str) -> bool:
    t = (text or "").lower()
    return status in (400, 403, 404, 422, 503) and any(
        x in t for x in ("not switched on", "model_not_found", "unavailable", "not available", "not allowed"))


def _vendor_failed(used: str, status: int, text: str) -> bool:
    """True (and that model is skipped for a while) when a chain model other than the last resort is not usable."""
    if used == UPGRADE_FROM or used not in chain() or not _unavailable(status, text):
        return False
    _down_until[used] = time.time() + VENDOR_RETRY_S
    log.warning("%s is not available for this key (%s); trying the next model for %s s", used, text[:160],
                VENDOR_RETRY_S)
    return True


def _record(**entry) -> None:
    log_list = usage_log.get()
    if log_list is not None:
        log_list.append(entry)


def summarize(entries: list[dict]) -> dict:
    """Totals for a usage log. Cost is only estimated when prices are configured."""
    live = [e for e in entries if not e.get("cached")]
    pin = sum(e.get("prompt_tokens") or 0 for e in live)
    pout = sum(e.get("completion_tokens") or 0 for e in live)
    priced = config.PRICE_IN_PER_1K is not None and config.PRICE_OUT_PER_1K is not None
    return {
        "calls": len(entries),
        "cached_calls": len(entries) - len(live),
        "failed_calls": sum(1 for e in entries if not e.get("ok")),
        "prompt_tokens": pin,
        "completion_tokens": pout,
        "latency_s": round(sum(e.get("latency_s") or 0 for e in live), 2),
        "by_stage": {s: sum(1 for e in entries if e.get("stage") == s) for s in sorted({e.get("stage") or "" for e in entries})},
        "estimated_cost_usd": round(pin / 1000 * config.PRICE_IN_PER_1K + pout / 1000 * config.PRICE_OUT_PER_1K, 4)
        if priced else None,
        "cost_note": None if priced else "Token prices are not configured, so no cost estimate.",
    }


def usage_summary(run: dict) -> dict:
    """A run's LLM calls and tokens plus its paid model calls (images, clips, voice), for the usage panel."""
    out = summarize(run.get("usage_log") or [])
    calls = run.get("model_calls") or []
    out["generated"] = {k: sum(1 for c in calls if c.get("kind") == k) for k in ("image", "clip", "voice")}
    out["generated"]["models"] = sorted({c.get("model") for c in calls if c.get("model")})
    return out


async def chat_json(model: str, messages: list[dict], max_tokens: int = 6000, reasoning_effort: str | None = None,
                    stage: str = "chat") -> dict:
    if not config.LLM_API_KEY:
        raise LLMError("PALETTE_API_KEY is not set in .env")
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "reasoning_effort": reasoning_effort or config.REASONING_EFFORT,
    }
    cdir = cache_dir.get()
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() if cdir else None
    if key and (cdir / f"{key}.json").exists():
        _record(stage=stage, model=model, ok=True, cached=True, latency_s=0)
        return json.loads((cdir / f"{key}.json").read_text())

    last_err = None
    attempt, skip = 0, set()
    while attempt < 2:
        started = time.monotonic()
        used = body["model"] = pick_model(model, skip)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20)) as client:
                r = await client.post(
                    f"{config.LLM_BASE_URL}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    json=body,
                )
            latency = round(time.monotonic() - started, 2)
            if r.status_code != 200 and _vendor_failed(used, r.status_code, r.text):
                skip.add(used)
                continue  # straight to the next model in the chain; not a failed attempt
            if r.status_code != 200:
                raise LLMError(f"LLM gateway returned {r.status_code}: {r.text[:300]}")
            data = r.json()
            usage = data.get("usage") or {}
            result = _extract_json(data["choices"][0]["message"].get("content") or "")
            _record(stage=stage, model=used, ok=True, cached=False, latency_s=latency, attempt=attempt + 1,
                    prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
            if key:
                cdir.mkdir(parents=True, exist_ok=True)
                (cdir / f"{key}.json").write_text(json.dumps(result))
            return result
        except (json.JSONDecodeError, KeyError, httpx.HTTPError, LLMError) as e:
            last_err = e
            _record(stage=stage, model=used, ok=False, cached=False, latency_s=round(time.monotonic() - started, 2),
                    attempt=attempt + 1, error=str(e)[:200])
            log.warning("LLM call failed (attempt %s, model %s, stage %s): %s", attempt + 1, used, stage, e)
            attempt += 1
    raise LLMError(f"LLM call failed: {last_err}")


async def chat_text(model: str, messages: list[dict], max_tokens: int = 24000, reasoning_effort: str = "medium",
                    stage: str = "chat") -> str:
    """Plain-text completion for long artefacts (e.g. a whole HTML composition) where a JSON wrapper is fragile."""
    if not config.LLM_API_KEY:
        raise LLMError("PALETTE_API_KEY is not set in .env")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "reasoning_effort": reasoning_effort}
    last_err = None
    attempt, skip = 0, set()
    while attempt < 2:
        started = time.monotonic()
        used = body["model"] = pick_model(model, skip)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20)) as client:
                r = await client.post(f"{config.LLM_BASE_URL}/v1/chat/completions",
                                      headers={"Authorization": f"Bearer {config.LLM_API_KEY}"}, json=body)
            if r.status_code != 200 and _vendor_failed(used, r.status_code, r.text):
                skip.add(used)
                continue
            if r.status_code != 200:
                raise LLMError(f"LLM gateway returned {r.status_code}: {r.text[:300]}")
            data = r.json()
            text = data["choices"][0]["message"].get("content") or ""
            if not text.strip():
                raise LLMError(f"empty reply (finish_reason={data['choices'][0].get('finish_reason')})")
            usage = data.get("usage") or {}
            _record(stage=stage, model=used, ok=True, cached=False, latency_s=round(time.monotonic() - started, 2),
                    attempt=attempt + 1, prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"))
            return text
        except (KeyError, httpx.HTTPError, LLMError) as e:
            last_err = e
            _record(stage=stage, model=used, ok=False, cached=False, latency_s=round(time.monotonic() - started, 2),
                    attempt=attempt + 1, error=str(e)[:200])
            log.warning("LLM text call failed (attempt %s, model %s, stage %s): %s", attempt + 1, used, stage, e)
            attempt += 1
    raise LLMError(f"LLM call failed: {last_err}")


async def transcribe(path: str | Path) -> dict:
    if not config.LLM_API_KEY:
        raise LLMError("PALETTE_API_KEY is not set in .env")
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20)) as client:
        with open(path, "rb") as f:
            r = await client.post(
                f"{config.LLM_BASE_URL}/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                data={"model": config.STT_MODEL},
                files={"file": (Path(path).name, f)},
            )
    ok = r.status_code == 200
    _record(stage="transcription", model=config.STT_MODEL, ok=ok, cached=False,
            latency_s=round(time.monotonic() - started, 2), error=None if ok else r.text[:200])
    if not ok:
        raise LLMError(f"Transcription returned {r.status_code}: {r.text[:300]}")
    return r.json()
