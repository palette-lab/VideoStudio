import { useCallback, useEffect, useRef, useState } from "react"
import { usePlatform } from "@palettelab/sdk"
import { api, bindPlatform, type Settings, type Run } from "./api"
import { useT, examplesFor, type T } from "./i18n"
import { EditableVideo } from "./textEditor"

// Modes in the menu. Presenter Studio and the HyperFrames Motion Studio stay in the backend (Auto still picks them).
const MODES = ["auto", "storyboard", "flow", "motion3d", "documentary", "programmatic"]
const MODE_IDS = new Set(MODES)
const BUSY = new Set(["analyzing", "approved", "executing"])
// Once a plan is approved the video is being made (or was made): these runs get the stage on the right.
const RENDER = new Set(["approved", "executing", "completed", "failed", "cancelled"])

// Whether real editing (OpenMontage + ffmpeg) is installed where the backend runs; from /health.
let realEditing = false
let studioApps = 0  // engines that really run (from /health), for the home badge
const isConnected = (adapter?: string) => adapter === "openmontage" ? realEditing : !!adapter && adapter !== "mock"

const human = (s?: string) => (s || "").replace(/_/g, " ")
const fmtTime = (t: number) => {
  const d = new Date(t * 1000)
  const today = new Date().toDateString() === d.toDateString()
  return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" })
}

/* Composer picks: "" = Auto (the AI reads it from the prompt). */
const LENGTHS = ["", "10", "15", "20", "30", "45", "60", "90"]
const RATIOS: [string, string][] = [["", "auto"], ["9:16", "portrait"], ["16:9", "landscape"], ["1:1", "square"], ["4:5", "tall"]]

/* ------------------------------------------------------------------ icons */
const I = {
  plus: <svg viewBox="0 0 20 20" aria-hidden><path d="M10 4v12M4 10h12" /></svg>,
  clip: <svg viewBox="0 0 20 20" aria-hidden><path d="M13.5 6.5 7.8 12.2a1.6 1.6 0 1 0 2.3 2.3l6-6a3.2 3.2 0 0 0-4.6-4.6l-6 6a4.8 4.8 0 1 0 6.8 6.8l5.2-5.2" /></svg>,
  x: <svg viewBox="0 0 20 20" aria-hidden><path d="M5 5l10 10M15 5 5 15" /></svg>,
  play: <svg viewBox="0 0 20 20" aria-hidden><path d="M7 5v10l8-5z" className="fill" /></svg>,
  note: <svg viewBox="0 0 20 20" aria-hidden><path d="M8 15V5l8-2v10" /><circle cx="6" cy="15" r="2" /><circle cx="14" cy="13" r="2" /></svg>,
  check: <svg viewBox="0 0 20 20" aria-hidden><path d="m4.5 10.5 3.5 3.5 7.5-8" /></svg>,
  up: <svg viewBox="0 0 20 20" aria-hidden><path d="M10 16V4m0 0-4.5 4.5M10 4l4.5 4.5" /></svg>,
  chev: <svg viewBox="0 0 20 20" aria-hidden><path d="m7.5 5 5 5-5 5" /></svg>,
  trash: <svg viewBox="0 0 20 20" aria-hidden><path d="M4 6h12M8 6V4h4v2m-6 0 .8 10h6.4L14 6" /></svg>,
  clock: <svg viewBox="0 0 20 20" aria-hidden><circle cx="10" cy="10" r="7" /><path d="M10 6v4l2.5 2" /></svg>,
  frame: <svg viewBox="0 0 20 20" aria-hidden><rect x="6" y="3" width="8" height="14" rx="1.5" /></svg>,
  spark: <svg viewBox="0 0 20 20" aria-hidden><path d="M10 2.5 11.6 8.4 17.5 10l-5.9 1.6L10 17.5l-1.6-5.9L2.5 10l5.9-1.6z" className="fill" /></svg>,
  sun: <svg viewBox="0 0 20 20" aria-hidden><circle cx="10" cy="10" r="3.5" /><path d="M10 2v2m0 12v2M2 10h2m12 0h2M4.3 4.3l1.4 1.4m8.6 8.6 1.4 1.4M4.3 15.7l1.4-1.4m8.6-8.6 1.4-1.4" /></svg>,
  moon: <svg viewBox="0 0 20 20" aria-hidden><path d="M16 12.5A6.5 6.5 0 0 1 7.5 4a6.5 6.5 0 1 0 8.5 8.5z" /></svg>,
  user: <svg viewBox="0 0 20 20" aria-hidden><circle cx="10" cy="7" r="3.2" /><path d="M3.8 16.5a6.3 6.3 0 0 1 12.4 0" /></svg>,
}

/* ------------------------------------------------------------------ app */
export default function App() {
  const platform: any = usePlatform()
  bindPlatform(platform)
  const { t, language } = useT()
  const [chats, setChats] = useState<any[]>([])
  const [chatId, setChatId] = useState<string | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showRecent, setShowRecent] = useState(false)
  const [pollKey, setPollKey] = useState(0)
  const endRef = useRef<HTMLDivElement>(null)
  const latest = runs[runs.length - 1]
  const busy = !!latest && BUSY.has(latest.status)
  // The newest run that has started making a video; while the first analysis runs there is none and the chat stays full width.
  const stageRun = [...runs].reverse().find((r) => r.plan && RENDER.has(r.status))

  const [apps, setApps] = useState(0)
  useEffect(() => {
    api.health().then((h: any) => {
      realEditing = !!h?.real_editing
      studioApps = Object.values(h?.adapters || {}).filter((a) => a && a !== "mock").length
      setApps(studioApps)
    }).catch(() => {})
  }, [])
  // Deep link: #chat=<id> opens that conversation.
  useEffect(() => {
    try {
      const m = /^#chat=([a-f0-9]{6,32})$/.exec(window.location.hash || "")
      if (m) setChatId(m[1])
    } catch { /* no hash access in this runtime */ }
  }, [])

  const refreshChats = useCallback(() => api.conversations().then(setChats).catch(() => {}), [])
  useEffect(() => { refreshChats() }, [refreshChats])

  useEffect(() => {
    if (!chatId) { setRuns([]); return }
    let alive = true
    let timer: any
    const tick = async () => {
      try {
        const c = await api.conversation(chatId)
        if (!alive) return
        setRuns(c.runs)
        const last = c.runs[c.runs.length - 1]
        if (last && BUSY.has(last.status)) timer = setTimeout(tick, 1500)
        else refreshChats()
      } catch (e: any) {
        if (!alive) return
        setError(e.message)
        timer = setTimeout(tick, 5000)  // a blip (network, server reload): keep checking instead of going quiet
      }
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
  }, [chatId, pollKey, refreshChats])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }) }, [runs.length, latest?.status])

  const fail = (e: any) => {
    setError(e.message)
    try { platform?.showToast?.(e.message, "error") } catch { /* inline alert shows it */ }
  }
  const after = (r: Run) => {
    if (r.conversation_id !== chatId) setChatId(r.conversation_id)
    else setPollKey((k) => k + 1)
    refreshChats()
  }
  const act = (fn: () => Promise<Run>) => async () => {
    setError(null)
    try { after(await fn()) } catch (e: any) { fail(e) }
  }

  /** The composer: a new chat, an answer to the router's question, or a follow-up change. */
  const send = async (text: string, files: File[], mode: string, onFile: (i: number) => void, settings?: Settings) => {
    setError(null)
    try {
      if (latest?.status === "needs_clarification" && (text || files.length)) {
        const q = latest.questions.find((x: any) => x.kind !== "upload") || latest.questions[0]
        after(await api.clarify(latest.id, text && q ? { [q.id]: text } : {}, files, false))
      } else {
        after(await api.create(text, mode, files, onFile, latest?.id, settings))
      }
      return true
    } catch (e: any) {
      fail(e)
      return false
    }
  }

  const newChat = () => { setChatId(null); setError(null); setShowRecent(false) }

  return (
    <div className="vr" lang={language} data-theme={platform?.colorMode === "dark" ? "dark" : "light"}>
      <header className="vr-nav">
        <button className="vr-logo" onClick={newChat} aria-label={t("nav.home")}>
          <span className="vr-mark" aria-hidden>
            <svg viewBox="0 0 32 32">
              <path d="M0 32V7a7 7 0 0 1 7-7h18a7 7 0 0 1 7 7v18a7 7 0 0 1-7 7z" fill="#6b5244" />
              <path d="M9 9l7 15 7-15" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinejoin="round" />
            </svg>
          </span>
          <span>Video<b>Studio</b></span>
        </button>
        <div className="vr-nav-mid">
          <button className={!chatId && !showRecent ? "on" : ""} onClick={newChat}>{t("nav.create")}</button>
          <button className={showRecent ? "on" : ""} onClick={() => setShowRecent(!showRecent)} aria-expanded={showRecent}>
            {t("nav.history")}{chats.length > 0 && <span className="vr-count">{chats.length}</span>}
          </button>
        </div>
        <div className="vr-nav-end">
          <QuickToggles />
          <AccountMenu />
          <button className="vr-cta" onClick={newChat}>{t("nav.newChat")} {I.plus}</button>
        </div>
      </header>
      {showRecent ? (
        <main className="vr-history">
        <h1 className="vr-history-title">{t("nav.history")}</h1>
        <section className="vr-recent" aria-label={t("nav.history")}>
          {chats.length === 0 && <p className="vr-empty">{t("history.empty")}</p>}
          {chats.map((c) => (
            <HistoryItem key={c.id} chat={c} active={c.id === chatId} onError={fail}
              onOpen={() => { setChatId(c.id); setShowRecent(false) }}
              onDeleted={() => { if (c.id === chatId) setChatId(null); refreshChats() }} />
          ))}
        </section>
        </main>
      ) : null}

      {error && <div className="vr-alert" role="alert">{error}<button onClick={() => setError(null)} aria-label={t("chat.dismiss")}>{I.x}</button></div>}

      {showRecent ? null : !chatId ? (
        <main className="vr-home">
          <header className="vr-hero">
            <div className="vr-badge"><span>{t("hero.badge")}</span>{apps > 0 && <em>{t("hero.apps", { n: apps })}</em>}</div>
            <h1><span className="soft">{t("hero.t1")}</span> {t("hero.t2")} <br />{t("hero.t3")} <span className="soft">{t("hero.t4")}</span></h1>
            <p>{t("hero.lead")}</p>
          </header>
          <Composer hero onSend={send} />
          <div className="vr-examples">
            <h2>{t("hero.tryOne")}</h2>
            {examplesFor(language).map((ex) => <button key={ex} onClick={() => send(ex, [], "auto", () => {})}>{ex}</button>)}
          </div>
        </main>
      ) : (
        <main className={`vr-chat ${stageRun ? "split" : ""}`}>
          <div className="vr-chat-col">
          <div className="vr-thread">
            {runs.length === 0 && <div className="vr-loading"><span className="vr-spin" /> {t("chat.loading")}</div>}
            {runs.map((r, i) => (
              <Turn key={r.id} run={r} prev={runs[i - 1]} latest={i === runs.length - 1} staged={r.id === stageRun?.id}
                onAnswer={(answers: any, proceed: boolean) => act(() => api.clarify(r.id, answers, [], proceed))()}
                onApprove={(accept: boolean) => act(() => api.approve(r.id, r.plan_revision, accept))()}
                onRecover={(sid: string, appId?: string, accept?: boolean) => act(() => api.recover(r.id, sid, appId, accept))()}
                onSelect={(oid: string) => act(() => api.select(r.id, oid))()}
                onRerun={(m: string) => act(() => api.rerun(r.id, m))()}
                onEditScene={(n: number, f: any) => act(() => api.editScene(r.id, n, f))()}
                onRegenScene={(n: number, how: string) => act(() => api.regenerateScene(r.id, n, how))()}
                onPalette={(o: number) => act(() => api.switchPalette(r.id, o))()}
                onMotionEngine={(e: string) => act(() => api.switchMotionEngine(r.id, e))()}
                onCancel={() => act(() => api.cancel(r.id))()}
                onRerender={(n: number, how: string, sid?: string) => act(() => api.rerenderScene(r.id, n, how, sid))()}
                onText={(sid: string, items: any[]) => act(() => api.saveText(r.id, sid, items))()}
              />
            ))}
            <div ref={endRef} />
          </div>
          <div className="vr-dock">
            <Composer
              busy={busy}
              defaultMode={latest?.mode}
              placeholder={busy ? t("composer.working") : latest?.status === "needs_clarification" ? t("composer.answer")
                : latest?.status === "awaiting_approval" ? t("composer.change")
                : t("composer.followUp")}
              onSend={send}
            />
            <div className="vr-dock-foot">
              <span>{t("composer.dockFoot")}</span>
            </div>
          </div>
          </div>
          {stageRun && <Stage key={stageRun.id} run={stageRun} onCancel={() => act(() => api.cancel(stageRun.id))()}
            onText={(sid: string, items: any[]) => act(() => api.saveText(stageRun.id, sid, items))()} />}
        </main>
      )}
    </div>
  )
}

