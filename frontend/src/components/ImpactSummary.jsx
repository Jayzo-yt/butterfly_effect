import { useEffect, useRef, useState } from 'react'
import { LEVEL_RANK, primaryConsequence } from '../consequence'

const BAND_LABEL = {
  critical: 'Critical', high: 'High', moderate: 'Moderate', low: 'Low', none: 'No impact',
}

/** The engine's own band cut-offs, from `_score` in src/analysis/scenario.py.
 *  Drawing them is what turns the score from a number into a reading: you can
 *  see not just that it is 46, but that 46 is eleven points into "high" and
 *  fourteen short of "critical". */
const BANDS = [
  { key: 'low', from: 0, to: 15 },
  { key: 'moderate', from: 15, to: 35 },
  { key: 'high', from: 35, to: 60 },
  { key: 'critical', from: 60, to: 100 },
]

function Figure({ value, label, level, note }) {
  return (
    <div>
      <div className={`v display${level ? ` lv-${level}` : ''}`}>{value}</div>
      <div className="k">{label}</div>
      {note && <div className="k k-note">{note}</div>}
    </div>
  )
}

/** Climb to the reading rather than snapping to it — the same 620 ms and the
 *  same easing as the needle, so the figure and the pointer arrive together.
 *  Honoured only when the viewer allows motion; otherwise the value is simply
 *  correct from the first frame. */
function useClimb(target, ms = 620) {
  const still = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const [shown, setShown] = useState(still ? target : 0)
  const frame = useRef(0)

  useEffect(() => {
    if (still) { setShown(target); return undefined }
    const start = performance.now()
    const from = 0
    const step = (now) => {
      const t = Math.min(1, (now - start) / ms)
      const eased = 1 - (1 - t) ** 3
      setShown(Math.round(from + (target - from) * eased))
      if (t < 1) frame.current = requestAnimationFrame(step)
    }
    frame.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame.current)
  }, [target, ms, still])

  return shown
}

