"""Programmatic Film: a whole narrated film drawn in code, in one of a set of hand-made looks, rendered frame by frame.

The renderer is Lemo-Opuscar's (github.com/lemomo-ai/lemo-opuscar, Lemomo / LemoLab), vendored in tools/opuscar: only
its MIT code (core/render: headless Chrome frame renderer, reading-time check, cue export, two-pass loudness mux;
core/lib.js; core/audio/sfx.py) and its OFL fonts. Its style guides (STYLE.md and every other doc) are CC BY-NC 4.0
and are NOT used: the style briefs below are written for this app from scratch.

Pipeline (one LLM-written page, checked before the long render):
    director   the script: beats with a narration line, what is drawn, the few words on screen, sound cues
    voice      every line through Kokoro (local, free) -> the real timeline (each beat as long as its line)
    page       the film as ONE index.html: window.READY, window.render(t) (deterministic), window.DUR, window.EV,
               window.TEXTS(t) - drawn on a 2D canvas with tools/opuscar/kit/vs.js and /core/lib.js
    check      stills at every beat (page errors), the reading-time check, and a vision review of the stills; every
               problem goes back to the page writer (up to FIX_ROUNDS)
    render     every frame -> MP4; sound cues + narration + music (MusicGen, local) mixed; mux to -14 LUFS with grain
"""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from router_core import config, llm

log = logging.getLogger("palette_video.opuscar")

