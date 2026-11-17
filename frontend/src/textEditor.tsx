import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { api } from "./api"
import { useT } from "./i18n"

/* Editable text layer over the clean (text-free) video. The words are drawn here with the server's own CSS
   (layer.css) and the same enter / exit motion as the render (engines/text_layer.py), so what the user edits is what
   the downloaded video shows. Click a text to retype it; drag its handle to move it. */

export type TextItem = {
  id: string; kind: string; text: string; start: number; end: number; x: number; y: number; w: number
  size: number; color: string; style: "3d" | "flat"; font: string; anim: "pop" | "rise" | "fade"; bg: string
  weight: number
}
export type TextLayer = { width: number; height: number; duration: number; items: TextItem[]; css: string
  fonts: Record<string, string> }

const ENTER = 0.35, EXIT = 0.25
const SWATCHES = ["#FFFFFF", "#111111", "#FFD84D", "#FF5A36", "#2BC8F2", "#6C3FD1"]

function shade(c: string, f = 0.45) {
  const m = /^#([0-9a-f]{6})$/i.exec(c || "")
  if (!m) return "#444444"
  const n = parseInt(m[1], 16)
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.floor(v * f).toString(16).padStart(2, "0"))
  return `#${ch.join("")}`.toUpperCase()
}

/** Where an item is at time t: hidden, or its opacity / scale / lift (the render's GSAP tweens, power2.out in). */
function pose(it: TextItem, t: number, h: number) {
  if (t < it.start || t >= it.end) return null
  const p = Math.min(1, (t - it.start) / ENTER)
  const e = 1 - Math.pow(1 - p, 3)
  const outAt = Math.max(it.start + ENTER, it.end - EXIT)
  const fade = t > outAt ? Math.max(0, 1 - (t - outAt) / EXIT) : 1
  return { opacity: e * fade, scale: it.anim === "pop" ? 0.8 + 0.2 * e : 1, dy: it.anim === "rise" ? (1 - e) * h * 0.04 : 0 }
}

