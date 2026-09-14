import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import ActionList from './components/ActionList'
import AffectedList from './components/AffectedList'
import CascadeDiagram from './components/CascadeDiagram'
import CommandBar from './components/CommandBar'
import ComparisonTable from './components/ComparisonTable'
import EventStrip from './components/EventStrip'
import HistoryPanel from './components/HistoryPanel'
import Verdict, { ImpactDetail } from './components/ImpactSummary'
import IncidentBuilder from './components/IncidentBuilder'
import LayerFilters from './components/LayerFilters'
import MapView from './components/MapView'
import Timeline from './components/Timeline'

// Mirrors ROAD_CLASS_LABEL in src/analysis/impact.py — an operator reads
// "Residential street", not the OSM highway tag.
const ROAD_CLASS_LABEL = {
  motorway: 'Highway', trunk: 'Major road', primary: 'Main road',
  secondary: 'Secondary road', tertiary: 'Local road',
  residential: 'Residential street', unclassified: 'Minor road',
  service: 'Service road', living_street: 'Residential street',
}

const HISTORY_KEY = 'incident-history-v1'
const TABS = [
  ['impact', 'Impact'],
  ['cascade', 'Cascade'],
  ['compare', 'Baseline'],
  ['actions', 'Actions'],
  ['timeline', 'Timeline'],
]

// What the engine is actually doing, in the order it does it. Shown while the
// request is in flight and dropped the moment it answers — on this dataset it
// usually answers before the second line, which is the honest impression to
// give of how fast it is.
const STAGES = [
  'Applying disruption to the network',
  'Routing emergency access',
  'Propagating dependencies',
  'Scoring impact',
]

const mercY = (lat) => {
  const r = (lat * Math.PI) / 180
  return (Math.log(Math.tan(Math.PI / 4 + r / 2)) * 180) / Math.PI
}

/** Opening view covering the whole extract. Computed rather than handed to
 *  Leaflet's fitBounds, which needs a measured container and collapses to max
 *  zoom without one. */
function frameAll(geos) {
  const lats = geos.map((g) => g[0])
  const lons = geos.map((g) => g[1])
  const [s, n] = [Math.min(...lats), Math.max(...lats)]
  const [w, e] = [Math.min(...lons), Math.max(...lons)]
  const zLon = Math.log2((360 * (window.innerWidth || 1280)) / (256 * Math.max(e - w, 1e-6)))
  const zLat = Math.log2((360 * (window.innerHeight || 720)) / (256 * Math.max(Math.abs(mercY(n) - mercY(s)), 1e-6)))
  return {
    center: [(s + n) / 2, (w + e) / 2],
    zoom: Math.max(10, Math.min(16, Math.floor(Math.min(zLon, zLat)) - 1)),
  }
}

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY)) ?? []
  } catch {
    return []
  }
}

