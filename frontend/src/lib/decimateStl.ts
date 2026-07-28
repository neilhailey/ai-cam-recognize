/**
 * Client-side STL reduction.
 *
 * High-poly scans are often 20-100 MB, and uploading that dominates the total
 * wait — far more than the analysis itself. The backend reduces to ~30k faces
 * anyway, so sending millions of triangles is wasted bandwidth. We do the same
 * grid vertex-clustering here first, turning a 94 MB upload into ~1.5 MB.
 *
 * Only binary STLs are handled (the common export format); anything else is
 * uploaded untouched and reduced server-side.
 */

const HEADER = 84
const RECORD = 50 // 12B normal + 36B verts + 2B attribute

export interface DecimateResult {
  file: File
  originalFaces: number
  faces: number
  reduced: boolean
}

/** Triangle count if `buf` is a binary STL, else null. */
export function binaryStlFaceCount(buf: ArrayBuffer): number | null {
  if (buf.byteLength < HEADER) return null
  const n = new DataView(buf).getUint32(80, true)
  return buf.byteLength === HEADER + RECORD * n ? n : null
}

/**
 * Reduce a binary STL to roughly `targetFaces` triangles by snapping vertices
 * to a uniform grid and dropping triangles that collapse. Returns a new binary
 * STL. Mirrors the server-side algorithm so results stay consistent.
 */
export function decimateBinaryStl(buf: ArrayBuffer, n: number, targetFaces: number): ArrayBuffer {
  const dv = new DataView(buf)

  // Pass 1: read vertices into a flat array and track the bounding box.
  const verts = new Float32Array(n * 9)
  let minX = Infinity, minY = Infinity, minZ = Infinity
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity
  for (let i = 0; i < n; i++) {
    const base = HEADER + i * RECORD + 12
    for (let k = 0; k < 3; k++) {
      const o = base + k * 12
      const x = dv.getFloat32(o, true)
      const y = dv.getFloat32(o + 4, true)
      const z = dv.getFloat32(o + 8, true)
      const j = i * 9 + k * 3
      verts[j] = x; verts[j + 1] = y; verts[j + 2] = z
      if (x < minX) minX = x; if (x > maxX) maxX = x
      if (y < minY) minY = y; if (y > maxY) maxY = y
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z
    }
  }

  const dx = maxX - minX, dy = maxY - minY, dz = maxZ - minZ
  const diag = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1
  const res = diag / Math.max(1, Math.sqrt(targetFaces) * 0.55)

  // Grid coords fit well inside the 2^53 exact-integer range when packed this way.
  const SPAN = 4194304 // 2^22 per axis
  const cell = new Map<number, number>()   // packed grid key -> new vertex index
  const cx: number[] = [], cy: number[] = [], cz: number[] = []
  const idx = new Int32Array(n * 3)

  for (let v = 0; v < n * 3; v++) {
    const j = v * 3
    const gx = Math.floor((verts[j] - minX) / res)
    const gy = Math.floor((verts[j + 1] - minY) / res)
    const gz = Math.floor((verts[j + 2] - minZ) / res)
    const key = (gx * SPAN + gy) * SPAN + gz
    let id = cell.get(key)
    if (id === undefined) {
      id = cx.length
      cell.set(key, id)
      // representative vertex = cell centre
      cx.push(minX + (gx + 0.5) * res)
      cy.push(minY + (gy + 0.5) * res)
      cz.push(minZ + (gz + 0.5) * res)
    }
    idx[v] = id
  }

  // Keep only triangles whose three vertices landed in distinct cells.
  const keep: number[] = []
  for (let i = 0; i < n; i++) {
    const a = idx[i * 3], b = idx[i * 3 + 1], c = idx[i * 3 + 2]
    if (a !== b && b !== c && a !== c) keep.push(i)
  }

  // Write a fresh binary STL. Normals are left zero — the backend recomputes them.
  const out = new ArrayBuffer(HEADER + RECORD * keep.length)
  const ov = new DataView(out)
  ov.setUint32(80, keep.length, true)
  for (let t = 0; t < keep.length; t++) {
    const i = keep[t]
    const base = HEADER + t * RECORD + 12
    for (let k = 0; k < 3; k++) {
      const id = idx[i * 3 + k]
      const o = base + k * 12
      ov.setFloat32(o, cx[id], true)
      ov.setFloat32(o + 4, cy[id], true)
      ov.setFloat32(o + 8, cz[id], true)
    }
  }
  return out
}

/**
 * Shrink `file` before upload when it is a large binary STL. Returns the
 * original file unchanged when it is small enough or not a binary STL.
 */
export async function prepareMeshUpload(file: File, targetFaces = 30_000): Promise<DecimateResult> {
  const buf = await file.arrayBuffer()
  const n = binaryStlFaceCount(buf)
  if (n === null || n <= targetFaces * 1.5) {
    return { file, originalFaces: n ?? 0, faces: n ?? 0, reduced: false }
  }
  const out = decimateBinaryStl(buf, n, targetFaces)
  const faces = binaryStlFaceCount(out) ?? 0
  const reduced = new File([out], file.name, { type: 'application/octet-stream' })
  return { file: reduced, originalFaces: n, faces, reduced: true }
}
