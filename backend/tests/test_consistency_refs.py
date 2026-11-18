"""Characters AND places are drawn before the scenes, and every scene keeps them (the boy-and-the-door film drifted:
plain pyjamas instead of striped ones, a different bedroom in the last scene)."""

import asyncio
import json

from engines import story_engine as se


def test_the_locked_look_is_written_into_every_still():
    eliot = {"name": "Eliot", "desc": "Eliot: messy dark-brown curls, hazel eyes; navy-and-white striped pyjamas",
             "urls": ["p", "f", "b"], "views": ["passport portrait", "full-body front", "full-body back"]}
    note = se._cast_note([eliot])
    assert "reference images 1-3 are Eliot" in note
    assert "LOCKED LOOK" in note and "navy-and-white striped pyjamas" in note and "wins over any other" in note
    photo = {"name": "", "desc": se.PHOTO_PERSON, "urls": ["u"], "views": ["photo"], "photo": "u"}
    lock = se._cast_note([photo])  # a photo may show only the face: the outfit is locked to the drawn views
    assert "LOCKED LOOK" in lock and "front and back references" in lock and se.PHOTO_PERSON not in lock


def test_clip_elements_bind_the_scene_cast_on_kling_only():
    mira = {"name": "Mira", "passport": "p1", "front": "f1", "back": "b1"}
    you = {"name": "", "photo": "u", "passport": "p2", "front": "f2", "back": None}
    els, note = se.clip_elements("kling_3_0_pro", [mira, you])
    assert els == [{"frontal_image": "p1", "reference_images": ["f1", "b1"]},
                   {"frontal_image": "p2", "reference_images": ["f2"]}]
    assert "@Element1 is Mira" in note and "@Element2 is the person" in note
    assert se.clip_elements("seedance_2_0", [mira]) == ([], "")
    assert se.clip_elements("kling_4_0", [mira]) == ([], "")
    assert se.clip_elements("kling_3_0_pro", [{"name": "x", "passport": "p"}]) == ([], "")  # needs a 2nd angle
    assert len(se.clip_elements("kling_3_0_pro", [mira] * 5)[0]) == se.MAX_ELEMENTS


def test_a_clip_endpoint_rejecting_elements_still_gets_the_plain_clip(monkeypatch):
    calls = []

    async def fake_fal(fn, key, model, prompt, **kw):
        calls.append((prompt, kw.get("elements")))
        if kw.get("elements"):
            raise RuntimeError("422 unexpected field elements")
        return {"video": {"url": "v.mp4"}}

    async def progress(_):
        pass
    monkeypatch.setattr(se, "_fal", fake_fal)
    monkeypatch.setattr(se, "_motion", lambda *a, **k: "she walks")
    s = {"duration": 5}
    q = {"video_model": "kling_3_0_pro", "fallbacks": [], "resolution": "720p", "audio": False}
    out = asyncio.run(se._clip({"name": "t", "app_id": "a"}, 0, 1, s, "still", "16:9", q, "k", progress,
                               cast=[{"name": "Mira", "passport": "p", "front": "f", "back": "b"}]))
    assert out == ("v.mp4", "kling_3_0_pro")
    assert calls[0][0].startswith("Characters: @Element1 is Mira") and calls[0][1]
    assert calls[1] == ("she walks", None) and "clip_elements" not in s


def test_places_are_planned_once_per_place(monkeypatch):
    seen = {}

    async def fake(model, messages, **kw):
        seen["user"] = json.loads(messages[1]["content"])
        assert kw["stage"] == "engine.story.locations" and "Same place at another time of day" in messages[0]["content"]
        return {"locations": [{"id": "L1", "name": "attic bedroom", "plate": "slanted blue walls, brass bed"},
                              {"id": "L2", "name": "forest", "plate": "moss, tall trees"}, {"id": "L3"}],
                "scenes": ["L1", "L1", "L2", "L9"]}
    monkeypatch.setattr(se.llm, "chat_json", fake)
    p = {"bible": {"characters": ["x"], "world": {"location": "attic"}},
         "scenes": [{"title": t, "image_prompt": "..."} for t in ("bed", "door", "forest", "morning", "extra")]}
    out = asyncio.run(se.plan_locations(p))
    assert [l["id"] for l in out["locations"]] == ["L1", "L2"]  # a place with no plate is dropped
    assert out["scenes"] == ["L1", "L1", "L2", None, None]  # unknown id -> None, one entry per scene
    assert "characters" not in seen["user"]["bible"] and len(seen["user"]["scenes"]) == 5


def test_the_check_compares_the_place_too(monkeypatch):
    seen = {}

    async def fake(model, messages, **kw):
        parts = messages[0]["content"]
        seen["text"], seen["images"] = parts[0]["text"], [p["image_url"]["url"] for p in parts[1:]]
        return {"same": False, "differences": ["the walls are cream instead of slanted blue"]}
    monkeypatch.setattr(se.llm, "chat_json", fake)
    diffs = asyncio.run(se.consistency_issues(["p", "f"], "still", {"bible": {}}, "plate"))
    assert seen["images"] == ["p", "f", "plate", "still"] and "LOCATION PLATE" in seen["text"]
    assert diffs == ["the walls are cream instead of slanted blue"]
    asyncio.run(se.consistency_issues(["p"], "still", {"bible": {}}))
    assert "LOCATION PLATE" not in seen["text"] and seen["images"] == ["p", "still"]


def test_a_scene_naming_nobody_gets_the_lead_only_unless_it_is_a_group():
    cast = [{"name": "Mira"}, {"name": "Arun"}]
    assert se.scene_cast({"image_prompt": "she opens the door"}, cast) == cast[:1]
    assert se.scene_cast({"image_prompt": "they hug at the station"}, cast) == cast
    assert se.scene_cast({"image_prompt": "Arun runs"}, cast) == cast[1:]
    assert se.scene_cast({"image_prompt": "an empty street at dawn"}, cast) == []


def test_kling_multi_shot_is_off_when_the_clip_has_characters():
    from engines.vendor.flow import fal_helper
    prompt = "[Shot 1, 2s: wide] she walks [Shot 2, 3s: close-up] she smiles"
    _, on = fal_helper.video_request("kling_3_0_pro", prompt=prompt, duration="5", start_image="s")
    _, off = fal_helper.video_request("kling_3_0_pro", prompt=prompt, duration="5", start_image="s", multi_shot=False)
    assert on.get("multi_prompt") and "multi_prompt" not in off


def test_the_clip_check_sees_the_characters(monkeypatch):
    seen = {}

    async def fake(model, messages, **kw):
        seen["parts"] = messages[0]["content"]
        return {"ok": True, "issues": []}
    monkeypatch.setattr(se.llm, "chat_json", fake)
    monkeypatch.setattr(se, "_clip_frames", lambda path: [{"type": "image_url", "image_url": {"url": f"f{k}"}}
                                                          for k in range(3)])
    assert asyncio.run(se.clip_issues("c.mp4", {"title": "x"}, cast=[{"name": "Mira", "passport": "p1"}])) == []
    urls = [x["image_url"]["url"] for x in seen["parts"] if x["type"] == "image_url"]
    assert urls == ["f0", "f1", "f2", "p1"] and "CHARACTER REFERENCES (Mira)" in seen["parts"][0]["text"]
