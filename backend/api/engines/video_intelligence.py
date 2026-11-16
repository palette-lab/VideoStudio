"""Video Intelligence agent: the user's "Universal Motion Graphics & Video Generation Agent" master prompt.

One agent, called only when it is useful: the agent picker (engines/prompt_enhancer.py) chooses it for motion
graphics, 3D / CGI product animation, exploded views, tech / SaaS / data explainers and abstract concepts - also when
the user never wrote "3D". The master prompt (prompts/video_intelligence_master.md) is its system prompt, verbatim,
plus the live app capability registry, so everything it plans is something an available app can really do.

Its job here is the creative-director front end: classify, style-lock, choose techniques, enhance, storyboard
(Establish / Develop / Resolve) and name limitations. The platform's own router, approval gate, execution,
checkpoints and validation then do the generation - the agent never claims to have generated anything.
"""

import json
from pathlib import Path

from router_core import registry

MASTER = (Path(__file__).parent / "prompts" / "video_intelligence_master.md").read_text(encoding="utf-8")

HERE = """

---
## THIS PLATFORM (runtime information - use it, never assume more)

You are at the PROMPT ENHANCEMENT + STORY AND SCENE PLANNING stage. After you, the platform's own router matches
capabilities, builds the workflow graph, asks the user for approval, generates, checkpoints and validates. So do
NOT claim anything was generated; plan only what the apps below can really do.

AVAILABLE APPLICATIONS (registry):
{APPS}

How "3D" is really produced here (be honest about it):
- Photoreal / CGI-look 3D (studio product renders, rotations, macro material shots, floating parts, cinematic light)
  -> Storyboard & Video or Flow Video: AI-generated clips of 3-15 s each, chained scene to scene.
- Precise 2.5D / CSS-3D motion graphics (kinetic type, infographics, diagrams, data charts, layered parallax,
  stylised exploded diagrams with leader lines, product turntables from an image) -> Motion Studio (HTML/GSAP,
  rendered exactly, max 60 s). Numbers and text are exact here - use it for any precise typography or data.
- Both can be combined: AI footage first, Motion Studio graphics on top.
An exploded view of internal components: real separation only as an AI clip (approximate), or as a clearly
stylised Motion Studio diagram. Never invent internal parts or specifications.

Context from the composer:
- Length: {LENGTH}. Format: {FORMAT}. Files attached: {FILES}.
- Keep every number, name, label and data value from the user EXACTLY; add none.

OUTPUT: return ONLY a JSON object:
{"categories": ["primary category", "secondary ..."],
 "creative_direction": "what the viewer should experience, in one or two sentences",
 "style_lock": "STYLE LOCK: ... (palette with HEX values, typography, lighting identity, materials, background
                language, graphic elements, animation principles, transition language)",
 "three_d": "none | cgi_render | css_2_5d | both",
 "techniques": ["only the techniques this video needs, from section 4.3"],
 "scenes": [{"n": 1, "seconds": 5, "purpose": "...", "narration": "the exact line or ''",
             "establish": "...", "develop": "...", "resolve": "...", "camera": "...", "effects": "...",
             "on_screen_text": "minimal, or ''", "transition": "how it hands off to the next scene"}],
 "suggested_apps": ["actual application names from the registry"],
 "limitations": ["what the available apps cannot do exactly, and the workaround chosen"],
 "enhanced_prompt": "the complete production-ready prompt: start with the STYLE LOCK line, then the creative
                     direction, then every scene in order with its establish / develop / resolve, camera, effects,
                     on-screen text and transition"}"""


def apps_summary() -> str:
    lines = []
    for a in registry.apps().values():
        if a.get("status") != "available":
            continue
        p = a.get("profile") or {}
        val = lambda k: (p.get(k) or {}).get("value")
        lines.append(f"- {a['name']}: {', '.join(a.get('capabilities') or [])}; max {val('max_duration_s')} s; "
                     f"formats {', '.join(val('aspect_ratios') or [])}; best for: "
                     f"{'; '.join((a.get('best_for') or [])[:3])}; not good for: {'; '.join((a.get('not_good_for') or [])[:2])}")
    return "\n".join(lines)


def messages(prompt: str, duration_s, aspect_ratio, files: list[str], first_pass: str = "") -> list[dict]:
    system = MASTER + (HERE.replace("{APPS}", apps_summary())
                       .replace("{LENGTH}", f"{duration_s:g} s" if duration_s else "not set (choose a fitting short length)")
                       .replace("{FORMAT}", aspect_ratio or "not set")
                       .replace("{FILES}", ", ".join(files[:10]) if files else "none"))
    user = f"USER INPUT:\n{prompt.strip()}"
    if first_pass:
        user += f"\n\nA first enhancement (use what helps, the user's input wins):\n{first_pass[:4000]}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def clean(data, original: str) -> dict:
    """Validated agent output; the enhanced prompt always carries the style lock."""
    d = data if isinstance(data, dict) else {}
    lock = str(d.get("style_lock") or "").strip()
    if lock and not lock.upper().startswith("STYLE LOCK"):
        lock = "STYLE LOCK: " + lock
    prompt = str(d.get("enhanced_prompt") or "").strip()
    if prompt and lock and "STYLE LOCK" not in prompt.upper():
        prompt = f"{lock}\n\n{prompt}"
    scenes = [s for s in (d.get("scenes") or []) if isinstance(s, dict)][:20]
    return {"categories": [str(c)[:60] for c in (d.get("categories") or [])][:4],
            "creative_direction": str(d.get("creative_direction") or "")[:400],
            "style_lock": lock[:1200],
            "three_d": d.get("three_d") if d.get("three_d") in ("none", "cgi_render", "css_2_5d", "both") else "none",
            "techniques": [str(t)[:80] for t in (d.get("techniques") or [])][:12],
            "scenes": [{k: (s.get(k) if isinstance(s.get(k), (int, float)) else str(s.get(k) or "")[:600])
                        for k in ("n", "seconds", "purpose", "narration", "establish", "develop", "resolve", "camera",
                                  "effects", "on_screen_text", "transition")} for s in scenes],
            "suggested_apps": [str(a)[:60] for a in (d.get("suggested_apps") or [])][:4],
            "limitations": [str(x)[:300] for x in (d.get("limitations") or [])][:8],
            "prompt": prompt or original.strip()}


# Downstream: how the engines honour the agent's style lock (cheap; only when the brief carries one).
ENGINE_RULES = """

STYLE LOCK (when the brief starts with "STYLE LOCK:"): it is the film's locked look - use its HEX palette,
typography, lighting identity, materials, background language and animation principles in EVERY scene/frame, and
never change the style between scenes. Scenes written as ESTABLISH / DEVELOP / RESOLVE keep that order inside the
clip; each RESOLVE hands off to the next scene's ESTABLISH."""


def summary_json(vi: dict) -> str:
    return json.dumps({k: vi[k] for k in ("categories", "three_d", "techniques", "limitations")}, ensure_ascii=False)
