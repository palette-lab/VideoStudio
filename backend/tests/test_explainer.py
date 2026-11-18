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
    assert xr.wanted(_run()) and xr.wanted(_run(mode="explainer"))
    assert not xr.wanted(_run(mode="explainer", assets={}))  # no file: the VOX Documentary makes the topic
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
    monkeypatch.setattr(xr, "_PKG", {})
    msgs = []

    async def progress(m):
        msgs.append(m)

    handoff = {"prompt": "explain the report", "request": {"duration": 40, "duration_given": True, "aspect_ratio": "16:9"}}
    out = asyncio.run(xr.run({"name": "Document Explainer"}, handoff,
                             [{"filename": "report.pdf", "text": "Revenue grew 12% in 2025."}], tmp_path / "w", progress))
    # explainroo's order: init, settings, the narration's voice, word times, then the scenes, check, render, verify
    order = [c for c in calls if c != "docs"]
    assert order[:5] == ["init", "set", "set", "voice", "voice"] and order[5] == "words"  # estimate, then record
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


def test_unknown_icons_and_render_errors_go_back_to_the_writer(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    (pkg / "engine" / "icons").mkdir(parents=True)
    (pkg / "engine" / "icons" / "lucide.json").write_text(json.dumps({"icons": {"git-branch": 1, "server": 1},
                                                                     "aliases": {}}))
    (pkg / "engine" / "icons" / "lucide-tags.json").write_text(json.dumps({"git-branch": ["git"], "server": []}))
    (pkg / "examples" / xr.EXAMPLE).mkdir(parents=True)
    (pkg / "examples" / xr.EXAMPLE / "script.md").write_text("## hook\nHello.")
    (pkg / "examples" / xr.EXAMPLE / "scenes.js").write_text("export default { hook(s) { s.icon('server') } }")
    renders, fixes = [], []

    async def fake_cli(args, cwd, timeout=0, want_json=True):
        if args[0] == "init":
            import pathlib
            pathlib.Path(args[1]).mkdir(parents=True)
            return {}
        if args[0] == "doctor":
            return {"checks": [{"name": "runs from", "detail": str(pkg)}]}
        if args[0] == "docs":
            return "(docs)"
        if args[0] == "check":
            return {"issues": []}  # explainroo's check does not see a missing icon
        if args[0] == "render":
            renders.append(1)
            if len(renders) == 1:  # a failure only the drawing shows
                raise xr.ExplainerError('explainroo render failed: scene "closing" at 0.00s: text too long')
            out = tmp_path / "v.mp4"
            out.write_bytes(b"x")
            return {"out": str(out)}
        if args[0] == "verify":
            return {"passed": True}
        return {}

    async def fake_chat(model, messages, **kw):
        st = kw["stage"]
        if st == "engine.explainer.analyse":
            return {"title": "T", "outline": [{"scene": "closing"}]}
        if st == "engine.explainer.script":
            return {"script_md": "## closing\nThanks."}
        if st == "engine.explainer.scenes":
            assert "EXAMPLE PROJECT" in messages[0]["content"] and "s.icon('server')" in messages[0]["content"]
            assert "x / y are the CENTRE of the" in messages[0]["content"]  # the grid mistake behind off-frame text
            return {"scenes_js": "export default { closing(s) { s.icon('github') } }"}
        fixes.append(messages[0]["content"].split("PROBLEMS:")[1])
        if len(fixes) == 1:
            assert 'icon "github" does not exist' in fixes[0] and "git-branch" in fixes[0]
            return {"scenes_js": "export default { closing(s) { s.icon('git-branch') } }"}
        assert "render failed" in fixes[1]
        return {"scenes_js": "export default { closing(s) { s.icon('git-branch'); } }"}

    monkeypatch.setattr(xr, "run_cli", fake_cli)
    monkeypatch.setattr(xr.llm, "chat_json", fake_chat)
    monkeypatch.setattr(xr, "available", lambda: None)
    monkeypatch.setattr(xr, "_DOCS", {})
    monkeypatch.setattr(xr, "_PKG", {})

    async def progress(m):
        pass
    out = asyncio.run(xr.run({"name": "X"}, {"prompt": "", "request": {}}, [{"filename": "a.txt", "text": "hi"}],
                             tmp_path / "w", progress))
    assert len(fixes) == 2 and len(renders) == 2 and out["path"].endswith("v.mp4")  # fixed, rendered on retry


def test_a_script_far_over_the_length_is_shortened_before_any_voice(tmp_path, monkeypatch):
    calls, stages, estimates = [], [], iter([138.0, 92.0])

    async def fake_cli(args, cwd, timeout=0, want_json=True):
        calls.append(" ".join(a for a in args if not a.startswith("/")))
        if args[0] == "init":
            import pathlib
            pathlib.Path(args[1]).mkdir(parents=True)
            return {}
        if args[0] == "voice" and "--estimate" in args:
            return {"estimate": {"seconds": next(estimates), "words": 300, "speechSeconds": 100}}
        if args[0] == "docs":
            return "(docs)"
        if args[0] == "check":
            return {"issues": []}
        if args[0] == "render":
            out = tmp_path / "v.mp4"
            out.write_bytes(b"x")
            return {"out": str(out)}
        return {}

    async def fake_chat(model, messages, **kw):
        stages.append(kw["stage"])
        if kw["stage"] == "engine.explainer.analyse":
            return {"title": "T", "target_seconds": 90, "outline": [{"scene": f"s{i}"} for i in range(9)]}
        if kw["stage"] == "engine.explainer.script":
            system = messages[0]["content"]
            assert f"about {xr.word_budget(90, 9)} spoken words" in system and "{B.Tech|B Tech}" in system
            return {"script_md": "## s0\nA very long script."}
        if kw["stage"] == "engine.explainer.length":
            msg = messages[1]["content"]
            want = xr.words_for({"words": 300, "seconds": 138, "speechSeconds": 100}, 90)
            assert "estimates this script at 138 s" in msg and f"It has 300 spoken words; it must have at most {want}" in msg
            assert f"cut {300 - want} words" in msg and "drop the least important scene" in msg  # a big cut
            return {"script_md": "## s0\nShorter."}
        return {"scenes_js": "export default { s0(s) {} }"}

    monkeypatch.setattr(xr, "run_cli", fake_cli)
    monkeypatch.setattr(xr.llm, "chat_json", fake_chat)
    monkeypatch.setattr(xr, "available", lambda: None)
    monkeypatch.setattr(xr, "_DOCS", {})
    monkeypatch.setattr(xr, "_PKG", {})

    async def progress(m):
        pass
    asyncio.run(xr.run({"name": "X"}, {"prompt": "", "request": {}}, [{"filename": "a.txt", "text": "hi"}],
                       tmp_path / "w", progress))
    assert stages.count("engine.explainer.length") == 1  # 138 s -> shortened; 92 s is within 15 % of 90
    assert (tmp_path / "w" / "explainer" / "script.md").read_text().startswith("## s0\nShorter.")
    voice = [c for c in calls if c.startswith("voice")]
    assert voice == ["voice --estimate", "voice --estimate", "voice"]  # recorded once, after the length is right
    assert 200 < xr.word_budget(90, 9) < 250


def test_the_word_target_comes_from_explainroos_own_estimate():
    # explainroo's estimate of its circle-area example: 195 words, 79.8 s, 65.2 s of it speech
    est = {"words": 195, "seconds": 79.8, "speechSeconds": 65.2}
    assert xr.words_for(est, 79.8) == 195  # the same length keeps the same words
    assert 85 <= xr.words_for(est, 45) <= 95  # 45 s: the pauses stay, so the speech has ~30 s
    assert xr.words_for({"seconds": 50}, 45) is None  # no word count: the budget is used instead
