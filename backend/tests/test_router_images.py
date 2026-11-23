import asyncio
import json

import httpx

from engines import router_images as ri


def _sse(*events):
    return "".join(f"event: message\ndata: {json.dumps(e)}\n\n" for e in events)


def test_origin_ignores_the_path(monkeypatch):
    monkeypatch.setenv("LLM_ROUTER_BASE_URL", "https://router-api.pltt.ai/image/edit")
    assert ri.origin() == "https://router-api.pltt.ai"
    monkeypatch.delenv("LLM_ROUTER_BASE_URL")
    assert ri.origin() == ri.DEFAULT_ORIGIN


def test_parse_events():
    done = _sse({"event": "job_started", "data": {}},
                {"event": "image_done", "data": {"type": "image_done", "url": "https://x/1.png"}}).splitlines()
    assert ri.parse_events(done) == ("https://x/1.png", None)
    bad = _sse({"event": "error", "data": {"type": "error", "message": "The provider rejected authentication."}})
    assert ri.parse_events(bad.splitlines()) == (None, "The provider rejected authentication.")


def test_a_failing_model_falls_through_to_the_next(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_ROUTER_API_KEY", "gw_test")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, content=b"PNG")
        body = json.loads(req.content)
        seen.append((req.url.path, body["model"], [i["url"] for i in body.get("inputs") or []]))
        assert req.headers["authorization"] == "Bearer gw_test"
        if body["model"] == ri.EDIT_MODELS[0]:  # its provider is out of credit
            text = _sse({"event": "error", "data": {"type": "error", "message": "Exhausted balance"}})
        else:
            text = _sse({"event": "image_done", "data": {"type": "image_done", "url": "https://x/ok.png"}},
                        {"event": "done", "data": {}})
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(ri.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    url = asyncio.run(ri.generate("paper", "16:9", ["https://x/ref.png"], tmp_path / "a.png"))
    assert url == "https://x/ok.png" and (tmp_path / "a.png").read_bytes() == b"PNG"
    assert [s[1] for s in seen] == ri.EDIT_MODELS[:2] and seen[0][0] == "/image/edit"
    assert seen[0][2] == ["https://x/ref.png"]
    seen.clear()
    asyncio.run(ri.generate("paper", "16:9"))
    assert seen[0][0] == "/image" and seen[0][1] == ri.TEXT_MODELS[0] and seen[0][2] == []


def test_no_key_no_router(monkeypatch):
    monkeypatch.delenv("LLM_ROUTER_API_KEY", raising=False)
    assert not ri.available()


def test_inline_pictures_take_each_providers_form_and_the_ratio_is_top_level():
    # The fal-backed models reject the base64 field but read a data: URL; Gemini direct takes base64 (router, 2026-11-20).
    body = ri._body("p", "image-edit-production", "9:16", ["data:image/jpeg;base64,QUJD", "https://x/r.png"])
    assert body["aspect_ratio"] == "9:16" and body["num_images"] == 1
    assert body["inputs"][0] == {"type": "image", "url": "data:image/jpeg;base64,QUJD", "role": "subject"}
    assert body["inputs"][1]["url"] == "https://x/r.png"
    g = ri._body("p", "image-edit-staging", "9:16", ["data:image/jpeg;base64,QUJD"])
    assert g["inputs"][0] == {"type": "image", "base64": "QUJD", "mime_type": "image/jpeg", "role": "subject"}


def test_fal_image_failures_fall_back_to_the_router(monkeypatch):
    from engines import story_engine as se
    calls = []

    def generate_image(key, model, prompt, **kw):
        calls.append("fal")
        raise RuntimeError("User is locked. Reason: Exhausted balance.")

    async def router(prompt, aspect="16:9", refs=None, dest=None):
        calls.append(("router", aspect, tuple(refs or [])))
        return "https://router/out.png"
    monkeypatch.setenv("LLM_ROUTER_API_KEY", "gw_test")
    monkeypatch.setattr(ri, "generate", router)
    monkeypatch.setattr(se, "_fal_down_until", 0.0)
    kw = {"aspect_ratio": "9:16", "num_images": 1, "image_urls": ["https://x/ref.png"]}
    res = asyncio.run(se._fal(generate_image, "fal_key", "nano_banana_pro", "a paper fox", **kw))
    assert se._url(res, "image") == "https://router/out.png"
    assert calls == ["fal", ("router", "9:16", ("https://x/ref.png",))]
    calls.clear()  # fal said the balance is gone: the next image does not even ask fal
    asyncio.run(se._fal(generate_image, "fal_key", "nano_banana_pro", "again", aspect_ratio="16:9"))
    assert calls == [("router", "16:9", ())]


def test_without_the_router_fal_errors_are_unchanged(monkeypatch):
    import pytest
    from engines import story_engine as se
    monkeypatch.delenv("LLM_ROUTER_API_KEY", raising=False)

    def generate_image(*a, **k):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(se._fal(generate_image, "k", "m", "p"))