APP_ID = "opuscar-programmatic-film"
ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path(os.getenv("OPUSCAR_DIR", ROOT / "tools" / "opuscar"))
CHROME = os.getenv("OPUSCAR_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FPS = 30
FIX_ROUNDS = 2
RENDER_TIMEOUT_S = 2400
WORDS_PER_S = 2.5
LEAD_S, GAP_S, TAIL_S = 0.6, 0.45, 1.4
SFX = ("click", "clack", "pop", "whoosh", "thump", "step", "ding", "creak", "rumble", "crash", "heartbeat", "beep")


class ProgrammaticError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ the looks
# Written for VideoStudio. Each: name, what it suits, the look (surface, palette, line, type), how things move and
# cut, its sound vocabulary, the film grain mux adds (0 for crisp digital looks), and words that ask for it.
STYLES = {
    "chalkboard": {
        "name": "Chalkboard", "suits": "lessons, word origins, myth-busting, maths and science basics",
        "look": "a slate-green board (#1f3a30 to #263f35) with faint smudges of old erased chalk; everything is drawn "
                "in chalk: off-white #efeee6 strokes with a dry, broken texture (two passes of rough() with jitter 2-3, "
                "the second fainter), accents in soft chalk yellow #f3d36b, pink #f2a7a7 and blue #9fd1f0. Hand "
                "lettering in 'Chalkduster', fallback 'Marker Felt'; small labels in the same chalk hand. A wooden "
                "frame edge may show at the border.",
        "motion": "everything is written or drawn on in the order a teacher would (draw-on strokes, text revealed left "
                  "to right at about 12 letters a second); circles and underlines land on the spoken key word; a beat "
                  "ends by wiping an area with a soft eraser smear or by moving along the board (a slow pan to clean "
                  "board). No glows, no gradients.",
        "sfx": "click for each chalk tap, whoosh for an eraser wipe, thump for a strong point", "grain": 2,
        "asks": ["chalkboard", "chalk board", "blackboard", "chalk"]},
    "whiteboard": {
        "name": "Whiteboard", "suits": "explainers, courses, science, business ideas",
        "look": "a clean white board #f7f7f4 with a faint cool sheen and a few ghost marks; dry-erase marker lines, "
                "black #1b1b1b 5-7 px, with red #d64541, blue #2d6cdf and green #2f9e5b for meaning only; simple "
                "doodles (stick people, icons, boxes, arrows) built from rough() with low jitter (1); lettering in "
                "'Marker Felt' or 'Fredoka' 600.",
        "motion": "the classic whiteboard animation: each drawing is drawn on line by line in the order you would "
                  "draw it, in sync with the narration; the camera pans or zooms out across one big board between "
                  "beats so earlier drawings stay visible; arrows connect ideas.",
        "sfx": "click for marker taps, pop for an icon appearing, whoosh for a pan", "grain": 0,
        "asks": ["whiteboard", "white board", "doodle video", "draw my life"]},
    "blueprint": {
        "name": "Blueprint", "suits": "engineering, how machines work, hardware launches, architecture",
        "look": "cyanotype paper #0f3d6e to #164a80 with a fine grid (#ffffff at 8% every 40 px, 18% every 200 px); "
                "white technical line work 2-3 px with thin dimension lines, arrowheads, centre lines (dash-dot) and "
                "callout leaders; labels in 'Courier New' bold capitals with small reference numbers; a title block "
                "in a corner (drawing name, scale, sheet number).",
        "motion": "parts are drafted line by line as on a drawing board, then dimensioned; cutaways and exploded "
                  "views separate along clean axes; a slow, steady camera across a large sheet; measurements tick up "
                  "as numbers are spoken.",
        "sfx": "click for each line committed, clack for parts locking, thump for a reveal", "grain": 2,
        "asks": ["blueprint", "technical drawing", "engineering drawing", "schematic"]},
    "ink-wash": {
        "name": "Ink Wash", "suits": "poetry, legends, wuxia, cultural brands, calm reflective films",
        "look": "warm rice paper #f2ead8 with soft fibres; black ink in every grey from #111 to a pale wash #c9c2b2; "
                "brush strokes that swell and taper (draw them as many thin rough() passes with varying width), soft "
                "bleeding edges, mountains in layered washes, mist as blank paper, one red seal stamp #b8322a; text "
                "in 'IM Fell English' or a vertical column of brush characters.",
        "motion": "slow and breathing: washes spread from a point, strokes flow in one gesture, the camera drifts "
                  "through layers of mountains and mist; cuts are dissolves through blank paper.",
        "sfx": "whoosh (soft) for a brush sweep, ding (gentle) for the seal", "grain": 2,
        "asks": ["ink wash", "ink-wash", "sumi-e", "chinese painting", "brush painting", "wuxia"]},
    "paper-popup": {
        "name": "Paper Pop-up", "suits": "children's stories, fables, game trailers, warm brand stories",
        "look": "a pop-up book: layered cut-paper shapes in a warm palette (cream #f4ead2, tomato #e2553f, mustard "
                "#e9b23a, teal #2a8c82, navy #23395b), each layer with a soft drop shadow below it and a slightly "
                "fibrous edge; characters are flat paper puppets with split pins; titles in 'Lilita One'.",
        "motion": "pages turn and scenery folds up from the gutter (scale a layer's height from 0 to 1 with back() "
                  "easing, shadow growing with it); puppets move on hinges with small bounces; tabs slide things in.",
        "sfx": "whoosh for a page turn, pop for a fold-up, click for a tab", "grain": 2,
        "asks": ["pop-up book", "popup book", "paper pop-up", "paper popup", "papercraft", "cut paper", "paper cut-out"]},
    "pixel-rpg": {
        "name": "Pixel RPG", "suits": "game trailers, recaps, nostalgia, playful explainers",
        "look": "a 16-bit role-playing game: the scene drawn on a small virtual screen (e.g. 320x180 or 180x320 "
                "logical pixels) scaled up with image smoothing OFF so every pixel is a crisp square; a limited "
                "palette of 16 colours; tile-based grass, stone and water; sprites from sprite(); dialogue in a "
                "bordered text box at the bottom in 'Courier New' bold, text typing on; HP bars and menus.",
        "motion": "sprites move tile by tile with 2-frame walk cycles; the camera snaps or scrolls in whole pixels; "
                  "screen transitions are a pixel wipe or a battle swirl; numbers pop up and float.",
        "sfx": "beep for text and menu ticks, pop for items, crash for a hit, ding for a level up", "grain": 0,
        "asks": ["pixel art", "pixel-art", "8-bit", "8 bit", "16-bit", "16 bit", "retro game", "rpg style", "pixel rpg"]},
    "newsreel": {
        "name": "Newsreel", "suits": "history stories, eras and anniversaries, 'the story of' films",
        "look": "a 1940s cinema newsreel: black and white with a warm silver tone, heavy grain, gentle flicker of "
                "brightness, dust and scratches, a soft vignette and a gate weave of 1-2 px; bold title cards in "
                "'Futura' heavy capitals or 'Didot', with rules and a banner; archival-style illustrations, maps and "
                "documents drawn in greys.",
        "motion": "punchy title cards slam in, the 'camera' pans across stills and documents, spinning newspaper "
                  "headlines, map arrows; cuts on the beat; every shot has a slight weave.",
        "sfx": "thump for title slams, whoosh for the spinning paper, click for the projector", "grain": 4,
        "asks": ["newsreel", "news reel", "1940s", "old film", "vintage film", "archival"]},
    "map-explainer": {
        "name": "Map Explainer", "suits": "geopolitics, trade routes, rivers and borders, travel, history of places",
        "look": "a clean modern map: ocean #0e2433 to #143247, land #e9e4d8 with soft relief shading, borders as thin "
                "#9a9384 lines, the place that matters highlighted in one accent (#e8743b), routes as animated dashed "
                "lines, cities as dots with labels in 'Helvetica Neue' (fallback 'Arial') medium; a small scale bar "
                "and north arrow. Draw coastlines as simple stylised polygons, never a claim of exact borders.",
        "motion": "the camera flies and zooms between places (interpolate the map's transform with eio()); routes "
                  "draw on from origin to destination; regions fill in; numbers count up next to their place.",
        "sfx": "whoosh for camera moves, pop for a city label, thump for a highlight", "grain": 0,
        "asks": ["map explainer", "animated map", "map animation", "map video", "geopolitics", "trade route"]},
    "dataviz": {
        "name": "Data Story", "suits": "data journalism, reports, climate and science numbers, business results",
        "look": "an editorial chart page: off-white #f6f4ef, ink #1d1d1f, one strong accent (#d7263d) and two quiet "
                "greys; charts built from scratch on the canvas (bars, lines, dots, unit charts) with thin axes, few "
                "gridlines and direct labels instead of legends; headline in 'Didot' or 'Georgia' bold, numbers and "
                "labels in 'Helvetica Neue'. Only numbers the user or the brief gave; never invent data.",
        "motion": "every chart builds as the narrator speaks: axes draw, bars grow with eo(), lines draw on, the key "
                  "value counts up and gets its accent and a short annotation; one idea per chart; charts morph into "
                  "the next where they can.",
        "sfx": "click per bar or point, ding for the key number", "grain": 0,
        "asks": ["data story", "data visualization", "data visualisation", "dataviz", "chart video", "infographic video"]},
    "iso-infographic": {
        "name": "Isometric Infographic", "suits": "processes, supply chains, how a system works, annual reports",
        "look": "an isometric world (30 degree axes) of clean blocks on a soft background #eef2f6: buildings, "
                "machines, trucks and people as simple prisms with three flat tones per colour (top light, left mid, "
                "right dark) from a palette of navy #27375c, teal #2bb3a3, coral #f26b5b and sand #f2c57c; thin "
                "white connectors and flow arrows; labels in 'Fredoka' 600 on small rounded tags.",
        "motion": "blocks rise from the ground plane with back() easing, items travel along conveyor paths, counters "
                  "tick; the camera pans across one connected isometric landscape step by step.",
        "sfx": "pop for blocks rising, click for counters, whoosh for pans", "grain": 0,
        "asks": ["isometric", "iso infographic", "isometric infographic"]},
    "risograph": {
        "name": "Risograph", "suits": "indie brands, zines, lifestyle, music, culture",
        "look": "a two- or three-colour riso print on cream paper #f3ecdc: fluorescent pink #ff48b0, blue #0078bf "
                "and yellow #ffe800 inks, overprinting where they cross (globalCompositeOperation 'multiply'), a "
                "slight misregistration of 2-4 px between colour layers, halftone dots for shading, big chunky "
                "type in 'Lilita One' or 'Futura' bold.",
        "motion": "bold cut-and-paste animation: shapes slide and stamp in, layers shift in and out of register on "
                  "accents, halftone dots grow; 12 frames a second steps (hold each drawing two frames) for a printed "
                  "feel.",
        "sfx": "thump for a stamp, click for steps, pop for shapes", "grain": 3,
        "asks": ["risograph", "riso", "zine style", "zine"]},
    "swiss-motion": {
        "name": "Swiss Motion", "suits": "brand identity, events, design talks, product principles, manifestos",
        "look": "International Typographic Style: a strict grid, white #ffffff or black #111111 ground, one signal "
                "colour (#e30613), big flush-left type in 'Helvetica Neue' bold (fallback 'Arial') with tight "
                "leading, simple geometric shapes (circles, bars, squares), asymmetric balance, lots of space.",
        "motion": "precise and rhythmic: type and shapes slide on the grid with eio() or snap with a short spring, "
                  "masks wipe text in, shapes scale from their edge; every move lands on a beat of the narration; "
                  "cuts are hard.",
        "sfx": "click on every snap, whoosh for wipes", "grain": 0,
        "asks": ["swiss style", "swiss design", "swiss motion", "international typographic", "kinetic typography",
                 "typographic"]},
}


# The realistic version of each look: the image model paints the real surface and the real medium (a photographed
# chalkboard, real marker, real ink on rice paper...), the page animates those images. A look without an entry here
# (Data Story: images would invent data; Swiss Motion: the typography IS the look) stays drawn in code.
# reveal "draw": each drawing appears on the same empty surface as if drawn, and is wiped before the next;
# reveal "fade": each beat is its own picture, dissolving into the next.
ILLUSTRATED = {
    "chalkboard": {
        "surface": "a close-up of a real dark-green slate chalkboard, photographed straight on so the board surface fills "
                   "the whole frame edge to edge: faint swirls of old erased chalk, a little wear, soft daylight from a window on the "
                   "left, true colour, photorealistic, sharp detail",
        "medium": "drawn on it by hand in real white and pastel chalk (soft yellow, pink, light blue): dusty, grainy, "
                  "slightly uneven strokes with real chalk texture and small smudges, like a skilled teacher's board "
                  "drawing",
        "reveal": "draw", "font": "Chalkduster", "weight": 400, "color": "#f1eee4", "texture": True, "shadow": None},
    "whiteboard": {
        "surface": "a real white dry-erase whiteboard photographed straight on, filling the frame, faint ghost marks of "
                   "old erased writing, soft office light with a gentle sheen, photorealistic",
        "medium": "drawn on it in real dry-erase markers (black, with red, blue and green accents): confident marker "
                  "lines with slight streaks, clear simple sketch-note drawings",
        "reveal": "draw", "font": "Marker Felt", "weight": 400, "color": "#1b1b1b", "texture": False, "shadow": None},
    "blueprint": {
        "surface": "a real sheet of cyanotype blueprint paper, deep Prussian blue with a fine printed grid and slightly "
                   "worn edges, photographed flat under even light, filling the frame, photorealistic",
        "medium": "a precise white technical drawing on it: crisp line work, dimension lines, centre lines, cutaways "
                  "and exploded views, like a real engineering blueprint",
        "reveal": "draw", "font": "Courier New", "weight": 700, "color": "#f4f8ff", "texture": False, "shadow": None},
    "ink-wash": {
        "surface": "a real sheet of warm rice paper with visible fibres, photographed flat in soft light, filling the "
                   "frame, photorealistic",
        "medium": "a traditional ink-wash painting on it in real black sumi ink: expressive brush strokes, soft wet "
                  "washes in many greys, bleeding edges, plenty of empty paper",
        "reveal": "draw", "font": "IM Fell English", "weight": 400, "color": "#1a1a1a", "texture": False, "shadow": None},
    "paper-popup": {
        "surface": "a real handmade paper pop-up book lying open on a wooden table, warm studio light, shallow depth of "
                   "field, the open pages filling the frame",
        "medium": "a real cut-paper pop-up scene standing up from the open pages: layered coloured card (cream, tomato "
                  "red, mustard, teal, navy) with real paper edges, folds and soft shadows, a craft photograph",
        "reveal": "fade", "font": "Lilita One", "weight": 400, "color": "#ffffff", "texture": False,
        "shadow": "rgba(0,0,0,.65)"},
    "pixel-rpg": {
        "surface": "a 16-bit pixel-art game screen of an empty grassy clearing, crisp square pixels, a limited palette",
        "medium": "the same 16-bit pixel-art game style: crisp square pixels, limited palette, tiled ground, small "
                  "detailed sprites",
        "reveal": "fade", "font": "Courier New", "weight": 700, "color": "#ffffff", "texture": False,
        "shadow": "rgba(0,0,0,.9)"},
    "newsreel": {
        "surface": "an authentic 1940s black-and-white documentary photograph, silver-toned film grain, soft focus, "
                   "faint scratches",
        "medium": "a 1940s black-and-white documentary photograph, silver tones, film grain, period detail; no "
                  "recognisable real person",
        "reveal": "fade", "font": "Futura", "weight": 700, "color": "#ffffff", "texture": False,
        "shadow": "rgba(0,0,0,.8)"},
    "map-explainer": {
        "surface": "a realistic shaded-relief map, natural terrain colours and a soft blue ocean, like a high-quality "
                   "printed atlas page photographed flat, filling the frame",
        "medium": "the same realistic shaded-relief map style, with the routes or regions that matter shown as clean "
                  "glowing lines and soft highlighted areas",
        "reveal": "fade", "font": "Helvetica Neue", "weight": 700, "color": "#ffffff", "texture": False,
        "shadow": "rgba(0,0,0,.75)"},
    "iso-infographic": {
        "surface": "a photoreal isometric miniature diorama base on a soft light-grey background, studio light, "
                   "tilt-shift look",
        "medium": "the same photoreal isometric miniature diorama style: small realistic models (buildings, machines, "
                  "vehicles, tiny people) in clay and painted wood, soft studio light",
        "reveal": "fade", "font": "Fredoka", "weight": 700, "color": "#27375c", "texture": False,
        "shadow": "rgba(255,255,255,.9)"},
    "risograph": {
        "surface": "a real risograph print scanned flat: cream paper with visible grain",
        "medium": "a risograph illustration in fluorescent pink and blue with yellow accents: halftone dots, overprint "
                  "and slight misregistration, a scanned real print",
        "reveal": "fade", "font": "Lilita One", "weight": 400, "color": "#ff48b0", "texture": True, "shadow": None},
}
NO_TEXT = ("No letters, words, numbers, labels, captions, signatures or writing of any kind anywhere in the image. "
           "The surface itself fills the ENTIRE frame edge to edge (no wall, ceiling, frame, tray or table edge "
           "beyond it); keep its top fifth plain - the same surface, just nothing drawn or placed there.")


def style_list() -> str:
    return "\n".join(f"- {k}: {v['name']} - {v['suits']}" for k, v in STYLES.items())


def named_style(prompt: str) -> str | None:
    p = (prompt or "").lower()
    for k, v in STYLES.items():
        if any(re.search(rf"\b{re.escape(a)}\b", p) for a in v["asks"]):
            return k
    return None


ASKS = re.compile(r"\bprogrammatic(ally)?\b|\bcode[- ](drawn|animated|made)\b|\bdrawn in code\b|\bopuscar\b", re.I)


def wanted(run: dict) -> bool:
    """The Programmatic Film mode, or in Auto a request for a programmatic film or one of these looks by name - with
    no footage or document of the user's (those are edited or explained by the other apps)."""
    mode = run.get("mode")
    if mode == "programmatic":
        return True
    if mode != "auto" or run.get("parent_run"):
        return False
    assets = (run.get("assets") or {}).values()
    if any(a.get("modality") in ("video", "audio", "document") for a in assets):
        return False
    p = run.get("prompt") or ""
    return bool(ASKS.search(p) or named_style(p))


def available() -> str | None:
    """None when the Programmatic Film can be made here; otherwise what is missing."""
    if not (RUNTIME / "core" / "render" / "video.mjs").exists():
        return f"the Lemo-Opuscar renderer is not installed ({RUNTIME / 'core' / 'render'})"
    if not (RUNTIME / "node_modules" / "playwright-core").exists():
        return f"playwright-core is not installed (cd {RUNTIME} && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install)"
    if not shutil.which("node"):
        return "Node.js is not installed"
    if not shutil.which("ffmpeg"):
        return "ffmpeg is not installed"
    if not os.path.exists(CHROME):
        return f"Chrome is not at {CHROME} (set OPUSCAR_CHROME)"
    return None


# ------------------------------------------------------------------------------------------------ prompts
DIRECTOR = """You are the director and writer of a narrated film that will be DRAWN IN CODE on a 2D canvas (no
footage, no photos, no AI video): every picture is shapes, lines, text and simple characters drawn by a program.

Choose the look that fits the topic (or the one the user named) from:
{STYLES}

Write the film as beats. A beat = one narration line and the one picture idea drawn while it is spoken.
- {BEATS} beats for about {SECONDS} seconds (a line of 8-20 words is about 3-8 s). Hook in the first line.
- "line": natural spoken narration, 6-22 words, numbers written as words, one idea per line.
- "picture": exactly what is drawn and how it moves in this look - concrete shapes, positions, what appears in what
  order, and which spoken word it lands on. Pictures a program can draw well: diagrams, maps, charts, icons, simple
  characters, objects, typography, transitions. Never photos or realistic faces.
- "image": the finished picture of this beat for an image model, in the look's real medium: the subject, how it is
  drawn or built, the composition and where things sit in the frame (keep the top fifth empty). Concrete, visual, NO
  words, letters or numbers in it - the on-screen words are added separately.
- "words": at most 1 short on-screen text (1-5 words: a key term, a number, a name) or none - the narration carries
  the rest; the subtitles are separate.
- "sfx": 0-2 cues from {SFX} at moments in the picture.
- Facts: only what the request says or what is common knowledge; never invent statistics. Keep the user's numbers.

Return ONLY JSON:
{"title": "...", "style": "<one key from the list>", "why_style": "one short reason",
 "palette": ["#hex", ...] (the look's palette, adapted to the topic if useful),
 "beats": [{"id": "b1", "line": "...", "picture": "...", "image": "...", "words": "" , "sfx": ["pop"]}],
 "music": "mood, instruments and tempo for an instrumental bed, or '' for none",
 "language": "en | es | fr | hi | ja | zh | pt | it"}"""

PAGE = """You write ONE self-contained HTML file that draws a narrated film frame by frame on a 2D canvas. A headless
browser renders it: it calls window.render(t) for every frame (t in seconds) and screenshots the page.

THE CONTRACT (must hold exactly):
- <canvas> filling the page: html,body{margin:0;overflow:hidden;background:<film background>}; canvas size W x H =
  {W} x {H} (use innerWidth/innerHeight; they equal these).
- <script type="module">. You may import ONLY from '/core/lib.js' and '/kit/vs.js' (APIs below). No other URLs, no
  network (no fetch/XMLHttpRequest/WebSocket), no eval, no timers driving the picture, no Date or performance.now in
  drawing. NEVER Math.random: use mulberry(seed) from the kit so every frame is the same every time.
- window.render(t): draw the WHOLE frame at second t from scratch (clear first). Deterministic: render(5) then
  render(2) must draw the same frame 2 as render(2) alone. Keep it fast (a few ms): cache static backgrounds with
  cache().
- window.DUR = {DUR}; window.TIMELINE = the TIMELINE below (copy it verbatim as a JS constant).
- window.TEXTS(t) -> [{id, text, x0, y0, x1, y1}]: every piece of readable text on screen at t with its box in
  pixels (textOn() returns the box). Report the FULL text from its first frame even while it is being written on.
  Each text must stay on screen, whole and unchanged, at least (letters / 15 + 1.5) seconds; keep it inside the frame
  with a 5% margin. Small decorative labels the viewer need not read: add pass: true.
- window.EV = [{t, type}] sound cues (type from {SFX}), at the moments things land; usually the beat's sfx.
- Load fonts first: await document.fonts.load('600 64px "Fredoka"') (and every face you use), then
  window.READY = true. Available faces: Fredoka (500/600/700), 'Lilita One', 'IM Fell English' (from
  <link rel="stylesheet" href="/core/fonts/fonts.css">), and the system faces Chalkduster, 'Marker Felt', Futura,
  'Helvetica Neue', Arial, Georgia, Didot, 'Courier New', Menlo, 'American Typewriter'.

THE FILM
- Follow the TIMELINE: beat i is on screen from t0 to t1; its narration is spoken from line_t0 to line_t1, so the
  picture must build WHILE the line is spoken and land its key moment on the spoken word. Nothing static: something
  moves in every second (draw-on, camera drift, counters, characters) but calmly.
- The look ({STYLE_NAME}) - follow it exactly:
  {LOOK}
  Motion and cuts: {MOTION}
  Palette: {PALETTE}
- Readable at phone size: on-screen words big (at least {MIN_PX} px), high contrast, never over other text or
  important drawings; at most one text block at a time besides small labels. NO subtitles (they are added later).
- Leave the bottom 18% free of important text (subtitles go there).
- Compose for {W}x{H} ({ASPECT}): use the whole frame, keep a 5% safe margin.

KIT (/kit/vs.js): beatAt(TIMELINE, t) -> {beat, i, p} | null; rough(g, pts, {width, color, jitter, seed, passes,
alpha, progress}) hand-drawn stroke drawn on to `progress`; circlePts(cx, cy, rx, ry?), boxPts(x, y, w, h);
arrow(g, x0, y0, x1, y1, {...rough opts, progress, head}); fitFont(g, text, maxW, family, weight, max, min) -> px;
wrap(g, text, maxW) -> lines (uses g.font); textOn(g, text, x, y, {font, color, align, progress}) -> box;
surface(g, W, H, color, {speckle, vignette, seed}); cache(key, W, H, draw) -> an offscreen CANVAS, drawn once:
draw(g2, W, H) receives that canvas's 2D CONTEXT (not the canvas) - e.g. const bg = cache('bg', W, H, (g2) =>
surface(g2, W, H, '#21402f')); then g.drawImage(bg, 0, 0) every frame; sprite(g, rows, x, y, px, pal);
keys(t, [[t, v], ...]) eased keyframes; and clamp, seg(t, a, b), ss, eo, mulberry, hash.
LIB (/core/lib.js): clamp, lerp, seg, ss, eio, eo, ei, back(t, s), spring(t, k, z), mulberry, hash, vnoise, TAU,
monotone(keys), track(keys), env(t, a, b, fadeIn, fadeOut).

Reply with the complete file only, in ONE ```html block."""

FIX = """The film page has these problems (from rendering stills, the reading-time check and a look at the frames).
Fix every one and keep everything that works. Reply with the complete corrected file in ONE ```html block.
{ISSUES}"""

REVIEW = """These are stills from a film drawn in code in the '{STYLE}' look, one per beat, in order. What each beat
should show:
{BEATS}
List only REAL visible defects a viewer would notice: an empty or nearly empty frame, a picture that does not show
its beat, text cut off at the edge or overlapping other text or drawings, text too small to read on a phone,
illegible contrast, a broken or garbled drawing, a look that clearly is not '{STYLE}'.
Return ONLY JSON {"ok": true|false, "problems": ["beat N: what is wrong and how to fix it", ...]}."""

UNSAFE = re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|eval|importScripts|setInterval|"
                    r"setTimeout|requestAnimationFrame)\s*\(|new\s+Function\b|Math\.random\s*\(|\bDate\.now\s*\(|"
                    r"performance\.now\s*\(", re.I)
