"""Document Explainer: the user's PDF / Word / text document -> an LLM reads and analyses it -> explainroo makes the
explainer video on this computer.

explainroo (github.com/vincentsch/explainroo, MIT, (c) 2026 Vincent Schmalbach) turns two files into a narrated MP4:
script.md (what the voice says) and scenes.js (drawings that appear on spoken words). Kokoro speaks, Whisper times
every word, Chrome draws the frames and ffmpeg makes the video with music, subtitles, chapters and a cover - all
local, no key. This engine plays explainroo's "coding agent": it follows the tool's own workflow (AGENTS.md):

    analyse      the documents -> facts with their place in the document, the angle, an outline (LLM)
    script.md    the narration first: it sets the timing (LLM, with `explainroo docs writing`)
    voice        explainroo voice (Kokoro + Whisper word times)
    scenes.js    one function per scene, drawings tied to spoken words (LLM, with `docs pictures` and
                 `docs scene-api`, and the real word times from `explainroo words`)
    check        explainroo check; every error and warning goes back to the LLM to fix (up to FIX_ROUNDS)
    render       explainroo render, then explainroo verify

scenes.js is code written from an uploaded document, so it is checked before it runs: no network, no eval, no
dynamic imports (explainroo runs it in a headless Chrome page that can only read the project's media files).
"""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from router_core import config, llm

log = logging.getLogger("palette_video.explainroo")

APP_ID = "explainroo-document-explainer"
VERSION = "0.6.0"  # pinned: the docs the LLM reads and the CLI that runs them are the same version
THEMES = ("studio", "clean", "paper", "chalk", "blueprint", "midnight", "editorial")
SIZES = ("16:9", "9:16", "1:1", "4:5")
LANGUAGES = ("en", "es", "fr", "it", "pt-br", "hi", "ja", "zh")  # Kokoro's (German needs a paid voice)
DOC_CHARS = 60000
FIX_ROUNDS = 3
EXAMPLE = "how-dns-works"  # one of explainroo's own finished projects, shown to the scene writer as a pattern
TIMEOUT_S = 1800
DOC_TOPICS = ("writing", "pictures", "scene-api", "troubleshooting")


class ExplainerError(RuntimeError):
    pass


def cli() -> list[str]:
    """The explainroo command: EXPLAINROO_BIN (a local checkout's bin/explainroo.js) or the pinned npm package."""
    own = os.getenv("EXPLAINROO_BIN")
    if own:
        return ["node", own]
    npx = shutil.which("npx")
    if not npx:
        raise ExplainerError("Node.js (npx) is not installed where VideoStudio runs; explainroo needs Node 20.11+.")
    return [npx, "--yes", f"explainroo@{VERSION}"]


def available() -> str | None:
    """None when explainroo can run here; otherwise what is missing."""
    if not (os.getenv("EXPLAINROO_BIN") or shutil.which("npx")):
        return "Node.js (npx) is not installed"
    if not shutil.which("ffmpeg"):
        return "ffmpeg is not installed"
    return None


async def run_cli(args: list[str], cwd: Path, timeout: int = TIMEOUT_S, want_json: bool = True) -> dict | str:
    """One explainroo command; with --json its report as a dict. A failing command raises with its last words."""
    cmd = cli() + args + (["--json"] if want_json else [])
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ExplainerError(f"explainroo {args[0]} took longer than {timeout // 60} minutes")
    text = out.decode("utf-8", "replace")
    if not want_json:
        if proc.returncode != 0:
            raise ExplainerError(f"explainroo {args[0]} failed: {err.decode('utf-8', 'replace').strip()[-400:]}")
        return text
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if proc.returncode != 0 and not (args[0] == "check" and data.get("issues") is not None):
        why = data.get("error") or err.decode("utf-8", "replace").strip()[-400:] or text[-400:]
        raise ExplainerError(f"explainroo {args[0]} failed: {why}")
    return data


_DOCS: dict[str, str] = {}


