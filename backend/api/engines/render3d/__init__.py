"""3D Render: story -> animated Three.js scene -> frame-aligned 3D reference + control passes -> photoreal video.

    spec        the scene planner (LLM) and the scene-spec normalizer: actors, props, events, camera shots
    renderer    drives tools/render3d (headless Chrome + three.js): stills, RGB / depth / normal / mask passes, metadata
    providers   the video-to-video transformation: Kling 4.0 first, Seedance 2.0 only when Kling fails
    continuity  checks the 3D reference against the story, then the realistic video against the 3D reference

The orchestration (plan, review gate, realize, compose) lives in engines/render3d_engine.py.
"""