export default function App() {
  const [city, setCity] = useState(null)
  const [facilities, setFacilities] = useState(null)
  const [options, setOptions] = useState(null)
  const [view, setView] = useState(null)
  const [loadError, setLoadError] = useState(null)

  // The scenario being built: a list, because a flood and a power cut on the
  // same evening are one situation, not two.
  const [incidents, setIncidents] = useState([])
  const [draft, setDraft] = useState({
    type: 'accident', severity: 'high', duration_hours: 2, radius_m: null,
  })
  const [target, setTarget] = useState(null)   // {kind:'road'|'asset', ...}
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [stage, setStage] = useState(0)
  const [runError, setRunError] = useState(null)
  const [tab, setTab] = useState('impact')
  const [focus, setFocus] = useState(null)     // map focus: [lat, lon]
  const [deckOpen, setDeckOpen] = useState(() => window.innerWidth > 880)
  const [history, setHistory] = useState(loadHistory)
  const stageTimer = useRef(null)

  const [layers, setLayers] = useState({
    roads: true, facilities: true, zone: true, cascade: true,
    evacuation: true, graticule: true, affectedOnly: false,
  })

  useEffect(() => {
    Promise.all([api.getCity(), api.getFacilities(), api.getDisruptionTypes()])
      .then(([c, f, o]) => {
        setCity(c)
        setFacilities(f.facilities)
        setOptions(o)
        setView(frameAll(c.road.nodes.map((n) => n.geo)))
      })
      .catch((e) => setLoadError(e.message))
  }, [])

  useEffect(() => () => clearInterval(stageTimer.current), [])

  // Road names come from the network, so selection can show "Malpe - Manipal
  // Road" rather than the pair of node ids that identify the segment.
  const roadIndex = useMemo(() => {
    const map = {}
    if (!city) return map
    for (const e of city.road.edges) {
      map[`${e.source}|${e.target}`] = e
      map[`${e.target}|${e.source}`] = e
    }
    return map
  }, [city])

  const facilityIndex = useMemo(() => {
    const map = {}
    for (const f of facilities ?? []) map[f.id] = f
    return map
  }, [facilities])

  const selectRoad = useCallback((u, v) => {
    const edge = roadIndex[`${u}|${v}`]
    const klass = ROAD_CLASS_LABEL[edge?.road_class] ?? 'Road'
    setTarget({
      kind: 'road',
      nodes: [u, v],
      // Preview only — once analysed, the server supplies the full label
      // including the nearest landmark for unnamed roads.
      name: (edge?.name || '').split(';')[0].trim() || `Unnamed ${klass.toLowerCase()}`,
      detail: klass,
      named_in_osm: Boolean(edge?.name),
    })
    setRunError(null)
  }, [roadIndex])

  const selectAsset = useCallback((facility) => {
    if (!facility) return
    setTarget({
      kind: 'asset',
      id: facility.id,
      name: facility.name,
      detail: [facility.category_label, facility.area].filter(Boolean).join(', '),
      category: facility.category,
      lat: facility.lat,
      lon: facility.lon,
    })
    setRunError(null)
  }, [])

  const handleSearchPick = (entry) => {
    if (entry.kind === 'road' && entry.edge) selectRoad(entry.edge[0], entry.edge[1])
    else if (entry.kind === 'asset') selectAsset(facilityIndex[entry.id])
    if (entry.lat != null) setFocus([entry.lat, entry.lon])
  }

  const queued = useMemo(() => {
    const list = [...incidents]
    if (target) {
      list.push({
        ...draft,
        edge: target.kind === 'road' ? target.nodes : null,
        facility_id: target.kind === 'asset' ? target.id : null,
        _label: target.name,
      })
    }
    return list
  }, [incidents, draft, target])

  const run = async (specs = queued) => {
    if (!specs.length) return
    setRunning(true)
    setStage(0)
    setRunError(null)
    // The stages advance while the request is genuinely outstanding. Nothing
    // is padded: if the engine answers in 120 ms the first line is all anyone
    // ever sees.
    stageTimer.current = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 260)
    try {
      const payload = specs.map(({ _label, ...rest }) => rest)
      const r = await api.simulate(payload)
      setResult(r)
      setTab('impact')
      setDeckOpen(true)
      const entry = {
        id: r.scenario_id,
        at: r.generated_at,
        title: r.incidents.map((i) => `${i.label} — ${i.target.name}`).join(' + '),
        level: r.score.level,
        score: r.score.value,
        specs: payload,
      }
      const next = [entry, ...history.filter((h) => h.id !== entry.id)].slice(0, 12)
      setHistory(next)
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
      } catch { /* private mode: history is a convenience, not state */ }
    } catch (e) {
      setRunError(e.message)
    } finally {
      clearInterval(stageTimer.current)
      setRunning(false)
    }
  }

  const addToScenario = () => {
    if (!target) return
    setIncidents([...incidents, {
      ...draft,
      edge: target.kind === 'road' ? target.nodes : null,
      facility_id: target.kind === 'asset' ? target.id : null,
      _label: target.name,
    }])
    setTarget(null)
  }

  const counts = city && facilities && {
    roads: city.road.edges.length,
    facilities: facilities.length,
  }

  const band = result && (result.score.level === 'none' ? 'clear' : result.score.level)

  return (
    <div className="layout" data-deck={deckOpen ? 'open' : 'closed'}>
      <CommandBar counts={counts} online={Boolean(city)} onPick={handleSearchPick} />

      <MapView
        city={city}
        facilities={facilities}
        center={view?.center ?? [13.35, 74.79]}
        zoom={view?.zoom ?? 13}
        focus={focus}
        result={result}
        target={target}
        onSelectRoad={selectRoad}
        onSelectAsset={selectAsset}
        layers={layers}
      />

      {/* The frame the city is seen through: a hairline, a vignette that seats
          it below the chassis, and four registration marks. */}
      <div className="aperture" aria-hidden="true"><i /><i /><i /><i /></div>

      {running && (
        <div className="run-stages" role="status">
          <i />
          {STAGES[stage]}
        </div>
      )}

      <div className="rail rail-left">
        {loadError && <div className="banner">Could not load city data: {loadError}</div>}

        <IncidentBuilder
          options={options}
          draft={draft}
          setDraft={setDraft}
          target={target}
          clearTarget={() => setTarget(null)}
          queued={incidents}
          removeQueued={(i) => setIncidents(incidents.filter((_, n) => n !== i))}
          onAdd={addToScenario}
          onRun={() => run()}
          running={running}
          error={runError}
        />

        <HistoryPanel
          history={history}
          onReplay={(entry) => { setIncidents([]); setTarget(null); run(entry.specs) }}
        />
        <LayerFilters layers={layers} setLayers={setLayers} />
      </div>

      {/* The answer can be put away so the map takes the whole screen — an
          incident commander switches between reading the assessment and
          looking at the ground. */}
      {result && !deckOpen && (
        <button className="reopen" style={{ '--band': `var(--${band})` }}
                onClick={() => setDeckOpen(true)}>
          <b className="display">{result.score.value}</b>
          <span>{result.incidents[0].label} — {result.incidents[0].target.name}</span>
        </button>
      )}

      {deckOpen && (
        <div className="rail rail-right">
          {result ? (
            <section className="deck" key={result.scenario_id}>
              <Verdict result={result} onCollapse={() => setDeckOpen(false)} onFocus={setFocus} />
              <nav className="tabs" role="tablist">
                {TABS.map(([key, label]) => (
                  <button
                    key={key}
                    role="tab"
                    aria-selected={tab === key}
                    className={`tab${tab === key ? ' tab-on' : ''}`}
                    onClick={() => setTab(key)}
                  >
                    {label}
                    {key === 'actions' && result.actions.length > 0 && (
                      <span className="tab-count">{result.actions.length}</span>
                    )}
                    {key === 'cascade' && result.cascade.indirect_total > 0 && (
                      <span className="tab-count">{result.cascade.indirect_total}</span>
                    )}
                  </button>
                ))}
              </nav>
              <div className="deck-body settle">
                {tab === 'impact' && (
                  <>
                    <ImpactDetail result={result} />
                    <div className="block">
                      <AffectedList result={result} onFocus={setFocus} />
                    </div>
                  </>
                )}
                {tab === 'cascade' && (
                  <CascadeDiagram cascade={result.cascade} result={result} onFocus={setFocus} />
                )}
                {tab === 'compare' && (
                  <ComparisonTable comparison={result.comparison} confidence={result.confidence} />
                )}
                {tab === 'actions' && <ActionList actions={result.actions} />}
                {tab === 'timeline' && <Timeline timeline={result.timeline} />}
              </div>
            </section>
          ) : (
            <section className="deck start">
              <h2>Nothing simulated yet</h2>
              <p>
                What is affected, why, what follows downstream, and what to do first — all of it
                appears here, traced to the data it came from.
              </p>
              <ol>
                <li><b>Find a place.</b> Search above, or click a road or asset on the map.</li>
                <li><b>Say what happened.</b> Only incidents that can happen to that thing are offered.</li>
                <li><b>Run it.</b> Add a second incident first to see their combined effect.</li>
              </ol>
            </section>
          )}
        </div>
      )}

      <EventStrip result={result} onPick={() => { setTab('timeline'); setDeckOpen(true) }} />
    </div>
  )
}
