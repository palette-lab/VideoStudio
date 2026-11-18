"""Programmatic Film (Lemo-Opuscar's renderer, our own looks): routing, the page rules, the timeline, the plan."""

from engines import opuscar_engine as oc
from router_core import config, registry, validator
from tests.conftest import REAL_REGISTRY

GOOD = """<!doctype html><html><head><link rel="stylesheet" href="/core/fonts/fonts.css"></head><body><canvas></canvas>
<script type="module">import { beatAt, textOn } from '/kit/vs.js'; import { ss } from '/core/lib.js';
window.DUR = 4; window.EV = []; window.render = t => {}; window.TEXTS = t => []; window.READY = true;</script></body></html>"""


def test_routing_by_mode_words_and_look():
    run = lambda prompt, mode="auto", **kw: {"mode": mode, "prompt": prompt, "assets": {}, **kw}
    assert oc.wanted(run("anything", "programmatic"))
    assert oc.wanted(run("a programmatic video about how vaccines work"))
    assert oc.wanted(run("explain compound interest in chalkboard style"))
    assert oc.named_style("a whiteboard animation of the water cycle") == "whiteboard"
    assert oc.named_style("16-bit pixel art recap of our year") == "pixel-rpg"
    assert oc.named_style("a cinematic film of a cat") is None
    assert not oc.wanted(run("a cinematic film of a cat"))
    assert not oc.wanted(run("chalkboard style", assets={"a": {"modality": "document"}}))  # documents: the explainer
    assert not oc.wanted(run("chalkboard style", assets={"a": {"modality": "video"}}))  # footage: the editors
    assert not oc.wanted(run("chalkboard style", parent_run="p1"))


def test_page_rules():
    assert oc.lint(GOOD) == []
    bad = GOOD.replace("window.READY = true;", "x = Math.random(); fetch('/x'); setTimeout(f, 1);") \
              .replace("/core/lib.js", "https://cdn.example.com/lib.js")
    problems = " | ".join(oc.lint(bad))
    for p in ("Math.random(", "fetch(", "setTimeout(", "https://cdn.example.com/lib.js", "READY"):
        assert p in problems
    assert oc.parse_html("Here:\n```html\n<!doctype html><p>x</p>\n```") == "<!doctype html><p>x</p>"


def test_timeline_follows_the_spoken_lines():
    beats = [{"id": "b1", "line": "One two three four five", "sfx": ["pop", "bogus"]},
             {"id": "b2", "line": "Six seven eight nine ten eleven twelve thirteen", "sfx": []}]
    tl, dur = oc.timeline(beats, [2.0, None])
    assert tl[0]["line_t0"] == oc.LEAD_S and tl[0]["line_t1"] == oc.LEAD_S + 2.0
    assert tl[1]["t0"] == tl[0]["t1"] and tl[0]["sfx"] == ["pop"]
    assert abs((tl[1]["line_t1"] - tl[1]["line_t0"]) - 8 / oc.WORDS_PER_S) < 1e-6  # no audio: reading pace
    assert dur == tl[-1]["t1"] and dur > tl[-1]["line_t1"] + 1
    subs = oc.srt(tl)
    assert subs.startswith("1\n00:00:00,600 --> 00:00:02,600\nOne two three four five")
    ev = oc.cues(tl, [{"t": 0.9, "type": "pop"}, {"t": 99, "type": "pop"}, {"t": 1, "type": "laser"}], dur)
    assert ev == [{"t": 0.9, "type": "pop"}]  # the page placed beat 1's pop; out-of-range and unknown cues dropped


def test_every_look_is_complete_and_our_own():
    assert len(oc.STYLES) == 12
    for k, v in oc.STYLES.items():
        assert v["name"] and v["suits"] and len(v["look"]) > 150 and v["motion"] and v["asks"]
        assert isinstance(v["grain"], int)


def test_plan_is_one_valid_step(monkeypatch):
    monkeypatch.setattr(config, "REGISTRY_DIR", REAL_REGISTRY)
    registry.apps.cache_clear(); registry.capabilities.cache_clear()
    run = {"mode": "programmatic", "prompt": "how a bill becomes law, newsreel style", "assets": {},
           "settings": {"duration_s": 40, "aspect_ratio": "9:16"}}
    d = oc.build(run)
    step = d["plan"]["steps"][0]
    assert step["app_id"] == oc.APP_ID and step["handoff"]["request"]["style"] == "newsreel"
    assert "Newsreel look" in d["understanding"]["requested_output"]
    issues = validator.validate(d["plan"], d["understanding"], "programmatic", {})
    assert not validator.errors(issues), issues
    registry.apps.cache_clear(); registry.capabilities.cache_clear()


def test_realistic_looks_use_the_image_model(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "x")
    assert oc.can_illustrate("chalkboard") and oc.can_illustrate("newsreel")
    assert not oc.can_illustrate("dataviz") and not oc.can_illustrate("swiss-motion")  # these stay drawn in code
    monkeypatch.delenv("FAL_KEY")
    assert not oc.can_illustrate("chalkboard")  # no image model: the film is drawn in code
    tl, dur = oc.timeline([{"id": "b1", "line": "A prism splits white light.", "image": "a prism and a rainbow",
                            "words": "white light"}], [2.0])
    blank, beats = oc.image_prompts("chalkboard", tl)
    assert "chalkboard" in blank and "Nothing is drawn" in blank and "No letters" in blank
    assert "SAME surface as the reference" in beats[0] and "a prism and a rainbow" in beats[0] and "No letters" in beats[0]
    page = oc.illustrated_page("chalkboard", tl, dur, ["img/blank.png", "img/b1.png"])
    assert oc.lint(page) == [] and '"Chalkduster"' in page and '"reveal": "draw"' in page and "img/b1.png" in page
