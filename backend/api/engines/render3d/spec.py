"""The scene planner: the story's events become a 3D scene the renderer can build and animate.

One LLM call turns the user's words and the temporal event graph (engines/temporal) into a scene spec: the actors
(rigged humanoids, quadrupeds, birds) with their look, the props (rooms, doors, furniture, nature, vehicles, products)
with real sizes and positions in metres, one timeline of events (who does what, from when to when, to where), and
camera shots that follow the action. `normalize` then makes that spec safe and consistent for the renderer: known
vocabulary only, unique ids, events on the clock without overlapping moves, shots that cover the whole film.
Nothing here is specific to one kind of story; the planner gets the same rules for a fable, a product film or an
action scene.
"""

from __future__ import annotations

import json
import re

from router_core import config, llm

ACTOR_KINDS = ("humanoid", "quadruped", "bird")
SPECIES = ("rabbit", "hare", "frog", "tortoise", "turtle", "cat", "dog", "fox", "wolf", "horse", "deer", "cow", "bear",
           "lion", "tiger", "elephant", "pig", "sheep", "mouse", "squirrel")
PROP_TYPES = ("room", "door", "wall", "chair", "stool", "sofa", "couch", "bench", "table", "desk", "counter", "bed",
              "shelf", "bookshelf", "cabinet", "fridge", "wardrobe", "tv", "lamp", "streetlight", "tree", "pine", "palm",
              "bush", "plant", "flower", "rock", "boulder", "mountain", "hill", "river", "water", "lake", "pond", "pool",
              "bridge", "fence", "house", "building", "road", "street", "path", "sidewalk", "car", "truck", "bus", "van",
              "bicycle", "cup", "mug", "book", "bag", "suitcase", "bottle", "phone", "key", "coin", "sign", "box",
              "crate", "gift", "ball", "sphere", "cube", "cylinder", "cone", "torus", "capsule", "plane", "rug",
              "platform", "pedestal", "stage", "cloud", "product", "shape")
MATERIALS = ("wood", "bark", "fabric", "leather", "stone", "concrete", "brick", "asphalt", "grass", "foliage", "soil",
             "sand", "snow", "water", "skin", "fur", "plastic", "paint", "metal", "ceramic", "rubber", "paper", "glass",
             "chrome", "emissive", "shell")
ACTOR_ACTIONS = ("walk", "run", "move", "approach", "enter", "exit", "jump", "hop", "sit", "stand_up", "lie_down",
                 "turn", "fly", "land", "swim", "crawl", "follow", "chase", "flee", "climb", "fall", "wave", "talk",
                 "look", "point", "nod", "clap", "eat", "drink", "hug", "shake_hands", "meet", "greet", "idle",
                 "celebrate", "think", "type", "read", "dance", "push", "pull", "throw", "catch", "open", "close",
                 "pick_up", "drop", "place", "give", "take")
PROP_ACTIONS = ("rotate", "spin", "explode", "assemble", "move", "scale", "float", "bounce", "appear", "disappear",
                "color", "morph", "light_on", "light_off", "fall", "drive", "orbit")
LOCO = {"walk", "run", "move", "approach", "enter", "exit", "jump", "hop", "sit", "stand_up", "lie_down", "turn", "fly",
        "land", "swim", "crawl", "follow", "chase", "flee", "climb", "fall"}
# hand actions need the subject standing still: they never overlap a move of the same subject
HANDS = {"open", "close", "pick_up", "take", "drop", "place", "give", "hug", "shake_hands", "throw", "catch", "push", "pull"}
MOVES = ("follow", "static", "dolly_in", "dolly_out", "reverse_dolly", "orbit", "arc", "turntable", "crane_up",
         "crane_down", "crane_arc", "handheld_follow", "track_side", "pan", "tilt_up", "tilt_down", "vertigo",
         "top_down", "over_shoulder", "two_shot", "chase")
