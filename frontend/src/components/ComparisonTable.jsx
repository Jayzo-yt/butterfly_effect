function format(value, unit) {
  if (value === null || value === undefined) return '—'
  if (typeof value !== 'number') return value
  return unit === '%' ? `${value}%` : unit ? `${value} ${unit}` : `${value}`
}

function change(row) {
  if (row.change === null || row.change === undefined) return '—'
  const sign = row.change > 0 ? '+' : ''
  return `${sign}${Math.round(row.change * 10) / 10}${row.unit === '%' ? '%' : ''}`
}

/** Normal against now, on the same measures and from the same routing, so each
 *  line is a difference rather than two separate opinions. */
export default function ComparisonTable({ comparison, confidence }) {
  return (
    <>
      <div className="head">
        <span className="cap">Normal conditions vs now</span>
        <b>{comparison.rows.length} measures</b>
      </div>

      <div className="table-scroll">
        <table className="compare">
          <thead>
            <tr>
              <th>Measure</th>
              <th>Normal</th>
              <th>Now</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            {comparison.rows.map((row) => (
              <tr key={row.metric}>
                <th scope="row">
                  {row.metric}
                  {row.basis && <em>{row.basis}</em>}
                </th>
                <td className="fig">{format(row.baseline, row.unit)}</td>
                <td className="fig">{format(row.disrupted, row.unit)}</td>
                <td className={`fig change-${row.direction}`}>{change(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="provenance">{comparison.note}</p>

      <details className="technical" open>
        <summary>How much to trust these figures ({confidence.level} confidence)</summary>
        <ul className="gap-list">
          {confidence.reasons.map((r, i) => (
            <li key={i} data-level={r.level === 'high' ? 'none' : r.level === 'medium' ? 'moderate' : 'high'}>
              <b className={`lv-${r.level === 'high' ? 'none' : r.level === 'medium' ? 'moderate' : 'high'}`}>
                {r.level}
              </b>{' '}
              {r.text}
            </li>
          ))}
        </ul>
        <p className="provenance">{confidence.note}</p>
      </details>
    </>
  )
}
