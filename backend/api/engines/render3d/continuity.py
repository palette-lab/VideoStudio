"""Temporal and visual continuity checks.

Before the transformation, the 3D reference itself is checked from its per-frame metadata (ground truth, no guessing):
nobody moves faster than their body allows (teleport), the camera does not jump inside a shot, every event's subject is
on screen while it happens, interactions really bring their subjects together, and every essential event of the story
is staged in an order its dependencies allow. A vision model also looks at one still per event.

After the transformation, the realistic video is compared with the reference frame by frame: the same composition
(edge-map correlation at the same timestamps), the same motion over time (correlation of frame-difference curves),
the same duration / frame rate / size, and a vision model compares reference and output stills of every event
(the event happens, in the same place, with the same characters). A successful API response is never taken as proof;
what cannot be checked automatically is marked UNCERTAIN for the user to review.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
from pathlib import Path

from router_core import config, llm

from ..temporal import graph as G

log = logging.getLogger("palette_video.render3d")

CAMERA_DISCONTINUITY = "CAMERA_DISCONTINUITY"
OFFSCREEN = "OFFSCREEN_ACTION"
MISSING_INTERACTION = "MISSING_INTERACTION"
SEVERE = G.SEVERE | {MISSING_INTERACTION, G.POSITION_DISCONTINUITY}
INTERACT = {"meet", "greet", "hug", "shake_hands", "give", "talk"}
SAMPLE_FPS = 4
SMALL = (96, 54)


# ------------------------------------------------------------------------------------------------ the 3D reference
def _vmax(spec: dict, aid: str) -> float:
    a = next((x for x in spec.get("actors") or [] if x["id"] == aid), None)
    if not a:
        return 40.0
    if a["kind"] == "bird":
        return 30.0
    if a["kind"] == "quadruped":
        return max(6.0, 25.0 * float(a.get("height") or 0.5))
    return 10.0


def reference_issues(spec: dict, meta: dict, graph: dict | None = None) -> list[dict]:
    """Issues {"code", "severity", "event", "t", "message"} found in the rendered 3D reference."""
    from .spec import coverage
    fps = float(meta.get("fps") or spec.get("fps") or 24)
    frames = meta.get("frames_meta") or []
    out: list[dict] = []
    # teleports: per-frame displacement beyond what the body can do (vertical jumps allowed for jumps/flights)
    for aid in {k for f in frames for k in (f.get("actors") or {})}:
        lim = _vmax(spec, aid) / fps * 1.6 + 0.02
        prev = None
        for f in frames:
            a = (f.get("actors") or {}).get(aid)
            if not a:
                continue
            p = a["pos"]
            if prev is not None:
                d = math.dist((p[0], p[2]), (prev[0], prev[2]))
                if d > lim:
                    out.append({"code": G.POSITION_DISCONTINUITY, "severity": "major", "event": None, "t": f["t"],
                                "message": f"{G.POSITION_DISCONTINUITY}: {aid} jumps {d:.2f} m in one frame at {f['t']:.2f} s"})
                    break
            prev = p
    # camera jumps inside one shot (a cut between shots is planned and allowed)
    by_shot: dict[int, list[dict]] = {}
    for f in frames:
        by_shot.setdefault(f.get("shot", 0), []).append(f)
    for sh, fs in by_shot.items():
        steps = [math.dist(a["camera"]["pos"], b["camera"]["pos"]) for a, b in zip(fs, fs[1:])]
        if len(steps) < 3:
            continue
        med = sorted(steps)[len(steps) // 2]
        for k, s in enumerate(steps):
            if s > max(0.35, 6 * med):
                out.append({"code": CAMERA_DISCONTINUITY, "severity": "major", "event": None, "t": fs[k + 1]["t"],
                            "message": f"{CAMERA_DISCONTINUITY}: the camera jumps {s:.2f} m inside shot {sh + 1} at "
                                       f"{fs[k + 1]['t']:.2f} s"})
                break
    # every event's subject on screen while it happens; interactions bring their subjects together
    for e in spec.get("events") or []:
        span = [f for f in frames if e["t0"] - 1e-6 <= f["t"] <= e["t1"] + 1e-6]
        if not span:
            continue
        subj = e["actor"]
        seen = [((f.get("actors") or {}).get(subj) or (f.get("props") or {}).get(subj) or {}) for f in span]
        vis = sum(1 for s in seen if (s.get("visible") or 0) >= 0.35 and (s.get("area") or 0) >= 0.0004
                  and not s.get("occluded")) / len(span)  # hidden behind a tree or a wall is not on screen
        if vis < 0.6:
            out.append({"code": OFFSCREEN, "severity": "major", "event": e["id"], "t": e["t0"],
                        "message": f"{OFFSCREEN}: {e['label']} ({e['t0']:.1f}-{e['t1']:.1f} s) is on screen only "
                                   f"{vis * 100:.0f}% of its time"})
        other = e.get("target") if e["action"] in INTERACT else e.get("receiver")
        if e["action"] in INTERACT and other:
            last = span[-1]
            a, b = (last.get("actors") or {}).get(subj), (last.get("actors") or {}).get(other)
            if a and b and math.dist((a["pos"][0], a["pos"][2]), (b["pos"][0], b["pos"][2])) > 2.5:
                out.append({"code": MISSING_INTERACTION, "severity": "major", "event": e["id"], "t": e["t1"],
                            "message": f"{MISSING_INTERACTION}: {e['label']} - {subj} and {other} are "
                                       f"{math.dist((a['pos'][0], a['pos'][2]), (b['pos'][0], b['pos'][2])):.1f} m apart"})
        if e["action"] in ("pick_up", "take") and e.get("target"):
            mid = span[len(span) // 2]
            a, p = (mid.get("actors") or {}).get(subj), (mid.get("props") or {}).get(e["target"])
            if a and p and math.dist((a["pos"][0], a["pos"][2]), (p["pos"][0], p["pos"][2])) > 1.4:
                out.append({"code": G.INCOMPLETE_ACTION, "severity": "major", "event": e["id"], "t": e["t0"],
                            "message": f"{G.INCOMPLETE_ACTION}: {subj} reaches for {e['target']} from "
                                       f"{math.dist((a['pos'][0], a['pos'][2]), (p['pos'][0], p['pos'][2])):.1f} m away"})
    for i in coverage(spec, graph or {}):
        out.append({**i, "severity": "major", "t": None})
    return out


REF_REVIEW = """These are stills from an UNTEXTURED 3D animation (simple shapes and mannequins - that is intended; a
video model will make it photoreal later). One still per planned event, in order:
{EVENTS}
For each still: does it clearly show that event (the right subject doing that action, visible in frame)? Report only
real staging problems: a subject missing or out of frame, a body inside an object or a wall, an object floating or
missing, the wrong subject, an action that cannot be read. Ignore the plain look.
JSON: {"events": [{"n": 1, "ok": true, "problem": ""}], "overall": ""}"""


async def vision_reference(stills: list[Path], events: list[dict]) -> list[dict]:
    """A vision model's look at one still per event (best effort: [] when it cannot run)."""
    if not stills:
        return []
    lines = "\n".join(f"{k}. {e['t0']:.1f}-{e['t1']:.1f} s: {e['label']}" for k, e in enumerate(events, 1))
    try:
        data = await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": REF_REVIEW.replace("{EVENTS}", lines)}, *[llm.image_part(p) for p in stills]]}],
            max_tokens=2500, reasoning_effort="low", stage="engine.render3d.reference_check")
    except llm.LLMError as e:
        log.warning("render3d reference vision check failed: %s", e)
        return []
    out = []
    for r in data.get("events") or []:
        try:
            k = int(r.get("n")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= k < len(events) and r.get("ok") is False and str(r.get("problem") or "").strip():
            e = events[k]
            out.append({"code": G.ACTION_SKIPPED, "severity": "minor", "event": e["id"], "t": e["t0"],
                        "message": f"vision check, {e['label']}: {str(r['problem'])[:240]}"})
    return out


# ------------------------------------------------------------------------------------------------ frame analysis
def gray_frames(path: str, size: tuple[int, int] = SMALL, fps: int = SAMPLE_FPS, duration: float | None = None) -> list[list[int]]:
    """Grayscale thumbnails sampled at a fixed rate from t=0 (the same timestamps for every video)."""
    w, h = size
    args = ["ffmpeg", "-v", "error", "-i", path]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    r = subprocess.run(args, capture_output=True, timeout=600)
    raw = r.stdout
    n = len(raw) // (w * h)
    return [list(raw[i * w * h:(i + 1) * w * h]) for i in range(n)]


def edges(img: list[int], size: tuple[int, int] = SMALL) -> list[float]:
    w, h = size
    out = [0.0] * (w * h)
    for y in range(1, h - 1):
        row = y * w
        for x in range(1, w - 1):
            i = row + x
            gx = img[i + 1] - img[i - 1]
            gy = img[i + w] - img[i - w]
            out[i] = math.sqrt(gx * gx + gy * gy)
    return out


def blur(img: list[float], size: tuple[int, int] = SMALL, r: int = 2) -> list[float]:
    """Box blur, so edges a few pixels apart still match (the model redraws details, not the layout)."""
    w, h = size
    tmp = [0.0] * (w * h)
    for y in range(h):
        for x in range(w):
            lo, hi = max(0, x - r), min(w, x + r + 1)
            tmp[y * w + x] = sum(img[y * w + lo:y * w + hi]) / (hi - lo)
    out = [0.0] * (w * h)
    for y in range(h):
        lo, hi = max(0, y - r), min(h, y + r + 1)
        for x in range(w):
            out[y * w + x] = sum(tmp[yy * w + x] for yy in range(lo, hi)) / (hi - lo)
    return out


def ncc(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    ma, mb = sum(a[:n]) / n, sum(b[:n]) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((b[i] - mb) ** 2 for i in range(n)))
    return num / (da * db) if da > 1e-9 and db > 1e-9 else 0.0


def motion_curve(frames: list[list[int]]) -> list[float]:
    return [sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a)) for a, b in zip(frames, frames[1:])]


def pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return ncc(a[:n], b[:n]) if n >= 3 else 0.0


def frame_similarity(ref: str, out: str, duration: float) -> dict:
    """Per-sample layout similarity (blurred edge maps) and the motion-timing correlation."""
    rf, of = gray_frames(ref, duration=duration), gray_frames(out, duration=duration)
    n = min(len(rf), len(of))
    if n == 0:
        return {"samples": 0, "layout": [], "layout_mean": 0.0, "motion_corr": 0.0, "fps": SAMPLE_FPS}
    layout = [round(ncc(blur(edges(rf[i])), blur(edges(of[i]))), 3) for i in range(n)]
    lum = [round(ncc(blur([float(v) for v in rf[i]], r=4), blur([float(v) for v in of[i]], r=4)), 3) for i in range(n)]
    mc = pearson(motion_curve(rf[:n]), motion_curve(of[:n]))
    return {"samples": n, "fps": SAMPLE_FPS, "layout": layout, "layout_mean": round(sum(layout) / n, 3),
            "luminance": lum, "luminance_mean": round(sum(lum) / n, 3), "motion_corr": round(mc, 3)}


def still(path: str, t: float, dest: Path, width: int = 640) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", path, "-frames:v", "1",
                        "-vf", f"scale={width}:-2", str(dest)], capture_output=True, timeout=120)
    return dest if r.returncode == 0 and dest.exists() else None