TIMES = ("day", "golden", "dusk", "night", "overcast")
GROUNDS = ("grass", "dirt", "sand", "snow", "asphalt", "concrete", "wood", "stone", "tiles", "carpet", "studio", "forest")
SYNONYMS = {"sits": "sit", "sit_down": "sit", "sitdown": "sit", "stand": "stand_up", "standup": "stand_up",
            "get_up": "stand_up", "walks": "walk", "runs": "run", "jumps": "jump", "leap": "jump", "jump_over": "jump",
            "picks_up": "pick_up", "pickup": "pick_up", "grab": "pick_up", "put_down": "place", "puts": "place",
            "opens": "open", "closes": "close", "rotate_360": "rotate", "turntable": "rotate", "spin_360": "rotate",
            "exploded_view": "explode", "explode_view": "explode", "reassemble": "assemble", "lie": "lie_down",
            "fly_to": "fly", "drives": "drive", "goes_to": "walk", "go": "walk", "speak": "talk", "say": "talk",
            "greets": "greet", "meets": "meet", "hugs": "hug", "looks_at": "look", "watch": "look", "transform": "morph"}
MAX_DURATION = 30.0   # the video-to-video model takes at most 30 s in one call
MIN_DURATION = 3.0
SIZES = {"16:9": {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)},
         "9:16": {"720p": (720, 1280), "1080p": (1080, 1920), "4k": (2160, 3840)},
         "1:1": {"720p": (1024, 1024), "1080p": (1440, 1440), "4k": (2160, 2160)}}
FPS_ALLOWED = (24, 25, 30, 60)


class SpecError(RuntimeError):
    pass


