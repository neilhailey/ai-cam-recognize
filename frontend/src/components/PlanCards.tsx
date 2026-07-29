import type { Dimensions, Mounting, OrientationResult, Rotary, SetupPlan, ToolingResult, Verdict } from '../types'

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

export function SetupPlanCard(
  { plan, verdict, rotary }: { plan: SetupPlan; verdict?: Verdict; rotary?: Rotary },
) {
  if (verdict === '4-axis') {
    return (
      <div className="plan-card">
        <div className="plan-card__title">🔁 Setup plan</div>
        <p className="plan-card__body">
          <strong>One rotary setup.</strong> Held between chuck and tailstock, the part
          turns to present every side{rotary?.axis ? '' : ''} — the whole form is cut in a
          single run.
        </p>
        <div className="plan-card__meta">
          A small tab is left at each end where it is gripped; those are trimmed off
          afterwards.
        </div>
      </div>
    )
  }
  return (
    <div className="plan-card">
      <div className="plan-card__title">🔁 3-axis setup plan</div>
      <p className="plan-card__body">
        {plan.n_setups === 0 ? (
          'No single-direction setup reaches this part.'
        ) : plan.fully_covered ? (
          plan.n_setups === 1
            ? <>Fully cuttable in a <strong>single 3-axis setup</strong> — no flip needed.</>
            : <>Fully cuttable in <strong>{plan.n_setups} 3-axis setups</strong> ({plan.n_setups - 1} flip{plan.n_setups > 2 ? 's' : ''}).</>
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

export function MountingCard(
  { mounting, verdict }: { mounting: Mounting; rotary?: Rotary; verdict: Verdict },
) {
  const how = verdict === '4-axis'
    ? 'Held between chuck and tailstock by waste stock on each end'
    : 'Clamped flat on the bed'
  const found = mounting.source === 'flat-face'
    ? `largest flat face (${mounting.area_pct}% of the surface)`
    : 'no flat face found — resting on its flattest side'
  return (
    <div className="plan-card">
      <div className="plan-card__title">🧲 Assumed mounting</div>
      <p className="plan-card__body">
        {how}, on the {found}.
      </p>
      <div className="plan-card__meta">
        Shown in the viewer{verdict === '4-axis'
          ? ' — the tan stubs are waste stock for the chuck and tailstock, cut off afterwards'
          : ''}.
        {mounting.flipped ? ' Model is flipped.' : ''} Use “Flip model” to mount the other way up.
      </div>
    </div>
  )
}

export function ToolingCard(
  { tooling, dimensions }: { tooling: ToolingResult; dimensions?: Dimensions },
) {
  // STL files carry no units. Anything under ~10 across is almost certainly a
  // normalised export rather than millimetres, so don't claim mm we can't back up.
  const mm = dimensions?.looks_like_mm ?? true
  const unit = mm ? 'mm' : 'units'
  const size = dimensions?.extents?.map((e) => e.toFixed(mm ? 0 : 2)).join(' × ')
  return (
    <div className="plan-card">
      <div className="plan-card__title">🔩 Tooling</div>
      <p className="plan-card__body">
        {tooling.limited ? (
          <>
            Use a cutter around <strong>⌀{tooling.max_tool_diameter} {unit}</strong> or
            smaller to reach the fine detail — larger tools miss the tightest features.
          </>
        ) : (
          <>No fine-detail limit — even a large cutter reaches every exposed surface.</>
        )}
      </p>
      <div className="plan-card__meta">
        Approximate guidance from feature accessibility.{' '}
        {size && <>Model measures {size} {unit}. </>}
        {mm
          ? 'Assumes your STL is modelled in millimetres.'
          : 'This STL has no real-world scale (it is normalised), so sizes are in file units — scale it before cutting.'}
      </div>
    </div>
  )
}