# ------------------------------------------------------------------------------------------------ the realistic video
COMPARE = """Pairs of frames at the same timestamps. In each pair the FIRST image is the untextured 3D reference (the
intended action, positions and camera) and the SECOND is the photoreal video made from it. Events, in order:
{EVENTS}
For each pair, judge the SECOND image against the first and the event:
- shows_event: does the photoreal frame show this event happening (same subject, same action)?
- same_layout: are the subjects and objects in the same places in the frame, with the same camera view?
- problems: any of {CODES}
Then across all the photoreal frames: is every character the same individual throughout (face, hair, clothes / fur,
colours), and is every object the same object?
JSON: {"pairs": [{"n": 1, "shows_event": "yes|no|unsure", "same_layout": "yes|no|unsure", "problems": [], "note": ""}],
       "same_characters": "yes|no|unsure", "same_objects": "yes|no|unsure", "note": ""}"""


async def vision_compare(pairs: list[tuple[Path, Path]], events: list[dict]) -> dict | None:
    if not pairs:
        return None
    lines = "\n".join(f"{k}. at {(e['t0'] + e['t1']) / 2:.1f} s: {e['label']}" for k, e in enumerate(events, 1))
    content = [{"type": "text", "text": COMPARE.replace("{EVENTS}", lines).replace("{CODES}", ", ".join(
        [*G.CODES, CAMERA_DISCONTINUITY, MISSING_INTERACTION]))}]
    for a, b in pairs:
        content += [llm.image_part(a), llm.image_part(b)]
    try:
        return await llm.chat_json(config.VISION_MODEL, [{"role": "user", "content": content}], max_tokens=3000,
                                   reasoning_effort="low", stage="engine.render3d.compare")
    except llm.LLMError as e:
        log.warning("render3d comparison vision check failed: %s", e)
        return None


