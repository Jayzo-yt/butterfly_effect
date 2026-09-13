const BAND_LABEL = {
  critical: 'Critical', high: 'High', moderate: 'Moderate', low: 'Low', none: 'No impact',
}

function Figure({ value, label, level, note }) {
  return (
    <div>
      <div className={`v fig${level ? ` lv-${level}` : ''}`}>{value}</div>
      <div className="k">{label}</div>
      {note && <div className="k k-note">{note}</div>}
    </div>
  )
}

/** The verdict, then the detail.
 *
 *  Score, band and the single most urgent action sit together at the top,
 *  because "what do I do now" is the question the tool exists to answer and it
 *  used to be three tabs away. */
export default function Verdict({ result, onCollapse }) {
  const { incidents, score, areas, actions } = result
  const lead = incidents[0]
  const band = score.level === 'none' ? 'clear' : score.level
  const first = actions[0]

  return (
    <div className="verdict" style={{ '--band': `var(--${band})` }}>
        <button className="collapse" onClick={onCollapse} title="Put the panel away"
                aria-label="Put the panel away">›</button>

        <div className="verdict-top">
          <div>
            <div className="verdict-score fig">{score.value}</div>
            <span className="verdict-of">of 100</span>
          </div>
          <div className="verdict-what">
            <div className="verdict-band">{BAND_LABEL[score.level] ?? score.level} risk</div>
            <h2 className="verdict-where">
              {incidents.length === 1 ? lead.label : `${incidents.length} incidents at once`}
            </h2>
            <p className="verdict-meta">
              {incidents.map((i) => i.target.name).join(' + ')}
              {areas?.areas?.length ? `, ${areas.areas[0].name}` : ''}
              {` · ${lead.severity}, ${lead.duration_hours} h`}
            </p>
          </div>
        </div>

      {first && (
        <div className="verdict-next">
          <h3>Do this now</h3>
          <p>{first.text} <em>{first.basis}</em></p>
        </div>
      )}
    </div>
  )
}

/** Everything the verdict summarises. Lives inside the Impact tab rather than
 *  permanently above the tabs, which is what made the panel four screens tall
 *  and pushed the tab strip below the fold. */
export function ImpactDetail({ result }) {
  const {
    summary, direct, network, population, emergency,
    evacuation, alternatives, confidence, cascade, score,
  } = result

  return (
    <>
        {/* What happened to the struck asset itself. */}
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

        {summary.affected_total === 0 && <p className="explain">{summary.explanation}</p>}
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

        {/* Why the score is what it is. A band with no arithmetic is a mood. */}
        {score.factors.length > 0 && (
          <div className="block">
            <div className="block-head">Why this score<b className="fig">{score.value}</b></div>
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
            <div className="block-head">
              Emergency response
              {emergency.status && (
                <b className={emergency.status === 'normal' ? 'lv-none'
                  : emergency.status === 'elevated' ? 'lv-moderate' : 'lv-critical'}>
                  {emergency.status}
                </b>
              )}
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
                    <strong className="lv-high">
                      ~{d.response_low_min}–{d.response_high_min} min
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
            <div className="block-head">Where its demand goes<b>{item.category_label}</b></div>
            {item.peers.map((p) => (
              <div key={p.id} className="row">
                <span>{p.name}</span>
                <strong>
                  {p.in_cordon ? <em>inside cordon</em>
                    : p.reachable ? `${p.travel_min} min` : <em>no route</em>}
                  {p.capacity ? <em> · {p.capacity} capacity</em> : ''}
                </strong>
              </div>
            ))}
            {item.capacity_note && <p className="provenance">{item.capacity_note}</p>}
          </div>
        ))}

        {evacuation?.required && evacuation.sites.map((site) => (
          <div key={site.id} className="block">
            <div className="block-head">
              Evacuating {site.name}
              {site.people && <b>~{site.people.toLocaleString()} people</b>}
            </div>
            {site.destinations.length ? site.destinations.map((d) => (
              <div key={d.id} className="dispatch">
                <div className="row">
                  <span>{d.name}</span>
                  <strong>
                    {d.in_cordon ? <em>inside cordon</em>
                      : d.walk_min != null ? `${d.walk_min} min walk` : <em>no route</em>}
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

      <div className="confidence">
        <div className="block-head">
          Confidence by component
          <b className={`lv-${confidence.level === 'high' ? 'none'
            : confidence.level === 'medium' ? 'moderate' : 'high'}`}>
            {confidence.level} overall
          </b>
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
