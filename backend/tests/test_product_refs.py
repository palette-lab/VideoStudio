"""The user's product photo: the planner's "reference N" (upload order) must not point the image model at another
picture, and a scene that shows the product gets the product photo first."""

from engines import story_engine as se

UP = [{"role": "product", "url": "p"}, {"role": "character", "url": "c"}]  # upload order: product, then the person


def test_named_refs_replace_upload_numbers():
    t = se._named_refs("1 Vit C serum dropper bottle exactly as in reference 1, held like reference image 2", UP)
    assert "reference 1" not in t and "reference image 2" not in t
    assert "the uploaded product photo" in t and "the uploaded person photo" in t
    assert se._named_refs("reference 9 stays", UP) == "reference 9 stays"  # not an upload: untouched


def test_shows_product_from_references_or_text():
    assert se._shows_product({"references": [1]}, UP)
    assert not se._shows_product({"references": [2]}, UP)
    assert se._shows_product({"props": ["1 serum bottle exactly as in reference 1"]}, UP)
    assert se._shows_product({"references": [{"index": 0, "asset_id": "a1"}]}, UP)
    assert not se._shows_product({"image_prompt": "a woman walks in"}, UP)
    assert not se._shows_product({"references": [1]}, [{"role": "character"}])


def test_refs_note_names_the_upload():
    n = se._refs_note([UP[0]], 1)
    assert "reference image 1 is the product (the uploaded product photo)" in n