async def docs(topic: str, cwd: Path) -> str:
    """explainroo's own documentation for one topic, from the same version that renders (cached per process)."""
    if topic not in _DOCS:
        _DOCS[topic] = str(await run_cli(["docs", topic], cwd, 120, want_json=False))
    return _DOCS[topic]


_PKG: dict[str, Path | None] = {}


async def package_dir(cwd: Path) -> Path | None:
    """Where the explainroo package that renders is installed (from `explainroo doctor`), for its icons and examples."""
    if "dir" not in _PKG:
        try:
            rep = await run_cli(["doctor"], cwd, 300)
            found = next((c.get("detail") for c in rep.get("checks") or [] if c.get("name") == "runs from"), None)
            _PKG["dir"] = Path(found) if found and Path(found).is_dir() else None
        except ExplainerError as e:
            log.warning("explainroo doctor failed: %s", e)
            _PKG["dir"] = None
    return _PKG["dir"]


def icon_set(pkg: Path | None) -> tuple[set[str], dict[str, list[str]]]:
    """explainroo's icon names (Lucide, with its aliases) and each icon's search tags; empty when unknown."""
    if not pkg:
        return set(), {}
    try:
        lucide = json.loads((pkg / "engine" / "icons" / "lucide.json").read_text(encoding="utf-8"))
        tags = json.loads((pkg / "engine" / "icons" / "lucide-tags.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("explainroo icon list unreadable: %s", e)
        return set(), {}
    return set(lucide.get("icons") or {}) | set(lucide.get("aliases") or {}), tags if isinstance(tags, dict) else {}


BRANDS = {"github": "git-branch", "gitlab": "git-merge", "bitbucket": "git-pull-request", "linkedin": "briefcase",
          "twitter": "message-circle", "x": "message-circle", "facebook": "users", "instagram": "camera",
          "youtube": "circle-play", "slack": "messages-square", "discord": "messages-square", "google": "search",
          "aws": "cloud", "azure": "cloud", "gcp": "cloud", "docker": "container", "kubernetes": "boxes",
          "python": "code", "javascript": "code", "react": "atom", "openai": "brain", "apple": "laptop",
          "windows": "monitor", "android": "smartphone", "chrome": "globe", "figma": "pen-tool", "notion": "notebook"}

ICON_USE = re.compile(r"""(?:\.icon\(\s*|\bicon\s*:\s*)(['"])([A-Za-z0-9_-]+)\1""")


def bad_icons(code: str, names: set[str], tags: dict[str, list[str]]) -> list[str]:
    """Problems for every icon scenes.js asks for that explainroo does not have (it fails only at render time), with
    real icons that fit. Brand logos (github, linkedin, ...) are not in Lucide."""
    if not names:
        return []
    out = []
    for wanted in sorted({m.group(2) for m in ICON_USE.finditer(code or "")} - names):
        words = set(re.split(r"[-_]", wanted.lower()))
        near = [BRANDS[w] for w in sorted(words) if BRANDS.get(w) in names]
        near += sorted(n for n, t in tags.items() if words & set(re.split(r"[-_ ]", n)) or words & {str(x).lower() for x in t})
        out.append(f'[error] icon "{wanted}" does not exist in explainroo (no brand logos). Use one that exists, '
                   f"e.g. {', '.join(dict.fromkeys(near[:8])) or 'code, globe, user, briefcase, star'}")
    return out


def example_project(pkg: Path | None) -> str:
    """A finished explainroo project (script.md + scenes.js) as a working pattern for positions, cues and layout."""
    if not pkg:
        return ""
    d = pkg / "examples" / EXAMPLE
    try:
        return (f"EXAMPLE PROJECT (a real, working explainroo video - copy its patterns: x / y positions inside the "
                f"frame, ids, at: cues, out:, at: -1 to keep earlier parts):\n--- script.md ---\n"
                f"{(d / 'script.md').read_text(encoding='utf-8')}\n--- scenes.js ---\n"
                f"{(d / 'scenes.js').read_text(encoding='utf-8')}")
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------------- the LLM steps

ANALYST = """You are the DocumentAnalyst of a video studio. The user uploaded one or more documents and wants an
explainer video made from them. Read them fully, then decide what the video should teach.
Rules: use only what the documents say (and universally established facts); never invent numbers, names, quotes
or claims; keep each fact with where it is in the document (section, heading or page); when the user's request
narrows the topic, follow it.
Return ONLY JSON:
{"title": "2-6 word video title", "language": one of LANGUAGES (the documents' / request's language),
 "audience": "who it is for", "angle": "the one idea the video makes clear",
 "facts": [{"claim": "...", "where": "section / heading / page", "number": "a number it states, or ''"}],
 "outline": [{"scene": "short-id", "idea": "what this scene explains", "show": "what to draw: a diagram, a chart of
  real numbers from the document, a list, a comparison, a timeline, a formula, an icon metaphor"}],
 "target_seconds": the length (the user's if given, else what the content needs: 45-120),
 "theme": one of THEMES (the look that suits the audience: studio for business, clean for products, paper/chalk for
  teaching, blueprint for engineering, midnight for developers, editorial for reports and essays),
 "size": one of SIZES (16:9 unless the user asks for vertical / a short / reel)}"""

SCRIPT = """You write script.md for explainroo, a tool that turns a narration script and drawings into an explainer
video. Below is explainroo's own guide to writing the script - follow it exactly (syntax, scene headings, markers,
{shown|spoken} pairs, length).

{DOCS}

Write the narration from the ANALYSIS: one scene per outline item (the scene heading is its id), only facts from
the analysis, every number and name exactly as there. Speak in the analysis' language.
LENGTH: {BUDGET}
Put [#marker]s before the words where a drawing should appear, so the pictures can land on them.
Every abbreviation, degree, code, version or symbol the voice could misread gets explainroo's {shown|spoken} pair
the first time, e.g. {B.Tech|B Tech}, {CGPA|C G P A}, {GenAI|gen A I}, {RAG|rag}, {7.5|seven point five}.
Return ONLY JSON {"script_md": "the whole script.md"}."""

SHORTEN = """explainroo estimates this script at {SECONDS} s, but the video must be about {TARGET} s.
It has {NOW} spoken words; it must have {WORDS} ({CHANGE}). Count the words of your new script before you answer.
Keep every scene heading and [#marker] of the scenes you keep; {ACTION}. Same syntax and rules as before.
Return ONLY JSON {"script_md": "the whole script.md"}."""

WORDS_PER_S = 3.0  # explainroo's Kokoro pace (180 words a minute, from `voice --estimate`)
SCENE_S = 1.2  # the pause and hold it adds per scene
LENGTH_TOLERANCE = 0.15
LENGTH_ROUNDS = 3


def words_for(est: dict, target_s: float) -> int | None:
    """How many spoken words fit target_s, from explainroo's own estimate of this script: its words and how much of
    its time is speech (the rest - pauses and scene holds - stays about the same)."""
    try:
        words, secs, speech = float(est["words"]), float(est["seconds"]), float(est["speechSeconds"])
    except (KeyError, TypeError, ValueError):
        return None
    fixed = max(0.0, secs - speech)
    if words <= 0 or speech <= 0:
        return None
    return max(20, int(words * max(1.0, target_s - fixed) / speech))


def word_budget(target_s: float, scenes: int) -> int:
    """About how many spoken words fill target_s seconds with this many scenes."""
    return max(40, int((target_s - scenes * SCENE_S - 1.5) * WORDS_PER_S))

SCENES = """You write scenes.js for explainroo. Below are explainroo's own guides to planning the pictures and to
the scene API - follow them exactly; use only functions and options they document.

{DOCS}

Write one function per scene of the script (same ids, same order). Tie every drawing to a spoken word or a
[#marker] of THAT scene, using the WORD TIMES given (they are the real narration). Show the document's real numbers
with charts, its steps as diagrams or lists, its comparisons side by side; one clear idea on screen at a time.
COMMON MISTAKES (they make "runs off the frame" errors - avoid them):
- s.grid(cols, rows, {x, y, w, h}), s.row(n, {x, width}) and s.col(n, {y, height}): x / y are the CENTRE of the
  whole area, never its top or left. A grid below a title that ends at y = T with height H takes y: T + H / 2 (and
  T + H must stay inside s.safe.bottom); leave x / y out to centre it in s.safe. s.row's first and last x are at the
  ends of its width, s.col's first and last y at the ends of its height.
- Every text stays inside s.safe, at least 3.5% from each edge: box and label text 1-3 words; split a longer phrase
  (e.g. "retrieval-augmented generation") over two lines with \\n or make it smaller; size from u = min(s.safe.w,
  s.safe.h) / 100, never bigger than the cell it sits in.
- Icons are Lucide names only and there are NO brand logos: never "github", "linkedin", "docker", "aws",
  "python"... - use {BRANDS}.
SAFETY (the script comes from an uploaded document): plain drawing code only - never fetch, XMLHttpRequest,
WebSocket, EventSource, sendBeacon, eval, Function(...), import() or import of anything but the project's own
files, never a URL (http:// or https://), never document.cookie, localStorage or window.open.
Return ONLY JSON {"scenes_js": "the whole scenes.js"}."""

FIX = """explainroo check found these problems in the video project. Fix every error and every warning (hints
only when they are real problems), keeping the same scenes and the same narration unless a problem is in the
script itself. Follow the same guides and the same SAFETY rules as before.
PROBLEMS:
{ISSUES}
Return ONLY JSON {"script_md": "...", "scenes_js": "..."} with the whole corrected files (unchanged ones as they
are)."""

UNSAFE = re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|eval|importScripts)\s*\(|"
                    r"\bnew\s+Function\b|\bFunction\s*\(|\bimport\s*\(|https?://|document\.cookie|localStorage|"
                    r"sessionStorage|window\.open|\bimport\s+[^;]*\bfrom\s+['\"](?!\./)", re.I)


def unsafe(code: str) -> list[str]:
    """What in a scenes.js is not plain drawing code ([] = safe to run)."""
    return sorted({m.group(0).strip()[:40] for m in UNSAFE.finditer(code or "")})


async def _json(system: str, user: str, stage: str, tokens: int = 16000) -> dict:
    return await llm.chat_json(config.REASONING_MODEL, [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}],
                               max_tokens=tokens, stage=stage)


