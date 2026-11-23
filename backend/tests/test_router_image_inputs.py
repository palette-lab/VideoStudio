"""Each router image model gets an inline picture in the form its provider accepts (the fal-backed models refused the
base64 field, which broke every scene image while fal itself was out of credit)."""

import asyncio

from engines import router_images as R

DATA = "data:image/jpeg;base64,QUJD"


def test_inline_picture_form_per_model():
    assert R._input(DATA, 0, "image-edit-production") == {"type": "image", "url": DATA, "role": "subject"}
    assert R._input(DATA, 0, "image-edit-default")["url"] == DATA
    g = R._input(DATA, 1, "image-edit-staging")
    assert g["base64"] == "QUJD" and g["mime_type"] == "image/jpeg" and "url" not in g
    assert R._input("https://x/a.png", 0, "image-edit-staging") == {"type": "image", "url": "https://x/a.png", "role": "subject"}


def test_fallback_chain_and_url_only_skip(monkeypatch):
    assert R.EDIT_MODELS[:3] == ["image-edit-production", "image-edit-default", "image-edit-staging"]
    assert "palette-runware-fallback-image" in R.EDIT_MODELS
    tried = []

    async def fake_one(client, path, body):
        tried.append(body["model"])
        raise R.RouterImageError(f"{body['model']}: rejected")
    monkeypatch.setattr(R, "_one", fake_one)
    monkeypatch.setenv("LLM_ROUTER_API_KEY", "k")
    try:
        asyncio.run(R.generate("p", "9:16", [DATA]))
    except R.RouterImageError as e:
        assert "palette-runware-fallback-image: skipped" in str(e)
    assert tried == ["image-edit-production", "image-edit-default", "image-edit-staging"]  # Runware skipped for inline
