"""Phase 1: the brief schema coerces untrusted LLM output into the fixed vocabularies."""

from router_core.schemas import Brief


def test_unknown_values_are_coerced_not_trusted():
    b = Brief.model_validate({
        "detected_intent": "Make A Movie",
        "production_type": ["Product Showcase", "hologram", "commercial"],
        "scene": {"shot_structure": "Single Continuous", "scene_count": "three"},
        "motion": {"kinds": ["realistic", "magic"], "programmatic_elements": ["Exploded-View", "laser"], "integration": "?"},
        "audio": {"music": "yes please", "subtitles": "Excluded"},
        "editing": {"operations": ["captions", "teleport"]},
        "constraints": {"duration_s": "15", "aspect_ratio": "9/16", "exclusions": "no logos"},
        "asset_roles": [{"asset_id": "a1", "role": "Garment", "use": "must appear"}, "junk"],
        "missing_info": [{"question": "Which?", "options": "only one"}],
        "confidence": 7,
    })
    assert b.detected_intent == "unclear"
    assert b.production_type == ["product_showcase", "commercial"]
    assert b.scene.shot_structure == "single_continuous" and b.scene.scene_count is None
    assert b.motion.kinds == ["realistic"] and b.motion.programmatic_elements == ["exploded_view"]
    assert b.motion.integration == "unspecified"
    assert b.audio.music == "unspecified" and b.audio.subtitles == "excluded"
    assert b.editing.operations == ["captions"]
    assert b.constraints.duration_s == 15 and b.constraints.aspect_ratio == "9:16"
    assert b.constraints.exclusions == ["no logos"]
    assert b.asset_roles[0].role == "garment" and b.asset_roles[0].use == "must_appear" and len(b.asset_roles) == 1
    assert b.missing_info[0].options == ["only one"]
    assert b.confidence == 1.0


def test_empty_brief_is_valid_and_safe():
    b = Brief.model_validate({})
    assert b.detected_intent == "unclear" and b.scene.shot_structure == "unspecified"
    assert b.role_of("a9").role == "unclear"


async def test_build_brief_adds_missing_asset_roles():
    from router_core import understanding

    async def chat(model, messages, **kw):
        return {"detected_intent": "create_new", "asset_roles": [{"asset_id": "ghost", "role": "product"}]}

    b = await understanding.build_brief("x", {"a1": {"modality": "image", "filename": "p.jpg"}}, {}, {}, chat)
    assert [(r.asset_id, r.role) for r in b.asset_roles] == [("a1", "unclear")]


def test_list_where_text_expected_is_joined_not_rejected():
    b = Brief.model_validate({"visual": {"lighting": ["dusk ambient", "string lights"], "style": None},
                              "user_goal": ["a", "b"], "missing_info": [{"question": ""}, {"question": "Which year?", "affects": "content"}]})
    assert b.visual.lighting == "dusk ambient, string lights" and b.visual.style == "" and b.user_goal == "a, b"
    assert len(b.missing_info) == 1 and b.missing_info[0].affects == "content"


def test_implementation_candidates_use_known_capabilities():
    from router_core import registry
    vocab = registry.capabilities()
    for app in registry.apps().values():
        for c in app.get("implementation_candidates", []):
            assert {"repo", "license", "entry", "covers", "limits"} <= set(c), app["app_id"]
            assert set(c["covers"]) <= set(vocab), (app["app_id"], c["covers"])