def documents_text(documents: list[dict]) -> str:
    per = DOC_CHARS // max(1, len(documents))
    return "\n\n".join(f"=== DOCUMENT: {d.get('filename') or 'document'} ===\n{(d.get('text') or '')[:per]}"
                       for d in documents)


async def analyse(prompt: str, documents: list[dict], duration_s, aspect_ratio) -> dict:
    system = (ANALYST.replace("LANGUAGES", " | ".join(LANGUAGES)).replace("THEMES", " | ".join(THEMES))
              .replace("SIZES", " | ".join(SIZES)))
    user = (f"REQUEST: {prompt.strip() or 'Explain this document.'}\n"
            f"LENGTH: {f'{duration_s} seconds' if duration_s else 'not set'}. FORMAT: {aspect_ratio or 'not set'}.\n\n"
            + documents_text(documents))
    a = await _json(system, user, "engine.explainer.analyse", 8000)
    a["language"] = a.get("language") if a.get("language") in LANGUAGES else "en"
    a["theme"] = a.get("theme") if a.get("theme") in THEMES else "studio"
    a["size"] = aspect_ratio if aspect_ratio in SIZES else (a.get("size") if a.get("size") in SIZES else "16:9")
    try:
        a["target_seconds"] = int(duration_s or a.get("target_seconds") or 75)
    except (TypeError, ValueError):
        a["target_seconds"] = 75
    if not a.get("outline"):
        raise ExplainerError("the analysis found nothing to explain in the document")
    return a


