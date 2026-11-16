"""Genre playbooks, OpenMontage guide loading and weighted scene durations."""

from engines import genres, story_engine


def test_detect_specific_genres_first():
    assert genres.detect("A UGC ad for my serum, creator style") == "ugc_ad"
    assert genres.detect("Unboxing video of our new phone") == "unboxing"
    assert genres.detect("An exploded view of the headphones showing the battery") == "exploded_view"
    assert genres.detect("A 20s ad for sneakers") == "advertisement"
    assert genres.detect("Fashion lookbook for the summer collection") == "fashion"
    assert genres.detect("something nice", ["explainer"]) == "explainer"
    assert genres.detect("a man walks home") == "cinematic"


def test_every_genre_arc_sums_to_100():
    for gid, g in genres.GENRES.items():
        assert sum(w for _, w in g["arc"]) == 100, gid


def test_playbook_states_beats_with_seconds():
    text = genres.playbook("unboxing", 40)
    assert "Unboxing" in text and "The reveal" in text and "about 9 s (22%)" in text


def test_openmontage_guides_load_only_from_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_GUIDES_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(genres.config, "OPENMONTAGE_DIR", tmp_path / "missing2")
    assert genres.openmontage_guides("advertisement", 30) == ""
    creative = tmp_path / "om" / "skills" / "creative"
    creative.mkdir(parents=True)
    (creative / "commercial-product-video.md").write_text("# Commercial craft\nHook in 2 seconds.")
    (creative / "video-gen-prompting.md").write_text("# Prompting\nSubject first.")
    monkeypatch.setenv("OPENMONTAGE_GUIDES_DIR", str(tmp_path / "om"))
    text = genres.openmontage_guides("ecommerce", 30)
    assert "Hook in 2 seconds" in text and "Subject first" in text and "not instructions" in text


def test_weighted_durations_give_the_peak_the_most_time():
    scenes = [{"weight": 1}, {"weight": 2}, {"weight": 5}, {"weight": 2}]
    d = story_engine.weighted_durations(scenes, 40, 3, 15)
    assert sum(d) == 40 and d[2] == max(d) and d[0] == min(d) and all(3 <= x <= 15 for x in d)


def test_weighted_durations_respect_limits_and_fallback():
    d = story_engine.weighted_durations([{"weight": 5}, {"weight": 1}, {"weight": 1}], 30, 3, 15)
    assert sum(d) == 30 and max(d) <= 15
    assert story_engine.weighted_durations([{"duration": 6}, {"duration": 4}], 10, 3, 15) == [6, 4]
