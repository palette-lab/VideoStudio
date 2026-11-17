"""Document Explainer: uploaded documents -> LLM analysis -> explainroo (faked here; real ffmpeg not needed)."""

import asyncio
import json
import zipfile

import pytest

from engines import explainroo_engine as xr
from engines.story_intel import research
from router_core import config, registry, validator
from tests.conftest import REAL_REGISTRY


@pytest.fixture(autouse=True)
def real_registry(monkeypatch):
    monkeypatch.setattr(config, "REGISTRY_DIR", REAL_REGISTRY)
    registry.apps.cache_clear(); registry.capabilities.cache_clear()
    yield
    registry.apps.cache_clear(); registry.capabilities.cache_clear()


def _run(mode="auto", assets=None, docs=None, prompt="explain this report"):
    assets = assets if assets is not None else {"a1": {"id": "a1", "modality": "document", "filename": "report.pdf"}}
    docs = docs if docs is not None else {"a1": {"filename": "report.pdf", "text": "Revenue grew 12% in 2025."}}
    return {"mode": mode, "assets": assets, "documents": docs, "prompt": prompt,
            "settings": {"duration_s": 60, "aspect_ratio": "16:9"}}


def test_documents_go_to_the_explainer_and_the_plan_validates():
    assert xr.wanted(_run()) and xr.wanted(_run(mode="explainer", assets={}))
    video = {"a1": {"modality": "document"}, "a2": {"modality": "video"}}
    assert not xr.wanted(_run(assets=video))  # the user's footage: the editing apps
    assert not xr.wanted(_run(assets={"a2": {"modality": "image"}}))  # no document
    assert not xr.wanted(_run(mode="storyboard"))  # the user picked another app
    d = xr.build(_run())
    step = d["plan"]["steps"][0]
    assert step["app_id"] == xr.APP_ID and step["handoff"]["request"]["duration"] == 60
    assert step["handoff"]["request"]["duration_given"] is True and step["handoff"]["request"]["documents"] == ["report.pdf"]
    assert not validator.errors(validator.validate(d["plan"], d["understanding"], "auto", {}))


def test_generated_drawing_code_is_refused_when_it_reaches_out():
    assert xr.unsafe("export default { intro(s) { s.text('Revenue', { at: 'grew' }) } }") == []
    assert xr.unsafe("import { card } from './format.js'") == []
    for bad in ("fetch('/x')", "new Function('a')", "eval(x)", "s.image('https://evil.test/a.png')",
                "import x from 'fs'", "await import('./x.js')", "localStorage.setItem(1, 2)"):
        assert xr.unsafe(bad), bad


def test_word_documents_are_read(tmp_path):
    p = tmp_path / "notes.docx"
    body = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Quarterly report</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Revenue grew </w:t></w:r><w:r><w:t>12%.</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", body)
    assert research.document_text(str(p)) == "Quarterly report\nRevenue grew 12%."


def test_engine_follows_explainroos_workflow_and_fixes_what_check_finds(tmp_path, monkeypatch):
    calls, stages = [], []
    checks = iter([{"issues": [{"level": "error", "scene": "intro", "t": 1.2, "message": "text cut off at the edge"}]},
                   {"issues": [{"level": "hint", "scene": "intro", "message": "fine"}]}])

    async def fake_cli(args, cwd, timeout=0, want_json=True):
        calls.append(args[0])
        if args[0] == "init":  # like explainroo: the project folder with its video.json
            import pathlib
            pathlib.Path(args[1]).mkdir(parents=True)
            return {"dir": args[1]}
        if args[0] == "docs":
            return f"(explainroo docs {args[1]})"
        if args[0] == "words":
            return {"scenes": [{"scene": "intro", "words": [{"text": "Revenue", "start": 0.3}]}]}
        if args[0] == "check":
            return next(checks)
        if args[0] == "render":
            out = tmp_path / "video.mp4"
            out.write_bytes(b"mp4")
            return {"out": str(out), "subtitles": {"srt": "video.srt"}, "chapters": "c.txt", "cover": "c.jpg"}
        if args[0] == "verify":
            return {"duration": 41.5, "passed": True, "verdict": "passed"}
        return {}

    async def fake_chat(model, messages, **kw):
        stages.append(kw["stage"])
        if kw["stage"] == "engine.explainer.analyse":
            assert "Revenue grew 12% in 2025." in messages[1]["content"]
            return {"title": "Revenue in 2025", "language": "en", "theme": "studio", "size": "16:9",
                    "facts": [{"claim": "revenue grew 12% in 2025", "where": "p. 1"}],
                    "outline": [{"scene": "intro", "idea": "growth", "show": "a bar chart"}]}
        if kw["stage"] == "engine.explainer.script":
            assert "(explainroo docs writing)" in messages[0]["content"]
            return {"script_md": "# Revenue\n\n## intro\n[#grew] Revenue grew twelve percent."}
        if kw["stage"] == "engine.explainer.scenes":
            assert "(explainroo docs scene-api)" in messages[0]["content"] and '"Revenue"' in messages[1]["content"]
            return {"scenes_js": "export default { intro(s) { fetch('https://x.test') } }"}  # refused, never run
        if kw["stage"] == "engine.explainer.fix":
            return {"scenes_js": "export default { intro(s) { s.text('12%', { at: '#grew' }) } }"}
        raise AssertionError(kw["stage"])

    monkeypatch.setattr(xr, "run_cli", fake_cli)
    monkeypatch.setattr(xr.llm, "chat_json", fake_chat)
    monkeypatch.setattr(xr, "available", lambda: None)
    monkeypatch.setattr(xr, "_DOCS", {})
    msgs = []

    async def progress(m):
        msgs.append(m)

    handoff = {"prompt": "explain the report", "request": {"duration": 40, "duration_given": True, "aspect_ratio": "16:9"}}
    out = asyncio.run(xr.run({"name": "Document Explainer"}, handoff,
                             [{"filename": "report.pdf", "text": "Revenue grew 12% in 2025."}], tmp_path / "w", progress))
    # explainroo's order: init, settings, the narration's voice, word times, then the scenes, check, render, verify
    order = [c for c in calls if c != "docs"]
    assert order[:4] == ["init", "set", "set", "voice"] and order[4] == "words"
    assert order[-2:] == ["render", "verify"] and order.count("check") == 2
    # the unsafe scenes.js was sent back (never written or checked), then check's error was fixed
    assert stages.count("engine.explainer.fix") == 2
    assert (tmp_path / "w" / "explainer" / "scenes.js").read_text().startswith("export default { intro(s) { s.text")
    assert out["title"] == "Revenue in 2025" and out["duration_s"] == 41.5 and out["verify"] == "passed"
    assert out["analysis"]["target_seconds"] == 40 and any("fixing" in m for m in msgs)


def test_no_readable_document_is_said_plainly(monkeypatch):
    import main
    res = main._explainer_result({**_run(docs={}), "id": "r1", "events": []})
    assert res["status"] == "not_possible" and "No readable document" in res["blocking_reasons"][0]
    monkeypatch.setattr(xr, "available", lambda: None)
    ok = main._explainer_result({**_run(), "id": "r1", "events": []})
    assert ok["status"] == "awaiting_approval" and ok["plan"]["steps"][0]["app_id"] == xr.APP_ID
    assert json.dumps(ok["understanding"])  # serialisable onto the run