def _issues(report: dict) -> list[dict]:
    return [i for i in report.get("issues") or [] if i.get("level") in ("error", "warn")]


# ------------------------------------------------------------------------------------------------- the engine

async def run(app: dict, handoff: dict, documents: list[dict], work: Path, progress) -> dict:
    """Make the explainer. Returns {"path", "duration_s", "title", "analysis", "subtitles", "chapters", "cover",
    "verify", "check"}."""
    why = available()
    if why:
        raise ExplainerError(f"explainroo cannot run here: {why}")
    docs_in = [d for d in documents if (d.get("text") or "").strip()]
    if not docs_in:
        raise ExplainerError("no readable document was uploaded (PDF, Word, TXT or Markdown)")
    name = app["name"]
    req = handoff.get("request") or {}
    work.mkdir(parents=True, exist_ok=True)
    project = work / "explainer"

    await progress(f"{name}: reading and analysing {len(docs_in)} document{'s' if len(docs_in) > 1 else ''}")
    a = await analyse(handoff.get("prompt") or "", docs_in, req.get("duration") if req.get("duration_given") else None,
                      req.get("aspect_ratio"))
    await progress(f"{name}: \"{a.get('title') or 'the explainer'}\" - {len(a['outline'])} scenes, "
                   f"{len(a.get('facts') or [])} facts from the document, {a['theme']} look")

    if project.exists():
        shutil.rmtree(project)
    await run_cli(["init", str(project), "--theme", a["theme"], "--size", a["size"], "--language", a["language"],
                   "--title", str(a.get("title") or "Explainer")[:80]], work, 300)
    for key, value in (("targetSeconds", str(a["target_seconds"])), ("watermark", "false")):
        await run_cli(["set", str(project), key, value], work, 120)

    analysis = json.dumps({k: a.get(k) for k in ("title", "angle", "audience", "facts", "outline", "target_seconds",
                                                 "language")}, ensure_ascii=False)
    await progress(f"{name}: writing the narration")
    target, n_scenes = a["target_seconds"], len(a["outline"])
    budget = word_budget(target, n_scenes)
    script_system = SCRIPT.replace("{DOCS}", await docs("writing", work)).replace(
        "{BUDGET}", f"the video is {target} s with {n_scenes} scenes, so write about {budget} spoken words in total "
                    f"(about {max(8, budget // max(1, n_scenes))} a scene); explainroo speaks about 180 words a minute "
                    f"and adds about {SCENE_S} s per scene.")
    s = await _json(script_system, f"ANALYSIS:\n{analysis}", "engine.explainer.script")
    script = str(s.get("script_md") or "").strip()
    if not script:
        raise ExplainerError("the script writer returned no script")
    (project / "script.md").write_text(script + "\n", encoding="utf-8")
    # The length is checked BEFORE the voice is made (voice --estimate makes nothing): a script far off the target
    # is rewritten to length, so the film is not minutes too long and nothing is recorded twice.
    for _ in range(LENGTH_ROUNDS):
        try:
            est = (await run_cli(["voice", str(project), "--estimate"], work, 300)).get("estimate") or {}
        except ExplainerError as e:
            log.warning("length estimate failed: %s", e)
            break
        secs = float(est.get("seconds") or 0)
        if not secs or abs(secs - target) <= target * LENGTH_TOLERANCE:
            break
        longer = secs > target
        await progress(f"{name}: the narration is about {secs:.0f} s for a {target} s video; "
                       f"{'shortening' if longer else 'lengthening'} it")
        now = int(float(est.get("words") or 0)) or None
        want = words_for(est, target) or budget
        change = (f"cut {now - want} words, about {round(100 * (now - want) / now)}%" if now and longer else
                  f"add {want - now} words" if now else "")
        big_cut = bool(now) and longer and (now - want) > 0.3 * now
        action = ("keep the most important facts exactly and cut the rest: shorter sentences, no side details, no "
                  "repeated points" + ("; you may drop the least important scene(s) entirely" if big_cut else "")
                  if longer else "add the next most useful facts from the analysis, exactly as there")
        r = await _json(script_system, f"ANALYSIS:\n{analysis}\n\nCURRENT script.md:\n{script}\n\n"
                        + SHORTEN.replace("{SECONDS}", f"{secs:.0f}").replace("{TARGET}", str(target))
                        .replace("{NOW}", str(now or "?")).replace("{WORDS}", f"at most {want}" if longer else
                                                                   f"about {want}")
                        .replace("{CHANGE}", change or "fit the length").replace("{ACTION}", action),
                        "engine.explainer.length")
        new = str(r.get("script_md") or "").strip()
        if not new:
            break
        script = new
        (project / "script.md").write_text(script + "\n", encoding="utf-8")

    await progress(f"{name}: recording the voice and timing every word")
    await run_cli(["voice", str(project)], work)
    words = await run_cli(["words", str(project)], work, 300)

    await progress(f"{name}: drawing the scenes")
    pkg = await package_dir(work)
    names, tags = icon_set(pkg)
    guides = "\n\n".join([await docs("pictures", work), await docs("scene-api", work), await docs("troubleshooting", work),
                           example_project(pkg)])
    scenes_system = SCENES.replace("{DOCS}", guides).replace(
        "{BRANDS}", ", ".join(f"{b} for {k}" for k, b in list(BRANDS.items())[:14]))
    scenes_user = (f"ANALYSIS:\n{analysis}\n\nSCRIPT (script.md):\n{script}\n\n"
                   f"WORD TIMES (explainroo words):\n{json.dumps(words, ensure_ascii=False)[:30000]}")
    sj = await _json(scenes_system, scenes_user, "engine.explainer.scenes", 24000)
    code = str(sj.get("scenes_js") or "")
    from . import story_engine
    fast = story_engine.quality() is story_engine.QUALITY["test"]
    report: dict = {}
    out: dict = {}
    for round_ in range(FIX_ROUNDS + 1):
        last = round_ == FIX_ROUNDS
        bad = unsafe(code)
        if bad:  # never run it: ask for a clean version
            problems = [f"scenes.js uses {', '.join(bad)} - not allowed (SAFETY); draw with the scene API only"]
        else:
            (project / "scenes.js").write_text(code, encoding="utf-8")
            await progress(f"{name}: checking the layout, timing and pronunciation"
                           + (f" (fix {round_})" if round_ else ""))
            report = await run_cli(["check", str(project)], work)
            problems = bad_icons(code, names, tags) + [
                f"[{i.get('level')}] scene {i.get('scene') or '-'}"
                + (f" at {i['t']} s" if i.get("t") is not None else "") + f": {i.get('message')}"
                for i in _issues(report)]
        if bad and last:
            raise ExplainerError("the drawing code kept using things that are not allowed: " + ", ".join(bad))
        if not bad and (not problems or last):
            if problems:
                await progress(f"{name}: {len(problems)} check note{'s' if len(problems) > 1 else ''} left; rendering anyway")
            await progress(f"{name}: rendering the video{' (fast quality)' if fast else ' with motion blur'}")
            try:
                out = await run_cli(["render", str(project)] + (["--quality", "fast"] if fast else []), work)
                break
            except ExplainerError as e:  # a scene that only fails while drawing: back to the writer, once more
                if last:
                    raise
                problems = [f"[error] render failed: {str(e)[:600]}"]
        await progress(f"{name}: fixing {len(problems)} problem{'s' if len(problems) > 1 else ''} ({problems[0][:90]})")
        fixed = await _json(scenes_system + "\n\n" + FIX.replace("{ISSUES}", "\n".join(problems[:40])),
                            f"{scenes_user}\n\nCURRENT scenes.js:\n{code}", "engine.explainer.fix", 24000)
        new_script = str(fixed.get("script_md") or "").strip()
        if new_script and new_script != script:  # the narration changed: voice and word times again
            script = new_script
            (project / "script.md").write_text(script + "\n", encoding="utf-8")
            await run_cli(["voice", str(project)], work)
        code = str(fixed.get("scenes_js") or code)

    path = out.get("out") or str(project / "out" / "video.mp4")
    if not Path(path).exists():
        raise ExplainerError("explainroo rendered no video file")
    await progress(f"{name}: verifying loudness, black frames and speech")
    try:
        ver = await run_cli(["verify", str(project)], work, 600)
    except ExplainerError as e:  # the video exists; the report is a nicety
        ver = {"verdict": f"not verified ({e})"}
    return {"path": path, "duration_s": float(ver.get("duration") or 0) or None, "title": a.get("title") or "",
            "analysis": a, "subtitles": (out.get("subtitles") or {}).get("srt"), "chapters": out.get("chapters"),
            "cover": out.get("cover"), "verify": ver.get("verdict") or ("passed" if ver.get("passed") else ""),
            "check": [i.get("message") for i in _issues(report)][:10], "script": script}