PLANNER = """You are a 3D layout artist, animator and cinematographer. You stage a story as a real 3D scene that a
program builds from primitives and rigged characters and animates on one clock. The animation decides WHAT HAPPENS;
the camera decides HOW IT IS SHOWN; a video-to-video AI later re-renders your untextured 3D film as photoreal footage,
keeping your motion, timing, positions and camera exactly. So stage every action fully on screen, in order, with
nothing teleporting.

WORLD: metres, y up, the ground is y=0. A subject with facing 0 looks toward +z; facing 90 looks toward +x.
Keep the action within about 25 m of the origin. Real sizes: an adult 1.6-1.9 m, a child 1.1-1.4 m, a door 0.95 x 2.1,
a chair seat 0.46 high, a table 0.75 high, a car 4.4 x 1.85 x 1.45, a rabbit 0.2 high, a tortoise 0.14 high.

Return ONLY this JSON:
{"title": "...", "logline": "one sentence",
 "duration": seconds (the sum of what happens; {MIN}-{MAX}),
 "look": "the photoreal look for the final film: era, place, light, lens, colour, mood (one sentence)",
 "environment": {"time_of_day": one of {TIMES}, "ground": one of {GROUNDS}, "indoor": false, "studio": false,
                 "fog": 0-0.05, "sun_azimuth": deg, "sun_elevation": deg, "exposure": 0.8-1.3,
                 "description": "what the place is, for the realistic re-render",
                 "scatter": [{"type": "tree|pine|bush|rock|flower|cloud", "count": n, "area": [x0, z0, x1, z1], "size": 1}]},
 "actors": [{"id": "A1", "name": "...", "kind": "humanoid|quadruped|bird", "species": "for animals: {SPECIES}",
             "height": metres, "color": "#hex (fur/feathers)", "start": {"x": 0, "z": 0, "facing": deg},
             "appearance": {"skin": "#hex", "hair": "#hex", "top": "#hex", "bottom": "#hex", "shoes": "#hex",
                            "build": "slim|average|heavy", "long_hair": false, "dress": false, "child": false,
                            "sleeves": "long|short", "shorts": false},
             "look": "how this character must look when photoreal: age, face, hair, clothes / fur, colours (one sentence)"}],
 "props": [{"id": "P1", "type": one of {PROPS}, "name": "...", "x": 0, "z": 0, "rot": deg, "w": m, "h": m, "d": m,
            "color": "#hex", "material": one of {MATERIALS},
            "variant": "for product: bottle|perfume|can|phone|watch|headphones|shoe|sneaker|laptop|camera|speaker|generic; for tree: pine",
            "size": "product size in m", "accent": "#hex", "room": "for a door: the room id it stands in",
            "door_side": "for a room: front|back|left|right", "look": "photoreal description"}],
 "events": [{"id": "S1", "graph_event": "E1", "actor": "A1 or a prop id for prop animations", "action": "...",
             "t0": s, "t1": s, "target": "prop or actor id", "to": {"x": 0, "z": 0} or {"prop": "P2", "anchor": "approach"},
             "via": [{"x": 0, "z": 0}], "height": m, "degrees": deg, "axis": "y", "amount": 1, "face": deg,
             "label": "what happens, in plain words"}],
 "camera": [{"t0": 0, "t1": s, "move": one of {MOVES}, "target": "A1" or ["A1", "A2"], "angle": deg,
             "distance": m, "height": m, "fov": deg, "sweep": deg, "rise": m, "why": "..."}]}

ACTIONS for characters: {ACTOR_ACTIONS}.
  walk/run/approach/move/hop/fly/swim/crawl/chase/flee/follow: go "to" a point (or a prop: {"prop": id, "anchor":
  "approach"}; or another actor id to meet them, the renderer stops short of them); optional "via" waypoints.
  jump: over something on the way, "to" the landing point, "height" in m. enter / exit: target a room (it walks
  through its doorway). sit / lie_down: target a chair, sofa, bench or bed (it turns and sits on its seat).
  stand_up: after sitting. turn: "degrees" or a "target" to face. open / close: target a door, fridge, cabinet or
  wardrobe (put the actor at the door's "approach" anchor first). pick_up / take: target a small prop (the actor
  must be next to it); drop / place (with "to" a point or a table id) / give (with "receiver") / throw: release it.
  wave, talk, look (target), point, nod, clap, eat, drink, hug, shake_hands, meet, greet, celebrate, think, type,
  read, dance: gestures that may overlap a move.
ACTIONS for props (the event's "actor" is the prop id): {PROP_ACTIONS}. rotate: "degrees" (360 = a full turn) about
  "axis"; explode / assemble: parts of a product fly apart along their axes and back ("amount" 1); move / drive:
  "to" a point; scale: "amount"; appear / disappear; color: "to" a #hex as "color"; morph: "into" another prop id.
ANCHORS: door "approach" (outside, in front of it), "through" (the doorway), "beyond" (inside); chair / sofa / bed
  "approach" (in front of the seat), "seat"; table "approach", "top"; room "doorway", "inside".
CAMERA moves: {MOVES}. follow / handheld_follow / reverse_dolly / track_side / chase: "angle" is relative to the
  subject's facing (0 = in front looking at the face, 180 = behind, 150 = front-three-quarter from behind... choose
  the side that shows the face and the action); every other move's "angle" is a world direction (0 = camera on +z
  looking toward -z). dolly_in / dolly_out push or pull; orbit / arc / turntable sweep "sweep" degrees; crane_up
  rises "rise" m; crane_arc is a 180 degree rising arc; vertigo is a dolly zoom; two_shot frames two subjects;
  over_shoulder frames target[1] over target[0]'s shoulder.

RULES
- Every event of the TEMPORAL SEQUENCE below is performed on screen, in that order, each with "graph_event" set to
  its id (several events may share one, one event may need several steps such as walk then open). Never skip one,
  never show a result without its action, never put a character somewhere new without the move there.
- A subject's moves never overlap in time; the next move starts where the last one ended. Leave 0.2-0.6 s between
  steps. Typical speeds: walk 1.2-1.5 m/s, run 3-5 m/s, a rabbit hop 2-4 m/s, a tortoise 0.1-0.25 m/s.
- Lay the set out so every path is clear: a chair's front faces where the actor comes from; a room's doorway faces
  the actor outside it; a river crosses the path at a right angle; things to pick up stand where the actor will be.
- Use a room prop (+ a door prop with "room" set) for interiors; set environment.indoor true only when the whole
  film is inside. A product film: environment.studio true, the product on a pedestal at the origin.
- Camera: one shot per beat, covering 0 to duration without gaps; follow moving characters (never leave them out of
  frame); a two_shot when two characters meet; a turntable or orbit for a product; at most one cut every 2 s.
- Colours and materials should already suggest the real thing (skin tones, fabric, fur, wood, grass), never
  neon. No text, logos or signs with writing.
- Characters and objects from the user's words and uploaded images only; invent nothing that changes the story."""


