# tools/render3d — notices

The 3D Render engine (`scene/engine.js`, `render.mjs`) is VideoStudio's own code.

## Third-party code and ideas

- **three.js** 0.170.0 (MIT) — installed from npm into `node_modules/`, used as-is.
- **playwright-core** 1.48.2 (Apache-2.0) — drives the system Chrome; installed from npm.
- **CamSketch** — https://github.com/phantomfjh/camsketch, MIT License, Copyright (c) 2026 Sorcara.
  The camera-move vocabulary and the shapes of its moves (dolly in/out, reverse dolly, orbit, arc, crane up,
  crane-arc with its cubic ease and late push-in, handheld follow wobble, parallax pan, tilt, vertigo dolly zoom)
  and its deterministic `renderAt`-per-frame approach were adapted in `scene/engine.js` (`cameraAt`), generalised
  to follow a moving subject. Its bundled `Soldier.glb` is **not** used.

## Reviewed and deliberately not copied

- **Animadraft** — https://github.com/Tuhalf/animadraft (the URL Animadraft/animadraft does not exist). GPL-3.0-only.
  Used only as an architectural reference (JSON scene program -> deterministic untextured draft -> video-to-video).
  No code was copied: copying GPL code would put VideoStudio under the GPL.
- **Cadra** — https://github.com/YASSERRMD/Cadra, Apache-2.0. Not published to npm; a pnpm/Turborepo WebGPU monorepo.
  Not vendored. Its documented quality defaults (ACES filmic tone mapping, sRGB output, frame-integer deterministic
  timeline) informed the renderer settings; no code was copied.