def pick_events(events: list[dict], k: int = 8) -> list[dict]:
    """At most k events, spread over the film (the vision call stays small)."""
    if len(events) <= k:
        return list(events)
    step = (len(events) - 1) / (k - 1)
    return [events[round(i * step)] for i in range(k)]


def technical(ref: dict, out: dict, want: dict) -> list[dict]:
    """Size, rate, length and sound of the final video against what was asked."""
    issues = []
    if abs(out["duration"] - ref["duration"]) > max(0.25, 0.04 * ref["duration"]):
        issues.append({"code": "DURATION", "severity": "major",
                       "message": f"the video is {out['duration']:.2f} s, the 3D reference {ref['duration']:.2f} s"})
    if out["fps"] and abs(out["fps"] - want["fps"]) > 0.05:
        issues.append({"code": "FRAME_RATE", "severity": "minor", "message": f"{out['fps']:g} fps instead of {want['fps']}"})
    if out["width"] and out["height"]:
        ar_out, ar_want = out["width"] / out["height"], want["width"] / want["height"]
        if abs(ar_out - ar_want) > 0.02:
            issues.append({"code": "ASPECT", "severity": "major",
                           "message": f"{out['width']}x{out['height']} is not the {want['aspect']} frame"})
    if not out["audio"]:
        issues.append({"code": "NO_AUDIO", "severity": "minor", "message": "the video has no sound"})
    return issues


