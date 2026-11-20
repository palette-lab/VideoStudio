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
    assert urls == ["f0", "f1", "f2", "p1"] and "in order: Mira (face)" in seen["parts"][0]["text"]
    # with a full-body view the check also sees the outfit (dress colour, footwear), not only the face
    asyncio.run(se.clip_issues("c.mp4", {"title": "x"}, cast=[{"name": "Mira", "passport": "p1", "front": "f1x"}]))
    urls = [x["image_url"]["url"] for x in seen["parts"] if x["type"] == "image_url"]
    assert urls[-2:] == ["p1", "f1x"] and "Mira (full outfit)" in seen["parts"][0]["text"]
    assert "outfit (garments, colours" in seen["parts"][0]["text"]


# --- Seedance reference mode: the clip model sees the character sheets, not only the scene's still ------------------

MIRA = {"name": "Mira", "passport": "p1", "front": "f1", "back": "b1"}


def test_seedance_gets_the_still_and_the_character_sheets_as_references():
    urls, note = se.clip_references("seedance_2_0", "still", [MIRA, {"name": "", "photo": "u", "passport": "p2"}])
    assert urls == ["still", "p1", "f1", "b1", "p2"]
    assert note.startswith("@Image1 is the opening frame") and "@Image2-@Image4 are Mira" in note
    assert "@Image5 is the person" in note and "never a costume or hair change" in note
    assert se.clip_references("kling_3_0_pro", "still", [MIRA]) == ([], "")  # Kling uses elements instead
    assert se.clip_references("seedance_2_0", "still", []) == ([], "")
    many, _ = se.clip_references("seedance_2_0", "still", [MIRA] * 5)
    assert len(many) <= 9  # the endpoint's limit


def _clip_call(monkeypatch, model, fail_refs=False):
    calls = []

    async def fake_fal(fn, key, m, prompt, **kw):
        calls.append({"prompt": prompt, **kw})
        if fail_refs and kw.get("image_references"):
            raise RuntimeError("500 upstream error")
        return {"video": {"url": "v.mp4"}}

    async def progress(_):
        pass
    monkeypatch.setattr(se, "_fal", fake_fal)
    monkeypatch.setattr(se, "_motion", lambda *a, **k: "she walks")
    s = {"duration": 5}
    q = {"video_model": model, "fallbacks": [], "resolution": "480p", "audio": False}
    out = asyncio.run(se._clip({"name": "t", "app_id": "a"}, 0, 1, s, "still", "9:16", q, "k", progress,
                               end="end", cast=[MIRA]))
    return out, calls, s


def test_a_seedance_clip_with_characters_uses_reference_mode(monkeypatch):
    out, calls, s = _clip_call(monkeypatch, "seedance_2_0")
    assert out == ("v.mp4", "seedance_2_0") and len(calls) == 1 and s["clip_refs"] == 4
    c = calls[0]
    assert c["image_references"] == ["still", "p1", "f1", "b1"] and "start_image" not in c and "end_image" not in c
    assert c["prompt"].startswith("@Image1 is the opening frame") and c["prompt"].endswith("she walks")
    from engines.vendor.flow import fal_helper
    mid, fields = fal_helper.video_request("seedance_2_0", prompt="x", image_references=c["image_references"],
                                           resolution="480p", duration="5")
    assert mid == "bytedance/seedance-2.0/fast/reference-to-video" and fields["image_urls"][0] == "still"


def test_a_failed_reference_clip_falls_back_to_the_plain_image_to_video(monkeypatch):
    out, calls, s = _clip_call(monkeypatch, "seedance_2_0", fail_refs=True)
    assert out == ("v.mp4", "seedance_2_0") and len(calls) == 2 and "clip_refs" not in s
    assert calls[1]["start_image"] == "still" and calls[1]["end_image"] == "end" and calls[1]["prompt"] == "she walks"


def test_kling_4_still_gets_a_plain_clip(monkeypatch):
    out, calls, _ = _clip_call(monkeypatch, "kling_4_0")
    assert len(calls) == 1 and "image_references" not in calls[0] and "elements" not in calls[0]


def test_a_failed_check_is_retried_then_marked_unchecked(monkeypatch):
    n = {"calls": 0}

    async def boom(*a, **k):
        n["calls"] += 1
        raise RuntimeError("gateway 502")
    monkeypatch.setattr(se.llm, "chat_json", boom)
    s = {}
    assert asyncio.run(se.consistency_issues(["p1"], "still", {}, note=s)) == []
    assert n["calls"] == 2 and s["qa_unchecked"] == ["consistency"]


def test_an_animal_lead_gets_its_references_without_being_named():
    cast = [{"name": "Rusty"}]
    assert se.scene_cast({"image_prompt": "a red fox sniffs the snow"}, cast) == cast
    assert se.scene_cast({"image_prompt": "the bottle and its label on a table"}, cast) == []


