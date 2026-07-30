import { useCallback, useEffect, useRef, useState } from 'react'
import { analyzePhoto, analyzeStl, apiUrl, isMeshFile } from './api'
import { prepareMeshUpload } from './lib/decimateStl'
import type { PhotoResult, StlResponse } from './types'
import { Dropzone } from './components/Dropzone'
import { VerdictCard } from './components/VerdictCard'
import { StatBars } from './components/StatBars'
import { MeshViewer } from './components/MeshViewer'
import { PhotoResultCard } from './components/PhotoResultCard'
import { MountingCard, OrientationCard, SetupPlanCard, ToolingCard } from './components/PlanCards'
import { StockCard } from './components/StockCard'
import { CutSimulator } from './components/CutSimulator'

type Mode = 'idle' | 'loading' | 'stl' | 'photo' | 'error'

const SAMPLES: { label: string; file: string }[] = [
  { label: 'Box (3-axis)', file: 'box.stl' },
  { label: 'Cross-drilled (4-axis)', file: 'cross_drilled.stl' },
  { label: 'Tilted pocket (5-axis)', file: 'tilted_pocket.stl' },
  { label: 'Relief (cut preview)', file: 'relief_logo.stl' },
  { label: 'Mushroom photo', file: 'mushroom.png' },
]

