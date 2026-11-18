"""Editable text layer: words live in a layer over the clean video, never in the generated pixels."""

import asyncio
import shutil

import pytest

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
    assert texts[0] == "Money grows" and "on money" not in texts and "8% a year" in texts  # fewer words
    assert "Interest\nearned on interest" in texts and len([t for t in texts if t == ""]) == 0
    assert all(i["kind"] != "arrow" for i in items)
    assert next(i for i in items if i["kind"] == "hook")["style"] == "3d"
    assert all(not i["bg"] for i in items)  # no background panels anywhere: the hook's 3D look
    assert next(i for i in items if i["kind"] == "callout")["style"] == "3d"
    owned = tl.from_graphics(gp, 15.0, words_owned=True)  # the scenes carry their own words: only the end card
    assert [i["kind"] for i in owned] == ["end_title", "cta"]
    low = next(i for i in items if i["text"] == "low")
    assert low["y"] < 74  # moved out of the captions band
    assert [i["text"] for i in items if i["kind"] in ("end_title", "end_line", "cta")] == ["Start early", "Subscribe"]


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
    # the scenes carry their own key words, so the director's hook is not added on top of them
    assert kinds.count("hook") == 0 and kinds.count("caption") == 1 and kinds.count("keyword") == 1
    assert calls["burn"] == ("/w/fx.mp4", 2) and out["text_layer"]["height"] == 1920


def test_text_free_effects_never_draw_letters():
    from engines import effects_engine
    assert set(effects_engine.NO_TEXT_FX) <= set(effects_engine.CATALOG)
    assert not set(effects_engine.NO_TEXT_FX) & effects_engine.REQUIRED_TEXT


def _boxes_clear(lay):
    items = [i for i in lay["items"] if i["kind"] != "caption"]
    for k, a in enumerate(items):
        for b in items[k + 1:]:
            if a["start"] < b["end"] and b["start"] < a["end"]:
                assert not tl._hit(tl.box(a, lay["width"], lay["height"]), tl.box(b, lay["width"], lay["height"]), 0), \
                    (a["text"], b["text"])


def test_engine_layout_has_no_overlap_no_repeats_and_stays_in_the_frame():
    W, H = 1080, 1920
    items = [  # the two screenshots: the same title twice, a line on top of it, a narrow card over a big title
        tl.item("keyword", "A DIRTY WORD", 0, 4, 50, 30, 84, 9, style="3d", n=1),
        tl.item("keyword", "A Dirty Word", 0.5, 4, 62, 26, 70, 7, style="3d", n=2),
        tl.item("keyword", "the second winter", 1, 4, 80, 45, 70, 7, style="3d", n=3),
        tl.item("callout", "THE AUTOMATA\nA 18th-century clockwork that wrote, played and danced", 5, 9, 28, 16, 64,
                4.2, style="3d", n=4),
        tl.item("keyword", "THE MACHINE THAT DANCED", 5, 9, 50, 30, 84, 9, style="3d", n=5),
        tl.item("caption", "and it danced", 5, 6, 50, 79, 86, 4.6, n=6),
    ]
    lay = tl.layer(items, W, H, 10, tidy=True)
    texts = [i["text"] for i in lay["items"]]
    assert texts.count("A DIRTY WORD") == 1 and "A Dirty Word" not in texts  # shown once
    assert "the second winter" not in texts  # one text at a time: no free moment for it, so it is left out
    assert len([i for i in lay["items"] if i["kind"] != "caption" and i["start"] < 9 and i["end"] > 5]) == 1
    _boxes_clear(lay)
    for i in lay["items"]:
        l, t, r, b = tl.box(i, W, H)
        assert l >= tl.MARGIN - 0.01 and r <= 100 - tl.MARGIN + 0.01 and t >= 0 and b <= 100
        if i["kind"] in tl.CENTRED:
            assert i["x"] == 50
        if i["kind"] != "caption":
            assert tl._lines(i, W, H) <= tl.MAX_LINES + ("\n" in i["text"])
    assert "THE MACHINE THAT DANCED" in texts and not any(i["kind"] == "callout" for i in lay["items"])
    assert next(i for i in lay["items"] if i["kind"] == "caption")["y"] == 79  # captions keep their band
    own = tl.layer(items, W, H, 10)  # the user's own edits are never moved
    assert [i["x"] for i in own["items"] if i["id"] == "t3"] == [80]


def test_text_goes_above_or_beside_a_face_never_over_it():
    W, H = 1080, 1920
    face = {"start": 0, "end": 5, "box": (30.0, 20.0, 70.0, 45.0)}  # a presenter's head, upper middle
    hook = tl.item("hook", "Money grows", 0, 3, 50, 30, 86, 9, style="3d", n=1)
    lay = tl.layer([hook], W, H, 5, tidy=True, faces=[face])
    it = lay["items"][0]
    l, t, r, b = tl.box(it, W, H)
    person = tl._face_boxes([face], it)[0]  # the face and the body below it
    assert not tl._hit(person, (l, t, r, b), 0) and b <= person[1]  # above the head, smaller
    assert it["size"] < 9
    big = tl.item("keyword", "A VERY LONG TITLE THAT NEEDS ROOM", 0, 3, 50, 30, 86, 9, style="3d", n=2)
    wide = {"start": 0, "end": 5, "box": (10.0, 8.0, 90.0, 70.0)}  # a close-up: only the sides are free
    lay2 = tl.layer([big], W, H, 5, tidy=True, faces=[wide])
    for i in lay2["items"]:
        assert not tl._hit(tl._face_boxes([wide], i)[0], tl.box(i, W, H), 0)
    side = {"start": 0, "end": 5, "box": (55.0, 30.0, 85.0, 50.0)}  # a person on the right: text on the left
    s3 = tl.layer([big], W, H, 5, tidy=True, faces=[side])["items"]
    assert s3 and not tl._hit(tl._face_boxes([side], s3[0])[0], tl.box(s3[0], W, H), 0)
    # no free place at all -> the text is left out rather than covering the face
    full = {"start": 0, "end": 5, "box": (0.0, 0.0, 100.0, 100.0)}
    assert tl.layer([big], W, H, 5, tidy=True, faces=[full])["items"] == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_faces_are_found_per_scene_by_the_vision_model(tmp_path, monkeypatch):
    import asyncio
    import subprocess
    from router_core import llm
    clip = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=480x854:rate=24:duration=6",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    seen = []

    async def fake(model, messages, **kw):
        seen.append(kw["stage"])
        assert any(p.get("type") == "image_url" for p in messages[0]["content"])
        return {"faces": [{"left": 30, "top": 20, "width": 40, "height": 25}, {"left": "bad"}]}
    monkeypatch.setattr(llm, "chat_json", fake)
    faces = asyncio.run(tl.faces_in(str(clip), 6.0, tmp_path, spans=[(0, 3), (3, 6)]))
    assert seen == ["engine.text.faces"] * 2  # one frame per scene
    assert faces == [{"start": 0, "end": 3, "box": (30.0, 20.0, 70.0, 45.0)},
                     {"start": 3, "end": 6, "box": (30.0, 20.0, 70.0, 45.0)}]
