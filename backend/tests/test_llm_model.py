"""PALETTE_LLM_MODEL: the preferred vendor model replaces palette-llm-max, with a fallback while it is switched off."""

import asyncio
import json

import httpx

from router_core import config, llm


def _gateway(monkeypatch, vendor_on: bool):
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if "/" in model and not vendor_on:
            return httpx.Response(503, json={"error": {"message": "Vendor models are not switched on for this "
                                                                  "organization yet.", "code": "unavailable"}})
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": '{"ok": true}'}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(config, "PREFERRED_MODEL", "anthropic/claude-opus-5.5")
    monkeypatch.setattr(llm, "_down_until", {})
    return seen


def test_opus_is_used_when_switched_on(monkeypatch):
    seen = _gateway(monkeypatch, vendor_on=True)
    assert asyncio.run(llm.chat_json("palette-llm-max", [], stage="t")) == {"ok": True}
    assert asyncio.run(llm.chat_text("palette-vlm", [], stage="t")) == '{"ok": true}'  # other models untouched
    assert seen == ["anthropic/claude-opus-5.5", "palette-vlm"]


def test_falls_back_to_palette_llm_max_while_switched_off(monkeypatch):
    seen = _gateway(monkeypatch, vendor_on=False)
    assert asyncio.run(llm.chat_json("palette-llm-max", [], stage="t")) == {"ok": True}
    assert asyncio.run(llm.chat_text("palette-llm-max", [], stage="t")) == '{"ok": true}'
    assert seen == ["anthropic/claude-opus-5.5", "palette-llm-max", "palette-llm-max"]  # tried once, then remembered


def test_console_urls_are_cleaned_to_the_api_root():
    assert config._clean_base("https://llm.pltt.dev/docs/orgs#start") == "https://llm.pltt.dev"
    assert config._clean_base("https://llm.pltt.dev/v1/") == "https://llm.pltt.dev"


def test_chain_opus_first_then_the_second_choice_then_palette_llm_max(monkeypatch):
    seen = _gateway(monkeypatch, vendor_on=False)  # every vendor model is switched off here
    monkeypatch.setattr(config, "PREFERRED_MODEL", "anthropic/claude-opus-5.5, openai/gpt-5.5, palette-llm")
    assert llm.chain() == ["anthropic/claude-opus-5.5", "openai/gpt-5.5", "palette-llm", "palette-llm-max"]
    assert asyncio.run(llm.chat_json("palette-llm-max", [], stage="t")) == {"ok": True}
    assert seen == ["anthropic/claude-opus-5.5", "openai/gpt-5.5", "palette-llm"]  # the first available one answers
    seen.clear()
    assert asyncio.run(llm.chat_json("palette-llm-max", [], stage="t")) == {"ok": True}
    assert seen == ["palette-llm"]  # switched-off models are skipped for a while
