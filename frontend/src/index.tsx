"use client"

import { useEffect, useMemo, useState } from "react"
import { PluginProvider, normalizePaletteLanguage, type PluginComponentProps } from "@palettelab/sdk"
import App from "./App"
import "./styles.css"

// Geometric display face; the app falls back to the system font if the sandbox blocks it.
if (typeof document !== "undefined" && !document.getElementById("pv-font")) {
  try {
    const link = document.createElement("link")
    link.id = "pv-font"
    link.rel = "stylesheet"
    link.href = "https://fonts.googleapis.com/css2?family=Urbanist:wght@400;500;600;700;800&family=Noto+Sans+KR:wght@400;500;600;700&display=swap"
    document.head.appendChild(link)
  } catch { /* system font */ }
}

type Mode = "light" | "dark"
const LANGS = ["en", "ko"]

/* Palette OS owns language and light/dark mode: the toggles call its setLanguage / setColorMode.
   The choice is also held here so the app switches at once, even in a runtime that does not echo
   the change back (older hosts, `pltt dev`). A new value from the OS always wins. */
export default function VideoRouter(props: PluginComponentProps) {
  const p = props.platform
  const osLang = normalizePaletteLanguage(p?.language, "en")
  const osMode: Mode = p?.colorMode === "dark" ? "dark" : "light"
  const [language, setLang] = useState(LANGS.includes(osLang) ? osLang : "en")
  const [colorMode, setMode] = useState<Mode>(osMode)
  useEffect(() => { if (LANGS.includes(osLang)) setLang(osLang) }, [osLang])
  useEffect(() => { setMode(osMode) }, [osMode])

  const platform = useMemo(() => ({
    ...p,
    language,
    supportedLanguages: LANGS,
    colorMode,
    setLanguage: (l: string) => {
      setLang(l)
      try { p?.setLanguage?.(l) } catch { /* local only */ }
    },
    setColorMode: (m: Mode) => {
      setMode(m)
      try { p?.setColorMode?.(m) } catch { /* local only */ }
    },
  }), [p, language, colorMode])

  return (
    <PluginProvider value={platform}>
      <App />
    </PluginProvider>
  )
}
