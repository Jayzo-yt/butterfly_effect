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
        <h2 className="panel-title">Report an incident</h2>
        <p className="note">Loading the incident catalogue…</p>
      </section>
    )
  }

  const groups = [...new Set(usable.map((t) => t.group))]


  return (
    <section className="panel">
      <h2 className="panel-title">
        Report an incident
        <small>{queued.length ? `${queued.length} queued` : ''}</small>
      </h2>

      <div className="field">
        <span>What is affected</span>
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
        <span>What happened</span>
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
        <span>How bad</span>
        <select value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value })}>
          {options.severities.map((s) => (
            <option key={s} value={s}>
              {s[0].toUpperCase() + s.slice(1)} — {options.severity_help?.[s]}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span>Expected duration</span>
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
        <span>
          Impact zone
          <small>{draft.radius_m ? 'set manually' : 'calculated'}</small>
        </span>
        <input
          type="number"
          min="50"
          max="20000"
          step="100"
          placeholder="Calculated automatically"
          value={draft.radius_m ?? ''}
          onChange={(e) => setDraft({
            ...draft,
            radius_m: e.target.value ? Number(e.target.value) : null,
          })}
        />
        <small className="field-note">
          Metres. Left blank, the engine scales this incident type's base radius
          ({chosen?.base_radius_m ?? 800} m) by severity and duration.
        </small>
      </label>

      {queued.length > 0 && (
        <ul className="queued">
          {queued.map((q, i) => (
            <li key={i}>
              <span>{q._label}</span>
              <em>{options.types.find((t) => t.key === q.type)?.label} · {q.severity}</em>
              <button className="link-quiet" onClick={() => removeQueued(i)} aria-label="Remove">
                remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <div className="banner">{error}</div>}

      <div className="actions">
        <button
          className="btn btn-primary"
          onClick={onRun}
          disabled={running || (!target && !queued.length)}
        >
          {running ? 'Simulating…' : queued.length ? 'Run all incidents' : 'Run simulation'}
        </button>
        <button className="btn btn-quiet" onClick={onAdd} disabled={!target}>
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