const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`
const r2 = (v: number) => Math.round(v * 100) / 100

function srt(items: TextItem[]) {
  const ts = (t: number) => {
    const ms = Math.round(Math.max(0, t) * 1000)
    const p = (n: number, l = 2) => String(n).padStart(l, "0")
    return `${p(Math.floor(ms / 3600000))}:${p(Math.floor(ms / 60000) % 60)}:${p(Math.floor(ms / 1000) % 60)},${p(ms % 1000, 3)}`
  }
  const caps = items.filter((i) => i.kind === "caption")
  return (caps.length ? caps : items).slice().sort((a, b) => a.start - b.start)
    .map((i, n) => `${n + 1}\n${ts(i.start)} --> ${ts(i.end)}\n${i.text}\n`).join("\n")
}

function save(blobOrUrl: Blob | string, name: string) {
  const url = typeof blobOrUrl === "string" ? blobOrUrl : URL.createObjectURL(blobOrUrl)
  const a = document.createElement("a")
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  if (typeof blobOrUrl !== "string") setTimeout(() => URL.revokeObjectURL(url), 60000)
}

export function EditableVideo({ runId, stepId, layer, finalUrl, busy, onSave, fill }: {
  runId: string; stepId: string; layer: TextLayer; finalUrl: string | null; busy: boolean
  onSave: (items: TextItem[]) => void; fill?: boolean
}) {
  const { t } = useT()
  const [clean, setClean] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [items, setItems] = useState<TextItem[]>(layer.items)
  const [sel, setSel] = useState<string | null>(null)
  const [rev, setRev] = useState(0)  // remounts an edited text so the browser's editing never fights React
  const [now, setNow] = useState(0)
  const [h, setH] = useState(400)
  const video = useRef<HTMLVideoElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const dirty = useMemo(() => JSON.stringify(items) !== JSON.stringify(layer.items), [items, layer.items])

  // The server's saved layer wins when it changes (after a save), unless the user is editing.
  useEffect(() => { if (!dirty) setItems(layer.items) }, [layer.items])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let u: string | null = null, alive = true
    api.cleanUrl(runId, stepId).then((x) => { u = x; if (alive) setClean(x); else URL.revokeObjectURL(x) })
      .catch((e) => alive && setErr(e.message))
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [runId, stepId])
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setH(el.clientHeight || 400))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  // Follow the video frame by frame while it plays; on pause / seek once.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      const v = video.current
      if (v) setNow(v.currentTime)
      if (v && !v.paused) raf = requestAnimationFrame(tick)
    }
    const v = video.current
    const on = () => { cancelAnimationFrame(raf); tick() }
    v?.addEventListener("play", on); v?.addEventListener("seeked", on); v?.addEventListener("pause", on)
    return () => { cancelAnimationFrame(raf); v?.removeEventListener("play", on); v?.removeEventListener("seeked", on); v?.removeEventListener("pause", on) }
  }, [clean])

  const update = useCallback((id: string, patch: Partial<TextItem>) =>
    setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...patch } : x))), [])
  const current = items.find((x) => x.id === sel) || null

  const pick = (id: string) => {
    video.current?.pause()
    setSel(id)
  }
  const drag = (e: React.PointerEvent, it: TextItem) => {
    e.preventDefault(); e.stopPropagation()
    const box = wrap.current?.getBoundingClientRect()
    if (!box) return
    const move = (ev: PointerEvent) => update(it.id, {
      x: r2(Math.min(100, Math.max(0, ((ev.clientX - box.left) / box.width) * 100))),
      y: r2(Math.min(100, Math.max(0, ((ev.clientY - box.top) / box.height) * 100))),
    })
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up) }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
  }
  const add = () => {
    const at = video.current?.currentTime || 0
    const id = `u${Date.now().toString(36)}`
    const it: TextItem = { id, kind: "keyword", text: t("edit.newText"), start: r2(at), end: r2(Math.min(layer.duration, at + 2.5)),
      x: 50, y: 30, w: 80, size: 7, color: "#FFFFFF", style: "3d", font: "display", anim: "pop", bg: "", weight: 800 }
    setItems((xs) => [...xs, it])
    setSel(id)
  }

  if (err) return <p className="vr-bad sm">{err}</p>
  return (
    <div className={`vr-tl ${fill ? "fill" : ""}`}>
      <style>{layer.css}</style>
      <div ref={wrap} className="vr-tl-wrap" style={{ aspectRatio: `${layer.width} / ${layer.height}`,
        width: `min(100%, calc(${fill ? 66 : 70}vh * ${(layer.width / layer.height).toFixed(4)}))` }}
        onPointerDown={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).tagName === "VIDEO") setSel(null) }}>
        {clean ? <video ref={video} src={clean} controls playsInline preload="metadata" />
          : <div className="vr-loading"><span className="vr-spin" /> {t("result.loadingVideo")}</div>}
        <div className="tl-layer vr-tl-layer" style={{ ["--tlh" as any]: `${h}px` }}>
          {items.map((it) => {
            const on = it.id === sel
            const p = on ? { opacity: 1, scale: 1, dy: 0 } : pose(it, now, h)
            if (!p) return null
            return (
              <div key={`${it.id}:${on ? rev : 0}`} data-id={it.id}
                className={`tl-item tl-${it.style} tl-${it.kind} ${it.bg ? "tl-bg" : ""} vr-tl-item ${on ? "on" : ""}`}
                style={{ left: `${it.x}%`, top: `${it.y}%`, width: `${it.w}%`, ["--s" as any]: it.size,
                  ["--c" as any]: it.color, ["--d" as any]: shade(it.color), ["--bg" as any]: it.bg || "transparent",
                  fontFamily: layer.fonts[it.font] || layer.fonts.display, fontWeight: it.weight, opacity: p.opacity,
                  transform: `translate(-50%, -50%) translateY(${p.dy}px) scale(${p.scale})` }}
                contentEditable={on} suppressContentEditableWarning spellCheck={false}
                onPointerDown={(e) => { if (!on) { e.preventDefault(); pick(it.id) } }}
                onBlur={(e) => { const txt = (e.currentTarget.innerText || "").replace(/\n+$/, ""); if (txt !== it.text) { update(it.id, { text: txt }); setRev((r) => r + 1) } }}>
                {it.text}
                {on && <span className="vr-tl-handle" contentEditable={false} title={t("edit.move")} onPointerDown={(e) => drag(e, it)}>⠿</span>}
              </div>
            )
          })}
        </div>
      </div>

      {current && (
        <div className="vr-tl-bar" role="toolbar" aria-label={t("edit.textTools")}>
          <div className="vr-seg">
            <button className={current.style === "3d" ? "on" : ""} onClick={() => update(current.id, { style: "3d" })}>3D</button>
            <button className={current.style === "flat" ? "on" : ""} onClick={() => update(current.id, { style: "flat" })}>{t("edit.flat")}</button>
          </div>
          <div className="vr-seg">
            <button aria-label={t("edit.smaller")} onClick={() => update(current.id, { size: Math.max(1.5, r2(current.size * 0.88)) })}>A−</button>
            <button aria-label={t("edit.bigger")} onClick={() => update(current.id, { size: Math.min(30, r2(current.size * 1.14)) })}>A+</button>
          </div>
          <div className="vr-tl-swatches">
            {SWATCHES.map((c) => <button key={c} aria-label={c} className={current.color === c ? "on" : ""} style={{ background: c }}
              onClick={() => update(current.id, { color: c })} />)}
            <input type="color" aria-label={t("edit.color")} value={current.color} onChange={(e) => update(current.id, { color: e.target.value.toUpperCase() })} />
          </div>
          <span className="vr-tl-time">{fmt(current.start)}–{fmt(current.end)}</span>
          <button className="vr-h-btn" onClick={() => update(current.id, { start: r2(Math.min(video.current?.currentTime || 0, current.end - 0.6)) })}>{t("edit.startHere")}</button>
          <button className="vr-h-btn" onClick={() => update(current.id, { end: r2(Math.max(video.current?.currentTime || 0, current.start + 0.6)) })}>{t("edit.endHere")}</button>
          <button className="vr-h-btn danger" onClick={() => { setItems((xs) => xs.filter((x) => x.id !== current.id)); setSel(null) }}>{t("history.delete")}</button>
        </div>
      )}

      <div className="vr-tl-actions">
        <span className="vr-muted sm">{busy ? t("edit.updating") : current ? t("edit.typing") : t("edit.hint")}</span>
        <button className="vr-h-btn" onClick={add} disabled={busy}>{t("edit.add")}</button>
        {dirty && <button className="vr-h-btn" onClick={() => { setItems(layer.items); setSel(null) }} disabled={busy}>{t("edit.revert")}</button>}
        <button className="vr-cta sm" disabled={!dirty || busy} onClick={() => { setSel(null); onSave(items) }}>
          {busy ? <><span className="vr-spin light" />{t("edit.updating")}</> : t("edit.save")}
        </button>
      </div>
      <div className="vr-tl-actions">
        <span className="vr-muted sm">{t("edit.download")}</span>
        {finalUrl && <button className="vr-h-btn" onClick={() => save(finalUrl, `videostudio-${runId}.mp4`)}>{t("edit.withText")}</button>}
        {clean && <button className="vr-h-btn" onClick={() => save(clean, `videostudio-${runId}-clean.mp4`)}>{t("edit.clean")}</button>}
        <button className="vr-h-btn" onClick={() => save(new Blob([srt(items)], { type: "text/plain" }), `videostudio-${runId}.srt`)}>.srt</button>
        <button className="vr-h-btn" onClick={() => save(new Blob([JSON.stringify({ ...layer, items, css: undefined, fonts: undefined }, null, 2)],
          { type: "application/json" }), `videostudio-${runId}-text.json`)}>{t("edit.layerFile")}</button>
      </div>
    </div>
  )
}
