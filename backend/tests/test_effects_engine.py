"""Effects Studio: plan clean-up, and a real fx.py render with the LLM and review faked."""

import asyncio
import shutil
import subprocess

import pytest

from engines import effects_engine as fx


def test_clean_plan_keeps_only_safe_effects():
    plan = {"effects": [
        {"fx": "backdrop-word", "start": 1, "end": 3, "text": "WHY"},
        {"fx": "glass-widgets", "start": 3.5, "end": 5, "text": "x"},             # not offered (built-in branding)
        {"fx": "lean-in", "start": 5, "end": 6.5},                                 # face effect, but no face
        {"fx": "checklist", "start": 7, "end": 9, "text": "a::1|b::2"},           # no title -> dropped
        {"fx": "spotlight", "start": 2.5, "end": 4.5},                             # no text -> dropped
        {"fx": "reject-stamp", "start": 2, "end": 12, "text": "MYTH"},            # overlaps -> trimmed, capped at 4 s
        {"fx": "moody", "start": 13, "end": 99, "opt": {"grade": "warm"}},        # clamped to the video
    ]}
    out = fx.clean_plan(plan, 15.0, face=False)
    assert [e["fx"] for e in out] == ["backdrop-word", "reject-stamp", "moody"]
    assert out[1]["start"] == 3.0 and out[1]["end"] == 7.0
    assert out[2]["grade"] == "warm" and out[2]["end"] <= 14.95


def test_face_effects_allowed_with_a_face():
    out = fx.clean_plan({"effects": [{"fx": "lean-in", "start": 1, "end": 2.5}]}, 10, face=True)
    assert out and out[0]["fx"] == "lean-in"


@pytest.mark.skipif(fx.available() is not None or not shutil.which("ffmpeg"), reason="effects tool not set up")
def test_real_render_keeps_length(tmp_path, monkeypatch):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=4", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)

    async def fake_plan(model, messages, **kw):
        return {"effects": [{"fx": "backdrop-word", "start": 0.5, "end": 2.2, "text": "LEARN"},
                            {"fx": "moody", "start": 2.4, "end": 3.8, "opt": {"grade": "cool"}}]}

    async def no_speech(video, work, *_):
        return []

    async def looks_fine(shots, plan):
        assert len(shots) == 2
        return []

    async def progress(m):
        pass

    monkeypatch.setattr(fx.llm, "chat_json", fake_plan)
    monkeypatch.setattr(fx, "transcript", no_speech)
    monkeypatch.setattr(fx, "_review", looks_fine)
    monkeypatch.setattr(fx, "pick_set", lambda names: ["backdrop-word", "moody"])  # this film's set
    out = asyncio.run(fx.run({"name": "Effects Studio"}, {"prompt": "explain"}, str(src), tmp_path / "w", progress))
    assert [e["fx"] for e in out["plan"]] == ["backdrop-word", "moody"] and out["face"] is False
    assert 3.8 < out["duration_s"] < 4.2


def test_product_talk_needs_a_face_and_a_product():
    from engines import effects_engine as fx
    assert fx.product_talk(True, "A creator reviews the new Galaxy phone", [])
    assert fx.product_talk(True, "", [{"text": "these earbuds last 30 hours"}])
    assert not fx.product_talk(False, "phone review", [])  # no person on screen
    assert not fx.product_talk(True, "a girl finds a kitten", [])


def test_logo_link_keeps_only_shipped_logos():
    from engines import effects_engine as fx
    plan = {"effects": [{"fx": "logo-link", "start": 1, "end": 3, "text": "Apple|Spotify"},
                        {"fx": "logo-link", "start": 4, "end": 6, "text": "apple|not-a-brand"},
                        {"fx": "pop-word", "start": 7, "end": 9, "text": "NEW", "opt": {"brand": "not-a-brand"}}]}
    out = fx.clean_plan(plan, 20, True)
    assert [e["fx"] for e in out] == ["logo-link", "pop-word"]
    assert out[0]["text"] == "apple|spotify" and "brand" not in out[1]


def test_each_film_gets_its_own_mix_of_effects(tmp_path, monkeypatch):
    import random
    from engines import approaches
    from router_core import config
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    names = list(fx.CATALOG)
    rng, sets = random.Random(3), []
    for _ in range(4):
        todays = fx.pick_set(names, rng)
        assert len(todays) == fx.SET_SIZE and len(set(todays)) == len(todays)
        for kind in ("word", "card", "camera", "look", "cta"):
            assert any(n in fx.KIND[kind] for n in todays)  # every job of an edit is covered
        for n in todays[:4]:
            approaches.remember("effects", n, keep=40)
        sets.append(set(todays))
    assert len(set().union(*sets)) > fx.SET_SIZE + 6  # films draw from far more than one fixed handful


def test_plan_keeps_only_todays_effects_once_each_and_known_looks():
    plan = {"effects": [{"fx": "spotlight", "start": 1, "end": 3, "text": "Glow", "opt": {"look": "sparkle"}},
                        {"fx": "pop-word", "start": 4, "end": 6, "text": "Vit C"},
                        {"fx": "spotlight", "start": 7, "end": 9, "text": "Again"},
                        {"fx": "tape-label", "start": 10, "end": 12, "text": "Serum", "opt": {"look": "x"}}]}
    out = fx.clean_plan(plan, 20, True, ["spotlight", "tape-label"])
    assert [e["fx"] for e in out] == ["spotlight", "tape-label"]
    assert out[0]["look"] == "sparkle" and "look" not in out[1]


def test_captions_vary_by_film_and_calm_films_get_quiet_styles(tmp_path, monkeypatch):
    import random
    from engines import approaches
    from router_core import config
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    rng = random.Random(5)
    assert all(fx.caption_style(True, rng) in ("clean", "grey") for _ in range(20))
    styles = set()
    for _ in range(12):
        st = fx.caption_style(False, rng)
        approaches.remember("captions", st)
        styles.add(st)
    assert styles == set(fx.CAPTION_STYLES)
    assert fx.captions_entry("bold", ["Lakme", "Vit C", ""]) == {"fx": "captions", "style": "bold",
                                                                 "keywords": "Lakme|Vit C"}