IMPORT = re.compile(r"""(?:\bimport\b[^'"]*|import\s*\()\s*['"]([^'"]+)['"]""")
URL = re.compile(r"""(?:src|href)\s*=\s*['"]([^'"]+)['"]""", re.I)
ALLOWED = ("/core/lib.js", "/kit/vs.js", "/kit/illustrated.js", "/core/fonts/fonts.css")


def lint(html: str) -> list[str]:
    """What the page must not do or must have, before anything is rendered."""
    out = []
    for m in UNSAFE.finditer(html):
        out.append(f"'{m.group(0).strip()}' is not allowed (deterministic drawing only, no network, no timers)")
    for u in IMPORT.findall(html) + URL.findall(html):
        if u not in ALLOWED and not u.startswith("data:"):
            out.append(f"'{u}' is not allowed: import only from /core/lib.js and /kit/vs.js (fonts: /core/fonts/fonts.css)")
    for need, why in (("window.render", "window.render(t) is missing"), ("window.DUR", "window.DUR is missing"),
                      ("window.READY", "window.READY = true is never set"), ("window.TEXTS", "window.TEXTS(t) is missing")):
        if need not in html:
            out.append(why)
    return list(dict.fromkeys(out))[:12]


def parse_html(reply: str) -> str:
    m = re.search(r"```html\s*(.*?)```", reply or "", re.S | re.I) or re.search(r"(<!doctype html.*</html>)", reply or "",
                                                                               re.S | re.I)
    if not m:
        raise ProgrammaticError("the page writer returned no HTML")
    return m.group(1).strip()


