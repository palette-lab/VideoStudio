"""Motion Studio: the LLM's HyperFrames composition is linted, repaired and rendered (real HyperFrames, tiny render)."""

import asyncio
import json

import pytest

from engines import motion_engine

pytestmark = pytest.mark.skipif(motion_engine.available() is not None, reason="HyperFrames runtime not installed")

GOOD = """<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111}
#root{position:relative;width:1080px;height:1080px;overflow:hidden;font-family:Arial,sans-serif;color:#fff}
#t{position:absolute;top:460px;width:100%;text-align:center;font-size:96px;font-weight:800}</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="2" data-width="1080" data-height="1080" data-fps="30">
<div id="t">PALETTE</div></div><script src="assets/gsap.min.js"></script><script>
const tl = gsap.timeline({paused:true}); tl.fromTo("#t",{y:40,opacity:0},{y:0,opacity:1,duration:1,ease:"power3.out"},0);
tl.to({}, {duration:1}, 1); window.__timelines = window.__timelines || {}; window.__timelines["main"] = tl;
</script></body></html>"""


def test_writes_repairs_and_renders(tmp_path, monkeypatch):
    replies = ["TITLE: Bad\n```html\n<div>no composition</div>\n```", f"TITLE: Palette\n```html\n{GOOD}\n```"]
    seen = []

    async def fake_chat(model, messages, **kw):
        seen.append(list(messages))
        return replies[len(seen) - 1]

    monkeypatch.setattr(motion_engine.llm, "chat_text", fake_chat)

    async def fake_review(video, dur, work, brief, times=None):
        return []  # the frames look right

    monkeypatch.setattr(motion_engine, "_visual_review", fake_review)
    msgs = []

    async def progress(m):
        msgs.append(m)

    out = asyncio.run(motion_engine.run({"app_id": "palette-motion-studio", "name": "Motion Studio"},
                                        {"prompt": "Kinetic title PALETTE", "request": {"aspect_ratio": "1:1", "duration": 2}},
                                        {}, None, tmp_path, progress, {"resolution": "480p", "max_total": 10}))
    assert len(seen) == 2 and "missing the root composition" in seen[1][-1]["content"]  # one repair round
    assert out["open_issues"] == []  # the good composition passes hyperframes check
    assert out["title"] == "Palette" and 1.9 < out["duration_s"] < 2.1 and out["size"] == "1080x1080"
    assert any("rendering" in m for m in msgs)
    system = seen[0][0]["content"]
    assert 'data-duration="2"' in system and "window.__timelines" in system and "requestAnimationFrame" in system
    assert "SAFE AREA" in system and "data-layout-allow-occlusion" in system


def test_parse_tolerates_missing_fence_and_title():
    assert motion_engine._parse("TITLE: X\n```html\n<html>a</html>\n```") == ("X", "<html>a</html>")
    assert motion_engine._parse("Here it is <!doctype html><html></html>") == ("", "<!doctype html><html></html>")
    assert motion_engine._parse("TITLE: Cut\n```html\n<html>truncated")[1] == "<html>truncated"


def test_a_visual_problem_triggers_one_repair_and_rerender(tmp_path, monkeypatch):
    replies = [f"TITLE: First\n```html\n{GOOD}\n```", f"TITLE: Fixed\n```html\n{GOOD.replace('PALETTE', 'FIXED')}\n```"]
    seen, reviews = [], []

    async def fake_chat(model, messages, **kw):
        seen.append(list(messages))
        return replies[len(seen) - 1]

    async def fake_review(video, dur, work, brief, times=None):
        reviews.append(1)
        return ["The category labels under the bars are cut off at the bottom edge."]

    async def progress(m):
        pass

    monkeypatch.setattr(motion_engine.llm, "chat_text", fake_chat)
    monkeypatch.setattr(motion_engine, "_visual_review", fake_review)
    out = asyncio.run(motion_engine.run({"app_id": "palette-motion-studio", "name": "Motion Studio"},
                                        {"prompt": "title", "request": {"aspect_ratio": "1:1", "duration": 2}},
                                        {}, None, tmp_path, progress, {"resolution": "480p", "max_total": 10}))
    assert len(reviews) == 1 and len(seen) == 2  # reviewed once, repaired once, not reviewed again
    assert "cut off at the bottom edge" in seen[1][-1]["content"] and out["title"] == "Fixed"


def test_chart_requests_use_the_tested_template(tmp_path, monkeypatch):
    async def fake_spec(model, messages, **kw):
        return {"title": "Q3 Revenue", "unit": "$M", "categories": ["Seoul", "Busan", "Tokyo", "LA"],
                "values": [38, 25, 47, 19], "highlight": 2, "highlight_label": "Top region",
                "end_line": {"text": "Tokyo leads", "accent": "+18%"}}

    async def no_text(*a, **kw):
        raise AssertionError("a chart with data must not be free-form written")

    async def progress(m):
        pass

    monkeypatch.setattr(motion_engine.llm, "chat_json", fake_spec)
    monkeypatch.setattr(motion_engine.llm, "chat_text", no_text)
    out = asyncio.run(motion_engine.run({"app_id": "palette-motion-studio", "name": "Motion Studio"},
                                        {"prompt": "Q3 sales chart", "request": {"aspect_ratio": "16:9", "duration": 4}},
                                        {}, None, tmp_path, progress, {"resolution": "480p", "max_total": 10},
                                        ["motion.data_visualization"]))
    assert out["template"] == "bar_chart" and out["size"] == "1920x1080" and out["open_issues"] == []
    assert 3.9 < out["duration_s"] < 4.1


def test_end_line_accent_is_not_repeated():
    from engines.templates import bar_chart
    html = bar_chart.render({"categories": ["A", "B"], "values": [1, 2],
                             "end_line": {"text": "Tokyo leads +18%", "accent": "+18%"}}, 1920, 1080, 6)
    assert html.count("+18%") == 1 and ">Tokyo leads</span>" in html


def test_graphics_on_footage_use_the_footage_shape_and_flag_white_catalogue_photos(tmp_path):
    import subprocess
    from engines import motion_engine as me
    assert me.ratio_of(1080, 1920) == "9:16" and me.ratio_of(720, 1280) == "9:16" and me.ratio_of(1920, 1080) == "16:9"
    white, scene = tmp_path / "white.png", tmp_path / "scene.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=200x200", "-vf",
                    "drawbox=x=70:y=40:w=60:h=120:color=pink:t=fill", "-frames:v", "1", str(white)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=200x200", "-frames:v", "1",
                    str(scene)], check=True)
    assert me.plain_background(str(white)) and not me.plain_background(str(scene))


def test_motion_studio_adds_no_second_round_of_effects_after_a_filming_step():
    from engines import adapter
    run = {"plan": {"steps": [{"step_id": "s1", "app_id": "pltt-storyboard-video-maker"},
                              {"step_id": "s2", "app_id": "palette-motion-studio"}]}}
    assert adapter._earlier_did_effects(run, {"step_id": "s2"})
    assert not adapter._earlier_did_effects(run, {"step_id": "s1"})
    solo = {"plan": {"steps": [{"step_id": "s1", "app_id": "palette-motion-studio"}]}}
    assert not adapter._earlier_did_effects(solo, {"step_id": "s1"})