export function App() {
  const [mode, setMode] = useState<Mode>('idle')
  const [error, setError] = useState('')
  const [stl, setStl] = useState<StlResponse | null>(null)
  const [photo, setPhoto] = useState<PhotoResult | null>(null)
  const [photoUrl, setPhotoUrl] = useState('')
  const [fileName, setFileName] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [stage, setStage] = useState<'preparing' | 'uploading'>('uploading')
  const [note, setNote] = useState('')
  const [flipped, setFlipped] = useState(false)
  // One place for the part's real size: the stock advice and the cut preview
  // both need it, and the analysis itself is scale-invariant.
  const [longestMm, setLongestMm] = useState('')
  const photoUrlRef = useRef('')
  const lastMeshRef = useRef<{ file: File; reduced: boolean } | null>(null)

  // Tick a seconds counter while an analysis is in flight.
  useEffect(() => {
    if (mode !== 'loading') return
    setElapsed(0)
    const id = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [mode])

  const reset = useCallback(() => {
    setMode('idle'); setError(''); setStl(null); setPhoto(null); setFileName(''); setNote('')
    if (photoUrlRef.current) URL.revokeObjectURL(photoUrlRef.current)
    photoUrlRef.current = ''; setPhotoUrl('')
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setError(''); setStl(null); setPhoto(null); setFileName(file.name); setNote('')
    setMode('loading')
    try {
      if (isMeshFile(file)) {
        // Shrink very high-poly STLs locally first — uploading tens of MB is
        // slower than the analysis itself, and the server reduces them anyway.
        setStage('preparing')
        const prep = await prepareMeshUpload(file)
        if (prep.reduced) {
          setNote(`Simplified ${prep.originalFaces.toLocaleString()} → ${prep.faces.toLocaleString()} triangles before upload for speed.`)
        }
        setStage('uploading')
        lastMeshRef.current = { file: prep.file, reduced: prep.reduced }
        const res = await analyzeStl(prep.file, prep.reduced, false)
        setLongestMm(res.dimensions?.looks_like_mm
          ? String(Math.round(Math.max(...res.dimensions.extents))) : '')
        setStl(res)
        setFlipped(false)
        setMode('stl')
      } else {
        const url = URL.createObjectURL(file)
        photoUrlRef.current = url
        setPhotoUrl(url)
        const res = await analyzePhoto(file)
        setPhoto(res.result)
        setMode('photo')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setMode('error')
    }
  }, [])

  const flipModel = useCallback(async () => {
    const last = lastMeshRef.current
    if (!last) return
    const next = !flipped
    setMode('loading'); setStage('uploading'); setError('')
    try {
      setStl(await analyzeStl(last.file, last.reduced, next))
      setFlipped(next)
      setMode('stl')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setMode('error')
    }
  }, [flipped])

  const loadSample = useCallback(async (name: string) => {
    try {
      const res = await fetch(`/samples/${name}`)
      const buf = await res.blob()
      const type = name.endsWith('.png') ? 'image/png' : 'application/octet-stream'
      handleFile(new File([buf], name, { type }))
    } catch (e) {
      setError(String(e)); setMode('error')
    }
  }, [handleFile])

  return (
    <div className="page">
      <header className="header">
        <h1>CNC Machinability Checker</h1>
        <p>Can this model be cut on a 3- or 4-axis router — or does it need 5-axis?</p>
      </header>

      {mode === 'idle' && (
        <>
          <Dropzone onFile={handleFile} />
          <div className="controls">
            <div className="samples">
              <span className="samples__label">Or try an example:</span>
              {SAMPLES.map((s) => (
                <button key={s.file} className="chip" onClick={() => loadSample(s.file)}>{s.label}</button>
              ))}
            </div>
          </div>
        </>
      )}

      {mode === 'loading' && (
        <div className="loading">
          <div className="spinner" />
          <div>Analyzing <strong>{fileName}</strong>… {elapsed > 0 && `${elapsed}s`}</div>
          <div className="loading__hint">
            {stage === 'preparing'
              ? 'Simplifying a high-poly mesh in your browser…'
              : elapsed > 20
                ? 'Large models take longer on the free server — hang tight.'
                : 'Uploading and casting rays to find undercuts…'}
          </div>
        </div>
      )}

      {mode === 'error' && (
        <div className="error-box">
          <strong>Analysis failed.</strong> {error}
          <div><button className="btn" onClick={reset}>Try again</button></div>
        </div>
      )}

      {mode === 'stl' && stl && (
        <div className="result">
          <div className="result__left">
            {note && <div className="note">{note}</div>}
            {stl.mesh_quality?.warnings?.map((w, i) => (
              <div key={i} className="warn">⚠️ {w}</div>
            ))}
            <VerdictCard report={stl.report} />
            <StatBars report={stl.report} />
            <OrientationCard orientation={stl.orientation} />
            <SetupPlanCard plan={stl.setups} verdict={stl.report.verdict} rotary={stl.rotary} />
            <ToolingCard tooling={stl.tooling} dimensions={stl.dimensions} />
            {stl.stock_geometry && (
              <StockCard
                geo={stl.stock_geometry}
                verdict={stl.report.verdict}
                looksLikeMm={stl.dimensions?.looks_like_mm ?? false}
                longestMm={longestMm}
                onLongestMm={setLongestMm}
              />
            )}
            <MountingCard mounting={stl.mounting} rotary={stl.rotary} verdict={stl.report.verdict} />
            <details className="caveats">
              <summary>What this check does &amp; doesn’t cover</summary>
              <ul>{stl.report.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </details>
            <div className="btn-row">
              <button className="btn" onClick={reset}>Check another</button>
              <button className="btn btn--ghost" onClick={flipModel}>
                {flipped ? 'Un-flip model' : 'Flip model'}
              </button>
            </div>
          </div>
          <div className="result__viewer">
            <MeshViewer
              url={apiUrl(stl.glb_url)}
              sweep={stl.sweep}
              verdict={stl.report.verdict}
              rotaryAxis={stl.rotary?.axis ?? null}
              gripFrac={stl.rotary?.grip_frac}
              onLoadError={(m) => setError(m)}
            />
            <div className="viewer-caption">
              Drag to orbit · {stl.report.verdict === '4-axis'
                ? 'tan = waste stock held in the chuck · red = undercut'
                : 'red = undercut regions'}
            </div>
          </div>
          {stl.heightmap && (
            <div className="result__sim">
              <div className="sim__title">🛠 Machining simulation</div>
              <CutSimulator
                map={stl.heightmap}
                mmPerUnit={(() => {
                  const v = parseFloat(longestMm)
                  const u = Math.max(...(stl.stock_geometry?.extents ?? [0]))
                  return v > 0 && u > 0 ? v / u : 0
                })()}
              />
            </div>
          )}
        </div>
      )}

      {mode === 'photo' && photo && (
        <div className="result result--photo">
          <PhotoResultCard result={photo} imageUrl={photoUrl} />
          <button className="btn" onClick={reset}>Check another</button>
        </div>
      )}
    </div>
  )
}