# ------------------------------------------------------------------------------------------------ timeline
def timeline(beats: list[dict], durations: list[float | None]) -> tuple[list[dict], float]:
    """Each beat as long as its spoken line (a short lead-in, the line, a breath); a beat without audio uses the
    line's reading pace. Returns (TIMELINE, DUR)."""
    t, out = 0.0, []
    for i, (b, d) in enumerate(zip(beats, durations)):
        line = str(b.get("line") or "").strip()
        spoken = float(d) if d else max(1.5, len(line.split()) / WORDS_PER_S)
        t0 = t
        lt0 = t0 + (LEAD_S if i == 0 else 0.25)
        t1 = lt0 + spoken + GAP_S
        out.append({"id": b.get("id") or f"b{i + 1}", "t0": round(t0, 3), "t1": round(t1, 3),
                    "image": str(b.get("image") or b.get("picture") or ""),
                    "line_t0": round(lt0, 3), "line_t1": round(lt0 + spoken, 3), "line": line,
                    "picture": str(b.get("picture") or ""), "words": str(b.get("words") or ""),
                    "sfx": [s for s in b.get("sfx") or [] if s in SFX]})
        t = t1
    out[-1]["t1"] = round(t + TAIL_S, 3) if out else TAIL_S
    return out, round(out[-1]["t1"] if out else TAIL_S, 3)