function Dial({ score }) {
  const band = score.level === 'none' ? 'clear' : score.level
  const reached = LEVEL_RANK[score.level] ?? 0
  const shown = useClimb(score.value)

  return (
    <div className="dial">
      <div className="dial-top">
        <span className="dial-score display">{shown}</span>
        <span className="dial-of">of 100</span>
        <span className="dial-band">{BAND_LABEL[score.level] ?? score.level}</span>
      </div>

      <div className="dial-scale">
        <div className="dial-track">
          {BANDS.map((b) => (
            <span
              key={b.key}
              className="dial-seg"
              data-on={LEVEL_RANK[b.key] <= reached}
              style={{ flex: b.to - b.from, color: `var(--${b.key})` }}
            />
          ))}
        </div>
        <span
          className="dial-needle"
          style={{ left: `${shown}%`, '--band': `var(--${band})` }}
        />
        <div className="dial-ticks">
          {BANDS.map((b) => (
            <span key={b.key} style={{ flex: b.to - b.from }}>
              {b.key[0].toUpperCase()}{b.key.slice(1)}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

/** The verdict: what happened, how bad, what it reached, what to do.
 *
 *  Four readings in the order an incident room asks for them. Everything else
 *  — the arithmetic, the responders, the evidence — sits behind the tabs. */
export default function Verdict({ result, onCollapse, onFocus }) {
  const { incidents, score, areas } = result
  const band = score.level === 'none' ? 'clear' : score.level
  const lead = incidents[0]
  const consequence = primaryConsequence(result)

  return (
    <div className="verdict" style={{ '--band': `var(--${band})` }}>
      <button className="collapse" onClick={onCollapse} title="Put the panel away"
              aria-label="Put the panel away">›</button>

      <div className="verdict-what">
        <span className="verdict-kind">
          {incidents.length === 1 ? 'Active incident' : `${incidents.length} incidents at once`}
        </span>
        <h2 className="verdict-where">
          {incidents.length === 1 ? lead.label : incidents.map((i) => i.type_label).join(' + ')}
        </h2>
        <p className="verdict-target">{incidents.map((i) => i.target.name).join(' + ')}</p>
        <p className="verdict-meta">
          {lead.severity} · {lead.duration_hours} h
          {areas?.areas?.length ? ` · ${areas.areas[0].name}` : ''}
        </p>
      </div>

      <Dial score={score} />

      <div className={`consequence${consequence.kind === 'none' ? ' consequence-quiet' : ''}`}>
        <span className="cap">
          {consequence.kind === 'cascade' ? 'Furthest consequence'
            : consequence.kind === 'access' ? 'Worst consequence'
              : 'Consequence'}
        </span>
        {consequence.lat != null ? (
          <button
            className="consequence-link"
            onClick={() => onFocus?.([consequence.lat, consequence.lon])}
            title="Show on the map"
          >
            <strong>{consequence.name}</strong>
          </button>
        ) : (
          <strong>{consequence.name}</strong>
        )}
        {consequence.what && <p className={`lv-${consequence.level}`}>{consequence.what}</p>}
        <p>{consequence.note}</p>
      </div>

    </div>
  )
}

/** Everything the verdict summarises. Lives inside the Impact tab rather than
 *  permanently above the tabs, which is what made the panel four screens tall
 *  and pushed the tab strip below the fold. */
export function ImpactDetail({ result }) {
  const {
    summary, direct, network, population, emergency,
    evacuation, alternatives, confidence, cascade, score, actions,
  } = result
  const first = actions[0]

  return (
    <>
      {first && (
        <div className="verdict-next">
          <div className="head"><span className="cap">Do this now</span></div>
          <p>{first.text}</p>
          <p className="provenance">{first.basis}</p>
        </div>
      )}

      {direct.map((d) => (
        <div key={d.id} className="direct" data-level={d.function_lost_pct >= 90 ? 'critical' : 'high'}>
          <div className="direct-head">
            <strong>{d.name}</strong>
            <span className={d.function_lost_pct >= 90 ? 'lv-critical' : 'lv-high'}>{d.status}</span>
          </div>
          <p>
            {d.function_lost_pct}% of its {d.service_lost || 'function'} lost
            {d.occupancy_finding?.value != null
              ? `; ${d.occupancy_finding.display} typically present`
              : d.occupancy_finding ? '; occupancy unknown' : ''}.
          </p>
          {d.occupancy_basis && <p className="provenance">{d.occupancy_basis}.</p>}
        </div>
      ))}

      {summary.affected_total === 0 && primaryConsequence(result).kind !== 'none'
        && <p className="explain">{summary.explanation}</p>}
      {network?.severed && <p className="explain explain-warn">{network.note}</p>}

      <div className="readout">
        <Figure
          value={summary.affected_total}
          label="facilities with changed access"
          level={summary.affected_total ? 'critical' : 'none'}
        />
        <Figure
          value={cascade.indirect_total}
          label="assets affected downstream"
          level={cascade.indirect_total ? 'high' : 'none'}
        />
        {network?.added_travel_min != null && (
          <Figure
            value={`+${network.added_travel_min}`}
            label="min detour on this route"
            level={network.added_travel_min > 1 ? 'moderate' : 'none'}
          />
        )}
        {network?.segments_closed > 0 && (
          <Figure value={network.segments_closed} label="streets closed in the cordon" level="moderate" />
        )}
        {network?.diverted_count > 0 && (
          <Figure value={network.diverted_count} label="roads absorbing traffic" />
        )}
        {population?.estimated_people != null && (
          <Figure
            value={population.estimated_people.toLocaleString()}
            label="people in zone"
            note="estimate"
          />
        )}
      </div>

      {score.factors.length > 0 && (
        <div className="block">
          <div className="head">
            <span className="cap">Why this score</span>
            <b>{score.value} of 100</b>
          </div>
          <ul className="factors">
            {score.factors.slice(0, 5).map((f) => (
              <li key={f.label}>
                <span className="factor-bar" style={{ width: `${Math.min(100, f.points * 3)}%` }} />
                <span className="factor-label">{f.label}</span>
                <span className="factor-points fig">+{f.points}</span>
                <span className="factor-detail">{f.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {emergency?.dispatch?.length > 0 && (
        <div className="block">
          <div className="head">
            <span className="cap">Emergency response</span>
            {emergency.status && <b>{emergency.status}</b>}
          </div>
          {emergency.dispatch.map((d) => (
            <div key={d.category} className="dispatch">
              <div className="row">
                <span>{d.category_label}</span>
                {!d.available ? (
                  <strong className="lv-critical">none mapped</strong>
                ) : d.unreachable ? (
                  <strong className="lv-critical">no route</strong>
                ) : (
                  <strong className="lv-high fig">
                    {d.response_low_min}–{d.response_high_min} min
                  </strong>
                )}
              </div>
              {d.available && !d.unreachable && (
                <p className="dispatch-detail">
                  {d.station} · {d.travel_min} min road travel
                  {d.overhead_min ? ` + ${d.overhead_min} min call handling and turnout` : ''}
                  {d.added_min > 0 ? `, ${d.added_min} min slower than normal` : ''}
                  {d.to_cordon_edge ? ', timed to the cordon edge' : ''}
                  {d.availability === 'unknown' ? ' · availability unknown' : ''}
                  {d.availability === 'busy' ? ' · units committed elsewhere' : ''}
                </p>
              )}
              {!d.available && (
                <p className="dispatch-detail">
                  No facility of this kind is mapped in the extract, so response cannot be
                  estimated. This is a data limitation, not a finding of zero cover.
                </p>
              )}
              {d.note && <p className="provenance">{d.note}</p>}
            </div>
          ))}
          <p className="provenance">
            Response time is road travel plus call handling and turnout, with the upper bound
            allowing for congestion. Road travel alone is not a response time.
          </p>
        </div>
      )}

      {alternatives?.available && alternatives.items.map((item) => (
        <div key={item.id} className="block">
          <div className="head">
            <span className="cap">Where its demand goes</span>
            <b>{item.category_label}</b>
          </div>
          {item.peers.map((p) => (
            <div key={p.id} className="row">
              <span>{p.name}</span>
              <strong>
                {p.in_cordon ? <em>inside cordon</em>
                  : p.reachable ? <span className="fig">{p.travel_min} min</span> : <em>no route</em>}
                {p.capacity ? <em> · {p.capacity} capacity</em> : ''}
              </strong>
            </div>
          ))}
          {item.capacity_note && <p className="provenance">{item.capacity_note}</p>}
        </div>
      ))}

      {evacuation?.required && evacuation.sites.map((site) => (
        <div key={site.id} className="block">
          <div className="head">
            <span className="cap">Evacuating {site.name}</span>
            {site.people && <b>~{site.people.toLocaleString()} people</b>}
          </div>
          {site.destinations.length ? site.destinations.map((d) => (
            <div key={d.id} className="dispatch">
              <div className="row">
                <span>{d.name}</span>
                <strong>
                  {d.in_cordon ? <em>inside cordon</em>
                    : d.walk_min != null ? <span className="fig">{d.walk_min} min walk</span>
                      : <em>no route</em>}
                </strong>
              </div>
              {!d.in_cordon && d.reachable && (
                <p className="dispatch-detail">
                  {d.route_m ?? d.distance_m} m
                  {d.crossings ? ` · ${d.crossings} junctions` : ''}
                  {' · '}
                  {d.designated ? 'designated shelter'
                    : 'mapped open ground, not a designated centre'}
                  {d.capacity != null
                    ? ` · holds about ${d.capacity.toLocaleString()}`
                    : ' · capacity unknown'}
                </p>
              )}
            </div>
          )) : <p className="explain explain-warn">{site.note}</p>}
          {site.capacity_state?.message && (
            <p className={`explain${site.capacity_state.state === 'insufficient' ? ' explain-warn' : ''}`}>
              {site.capacity_state.message}
            </p>
          )}
          {site.people_basis && <p className="provenance">{site.people_basis}.</p>}
        </div>
      ))}

      {/* Uncertainty as a reading of its own: which parts of this answer rest on
          measurement, and which on an assumption. */}
      <div className="block">
        <div className="head">
          <span className="cap">Confidence by component</span>
          <b>{confidence.level} overall</b>
        </div>
        {(confidence.components ?? []).map((c) => (
          <div key={c.key} className="row">
            <span>{c.label}</span>
            <strong className={`lv-${c.level === 'high' ? 'none'
              : c.level === 'medium' ? 'moderate' : 'high'}`}>{c.level}</strong>
          </div>
        ))}
        {confidence.limits && confidence.limits.length > 0 && (
          <p className="provenance">{confidence.limits[0]}</p>
        )}
      </div>
    </>
  )
}