def planner_prompt() -> str:
    return (PLANNER.replace("{MIN}", f"{MIN_DURATION:g}").replace("{MAX}", f"{MAX_DURATION:g}")
            .replace("{TIMES}", "|".join(TIMES)).replace("{GROUNDS}", "|".join(GROUNDS))
            .replace("{SPECIES}", ", ".join(SPECIES)).replace("{PROPS}", "|".join(PROP_TYPES))
            .replace("{MATERIALS}", "|".join(MATERIALS)).replace("{ACTOR_ACTIONS}", ", ".join(ACTOR_ACTIONS))
            .replace("{PROP_ACTIONS}", ", ".join(PROP_ACTIONS)).replace("{MOVES}", ", ".join(MOVES)))


def graph_block(graph: dict) -> str:
    """The temporal sequence the planner must stage (engines/temporal's graph)."""
    evs = graph.get("events") or []
    if not evs:
        return ""
    ents = graph.get("entities") or {}
    lines = []
    for kind in ("characters", "objects", "locations"):
        for e in ents.get(kind) or []:
            lines.append(f"- {kind[:-1]} {e['id']}: {e['name']}" + (f" ({e.get('description')})" if e.get("description") else ""))
    lines.append("TEMPORAL SEQUENCE (every event, in order):")
    for e in evs:
        bits = [f"{e['id']}: {e['description']}"]
        if e.get("requires"):
            bits.append(f"after {', '.join(e['requires'])}")
        if e.get("parallel_with"):
            bits.append(f"together with {', '.join(e['parallel_with'])}")
        if e.get("motion"):
            bits.append(f"motion: {e['motion']}")
        if e.get("camera"):
            bits.append(f"camera: {e['camera']}")
        bits.append(f"~{e.get('duration_s', 3):g} s")
        if not e.get("essential", True):
            bits.append("optional")
        lines.append("- " + " | ".join(bits))
    return "\n".join(lines)


async def plan(prompt: str, graph: dict, seconds: float | None, aspect: str, images_note: str = "",
               fix: list[str] | None = None, previous: dict | None = None) -> dict:
    """The raw scene spec from the planner (normalize it before rendering)."""
    user = "\n\n".join(x for x in (
        f"THE USER'S REQUEST:\n{prompt.strip()}",
        f"UPLOADED IMAGES: {images_note}" if images_note else "",
        graph_block(graph),
        f"LENGTH: {seconds:g} s (the user asked for it)" if seconds else "LENGTH: as long as the events need",
        f"FRAME: {aspect}") if x)
    messages = [{"role": "system", "content": planner_prompt()}, {"role": "user", "content": user}]
    if fix and previous:
        messages += [{"role": "assistant", "content": json.dumps(previous, ensure_ascii=False)[:60000]},
                     {"role": "user", "content": "The 3D reference rendered from this spec has these problems. Return "
                                                 "the whole corrected JSON:\n" + "\n".join(f"- {p}" for p in fix)}]
    try:
        return await llm.chat_json(config.REASONING_MODEL, messages, max_tokens=16000, reasoning_effort="medium",
                                   stage="engine.render3d.fix" if fix else "engine.render3d.scene")
    except llm.LLMError as e:
        raise SpecError(f"the scene planner failed: {e}") from e


# ------------------------------------------------------------------------------------------------ normalize
def _num(v, d: float, lo: float | None = None, hi: float | None = None) -> float:
    try:
        x = float(v)
        if x != x:  # NaN
            raise ValueError
    except (TypeError, ValueError):
        x = d
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")
NAMED = {"gold": "#d4a937", "golden": "#d4a937", "silver": "#c0c4c8", "bronze": "#a86f3c", "copper": "#b5653a",
         "red": "#c0392b", "crimson": "#a3162a", "orange": "#e67e22", "yellow": "#f1c40f", "green": "#2e8b57",
         "blue": "#2e6fd1", "navy": "#1f2f57", "teal": "#1f8a86", "cyan": "#26b5c9", "purple": "#7d3cb5",
         "violet": "#8e44ad", "pink": "#e88aa8", "magenta": "#c2187a", "white": "#f2f2ef", "black": "#151517",
         "grey": "#8a8d91", "gray": "#8a8d91", "brown": "#7a5434", "beige": "#d9c7a7", "cream": "#f2e9d2"}


