const WHEN = {
  critical: 'Immediate', high: 'Within minutes', moderate: 'Prepare', low: 'Longer term',
}

/** Grouped by how soon, edged by how serious — the same severity grammar the
 *  rest of the panel uses. Every line is a consequence of something the
 *  simulation measured, and says underneath what it rests on. */
export default function ActionList({ actions }) {
  const groups = ['critical', 'high', 'moderate', 'low']
    .map((p) => [p, actions.filter((a) => a.priority === p)])
    .filter(([, list]) => list.length)

  return (
    <>
      <div className="head">
        <span className="cap">Recommended actions</span>
        <b>{actions.length}</b>
      </div>

      {groups.map(([priority, list]) => (
        <div key={priority} className="action-group">
          <h3 className={`action-heading lv-${priority}`}>
            {WHEN[priority]}
            <span>{list.length}</span>
          </h3>
          <ul className="action-list">
            {list.map((a, i) => (
              <li key={i} data-level={priority}>
                <div className="action-where">{a.area}</div>
                <p className="action-text">{a.text}</p>
                <p className="action-basis">{a.basis}</p>
              </li>
            ))}
          </ul>
        </div>
      ))}

      <p className="provenance">
        Each line is produced by a condition this result met. Nothing here is a standing
        recommendation.
      </p>
    </>
  )
}
