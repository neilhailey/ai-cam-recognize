import { useRef, useState } from 'react'

interface Props {
  onFile: (file: File) => void
  disabled?: boolean
}

export function Dropzone({ onFile, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)

  return (
    <div
      className={`dropzone${drag ? ' dropzone--active' : ''}${disabled ? ' dropzone--disabled' : ''}`}
      onClick={() => !disabled && inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        if (disabled) return
        const f = e.dataTransfer.files?.[0]
        if (f) onFile(f)
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".stl,.obj,.ply,.glb,.off,.3mf,image/*"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />
      <div className="dropzone__icon">⬆</div>
      <div className="dropzone__title">Drop an STL model or a photo</div>
      <div className="dropzone__hint">
        STL / OBJ / PLY / GLB → rigorous geometry check &nbsp;·&nbsp; JPG / PNG → quick AI pre-screen
      </div>
    </div>
  )
}