def _hex(v, d: str | None = None) -> str | None:
    if isinstance(v, str) and _HEX.match(v):
        return "#" + v.lstrip("#").lower()
    if isinstance(v, str) and v.strip().lower() in NAMED:
        return NAMED[v.strip().lower()]
    return d


def _named_in(text: str) -> str | None:
    """The first colour named in a sentence ("turns to polished gold" -> gold)."""
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in NAMED:
            return NAMED[w]
    return None


def _point(v):
    """A destination as the renderer reads it: {"x","z"}, {"prop","anchor"}, {"actor"} or an id string."""
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return {"x": _num(v[0], 0, -60, 60), "z": _num(v[-1], 0, -60, 60)}
    if isinstance(v, dict):
        if v.get("prop") or v.get("actor"):
            out = {k: str(v[k]) for k in ("prop", "actor", "anchor") if v.get(k)}
            return out
        if "x" in v or "z" in v:
            out = {"x": _num(v.get("x"), 0, -60, 60), "z": _num(v.get("z"), 0, -60, 60)}
            if v.get("y") is not None:
                out["y"] = _num(v.get("y"), 0, 0, 30)
            return out
    return None


def size_for(aspect: str, resolution: str) -> tuple[int, int]:
    return SIZES.get(aspect, SIZES["16:9"]).get(resolution, SIZES.get(aspect, SIZES["16:9"])["720p"])


