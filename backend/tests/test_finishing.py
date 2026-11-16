"""Finishing pass: which films get graphics, and the timed brief Motion Studio receives."""

from engines import finishing


def test_ads_and_product_films_get_graphics_story_films_only_when_asked():
    assert finishing.wants_graphics("advertisement", "a shoe ad")
    assert finishing.wants_graphics("exploded_view", "")
    assert not finishing.wants_graphics("cinematic", "a short film about a girl")
    assert finishing.wants_graphics("cinematic", "a short film with an end card and on-screen text")


def test_motion_brief_carries_exact_times_targets_and_measurements():
    gp = {"hook": {"text": "Run before the city", "start": 0.2, "end": 2.8, "style": "slams in"},
          "overlays": [{"scene": 6, "start": 28.0, "end": 31.5, "kind": "measure", "text": "Stack height",
                        "value": "38 mm", "target": {"x": 30, "y": 45, "w": 40, "h": 30, "what": "the shoe"},
                        "place": "right of the shoe"}],
          "accents": [{"at": 13.2, "kind": "flash", "why": "sun breaks through"}],
          "end_card": {"start": 29.0, "title": "Strider", "line": "Built for the ones who go", "cta": "Run now"}}
    text = finishing.motion_brief(gp, 33.2, {"title": "Before the City Wakes"})
    assert "HOOK 0.2-2.8 s" in text and "Run before the city" in text
    assert "MEASURE 28.0-31.5 s (scene 6)" in text and "38 mm" in text and "x 30%, y 45%, w 40%, h 30%" in text
    assert "scale bar with tick marks" in text and "ACCENT at 13.2 s: flash" in text
    assert "END CARD from 29.0 s" in text and "Run now" in text


def test_brief_locks_values_and_switches_to_creator_style_for_ugc():
    gp = {"overlays": [{"scene": 1, "start": 1, "end": 3, "kind": "measure", "text": "Heel", "value": "38 mm"}]}
    premium = finishing.motion_brief(gp, 10, {"title": "T", "genre_label": "Advertisement"})
    ugc = finishing.motion_brief(gp, 10, {"title": "T", "genre_label": "UGC ad"})
    assert "EXACTLY as written" in premium and "never change, round or replace a value" in premium
    assert "Premium, clean" in premium and "Creator style (UGC)" in ugc


def test_measure_geometry_goes_into_the_brief_as_exact_pixels():
    gp = {"overlays": [{"scene": 3, "start": 10, "end": 13, "kind": "measure", "shape": "ring", "text": "EAR CUP",
                        "value": "95 mm", "geometry": {"shape": "ring", "cx": 1100, "cy": 560, "r": 180}}]}
    text = finishing.motion_brief(gp, 20, {"title": "T", "genre_label": "Advertisement"})
    assert "circle centred at (1100px, 560px) with radius 180px" in text and "95 mm" in text
    assert finishing.required_texts(gp) == ["95 mm"]


def test_motion_design_overlays_only_where_the_footage_supports_them():
    from engines import finishing
    gp = {"treatment": "product", "scene_treatments": [{"scene": 2, "treatment": "exploded_view"}],
          "overlays": [
              {"scene": 1, "kind": "part_label", "start": 1, "end": 3, "text": "Driver"},   # closed product: no
              {"scene": 2, "kind": "part_label", "start": 5, "end": 7, "text": "Driver"},   # parts apart: yes
              {"scene": 1, "kind": "circuit", "start": 1, "end": 3, "text": "signal"},      # not a device: no
              {"scene": 1, "kind": "sweep", "start": 1, "end": 2, "text": ""},
              {"scene": 1, "kind": "hologram", "start": 1, "end": 2, "text": "x"},          # unknown kind
              {"scene": 1, "kind": "callout", "start": 3, "end": 2, "text": "x"}]}          # reversed times
    kept = finishing.validate_overlays(gp)
    assert [(o["scene"], o["kind"]) for o in kept] == [(2, "part_label"), (1, "sweep")]


def test_motion_brief_carries_the_art_direction_and_craft():
    from engines import finishing
    gp = {"treatment": "electronic_device", "concept": "signal made visible", "design": {"accent": "#48B8FF"},
          "energy_curve": [{"at": 4.0, "beat": "peak", "what": "the chip lights up"}],
          "overlays": [{"scene": 1, "kind": "circuit", "start": 1, "end": 3, "text": "Power", "detail": "power",
                        "target": {"x": 10, "y": 10, "w": 30, "h": 30, "what": "the board"}}]}
    brief = finishing.motion_brief(gp, 10.0, {"title": "Board", "genre_label": "Explainer"})
    assert "ELECTRONIC / ELECTRICAL DEVICE" in brief and "signal made visible" in brief and "#48B8FF" in brief
    assert "MOTION CRAFT" in brief and "4.0 s peak" in brief and "traces" in brief


def test_finish_level_full_for_people_products_and_ads_clean_for_action_and_cinema():
    from engines import genres, motion_design as md
    ugc = "Create a 30-second UGC skincare advertisement featuring a creator explaining a vitamin C serum"
    assert md.finish_level(genres.detect(ugc), ugc) == "full"
    assert md.finish_level("cinematic", "a short film about a girl in the rain") == "clean"
    assert md.finish_level("action", "a car chase through the city at night") == "clean"
    assert md.finish_level("cinematic", "a short film", person_talks=True) == "full"  # someone talks to camera
    assert md.finish_level("cinematic", "a moody film of my new wireless headphones") == "full"  # a product
    assert md.finish_level("documentary", "a film", ["explainer"]) == "full"


def test_clean_finish_keeps_only_typography():
    gp = {"add_graphics": False, "hook": {"text": "Run"},
          "overlays": [{"kind": "callout"}, {"kind": "kinetic_text"}, {"kind": "measure"}],
          "accents": [{"at": 1, "kind": "flash"}]}
    out = finishing.apply_level(gp, "clean")
    assert out["add_graphics"] and [o["kind"] for o in out["overlays"]] == ["kinetic_text"] and out["accents"] == []
    assert finishing.apply_level({"add_graphics": False, "overlays": [{"kind": "callout"}]}, "full")["add_graphics"]


def test_every_film_gets_a_motion_signature_from_the_document_families(tmp_path, monkeypatch):
    import random
    from engines import motion_design as md
    from router_core import config
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    sig = md.pick_signature("product", random.Random(2))
    assert len(sig["type"]) == 2 and len(sig["accents"]) == 2 and sig["tech"][0] in md.TECH_STYLES
    assert "THIS FILM'S SIGNATURE" in md.signature_rules(sig)
    assert any("SIGNATURE STYLE" in line for line in md.brief_header({"signature": sig}))
    assert "tech" not in md.pick_signature("cinematic", random.Random(2))


def test_graphics_keep_out_of_the_captions_band():
    text = finishing.motion_brief({"captions_band": [64, 75], "hook": {"text": "Glow up"}}, 10, {"title": "T"})
    assert "CAPTIONS are already burned into the footage between 64% and 75%" in text