/** The card picture: the video's frame (skeleton while it loads), or a placeholder saying where the run stands. */
function VideoThumb({ video, status }: { video?: { run_id: string; step_id: string }; status: string }) {
  const { t } = useT()
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!video) return
    let u: string | null = null
    let alive = true
    api.thumbnailUrl(video.run_id, video.step_id)
      .then((x) => { u = x; if (alive) setSrc(x); else URL.revokeObjectURL(x) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [video?.run_id, video?.step_id])
  if (video && !failed) {
    return src ? (
      <span className="vr-card-media"><img src={src} alt="" /><span className="vr-card-play" aria-hidden>{I.play}</span></span>
    ) : <span className="vr-card-media vr-skeleton" aria-label={t("history.loadingPreview")} />
  }
  const busy = BUSY.has(status)
  return (
    <span className={`vr-card-media empty ${busy ? "vr-skeleton" : ""}`}>
      <span>{busy ? t("history.making") : statusLabel(status, t)}</span>
    </span>
  )
}

/** One chat in the history: open it, download its newest video, or delete it (asks once to confirm). */
function HistoryItem({ chat, active, onOpen, onDeleted, onError }: any) {
  const { t } = useT()
  const [confirm, setConfirm] = useState(false)
  const [working, setWorking] = useState<"" | "download" | "delete">("")
  const busy = BUSY.has(chat.status)
  const download = async () => {
    setWorking("download")
    try {
      const url = await api.outputUrl(chat.video.run_id, chat.video.step_id)
      const a = document.createElement("a")
      a.href = url
      a.download = `videostudio-${chat.video.run_id}.mp4`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e: any) { onError(e) } finally { setWorking("") }
  }
  const remove = async () => {
    setWorking("delete")
    try { await api.removeConversation(chat.id); onDeleted() } catch (e: any) { onError(e); setWorking("") }
  }
  return (
    <div className={`vr-h-item ${active ? "on" : ""}`}>
      <button className="vr-h-thumb" onClick={onOpen} aria-label={t("history.open", { name: chat.title || t("history.filesOnly") })}>
        <VideoThumb video={chat.video} status={chat.status} />
      </button>
      <button className="vr-h-open" onClick={onOpen}>
        <span className="vr-h-prompt">{chat.title || t("history.filesOnly")}</span>
        <span className="vr-h-meta"><StatusDot status={chat.status} />{statusLabel(chat.status, t)} · {chat.turns > 1 ? `${t("history.turns", { n: chat.turns })} · ` : ""}{fmtTime(chat.updated_at)}</span>
      </button>
      <div className="vr-h-actions">
        {confirm ? (
          <>
            <span>{busy ? t("history.stillRunning") : t("history.confirm")}</span>
            <button className="vr-h-btn danger" disabled={!!working} onClick={remove}>{working === "delete" ? t("history.deleting") : t("history.delete")}</button>
            <button className="vr-h-btn" disabled={!!working} onClick={() => setConfirm(false)}>{t("history.cancel")}</button>
          </>
        ) : (
          <>
            {chat.video && (
              <button className="vr-h-btn" disabled={!!working} onClick={download}>{working === "download" ? t("history.preparing") : t("history.download")}</button>
            )}
            <button className="vr-h-btn" onClick={() => setConfirm(true)}>{t("history.delete")}</button>
          </>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ composer */
function Composer({ hero, busy, placeholder, defaultMode, onSend }: {
  hero?: boolean; busy?: boolean; placeholder?: string; defaultMode?: string
  onSend: (t: string, f: File[], m: string, onFile: (i: number) => void, settings?: Settings) => Promise<boolean>
}) {
  const { t } = useT()
  const [text, setText] = useState("")
  const [mode, setMode] = useState(defaultMode && MODE_IDS.has(defaultMode) ? defaultMode : "auto")
  const [files, setFiles] = useState<File[]>([])
  const [drag, setDrag] = useState(false)
  const [sending, setSending] = useState<string | null>(null)
  const [length, setLength] = useState("")  // "" = Auto (from the prompt)
  const [ratio, setRatio] = useState("")
  const input = useRef<HTMLInputElement>(null)
  // A chat made in a mode that is no longer in the menu (presenter, motion) continues in Auto.
  useEffect(() => { if (defaultMode) setMode(MODE_IDS.has(defaultMode) ? defaultMode : "auto") }, [defaultMode])
  // Copy the files out now: resetting the input right after empties the live FileList the state update would read.
  const add = (list: FileList | null) => {
    const picked = list ? Array.from(list) : []
    if (picked.length) setFiles((f) => [...f, ...picked])
  }
  const ready = (text.trim() || files.length) && !sending && !busy

  const submit = async () => {
    if (!ready) return
    setSending(t("composer.sending"))
    const ok = await onSend(text.trim(), files, mode, (i) => setSending(t("composer.uploading", { i: i + 1, n: files.length })),
                            { duration_s: length ? Number(length) : undefined, aspect_ratio: ratio || undefined })
    setSending(null)
    if (ok) { setText(""); setFiles([]) }
  }

  return (
    <div
      className={`vr-box ${hero ? "hero" : "dock"} ${drag ? "drag" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files) }}
    >
      {files.length > 0 && (
        <div className="vr-attach">
          {files.map((f, i) => <Thumb key={`${f.name}-${f.size}-${i}`} file={f} onRemove={() => setFiles(files.filter((_, j) => j !== i))} />)}
        </div>
      )}
      <textarea
        id={hero ? "vr-prompt" : "vr-reply"}
        aria-label={hero ? t("composer.describe") : t("composer.message")}
        value={text}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // Enter runs it; Shift+Enter or Ctrl/Cmd+Shift+Enter adds a new line. Never while an IME (Korean, ...)
          // is still composing a character: that Enter only confirms the character.
          if (e.key !== "Enter" || e.nativeEvent.isComposing || e.keyCode === 229) return
          if (!e.shiftKey) { e.preventDefault(); submit(); return }
          if (e.ctrlKey || e.metaKey) {  // browsers add no line for Ctrl+Shift+Enter themselves
            e.preventDefault()
            const el = e.currentTarget, a = el.selectionStart, b = el.selectionEnd
            setText(text.slice(0, a) + "\n" + text.slice(b))
            requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = a + 1 })
          }
        }}
        placeholder={placeholder || t("composer.placeholder")}
        rows={hero ? 4 : 1}
      />
      <div className="vr-bar">
        <button className="vr-chipbtn" onClick={() => input.current?.click()} aria-label={t("composer.addFilesAria")}>
          {I.clip}<span>{files.length ? t("composer.filesAdded", { n: files.length }) : hero ? t("composer.addFiles") : ""}</span>
        </button>
        <input ref={input} type="file" multiple hidden accept="image/*,video/*,audio/*,.pdf,.txt,.md,.docx,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(e) => { add(e.target.files); e.target.value = "" }} />
        <label className={`vr-pick ${length ? "on" : ""}`} title={t("composer.lengthHint")}>
          {I.clock}
          <select aria-label={t("composer.length")} value={length} onChange={(e) => setLength(e.target.value)}>
            {LENGTHS.map((v) => <option key={v} value={v}>{v ? `${v} s` : t("composer.auto")}</option>)}
          </select>
        </label>
        <label className={`vr-pick ${ratio ? "on" : ""}`} title={t("composer.formatHint")}>
          {I.frame}
          <select aria-label={t("composer.format")} value={ratio} onChange={(e) => setRatio(e.target.value)}>
            {RATIOS.map(([v, k]) => <option key={v} value={v}>{v ? `${v} ${t(`composer.${k}`)}` : t("composer.auto")}</option>)}
          </select>
        </label>
        <label className={`vr-pick vr-mode ${mode !== "auto" ? "on" : ""}`} title={t(`modes.${mode}Hint`)}>
          {I.spark}
          <select aria-label={t("composer.mode")} value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODES.map((m) => <option key={m} value={m} title={t(`modes.${m}Hint`)}>{t(`modes.${m}`)}</option>)}
          </select>
        </label>
        {hero ? (
          <button className="vr-cta go" disabled={!ready} onClick={submit}>
            {sending ? <><span className="vr-spin light" />{sending}…</> : <>{t("composer.generate")} {I.up}</>}
          </button>
        ) : (
          <button className="vr-send" disabled={!ready} onClick={submit} aria-label={t("composer.send")}>
            {sending || busy ? <span className="vr-spin light" /> : I.up}
          </button>
        )}
      </div>
    </div>
  )
}

function Thumb({ file, onRemove }: { file: File; onRemove: () => void }) {
  const { t } = useT()
  const kind = file.type.split("/")[0]
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (kind !== "image" && kind !== "video") return
    const u = URL.createObjectURL(file)
    setUrl(u)
    return () => URL.revokeObjectURL(u)
  }, [file, kind])
  return (
    <div className="vr-thumb" title={file.name}>
      {kind === "image" && url && <img src={url} alt={file.name} />}
      {kind === "video" && url && <video src={`${url}#t=0.1`} muted playsInline preload="metadata" />}
      {kind !== "image" && kind !== "video" && <div className="vr-thumb-file">{I.note}<small>{file.name}</small></div>}
      {kind === "video" && <span className="vr-thumb-badge">{I.play}</span>}
      <button className="vr-thumb-x" aria-label={t("composer.remove", { name: file.name })} onClick={onRemove}>{I.x}</button>
    </div>
  )
}

/* ------------------------------------------------------------------ one turn: your message + the reply */
function statusLabel(s: string, t: T) {
  return t(`status.${s}`, undefined, human(s))
}

function StatusDot({ status }: { status: string }) {
  const tone = ["completed", "succeeded"].includes(status) ? "ok" : ["failed", "error", "not_possible"].includes(status) ? "bad"
    : ["needs_clarification", "awaiting_approval"].includes(status) ? "attn" : BUSY.has(status) || status === "running" ? "busy" : "idle"
  return <span className={`vr-dot ${tone}`} aria-hidden />
}

/** A collapsed section with an arrow, like a model's "thinking" block. */
function Fold({ title, meta, live, open, children }: { title: string; meta?: string; live?: boolean; open?: boolean; children: any }) {
  return (
    <details className={`vr-fold ${live ? "live" : ""}`} open={open}>
      <summary>
        <span className="vr-chev">{I.chev}</span>
        {live && <span className="vr-spin" />}
        <span className="vr-fold-title">{title}</span>
        {meta && <span className="vr-fold-meta">{meta}</span>}
      </summary>
      <div className="vr-fold-body">{children}</div>
    </details>
  )
}

/** An uploaded file in a sent message: image and video previews like a chat app, audio as a small card. */
function SentFile({ runId, asset }: { runId: string; asset: any }) {
  const media = asset.modality === "image" || asset.modality === "video"
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!media && asset.modality !== "audio") return
    let u: string | null = null, gone = false
    api.fileUrl(runId, asset.id).then((x) => { if (gone) URL.revokeObjectURL(x); else { u = x; setUrl(x) } }).catch(() => !gone && setFailed(true))
    return () => { gone = true; if (u) URL.revokeObjectURL(u) }
  }, [runId, asset.id, asset.modality, media])
  if (asset.modality === "audio") {
    return (
      <div className="vr-sent-audio" title={asset.filename}>
        <span className="vr-sent-ico">{I.note}</span>
        <div><strong>{asset.filename}</strong>{url ? <audio src={url} controls preload="metadata" /> : <small>{failed ? "Preview unavailable" : "Audio"}</small>}</div>
      </div>
    )
  }
  return (
    <a className={`vr-sent-media ${url ? "" : "loading"}`} href={url || undefined} target="_blank" rel="noreferrer" title={asset.filename}
      onClick={(e) => { if (!url) e.preventDefault() }}>
      {url && asset.modality === "image" && <img src={url} alt={asset.filename} />}
      {url && asset.modality === "video" && <video src={`${url}#t=0.1`} muted playsInline preload="metadata" />}
      {!url && <span className="vr-sent-ph">{failed ? <small>{asset.filename}</small> : <span className="vr-spin" />}</span>}
      {asset.modality === "video" && <span className="vr-thumb-badge">{I.play}</span>}
    </a>
  )
}

function Turn(p: any) {
  const { run, latest } = p
  const assets: any[] = Object.values(run.assets || {})
  // Only the files this message added: follow-ups and reruns carry earlier files over (marked inherited).
  const legacy = run.parent_run && !assets.some((a) => "inherited" in a)
  const own = legacy ? [] : assets.filter((a) => !a.inherited)
  const showFiles = own.length > 0
  const { t } = useT()
  return (
    <section className="vr-turn">
      {showFiles && (
        <div className="vr-msg user">
          <div className="vr-sent-files" aria-label={`${own.length} uploaded file${own.length > 1 ? "s" : ""}`}>
            {own.map((a) => <SentFile key={a.id} runId={run.id} asset={a} />)}
          </div>
        </div>
      )}
      <div className="vr-msg user">
        <div className="vr-bubble">
          {(run.message || run.prompt || !showFiles) && <p>{run.message || run.prompt || t("history.filesOnly")}</p>}
          {(run.settings?.duration_s || run.settings?.aspect_ratio) && (
            <div className="vr-files">
              {run.settings.duration_s && <span className="vr-chip">{I.clock}{run.settings.duration_s} s</span>}
              {run.settings.aspect_ratio && <span className="vr-chip">{I.frame}{run.settings.aspect_ratio}</span>}
            </div>
          )}
        </div>
      </div>
      <div className="vr-msg bot">
        <span className="vr-avatar" aria-hidden>{I.spark}</span>
        <div className="vr-reply">
          {run.story_intel?.plan?.scenes?.length > 0 && <StoryPanel run={run} onEdit={p.onEditScene} onRegen={p.onRegenScene} onPalette={p.onPalette} onMotionEngine={p.onMotionEngine} />}
          {run.enhanced_prompt && !run.story_intel?.plan && (
            <Fold title={t("chat.enhanced")} meta={`${run.agent_label || t("chat.fromWords")}${run.agent_why ? ` · ${run.agent_why}` : ""}`}>
              {run.video_intelligence?.categories?.length > 0 && (
                <div className="vr-files">
                  {run.video_intelligence.categories.map((c: string) => <span key={c} className="vr-chip">{c}</span>)}
                  {run.video_intelligence.three_d && run.video_intelligence.three_d !== "none" &&
                    <span className="vr-chip">3D: {({ cgi_render: "CGI render", css_2_5d: "2.5D motion graphics", both: "CGI + 2.5D" } as any)[run.video_intelligence.three_d]}</span>}
                </div>
              )}
              <p className="vr-enhanced">{run.enhanced_prompt}</p>
              {run.video_intelligence?.limitations?.length > 0 && (
                <ul className="vr-muted sm">{run.video_intelligence.limitations.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
              )}
            </Fold>
          )}
          <Analysis run={run} />
          {p.prev && <Changes run={run} prev={p.prev} />}
          <Answer {...p} latest={latest} />
        </div>
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ Story Intelligence: the film before it is made */
const PROMPT_PARTS: [string, string][] = [["subject", "Subject"], ["environment", "Environment"], ["action", "Action"],
  ["interaction", "Interaction"], ["emotion", "Emotion"], ["camera", "Camera"], ["lighting", "Lighting"],
  ["visual_design", "Visual design"], ["motion", "Motion"], ["effects", "Effects"], ["continuity", "Continuity"], ["ending", "Ending"]]
const txt = (v: any): string => Array.isArray(v) ? v.map(txt).join("; ") : v && typeof v === "object"
  ? Object.entries(v).filter(([, x]) => x).map(([k, x]) => `${k.replace(/_/g, " ")}: ${txt(x)}`).join(" · ") : String(v ?? "")

const PALETTE_SWATCH: Record<number, string[]> = { 1: ["#F4EFE6", "#4B2A91", "#141414"], 2: ["#151A21", "#14E0C8", "#F5F7FA"], 3: ["#F2EBDD", "#B4532A", "#2B2B2B"] }

const MOTION_ENGINES: [string, string, string][] = [
  ["opus_llm", "Opus LLM", "No video model: Opus plans each keyframe's motion and code renders it (camera, layers, SFX)"],
  ["video_model", "AI video model", "Each keyframe is filmed as an AI video clip"],
]

function MotionPlan({ mg, editable, onPalette, engine, onMotionEngine }: { mg: any; editable: boolean; onPalette: (o: number) => void; engine?: string; onMotionEngine?: (e: string) => void }) {
  const { t } = useT()
  const chosen = Number(mg.palette?.option)
  const art = mg.art_direction || {}
  // Each video gets its own palettes (with HEX swatches); older plans fall back to the original three.
  const swatches = (k: string) => mg.palette_swatches?.[k]?.length ? mg.palette_swatches[k] : PALETTE_SWATCH[Number(k)] || []
  return (
    <>
      {art.name && (
        <p className="vr-art"><span className="vr-chip accent">{t("plan.artDirection")}</span> <strong>{art.name}</strong>
          {art.concept && <span className="vr-muted sm"> — {art.concept}</span>}</p>
      )}
      <div className="vr-palettes" role="group" aria-label={t("plan.palette")}>
        {Object.entries(mg.palettes || {}).map(([k, name]) => (
          <button key={k} className={`vr-palette ${Number(k) === chosen ? "on" : ""}`} disabled={!editable || Number(k) === chosen}
            onClick={() => onPalette(Number(k))} title={Number(k) === chosen ? t("plan.lockedPalette") : t("plan.lockPalette")}>
            <span className="vr-swatches">{swatches(k).map((c: string, i: number) => <i key={`${c}-${i}`} style={{ background: c }} />)}</span>
            {k}. {String(name)}
          </button>
        ))}
      </div>
      {mg.palette?.why && <p className="vr-muted sm">{mg.palette.why}</p>}
      <div className="vr-palettes" role="group" aria-label="Motion engine">
        {MOTION_ENGINES.map(([k, label, why]) => (
          <button key={k} className={`vr-palette ${(engine || "video_model") === k ? "on" : ""}`} title={why}
            disabled={!editable || !onMotionEngine || (engine || "video_model") === k} onClick={() => onMotionEngine?.(k)}>
            {label}
          </button>
        ))}
      </div>
      <details className="vr-story-sec" open><summary>Script · {mg.beats?.length || 0} visual beats</summary>
        {(mg.beats || []).map((b: any) => <p key={b.id}><b>{b.id}</b> <span className="vr-muted">{b.role}</span> — {b.line}</p>)}
        {mg.connection && <p className="vr-muted sm">{mg.connection}</p>}
      </details>
      {mg.production && Object.keys(mg.production).length > 0 && (
        <details className="vr-story-sec"><summary>Production plan</summary>
          {["application", "model", "method", "resolution", "aspect_ratio", "audio"].filter((k) => mg.production[k]).map((k) =>
            <p key={k}><b>{k.replace(/_/g, " ")}</b> {txt(mg.production[k])}</p>)}
          {mg.production.limitations?.length > 0 && <ul>{mg.production.limitations.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>}
        </details>
      )}
    </>
  )
}

function StoryPanel({ run, onEdit, onRegen, onPalette, onMotionEngine }: { run: Run; onEdit: (n: number, f: any) => void; onRegen: (n: number, how: string) => void; onPalette: (o: number) => void; onMotionEngine?: (e: string) => void }) {
  const intel = run.story_intel, plan = intel.plan, c = intel.classification || {}, r = intel.research, v = intel.validation || {}
  const editable = run.status === "awaiting_approval" || run.status === "needs_clarification"
  const issues = [...(v.rules || []), ...(v.issues || [])]
  const mg = plan.motion_graphics
  return (
    <Fold title={`${mg ? "Full 3D motion graphics" : "Story"} · ${plan.title || "untitled"}`} open={editable}
      meta={`${String(c.category || "").replace(/_/g, " ")} · ${plan.scenes.length} scenes · ${c.duration_s} s${v.score != null ? ` · review ${v.score}/100` : ""}`}>
      <div className="vr-story">
        <div className="vr-files">
          {[c.category, c.story_type, c.audience, c.tone].filter(Boolean).map((x: string) => <span key={x} className="vr-chip">{String(x).replace(/_/g, " ")}</span>)}
        </div>
        {plan.logline && <p className="vr-story-log">{plan.logline}</p>}
        {mg ? <MotionPlan mg={mg} editable={editable} onPalette={onPalette} engine={intel.motion_engine} onMotionEngine={onMotionEngine} /> : plan.story_concept && <p>{plan.story_concept}</p>}
        {r && (r.summary || r.note) && (
          <details className="vr-story-sec"><summary>Research{r.sources?.length ? ` · ${r.sources.length} sources` : ""}</summary>
            {r.summary && <p>{r.summary}</p>}{r.note && <p className="vr-muted sm">{r.note}</p>}
            {r.facts?.length > 0 && <ul>{r.facts.map((f: any, i: number) => <li key={i}>{f.claim} <span className="vr-muted">— {f.source}{f.confidence && f.confidence !== "verified" ? ` (${f.confidence})` : ""}</span></li>)}</ul>}
            {r.conflicts?.length > 0 && <p className="vr-muted sm">Sources disagree: {r.conflicts.join("; ")}</p>}
            {r.sources?.length > 0 && <p className="vr-muted sm">Sources: {r.sources.map((s: any, i: number) => <span key={i}>{i ? ", " : ""}{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}</span>)}</p>}
          </details>
        )}
        {plan.puzzle && (
          <details className="vr-story-sec"><summary>Puzzle and solution{v.solver ? ` · independent check: ${v.solver.unique ? "unique answer" : "not unique"}` : ""}</summary>
            <p><b>Question</b> {plan.puzzle.question}</p><p><b>Rules</b> {txt(plan.puzzle.rules)}</p>
            <ul>{(plan.puzzle.clues || []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
            <p><b>Solution</b> {plan.puzzle.solution}</p><p className="vr-muted sm">{plan.puzzle.reasoning}</p>
            {v.solver && <p className="vr-muted sm">Independent solver: {v.solver.answer}</p>}
          </details>
        )}
        {!mg && <details className="vr-story-sec"><summary>Continuity bible</summary>
          {(plan.bible?.characters || []).map((ch: any, i: number) => <p key={i}><b>{ch.name || `Character ${i + 1}`}</b> {txt({ ...ch, name: undefined })}</p>)}
          {plan.bible?.world && <p><b>World</b> {txt(plan.bible.world)}</p>}
          {(plan.bible?.objects || []).map((o: any, i: number) => <p key={i}><b>{o.name || "Object"}</b> {txt({ ...o, name: undefined })}</p>)}
        </details>}
        <details className="vr-story-sec"><summary>Script</summary>
          {plan.scenes.filter((s: any) => s.audio?.line && s.audio.type !== "none").map((s: any) =>
            <p key={s.n}><b>{s.n}. {s.audio.speaker || "Narrator"}</b> “{s.audio.line}” <span className="vr-muted">{s.audio.emotion}{s.audio.delivery ? ` · ${s.audio.delivery}` : ""}</span></p>)}
          {plan.takeaway && <p><b>Ending</b> {plan.takeaway}</p>}
        </details>
        {(plan.audio_plan || plan.style) && (
          <details className="vr-story-sec"><summary>Audio, look and effects</summary>
            {plan.style?.visual_style && <p><b>Look</b> {plan.style.visual_style}{plan.style.look ? ` · ${txt(plan.style.look)}` : ""}</p>}
            {plan.audio_plan?.music && <p><b>Music</b> {plan.audio_plan.music}</p>}
            {plan.audio_plan?.voice && <p><b>Voice</b> {plan.audio_plan.voice}</p>}
            {plan.audio_plan?.sync_points?.length > 0 && <p><b>Sync</b> {plan.audio_plan.sync_points.join(" · ")}</p>}
            {plan.style?.effects_plan?.length > 0 && <ul>{plan.style.effects_plan.map((e: any, i: number) => <li key={i}>Scene {e.scene}: {e.effect} <span className="vr-muted">— {e.why}</span></li>)}</ul>}
          </details>
        )}
        <div className="vr-story-scenes">
          {plan.scenes.map((s: any) => <StoryScene key={`${s.n}-${s.title}`} s={s} editable={editable} onEdit={onEdit} onRegen={onRegen} />)}
        </div>
        {(() => {
          const must = issues.filter((i: any) => i.blocking || (v.rules || []).includes(i))
          const notes = issues.filter((i: any) => !must.includes(i))
          const row = (i: any, k: number) => <li key={k}>{i.scene ? `Scene ${i.scene}: ` : ""}{i.problem}{i.fix ? ` → ${i.fix}` : ""}</li>
          return (<>
            <p className={v.passed === false && must.length ? "vr-qa-bad sm" : "vr-good sm"}>
              {must.length ? `Validation: ${must.length} problem${must.length > 1 ? "s" : ""} to fix` : "Validation passed"}
              {notes.length ? ` · ${notes.length} note${notes.length > 1 ? "s" : ""}` : ""}
              {v.revised ? ` · revised after the first review${v.first_score != null ? ` (${v.first_score}/100)` : ""}` : ""}
            </p>
            {must.length > 0 && <ul className="vr-qa-bad sm">{must.slice(0, 8).map(row)}</ul>}
            {notes.length > 0 && <details className="vr-muted sm"><summary>Reviewer notes</summary><ul>{notes.slice(0, 8).map(row)}</ul></details>}
          </>)
        })()}
      </div>
    </Fold>
  )
}

function StoryScene({ s, editable, onEdit, onRegen }: { s: any; editable: boolean; onEdit: (n: number, f: any) => void; onRegen: (n: number, how: string) => void }) {
  const [mode, setMode] = useState<"" | "edit" | "regen">("")
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [how, setHow] = useState("")
  const field = (k: string, label: string, val: string, rows = 2) => (
    <label className="vr-story-field"><span>{label}</span>
      <textarea rows={rows} defaultValue={val} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} /></label>
  )
  return (
    <details className="vr-story-scene">
      <summary><b>{s.n}. {s.title}</b> <span className="vr-muted">{s.duration_s} s · {s.purpose}</span>{s.edited && <span className="vr-chip">edited</span>}</summary>
      <p>{s.what_happens}</p>
      <p className="sm"><b>Action</b> {s.actions}</p>
      {s.emotions && <p className="sm"><b>Emotion</b> {s.emotions}</p>}
      <p className="sm"><b>Shot</b> {[s.framing, s.camera_movement, s.lens].filter(Boolean).join(" · ")}</p>
      {s.layers && <p className="sm"><b>Layers</b> {txt(s.layers)}</p>}
      <p className="sm"><b>Light</b> {s.lighting}{s.atmosphere ? ` · ${s.atmosphere}` : ""}</p>
      {s.audio?.line && s.audio.type !== "none" && <p className="sm"><b>{s.audio.speaker || "Voice"}</b> “{s.audio.line}”</p>}
      {s.sfx && <p className="sm"><b>Sound</b> {s.sfx}</p>}
      <p className="sm"><b>In</b> {s.transition_in} · <b>Out</b> {s.transition_out}</p>
      <p className="sm"><b>Starts</b> {s.start_state} · <b>Ends</b> {s.end_state}</p>
      {s.props && <p className="sm"><b>Objects</b> {txt(s.props)}</p>}
      {s.motion_gfx && (
        <details className="vr-scene-prompt"><summary>3D keyframe · words · motion</summary>
          <p><b>Keyframe (text-to-image)</b> {s.motion_gfx.keyframe_prompt}</p>
          {s.motion_gfx.typography_text && <p><b>3D words in the scene</b> “{s.motion_gfx.typography_text}” — {s.motion_gfx.typography_style}</p>}
          {s.motion_gfx.three_d_elements && <p><b>3D</b> {s.motion_gfx.three_d_elements}</p>}
          {s.motion_gfx.graphic_elements && <p><b>Graphics</b> {s.motion_gfx.graphic_elements}</p>}
          {(s.motion_gfx.motion?.timeline || []).map((x: any, i: number) => <p key={i}><b>{x.t} s</b> {x.what}</p>)}
          {s.motion_gfx.motion?.typography_motion && <p><b>Type motion</b> {s.motion_gfx.motion.typography_motion}</p>}
          {s.motion_gfx.method && <p><b>Method</b> {String(s.motion_gfx.method).replace(/_/g, " ")}</p>}
        </details>
      )}
      <details className="vr-scene-prompt"><summary>Video prompt</summary>
        {PROMPT_PARTS.filter(([k]) => s.prompt?.[k]).map(([k, label]) => <p key={k}><b>{label}</b> {s.prompt[k]}</p>)}
      </details>
      {editable && (
        <div className="vr-story-actions">
          <button className="vr-ghost sm" onClick={() => setMode(mode === "edit" ? "" : "edit")}>Edit</button>
          <button className="vr-ghost sm" onClick={() => setMode(mode === "regen" ? "" : "regen")}>Regenerate</button>
        </div>
      )}
      {mode === "edit" && (
        <div className="vr-story-form">
          {field("title", "Title", s.title, 1)}
          {field("what_happens", "What happens", s.what_happens, 3)}
          {field("actions", "Action beats", s.actions, 3)}
          {field("line", "Narration / dialogue", s.audio?.line || "", 2)}
          {field("prompt.action", "Prompt: action", s.prompt?.action || "", 3)}
          {field("prompt.camera", "Prompt: camera", s.prompt?.camera || "", 2)}
          <button className="vr-cta sm" disabled={!Object.keys(draft).length}
            onClick={() => { onEdit(s.n, draft); setMode(""); setDraft({}) }}>Save scene</button>
        </div>
      )}
      {mode === "regen" && (
        <div className="vr-story-form">
          <label className="vr-story-field"><span>What should change? (optional)</span>
            <textarea rows={2} value={how} placeholder="e.g. make the hare trip over a root before he naps" onChange={(e) => setHow(e.target.value)} /></label>
          <button className="vr-cta sm" onClick={() => { onRegen(s.n, how); setMode(""); setHow("") }}>Regenerate this scene</button>
        </div>
      )}
    </details>
  )
}

function Analysis({ run }: { run: Run }) {
  const { t } = useT()
  const thinking = run.status === "analyzing"
  const u = run.understanding
  const assets: any[] = Object.values(run.assets || {})
  // Thinking time only: from the first event to the end of the analysis (not the run that follows).
  const ev = run.events || []
  const endIdx = ev.findIndex((e: any) => /^(Plan ready|A question|No connected app|Analysis finished)/.test(e.msg))
  const secs = ev.length > 1 && endIdx > 0 ? Math.max(1, Math.round(ev[endIdx].t - ev[0].t)) : null
  const title = thinking ? `${t(`stage.${run.stage}`, undefined, t("chat.thinking"))}…` : t("chat.analysis")
  const meta = thinking ? undefined : [u?.production_type?.slice(0, 2).map(human).join(" · "), secs && !BUSY.has(run.status) ? `${secs}s` : null].filter(Boolean).join(" · ")
  return (
    <Fold title={title} meta={meta} live={thinking}>
      <ul className="vr-events">{(run.events || []).map((e: any, i: number) => <li key={i}>{e.msg}</li>)}</ul>
      {u && (
        <>
          <p className="vr-goal">{u.goal}</p>
          <Facts u={u} />
          {u.assumptions?.length > 0 && <div><h4>{t("chat.assumed")}</h4><ul>{u.assumptions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>}
        </>
      )}
      {assets.length > 0 && (
        <div className="vr-assets">
          {assets.map((a) => {
            const r = run.perception?.[a.id]
            return (
              <div key={a.id} className="vr-asset">
                <div className="vr-asset-head">
                  <span className="vr-chip">{a.modality}</span><strong>{a.filename}</strong>
                  {a.role && a.role !== "unclear" && <span className="vr-role">{t("chat.usedAs", { role: human(a.role) })}</span>}
                  {a.role === "unclear" && <span className="vr-role warn">{t("chat.unclear")}</span>}
                </div>
                {r?.summary && <p className="sm">{r.summary}</p>}
                {r?.error && <p className="vr-muted sm">{r.error}</p>}
              </div>
            )
          })}
        </div>
      )}
    </Fold>
  )
}

function Answer({ run, latest, staged, onAnswer, onApprove, onRecover, onSelect, onRerun, onCancel, onRerender, onText }: any) {
  const { t } = useT()
  const s = run.status
  if (s === "analyzing") return latest ? <div className="vr-row"><button className="vr-ghost sm" onClick={onCancel}>{t("chat.cancel")}</button></div> : null
  if (s === "draft") return null
  if (s === "superseded") return <p className="vr-muted sm">{t("chat.replaced")}</p>
  if (s === "error") return (
    <div className="vr-answer">
      <p className="vr-bad">{t("chat.wrong")} {run.error}</p>
      {latest && <div className="vr-row"><button className="vr-primary sm" onClick={() => onRerun(run.mode || "auto")}>{t("chat.tryAgain")}</button></div>}
    </div>
  )
  if (s === "cancelled" && !run.plan) return <p className="vr-muted">{t("chat.cancelledNothing")}</p>
  if (s === "needs_clarification") return <Ask run={run} live={latest} onAnswer={onAnswer} onRerun={onRerun} />
  if (s === "not_possible") {
    const unsupported = run.unsupported?.length > 0
    return (
      <div className="vr-answer">
        <p><strong>{unsupported ? t("chat.noApp") : t("chat.notInMode")}</strong> {run.suggestion}</p>
        {run.blocking_reasons?.length > 0 && <Fold title={t("chat.whyRuledOut")}><ul>{run.blocking_reasons.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul></Fold>}
        {latest && !unsupported && run.mode !== "auto" && (
          <div className="vr-row">
            <button className="vr-primary sm" onClick={() => onRerun("auto")}>{t("chat.letAutoPick")}</button>
          </div>
        )}
      </div>
    )
  }
  if (!run.plan) return null
  return <PlanAnswer run={run} latest={latest} staged={staged} onApprove={onApprove} onRecover={onRecover} onSelect={onSelect} onRerun={onRerun} onRerender={onRerender} onText={onText} />
}

function Ask({ run, live, onAnswer, onRerun }: any) {
  const { t } = useT()
  const [picked, setPicked] = useState<Record<string, string>>({})
  const answered = Object.keys(run.answers || {}).length
  return (
    <div className="vr-answer">
      {run.questions.map((q: any) => (
        <div key={q.id} className="vr-q">
          <p><strong>{q.question}</strong></p>
          {q.why && <p className="vr-muted sm">{q.why}</p>}
          {q.options?.length > 0 && q.kind !== "upload" && (
            <div className="vr-opts">
              {q.options.map((o: string) => (
                <button key={o} disabled={!live} className={picked[q.id] === o ? "on" : ""} aria-pressed={picked[q.id] === o}
                  onClick={() => {
                    const next = { ...picked, [q.id]: o }
                    setPicked(next)
                    if (run.questions.filter((x: any) => x.kind !== "upload").every((x: any) => next[x.id])) onAnswer(next, false)
                  }}>{o}</button>
              ))}
            </div>
          )}
          {q.kind === "upload" && live && <p className="vr-muted sm">{t("chat.attachHint")}</p>}
        </div>
      ))}
      {live && (
        <div className="vr-row">
          <span className="vr-muted sm">{run.questions.some((q: any) => q.options?.length) ? t("chat.pickOrType") : t("chat.typeBelow")}</span>
          <button className="vr-ghost sm" onClick={() => onAnswer({}, true)}>{t("chat.defaults")}</button>
          {run.mode !== "auto" && <button className="vr-ghost sm" onClick={() => onRerun("auto")}>{t("chat.letAuto")}</button>}
        </div>
      )}
      {!live && answered > 0 && <p className="vr-muted sm">{t("chat.answered")}</p>}
    </div>
  )
}

function PlanAnswer({ run, latest, staged, onApprove, onRecover, onSelect, onRerun, onRerender, onText }: any) {
  const { t } = useT()
  const { plan, apps } = run
  const [accept, setAccept] = useState(false)
  const names = plan.steps.map((s: any) => (s.handoff?.app_name || apps[s.app_id]?.name) || s.app_id)
  const changes = run.plan_changes || []
  const simulated = plan.steps.filter((s: any) => !isConnected(apps[s.app_id]?.adapter)).length
  const failed = run.status === "failed"
  const running = BUSY.has(run.status)
  const done = run.status === "completed"
  return (
    <div className="vr-answer">
      {run.status === "awaiting_approval" && (
        <p>{run.mode === "motion3d" && names.length === 1
          // 3D Motion Video renders on Storyboard & Video: name the mode the user picked, then the app behind it
          ? <>{t("plan.use")} <strong>{t("modes.motion3d")}</strong> ({t("plan.renderedBy", { app: names[0] })}).</>
          : names.length > 1 ? <>{t("plan.combine")} <strong>{names.join(" → ")}</strong>.</> : <>{t("plan.use")} <strong>{names[0]}</strong>.</>} {plan.why}</p>
      )}
      {(running || done || failed) && !run.report && <p>{names.length > 1 ? <>{t("plan.running")} <strong>{names.join(" → ")}</strong>…</> : <>{t("plan.running")} <strong>{names[0]}</strong>…</>}</p>}

      {plan.steps.filter((st: any) => st.finish).slice(0, 1).map((st: any) => (
        <p key={st.step_id} className={`vr-finish ${st.finish.level}`}>
          <span className="vr-chip">{I.spark}{st.finish.level === "full" ? t("plan.effects") : st.finish.level === "documentary" ? t("plan.documentary") : t("plan.clean")}</span>
          <span>{st.finish.summary}</span>
        </p>
      ))}
      {plan.steps.filter((st: any) => st.handoff).map((st: any) => <Handoff key={st.step_id} h={st.handoff} />)}

      {latest && run.options?.length > 1 && run.status === "awaiting_approval" && <Options run={run} onSelect={onSelect} />}

      {(running || done || failed) && (
        <ul className="vr-progress">
          {plan.steps.map((s: any) => {
            const ex = run.execution?.[s.step_id] || {}
            const st = ex.status || "planned"
            return (
              <li key={s.step_id}>
                <StatusDot status={st} />{(s.handoff?.app_name || apps[s.app_id]?.name)}<span>{statusLabel(st, t)}{ex.mock && st === "succeeded" ? t("plan.simulatedSuffix") : ""}</span>
                {st === "running" && ex.message && <em className="vr-live">{ex.message.replace(`${(s.handoff?.app_name || apps[s.app_id]?.name)}: `, "")}</em>}
              </li>
            )
          })}
        </ul>
      )}

      <Fold title={t("plan.title", { n: plan.steps.length })} open={failed}
        meta={typeof plan.confidence === "number" ? t("plan.confidence", { n: Math.round(plan.confidence * 100) }) : undefined}>
        <ol className="vr-pipeline">
          {plan.steps.map((s: any, i: number) => <Step key={s.step_id} step={s} index={i} run={run} onRecover={onRecover} />)}
        </ol>
        {(run.issues || []).filter((x: any) => x.level !== "error").length > 0 && (
          <div><h4>{t("plan.worth")}</h4><ul>{run.issues.filter((x: any) => x.level !== "error").map((x: any, i: number) => <li key={i}>{x.message}</li>)}</ul></div>
        )}
      </Fold>

      {latest && run.status === "awaiting_approval" && (
        <div className="vr-approve">
          {run.uncertainty?.length > 0 && run.mode !== "auto" && (
            <p className="vr-unsure"><strong>{t("plan.unsure")}</strong> {run.uncertainty.join(" ")} <button className="vr-link" onClick={() => onRerun("auto")}>{t("chat.letAuto")}</button></p>
          )}
          {changes.map((c: any, i: number) => <p key={i} className="vr-unsure"><strong>{t("plan.unavailable", { a: c.unavailable, b: c.replacement })}</strong> {c.changes.join(" ")}</p>)}
          {changes.length > 0 && <label className="vr-consent"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /> {t("plan.accept")}</label>}
          <div className="vr-approve-bar">
            <button className="vr-primary" disabled={changes.length > 0 && !accept} onClick={() => onApprove(accept)}>{t("plan.approve")}</button>
            <span className="vr-muted sm">
              {simulated > 0 ? <><span className="vr-chip mock">{t("plan.simulated")}</span> {simulated === plan.steps.length ? t("plan.noneConnected") : t("plan.someConnected", { a: simulated, b: plan.steps.length })}</> : <><span className="vr-chip ok">{t("plan.real")}</span> {t("plan.allConnected")}</>}
              {" "}{t("plan.orType")}
            </span>
          </div>
        </div>
      )}

      {run.report && <Result run={run} staged={staged} onRerender={onRerender} onText={onText} />}
    </div>
  )
}

/** What a follow-up changed compared with the previous turn's understanding. */
function Changes({ run, prev }: { run: Run; prev: Run }) {
  const { t } = useT()
  const a = factMap(prev.understanding), b = factMap(run.understanding)
  if (!run.understanding || !prev.understanding) return null
  const keys = ["Length", "Format", "Structure", "Animated scenes", "Animation", "On top of footage", "Audio", "Not wanted"]
  const diff = keys.filter((k) => a[k] !== b[k] && b[k]).map((k) => a[k] ? `${k} ${a[k]} → ${b[k]}` : `${k} → ${b[k]}`)
  if (!diff.length) return null
  return <p className="vr-changes"><strong>{t("chat.changed")}</strong> {diff.join(" · ")}</p>
}

function factMap(u: any): Record<string, string> {
  return Object.fromEntries(u ? factItems(u) : [])
}

/** The prompt and settings the brain hands the selected app. */
function Handoff({ h }: { h: any }) {
  const { t } = useT()
  const [copied, setCopied] = useState(false)
  const r = h.request || {}
  const st = r.settings || {}
  const settings = [
    ["Length", r.total_duration ?? r.duration ?? st.duration, "s"],
    ["Format", r.aspect_ratio ?? st.aspect, ""],
    ["Scenes", r.scene_count ?? r.scenes, ""],
    ["Language", r.language ?? st.language, ""],
    ["Audio", r.audio_mode ?? st.outputs ?? (r.audio === true ? "native sound" : r.audio === false ? "silent" : null), ""],
    ["References", (r.references || r.source_ids || []).length || null, " files"],
  ].filter(([, v]) => v !== undefined && v !== null && v !== "")
  const copy = async () => {
    try { await navigator.clipboard.writeText(h.prompt); setCopied(true); setTimeout(() => setCopied(false), 1600) } catch { /* select manually */ }
  }
  return (
    <Fold title={t("plan.promptFor", { name: h.app_name })} meta={settings.slice(0, 2).map(([, v, u]) => `${v}${u}`).join(" · ")}>
      <div className="vr-handoff">
        <pre>{h.prompt}</pre>
        <div className="vr-row">
          {settings.map(([k, v, u]) => <span key={k as string} className="vr-chip">{k}: {String(v)}{u}</span>)}
          <button className="vr-ghost sm" onClick={copy}>{copied ? t("nav.copied") : t("plan.copyPrompt")}</button>
        </div>
        {h.notes?.length > 0 && <ul className="vr-muted sm">{h.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
      </div>
    </Fold>
  )
}

function Facts({ u }: { u: any }) {
  const items = factItems(u)
  if (!items.length) return null
  return <dl className="vr-facts">{items.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
}

function factItems(u: any): [string, string][] {
  const items: [string, string][] = []
  if (u.production_type?.length) items.push(["Type", u.production_type.map(human).join(" · ")])
  const sc = u.scene || {}
  if (sc.shot_structure && sc.shot_structure !== "unspecified")
    items.push(["Structure", sc.shot_structure === "multi_scene" ? `Multi-scene${sc.scene_count ? ` · ${sc.scene_count} scenes` : ""}` : "One continuous shot"])
  const c = u.constraints || {}
  if (c.duration_s) items.push(["Length", `${c.duration_s} s`])
  if (c.aspect_ratio) items.push(["Format", c.aspect_ratio])
  const m = u.motion || {}
  if (m.programmatic_elements?.length) {
    const over = new Set(m.overlay_elements || [])
    const own = m.programmatic_elements.filter((e: string) => !over.has(e))
    if (own.length) items.push([m.integration === "separate_segments" ? "Animated scenes" : "Animation", own.map(human).join(", ")])
    if (over.size) items.push(["On top of footage", [...over].map((e: any) => human(e)).join(", ")])
  }
  const a = u.audio || {}
  const req = Object.entries(a).filter(([, v]) => v === "requested").map(([k]) => human(k))
  const exc = Object.entries(a).filter(([, v]) => v === "excluded").map(([k]) => human(k))
  if (req.length) items.push(["Audio", req.join(", ")])
  if (exc.length || c.exclusions?.length) items.push(["Not wanted", [...exc, ...(c.exclusions || [])].join(", ")])
  if (sc.consistency?.length) items.push(["Keep consistent", sc.consistency.join(", ")])
  return items
}

function Options({ run, onSelect }: any) {
  const { t } = useT()
  return (
    <div className="vr-options">
      {run.options.map((o: any) => {
        const sel = run.selected_option === o.id
        return (
          <button key={o.id} className={`vr-option ${sel ? "sel" : ""}`} disabled={sel} aria-pressed={sel} onClick={() => onSelect(o.id)}>
            <span className="vr-option-top">
              <span className="vr-muted sm">{o.approach}</span>
              {o.recommended && <span className="vr-chip accent">{t("plan.recommended")}</span>}
              {sel && <span className="vr-chip ok">{t("plan.selected")}</span>}
            </span>
            <strong>{o.names.join(" → ")}</strong>
            <span className="sm">{o.why}</span>
          </button>
        )
      })}
    </div>
  )
}

function Step({ step, index, run, onRecover }: any) {
  const { t } = useT()
  const app = run.apps[step.app_id]
  const ex = run.execution?.[step.step_id] || {}
  const status = ex.status || "planned"
  const [consent, setConsent] = useState<Record<string, boolean>>({})
  const fbs = ex.recovery?.fallbacks || []
  const src = (i: any) => i.source === "prompt" ? t("plan.yourRequest") : i.source.startsWith("asset:")
    ? run.assets[i.source.slice(6)]?.filename || i.source
    : t("plan.fromStep", { n: run.plan.steps.findIndex((s: any) => s.step_id === i.source.slice(5)) + 1 })
  const uses = step.inputs.filter((i: any) => i.use)
  return (
    <li className={`vr-step st-${status}`}>
      <span className="vr-step-n" aria-hidden>{status === "succeeded" ? I.check : index + 1}</span>
      <div className="vr-step-body">
        <div className="vr-step-top">
          <strong>{app?.name || step.app_id}</strong>
          {(ex.mock && status === "succeeded") || !isConnected(app?.adapter)
            ? <span className="vr-chip mock">{t("plan.simulated")}</span> : <span className="vr-chip ok">{t("plan.connected")}</span>}
          {!app?.verified && <span className="vr-chip" title={t("plan.unverifiedHint")}>{t("plan.unverified")}</span>}
        </div>
        <p className="sm">{step.responsibility}</p>
        <p className="vr-muted sm">{t("plan.gets", { x: step.inputs.map(src).join(", ") || t("plan.nothing") })}</p>
        {uses.length > 0 && <ul className="vr-uses">{uses.map((i: any) => <li key={i.source}><strong>{src(i)}:</strong> {i.use}</li>)}</ul>}
        {step.instructions && <p className="vr-rule">{step.instructions}</p>}
        {ex.message && <p className={status === "failed" ? "vr-bad sm" : "vr-muted sm"}>{ex.message}</p>}
        {status === "failed" && run.status === "failed" && (
          <div className="vr-row">
            <button className="vr-ghost sm" onClick={() => onRecover(step.step_id)}>{t("plan.retry")}</button>
            {fbs.filter((f: any) => f.classification !== "invalid").map((f: any) => (
              <span key={f.app_id} className="vr-fb">
                {f.classification === "changes_result" && (
                  <label className="vr-consent sm"><input type="checkbox" checked={!!consent[f.app_id]} onChange={(e) => setConsent({ ...consent, [f.app_id]: e.target.checked })} /> {t("plan.acceptPrefix")} {f.changes.join("; ")}</label>
                )}
                <button className="vr-ghost sm" disabled={f.classification === "changes_result" && !consent[f.app_id]}
                  onClick={() => onRecover(step.step_id, f.app_id, !!consent[f.app_id])}>{t("plan.useApp", { name: f.name })}</button>
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

/** The run's final real video step and its output as a local URL (null while loading or when there is none). */
function useRunVideo(run: Run) {
  const real = (run.report?.steps || []).filter((s: any) => !s.mock && s.status === "succeeded" && s.outputs?.length)
  const last = real[real.length - 1]
  const [url, setUrl] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!last) return
    let u: string | null = null
    let alive = true
    api.outputUrl(run.id, last.step_id)
      .then((x) => { u = x; if (alive) setUrl(x); else URL.revokeObjectURL(x) })
      .catch((e) => { if (alive) setErr(e.message) })
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [run.id, last?.step_id, last?.outputs?.[0]?.version])
  return { last, url, err }
}

/** The finished video's editable text layer, when its words were kept out of the pixels (presenter, 3D motion). */
function textLayerOf(run: Run, last: any) {
  const o = last?.outputs?.[0]
  return o?.text_layer?.items && o?.has_clean ? o.text_layer : null
}
const textBusy = (run: Run) => run.status === "executing" && !!run.rerender?.text

function Result({ run, staged, onRerender, onText }: { run: Run; staged?: boolean; onRerender?: (n: number, how: string, sid?: string) => void
  onText?: (sid: string, items: any[]) => void }) {
  const { t } = useT()
  const r = run.report
  const { last, url, err } = useRunVideo(run)
  if (run.status === "cancelled") {
    return <div className="vr-result bad"><p><strong>{t("result.cancelled")}</strong> {t("result.cancelledBody")}</p><UsagePanel run={run} /></div>
  }
  if (staged) {
    return (
      <div className={`vr-result ${r.success ? "ok" : r.mock ? "mock" : "bad"}`}>
        <p><strong>{r.success ? t("result.ready") : r.mock ? t("result.sim") : t("result.notFinished")}</strong>{" "}
          {r.success ? t("result.playing") : r.mock ? t("result.simBody") : t("result.failBody")}</p>
        {last && <SceneList run={run} last={last} onRerender={onRerender} />}
        <UsagePanel run={run} />
      </div>
    )
  }
  return (
    <div className={`vr-result ${r.success ? "ok" : r.mock ? "mock" : "bad"}`}>
      <p><strong>{r.success ? t("result.ready") : r.mock ? t("result.sim") : t("result.notFinished")}</strong>{" "}
        {r.success ? "" : r.mock ? t("result.simBody") : t("result.failBody")}</p>
      {last?.outputs?.[0]?.review?.score != null && (
        <p className="vr-muted sm">{last.outputs[0].genre ? `${last.outputs[0].genre} · ` : ""}Story review {last.outputs[0].review.score}/100
          {last.outputs[0].review.first_score != null ? ` (rewritten from ${last.outputs[0].review.first_score})` : ""}</p>
      )}
      {last?.outputs?.[0]?.invented_specs?.length > 0 && (
        <p className="vr-unsure"><strong>Check these numbers before publishing.</strong> The graphics show values your request
          did not give, so the AI proposed them: {last.outputs[0].invented_specs.join("; ")}. Send the real ones as a follow-up to replace them.</p>
      )}
      {last?.outputs?.[0]?.graphics_error && <p className="vr-muted sm">Graphics were skipped: {last.outputs[0].graphics_error}</p>}
      {last?.outputs?.[0]?.graphics_note && <p className="vr-muted sm">{last.outputs[0].graphics_note}</p>}
      {last?.outputs?.[0]?.title && <p className="vr-film-title">“{last.outputs[0].title}”{last.outputs[0].story ? <span> {last.outputs[0].story}</span> : null}</p>}
      {last && textLayerOf(run, last) && onText ? (
        <EditableVideo runId={run.id} stepId={last.step_id} layer={textLayerOf(run, last)} finalUrl={url}
          busy={textBusy(run)} onSave={(items) => onText(last.step_id, items)} />
      ) : last && (
        <div className="vr-video">
          {url ? <video controls preload="metadata" src={url} /> : err ? <p className="vr-bad sm">{err}</p> : <div className="vr-loading"><span className="vr-spin" /> {t("result.loadingVideo")}</div>}
          {url && <a className="vr-ghost sm" href={url} download={`videostudio-${run.id}.mp4`}>{t("result.downloadVideo")}</a>}
        </div>
      )}
      {last && <SceneList run={run} last={last} onRerender={onRerender} />}
      <UsagePanel run={run} />
    </div>
  )
}

/** LLM calls and the paid generations (images, clips, voice lines) this run used. */
function UsagePanel({ run }: { run: Run }) {
  const u = run.usage
  if (!u || (!u.calls && !u.generated)) return null
  const g = u.generated || {}
  const tok = (u.prompt_tokens || 0) + (u.completion_tokens || 0)
  return (
    <p className="vr-muted sm vr-usage">
      AI usage: {u.calls} LLM call{u.calls === 1 ? "" : "s"}{tok ? ` · ${tok.toLocaleString()} tokens` : ""}
      {g.image ? ` · ${g.image} image${g.image > 1 ? "s" : ""}` : ""}{g.clip ? ` · ${g.clip} clip${g.clip > 1 ? "s" : ""}` : ""}
      {g.voice ? ` · ${g.voice} voice line${g.voice > 1 ? "s" : ""}` : ""}
      {u.estimated_cost_usd != null ? ` · about $${u.estimated_cost_usd}` : ""}
      {g.models?.length ? <span title={g.models.join(", ")}> · models: {g.models.join(", ")}</span> : null}
    </p>
  )
}

/** A scene's first frame from app storage when it was kept there (the provider's link expires), else the link. */
function SceneImage({ run, stepId, n, sc }: { run: Run; stepId: string; n: number; sc: any }) {
  const [src, setSrc] = useState<string | null>(sc.stored?.includes("still") ? null : sc.still_url)
  useEffect(() => {
    if (!sc.stored?.includes("still")) { setSrc(sc.still_url); return }
    let u: string | null = null, alive = true
    api.sceneMediaUrl(run.id, stepId, n, "still").then((x) => { u = x; if (alive) setSrc(x) }).catch(() => alive && setSrc(sc.still_url))
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [run.id, stepId, n, sc.still_url, sc.stored?.join(",")])
  return src ? <img src={src} alt={sc.title} loading="lazy" /> : <div className="vr-skel" aria-hidden />
}

function SceneList({ run, last, onRerender }: { run: Run; last: any; onRerender?: (n: number, how: string, sid?: string) => void }) {
  const { t } = useT()
  const o = last.outputs?.[0]
  const [open, setOpen] = useState<number | null>(null)
  const [how, setHow] = useState("")
  if (!o?.scenes?.length) return null
  const canRerender = !!onRerender && run.status === "completed" && o.spans?.length === o.scenes.length
  return (
    <Fold title={`${t("result.scenes")} · ${o.scenes.length}`} meta={`made by ${last.app}${o.version ? ` · version ${o.version + 1}` : ""}`}>
      <div className="vr-scenes">
        {o.scenes.map((sc: any, i: number) => (
          <figure key={`${i}-${sc.rerendered || 0}`}>
            <SceneImage run={run} stepId={last.step_id} n={i + 1} sc={sc} />
            <figcaption><strong>{i + 1}. {sc.title}</strong>{sc.beat && <span>{sc.beat}</span>}<span>{sc.duration} s · {sc.model}{sc.rerendered ? ` · new take ${sc.rerendered}` : ""}</span>
              <SceneCheck qa={sc.qa} /></figcaption>
            {(sc.image_prompt || sc.motion_prompt || sc.action) && (
              <details className="vr-scene-prompt">
                <summary>{t("result.scenePrompt")}</summary>
                {sc.action && <p><b>Action</b> {sc.action}</p>}
                {sc.props && <p><b>Objects</b> {Array.isArray(sc.props) ? sc.props.join("; ") : sc.props}</p>}
                {sc.image_prompt && <p><b>First frame</b> {sc.image_prompt}</p>}
                {sc.motion_prompt && <p><b>Motion</b> {sc.motion_prompt}</p>}
              </details>
            )}
            {canRerender && (open === i ? (
              <div className="vr-story-form">
                <textarea rows={2} value={how} placeholder={t("result.whatChange")} onChange={(e) => setHow(e.target.value)} />
                <button className="vr-cta sm" onClick={() => { onRerender!(i + 1, how, last.step_id); setOpen(null); setHow("") }}>{t("result.newTake")}</button>
              </div>
            ) : <button className="vr-ghost sm" onClick={() => setOpen(i)}>{t("result.reRender")}</button>)}
          </figure>
        ))}
      </div>
    </Fold>
  )
}

/** The AI check of a scene's first and last frame: passed, fixed by a redraw, or what is still wrong. */
function SceneCheck({ qa }: { qa?: Record<string, { issues?: string[]; redrawn?: boolean; retaken?: boolean; fixed?: string[] }> }) {
  const { t } = useT()
  if (!qa || !Object.keys(qa).length) return null
  const left = Object.values(qa).flatMap(r => r.issues || [])
  const fixed = Object.values(qa).flatMap(r => (r.redrawn || r.retaken ? r.fixed || [] : []))
  if (!left.length) return <span className="vr-good">{t("result.checkPassed")}{fixed.length ? ` · fixed ${fixed.length}` : ""}</span>
  return <span className="vr-qa-bad" title={left.join("\n")}>{t("result.checkFail")} {left[0]}{left.length > 1 ? ` (+${left.length - 1})` : ""}</span>
}

/* ------------------------------------------------------------------ stage: the video being made, right of the chat */
const RATIO: Record<string, string> = { "16:9": "16 / 9", "9:16": "9 / 16", "1:1": "1 / 1", "4:5": "4 / 5", "4:3": "4 / 3", "3:4": "3 / 4", "21:9": "21 / 9" }

/** Scene count and frame shape the plan asked for, so the skeleton matches the video that is coming. */
function stageShape(run: Run): { scenes: number; ratio: string; vertical: boolean; wh: number } {
  const req = (run.plan?.steps || []).map((s: any) => s.handoff?.request || {}).find((r: any) => r.scene_count || r.scenes || r.aspect_ratio) || {}
  const u = run.understanding || {}
  const n = Number(req.scene_count ?? req.scenes ?? u.scene?.scene_count) || (u.scene?.shot_structure === "single_shot" ? 1 : 4)
  const ar = String(req.aspect_ratio ?? req.settings?.aspect ?? u.constraints?.aspect_ratio ?? "16:9")
  const [w, h] = ar.split(":").map(Number)
  return { scenes: Math.min(Math.max(n, 1), 60), ratio: RATIO[ar] || (w && h ? `${w} / ${h}` : "16 / 9"), vertical: !!(w && h && h > w),
           wh: w && h ? w / h : 16 / 9 }
}

/** One scene card with the real result: its clip (plays on hover; the newest plays by itself) or its still. */
function SceneMedia({ sc, index, ratio, pending, picked, onPick }: { sc: any; index: number; ratio: string; pending: boolean;
                                                                   picked?: boolean; onPick?: () => void }) {
  const ref = useRef<HTMLVideoElement>(null)
  return (
    <span className={`vr-scene-media ${onPick ? "pickable" : ""} ${picked ? "picked" : ""}`} style={{ aspectRatio: ratio }}
      role={onPick ? "button" : undefined} tabIndex={onPick ? 0 : undefined} aria-pressed={onPick ? !!picked : undefined}
      title={onPick ? `Play scene ${index + 1} in the big screen` : undefined} onClick={onPick}
      onKeyDown={(e) => { if (onPick && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onPick() } }}>
      {sc.clip_url
        ? <video ref={ref} src={sc.clip_url} poster={sc.still_url || undefined} muted loop playsInline preload="metadata"
            onMouseEnter={() => ref.current?.play().catch(() => {})} onMouseLeave={() => ref.current?.pause()} />
        : <img src={sc.still_url} alt={sc.title || `Scene ${index + 1}`} loading="lazy" />}
      {sc.clip_url && <span className="vr-thumb-badge">{I.play}</span>}
      {pending && <span className="vr-scene-pending"><span className="vr-spin light" />clip</span>}
    </span>
  )
}

function Stage({ run, onCancel, onText }: { run: Run; onCancel?: () => void; onText?: (sid: string, items: any[]) => void }) {
  const { t } = useT()
  const { plan, apps } = run
  const working = BUSY.has(run.status)
  const { last, url, err } = useRunVideo(run)
  const out = last?.outputs?.[0] || {}
  // What the engine has really made so far (stills, then clips), until the finished video replaces it.
  const live = (plan.steps.map((s: any) => run.execution?.[s.step_id]?.live).filter(Boolean).pop() || {}) as any
  const scenes: any[] = out.scenes?.length ? out.scenes : live.scenes || []
  const madeClips = scenes.filter((x) => x?.clip_url).length
  const madeStills = scenes.filter((x) => x?.still_url).length
  const preview = [...scenes].reverse().find((x) => x?.clip_url) || [...scenes].reverse().find((x) => x?.still_url)
  const shape = stageShape(run)
  // A scene card the user clicked plays in the big screen (click it again, or "Back to the film", to return).
  const [pickedAt, setPickedAt] = useState<number | null>(null)
  // The finished film (or another run) always takes the big screen back from a picked scene.
  useEffect(() => { setPickedAt(null) }, [run.id, url])
  const picked = pickedAt != null && (scenes[pickedAt]?.clip_url || scenes[pickedAt]?.still_url) ? scenes[pickedAt] : null
  // An explicit width: with only aspect-ratio + max-height the grid reserved less room than the screen drew, so the
  // picture spilled behind the scene cards.
  const screenStyle = shape.vertical ? { aspectRatio: shape.ratio }
    : { aspectRatio: shape.ratio, width: `min(100%, calc(58vh * ${shape.wh.toFixed(4)}))` }
  const names = plan.steps.map((s: any) => (s.handoff?.app_name || apps[s.app_id]?.name) || s.app_id)
  const steps = plan.steps.map((s: any) => ({ s, ex: run.execution?.[s.step_id] || {} }))
  const doneSteps = steps.filter(({ ex }: any) => ex.status === "succeeded").length
  const current = steps.find(({ ex }: any) => ex.status === "running")
  const liveMsg = current?.ex.message?.replace(`${(current.s.handoff?.app_name || apps[current.s.app_id]?.name)}: `, "")
  const r = run.report
  const pct = working ? Math.round(((doneSteps + (current ? 0.5 : 0)) / Math.max(steps.length, 1)) * 100) : 100
  // The app's progress says "scene 4/13 …": the real total (the plan's count can differ) and how far it got.
  const sceneProg = /scene\s+(\d+)\s*\/\s*(\d+)/i.exec(liveMsg || "")
  const sceneNow = sceneProg ? Number(sceneProg[1]) : 0
  const sceneSlots = scenes.length || (sceneProg ? Math.min(Number(sceneProg[2]), 60) : shape.scenes)
  const madeLabel = !working ? `${sceneSlots}` : madeClips ? t("studio.clipsMade", { a: madeClips, b: sceneSlots })
    : madeStills ? t("studio.imagesMade", { a: madeStills, b: sceneSlots }) : sceneNow ? t("studio.made", { a: sceneNow - 1, b: sceneSlots }) : `${sceneSlots}`

  return (
    <aside className="vr-stage" aria-label={t("studio.yourVideo")} aria-busy={working}>
      <div className="vr-stage-head">
        <div>
          <span className="vr-stage-kicker"><StatusDot status={working ? "running" : run.status} />{working ? t("studio.making") : statusLabel(run.status, t)}</span>
          <h2>{out.title || live.title || names.join(" → ")}</h2>
        </div>
        {working && onCancel && <button className="vr-ghost sm" onClick={onCancel} disabled={!!run.cancel_requested}>{run.cancel_requested ? t("studio.stopping") : t("studio.cancel")}</button>}
        {!working && url && <a className="vr-ghost sm" href={url} download={`videostudio-${run.id}.mp4`}>{t("studio.download")}</a>}
      </div>

      {last && textLayerOf(run, last) && onText && (!working || textBusy(run)) ? (
        <EditableVideo runId={run.id} stepId={last.step_id} layer={textLayerOf(run, last)} finalUrl={url}
          busy={textBusy(run)} onSave={(items) => onText(last.step_id, items)} fill />
      ) : <div className={`vr-screen ${shape.vertical ? "vertical" : ""}`} style={screenStyle}>
        {picked ? (
          <div className="vr-screen-live">
            {picked.clip_url ? <video key={picked.clip_url} src={picked.clip_url} poster={picked.still_url || undefined} controls autoPlay playsInline />
              : <img src={picked.still_url} alt={picked.title || `Scene ${(pickedAt ?? 0) + 1}`} />}
            <button className="vr-ghost sm vr-screen-back" onClick={() => setPickedAt(null)}>
              {`Scene ${(pickedAt ?? 0) + 1}${picked.clip_url ? "" : " (image)"} · Back to the film`}
            </button>
          </div>
        ) : url ? (
          <video controls autoPlay muted playsInline preload="metadata" src={url} />
        ) : working && (preview || live.presenter_url) ? (
          <div className="vr-screen-live">
            {preview?.clip_url ? <video key={preview.clip_url} src={preview.clip_url} poster={preview.still_url || undefined} autoPlay muted loop playsInline />
              : <img src={preview?.still_url || live.presenter_url} alt={preview?.title || "Presenter"} />}
            <div className="vr-screen-info overlay">
              <span className="vr-spin light" />
              <strong>{liveMsg || t("studio.rendering")}</strong>
              <span className="sm">{preview ? `Preview: ${preview.title}${preview.clip_url ? " (raw clip, before effects)" : " (image, the clip comes next)"}` : t("studio.presenter")}</span>
            </div>
          </div>
        ) : working || (last && !err) ? (
          <div className="vr-screen-skel vr-skeleton">
            <div className="vr-screen-info">
              <span className="vr-spin" />
              <strong>{working ? (liveMsg || (run.status === "approved" ? t("studio.starting") : t("studio.rendering"))) : t("result.loadingVideo")}</strong>
              {working && <span className="vr-muted sm">{t("studio.step", { a: Math.min(doneSteps + 1, steps.length), b: steps.length })} · {names[Math.min(doneSteps, names.length - 1)]}</span>}
            </div>
          </div>
        ) : (
          <div className="vr-screen-empty">
            <strong>{err ? t("studio.couldNotLoad") : r?.mock ? t("studio.simTitle") : t("studio.noVideo")}</strong>
            <span className="vr-muted sm">{err || (r?.mock ? t("studio.notConnected")
              : `${(steps.find(({ ex }: any) => ex.status === "failed")?.ex.message || "").replace(/\s*Retry this step\.?$/, "")} ${t("studio.retryHint")}`.trim())}</span>
          </div>
        )}
        {working && <span className="vr-screen-bar" style={{ width: `${pct}%` }} aria-hidden />}
      </div>}

      <div className="vr-stage-scenes">
        <h3>{t("studio.scenes")} <span className="vr-muted">· {madeLabel}</span></h3>
        <div className={`vr-scene-row ${shape.vertical ? "vertical" : ""}`}>
          {Array.from({ length: sceneSlots }).map((_, i) => {
            const sc = scenes[i]
            return (
              <figure key={i} className="vr-scene">
                {sc?.clip_url || sc?.still_url
                  ? <SceneMedia sc={sc} index={i} ratio={shape.ratio} pending={working && !sc.clip_url} picked={pickedAt === i}
                      onPick={() => setPickedAt(pickedAt === i ? null : i)} />
                  : <span className={`vr-scene-skel ${working ? "vr-skeleton" : ""} ${working && i + 1 < sceneNow ? "made" : ""}`} style={{ aspectRatio: shape.ratio, animationDelay: `${i * 0.12}s` }}>
                      {working && i + 1 < sceneNow && <span className="vr-scene-mark" aria-label="Made">{I.check}</span>}
                      {working && i + 1 === sceneNow && <span className="vr-spin" aria-label="Making now" />}
                    </span>}
                <figcaption>
                  {sc ? <><strong>{i + 1}. {sc.title}</strong><span>{sc.duration} s{sc.model ? ` · ${sc.model}` : ""}</span></>
                    : <><span className={`vr-line ${working ? "vr-skeleton" : ""}`} /><span className={`vr-line short ${working ? "vr-skeleton" : ""}`} /></>}
                </figcaption>
              </figure>
            )
          })}
        </div>
      </div>

      <ul className="vr-stage-steps">
        {steps.map(({ s, ex }: any) => (
          <li key={s.step_id}><StatusDot status={ex.status || "planned"} />{(s.handoff?.app_name || apps[s.app_id]?.name) || s.app_id}<span>{statusLabel(ex.status || "planned", t)}{ex.mock && ex.status === "succeeded" ? t("plan.simulatedSuffix") : ""}</span></li>
        ))}
      </ul>
    </aside>
  )
}

/* ------------------------------------------------------------------ top bar: theme + language toggles, account */
/** One-tap switches for light/dark and English/Korean (they ask Palette OS to change its setting). */
function QuickToggles() {
  const platform: any = usePlatform()
  const { t, language, setLanguage } = useT()
  const dark = platform?.colorMode === "dark"
  return (
    <div className="vr-toggles">
      <button className="vr-icon-btn" onClick={() => platform?.setColorMode?.(dark ? "light" : "dark")}
        aria-label={t("nav.toggleTheme", { mode: dark ? t("nav.light") : t("nav.dark") })}
        title={t("nav.toggleTheme", { mode: dark ? t("nav.light") : t("nav.dark") })}>
        {dark ? I.sun : I.moon}
      </button>
      <div className="vr-seg" role="group" aria-label={t("nav.toggleLang")}>
        {(["en", "ko"] as const).map((l) => (
          <button key={l} className={language === l ? "on" : ""} aria-pressed={language === l} onClick={() => setLanguage(l)}>
            {l === "en" ? "EN" : "한"}
          </button>
        ))}
      </div>
    </div>
  )
}

/** Who is signed in and in which organization (from usePlatform), plus the same toggles as a labelled list. */
function AccountMenu() {
  const platform: any = usePlatform()
  const { t, language, setLanguage } = useT()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState("")
  const ref = useRef<HTMLDivElement>(null)
  const user = platform?.user || {}
  const orgId = platform?.organizationId
  const org = (platform?.orgs || []).find((o: any) => o.id === orgId)
  const dark = platform?.colorMode === "dark"
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", close)
    document.addEventListener("keydown", esc)
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc) }
  }, [open])
  const copy = async (key: string, value: string) => {
    try { await navigator.clipboard.writeText(value); setCopied(key); setTimeout(() => setCopied(""), 1400) } catch { /* select manually */ }
  }
  const initial = String(user.name || user.email || "?").trim().charAt(0).toUpperCase()
  const idRow = (key: string, label: string, value: any) => (
    <div className="vr-acct-row">
      <dt>{label}</dt>
      <dd><code title={String(value ?? "—")}>{value ?? "—"}</code>
        {value != null && <button className="vr-h-btn" onClick={() => copy(key, String(value))}>{copied === key ? t("nav.copied") : t("nav.copy")}</button>}
      </dd>
    </div>
  )
  return (
    <div className="vr-acct" ref={ref}>
      <button className="vr-avatar-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog" aria-label={t("nav.account")}>
        {initial !== "?" ? initial : I.user}
      </button>
      {open && (
        <div className="vr-acct-pop" role="dialog" aria-label={t("nav.account")}>
          <div className="vr-acct-head">
            <span className="vr-avatar-btn lg" aria-hidden>{initial !== "?" ? initial : I.user}</span>
            <div><strong>{user.name || "—"}</strong><span>{user.email || ""}</span></div>
          </div>
          <dl className="vr-acct-ids">
            {idRow("user", t("nav.userId"), user.id)}
            {idRow("org", t("nav.orgId"), orgId)}
            {(org?.name || platform?.orgRole) && (
              <div className="vr-acct-row"><dt>{org?.name || t("nav.role")}</dt><dd><span className="vr-chip">{platform?.orgRole || "—"}</span></dd></div>
            )}
          </dl>
          <div className="vr-acct-set">
            <span>{t("nav.theme")}</span>
            <div className="vr-seg">
              <button className={!dark ? "on" : ""} aria-pressed={!dark} onClick={() => platform?.setColorMode?.("light")}>{I.sun}{t("nav.light")}</button>
              <button className={dark ? "on" : ""} aria-pressed={dark} onClick={() => platform?.setColorMode?.("dark")}>{I.moon}{t("nav.dark")}</button>
            </div>
          </div>
          <div className="vr-acct-set">
            <span>{t("nav.language")}</span>
            <div className="vr-seg">
              <button className={language === "en" ? "on" : ""} aria-pressed={language === "en"} onClick={() => setLanguage("en")}>English</button>
              <button className={language === "ko" ? "on" : ""} aria-pressed={language === "ko"} onClick={() => setLanguage("ko")}>한국어</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
