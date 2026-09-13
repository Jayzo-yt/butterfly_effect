const LEVEL_IN_LABEL = /\s*\((critical|high|moderate|low)\)/

function clock(minutes) {
  if (minutes === 0) return 'T+0'
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${m}` : `${h} h`
}

/** How the consequences arrive. Times are measured — responder drive times,
 *  each network's storage delay, the stated duration — not a fixed script
 *  replayed for every incident.
 *
 *  The engine writes a level into the label, e.g. "7 assets lose water supply
 *  (moderate)". It reads better as the row's edge than as a word in the text. */
export default function Timeline({ timeline }) {
  const span = Math.max(...timeline.map((e) => e.minute), 1)

  return (
    <>
      <div className="block-head">How it unfolds<b>to {clock(span)}</b></div>
      <ol className="timeline">
        {timeline.map((event, i) => {
          const level = LEVEL_IN_LABEL.exec(event.label)?.[1] ?? 'low'
          return (
            <li key={i}>
              <span className="timeline-time">{clock(event.minute)}</span>
              <div className="timeline-body" data-level={event.level ?? level}>
                <strong>{event.label.replace(LEVEL_IN_LABEL, '')}</strong>
                <p>{event.detail}</p>
                {event.basis && <p className="provenance">{event.basis}</p>}
              </div>
            </li>
          )
        })}
      </ol>
    </>
  )
}
