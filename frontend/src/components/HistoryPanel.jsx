function when(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** Past runs, kept in this browser. Replaying re-runs the analysis against the
 *  current network rather than showing a stored picture, so the answer is
 *  always the one the model gives now. */
export default function HistoryPanel({ history, onReplay }) {
  if (!history.length) return null

  return (
    <section className="panel">
      <div className="head">
        <span className="cap">Previous runs</span>
        <b>{history.length}</b>
      </div>
      <ul className="history">
        {history.map((entry) => (
          <li key={entry.id}>
            <button onClick={() => onReplay(entry)}>
              <span className={`history-score lv-${entry.level}`}>{entry.score}</span>
              <span className="history-title">{entry.title}</span>
              <em>{when(entry.at)}</em>
            </button>
          </li>
        ))}
      </ul>
      <p className="provenance">Stored in this browser only. Replaying re-runs the analysis.</p>
    </section>
  )
}
