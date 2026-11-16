"use client"

import { PluginProvider, type PluginComponentProps } from "@palettelab/sdk"
import App from "./App"
import "./styles.css"

// Geometric display face; the app falls back to the system font if the sandbox blocks it.
if (typeof document !== "undefined" && !document.getElementById("pv-font")) {
  try {
    const link = document.createElement("link")
    link.id = "pv-font"
    link.rel = "stylesheet"
    link.href = "https://fonts.googleapis.com/css2?family=Urbanist:wght@400;500;600;700;800&display=swap"
    document.head.appendChild(link)
  } catch { /* system font */ }
}

export default function VideoRouter(props: PluginComponentProps) {
  return (
    <PluginProvider value={props.platform}>
      <App />
    </PluginProvider>
  )
}