def normalize(raw: dict, *, aspect: str = "16:9", resolution: str = "720p", fps: int = 24,
              seconds: float | None = None) -> tuple[dict, list[str]]:
    """(the spec the renderer gets, notes about what was corrected). Raises SpecError when nothing can be staged."""
    notes: list[str] = []
    raw = raw if isinstance(raw, dict) else {}
    w, h = size_for(aspect, resolution)
    fps = fps if fps in FPS_ALLOWED else 24
    env_raw = raw.get("environment") if isinstance(raw.get("environment"), dict) else {}
    env = {"time_of_day": env_raw.get("time_of_day") if env_raw.get("time_of_day") in TIMES else "day",
           "ground": env_raw.get("ground") if env_raw.get("ground") in GROUNDS else None,
           "indoor": bool(env_raw.get("indoor")), "studio": bool(env_raw.get("studio")),
           "fog": _num(env_raw.get("fog"), 0, 0, 0.08), "sun_azimuth": _num(env_raw.get("sun_azimuth"), 35, -360, 360),
           "sun_elevation": _num(env_raw.get("sun_elevation"), 45, 3, 89),
           "exposure": _num(env_raw.get("exposure"), 1.0, 0.6, 1.6),
           "description": str(env_raw.get("description") or "")[:400]}
    if env["ground"] is None:
        env.pop("ground")
    if _hex(env_raw.get("ground_color")):
        env["ground_color"] = _hex(env_raw.get("ground_color"))
    scatter = []
    for s in (env_raw.get("scatter") or [])[:6]:
        if isinstance(s, dict) and s.get("type") in ("tree", "pine", "palm", "bush", "rock", "flower", "cloud", "plant"):
            area = s.get("area") if isinstance(s.get("area"), list) and len(s["area"]) == 4 else None
            scatter.append({"type": s["type"], "count": int(_num(s.get("count"), 8, 0, 60)), "size": _num(s.get("size"), 1, 0.3, 3),
                            **({"area": [_num(a, 0, -80, 80) for a in area]} if area else {}),
                            **({"color": _hex(s.get("color"))} if _hex(s.get("color")) else {})})
    env["scatter"] = scatter

    # ---- actors
    actors, ids = [], set()
    for i, a in enumerate(raw.get("actors") or [], start=1):
        if not isinstance(a, dict) or len(actors) >= 8:
            continue
        aid = re.sub(r"[^A-Za-z0-9_]", "", str(a.get("id") or f"A{i}")) or f"A{i}"
        if aid in ids:
            aid = f"{aid}_{i}"
        ids.add(aid)
        kind = a.get("kind") if a.get("kind") in ACTOR_KINDS else ("quadruped" if a.get("kind") == "animal" else "humanoid")
        species = str(a.get("species") or "").lower().strip()
        if kind == "humanoid" and species in SPECIES:
            kind = "quadruped"
        st = a.get("start") if isinstance(a.get("start"), dict) else {}
        ap = a.get("appearance") if isinstance(a.get("appearance"), dict) else {}
        out = {"id": aid, "name": str(a.get("name") or aid)[:60], "kind": kind,
               "start": {"x": _num(st.get("x"), 0, -60, 60), "z": _num(st.get("z"), 0, -60, 60),
                         "facing": _num(st.get("facing"), 0, -720, 720)},
               "look": str(a.get("look") or "")[:300]}
        if kind == "quadruped":
            out["species"] = next((s for s in SPECIES if s in species), "dog")
        default_h = 1.75 if kind == "humanoid" else None
        if a.get("height") is not None or default_h:
            out["height"] = _num(a.get("height"), default_h or 0.5, 0.03, 4.0)
        if _hex(a.get("color")):
            out["color"] = _hex(a.get("color"))
        if kind == "humanoid":
            out["appearance"] = {k: _hex(ap.get(k)) for k in ("skin", "hair", "top", "bottom", "shoes") if _hex(ap.get(k))}
            out["appearance"].update({k: bool(ap.get(k)) for k in ("long_hair", "dress", "child", "shorts") if ap.get(k)})
            if ap.get("build") in ("slim", "average", "heavy"):
                out["appearance"]["build"] = ap["build"]
            if ap.get("sleeves") in ("long", "short"):
                out["appearance"]["sleeves"] = ap["sleeves"]
        actors.append(out)

    # ---- props
    props, pids = [], set()
    for i, p in enumerate(raw.get("props") or [], start=1):
        if not isinstance(p, dict) or len(props) >= 60:
            continue
        pid = re.sub(r"[^A-Za-z0-9_]", "", str(p.get("id") or f"P{i}")) or f"P{i}"
        if pid in pids or pid in ids:
            pid = f"{pid}_{i}"
        pids.add(pid)
        typ = str(p.get("type") or "").lower().strip()
        if typ not in PROP_TYPES:
            notes.append(f"prop {pid}: unknown type '{typ}', drawn as a box")
            typ = "box"
        out = {"id": pid, "type": typ, "name": str(p.get("name") or typ)[:60],
               "x": _num(p.get("x"), 0, -80, 80), "z": _num(p.get("z"), 0, -80, 80), "rot": _num(p.get("rot"), 0, -720, 720),
               "look": str(p.get("look") or "")[:300]}
        for k, lo, hi in (("w", 0.01, 80), ("h", 0.01, 120), ("d", 0.01, 200), ("y", 0, 40), ("size", 0.03, 5),
                          ("scale", 0.05, 20), ("open_degrees", 10, 170), ("door_width", 0.6, 3)):
            if p.get(k) is not None:
                out[k] = _num(p.get(k), 1, lo, hi)
        for k in ("color", "accent", "label_color", "floor_color"):
            if _hex(p.get(k)):
                out[k] = _hex(p.get(k))
        if p.get("material") in MATERIALS:
            out["material"] = p["material"]
        if p.get("floor") in MATERIALS:
            out["floor"] = p["floor"]
        for k in ("variant", "door_side", "opens"):
            if p.get(k):
                out[k] = str(p[k]).lower()[:30]
        if p.get("room"):
            out["room"] = str(p["room"])
        if p.get("asset"):
            out["asset"] = str(p["asset"])
        props.append(out)
    for p in props:  # a door's room must exist
        if p.get("room") and not any(q["id"] == p["room"] and q["type"] == "room" for q in props):
            notes.append(f"door {p['id']}: room {p['room']} does not exist; the door stands on its own")
            p.pop("room")
    known = ids | pids
    if not actors and not props:
        raise SpecError("the scene planner placed no characters or objects")

    # ---- events
    events, seen = [], set()
    for i, e in enumerate(raw.get("events") or [], start=1):
        if not isinstance(e, dict) or len(events) >= 120:
            continue
        act = str(e.get("action") or "").lower().strip().replace(" ", "_").replace("-", "_")
        act = SYNONYMS.get(act, act)
        who = str(e.get("actor") or "")
        is_prop = who in pids
        if who not in known:
            notes.append(f"event {e.get('id') or i}: unknown subject '{who}', dropped")
            continue
        allowed = PROP_ACTIONS if is_prop else ACTOR_ACTIONS
        if act not in allowed:
            notes.append(f"event {e.get('id') or i}: {who} cannot '{act}', dropped")
            continue
        eid = re.sub(r"[^A-Za-z0-9_]", "", str(e.get("id") or f"S{i}")) or f"S{i}"
        if eid in seen:
            eid = f"{eid}_{i}"
        seen.add(eid)
        t0 = _num(e.get("t0"), 0, 0, 600)
        t1 = _num(e.get("t1"), t0 + 1.5, 0, 600)
        out = {"id": eid, "actor": who, "action": act, "t0": round(t0, 3), "t1": round(max(t1, t0 + 0.3), 3),
               "label": str(e.get("label") or f"{who} {act}")[:200]}
        g = e.get("graph_event")
        if g:
            out["graph_event"] = [str(x) for x in g] if isinstance(g, list) else [str(g)]
        if e.get("target") is not None:
            tgt = str(e["target"])
            if tgt in known:
                out["target"] = tgt
            else:
                notes.append(f"event {eid}: unknown target '{tgt}' ignored")
        for k in ("to", "center"):
            pt = _point(e.get(k))
            if pt is not None:
                ref = pt if isinstance(pt, str) else (pt.get("prop") or pt.get("actor"))
                if ref and ref not in known:
                    notes.append(f"event {eid}: '{k}' points at unknown '{ref}', ignored")
                    continue
                out[k] = pt
        if act == "color":  # the colour may come as "color", as "to", by name, or only in the label
            out.pop("to", None)
            c = _hex(e.get("color")) or _hex(e.get("to") if isinstance(e.get("to"), str) else None) or _named_in(out["label"])
            if c:
                out["color"] = c
            else:
                notes.append(f"event {eid}: a colour change with no colour; dropped")
                continue
        via = [p for p in (_point(v) for v in (e.get("via") or [])[:6]) if p is not None
               and not ((p if isinstance(p, str) else (p.get("prop") or p.get("actor") or "")) not in known and not
                        (isinstance(p, dict) and "x" in p))]
        if via:
            out["via"] = via
        for k, lo, hi in (("height", 0, 30), ("degrees", -3600, 3600), ("amount", -10, 10), ("face", -720, 720),
                          ("radius", 0.1, 60), ("altitude", 0, 60), ("arc", 0, 10), ("gap", 0.1, 5), ("to_scale", 0.01, 20)):
            if e.get(k) is not None:
                out[k] = _num(e.get(k), 0, lo, hi)
        if e.get("axis") in ("x", "y", "z"):
            out["axis"] = e["axis"]
        if _hex(e.get("color")):
            out["color"] = _hex(e.get("color"))
        for k in ("into", "receiver"):
            if e.get(k) and str(e[k]) in known:
                out[k] = str(e[k])
        events.append(out)
    events.sort(key=lambda x: (x["t0"], x["t1"]))

    # one subject's moves (and hand actions) never overlap: one that starts before the last ended waits for it
    last_end: dict[str, float] = {}
    for e in events:
        if (e["action"] not in LOCO and e["action"] not in HANDS) or e["actor"] in pids:
            continue
        prev = last_end.get(e["actor"])
        if prev is not None and e["t0"] < prev - 1e-6:
            dur = e["t1"] - e["t0"]
            notes.append(f"event {e['id']}: started before {e['actor']}'s previous move ended; moved to {prev:.2f} s")
            e["t0"], e["t1"] = round(prev, 3), round(prev + dur, 3)
        last_end[e["actor"]] = e["t1"]
    events.sort(key=lambda x: (x["t0"], x["t1"]))

    # ---- the clock
    want = seconds or _num(raw.get("duration"), 0, 0, 600) or 0
    need = max([e["t1"] for e in events] or [0]) + 0.6
    duration = _num(max(want, need), 8, MIN_DURATION, MAX_DURATION)
    if need > MAX_DURATION + 0.01:
        k = (MAX_DURATION - 0.6) / (need - 0.6)
        notes.append(f"the events needed {need:.1f} s; time was compressed to fit {MAX_DURATION:g} s")
        for e in events:
            e["t0"], e["t1"] = round(e["t0"] * k, 3), round(e["t1"] * k, 3)
    if seconds and need > seconds + 0.01 and need <= MAX_DURATION:
        notes.append(f"the events need {need:.1f} s, longer than the {seconds:g} s asked; the film is {duration:.1f} s")

    # ---- camera: contiguous shots over the whole film
    shots = []
    for s in raw.get("camera") or []:
        if not isinstance(s, dict):
            continue
        mv = str(s.get("move") or "follow").lower()
        tgt = s.get("target")
        tgts = [str(x) for x in (tgt if isinstance(tgt, list) else [tgt]) if x is not None and str(x) in known]
        out = {"t0": _num(s.get("t0"), 0, 0, duration), "t1": _num(s.get("t1"), duration, 0, duration),
               "move": mv if mv in MOVES else "follow", "why": str(s.get("why") or "")[:160]}
        if tgts:
            out["target"] = tgts if len(tgts) > 1 else tgts[0]
        for k, lo, hi in (("angle", -720, 720), ("distance", 0.3, 80), ("height", 0.05, 60), ("fov", 10, 100),
                          ("sweep", -720, 720), ("rise", -20, 30), ("to", 0.1, 5), ("fov_to", 10, 110), ("span", 0, 20)):
            if s.get(k) is not None:
                out[k] = _num(s.get(k), 0, lo, hi)
        if s.get("side") in ("left", "right"):
            out["side"] = s["side"]
        shots.append(out)
    shots = [s for s in sorted(shots, key=lambda s: s["t0"]) if s["t1"] - s["t0"] > 0.05]
    if not shots:
        first = actors[0]["id"] if actors else props[0]["id"]
        shots = [{"t0": 0, "t1": duration, "move": "follow" if actors else "orbit", "target": first, "angle": 150 if actors else 30}]
        notes.append("no camera shots were planned; one following shot covers the film")
    shots[0]["t0"] = 0.0
    for a, b in zip(shots, shots[1:]):
        b["t0"] = a["t1"] = round((a["t1"] + b["t0"]) / 2, 3) if abs(a["t1"] - b["t0"]) > 1e-6 else a["t1"]
    shots[-1]["t1"] = duration
    shots = [s for s in shots if s["t1"] - s["t0"] > 0.05]

    spec = {"title": str(raw.get("title") or "")[:120], "logline": str(raw.get("logline") or "")[:300],
            "look": str(raw.get("look") or "")[:400], "fps": fps, "duration": round(duration, 3), "width": w,
            "height": h, "aspect": aspect, "resolution": resolution, "environment": env, "actors": actors,
            "props": props, "events": events, "camera": shots}
    return spec, notes


