import L from 'leaflet'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Circle, CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, ZoomControl,
} from 'react-leaflet'
import { COLORS, NETWORK_COLOR, roadStyle } from '../theme'
import { pathFor } from '../glyphs'

/** Leaflet measures its container when the map is created; a map created into
 *  a box the browser has not laid out yet lays its tile grid for the wrong
 *  size. Re-measure on any box change. */
function KeepSized() {
  const map = useMap()
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map])
  return null
}

/** Centre on whatever the operator just picked in the search or a panel. */
function FlyTo({ focus }) {
  const map = useMap()
  useEffect(() => {
    if (focus) map.setView(focus, Math.max(map.getZoom(), 15), { animate: true })
  }, [focus, map])
  return null
}

// An unaffected asset is context, not a finding: it takes the same grey the
// road network sits in rather than a colour of its own.
const ASSET_QUIET = '#55709a'

const LEVEL_COLOR = {
  critical: COLORS.critical,
  high: COLORS.high,
  moderate: COLORS.moderate,
  low: COLORS.low,
  none: COLORS.clear,
}

/** Assets carry one weight when they are part of the finding and another when
 *  they are the city around it. Before a run everything is quiet and equal;
 *  once there is a result, anything untouched steps back to a mark you can see
 *  but do not read. */
function facilityIcon(category, color, emphasis, recede) {
  const size = emphasis ? 24 : 16
  const glyph = Math.round(size * 0.58)   // the mark needs air, or a filled
                                          // symbol reads as a solid block
  const opacity = recede ? 0.22 : emphasis ? 1 : 0.5
  const border = emphasis ? 1.5 : 1
  const lift = emphasis ? 'box-shadow:0 0 0 3px rgba(0,0,0,.5);' : ''
  return L.divIcon({
    className: '',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<div style="width:${size}px;height:${size}px;border-radius:2px;opacity:${opacity};`
      + `display:grid;place-items:center;background:#0a0e16;`
      + `border:${border}px solid ${color};${lift}">`
      + `<svg viewBox="0 0 24 24" width="${glyph}" height="${glyph}" fill="${color}">`
      + `<path d="${pathFor(category)}"/></svg></div>`,
  })
}

function incidentIcon() {
  return L.divIcon({
    className: '',
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    html: '<div class="incident-pin"><span></span></div>',
  })
}

/** A coordinate graticule at a real interval, labelled with real degrees.
 *  It reads as a digital-twin cue because it is one: the lines are where the
 *  parallels and meridians actually fall. */
function graticule(nodes) {
  if (!nodes?.length) return []
  const lats = nodes.map((n) => n.geo[0])
  const lons = nodes.map((n) => n.geo[1])
  const south = Math.min(...lats)
  const north = Math.max(...lats)
  const west = Math.min(...lons)
  const east = Math.max(...lons)
  const step = 0.05
  const pad = 0.04
  const lines = []
  for (let lat = Math.ceil(south / step) * step; lat <= north; lat += step) {
    lines.push({ key: `lat${lat.toFixed(2)}`, positions: [[lat, west - pad], [lat, east + pad]] })
  }
  for (let lon = Math.ceil(west / step) * step; lon <= east; lon += step) {
    lines.push({ key: `lon${lon.toFixed(2)}`, positions: [[south - pad, lon], [north + pad, lon]] })
  }
  return lines
}

const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

