const BASE = "/api/v1/plugins/palette-video"

/* Requests go through the platform's own apiFetch (usePlatform().apiFetch): it carries the session and app
   identity in Palette OS, the sandbox bridge in iframe runtime, and the local backend under `pltt dev`.
   (The SDK's standalone apiFetch assumes a fixed base URL, so it is not used here.) */
type Fetcher = (path: string, init?: RequestInit) => Promise<Response>
let fetcher: Fetcher | null = null
export function bindPlatform(platform: any) {
  if (platform?.apiFetch) fetcher = platform.apiFetch
}
function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!fetcher) return Promise.reject(new Error("Palette platform context is not ready yet."))
  return fetcher(path, init)
}

export type Run = any
/** The composer's Length / Format picks (empty = Auto: read from the prompt). */
export type Settings = { duration_s?: number; aspect_ratio?: string }

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    const d = body?.detail
    if (typeof d === "string") return d
    if (d?.message) return d.message
    return body?.message || `Request failed (${res.status})`
  } catch {
    return `Request failed (${res.status})`
  }
}

async function call<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(`${BASE}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...(init?.headers || {}) } : init?.headers,
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

const post = (path: string, body?: unknown) =>
  call(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => call("/health"),
  conversations: () => call<any[]>("/conversations"),
  conversation: (id: string) => call<{ id: string; runs: Run[] }>(`/conversations/${id}`),
  removeConversation: (id: string) => call(`/conversations/${id}`, { method: "DELETE" }),
  run: (id: string) => call<Run>(`/runs/${id}`),

  /** Create the run (a follow-up when parentRun is set), upload each file, then start the analysis job. */
  async create(prompt: string, mode: string, files: File[], onFile?: (i: number) => void, parentRun?: string,
               settings?: Settings): Promise<Run> {
    const run = await post("/runs", { prompt, mode, parent_run: parentRun || null,
                                      duration_s: settings?.duration_s || null, aspect_ratio: settings?.aspect_ratio || null })
    for (let i = 0; i < files.length; i++) {
      onFile?.(i)
      await uploadFile(run.id, files[i])
    }
    return post(`/runs/${run.id}/start`)
  },

  async clarify(id: string, answers: Record<string, string>, files: File[], proceed: boolean): Promise<Run> {
    for (const f of files) await uploadFile(id, f)
    return post(`/runs/${id}/clarify`, { answers, proceed_with_defaults: proceed })
  },

  rerun: (id: string, mode: string) => post(`/runs/${id}/rerun`, { mode }),
  /** Story Intelligence: change one scene before approval (the rest of the story stays). */
  editScene: (id: string, n: number, fields: Record<string, unknown>) => post(`/runs/${id}/story/scenes/${n}`, { fields }),
  switchPalette: (id: string, option: number) => post(`/runs/${id}/story/palette`, { option }),
  switchMotionEngine: (id: string, engine: string) => post(`/runs/${id}/story/motion-engine`, { engine }),
  /** Stop a run that is analysing or rendering (what was made so far is kept). */
  cancel: (id: string) => post(`/runs/${id}/cancel`),
  /** A new take of one scene of a finished film, spliced in; the rest of the film stays. */
  rerenderScene: (id: string, n: number, instruction: string, stepId?: string) =>
    post(`/runs/${id}/scenes/${n}/rerender`, { instruction, step_id: stepId || null }),
  regenerateScene: (id: string, n: number, instruction: string) =>
    post(`/runs/${id}/story/scenes/${n}/regenerate`, { instruction }),
  select: (id: string, optionId: string) => post(`/runs/${id}/select`, { option_id: optionId }),
  approve: (id: string, revision: number, accept: boolean) =>
    post(`/runs/${id}/approve`, { plan_revision: revision, accept_changes: accept }),
  recover: (id: string, stepId: string, appId?: string, accept = false) =>
    post(`/runs/${id}/recover`, { step_id: stepId, app_id: appId || null, accept_changes: accept }),

  /** The produced video as a local object URL (fetched with the app's auth). */
  async thumbnailUrl(id: string, stepId: string): Promise<string> {
    const res = await apiFetch(`${BASE}/runs/${id}/thumbnail/${stepId}`)
    if (!res.ok) throw new Error(await readError(res))
    return URL.createObjectURL(await res.blob())
  },
  /** A file the user uploaded, as a local object URL (fetched with the app's auth). */
  async fileUrl(id: string, assetId: string): Promise<string> {
    const res = await apiFetch(`${BASE}/runs/${id}/files/${assetId}`)
    if (!res.ok) throw new Error(await readError(res))
    return URL.createObjectURL(await res.blob())
  },
  /** A scene's stored first frame or clip (kept in app storage; the provider's own links expire). */
  async sceneMediaUrl(id: string, stepId: string, n: number, kind: "still" | "clip"): Promise<string> {
    const res = await apiFetch(`${BASE}/runs/${id}/scenes/${stepId}/${n}/${kind}`)
    if (!res.ok) throw new Error(await readError(res))
    return URL.createObjectURL(await res.blob())
  },
  /** Save the edited words; the server renders them onto the clean video again (a new version of the video). */
  saveText: (id: string, stepId: string, items: unknown[]) => post(`/runs/${id}/text`, { step_id: stepId, items }),
  /** The video without its text layer (to edit the words on, or to download for another editor). */
  async cleanUrl(id: string, stepId: string): Promise<string> {
    const res = await apiFetch(`${BASE}/runs/${id}/clean/${stepId}`)
    if (!res.ok) throw new Error(await readError(res))
    return URL.createObjectURL(await res.blob())
  },
  /** 3D Render: one of the step's related files (reference, depth, normal, mask_characters, mask_objects, final,
   *  comparison, sheet) as a local object URL. */
  async render3dUrl(id: string, stepId: string, kind: string): Promise<string> {
    const res = await apiFetch(`${BASE}/runs/${id}/render3d/${stepId}/${kind}`)
    if (!res.ok) throw new Error(await readError(res))
    return URL.createObjectURL(await res.blob())
  },
  /** 3D Render: the reviewed 3D reference becomes a realistic video (Kling 4.0; Seedance 2.0 only if Kling fails). */
  realize: (id: string, stepId: string, instruction: string) => post(`/runs/${id}/render3d/${stepId}/realize`, { instruction }),
  async outputUrl(id: string, stepId: string): Promise<string> {
    const res = await apiFetch(`${BASE}/runs/${id}/output/${stepId}`)
    if (!res.ok) throw new Error(await readError(res))
    return URL.createObjectURL(await res.blob())
  },
}

async function uploadFile(runId: string, file: File) {
  const form = new FormData()
  form.append("file", file)
  const res = await apiFetch(`${BASE}/runs/${runId}/files`, { method: "POST", body: form })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}
