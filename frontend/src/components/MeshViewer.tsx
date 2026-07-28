import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

interface Props {
  url: string
  onLoadError?: (msg: string) => void
}

/**
 * Renders the analysis GLB (per-face colors baked in by the backend) with
 * OrbitControls. Geometry is centered and normalized to ~100 world units on
 * the vertex buffer (mesh.scale stays 1) — the pattern proven in the demo's
 * StlViewer to avoid scale/rotation interaction bugs.
 */
export function MeshViewer({ url, onLoadError }: Props) {
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
    camera.position.set(120, 90, 150)

    scene.add(new THREE.AmbientLight(0xffffff, 0.75))
    const key = new THREE.DirectionalLight(0xffffff, 1.6)
    key.position.set(1, 2, 1.5)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0xffffff, 0.7)
    fill.position.set(-1.5, -0.5, -1)
    scene.add(fill)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.06
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.8

    const group = new THREE.Group()
    scene.add(group)

    const loader = new GLTFLoader()
    loader.load(
      url,
      (gltf) => {
        const root = gltf.scene
        // Recolor-safe material: show the baked vertex/face colors, flat shaded.
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

        // Center + normalize the whole GLTF scene to ~100 units.
        const box = new THREE.Box3().setFromObject(root)
        const size = new THREE.Vector3()
        const center = new THREE.Vector3()
        box.getSize(size)
        box.getCenter(center)
        const maxDim = Math.max(size.x, size.y, size.z) || 1
        const s = 100 / maxDim
        root.position.set(-center.x * s, -center.y * s, -center.z * s)
        root.scale.setScalar(s)
        group.add(root)

        // Frame the camera to the normalized ~100-unit model.
        const dist = 190
        camera.position.set(dist * 0.8, dist * 0.55, dist)
        controls.target.set(0, 0, 0)
        controls.minDistance = 40
        controls.maxDistance = 900
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
  }, [url])

  return <div ref={mountRef} style={{ width: '100%', height: '100%' }} />
}
