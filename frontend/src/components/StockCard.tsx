import { useEffect, useMemo, useState } from 'react'
import { recommendStock, type StockGeometry } from '../lib/stock'
import type { Verdict } from '../types'

interface Props {
  geo: StockGeometry
  verdict: Verdict
  looksLikeMm: boolean
  onStockChange?: (mm: { d?: number; l?: number; w?: number; t?: number } | null) => void
}

/**
 * "What do I buy?" — asks for the part's real size, then sizes the blank.
 *
 * The size prompt lives here because most STLs carry no units (the test models
 * are all ~1 unit across), and every stock figure is meaningless without it.
 * The analysis is scale-invariant, so changing the size re-computes instantly
 * with no round-trip to the server.
 */
export function StockCard({ geo, verdict, looksLikeMm, onStockChange }: Props) {
  const longestUnits = Math.max(...(geo?.extents ?? [1]))
  // If the file already looks like millimetres, trust it; otherwise ask.
  const [longestMm, setLongestMm] = useState<string>(
    looksLikeMm ? String(Math.round(longestUnits)) : '',
  )

  const mmPerUnit = useMemo(() => {
    const v = parseFloat(longestMm)
    return v > 0 && longestUnits > 0 ? v / longestUnits : 0
  }, [longestMm, longestUnits])

  const advice = useMemo(
    () => (mmPerUnit ? recommendStock(geo, verdict, mmPerUnit) : null),
    [geo, verdict, mmPerUnit],
  )

  useEffect(() => { onStockChange?.(advice ? advice.mm : null) }, [advice, onStockChange])

  return (
    <div className="plan-card">
      <div className="plan-card__title">📦 Stock</div>

      <label className="scale-input">
        Longest dimension of the finished part
        <span>
          <input
            type="number" min="1" step="1" placeholder="e.g. 180"
            value={longestMm}
            onChange={(e) => setLongestMm(e.target.value)}
          />
          mm
        </span>
      </label>

      {!advice ? (
        <p className="plan-card__meta" style={{ marginTop: 10 }}>
          {looksLikeMm
            ? 'Enter the finished size to get a stock recommendation.'
            : 'This STL has no real-world scale, so enter how big the part should actually be.'}
        </p>
      ) : (
        <>
          <p className="plan-card__body" style={{ marginTop: 10 }}>
            Buy <strong>{advice.label}</strong>
            {advice.type === 'round_bar' ? ' — turned between centres.' : '.'}
          </p>
          <ul className="stock-lines">
            {advice.lines.map((l, i) => <li key={i}>{l}</li>)}
            {advice.removedFraction !== null && (
              <li>~{Math.round(advice.removedFraction * 100)}% of the blank becomes chips</li>
            )}
          </ul>
          <div className="plan-card__meta">
            Snapped up to a commonly stocked size. Allowances assume screw-down
            work-holding; adjust if you use vacuum or tape.
          </div>
        </>
      )}
    </div>
  )
}