def test_a_rerender_keeps_the_film_cast(monkeypatch, tmp_path):
    seen = []

    async def fake_fal(fn, key, m, prompt, **kw):
        seen.append((fn.__name__, prompt, kw))
        return {"images": [{"url": "new_still"}]} if fn.__name__ == "generate_image" else {"video": {"url": "v.mp4"}}

    async def no_issues(*a, **k):
        return []

    async def dl(url, dest):
        return str(dest)

    async def progress(_):
        pass
    monkeypatch.setattr(se, "_fal", fake_fal)
    monkeypatch.setattr(se, "consistency_issues", no_issues)
    monkeypatch.setattr(se, "scene_issues", no_issues)
    monkeypatch.setattr(se, "clip_issues", no_issues)
    monkeypatch.setattr(se, "_download", dl)
    monkeypatch.setattr(se, "splice", lambda film, clip, a, b, out: out)
    monkeypatch.setattr(se, "fal_key", lambda: "k")
    out = {"path": "film.mp4", "spans": [[0, 5]], "aspect_ratio": "9:16", "clip_models": ["seedance_2_0"],
           "plan": {"cast": [{"name": "Mira", "desc": "Mira: red braids, yellow raincoat", **{k: MIRA[k] for k in
                                                                                             ("passport", "front", "back")}}]},
           "scenes": [{"title": "Mira jumps in a puddle", "image_prompt": "Mira jumps in a puddle", "still_url": "old"}]}
    asyncio.run(se.rerender_scene(out, 1, "", tmp_path, progress))
    img = next(kw for fn, _, kw in seen if fn == "generate_image")
    vid = next(kw for fn, _, kw in seen if fn == "generate_video")
    assert img["image_urls"][:3] == ["p1", "f1", "b1"]
    assert vid["image_references"] == ["new_still", "p1", "f1", "b1"]


# --- the Meera / Arjun drama: one blended "person" sheet, faces changed between scenes ------------------------------

FLAT = ("Meera: 28-year-old Indian woman, oval face, long black hair in a neat low bun, red silk saree with a gold "
        "border. Arjun: 30-year-old Indian man, light trimmed beard, short curly hair, plain navy cotton kurta. "
        "Meera's father is never shown; he is present only through the letter and the phone message.")


def test_a_bible_written_as_one_string_is_split_per_character():
    cast = se.cast_list({"bible": {"characters": FLAT}})
    assert [c["name"] for c in cast] == ["Meera", "Arjun"]  # not one blended person; the unseen father is not drawn
    assert "saree" in cast[0]["desc"] and "kurta" not in cast[0]["desc"] and "kurta" in cast[1]["desc"]


def test_the_approved_story_characters_win_over_the_planner_text():
    story = {"bible": {"characters": [
        {"name": "Meera", "face": "oval face", "hair": "low bun", "wardrobe": "red silk saree"},
        {"name": "Arjun", "face": "light beard", "hair": "short curls", "wardrobe": "navy kurta"},
        {"name": "Dad", "face": "never shown, only a voice on the phone"}]}}
    cast = se.cast_list({"bible": {"characters": FLAT}}, story)
    assert [c["name"] for c in cast] == ["Meera", "Arjun"] and "red silk saree" in cast[0]["desc"]


def test_a_different_face_outweighs_any_number_of_accessories():
    face = ["Meera's face is a different face from the portrait: narrower eyes, thinner lips"]
    trinkets = ["a thin gold necklace not in her references", "a bracelet on her right wrist", "a bindi"]
    assert se.drift_score(face) > se.drift_score(trinkets)  # (1, 1) beats (0, 3): the face must be fixed first
    assert se.drift_score(["Arjun's kurta is grey instead of navy"])[0] == 1
    assert se.drift_score(["the tote bag is on the other shoulder"])[0] == 0


def test_a_character_scene_starts_on_a_model_that_takes_the_references(monkeypatch):
    calls = []

    async def fake_fal(fn, key, m, prompt, **kw):
        calls.append((m, sorted(k for k in kw if k in ("image_references", "elements", "start_image"))))
        return {"video": {"url": "v.mp4"}}

    async def progress(_):
        pass
    monkeypatch.setattr(se, "_fal", fake_fal)
    monkeypatch.setattr(se, "_motion", lambda *a, **k: "she reads")
    q = {**se.QUALITY["full"]}  # Kling 4.0 first: it takes no character references
    out = asyncio.run(se._clip({"name": "t", "app_id": "a"}, 0, 1, {"duration": 5}, "still", "9:16", q, "k",
                               progress, cast=[MIRA]))
    assert out == ("v.mp4", "seedance_2_0") and calls[0] == ("seedance_2_0", ["image_references"])
    calls.clear()  # a scene without drawn characters keeps the quality's own order
    asyncio.run(se._clip({"name": "t", "app_id": "a"}, 0, 1, {"duration": 5}, "still", "9:16", q, "k", progress))
    assert calls[0][0] == "kling_4_0"