def coverage(spec: dict, graph: dict) -> list[dict]:
    """Story checks on the spec itself: every essential graph event is staged, in an order its dependencies allow.
    Issues: {"code", "event", "message"} with engines/temporal's codes."""
    from ..temporal import graph as G
    evs = graph.get("events") or []
    if not evs:
        return []
    staged: dict[str, list[dict]] = {}
    for e in spec.get("events") or []:
        for g in e.get("graph_event") or []:
            staged.setdefault(g, []).append(e)
    issues = []
    for ge in evs:
        if ge["id"] not in staged and ge.get("essential", True):
            issues.append({"code": G.MISSING_EVENT, "event": ge["id"],
                           "message": f"{G.MISSING_EVENT}: {ge['id']} ({ge['description'][:120]}) is not staged"})
    first = {g: min(e["t0"] for e in es) for g, es in staged.items()}
    for ge in evs:
        for req in ge.get("requires") or []:
            if ge["id"] in first and req in first and first[ge["id"]] + 1e-6 < first[req]:
                issues.append({"code": G.WRONG_EVENT_ORDER, "event": ge["id"],
                               "message": f"{G.WRONG_EVENT_ORDER}: {ge['id']} starts at {first[ge['id']]:.1f} s, "
                                          f"before {req} which it needs ({first[req]:.1f} s)"})
    return issues
