import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export interface HeightMap {
  w: number
  h: number
  cell: number
  x0: number
  y0: number
  zmin: number
  zmax: number
  inside: number[]
  z: number[]      // uint16, rescaled between zmin and zmax
}

interface Props {
  map: HeightMap
  /** mm per model unit; 0 when the real size is unknown. */
  mmPerUnit: number
}

/**
 * Live machining simulation of a relief.
 *
 * The stock starts as a solid block and is carved away as a ball-nose runs a
 * raster finishing pass over it, exactly as it would on the machine. Material
 * removal is real, not a fade-in: at each tool position the ball can descend
 * only until it touches the highest point of the target surface beneath its
 * footprint, and every grid cell it covers is lowered to the underside of the
 * ball. That is also why detail finer than the tool survives as leftover
 * material — the same reason a real cutter cannot reach it.
 */
export function CutSimulator({ map, mmPerUnit }: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const apiRef = useRef<{
    reset: (toolFrac: number, stepFrac: number) => void
    setRunning: (v: boolean) => void
    setSpeed: (v: number) => void
  } | null>(null)

  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [toolFrac, setToolFrac] = useState(0.045)   // tool ⌀ as a fraction of width
  const [speed, setSpeed] = useState(3)

  const widthUnits = map.w * map.cell
  const toolMm = mmPerUnit ? toolFrac * widthUnits * mmPerUnit : 0

  useEffect(() => {
    const mount = mountRef.current
    if (!mount || !map?.z?.length) return
    const W = mount.clientWidth || 700
    const H = mount.clientHeight || 420

    // preserveDrawingBuffer so the canvas can be screenshotted or saved —
    // without it the buffer is cleared before compositing and captures come out black.
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(W, H)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x11161d)
    const camera = new THREE.PerspectiveCamera(42, W / H, 0.1, 10000)
    camera.up.set(0, 0, 1)

    scene.add(new THREE.AmbientLight(0xffffff, 0.7))
    const key = new THREE.DirectionalLight(0xffffff, 1.5)
    key.position.set(1, -1.4, 2)
    scene.add(key)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.12

    // ---- target surface, in a normalised ~100-unit world -------------------
    const { w, h, cell, zmin, zmax } = map
    const S = 100 / Math.max(w * cell, h * cell)
    const zSpan = Math.max(zmax - zmin, 1e-9)
    const target = new Float32Array(w * h)
    for (let i = 0; i < w * h; i++) target[i] = (map.z[i] / 65535) * zSpan * S
    const stockTop = zSpan * S * 1.06          // a little skim on top, as in reality

    // ---- stock as a displaced grid -----------------------------------------
    const geom = new THREE.PlaneGeometry((w - 1) * cell * S, (h - 1) * cell * S, w - 1, h - 1)
    const pos = geom.attributes.position as THREE.BufferAttribute
    const cur = new Float32Array(w * h).fill(stockTop)
    for (let i = 0; i < w * h; i++) pos.setZ(i, stockTop)
    pos.needsUpdate = true
    geom.computeVertexNormals()

    const surface = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
      color: 0xc8a97a, roughness: 0.85, metalness: 0.02, side: THREE.DoubleSide,
    }))
    scene.add(surface)

    // sides + base so it reads as a solid billet rather than a sheet
    const bx = (w - 1) * cell * S, by = (h - 1) * cell * S
    const block = new THREE.Mesh(
      new THREE.BoxGeometry(bx, by, stockTop),
      new THREE.MeshStandardMaterial({ color: 0xa98d63, roughness: 0.95 }))
    block.position.z = -stockTop / 2
    scene.add(block)

    // ---- the cutter ---------------------------------------------------------
    const toolGroup = new THREE.Group()
    const steel = new THREE.MeshStandardMaterial({ color: 0xb9c2cc, roughness: 0.35, metalness: 0.8 })
    const ball = new THREE.Mesh(new THREE.SphereGeometry(1, 20, 14), steel)
    const shank = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 20), steel)
    shank.rotation.x = Math.PI / 2
    toolGroup.add(ball, shank)
    scene.add(toolGroup)

    camera.position.set(bx * 0.9, -by * 1.1, stockTop + Math.max(bx, by) * 0.75)
    controls.target.set(0, 0, stockTop * 0.3)
    controls.update()

    // ---- toolpath + cutting -------------------------------------------------
    let rCells = 1, rWorld = 1, path: number[][] = [], pi = 0, speedRef = 3, run = false

    const buildPath = (tf: number, stepFrac: number) => {
      rWorld = Math.max(tf * bx / 2, cell * S * 1.2)
      rCells = Math.max(rWorld / (cell * S), 1)
      const stepCells = Math.max(Math.round(rCells * 2 * stepFrac), 1)
      path = []
      for (let row = 0, dir = 1; row < h; row += stepCells, dir = -dir) {
        const cols = []
        for (let c = 0; c < w; c += 1) cols.push(c)
        if (dir < 0) cols.reverse()
        for (const c of cols) path.push([c, row])
      }
      pi = 0
      cur.fill(stockTop)
      for (let i = 0; i < w * h; i++) pos.setZ(i, stockTop)
      pos.needsUpdate = true
      geom.computeVertexNormals()
      ball.scale.setScalar(rWorld)
      shank.scale.set(rWorld * 0.75, rWorld * 0.75, stockTop * 2.4)
      shank.position.z = rWorld + stockTop * 1.2
      setProgress(0)
    }

    /** Lowest the ball can sit at (cx, cy) without gouging the target. */
    const toolCentre = (cx: number, cy: number) => {
      const R = Math.ceil(rCells)
      let best = -Infinity
      for (let dy = -R; dy <= R; dy++) {
        const y = cy + dy
        if (y < 0 || y >= h) continue
        for (let dx = -R; dx <= R; dx++) {
          const x = cx + dx
          if (x < 0 || x >= w) continue
          const d2 = (dx * dx + dy * dy) * (cell * S) ** 2
          if (d2 > rWorld * rWorld) continue
          const lift = Math.sqrt(rWorld * rWorld - d2)
          const v = target[y * w + x] + lift
          if (v > best) best = v
        }
      }
      return best === -Infinity ? stockTop : best
    }

    /** Remove everything the ball sweeps through at this position. */
    const cutAt = (cx: number, cy: number, zc: number) => {
      const R = Math.ceil(rCells)
      for (let dy = -R; dy <= R; dy++) {
        const y = cy + dy
        if (y < 0 || y >= h) continue
        for (let dx = -R; dx <= R; dx++) {
          const x = cx + dx
          if (x < 0 || x >= w) continue
          const d2 = (dx * dx + dy * dy) * (cell * S) ** 2
          if (d2 > rWorld * rWorld) continue
          const under = zc - Math.sqrt(rWorld * rWorld - d2)
          const i = y * w + x
          if (under < cur[i]) { cur[i] = under; pos.setZ(i, under) }
        }
      }
    }

    buildPath(toolFrac, 0.35)

    apiRef.current = {
      reset: (tf, sf) => buildPath(tf, sf),
      setRunning: (v) => { run = v },
      setSpeed: (v) => { speedRef = v },
    }

    let raf = 0
    const animate = () => {
      raf = requestAnimationFrame(animate)
      if (run && pi < path.length) {
        const steps = Math.max(1, Math.round(speedRef * 14))
        for (let k = 0; k < steps && pi < path.length; k++, pi++) {
          const [cx, cy] = path[pi]
          const zc = toolCentre(cx, cy)
          cutAt(cx, cy, zc)
          if (k === steps - 1 || pi === path.length - 1) {
            toolGroup.position.set(
              (cx - (w - 1) / 2) * cell * S,
              -((cy - (h - 1) / 2) * cell * S),
              zc)
          }
        }
        pos.needsUpdate = true
        geom.computeVertexNormals()
        setProgress(Math.round((pi / path.length) * 100))
        if (pi >= path.length) run = false
      }
      controls.update()
      renderer.render(scene, camera)
    }
    animate()

    const onResize = () => {
      const ww = mount.clientWidth, hh = mount.clientHeight
      camera.aspect = ww / hh
      camera.updateProjectionMatrix()
      renderer.setSize(ww, hh)
    }
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      apiRef.current = null
      controls.dispose()
      renderer.dispose()
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
    }
  }, [map])

  useEffect(() => { apiRef.current?.setRunning(running) }, [running])
  useEffect(() => { apiRef.current?.setSpeed(speed) }, [speed])

  return (
    <div className="sim">
      <div ref={mountRef} className="sim__canvas" />
      <div className="sim__bar">
        <button className="viewbar__btn" onClick={() => setRunning((r) => !r)}>
          {running ? '❚❚ Pause' : '▶ Run'}
        </button>
        <button
          className="viewbar__btn"
          onClick={() => { setRunning(false); apiRef.current?.reset(toolFrac, 0.35) }}
        >↺ Reset</button>
        <label className="sim__field">
          tool
          <input
            type="range" min={0.015} max={0.12} step={0.005} value={toolFrac}
            onChange={(e) => {
              const v = Number(e.target.value)
              setToolFrac(v); setRunning(false); apiRef.current?.reset(v, 0.35)
            }}
          />
          <span>{toolMm ? `⌀${toolMm.toFixed(1)}mm` : `⌀${(toolFrac * 100).toFixed(1)}%`}</span>
        </label>
        <label className="sim__field">
          speed
          <input type="range" min={1} max={8} step={1} value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))} />
        </label>
        <span className="sim__pct">{progress}%</span>
      </div>
    </div>
  )
}
