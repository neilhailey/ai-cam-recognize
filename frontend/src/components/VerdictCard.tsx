import type { StlReport } from '../types'

const STYLE: Record<string, { emoji: string; className: string; blurb: string }> = {
  '3-axis': {
    emoji: '✅',
    className: 'verdict verdict--3',
    blurb: 'Cuttable on a standard 3-axis router.',
  },
  '4-axis': {
    emoji: '🔄',
    className: 'verdict verdict--4',
    blurb: 'Needs a 4th (rotary) axis — a 3-axis router cannot reach everything.',
  },
  '5-axis': {
    emoji: '⚠️',
    className: 'verdict verdict--5',
    blurb: 'Has true undercuts — needs 5-axis (or splitting the model).',
  },
}

export function VerdictCard({ report }: { report: StlReport }) {
  const s = STYLE[report.verdict] ?? STYLE['5-axis']
  return (
    <div className={s.className}>
      <div className="verdict__emoji">{s.emoji}</div>
      <div className="verdict__body">
        <div className="verdict__label">{report.verdict_label}</div>
        <div className="verdict__blurb">{s.blurb}</div>
        {report.verdict === '4-axis' && report.rotary_length > 0 && (
          <div className="verdict__meta">
            Mounted along its longest dimension ({report.rotary_length})
          </div>
        )}
      </div>
    </div>
  )
}
