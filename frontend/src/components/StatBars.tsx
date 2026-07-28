import type { StlReport } from '../types'

interface Row {
  key: string
  label: string
  pct: number
  color: string
}

export function StatBars({ report }: { report: StlReport }) {
  const p = report.machinable_pct
  const rows: Row[] = [
    { key: '3', label: 'Reachable with 3-axis', pct: p['3axis'], color: '#4caf50' },
    { key: '4', label: 'Reachable with 4-axis', pct: p['4axis'], color: '#ffc107' },
    { key: '5', label: 'Reachable with 5-axis', pct: p['5axis'], color: '#f44336' },
  ]
  return (
    <div className="stats">
      <div className="stats__title">Machinable surface area (cumulative)</div>
      {rows.map((r) => (
        <div className="bar" key={r.key}>
          <div className="bar__label">{r.label}</div>
          <div className="bar__track">
            <div className="bar__fill" style={{ width: `${r.pct}%`, background: r.color }} />
          </div>
          <div className="bar__pct">{r.pct.toFixed(1)}%</div>
        </div>
      ))}

      <div className="flags">
        {report.enclosed_pct > 0.5 && (
          <span className="flag flag--muted">
            hollow model · {report.enclosed_pct.toFixed(0)}% internal void (excluded)
          </span>
        )}
        {report.vertical_wall_pct > 10 && (
          <span className="flag flag--warn">
            {report.vertical_wall_pct.toFixed(0)}% vertical wall (long tool)
          </span>
        )}
        <span className="flag flag--muted">{report.n_faces.toLocaleString()} faces · {report.ray_backend}</span>
      </div>

      <ul className="legend">
        <li><span className="dot" style={{ background: '#4caf50' }} /> 3-axis reachable</li>
        <li><span className="dot" style={{ background: '#ffc107' }} /> needs 4-axis (rotary)</li>
        <li><span className="dot" style={{ background: '#f44336' }} /> undercut / 5-axis</li>
        <li><span className="dot" style={{ background: '#212121' }} /> enclosed</li>
        <li><span className="dot" style={{ background: '#969696' }} /> mounting face (excluded)</li>
        <li><span className="dot" style={{ background: '#58a6ff' }} /> held in chuck</li>
      </ul>
    </div>
  )
}