def srt(tl: list[dict]) -> str:
    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{ts(b['line_t0'])} --> {ts(b['line_t1'])}\n{b['line']}\n"
                     for i, b in enumerate([b for b in tl if b["line"]], start=1))


def cues(tl: list[dict], page_ev: list[dict], dur: float) -> list[dict]:
    """The page's own cues; a beat whose sfx the page did not place gets it shortly after the beat starts."""
    ev = [{"t": float(e["t"]), "type": str(e["type"])} for e in page_ev or []
          if isinstance(e, dict) and str(e.get("type")) in SFX and 0 <= float(e.get("t") or -1) < dur]
    for b in tl:
        for k, s in enumerate(b["sfx"]):
            if not any(e["type"] == s and b["t0"] <= e["t"] < b["t1"] for e in ev):
                ev.append({"t": round(b["line_t0"] + 0.3 + k * 0.6, 3), "type": s})
    return sorted(ev, key=lambda e: e["t"])[:120]


# ------------------------------------------------------------------------------------------------ running
def _env() -> dict:
    return {**os.environ, "PLAYWRIGHT_CHROME": CHROME}


async def _node(args: list[str], timeout: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("node", *args, cwd=str(RUNTIME), env=_env(),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, f"timed out after {timeout} s"
    return proc.returncode, out.decode(errors="replace")


def page_errors(output: str) -> list[str]:
    """The renderer's own complaints about the page (page errors, missing files, a READY that never came)."""
    keep = []
    for l in output.splitlines():
        if re.search(r"\[pageerror\]|\[page\]|never became true|not allowed|needs /|WebGL|must be|\w*Error:", l):
            m = re.search(r"\b(\w*Error: .*)", l)  # "page.evaluate: ReferenceError: x is not defined" -> the error
            keep.append(("[pageerror] " + m.group(1) if m and "[pageerror]" not in l else l).strip())
    return list(dict.fromkeys(keep))[:8]


def readcheck_problems(output: str) -> list[str]:
    out = []
    for l in output.splitlines():
        m = re.match(r"BAD\s+(\S+)\s+at\s+([\d.]+)s\s+(.*)", l.strip())
        if m:
            out.append(f"text '{m.group(1)}' at {m.group(2)} s: {m.group(3).strip()} - hold it longer or keep it "
                       "inside the frame")
        elif "NOT CHECKED" in l:
            out.append(l.strip())
    return out[:10]


async def stills(film: Path, times: list[float], w: int, h: int, out: Path) -> tuple[list[Path], list[str]]:
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.jpg"):
        f.unlink()
    code, text = await _node(["core/render/still.mjs", str(film), *[f"{t:.2f}" for t in times], "--size", f"{w}x{h}",
                              "--out", str(out), "--prefix", "s_"], 600)
    shots = sorted(out.glob("s_*.jpg"), key=lambda p: float(p.stem[2:]))
    return shots, ([] if code == 0 else (page_errors(text) or [text.strip()[-400:]]))


async def review(shots: list[Path], tl: list[dict], style: str) -> list[str]:
    if not shots:
        return []
    beats = "\n".join(f"beat {i + 1}: {b['picture'][:220]}" + (f" (words: {b['words']})" if b["words"] else "")
                      for i, b in enumerate(tl))
    try:
        data = await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": REVIEW.replace("{STYLE}", STYLES[style]["name"]).replace("{BEATS}", beats)},
            *[llm.image_part(p) for p in shots[:8]]]}], max_tokens=2000, reasoning_effort="low",
            stage="engine.programmatic.review")
    except llm.LLMError as e:  # the review is a quality aid; never fail the film for it
        log.warning("programmatic review failed: %s", e)
        return []
    problems = [str(p) for p in data.get("problems") or [] if str(p).strip()]
    return [] if data.get("ok") is True and not problems else problems[:8]


async def check(film: Path, tl: list[dict], dur: float, w: int, h: int, style: str, work: Path,
                look: bool = True) -> list[str]:
    """Everything wrong with the page: lint, page errors while drawing stills, reading time, then the vision review."""
    problems = lint((film / "index.html").read_text())
    if problems:
        return problems
    times = [min(dur - 0.05, (b["line_t0"] + b["line_t1"]) / 2 + 0.6) for b in tl]
    shots, errors = await stills(film, times, w, h, work / "stills")
    if errors:
        return errors
    code, text = await _node(["core/render/readcheck.mjs", str(film), "--size", f"{w}x{h}"], 600)
    problems = readcheck_problems(text) if code != 0 else []
    if look:
        problems += await review(shots, tl, style)
    return problems


