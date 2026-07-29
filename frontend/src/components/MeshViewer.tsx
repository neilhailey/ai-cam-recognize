import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Verdict } from '../types'

export type ViewName = 'iso' | 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom'

/** Camera offsets from the target, in the Z-up machine frame. */
const VIEW_DIRS: Record<ViewName, [number, number, number]> = {
  iso: [1, -1, 0.8],
  front: [0, -1, 0],
  back: [0, 1, 0],
  left: [-1, 0, 0],
  right: [1, 0, 0],
  top: [0, 0, 1],
  bottom: [0, 0, -1],
}

const VIEW_BUTTONS: ViewName[] = ['iso', 'front', 'back', 'left', 'right', 'top', 'bottom']

interface Props {
  url: string
  verdict?: Verdict
  rotaryAxis?: 'x' | 'y' | null
  gripFrac?: number
  onLoadError?: (msg: string) => void
}

/** Small coloured text label that always faces the camera. */
function makeLabel(text: string, color: string): THREE.Sprite {
  const font = 'bold 44px -apple-system, sans-serif'
  // Measure first — a fixed-width canvas silently crops longer words
  // ("mounting" came out as "ountir").
  const probe = document.createElement('canvas').getContext('2d')!
  probe.font = font
  const w = Math.ceil(probe.measureText(text).width) + 16
  const h = 64
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')!
  ctx.font = font
  ctx.fillStyle = color
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, w / 2, h / 2)
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true }),
  )
  sprite.scale.set((w / h) * 7, 7, 1)     // keep the text's aspect ratio
  return sprite
}

/**
 * Renders the analysis GLB (per-face colours baked in by the backend) plus a
 * simulation of how the part is held: a bed for 3-axis work, a rotary chuck and
 * tailstock for 4-axis.
 *
 * The scene is Z-up to match CAD/CNC convention — trimesh writes the GLB with no
 * Y-up node transform, so the model's +Z really is "up" and three.js's default
 * Y-up would lay every part on its side.
 */
