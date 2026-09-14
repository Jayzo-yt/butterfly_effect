import { useState } from 'react'
import { Glyph } from '../glyphs'
import { primaryConsequence } from '../consequence'
import { NETWORK_COLOR } from '../theme'

const LEVEL_COLOR = {
  critical: '#ff5f52', high: '#ff9040', moderate: '#e8b43c', low: '#7e8ca3', none: '#3cc583',
}

const NODE_W = 142
const NODE_H = 38
const GAP_X = 10
const GAP_Y = 16
const ROUND_GAP = 40
const HEADER_H = 16
const SHOWN_PER_ROUND = 8   // a diagram of twenty identical boxes says nothing

/** The single path the failure took, from what was hit to what it reached.
 *
 *  Twenty-three boxes tell you the size of a cascade; three tell you what it
 *  *is*. Walk back up `via_source` from the furthest consequence to the
 *  incident and you get the chain an operator repeats down the phone —
 *  substation, then the water tower it powers, then the hospital that tower
 *  supplies. Everything else stays below as the evidence for it.
 */
function chainTo(cascade, endId) {
  const byId = new Map(cascade.nodes.map((n) => [n.id, n]))
  const path = []
  let node = byId.get(endId)
  const seen = new Set()
  while (node && !seen.has(node.id)) {
    seen.add(node.id)
    path.unshift(node)
    node = node.via_source ? byId.get(node.via_source) : null
  }
  return path
}

/** Rounds run down the page, and the assets in a round wrap across it.
 *
 *  The first version put each round in its own column, which is the textbook
 *  drawing and was 1,150px tall the moment one substation fed twenty-three
 *  consumers. Rounds-as-rows fits the rail, and capping each row keeps the
 *  picture readable — the remainder is listed underneath rather than drawn.
 */
function layout(nodes, edges, width) {
  const perRow = Math.max(1, Math.floor((width + GAP_X) / (NODE_W + GAP_X)))
  const rounds = [...new Set(nodes.map((n) => n.round))].sort((a, b) => a - b)
  // An asset that passes the failure on has to stay on the diagram even if
  // its own impact is smaller: without this the water tower was cut from a
  // full round, and the round below it showed seven assets losing water with
  // nothing on screen explaining where the water went.
  const carriers = new Set(edges.map((e) => e.source))
  const position = {}
  const bands = []
  let y = 0

  for (const round of rounds) {
    const all = nodes.filter((n) => n.round === round)
      .sort((a, b) => (carriers.has(b.id) - carriers.has(a.id)) || (b.impact - a.impact))
    const shown = all.slice(0, SHOWN_PER_ROUND)
    bands.push({ round, shown, hidden: all.length - shown.length, y })
    y += HEADER_H
    shown.forEach((node, i) => {
      const col = i % perRow
      const row = Math.floor(i / perRow)
      const cols = Math.min(perRow, shown.length - row * perRow)
      const rowWidth = cols * NODE_W + (cols - 1) * GAP_X
      position[node.id] = {
        x: Math.max(0, (width - rowWidth) / 2) + col * (NODE_W + GAP_X),
        y: y + row * (NODE_H + GAP_Y),
      }
    })
    y += Math.ceil(shown.length / perRow) * (NODE_H + GAP_Y) + ROUND_GAP
  }
  return { position, bands, height: Math.max(y - ROUND_GAP + 6, NODE_H) }
}

function Chain({ path, onFocus, onPick }) {
  if (path.length < 2) return null

  return (
    <div className="chain">
      {path.map((n, i) => (
        <div key={n.id}>
          {i > 0 && (
            <div
              className="chain-hop"
              style={{ color: NETWORK_COLOR[n.via_network] ?? 'var(--muted)' }}
            >
              <i />
              <span>{n.via_network_label}</span>
              <em>{n.confidence} confidence</em>
            </div>
          )}
          <button
            className="chain-node"
            data-level={n.level}
            onClick={() => { onPick(n.id); onFocus?.([n.lat, n.lon]) }}
          >
            <span className="chain-glyph" style={{ color: LEVEL_COLOR[n.level] }}>
              <Glyph category={n.category} size={15} />
            </span>
            <span>
              <span className="chain-name">{n.name}</span>
              <br />
              <span className="chain-role">
                {n.round === 0 ? 'Incident site'
                  : `Round ${n.round} · ${n.category_label}`}
              </span>
            </span>
          </button>
        </div>
      ))}
    </div>
  )
}

