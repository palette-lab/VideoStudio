# 3D Render runtime

Deterministic Three.js renderer for VideoStudio's **3D Render** agent (`backend/api/engines/render3d_engine.py`).

    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install        # three + playwright-core (uses the system Chrome)
    node render.mjs <film dir with spec.json> --out <dir>  # rgb/depth/normal/mask_characters/mask_objects .mp4 + metadata.json
    node render.mjs <film dir> --out <dir> --stills 1.5,4  # RGB stills only
    node render.mjs <film dir> --out <dir> --meta-only     # per-frame metadata only (fast checks)

`spec.json` (written by `engines/render3d/spec.py`): `fps, duration, width, height, environment, actors, props,
events, camera`. Every frame is a pure function of its frame number; actor bones are baked into AnimationMixer
clips; every pass renders the identical scene state and camera. Env: `RENDER3D_CHROME`, `FFMPEG`.
See NOTICE.md for third-party attributions.
