import { useEffect } from 'react'

const DURATION_LABEL = {
  0.25: '15 minutes', 0.5: '30 minutes', 1: '1 hour', 2: '2 hours',
  6: '6 hours', 12: '12 hours', 24: '24 hours',
}

/** Group the incident catalogue the way an operator thinks about it, and only
 *  offer what can actually happen to the thing they selected. */
function applicable(types, target) {
  if (!target) return types
  return types.filter((t) => (
    target.kind === 'road'
      ? t.targets.includes('road')
      : t.targets.includes('asset')
        && (!t.asset_categories.length || t.asset_categories.includes(target.category))
  ))
}

export default function IncidentBuilder({
  options, draft, setDraft, target, clearTarget, queued, removeQueued,
  onAdd, onRun, running, error,
}) {
  const usable = applicable(options?.types ?? [], target)
  const chosen = usable.find((t) => t.key === draft.type) ?? usable[0]

  // Selecting a water tower while "road accident" is chosen would otherwise
  // leave an impossible pairing on screen. Corrected after render, never
  // during it.
  useEffect(() => {
    if (chosen && chosen.key !== draft.type) setDraft({ ...draft, type: chosen.key })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chosen?.key])

  if (!options) {
    return (
      <section className="panel">
          <div className="console-lead">
          <h2>Scenario</h2>
        </div>
        <p className="note">Loading the incident catalogue…</p>
      </section>
    )
  }

  const groups = [...new Set(usable.map((t) => t.group))]

  return (
    <section className="panel">

      <div className="console-lead">
        <h2>Scenario</h2>
        <p>{queued.length ? `${queued.length} queued` : 'Build a disruption'}</p>
      </div>

      <div className="field">
        <span className="cap">Target</span>
        {target ? (
          <div className="picked">
            <strong>{target.name}</strong>
            <em>{target.detail}{target.kind === 'road' && !target.named_in_osm
              ? ' · unnamed in OpenStreetMap' : ''}</em>
            <button className="link-quiet" onClick={clearTarget}>Choose something else</button>
          </div>
        ) : (
          <p className="picked picked-empty">
            Search above, or click a road or asset on the map.
          </p>
        )}
      </div>

      <label className="field">
        <span className="cap">Incident</span>
        <select
          value={chosen?.key ?? ''}
          onChange={(e) => setDraft({ ...draft, type: e.target.value, radius_m: null })}
          disabled={!target}
        >
          {groups.map((group) => (
            <optgroup key={group} label={group}>
              {usable.filter((t) => t.group === group).map((t) => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
        {chosen && <small className="field-note">{chosen.note}</small>}
      </label>

      <label className="field">
        <span className="cap">Severity</span>
        <select value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value })}>
          {options.severities.map((s) => (
            <option key={s} value={s}>
              {s[0].toUpperCase() + s.slice(1)} — {options.severity_help?.[s]}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span className="cap">Duration</span>
        <select
          value={draft.duration_hours}
          onChange={(e) => setDraft({ ...draft, duration_hours: Number(e.target.value) })}
        >
          {options.durations_hours.map((h) => (
            <option key={h} value={h}>{DURATION_LABEL[h] ?? `${h} hours`}</option>
          ))}
        </select>
      </label>

      <label className="field">
        <span className="cap">
          Impact area
          <em>{draft.radius_m ? 'set manually' : 'auto'}</em>
        </span>
        {!draft.radius_m && (
          <div className="zone-readout">
            <b className="display">{chosen?.base_radius_m ?? 800}</b>
            <span>m base, scaled by severity and duration</span>
          </div>
        )}
        <input
          type="number"
          min="50"
          max="20000"
          step="100"
          placeholder="Override in metres"
          value={draft.radius_m ?? ''}
          onChange={(e) => setDraft({
            ...draft,
            radius_m: e.target.value ? Number(e.target.value) : null,
          })}
        />
      </label>

      {/* Several incidents are one situation, so they are numbered as one
          scenario rather than listed as separate jobs. */}
      {queued.length > 0 && (
        <ul className="queued">
          {queued.map((q, i) => (
            <li key={i} data-level={q.severity === 'critical' ? 'critical'
              : q.severity === 'high' ? 'high' : 'moderate'}>
              <span className="queued-n">Incident {String(i + 1).padStart(2, '0')}</span>
              <span>{q._label}</span>
              <em>{options.types.find((t) => t.key === q.type)?.label} · {q.severity} · {q.duration_hours} h</em>
              <button className="link-quiet" onClick={() => removeQueued(i)} aria-label="Remove">
                remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <div className="banner">{error}</div>}

      <div className="acts">
        <button
          className="btn btn-primary"
          onClick={onRun}
          disabled={running || (!target && !queued.length)}
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
            <path d="M6 4l14 8-14 8z" />
          </svg>
          {running ? 'Running' : queued.length ? 'Run all incidents' : 'Run simulation'}
        </button>
        <button className="btn" onClick={onAdd} disabled={!target}>
          Add another
        </button>
      </div>

      {queued.length > 0 && (
        <p className="note">
          Incidents run together on one network, so their effects combine rather than being
          evaluated one at a time.
        </p>
      )}
    </section>
  )
}
