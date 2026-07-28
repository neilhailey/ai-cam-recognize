import { useCallback, useRef, useState } from 'react'
import { analyzePhoto, analyzeStl, apiUrl, isMeshFile } from './api'
import type { PhotoResult, StlResponse } from './types'
import { Dropzone } from './components/Dropzone'
import { VerdictCard } from './components/VerdictCard'
import { StatBars } from './components/StatBars'
import { MeshViewer } from './components/MeshViewer'
import { PhotoResultCard } from './components/PhotoResultCard'
import { OrientationCard, SetupPlanCard, ToolingCard } from './components/PlanCards'

type Mode = 'idle' | 'loading' | 'stl' | 'photo' | 'error'

const SAMPLES: { label: string; file: string }[] = [
  { label: 'Box (3-axis)', file: 'box.stl' },
  { label: 'Cross-drilled (4-axis)', file: 'cross_drilled.stl' },
  { label: 'Tilted pocket (5-axis)', file: 'tilted_pocket.stl' },
  { label: 'Mushroom photo', file: 'mushroom.png' },
]

export function App() {
  const [mode, setMode] = useState<Mode>('idle')
  const [error, setError] = useState('')
  const [stl, setStl] = useState<StlResponse | null>(null)
  const [photo, setPhoto] = useState<PhotoResult | null>(null)
  const [photoUrl, setPhotoUrl] = useState('')
  const [fileName, setFileName] = useState('')
  const photoUrlRef = useRef('')

  const reset = useCallback(() => {
    setMode('idle'); setError(''); setStl(null); setPhoto(null); setFileName('')
    if (photoUrlRef.current) URL.revokeObjectURL(photoUrlRef.current)
    photoUrlRef.current = ''; setPhotoUrl('')
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setError(''); setStl(null); setPhoto(null); setFileName(file.name)
    setMode('loading')
    try {
      if (isMeshFile(file)) {
        setStl(await analyzeStl(file))
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
          <div>Analyzing <strong>{fileName}</strong>…</div>
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
            <VerdictCard report={stl.report} />
            <StatBars report={stl.report} />
            <OrientationCard orientation={stl.orientation} />
            <SetupPlanCard plan={stl.setups} />
            <ToolingCard tooling={stl.tooling} />
            <details className="caveats">
              <summary>What this check does &amp; doesn’t cover</summary>
              <ul>{stl.report.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </details>
            <button className="btn" onClick={reset}>Check another</button>
          </div>
          <div className="result__viewer">
            <MeshViewer url={apiUrl(stl.glb_url)} onLoadError={(m) => setError(m)} />
            <div className="viewer-caption">Drag to orbit · red = undercut regions</div>
          </div>
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