# ------------------------------------------------------------------------------------------------- the plan

def documents_of(run: dict) -> list[dict]:
    return [d for d in (run.get("documents") or {}).values() if (d.get("text") or "").strip()]


def wanted(run: dict) -> bool:
    """Uploaded documents become an explainer: in the Documentary mode (and the older Document Explainer mode) whenever
    a document is attached (with none, vox_doc_engine makes the topic a VOX documentary); in Auto when the run has a document and no footage or audio of
    the user's (those are edited or filmed by the other apps)."""
    mode = run.get("mode")
    has_doc = any(a.get("modality") == "document" for a in (run.get("assets") or {}).values())
    if mode in ("documentary", "explainer"):  # the one Documentary mode: a file is explained, a topic is a VOX film
        return has_doc
    footage = any(a.get("modality") in ("video", "audio") for a in (run.get("assets") or {}).values())
    if mode == "auto" and has_doc:  # a novel or story text to turn into a short drama goes to the drama director
        from .story_intel import drama
        if drama.asks_for_drama(run.get("prompt") or ""):
            return False
    return mode == "auto" and has_doc and not footage and not run.get("parent_run")


def build(run: dict) -> dict:
    """{"understanding", "plan"}: one step, the Document Explainer, with the user's words and settings."""
    from router_core import registry
    app = registry.apps()[APP_ID]
    st = run.get("settings") or {}
    docs_ = documents_of(run)
    names = ", ".join(d.get("filename") or "document" for d in docs_) or "the uploaded document"
    words = (run.get("prompt") or "").strip()
    dur = st.get("duration_s")
    ar = st.get("aspect_ratio")
    caps = ["explainer.from_document"]
    step = {"step_id": "s1", "app_id": APP_ID, "subtask_ids": ["t_explain"], "capabilities_used": caps,
            "responsibility": f"Read and analyse {names}, write the narration and the drawings from it, and render "
                              "the explainer video with explainroo (voice, word timing, music, subtitles, chapters).",
            "inputs": [{"source": "prompt", "format": "text"}], "outputs": ["video_file"],
            "handoff": {"app_id": APP_ID, "app_name": app["name"], "prompt": words, "language": "", "endpoint": "",
                        "request": {"prompt": words, "duration": dur, "duration_given": bool(dur),
                                    "aspect_ratio": ar, "documents": [d.get("filename") for d in docs_]},
                        "notes": []}}
    understanding = {
        "detected_intent": "create_new", "user_goal": words or f"An explainer video of {names}",
        "goal": words or f"An explainer video of {names}",
        "requested_output": f"an explainer video of {names}" + (f", {dur} s" if dur else "") + (f", {ar}" if ar else ""),
        "production_type": ["explainer"], "assumptions": ["the video explains only what the documents say"],
        "scene": {"narrative": False}, "constraints": {"duration_s": dur, "aspect_ratio": ar},
        "subtasks": [{"id": "t_explain", "group": "generate", "description": f"Explain {names}",
                      "required_capabilities": caps, "basis": "uploaded documents"}],
        "direct": True, "confidence": 1.0}
    return {"understanding": understanding, "plan": {"feasible": True, "steps": [step], "direct": True}}
