"""Story methods: every film is not built the same way."""

import random

from engines import approaches, planners
from router_core import config

Q = {"video_model": "kling_3_0_pro", "image_model": "nano_banana_pro", "audio": True}


def test_a_brief_that_asks_for_a_method_gets_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    assert approaches.pick("ugc_ad", "show her applying the serum")["id"] == "routine"
    assert approaches.pick("advertisement", "a before and after of my skin")["id"] == "before_after"
    assert approaches.pick("exploded_view", "an exploded view") is None  # its own structure


def test_films_in_a_row_use_different_methods(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    rng, seen = random.Random(1), []
    for _ in range(4):
        m = approaches.pick("ugc_ad", "a 30 s UGC ad for my serum, use natural dialogue", rng)
        assert not m["asked"] and m["id"] != "routine" and m["id"] not in seen[-approaches.RECENT_KEEP:]
        approaches.remember("product", m["id"])
        seen.append(m["id"])


def test_the_method_replaces_the_generic_arc_and_gates_the_routine_rules():
    pov = next(o for o in approaches.APPROACHES["product"] if o["id"] == "pov") | {"family": "product"}
    system, _ = planners.prompt_for("pltt-storyboard-video-maker", "a UGC ad, use natural dialogue", 30, "9:16",
                                    3, 10, 6, [], Q, "ugc_ad", pov)
    assert "STORY METHOD - POV / first person" in system and "PRODUCT IN USE" not in system
    assert "Discovery: they pull out the product" not in system  # the genre's default arc is not used


def test_product_without_a_person_becomes_a_music_led_product_film(tmp_path, monkeypatch):
    from engines import genres
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    product = [{"role": "product"}]
    assert genres.detect("a UGC video of my serum without a person") == "product_film"
    assert genres.faceless("advertisement", "a 15 s premium ad for this sunscreen", product)
    assert not genres.faceless("advertisement", "an ad where a woman applies this sunscreen", product)
    assert not genres.faceless("advertisement", "a 15 s ad for this sunscreen", product + [{"role": "character"}])
    assert not genres.faceless("ugc_ad", "a UGC ad for my serum", product)  # UGC with a creator stays as it was
    assert approaches.pick("product_film", "a CGI film of my serum")["id"] == "cgi_hero"
    assert approaches.pick("product_film", "show its natural ingredients")["id"] == "ingredient_world"
    assert approaches.pick("product_film", "a calm product reel")["family"] == "faceless"
    system, _ = planners.prompt_for("pltt-storyboard-video-maker", "a calm product reel", 15, "9:16", 3, 10, 5, [],
                                    Q, "product_film", approaches.pick("product_film", "a calm product reel"))
    assert "PRODUCT FILM WITHOUT A PERSON" in system and "Product film (no person)" in system
    assert "PRODUCT IN USE" not in system
