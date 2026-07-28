import type { OrientationResult, SetupPlan, ToolingResult } from '../types'

export function OrientationCard({ orientation }: { orientation: OrientationResult }) {
  return (
    <div className={`plan-card${orientation.improved ? ' plan-card--good' : ''}`}>
      <div className="plan-card__title">
        {orientation.improved ? '🧭 Better orientation found' : '🧭 Orientation'}
      </div>
      <p className="plan-card__body">{orientation.description}</p>
      {orientation.improved && (
        <div className="plan-card__meta">
          Rotate so <strong>{orientation.best_up_name}</strong> faces up →{' '}
          <strong>{orientation.best_verdict_label}</strong>
        </div>
      )}
    </div>
  )
}

export function SetupPlanCard({ plan }: { plan: SetupPlan }) {
  return (
    <div className="plan-card">
      <div className="plan-card__title">🔁 3-axis setup plan</div>
      <p className="plan-card__body">
        {plan.n_setups === 0 ? (
          'No single-direction setup reaches this part.'
        ) : plan.fully_covered ? (
          <>Fully cuttable in <strong>{plan.n_setups} 3-axis setup{plan.n_setups > 1 ? 's' : ''}</strong> (flips).</>
        ) : (
          <>
            <strong>{plan.n_setups}</strong> 3-axis setup{plan.n_setups > 1 ? 's' : ''} cover{' '}
            {(100 - plan.uncoverable_pct).toFixed(0)}%; <strong>{plan.uncoverable_pct.toFixed(1)}%</strong>{' '}
            needs true 5-axis.
          </>
        )}
      </p>
      {plan.setups.length > 0 && (
        <ol className="setup-steps">
          {plan.setups.map((s, i) => (
            <li key={i}>
              Setup {i + 1}: cut from <strong>{s.direction}</strong>
              <span className="setup-steps__cov"> → {s.cumulative_coverage_pct.toFixed(0)}% covered</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export function ToolingCard({ tooling }: { tooling: ToolingResult }) {
  return (
    <div className="plan-card">
      <div className="plan-card__title">🔩 Tooling</div>
      <p className="plan-card__body">
        {tooling.limited ? (
          <>
            Use a cutter around <strong>⌀{tooling.max_tool_diameter} mm</strong> or
            smaller to reach the fine detail — larger tools miss the tightest features.
          </>
        ) : (
          <>No fine-detail limit — even a large cutter reaches every exposed surface.</>
        )}
      </p>
      <div className="plan-card__meta">
        Approximate guidance from feature accessibility. Assumes your STL is modeled in
        millimeters (the CAD/CNC standard).
      </div>
    </div>
  )
}