export default function CascadeDiagram({ cascade, result, onFocus }) {
  const [picked, setPicked] = useState(null)

  if (!cascade.nodes.length) {
    return (
      <>
        <div className="head"><span className="cap">Dependency cascade</span></div>
        <p className="explain">{cascade.summary}</p>
        <Gaps gaps={cascade.gaps} coverage={cascade.coverage} />
      </>
    )
  }

  const width = 296
  const { position, bands, height } = layout(cascade.nodes, cascade.edges, width)
  const node = cascade.nodes.find((n) => n.id === picked)
  const drawn = cascade.edges.filter((e) => position[e.source] && position[e.target])

  const far = result ? primaryConsequence(result) : null
  const path = far?.kind === 'cascade' ? chainTo(cascade, far.node.id) : []

  return (
    <>
      {path.length >= 2 && (
        <>
          <div className="head">
            <span className="cap">The path it took</span>
            <b>{cascade.rounds} round{cascade.rounds === 1 ? '' : 's'}</b>
          </div>
          <Chain path={path} onFocus={onFocus} onPick={setPicked} />
        </>
      )}

      <div className="block">
        <div className="head">
          <span className="cap">Everything downstream</span>
          <b>{cascade.indirect_total} assets</b>
        </div>
        <p className="note">{cascade.summary}</p>
      </div>

      <div className="cascade-scroll">
        <svg width={width} height={height} role="img"
             aria-label={`Dependency cascade: ${cascade.summary}`}>
          {bands.map((band) => (
            <text key={`b${band.round}`} x="0" y={band.y + 10} className="cascade-band">
              {band.round === 0 ? 'Directly affected'
                : `Round ${band.round}${band.hidden ? ` · showing ${band.shown.length} of ${band.shown.length + band.hidden}` : ''}`}
            </text>
          ))}

          {drawn.map((e, i) => {
            const a = position[e.source]
            const b = position[e.target]
            const x1 = a.x + NODE_W / 2
            const y1 = a.y + NODE_H
            const x2 = b.x + NODE_W / 2
            const y2 = b.y
            const mid = (y1 + y2) / 2
            return (
              <path
                key={i}
                d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`}
                fill="none"
                stroke={NETWORK_COLOR[e.network] ?? LEVEL_COLOR[e.level]}
                strokeWidth="1.2"
                strokeDasharray={e.confidence === 'low' ? '4 3' : undefined}
                opacity="0.5"
              />
            )
          })}

          {bands.flatMap((band) => band.shown.map((n) => {
            const p = position[n.id]
            const on = picked === n.id
            return (
              <g
                key={n.id}
                transform={`translate(${p.x},${p.y})`}
                className="cascade-node"
                onClick={() => { setPicked(on ? null : n.id); onFocus?.([n.lat, n.lon]) }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setPicked(on ? null : n.id)}
              >
                <title>{n.name}</title>
                <rect
                  width={NODE_W} height={NODE_H} rx="2"
                  fill={on ? '#141b2b' : '#0d121c'}
                  stroke={LEVEL_COLOR[n.level]}
                  strokeWidth={on ? 1.6 : 1}
                  opacity={on ? 1 : 0.9}
                />
                <text x="9" y="16" className="cascade-node-name">
                  {n.name.length > 21 ? `${n.name.slice(0, 20)}…` : n.name}
                </text>
                <text x="9" y="29" className="cascade-node-meta">
                  {n.round === 0 ? 'incident site'
                    : `${n.via_network_label?.toLowerCase() ?? ''} · ${n.confidence}`}
                </text>
              </g>
            )
          }))}
        </svg>
      </div>

      {node ? (
        <div className="cascade-detail">
          <h3>{node.name}</h3>
          <dl className="pairs">
            <div><dt>Asset</dt><dd>{node.category_label}</dd></div>
            <div><dt>Impact</dt><dd className={`lv-${node.level}`}>{node.level}</dd></div>
            <div><dt>Propagation round</dt><dd className="fig">{node.round}</dd></div>
            {node.via_source_name && (
              <div><dt>Caused by</dt><dd>{node.via_source_name} ({node.via_network_label})</dd></div>
            )}
            <div><dt>Confidence</dt><dd className={node.confidence === 'low' ? 'lv-high' : ''}>
              {node.confidence}</dd></div>
          </dl>
          <p className="why">{node.reason}</p>
        </div>
      ) : (
        <p className="note">
          Click a box for the reason, the asset it depends on, and how much to trust the
          link. A dashed line is a low-confidence dependency.
        </p>
      )}

      <details className="technical">
        <summary>Every affected asset ({cascade.nodes.length})</summary>
        <ul className="gap-list">
          {cascade.nodes.map((n) => (
            <li key={n.id} data-level={n.level}>
              <strong>{n.name}</strong> — {n.round === 0 ? 'incident site' : n.reason}
            </li>
          ))}
        </ul>
      </details>

      <Gaps gaps={cascade.gaps} coverage={cascade.coverage} />
    </>
  )
}

function Gaps({ gaps, coverage }) {
  if (!gaps?.length) return null
  return (
    <details className="technical">
      <summary>What could not be modelled ({gaps.length})</summary>
      <ul className="gap-list">
        {gaps.map((g, i) => <li key={i} data-level="moderate"><strong>{g.label}:</strong> {g.message}</li>)}
      </ul>
      {coverage && (
        <dl className="coverage">
          {Object.entries(coverage).map(([key, c]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{c.suppliers} supplier(s), {c.consumers_linked} consumer(s) linked</dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  )
}
