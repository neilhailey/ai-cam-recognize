import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Verdict } from '../types'

interface Props {
  url: string
  verdict?: Verdict
  rotaryAxis?: 'x' | 'y' | null
  gripFrac?: number
  onLoadError?: (msg: string) => void
}

/** Small coloured text label that always faces the camera. */
function makeLabel(text: string, color: string): THREE.Sprite {
  const c = document.createElement('canvas')
  c.width = 128
  c.height = 64
  const ctx = c.getContext('2d')!
  ctx.fillStyle = color
  ctx.font = 'bold 44px -apple-system, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, 64, 32)
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true }),
  )
  sprite.scale.set(14, 7, 1)
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

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.06
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.7

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
          // chuck body at the gripped (negative) end
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
        const d = Math.max(dim.length() * 1.5, 140)
        camera.position.set(d * 0.75, -d * 0.85, d * 0.55)
        controls.target.set(0, 0, dim.z * 0.45)
        controls.minDistance = 40
        controls.maxDistance = 1200
        controls.update()
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
      controls.dispose()
      renderer.dispose()
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
    }
  }, [url, verdict, rotaryAxis, gripFrac])

  return <div ref={mountRef} style={{ width: '100%', height: '100%' }} />
}
