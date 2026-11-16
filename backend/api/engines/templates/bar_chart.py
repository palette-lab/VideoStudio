"""Tested HyperFrames bar-chart template: the LLM only supplies data; the layout is fixed and checked.

Vertical bars for up to 6 short categories, horizontal bars otherwise. Everything sits inside a 7% safe area: title
and subtitle on top, bars in the middle with values above (or beside) them, category labels under (or before) each bar,
an optional highlight tag, and an optional end card with a kinetic line. Timing scales with the requested length.
"""

import html
import json

PALETTES = {
    "dark": {"bg": "#0f1420", "ink": "#f4f6fb", "muted": "#9aa4b8", "grid": "rgba(255,255,255,.08)",
             "bar": ["#5b8cff", "#3fc9a8", "#f2a64a", "#a57bf5", "#ef6f8a", "#53c2e8", "#c6d36b", "#f08a5d"],
             "accent": "#ffb547", "card": "#0b0f18"},
    "light": {"bg": "#f6f8fc", "ink": "#121826", "muted": "#5b6577", "grid": "rgba(18,24,38,.08)",
              "bar": ["#2f6bff", "#14a586", "#e8892c", "#7d55e0", "#d9466a", "#1fa2cf", "#8fa22f", "#d8663a"],
              "accent": "#2f6bff", "card": "#111827"},
}


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def render(spec: dict, width: int, height: int, duration: float) -> str:
    cats = [str(c) for c in spec.get("categories") or []][:12]
    vals = [float(v) for v in (spec.get("values") or [])][: len(cats)]
    if not cats or len(vals) != len(cats):
        raise ValueError("chart spec needs categories and the same number of values")
    pal = PALETTES.get(spec.get("theme") or "dark", PALETTES["dark"])
    title = html.escape(str(spec.get("title") or ""))
    subtitle = html.escape(str(spec.get("subtitle") or ""))
    unit = html.escape(str(spec.get("unit") or ""))
    hi = spec.get("highlight")
    hi = hi if isinstance(hi, int) and 0 <= hi < len(cats) else None
    tag = html.escape(str(spec.get("highlight_label") or ""))
    end = spec.get("end_line") or {}
    raw_text, raw_accent = str(end.get("text") or "").strip(), str(end.get("accent") or "").strip()
    if raw_accent and raw_accent in raw_text:  # "Tokyo leads +18%" + accent "+18%": show the accent once
        raw_text = raw_text.replace(raw_accent, "").strip()
    end_text, end_accent = html.escape(raw_text), html.escape(raw_accent)
    horizontal = len(cats) > 6 or max(len(c) for c in cats) > 12 or width < height * 0.8 and len(cats) > 4
    vmax = max(vals) or 1.0
    s = min(width, height) / 1080  # type scale
    pad_x, pad_y = round(width * 0.07), round(height * 0.07)
    title_px, sub_px, label_px, value_px = round(64 * s), round(28 * s), round(30 * s), round(34 * s)
    top = pad_y + title_px + (sub_px + round(16 * s) if subtitle else 0) + round(48 * s)
    chart_h = height - top - pad_y - (label_px + round(28 * s) if not horizontal else 0)
    chart_w = width - 2 * pad_x
    # Timing (seconds): title, bars, hold, end card.
    has_end = bool(end_text)
    t_bars = 0.6
    t_end = duration * (0.68 if has_end else 1.0)
    grow = max(0.6, min(1.6, (t_end - t_bars) * 0.35))
    stagger = max(0.08, min(0.25, (t_end - t_bars - grow) * 0.15 / max(1, len(cats) - 1)))

    bars_html, tl = [], []
    if not horizontal:
        n = len(cats)
        slot = chart_w / n
        bw = min(slot * 0.62, 220 * s)
        for i, (c, v) in enumerate(zip(cats, vals)):
            h = max(4.0, (chart_h - value_px - round(20 * s)) * v / vmax)
            x = pad_x + slot * i + (slot - bw) / 2
            color = pal["accent"] if i == hi else pal["bar"][i % len(pal["bar"])]
            bars_html.append(
                f'<div class="bar" id="bar{i}" style="left:{x:.1f}px;bottom:{height - top - chart_h:.1f}px;width:{bw:.1f}px;'
                f'height:{h:.1f}px;background:{color}"></div>'
                f'<div class="val" id="val{i}" style="left:{x - 40 * s:.1f}px;width:{bw + 80 * s:.1f}px;'
                f'bottom:{height - top - chart_h + h + 10 * s:.1f}px;color:{color if i == hi else pal["ink"]}">'
                f'{_fmt(v)}</div>'
                f'<div class="cat" style="left:{x - 40 * s:.1f}px;width:{bw + 80 * s:.1f}px;'
                f'top:{top + chart_h + 14 * s:.1f}px">{html.escape(c)}</div>')
            start = t_bars + i * stagger
            tl.append(f'tl.fromTo("#bar{i}",{{scaleY:0}},{{scaleY:1,duration:{grow:.2f},ease:"power3.out"}},{start:.2f});')
            tl.append(f'tl.fromTo("#val{i}",{{opacity:0,y:{10 * s:.0f}}},{{opacity:1,y:0,duration:0.4}},{start + grow * 0.7:.2f});')
        if hi is not None and tag:
            x = pad_x + slot * hi + slot / 2
            h = max(4.0, (chart_h - value_px - round(20 * s)) * vals[hi] / vmax)
            tag_bottom = height - top - chart_h + h + value_px + 26 * s
            bars_html.append(f'<div class="tag" id="tag" style="left:{x:.1f}px;bottom:{tag_bottom:.1f}px">{tag}</div>')
            tl.append(f'tl.fromTo("#tag",{{opacity:0,scale:0.8}},{{opacity:1,scale:1,duration:0.4,ease:"back.out(2)"}},'
                      f'{t_bars + (len(cats) - 1) * stagger + grow:.2f});')
        axis = f'<div class="axis" style="left:{pad_x}px;right:{pad_x}px;top:{top + chart_h:.1f}px"></div>'
    else:
        n = len(cats)
        label_w = min(chart_w * 0.32, max(len(c) for c in cats) * label_px * 0.6 + 20 * s)
        track_w = chart_w - label_w - value_px * 3.2
        row = chart_h / n
        bh = min(row * 0.62, 90 * s)
        for i, (c, v) in enumerate(zip(cats, vals)):
            y = top + row * i + (row - bh) / 2
            w = max(4.0, track_w * v / vmax)
            color = pal["accent"] if i == hi else pal["bar"][i % len(pal["bar"])]
            bars_html.append(
                f'<div class="cat h" style="left:{pad_x}px;width:{label_w - 16 * s:.1f}px;top:{y:.1f}px;'
                f'height:{bh:.1f}px;line-height:{bh:.1f}px">{html.escape(c)}</div>'
                f'<div class="bar h" id="bar{i}" style="left:{pad_x + label_w:.1f}px;top:{y:.1f}px;width:{w:.1f}px;'
                f'height:{bh:.1f}px;background:{color}"></div>'
                f'<div class="val h" id="val{i}" style="left:{pad_x + label_w + w + 14 * s:.1f}px;top:{y:.1f}px;'
                f'height:{bh:.1f}px;line-height:{bh:.1f}px;color:{color if i == hi else pal["ink"]}">{_fmt(v)}</div>')
            start = t_bars + i * stagger
            tl.append(f'tl.fromTo("#bar{i}",{{scaleX:0}},{{scaleX:1,duration:{grow:.2f},ease:"power3.out"}},{start:.2f});')
            tl.append(f'tl.fromTo("#val{i}",{{opacity:0,x:{-10 * s:.0f}}},{{opacity:1,x:0,duration:0.4}},{start + grow * 0.7:.2f});')
        axis = f'<div class="axis v" style="left:{pad_x + label_w:.1f}px;top:{top:.1f}px;height:{chart_h:.1f}px"></div>'

    end_html = ""
    if has_end:
        end_html = (f'<div id="endcard"><div class="end-line"><span id="endtxt">{end_text}</span>'
                    + (f' <span id="endacc" style="color:{pal["accent"]}">{end_accent}</span>' if end_accent else "")
                    + "</div></div>")
        tl.append(f'tl.fromTo("#chart",{{opacity:1}},{{opacity:0.12,duration:0.5}},{t_end:.2f});')
        tl.append(f'tl.fromTo("#endcard",{{opacity:0}},{{opacity:1,duration:0.4}},{t_end:.2f});')
        tl.append(f'tl.fromTo("#endtxt",{{opacity:0,y:{30 * s:.0f}}},{{opacity:1,y:0,duration:0.6,ease:"power3.out"}},{t_end + 0.2:.2f});')
        if end_accent:
            tl.append(f'tl.fromTo("#endacc",{{opacity:0,scale:0.6}},{{opacity:1,scale:1,duration:0.5,ease:"back.out(2.2)"}},{t_end + 0.6:.2f});')
    tl.append(f'tl.to({{}},{{duration:0.01}},{max(0.0, duration - 0.01):.2f});')

    unit_html = f'<div class="unit" style="left:{pad_x}px;top:{top - round(40 * s)}px">{unit}</div>' if unit else ""
    css = f"""html,body{{margin:0;padding:0;width:{width}px;height:{height}px;overflow:hidden;background:{pal['bg']}}}
#root{{position:relative;width:{width}px;height:{height}px;overflow:hidden;background:{pal['bg']};
font-family:"Helvetica Neue",Arial,sans-serif;color:{pal['ink']}}}
#chart{{position:absolute;left:0;top:0;width:{width}px;height:{height}px}}
#head{{position:absolute;left:{pad_x}px;right:{pad_x}px;top:{pad_y}px;text-align:center}}
#title{{font-size:{title_px}px;font-weight:800;letter-spacing:-.01em;line-height:1.1}}
#subtitle{{margin-top:{round(12 * s)}px;font-size:{sub_px}px;color:{pal['muted']}}}
.unit{{position:absolute;font-size:{round(22 * s)}px;color:{pal['muted']};letter-spacing:.08em;text-transform:uppercase}}
.bar{{position:absolute;border-radius:{round(10 * s)}px {round(10 * s)}px 0 0;transform-origin:bottom center}}
.bar.h{{border-radius:0 {round(10 * s)}px {round(10 * s)}px 0;transform-origin:left center}}
.val{{position:absolute;text-align:center;font-size:{value_px}px;font-weight:800}}
.val.h{{text-align:left;width:auto}}
.cat{{position:absolute;text-align:center;font-size:{label_px}px;color:{pal['ink']};font-weight:600;white-space:nowrap}}
.cat.h{{text-align:right;overflow:hidden;text-overflow:ellipsis}}
.axis{{position:absolute;height:2px;background:{pal['muted']}}}
.axis.v{{width:2px;height:auto}}
.tag{{position:absolute;transform:translateX(-50%);background:{pal['accent']};color:#111;font-weight:800;
font-size:{round(22 * s)}px;padding:{round(6 * s)}px {round(14 * s)}px;border-radius:999px;white-space:nowrap}}
#endcard{{position:absolute;left:0;top:0;width:{width}px;height:{height}px;box-sizing:border-box;display:flex;align-items:center;justify-content:center;padding:0 {pad_x}px;opacity:0}}
.end-line{{font-size:{round(84 * s)}px;font-weight:800;text-align:center;line-height:1.15;text-shadow:0 2px 24px rgba(0,0,0,.35)}}"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{duration:g}" data-width="{width}" data-height="{height}" data-fps="30">
<div id="chart" data-layout-allow-occlusion="true">
<div id="head"><div id="title">{title}</div>{f'<div id="subtitle">{subtitle}</div>' if subtitle else ''}</div>
{unit_html}{axis}{''.join(bars_html)}
</div>{end_html}
</div>
<script src="assets/gsap.min.js"></script>
<script>
const tl = gsap.timeline({{paused:true}});
tl.fromTo("#head",{{opacity:0,y:{-20 * s:.0f}}},{{opacity:1,y:0,duration:0.6,ease:"power3.out"}},0);
{chr(10).join(tl)}
window.__timelines = window.__timelines || {{}}; window.__timelines["main"] = tl;
</script></body></html>"""


SPEC_PROMPT = """Extract the chart the user asked for as JSON. Use ONLY numbers the user gave; never invent data.
Return: {"title": "...", "subtitle": "...", "unit": "e.g. $M or %", "categories": ["..."], "values": [numbers],
"highlight": <index of the bar to emphasise, or null>, "highlight_label": "short tag, e.g. Top region",
"end_line": {"text": "closing line WITHOUT the accent, e.g. Tokyo leads", "accent": "the emphasised part, e.g. +18%"}, "theme": "dark" | "light"}
If the request has no numbers for a chart, return {"categories": [], "values": []}."""


def spec_json_schema_example() -> str:
    return json.dumps({"title": "Q3 Revenue", "categories": ["Seoul", "Tokyo"], "values": [38, 47]})