async def _llm_text(messages: list[dict], stage: str, tokens: int = 32000) -> str:
    return await llm.chat_text(config.REASONING_MODEL, messages, max_tokens=tokens, stage=stage)


async def direct(prompt: str, seconds: int, style: str | None) -> dict:
    beats = max(4, min(18, round(seconds / 5)))
    system = (DIRECTOR.replace("{STYLES}", style_list()).replace("{BEATS}", str(beats))
              .replace("{SECONDS}", str(seconds)).replace("{SFX}", ", ".join(SFX)))
    user = prompt.strip() + (f"\n\nThe look is fixed: {style}." if style else "")
    data = await llm.chat_json(config.REASONING_MODEL, [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}],
                               max_tokens=8000, reasoning_effort="medium", stage="engine.programmatic.director")
    data["style"] = style or (data.get("style") if data.get("style") in STYLES else "whiteboard")
    data["beats"] = [b for b in data.get("beats") or [] if isinstance(b, dict) and str(b.get("line") or "").strip()]
    if not data["beats"]:
        raise ProgrammaticError("the director wrote no beats")
    return data


def page_messages(script: dict, tl: list[dict], dur: float, w: int, h: int, aspect: str) -> list[dict]:
    st = STYLES[script["style"]]
    system = (PAGE.replace("{W}", str(w)).replace("{H}", str(h)).replace("{DUR}", f"{dur:g}")
              .replace("{SFX}", ", ".join(SFX)).replace("{STYLE_NAME}", st["name"]).replace("{LOOK}", st["look"])
              .replace("{MOTION}", st["motion"]).replace("{PALETTE}", ", ".join(script.get("palette") or []) or "the look's")
              .replace("{MIN_PX}", str(max(44, round(min(w, h) * 0.045)))).replace("{ASPECT}", aspect))
    user = (f"TITLE: {script.get('title') or ''}\nSOUND CUES this look uses: {st['sfx']}\n"
            f"TIMELINE (seconds):\n{json.dumps(tl, ensure_ascii=False, indent=1)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def voice(tl_beats: list[dict], language: str, work: Path, progress, name: str) -> tuple[list, list[str]]:
    """Every narration line through Kokoro (local). Returns (files or None per beat, voices used)."""
    from . import presenter_engine, story_engine
    used: list[str] = []
    files: list[str | None] = []
    await progress(f"{name}: recording the narration ({len(tl_beats)} lines, local voice)")
    key = os.getenv("FAL_KEY", "")
    for i, b in enumerate(tl_beats):
        try:
            files.append(await presenter_engine.speak(str(b["line"]), "female", language or "english", key,
                                                      work / f"vo_{i + 1:02d}", used))
        except Exception as e:  # a film with subtitles and music is still a film
            log.warning("narration line %s failed: %s", i + 1, e)
            files.append(None)
    return files, used


# ------------------------------------------------------------------------------------------------ realistic looks
IMAGE_QA = """These are images for a film in a {LOOK} look. Image 1 is the empty surface; images 2.. are the beats,
in order. What each beat image must show:
{BEATS}
For each BEAT image (2..), report it only if it has a real problem: ANY visible letters, words, numbers or writing (even
fake or garbled lettering); it does not show what it must; or (for a drawn look) it is not the same surface, camera and
light as image 1. Return ONLY JSON {"bad": [{"n": <image number>, "why": "..."}]}."""


def can_illustrate(style: str) -> bool:
    return style in ILLUSTRATED and bool(os.getenv("FAL_KEY"))


def image_prompts(style: str, tl: list[dict]) -> tuple[str, list[str]]:
    """(the empty surface, each beat's picture) for the image model."""
    il = ILLUSTRATED[style]
    blank = f"{il['surface']}. Nothing is drawn or placed on it yet. {NO_TEXT}"
    if il["reveal"] == "draw":
        beats = [f"The SAME surface as the reference image - same camera, framing, light, texture and old marks. "
                 f"{il['medium'][0].upper() + il['medium'][1:]}: {b['image']}. Only this drawing is added; everything "
                 f"else stays exactly as in the reference. {NO_TEXT}" for b in tl]
    else:
        beats = [f"{il['medium'][0].upper() + il['medium'][1:]}: {b['image']}. Same look, palette and light as the "
                 f"reference image. {NO_TEXT}" for b in tl]
    return blank, beats


async def illustrate(style: str, tl: list[dict], aspect: str, film: Path, progress, name: str) -> list[str]:
    """The empty surface, then every beat's picture made from it (all at once), checked for text and redrawn once.
    Returns the image paths relative to the film page: [blank, b1, b2, ...]."""
    from . import story_engine
    from .vendor.flow import fal_helper
    key, model = story_engine.fal_key(), story_engine.quality()["image_model"]
    (film / "img").mkdir(exist_ok=True)
    blank_p, beat_p = image_prompts(style, tl)

    async def gen(prompt: str, refs: list[str], dest: Path) -> str:
        kw = {"aspect_ratio": aspect, "num_images": 1} | ({"image_urls": refs} if refs else {})
        url = story_engine._url(await story_engine._fal(fal_helper.generate_image, key, model, prompt, **kw), "image")
        await story_engine._download(url, dest)
        return url

    await progress(f"{name}: painting the empty {STYLES[style]['name'].lower()} (image model)")
    blank_url = await gen(blank_p, [], film / "img" / "blank.png")
    await progress(f"{name}: painting {len(tl)} beat pictures on it, all at once")
    paths = [film / "img" / f"b{i + 1}.png" for i in range(len(tl))]
    got = await asyncio.gather(*[gen(p, [blank_url], d) for p, d in zip(beat_p, paths)], return_exceptions=True)
    for i, g in enumerate(got):  # one more try for a refused or failed picture
        if isinstance(g, Exception):
            log.warning("beat %s picture failed: %s", i + 1, g)
            await gen(beat_p[i], [blank_url], paths[i])
    try:
        beats = "\n".join(f"image {i + 2}: {b['image'][:200]}" for i, b in enumerate(tl))
        data = await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": IMAGE_QA.replace("{LOOK}", STYLES[style]["name"]).replace("{BEATS}", beats)},
            llm.image_part(film / "img" / "blank.png"), *[llm.image_part(p) for p in paths]]}],
            max_tokens=2000, reasoning_effort="low", stage="engine.programmatic.image_qa")
        bad = {int(b["n"]) - 2: str(b.get("why") or "") for b in data.get("bad") or []
               if isinstance(b, dict) and 2 <= int(b.get("n") or 0) <= len(tl) + 1}
    except (llm.LLMError, ValueError, TypeError, KeyError) as e:  # the check is a quality aid
        log.warning("image check failed: %s", e)
        bad = {}
    if bad:
        await progress(f"{name}: redrawing {len(bad)} picture{'s' if len(bad) > 1 else ''} "
                       f"({next(iter(bad.values()))[:80]})")
        await asyncio.gather(*[gen(beat_p[i] + f" FIX: {why}. Absolutely no lettering.", [blank_url], paths[i])
                               for i, why in bad.items()], return_exceptions=True)
    return ["img/blank.png"] + [f"img/{p.name}" for p in paths]


