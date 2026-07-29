import { useState } from 'react'

export interface CutPoint { tool: number; lost_pct: number; max_loss: number }
export interface CutPreview {
  ok: boolean
  relief_depth: number
  points: CutPoint[]
  grid: number[]
}

interface Props {
  preview: CutPreview
  /** mm per model unit, 0 when the part's real size is unknown. */
  mmPerUnit: number
}

/**
 * "Will this cutter still show the detail?"
 *
 * A ball nose can only leave surfaces it physically fits into, so anything
 * finer than the tool gets rounded away. The backend measured that for a range
 * of tool sizes; this just lets you slide through them.
 */
export function CutPreviewCard({ preview, mmPerUnit }: Props) {
  const pts = preview?.points ?? []
  const [i, setI] = useState(() => Math.min(3, Math.max(pts.length - 1, 0)))
  if (!preview?.ok || !pts.length) return null

  const p = pts[Math.min(i, pts.length - 1)]
  const mm = mmPerUnit ? p.tool * mmPerUnit : 0
  const lostDepthMm = mmPerUnit ? p.max_loss * mmPerUnit : 0
  const verdict =
    p.lost_pct < 2 ? { text: 'keeps essentially all the detail', cls: 'good' }
      : p.lost_pct < 8 ? { text: 'loses the finest detail', cls: 'warn' }
        : { text: 'noticeably softens the carving', cls: 'bad' }

  return (
    <div className="plan-card">
      <div className="plan-card__title">🔍 Cut preview</div>

      <input
        className="cut-slider"
        type="range" min={0} max={pts.length - 1} value={i}
        onChange={(e) => setI(Number(e.target.value))}
      />

      <p className="plan-card__body">
        A <strong>{mm ? `⌀${mm.toFixed(1)} mm` : `⌀${p.tool.toFixed(4)} unit`}</strong> ball
        nose <span className={`cut-verdict cut-verdict--${verdict.cls}`}>{verdict.text}</span>
        {' '}— <strong>{p.lost_pct}%</strong> of the surface would be rounded off
        {lostDepthMm > 0 && <>, by up to {lostDepthMm.toFixed(2)} mm</>}.
      </p>

      <div className="cut-scale">
        {pts.map((q, n) => (
          <span
            key={n}
            className={`cut-tick${n === i ? ' cut-tick--on' : ''}`}
            title={`${q.lost_pct}% lost`}
            style={{ opacity: 0.35 + 0.65 * (1 - q.lost_pct / 100) }}
          />
        ))}
      </div>

      <div className="plan-card__meta">
        Models what a ball cutter can physically reach into — it cannot enter anything
        tighter than itself. Does not include tool deflection or finish quality.
        {!mmPerUnit && ' Set the part size above to see real tool diameters.'}
      </div>
    </div>
  )
}