export function MeshViewer({ url, verdict, rotaryAxis, gripFrac = 0.12, onLoadError }: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const apiRef = useRef<{ setView: (v: ViewName, refit?: boolean) => void } | null>(null)
  const onErrRef = useRef(onLoadError)
  useEffect(() => { onErrRef.current = onLoadError }, [onLoadError])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    const W = mount.clientWidth || 800
    const H = mount.clientHeight || 520

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    renderer.setSize(W, H)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x11161d)

    const camera = new THREE.PerspectiveCamera(42, W / H, 0.1, 10000)
    camera.up.set(0, 0, 1)                       // Z-up, like the machine
    camera.position.set(150, -170, 110)

    scene.add(new THREE.AmbientLight(0xffffff, 0.75))
    const key = new THREE.DirectionalLight(0xffffff, 1.5)
    key.position.set(1, -1.5, 2)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0xffffff, 0.6)
    fill.position.set(-1.5, 1, -0.5)
    scene.add(fill)

    // CAD/CAM conventions: the model holds still until you move it, left-drag
    // orbits, right-drag pans, wheel zooms toward the cursor, and Z stays up so
    // the part never rolls onto its side.
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.12          // settles quickly instead of drifting
    controls.rotateSpeed = 0.8
    controls.zoomSpeed = 0.9
    controls.panSpeed = 0.8
    controls.screenSpacePanning = true
    controls.zoomToCursor = true
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    }

    const group = new THREE.Group()
    scene.add(group)

    const loader = new GLTFLoader()
    loader.load(
      url,
      (gltf) => {
        const root = gltf.scene
        root.traverse((obj) => {
          const mesh = obj as THREE.Mesh
          if (!mesh.isMesh) return
          mesh.material = new THREE.MeshStandardMaterial({
            vertexColors: true,
            flatShading: true,
            roughness: 0.85,
            metalness: 0.0,
            side: THREE.DoubleSide,
          })
        })

        // Centre in X/Y, normalise to ~100 units, and rest the base on z = 0 so
        // the bed/chuck line up with where the part actually sits.
        const box = new THREE.Box3().setFromObject(root)
        const size = new THREE.Vector3()
        const center = new THREE.Vector3()
        box.getSize(size)
        box.getCenter(center)
        const s = 100 / (Math.max(size.x, size.y, size.z) || 1)
        root.scale.setScalar(s)
        root.position.set(-center.x * s, -center.y * s, -box.min.z * s)
        group.add(root)

        const dim = new THREE.Vector3(size.x * s, size.y * s, size.z * s)
        const radius = Math.max(dim.x, dim.y) / 2

        // --- coordinate system ------------------------------------------------
        const axes = new THREE.AxesHelper(dim.length() * 0.42)
        group.add(axes)
        const L = dim.length() * 0.46
        const labels: [string, string, THREE.Vector3][] = [
          ['X', '#ff6b6b', new THREE.Vector3(L, 0, 0)],
          ['Y', '#7bd88f', new THREE.Vector3(0, L, 0)],
          ['Z', '#6aa9ff', new THREE.Vector3(0, 0, L)],
        ]
        for (const [t, c, pos] of labels) {
          const sp = makeLabel(t, c)
          sp.position.copy(pos)
          group.add(sp)
        }

        if (verdict === '4-axis' && rotaryAxis) {
          // --- rotary chuck + tailstock, on the axis the part spins about -----
          const along = rotaryAxis === 'x' ? dim.x : dim.y
          const dir = rotaryAxis === 'x'
            ? new THREE.Vector3(1, 0, 0)
            : new THREE.Vector3(0, 1, 0)
          const centreZ = dim.z / 2
          // Size the jaws to the part's cross-section (across the spin axis),
          // not its overall length, so they look like they grip it.
          const crossR = Math.max(rotaryAxis === 'x' ? dim.y : dim.x, dim.z) / 2
          const chuckR = Math.max(crossR * 1.15, 6)

          // spin axis through the part
          const axisGeo = new THREE.BufferGeometry().setFromPoints([
            dir.clone().multiplyScalar(-along * 0.95).setZ(centreZ),
            dir.clone().multiplyScalar(along * 0.95).setZ(centreZ),
          ])
          group.add(new THREE.Line(
            axisGeo,
            new THREE.LineDashedMaterial({ color: 0x58a6ff, dashSize: 4, gapSize: 3 }),
          ).computeLineDistances())

          const steel = new THREE.MeshStandardMaterial({
            color: 0x9aa4b2, roughness: 0.5, metalness: 0.7,
            transparent: true, opacity: 0.55,   // see the gripped end through the jaws
          })
          // Waste stock left on each end: this is what the chuck and tailstock
          // actually grip — the finished part itself is never clamped.
          const stockR = Math.max(crossR * 0.55, 4)
          const stockL = along * 0.22
          const stockMat = new THREE.MeshStandardMaterial({
            color: 0xc8a97a, roughness: 0.9, metalness: 0.0,
          })
          for (const sign of [-1, 1]) {
            const stub = new THREE.Mesh(
              new THREE.CylinderGeometry(stockR, stockR, stockL, 20), stockMat)
            if (rotaryAxis === 'x') stub.rotation.z = Math.PI / 2
            stub.position.copy(
              dir.clone().multiplyScalar(sign * (along / 2 + stockL * 0.45))).setZ(centreZ)
            group.add(stub)
          }
          const sLabel = makeLabel('waste stock', '#c8a97a')
          sLabel.position.copy(
            dir.clone().multiplyScalar(-(along / 2) - stockL * 0.45)).setZ(centreZ - stockR - 7)
          group.add(sLabel)

          // chuck body clamping that stock
          const chuck = new THREE.Mesh(new THREE.CylinderGeometry(chuckR, chuckR, along * 0.10, 24), steel)
          // tailstock cone at the far end
          const tail = new THREE.Mesh(new THREE.ConeGeometry(chuckR * 0.55, along * 0.14, 20), steel)
          // CylinderGeometry runs along +Y; rotate onto the spin axis.
          if (rotaryAxis === 'x') {
            chuck.rotation.z = -Math.PI / 2
            tail.rotation.z = Math.PI / 2
          }
          chuck.position.copy(dir.clone().multiplyScalar(-(along / 2) - along * 0.09)).setZ(centreZ)
          tail.position.copy(dir.clone().multiplyScalar((along / 2) + along * 0.08)).setZ(centreZ)
          group.add(chuck, tail)

          const lbl = makeLabel(`${rotaryAxis.toUpperCase()} axis`, '#8b949e')
          lbl.position.copy(dir.clone().multiplyScalar(-(along / 2) - along * 0.09)).setZ(centreZ + chuckR + 8)
          group.add(lbl)
        } else {
          // --- machine bed for 3-axis (and as a floor when 5-axis) ------------
          const bedSize = Math.max(dim.x, dim.y) * 2.2
          const grid = new THREE.GridHelper(bedSize, 16, 0x3a4455, 0x2a323d)
          grid.rotation.x = Math.PI / 2            // GridHelper is XZ; put it in XY
          group.add(grid)
          const slab = new THREE.Mesh(
            new THREE.BoxGeometry(bedSize, bedSize, 1.5),
            new THREE.MeshStandardMaterial({
              color: 0x2a323d, roughness: 0.95, transparent: true, opacity: 0.55,
            }),
          )
          slab.position.z = -0.9
          group.add(slab)
        }

        // --- assumed mounting face marker (bed setups only; 4-axis is chucked)
        if (verdict !== '4-axis') {
          // A disc under the part would be hidden by the part itself, so ring the
          // footprint instead — that reads as "clamped here" from any angle.
          const inner = Math.max(radius * 1.02, 4)
          const patch = new THREE.Mesh(
            new THREE.RingGeometry(inner, inner * 1.35, 48),
            new THREE.MeshBasicMaterial({
              color: 0x58a6ff, transparent: true, opacity: 0.4, side: THREE.DoubleSide,
            }),
          )
          patch.position.z = 0.2
          group.add(patch)
          const mLabel = makeLabel('mounting', '#58a6ff')
          mLabel.position.set(inner * 1.2, -inner * 1.2, 5)
          group.add(mLabel)
        }

        // frame the whole setup
        const fit = Math.max(dim.length() * 1.5, 140)
        controls.target.set(0, 0, dim.z * 0.45)
        controls.minDistance = 20
        controls.maxDistance = 1600

        /** Jump to a named view, keeping the current zoom distance. */
        const setView = (view: ViewName, refit = false) => {
          const t = controls.target
          const dist = refit ? fit : camera.position.distanceTo(t)
          const dir = VIEW_DIRS[view]
          // Looking straight down/up, Z cannot also be "up" on screen — use +Y,
          // which is what CAD packages show for top and bottom views.
          // Z is up everywhere except looking straight along it, where it cannot
          // also be "up" on screen. Crucially this RESETS to Z on every other
          // view — leaving the camera in a Y-up frame made all later dragging
          // tumble the part about the wrong axis.
          const topLike = view === 'top' || view === 'bottom'
          camera.up.set(0, topLike ? 1 : 0, topLike ? 0 : 1)
          camera.position.copy(t).addScaledVector(
            new THREE.Vector3(...dir).normalize(), dist)
          camera.lookAt(t)
          controls.update()
        }
        setView('iso', true)
        apiRef.current = { setView }
      },
      undefined,
      (err) => onErrRef.current?.(String((err as ErrorEvent)?.message || err)),
    )

    let raf = 0
    const animate = () => {
      raf = requestAnimationFrame(animate)
      controls.update()
      renderer.render(scene, camera)
    }
    animate()

    const onResize = () => {
      const w = mount.clientWidth, h = mount.clientHeight
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
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
  }, [url, verdict, rotaryAxis, gripFrac])

  return (
    <div className="viewer">
      <div ref={mountRef} className="viewer__canvas" />
      <div className="viewbar">
        {VIEW_BUTTONS.map((v) => (
          <button
            key={v}
            className="viewbar__btn"
            title={v === 'iso' ? 'Isometric (reset zoom)' : `${v} view`}
            onClick={() => apiRef.current?.setView(v, v === 'iso')}
          >
            {v === 'iso' ? 'Iso' : v[0].toUpperCase() + v.slice(1)}
          </button>
        ))}
      </div>
    </div>
  )
}