async def compare(ref_rgb: str, out_path: str, spec: dict, work: Path, essential: set[str] | None = None) -> dict:
    """The realistic video against its 3D reference. {"status": PASS|FAIL|UNCERTAIN, "events": [...], ...}."""
    from .renderer import probe
    pr, po = probe(ref_rgb), probe(out_path)
    want = {"fps": spec["fps"], "width": spec["width"], "height": spec["height"], "aspect": spec.get("aspect", "16:9")}
    tech = technical(pr, po, want)
    import asyncio
    sim = await asyncio.to_thread(frame_similarity, ref_rgb, out_path, min(pr["duration"], po["duration"]) or pr["duration"])
    evs = pick_events(spec.get("events") or [])
    pairs, rows = [], []
    for k, e in enumerate(evs):
        t = (e["t0"] + e["t1"]) / 2
        a = still(ref_rgb, t, work / f"cmp_ref_{k}.jpg")
        b = still(out_path, t, work / f"cmp_out_{k}.jpg")
        if a and b:
            pairs.append((a, b))
        i = min(sim["samples"] - 1, max(0, round(t * SAMPLE_FPS))) if sim["samples"] else -1
        rows.append({"id": e["id"], "label": e["label"], "t": round(t, 2), "actor": e["actor"], "action": e["action"],
                     "graph_event": e.get("graph_event") or [], "layout": sim["layout"][i] if i >= 0 else None,
                     "ref_still": str(a) if a else None, "out_still": str(b) if b else None})
    vis = await vision_compare(pairs, evs) if len(pairs) == len(evs) else None
    by_n = {}
    for r in (vis or {}).get("pairs") or []:
        try:
            by_n[int(r.get("n")) - 1] = r
        except (TypeError, ValueError):
            pass
    issues = list(tech)
    for k, row in enumerate(rows):
        v = by_n.get(k)
        codes = sorted({c for c in (v or {}).get("problems") or [] if c in (*G.CODES, CAMERA_DISCONTINUITY, MISSING_INTERACTION)})
        if v and v.get("shows_event") == "no":
            codes = sorted(set(codes) | {G.MISSING_EVENT})
        if v and v.get("same_layout") == "no" and (row["layout"] or 0) < 0.25:
            codes = sorted(set(codes) | {G.POSITION_DISCONTINUITY})
        if v is None:
            row["verdict"] = {"status": "UNCERTAIN", "codes": [], "note": "could not be checked automatically - watch it"}
        elif v.get("shows_event") == "unsure" and not codes:
            row["verdict"] = {"status": "UNCERTAIN", "codes": [], "note": str(v.get("note") or "")[:200]}
        else:
            row["verdict"] = {"status": "FAIL" if set(codes) & SEVERE else G.PASS, "codes": codes,
                              "note": str((v or {}).get("note") or "")[:200]}
        for c in codes:
            issues.append({"code": c, "severity": "major" if c in SEVERE else "minor", "event": row["id"],
                           "message": f"{c}: {row['label']} at {row['t']:.1f} s" + (f" ({v.get('note')})" if v and v.get("note") else "")})
    if vis and vis.get("same_characters") == "no":
        issues.append({"code": G.CHARACTER_INCONSISTENCY, "severity": "major", "message": "a character changes look during the film" + (f": {vis.get('note')}" if vis.get("note") else "")})
    if vis and vis.get("same_objects") == "no":
        issues.append({"code": G.OBJECT_INCONSISTENCY, "severity": "minor", "message": "an object changes during the film"})
    if sim["samples"] and sim["layout_mean"] < 0.15:
        issues.append({"code": G.POSITION_DISCONTINUITY, "severity": "major",
                       "message": f"the composition differs from the 3D reference (edge similarity {sim['layout_mean']:.2f})"})
    if sim["samples"] > 4 and sim["motion_corr"] < 0.15:
        issues.append({"code": "MOTION_TIMING", "severity": "minor",
                       "message": f"the motion over time differs from the 3D reference (correlation {sim['motion_corr']:.2f})"})
    essential = essential or {e["id"] for e in evs}
    failed = [r for r in rows if r["verdict"]["status"] == "FAIL" and r["id"] in essential]
    unsure = [r for r in rows if r["verdict"]["status"] == "UNCERTAIN"]
    major = [i for i in issues if i.get("severity") == "major" and i.get("code") not in ("DURATION",)]
    status = "FAIL" if failed or any(i["code"] == G.CHARACTER_INCONSISTENCY for i in major) else \
        "UNCERTAIN" if unsure or major or vis is None else G.PASS
    return {"status": status, "events": rows, "issues": issues, "similarity": sim, "reference": pr, "output": po,
            "vision": bool(vis), "same_characters": (vis or {}).get("same_characters"),
            "failed_events": [r["id"] for r in failed]}


def score(report: dict) -> float:
    """Higher is better: passed events, then layout similarity (to keep the better of two takes)."""
    ev = report.get("events") or []
    ok = sum(1 for r in ev if r["verdict"]["status"] == G.PASS) - 2 * sum(1 for r in ev if r["verdict"]["status"] == "FAIL")
    return ok + (report.get("similarity") or {}).get("layout_mean", 0)


def dump(report: dict, path: Path) -> str:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return str(path)
