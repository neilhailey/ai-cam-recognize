import type { PhotoResponse, StlResponse } from './types'

// In dev the Vite proxy handles /api; in production (Vercel) set VITE_API_BASE
// to the deployed backend origin, e.g. https://ai-cam-recognize.onrender.com
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

const MESH_EXTS = ['.stl', '.obj', '.ply', '.glb', '.off', '.3mf']

export function isMeshFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return MESH_EXTS.some((e) => name.endsWith(e))
}

/** Prefix a server-relative path (e.g. a glb_url) with the API base. */
export const apiUrl = (path: string) => `${API_BASE}${path}`

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(apiUrl(path), { method: 'POST', body: form })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      if (j.detail) detail = j.detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export function analyzeStl(file: File, preSimplified = false): Promise<StlResponse> {
  const form = new FormData()
  form.append('file', file)
  // Tell the server the browser already reduced this mesh, so it judges it with
  // the tolerance for a simplified mesh rather than treating it as exact.
  form.append('pre_simplified', String(preSimplified))
  return postForm<StlResponse>('/api/analyze/stl', form)
}

export function analyzePhoto(file: File): Promise<PhotoResponse> {
  const form = new FormData()
  form.append('file', file)
  return postForm<PhotoResponse>('/api/analyze/photo', form)
}
