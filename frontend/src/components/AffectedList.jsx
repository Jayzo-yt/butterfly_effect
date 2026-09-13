import { useState } from 'react'

/** What the incident did to each facility, and why.
 *
 *  Severity is the left edge, not a chip — the same grammar the actions, the
 *  cascade and the timeline use. Three states an operator has to tell apart:
 *  the incident site itself, a facility inside the cordon (reachable again once
 *  the cordon lifts), and one genuinely severed from the network. */
export default function AffectedList({ result, onFocus }) {
  const [open, setOpen] = useState(null)
  if (!result) return null

  const affected = result.facilities.filter((f) => f.level !== 'none')
  const nearby = result.facilities.filter((f) => f.level === 'none')
  if (!affected.length && !nearby.length) return null

  const state = (f) => {
    if (f.is_incident_site) return 'incident site'
    if (f.in_cordon) return 'in cordon'
    if (f.cut_off) return 'cut off'
    return f.added_min > 0 ? `+${f.added_min} min` : '—'
  }

  const row = (f) => {
    const isOpen = open === f.id
    return (
      <li key={f.id} data-level={f.is_incident_site ? 'critical' : f.level}>
        <button
          className="affected-head"
          aria-expanded={isOpen}
          onClick={() => { setOpen(isOpen ? null : f.id); onFocus?.([f.lat, f.lon]) }}
        >
          <span className="affected-name">{f.name}</span>
          <span className={`affected-delta fig lv-${f.level}`}>{state(f)}</span>
          <span className="affected-sub">
            {f.category_label}{f.address ? `, ${f.address}` : ''}
          </span>
        </button>

        {isOpen && (
          <div className="affected-detail">
            <p className="why">{f.reason}</p>
            <dl className="pairs">
              <div>
                <dt>Distance from incident</dt>
                <dd className="fig">{(f.distance_m / 1000).toFixed(2)} km</dd>
              </div>
              {f.baseline_min != null && (
                <div>
                  <dt>Emergency access</dt>
                  <dd className="fig">
                    {f.baseline_min} min → {f.cut_off ? 'no route' : `${f.after_min} min`}
                  </dd>
                </div>
              )}
              <div><dt>Impact score</dt><dd className="fig">{f.score}</dd></div>
            </dl>
            {/* Identifiers kept, but out of the operator's way. */}
            <details className="technical">
              <summary>Technical details</summary>
              <dl className="pairs">
                <div><dt>Asset ID</dt><dd className="fig">{f.id}</dd></div>
                <div><dt>Category key</dt><dd className="fig">{f.category}</dd></div>
                <div>
                  <dt>Coordinates</dt>
                  <dd className="fig">{f.lat.toFixed(5)}, {f.lon.toFixed(5)}</dd>
                </div>
                <div><dt>Source</dt><dd>OpenStreetMap</dd></div>
              </dl>
            </details>
          </div>
        )}
      </li>
    )
  }

  return (
    <>
      <div className="block-head">
        {affected.length ? 'Affected facilities' : 'Facilities checked'}
        <b>{affected.length} of {result.facilities.length}</b>
      </div>

      {!affected.length && (
        <p className="note">
          None of the {nearby.length} facilities in range lost access.
        </p>
      )}

      {affected.length > 0 && <ul className="affected">{affected.map(row)}</ul>}

      {/* The unaffected ones stay available — an operator should be able to see
          exactly what was checked — but a dozen near-identical "open space,
          access unchanged" rows at the end of the list is noise, not evidence. */}
      {nearby.length > 0 && (
        <details className="technical">
          <summary>{nearby.length} more in range, access unchanged</summary>
          <ul className="affected">{nearby.map(row)}</ul>
        </details>
      )}
    </>
  )
}