ILLUSTRATED_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/core/fonts/fonts.css">
<style>html,body{margin:0;overflow:hidden;background:#000}canvas{display:block}</style></head><body>
<canvas id="film"></canvas>
<script type="module">
import { loadImages, cover, makeMask, reveal, words, fit, dust, clamp, seg, ss } from '/kit/illustrated.js';
const TL = __TL__, IMGS = __IMGS__, LOOK = __LOOK__;
window.TIMELINE = TL; window.DUR = __DUR__;
const cv = document.getElementById('film'), W = innerWidth, H = innerHeight; cv.width = W; cv.height = H;
const g = cv.getContext('2d');
const comp = document.createElement('canvas'); comp.width = W; comp.height = H; const c = comp.getContext('2d');
const DIRS = ['ltr', 'center', 'ttb', 'blobs', 'rtl'], ERASE = 0.6, PUSH = 0.06;
const imgs = await loadImages(IMGS), blank = imgs[0], pics = imgs.slice(1);
const masks = TL.map((b, i) => makeMask(W, H, 11 + i * 7, LOOK.reveal === 'draw' ? DIRS[i % DIRS.length] : 'blobs'));
const wipes = TL.map((b, i) => makeMask(W, H, 101 + i, i % 2 ? 'rtl' : 'ltr'));
const focus = TL.map((b, i) => [0.5 + 0.06 * Math.sin(i * 2.1), 0.5 + 0.05 * Math.cos(i * 1.7)]);
let boxes = [];
const beatAt = t => { for (let i = TL.length - 1; i >= 0; i--) if (t >= TL[i].t0) return i; return 0; };
const drawTime = b => Math.max(1.4, Math.min(4.5, 0.75 * (b.line_t1 - b.line_t0)));
const wordsWindow = (b, i) => [b.line_t0 + 0.25, LOOK.reveal === 'draw' && i < TL.length - 1 ? b.t1 - ERASE : b.t1 + 1];
window.render = t => {
  boxes = [];
  const i = beatAt(t), b = TL[i], last = i === TL.length - 1;
  c.globalAlpha = 1; cover(c, blank, W, H);
  if (LOOK.reveal === 'draw') {
    const p = ss(seg(t, b.line_t0 - 0.15, b.line_t0 + drawTime(b)));
    reveal(c, pics[i], masks[i], p, W, H);
    const e = last ? 0 : seg(t, b.t1 - ERASE, b.t1);
    if (e > 0) reveal(c, blank, wipes[i], ss(e), W, H);
  } else {
    if (i > 0) cover(c, pics[i - 1], W, H);
    const p = ss(seg(t, b.t0, b.t0 + (i ? 0.7 : 1.4)));
    if (i === 0) reveal(c, pics[0], masks[0], p, W, H); else { c.globalAlpha = p; cover(c, pics[i], W, H); c.globalAlpha = 1; }
  }
  const z = 1 + PUSH * ss(seg(t, b.t0, b.t1)), [fx, fy] = focus[i];
  g.save(); g.translate(W * fx, H * fy); g.scale(z, z); g.translate(-W * fx, -H * fy); g.drawImage(comp, 0, 0); g.restore();
  if (LOOK.reveal === 'draw' && !last) { const e = seg(t, b.t1 - ERASE, b.t1); if (e > 0 && e < 1) dust(g, t, W * (i % 2 ? 1 - e : e), H * 0.5, 1 - e, 7 + i, LOOK.dust); }
  const [w0, w1] = wordsWindow(b, i);
  if (b.words && t >= w0 && t < w1) {
    const size = fit(g, b.words, W * 0.8, `"${LOOK.font}"`, LOOK.weight, Math.round(Math.min(W, H) * 0.095), 30);
    const box = words(g, b.words, W / 2, H * 0.06 + size, { font: `${LOOK.weight} ${size}px "${LOOK.font}"`,
      color: LOOK.color, texture: LOOK.texture, shadow: LOOK.shadow, progress: seg(t, w0, w0 + 0.06 * b.words.length + 0.3) });
    boxes.push({ id: b.id, text: b.words, ...box });
  }
};
window.TEXTS = t => { window.render(t); return boxes; };
window.EV = TL.flatMap((b, i) => LOOK.reveal === 'draw' && i < TL.length - 1 ? [{ t: b.t1 - ERASE, type: 'whoosh' }] : []);
await document.fonts.load(`${LOOK.weight} 64px "${LOOK.font}"`);
window.READY = true;
</script></body></html>"""


def illustrated_page(style: str, tl: list[dict], dur: float, imgs: list[str]) -> str:
    il = ILLUSTRATED[style]
    look = {"reveal": il["reveal"], "font": il["font"], "weight": il["weight"], "color": il["color"],
            "texture": il["texture"], "shadow": il["shadow"],
            "dust": "235,232,220" if style == "chalkboard" else "200,200,200"}
    beats = [{k: b[k] for k in ("id", "t0", "t1", "line_t0", "line_t1", "words")} for b in tl]
    return (ILLUSTRATED_PAGE.replace("__TL__", json.dumps(beats, ensure_ascii=False))
            .replace("__IMGS__", json.dumps(imgs)).replace("__LOOK__", json.dumps(look)).replace("__DUR__", f"{dur:g}"))


async def run(app: dict, handoff: dict, work: Path, progress) -> dict:
    """Make the film. Returns {"path", "duration_s", "title", "style", "script", "subtitles", "checks"}."""
    from . import music, presenter_engine
    from .vendor.flow import video_merge
    why = available()
    if why:
        raise ProgrammaticError(why)
    name = app["name"]
    req = handoff.get("request") or {}
    prompt = (handoff.get("prompt") or req.get("prompt") or "").strip()
    aspect = req.get("aspect_ratio") if req.get("aspect_ratio") in SIZES else "16:9"
    w, h = SIZES[aspect]
    seconds = int(max(15, min(180, float(req.get("duration") or 45))))
    work.mkdir(parents=True, exist_ok=True)
    film = work / "film"
    film.mkdir(exist_ok=True)

    await progress(f"{name}: writing the film (script, look, beats)")
    script = await direct(prompt, seconds, req.get("style") if req.get("style") in STYLES else named_style(prompt))
    style = script["style"]
    await progress(f"{name}: '{script.get('title') or 'the film'}' in the {STYLES[style]['name']} look, "
                   f"{len(script['beats'])} beats")
    lang = {"en": "english", "es": "spanish", "fr": "french", "hi": "hindi", "ja": "japanese", "zh": "chinese",
            "pt": "portuguese", "it": "italian"}.get(str(script.get("language") or "en")[:2], "english")
    files, used = await voice(script["beats"], lang, work, progress, name)
    ff = video_merge.find_ffmpeg()
    lengths = [await asyncio.to_thread(video_merge.probe_duration, ff, f) if f else None for f in files]
    tl, dur = timeline(script["beats"], lengths)

    music_job = None
    if script.get("music") and music.available() is None:
        music_job = asyncio.create_task(music.compose(f"{script['music']}, instrumental only", dur + 1, work / "music.wav"))

    problems: list[str] = []
    illustrated = can_illustrate(style)
    if illustrated:  # the realistic version: real-looking images from the image model, animated by a tested template
        try:
            imgs = await illustrate(style, tl, aspect, film, progress, name)
        except Exception as e:  # no images: the film is still drawn in code
            log.warning("illustration failed, drawing in code: %s", e)
            await progress(f"{name}: the image model failed ({str(e)[:100]}); drawing the film in code instead")
            illustrated = False
    if illustrated:
        html = illustrated_page(style, tl, dur, imgs)
        (film / "index.html").write_text(html)
        await progress(f"{name}: checking the film (stills, reading time)")
        problems = await check(film, tl, dur, w, h, style, work, look=False)
        if any("[pageerror]" in p or "never became true" in p for p in problems):
            raise ProgrammaticError("the illustrated film failed to draw: " + "; ".join(problems[:3]))
    else:
        await progress(f"{name}: drawing the film page ({dur:.0f} s)")
        msgs = page_messages(script, tl, dur, w, h, aspect)
        html = parse_html(await _llm_text(msgs, "engine.programmatic.page"))
    for rnd in range(0 if illustrated else FIX_ROUNDS + 1):
        (film / "index.html").write_text(html)
        await progress(f"{name}: checking the page (stills, reading time, a look at every beat)")
        problems = await check(film, tl, dur, w, h, style, work, look=rnd < FIX_ROUNDS)
        if not problems or rnd == FIX_ROUNDS:
            break
        await progress(f"{name}: fixing {len(problems)} problem{'s' if len(problems) > 1 else ''} ({problems[0][:90]})")
        msgs = msgs + [{"role": "assistant", "content": f"```html\n{html}\n```"},
                       {"role": "user", "content": FIX.replace("{ISSUES}", "\n".join(f"- {p}" for p in problems))}]
        html = parse_html(await _llm_text(msgs, f"engine.programmatic.fix{rnd + 1}"))
    hard = [] if illustrated else [p for p in problems if p in lint(html) or "[pageerror]" in p
                                   or "never became true" in p]
    if hard:
        raise ProgrammaticError("the film page still fails: " + "; ".join(hard[:3]))

    await progress(f"{name}: rendering {round(dur * FPS)} frames")
    raw = work / "picture.mp4"
    code, text = await _node(["core/render/video.mjs", str(film), "--fps", str(FPS), "--workers", "3",
                              "--size", f"{w}x{h}", "--out", str(raw)], RENDER_TIMEOUT_S)
    if code != 0 or not raw.exists():
        raise ProgrammaticError("the render failed: " + "; ".join(page_errors(text) or [text.strip()[-300:]]))
    code, text = await _node(["core/render/events.mjs", str(film), "--size", f"{w}x{h}",
                              "--out", str(work / "events.json")], 300)
    page_ev = (json.loads((work / "events.json").read_text()).get("ev") or []) if code == 0 else []

    await progress(f"{name}: mixing narration, sound effects and music")
    track = None
    if music_job:
        try:
            track = await music_job
        except Exception as e:  # music is a finishing touch
            log.warning("music failed: %s", e)
    spec = {"dur": dur, "narration": [{"path": f, "t": b["line_t0"]} for f, b in zip(files, tl) if f],
            "events": cues(tl, page_ev, dur), "music": track}
    (work / "mix.json").write_text(json.dumps(spec))
    mixed = work / "mix.wav"
    res = await presenter_engine.run_local([presenter_engine.LOCAL_PY, RUNTIME / "kit" / "mix.py", work / "mix.json",
                                            mixed], "The sound mix", timeout=600)
    final = work / "final.mp4"
    proc = await asyncio.create_subprocess_exec("sh", str(RUNTIME / "core" / "render" / "mux.sh"), str(raw), str(mixed),
                                                str(final), str(FPS), str(STYLES[style]["grain"]), cwd=str(RUNTIME),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    if proc.returncode != 0 or not final.exists():
        raise ProgrammaticError("mastering failed: " + out.decode(errors="replace").strip()[-300:])
    subs = work / "film.srt"
    subs.write_text(srt(tl))
    return {"path": str(final), "duration_s": await asyncio.to_thread(video_merge.probe_duration, ff, str(final)),
            "title": script.get("title") or "", "style": style, "style_name": STYLES[style]["name"],
            "why_style": script.get("why_style") or "", "script": "\n".join(b["line"] for b in tl),
            "subtitles": str(subs), "checks": problems, "voice": sorted(set(used)), "sfx": res.get("events", 0),
            "music": bool(track), "mastering": out.decode(errors="replace").strip().splitlines()[-1:],
            "illustrated": illustrated}


# ------------------------------------------------------------------------------------------------ the plan
def build(run: dict) -> dict:
    """{"understanding", "plan"}: one step, the Programmatic Film, with the user's words and settings."""
    from router_core import registry
    app = registry.apps()[APP_ID]
    st = run.get("settings") or {}
    words = (run.get("prompt") or "").strip()
    dur = st.get("duration_s")
    ar = st.get("aspect_ratio")
    style = named_style(words)
    caps = ["programmatic.styled_film"]
    look = f"the {STYLES[style]['name']} look" if style else "the look that fits the topic"
    step = {"step_id": "s1", "app_id": APP_ID, "subtask_ids": ["t_film"], "capabilities_used": caps,
            "responsibility": f"Write the narrated film, draw every frame of it in code in {look}, check it (page "
                              "errors, reading time, a look at every beat), render it, and mix narration, sound "
                              "effects and music to broadcast loudness.",
            "inputs": [{"source": "prompt", "format": "text"}], "outputs": ["video_file"],
            "handoff": {"app_id": APP_ID, "app_name": app["name"], "prompt": words, "language": "", "endpoint": "",
                        "request": {"prompt": words, "duration": dur, "duration_given": bool(dur),
                                    "aspect_ratio": ar, "style": style}, "notes": []}}
    understanding = {
        "detected_intent": "create_new", "user_goal": words, "goal": words,
        "requested_output": f"a programmatic film in {look}" + (f", {dur} s" if dur else "") + (f", {ar}" if ar else ""),
        "production_type": ["explainer"], "assumptions": ["every picture is drawn in code: no footage or AI video"],
        "scene": {"narrative": False}, "constraints": {"duration_s": dur, "aspect_ratio": ar},
        "subtasks": [{"id": "t_film", "group": "generate", "description": f"A film drawn in code in {look}",
                      "required_capabilities": caps, "basis": "request"}],
        "direct": True, "confidence": 1.0}
    return {"understanding": understanding, "plan": {"feasible": True, "steps": [step], "direct": True}}
