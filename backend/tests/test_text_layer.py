"""Editable text layer: words live in a layer over the clean video, never in the generated pixels."""

import asyncio

from engines import text_layer as tl


def test_items_are_cleaned_and_kept_inside_the_frame_and_the_film():
    lay = tl.layer([{"id": "a", "text": "  Hi  ", "start": -3, "end": 99, "x": 140, "y": -5, "size": 80,
                     "color": "red", "style": "chrome", "font": "comic", "kind": "weird"},
                    {"id": "a", "text": "dup id", "start": 1, "end": 1.1},
                    {"text": ""}], 1080, 1920, 10.0)
    a, b = lay["items"]
    assert a["text"] == "Hi" and a["start"] == 0 and a["end"] == 10 and a["x"] == 100 and a["y"] == 0
    assert a["size"] == 30 and a["color"] == "#FFFFFF" and a["style"] == "flat" and a["font"] == "display"
    assert a["kind"] == "keyword" and b["id"] != a["id"] and b["end"] - b["start"] >= tl.MIN_S
    assert lay["css"] == tl.CSS and lay["width"] == 1080


def test_graphics_plan_becomes_editable_text_without_shapes():
    gp = {"design": {"accent": "#FF5500"},
          "hook": {"text": "Money grows", "sub": "on money", "start": 0.2, "end": 2.5},
          "overlays": [{"kind": "kinetic_text", "text": "8% a year", "start": 3, "end": 5},
                       {"kind": "callout", "text": "Interest", "detail": "earned on interest", "start": 6, "end": 8,
                        "target": {"x": 60, "y": 50, "w": 20, "h": 10}},
                       {"kind": "arrow", "start": 6, "end": 8},
                       {"kind": "lower_third", "text": "low", "start": 9, "end": 10, "place": "bottom"}],
          "end_card": {"title": "Start early", "line": "Time does the work", "cta": "Subscribe", "start": 12}}
    items = tl.from_graphics(gp, 15.0, captions_band=(74, 84))
    texts = [i["text"] for i in items]
    assert texts[:2] == ["Money grows", "on money"] and "8% a year" in texts
    assert "Interest\nearned on interest" in texts and len([t for t in texts if t == ""]) == 0
    assert all(i["kind"] != "arrow" for i in items)
    assert next(i for i in items if i["kind"] == "hook")["style"] == "3d"
    low = next(i for i in items if i["text"] == "low")
    assert low["y"] < 74  # moved out of the captions band
    assert [i["text"] for i in items if i["kind"] in ("end_title", "end_line", "cta")] == \
        ["Start early", "Time does the work", "Subscribe"]


def test_captions_from_the_transcript_and_srt_export():
    caps = tl.captions([{"start": 0.0, "end": 4.0, "text": "money grows on money over time"}], (74, 84), 4)
    assert [c["text"] for c in caps] == ["money grows on money", "over time"]
    assert caps[0]["start"] == 0 and caps[1]["end"] == 4.0 and caps[0]["y"] == 79
    srt = tl.to_srt(tl.layer(caps, 1080, 1920, 5))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,670\nmoney grows on money")


def test_3d_scene_words_become_3d_text_items():
    items = tl.from_scene_words([{"overlay_text": "spends money"}, {"overlay_text": ""}], [(0, 4), (4, 9)])
    assert len(items) == 1 and items[0]["style"] == "3d" and items[0]["start"] == 0.3 and items[0]["end"] == 3.8


def test_the_render_composition_has_every_item_and_one_seekable_timeline():
    lay = tl.layer([tl.item("hook", "Hello <b>", 0.2, 2, style="3d", n=0),
                    tl.item("caption", "world", 1, 3, n=1)], 1080, 1920, 4)
    html = tl.composition(lay)
    assert 'data-duration="4"' in html and 'id="base" src="assets/base.mp4"' in html
    assert "Hello &lt;b&gt;" in html and "tl-3d" in html and "tl-flat" in html
    assert html.count('tl.fromTo("#') == 2 and 'window.__timelines["main"] = tl' in html
    assert tl.shade("#FFFFFF").startswith("#72")


def test_finish_makes_a_clean_video_and_an_editable_layer(monkeypatch, tmp_path):
    from engines import effects_engine, finishing, motion_engine

    calls = {}

    async def fake_effects(app, handoff, video, work, progress):
        calls["effects"] = handoff
        return {"path": "/w/fx.mp4", "plan": [{"fx": "lean-in", "start": 1, "end": 2}],
                "transcript": [{"start": 0, "end": 2, "text": "money grows"}]}

    async def fake_plan(*a, **kw):
        calls["graphics"] = True
        return {"hook": {"text": "Compound interest", "start": 0.2, "end": 2}}

    async def fake_burn(clean, lay, work, progress=None, label=""):
        calls["burn"] = (clean, len(lay["items"]))
        return "/w/with_text.mp4"

    async def fake_size(path):
        return (1080, 1920)

    async def progress(m):
        pass

    monkeypatch.setattr(effects_engine, "available", lambda: None)
    monkeypatch.setattr(effects_engine, "run", fake_effects)
    monkeypatch.setattr(finishing, "plan_graphics", fake_plan)
    monkeypatch.setattr(tl, "burn", fake_burn)
    monkeypatch.setattr(motion_engine, "_frame_size", fake_size)
    monkeypatch.setattr(tl.asyncio, "to_thread", lambda f, *a: asyncio.sleep(0, result=10.0))
    out = asyncio.run(tl.finish({"name": "P"}, "brief", {"scenes": []}, "/w/in.mp4", [], [], "9:16", tmp_path,
                                progress, effects_style="ugc", scene_words=[{"overlay_text": "grows"}],
                                spans=[(0, 5)]))
    assert calls["effects"]["text_free"] is True and calls["graphics"]
    assert out["clean"] == "/w/fx.mp4" and out["path"] == "/w/with_text.mp4"
    kinds = [i["kind"] for i in out["text_layer"]["items"]]
    assert kinds.count("hook") == 1 and kinds.count("caption") == 1 and kinds.count("keyword") == 1
    assert calls["burn"] == ("/w/fx.mp4", 3) and out["text_layer"]["height"] == 1920


def test_text_free_effects_never_draw_letters():
    from engines import effects_engine
    assert set(effects_engine.NO_TEXT_FX) <= set(effects_engine.CATALOG)
    assert not set(effects_engine.NO_TEXT_FX) & effects_engine.REQUIRED_TEXT
