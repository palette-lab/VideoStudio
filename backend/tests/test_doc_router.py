"""An uploaded document goes where its CONTENT belongs: only a document to explain goes to the Document Explainer."""
import asyncio

import pytest

from engines import doc_router, explainroo_engine

AD = {"filename": "fashion_product_advertisement.docx",
      "text": "NEW SEASON • NEW STYLE\nWEAR YOUR CONFIDENCE\nEveryday fashion. Unforgettable you.\n"
              "FRESH, VERSATILE STYLES\nDISCOVER THE COLLECTION\nShop now • [Your website / Store name]"}


def _fake(answer, seen):
    async def chat(model, messages, **kw):
        seen.append((kw["stage"], messages[1]["content"]))
        return answer
    return chat


def test_an_ad_document_becomes_an_ad(monkeypatch):
    seen = []
    monkeypatch.setattr(doc_router.llm, "chat_json", _fake({"use": "ad", "kind": "fashion ad copy", "why": "slogan"}, seen))
    r = asyncio.run(doc_router.route("", [AD]))
    assert r == {"use": "ad", "label": "Advertisement", "kind": "fashion ad copy", "why": "slogan"}
    assert seen[0][0] == "story.doc_route" and "WEAR YOUR CONFIDENCE" in seen[0][1]
    b = doc_router.brief(r, [AD])
    assert "IS THE BRIEF" in b and "call to action" in b and "WEAR YOUR CONFIDENCE" in b and "placeholders" in b


def test_a_failed_or_odd_answer_keeps_the_explainer(monkeypatch):
    seen = []
    monkeypatch.setattr(doc_router.llm, "chat_json", _fake({"use": "poem"}, seen))
    assert asyncio.run(doc_router.route("x", [AD]))["use"] == "explain"

    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(doc_router.llm, "chat_json", boom)
    assert asyncio.run(doc_router.route("x", [AD]))["use"] == "explain"
    assert asyncio.run(doc_router.route("x", [{"text": " "}]))["use"] == "explain"
    assert doc_router.brief({"use": "explain"}, [AD]) == ""


@pytest.mark.parametrize("use,explained", [("explain", True), ("ad", False), ("story", False), ("lesson", False),
                                           (None, True)])
def test_the_explainer_takes_only_documents_to_explain(use, explained):
    run = {"mode": "auto", "prompt": "", "assets": {"a": {"modality": "document"}}}
    if use:
        run["doc_route"] = {"use": use}
    assert explainroo_engine.wanted(run) is explained
    run["mode"] = "explainer"  # the user picked the Document Explainer mode: it explains, whatever the content
    assert explainroo_engine.wanted(run) is True


def test_the_story_prompt_carries_the_document(monkeypatch):
    pytest.importorskip("palette_sdk")
    import main
    run = {"prompt": "make a video", "documents": {"a": AD}, "doc_route": {"use": "ad", "kind": "fashion ad"}}
    p = main._story_prompt(run)
    assert p.startswith("make a video") and "WEAR YOUR CONFIDENCE" in p
    run["doc_route"] = {"use": "drama"}
    assert main._story_prompt(run).endswith("Turn the uploaded document into a short drama.")
    run.pop("doc_route")
    assert main._story_prompt(run) == "make a video"