export default function MapView({
  city, facilities, center, zoom, focus, result, target, onSelectRoad, onSelectAsset, layers,
}) {
  const host = useRef(null)
  const [boxReady, setBoxReady] = useState(false)
  useEffect(() => {
    const el = host.current
    if (!el) return
    if (el.clientWidth > 0) { setBoxReady(true); return }
    const observer = new ResizeObserver(() => {
      if (el.clientWidth > 0) { setBoxReady(true); observer.disconnect() }
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const grid = useMemo(() => graticule(city?.road?.nodes), [city])

  const pos = {}
  if (city) for (const n of city.road.nodes) pos[n.id] = n.geo

  // Impact level per facility, so the map and the list agree.
  const levelOf = {}
  if (result) for (const f of result.facilities) levelOf[f.id] = f.level
  const cascadeLevel = {}
  if (result) for (const n of result.cascade.nodes) cascadeLevel[n.id] = n.level

  const incidentEdges = new Set()
  for (const i of result?.incidents ?? []) {
    if (i.target.kind === 'road') incidentEdges.add(i.target.id)
  }
  if (target?.kind === 'road') incidentEdges.add(`${target.nodes[0]}-${target.nodes[1]}`)

  const hitSegments = new Set(
    (result?.affected_segments ?? []).map((s) => `${s.source}|${s.target}`)
  )

  return (
    <div className="map" ref={host}>
      {!city || !boxReady ? (
        <div className="loading"><div className="pulse" /><p>Loading city network</p></div>
      ) : (
        <MapContainer center={center} zoom={zoom} style={{ height: '100%', width: '100%' }}
                      preferCanvas zoomControl={false}>
          <KeepSized />
          <FlyTo focus={focus} />
          <ZoomControl position="bottomleft" />

          {/* Standard OpenStreetMap tiles, inverted to a dark ground in CSS
              (see .leaflet-tile-pane). The hosted dark styles all want an API
              key, and a basemap that can stop rendering is not one to build an
              operations view on. */}
          <TileLayer attribution={TILE_ATTRIBUTION}
                     url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

          {layers.graticule && grid.map((g) => (
            <Polyline
              key={g.key}
              positions={g.positions}
              interactive={false}
              pathOptions={{ color: COLORS.graticule, weight: 1, opacity: 0.9 }}
            />
          ))}

          {/* Impact zones first, so they sit under everything they contain.
              An outline and the faintest wash: a measured extent, not a stain. */}
          {layers.zone && result?.incidents.map((i) => (
            i.target.lat != null && (
              <Circle
                key={`zone${i.index}`}
                center={[i.target.lat, i.target.lon]}
                radius={i.radius_m}
                interactive={false}
                pathOptions={{
                  color: COLORS.high, weight: 1, dashArray: '2 5', opacity: 0.55,
                  fillColor: COLORS.high, fillOpacity: 0.035,
                }}
              />
            )
          ))}

          {layers.roads && city.road.edges.map((e, i) => {
            const hit = hitSegments.has(`${e.source}|${e.target}`)
                     || hitSegments.has(`${e.target}|${e.source}`)
            const isIncident = incidentEdges.has(`${e.source}-${e.target}`)
                            || incidentEdges.has(`${e.target}-${e.source}`)
            // A trunk road and a service lane are not the same object. Weight
            // and value follow the class, so the arterial structure of the
            // town is what you see first.
            const road = roadStyle(e.road_class)
            return (
              <Polyline
                key={`r${i}`}
                positions={[pos[e.source], pos[e.target]]}
                pathOptions={{
                  color: isIncident ? COLORS.critical : hit ? COLORS.high : road.c,
                  weight: isIncident ? Math.max(5, road.w * 2.6)
                    : hit ? Math.max(2.4, road.w * 1.8) : road.w,
                  opacity: isIncident ? 1 : hit ? 0.92 : road.o * (result ? 0.45 : 1),
                }}
                eventHandlers={{ click: () => onSelectRoad(e.source, e.target) }}
              >
                <Tooltip sticky>
                  {e.name || 'Unnamed road'}
                  <br />
                  <em>{isIncident ? 'Incident location' : 'Click to place an incident here'}</em>
                </Tooltip>
              </Polyline>
            )
          })}

          {/* Dependency links that actually carried an effect, drawn where they
              run: the cascade is a geography, not only a diagram. Each line
              takes the colour of the network that carried the failure, and a
              low-confidence link is drawn fainter. */}
          {layers.cascade && result?.cascade.edges.map((e, i) => {
            const a = result.cascade.nodes.find((n) => n.id === e.source)
            const b = result.cascade.nodes.find((n) => n.id === e.target)
            if (!a || !b) return null
            return (
              <Polyline
                key={`c${i}`}
                positions={[[a.lat, a.lon], [b.lat, b.lon]]}
                className="cascade-trace"
                pathOptions={{
                  color: NETWORK_COLOR[e.network] ?? LEVEL_COLOR[e.level],
                  weight: 1.4,
                  opacity: e.confidence === 'low' ? 0.45 : 0.8,
                }}
              >
                <Tooltip sticky>
                  <strong>{e.network_label} dependency</strong>
                  <br />{a.name} → {b.name}
                  <br /><em>{e.confidence} confidence</em>
                </Tooltip>
              </Polyline>
            )
          })}

          {/* Evacuation routes, from the same routing the panel quotes. */}
          {layers.evacuation && result?.evacuation?.sites?.flatMap((site) =>
            site.destinations.filter((d) => d.reachable && d.route.length).map((d) => (
              <Polyline
                key={`e${site.id}-${d.id}`}
                positions={d.route}
                pathOptions={{ color: COLORS.clear, weight: 3, opacity: 0.8, dashArray: '8 6' }}
              >
                <Tooltip sticky>
                  Evacuation route to <strong>{d.name}</strong>
                  <br />{d.travel_min} min
                </Tooltip>
              </Polyline>
            ))
          )}

          {layers.facilities && facilities?.map((f) => {
            const level = levelOf[f.id] ?? cascadeLevel[f.id]
            const affected = level && level !== 'none'
            if (layers.affectedOnly && !affected) return null
            return (
              <Marker
                key={f.id}
                position={[f.lat, f.lon]}
                icon={facilityIcon(f.category, LEVEL_COLOR[level] ?? ASSET_QUIET, affected,
                                   Boolean(result) && !affected)}
                zIndexOffset={affected ? 500 : 0}
                eventHandlers={{ click: () => onSelectAsset(f) }}
              >
                <Tooltip direction="top" offset={[0, -12]}>
                  <strong>{f.name}</strong>
                  <br />
                  {f.category_label}{f.area ? `, ${f.area}` : ''}
                  {affected && <><br /><em>Impact: {level}</em></>}
                  <br /><em>Click to place an incident here</em>
                </Tooltip>
              </Marker>
            )
          })}

          {/* Where the incidents are. The one glow in the interface. */}
          {result?.incidents.map((i) => (
            i.target.lat != null && (
              <Marker key={`i${i.index}`} position={[i.target.lat, i.target.lon]}
                      icon={incidentIcon()} zIndexOffset={900}>
                <Tooltip direction="top" offset={[0, -16]}>
                  <strong>{i.label}</strong>
                  <br />{i.target.name}
                  <br /><em>{i.severity}, {i.duration_hours} h</em>
                </Tooltip>
              </Marker>
            )
          ))}

          {target?.kind === 'asset' && !result && (
            <CircleMarker
              center={[target.lat, target.lon]}
              radius={11}
              pathOptions={{
                color: COLORS.accent, weight: 1.5,
                fillColor: COLORS.accent, fillOpacity: 0.2,
              }}
            />
          )}
          {target?.kind === 'road' && !result && pos[target.nodes[0]] && (
            <CircleMarker
              center={pos[target.nodes[0]]}
              radius={7}
              pathOptions={{
                color: COLORS.accent, weight: 1.5,
                fillColor: COLORS.accent, fillOpacity: 0.6,
              }}
            />
          )}
        </MapContainer>
      )}
    </div>
  )
}
