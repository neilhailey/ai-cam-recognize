/**
 * Stock recommendation.
 *
 * The analysis itself is scale-invariant, so the model's real size is applied
 * here rather than re-running anything on the server: the user says how big the
 * part actually is, and every figure below follows from that.
 *
 * Self-contained on purpose — if this feature is dropped, deleting this file and
 * its two call sites removes it entirely.
 */

export interface StockGeometry {
  extents: number[]        // model units
  volume: number           // 0 when the mesh is not watertight
  swept_radius: number     // distance from the rotary axis, model units
  axis_length: number
}

export interface StockAdvice {
  type: 'round_bar' | 'rectangular'
  label: string
  lines: string[]
  removedFraction: number | null
  /** Dimensions in mm, for drawing the blank in the viewer. */
  mm: { d?: number; l?: number; w?: number; t?: number }
}

/** Board thicknesses and bar diameters people actually buy (mm). */
const BOARD_THICKNESS = [12, 19, 25, 32, 38, 50, 63, 75, 100]
const BAR_DIAMETER = [25, 40, 50, 60, 75, 90, 100, 125, 150]

const snapUp = (v: number, table: number[]) =>
  table.find((t) => t >= v - 1e-6) ?? Math.ceil(v / 25) * 25

/** Round up to a tidy buyable length. */
const roundUp5 = (v: number) => Math.ceil(v / 5) * 5

export interface StockOptions {
  sideMargin?: number     // mm of material around the part, 3-axis
  facing?: number         // mm skimmed off the top
  holdDown?: number       // mm left under the part for screws/clamping
  wasteEachEnd?: number   // mm of bar gripped at each end, 4-axis
}

export function recommendStock(
  geo: StockGeometry,
  verdict: string,
  mmPerUnit: number,
  opts: StockOptions = {},
): StockAdvice | null {
  if (!geo || !mmPerUnit || !isFinite(mmPerUnit) || mmPerUnit <= 0) return null
  const { sideMargin = 5, facing = 1.5, holdDown = 6, wasteEachEnd = 30 } = opts
  const [ex, ey, ez] = geo.extents.map((e) => e * mmPerUnit)

  if (verdict === '4-axis' && geo.swept_radius > 0) {
    const partD = geo.swept_radius * 2 * mmPerUnit
    const partL = geo.axis_length * mmPerUnit
    const rawD = Math.max(partD * 1.1, partD + 6)
    const d = snapUp(rawD, BAR_DIAMETER)
    const l = roundUp5(partL + 2 * wasteEachEnd)
    const removed = geo.volume > 0
      ? 1 - (geo.volume * mmPerUnit ** 3) / (Math.PI * (d / 2) ** 2 * l)
      : null
    return {
      type: 'round_bar',
      label: `Ø${d} × ${l} mm round bar`,
      lines: [
        `Part is Ø${partD.toFixed(0)} × ${partL.toFixed(0)} mm`,
        `${wasteEachEnd} mm waste at each end for the chuck and tailstock`,
      ],
      removedFraction: removed,
      mm: { d, l },
    }
  }

  // 3-axis / relief: a rectangular blank, thick enough to face and to clamp.
  const w = roundUp5(ex + 2 * sideMargin)
  const len = roundUp5(ey + 2 * sideMargin)
  const rawT = ez + facing + holdDown
  const t = snapUp(rawT, BOARD_THICKNESS)
  const removed = geo.volume > 0
    ? 1 - (geo.volume * mmPerUnit ** 3) / (w * len * t)
    : null
  return {
    type: 'rectangular',
    label: `${len} × ${w} × ${t} mm blank`,
    lines: [
      `Part is ${ey.toFixed(0)} × ${ex.toFixed(0)} × ${ez.toFixed(0)} mm`,
      `${sideMargin} mm margin all round, ${facing} mm to face off, ${holdDown} mm left underneath to clamp`,
    ],
    removedFraction: removed,
    mm: { l: len, w, t },
  }
}
