const LEVEL_IN_LABEL = /\s*\((critical|high|moderate|low)\)/

function clock(minutes) {
  if (minutes === 0) return 'T+0'
  if (minutes < 60) return `T+${minutes}`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `T+${h}h${String(m).padStart(2, '0')}` : `T+${h}h`
}

/** How the consequences arrive, along the bottom of the screen.
 *
 *  The same timeline the panel shows, laid horizontally so the shape of the
 *  incident — everything at once, or a slow chain over five hours — is legible
 *  without opening a tab. Times come from the engine: responder drive times,
 *  each network's storage delay, the duration the operator entered.
 */
export default function EventStrip({ result, onPick }) {
  const events = result?.timeline ?? []

  return (
    <div className="strip-rail">

      <div className="strip-lead">
        <span className="cap">Event sequence</span>
        {events.length ? (
          <b className="display">{clock(events[events.length - 1].minute)}</b>
        ) : (
          <b className="display" style={{ color: 'var(--faint)' }}>—</b>
        )}
      </div>

      {events.length ? (
        <div className="strip-track">
          {events.map((event, i) => {
            const level = event.level ?? LEVEL_IN_LABEL.exec(event.label)?.[1] ?? 'low'
            return (
              <button
                key={i}
                className="strip-event"
                data-level={level}
                style={{ animationDelay: `${Math.min(i, 9) * 45}ms` }}
                onClick={() => onPick?.()}
                title={event.detail}
              >
                <span className="strip-dot" />
                <span className="strip-t">{clock(event.minute)}</span>
                <span className="strip-label">{event.label.replace(LEVEL_IN_LABEL, '')}</span>
              </button>
            )
          })}
        </div>
      ) : (
        <p className="strip-empty">
          Run a scenario and the sequence of consequences appears here, timed from the
          responder routing and each network&rsquo;s propagation delay.
        </p>
      )}
    </div>
  )
}
