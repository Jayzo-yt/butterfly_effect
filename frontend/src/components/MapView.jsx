import L from 'leaflet'
import { useEffect, useRef, useState } from 'react'
import {
  Circle, CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, ZoomControl,
} from 'react-leaflet'
import { COLORS } from '../theme'

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

const LEVEL_COLOR = {
  critical: COLORS.critical,
  high: COLORS.high,
  moderate: COLORS.moderate,
  low: COLORS.low,
  none: COLORS.clear,
}

const GLYPH = {
  hospital: '<path d="M9 5h6v4h4v6h-4v4H9v-4H5V9h4z"/>',
  clinic: '<path d="M9 5h6v4h4v6h-4v4H9v-4H5V9h4z"/>',
  health_centre: '<path d="M9 5h6v4h4v6h-4v4H9v-4H5V9h4z"/>',
  fire_station: '<path d="M12 2c1 4 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3 1-6 1-8z"/>',
  police: '<path d="M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z"/>',
  ambulance_station: '<path d="M3 7h11v5h4l3 3v3h-3a2 2 0 0 1-4 0H8a2 2 0 0 1-4 0H3z"/>',
  substation: '<path d="M13 2 4 14h6l-1 8 9-12h-6z"/>',
  power_plant: '<path d="M13 2 4 14h6l-1 8 9-12h-6z"/>',
  water_tower: '<path d="M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z"/>',
  water_works: '<path d="M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z"/>',
  pumping_station: '<path d="M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z"/>',
  school: '<path d="M12 3 2 8l10 5 10-5zM6 12v5c0 1.5 3 3 6 3s6-1.5 6-3v-5l-6 3z"/>',
  college: '<path d="M12 3 2 8l10 5 10-5zM6 12v5c0 1.5 3 3 6 3s6-1.5 6-3v-5l-6 3z"/>',
  railway_station: '<path d="M6 3h12v11a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3zM7 19l-2 3h14l-2-3z"/>',
  bus_station: '<path d="M4 4h16v10H4zM5 15h3v3H5zm11 0h3v3h-3z"/>',
  open_space: '<path d="M4 18h16v3H4zM12 3l7 12H5z"/>',
  shelter: '<path d="M12 3 3 10h3v9h12v-9h3z"/>',
  telecom: '<path d="M11 8h2v13h-2zM7 3a7 7 0 0 0 0 10M17 3a7 7 0 0 1 0 10"/>',
}

function facilityIcon(category, color, emphasis, recede) {
  const glyph = GLYPH[category] || '<circle cx="12" cy="12" r="7"/>'
  const size = emphasis ? 28 : 20
  // With a finding on screen, assets that take no part in it step back rather
  // than competing with the ones that do.
  const opacity = recede ? 0.38 : 1
  return L.divIcon({
    className: '',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<div style="
      width:${size}px;height:${size}px;border-radius:6px;opacity:${opacity};
      display:grid;place-items:center;background:#101a23;
      border:${emphasis ? 2 : 1}px solid ${color};
      box-shadow:0 2px 8px rgba(2,7,12,.6);">
      <svg viewBox="0 0 24 24" width="${size - 9}" height="${size - 9}" fill="${color}">${glyph}</svg>
    </div>`,
  })
}

function incidentIcon() {
  return L.divIcon({
    className: '',
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    html: `<div class="incident-pin"><span></span></div>`,
  })
}

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

  const pos = {}
  if (city) for (const n of city.road.nodes) pos[n.id] = n.geo

  // Impact level per facility, so the map and the list agree.
  const levelOf = {}
  if (result) for (const f of result.facilities) levelOf[f.id] = f.level
  const cascadeLevel = {}
  if (result) for (const n of result.cascade.nodes) cascadeLevel[n.id] = n.level

  const facilityAt = {}
  for (const f of facilities ?? []) facilityAt[f.id] = f

  const incidentEdges = new Set()
  for (const i of result?.incidents ?? []) {
    if (i.target.kind === 'road') incidentEdges.add(i.target.id)
  }
  if (target?.kind === 'road') incidentEdges.add(`${target.nodes[0]}-${target.nodes[1]}`)

  const hitSegments = new Set(
    (result?.affected_segments ?? []).map((s) => `${s.source}|${s.target}`)
  )

  return (
    <div className={`map${result ? ' map-focused' : ''}`} ref={host}>
      {!city || !boxReady ? (
        <div className="loading"><div className="pulse" /><p>Loading city network</p></div>
      ) : (
        <MapContainer center={center} zoom={zoom} style={{ height: '100%', width: '100%' }}
                      preferCanvas zoomControl={false}>
          <KeepSized />
          <FlyTo focus={focus} />
          <ZoomControl position="bottomleft" />
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          {/* Impact zones first, so they sit under everything they contain. */}
          {layers.zone && result?.incidents.map((i) => (
            i.target.lat != null && (
              <Circle
                key={`zone${i.index}`}
                center={[i.target.lat, i.target.lon]}
                radius={i.radius_m}
                pathOptions={{ color: COLORS.moderate, weight: 1, fillColor: COLORS.moderate, fillOpacity: 0.06 }}
              />
            )
          ))}

          {layers.roads && city.road.edges.map((e, i) => {
            const hit = hitSegments.has(`${e.source}|${e.target}`)
                     || hitSegments.has(`${e.target}|${e.source}`)
            const isIncident = incidentEdges.has(`${e.source}-${e.target}`)
                            || incidentEdges.has(`${e.target}-${e.source}`)
            return (
              <Polyline
                key={`r${i}`}
                positions={[pos[e.source], pos[e.target]]}
                pathOptions={{
                  color: isIncident ? COLORS.critical : hit ? COLORS.moderate : COLORS.edge,
                  weight: isIncident ? 7 : hit ? 3 : 1.6,
                  opacity: isIncident ? 1 : hit ? 0.9 : result ? 0.28 : 0.5,
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

          {/* Dependency links that actually carried an effect, drawn where
              they run: the cascade is a geography, not only a diagram. */}
          {layers.cascade && result?.cascade.edges.map((e, i) => {
            const a = result.cascade.nodes.find((n) => n.id === e.source)
            const b = result.cascade.nodes.find((n) => n.id === e.target)
            if (!a || !b) return null
            return (
              <Polyline
                key={`c${i}`}
                positions={[[a.lat, a.lon], [b.lat, b.lon]]}
                pathOptions={{
                  color: LEVEL_COLOR[e.level], weight: 2, opacity: 0.85,
                  dashArray: e.confidence === 'low' ? '3 6' : '7 5',
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
                pathOptions={{ color: COLORS.clear, weight: 4, opacity: 0.85, dashArray: '10 6' }}
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
                icon={facilityIcon(f.category, LEVEL_COLOR[level] ?? COLORS.edge, affected,
                                   Boolean(result) && !affected)}
                zIndexOffset={affected ? 500 : 0}
                eventHandlers={{ click: () => onSelectAsset(f) }}
              >
                <Tooltip direction="top" offset={[0, -12]}>
                  <strong>{f.name}</strong>
                  <br />
                  {f.category_label}{f.area ? ` · ${f.area}` : ''}
                  {affected && <><br /><em>Impact: {level}</em></>}
                  <br /><em>Click to place an incident here</em>
                </Tooltip>
              </Marker>
            )
          })}

          {/* Where the incidents are. */}
          {result?.incidents.map((i) => (
            i.target.lat != null && (
              <Marker key={`i${i.index}`} position={[i.target.lat, i.target.lon]}
                      icon={incidentIcon()} zIndexOffset={900}>
                <Tooltip direction="top" offset={[0, -16]}>
                  <strong>{i.label}</strong>
                  <br />{i.target.name}
                  <br /><em>{i.severity} · {i.duration_hours} h</em>
                </Tooltip>
              </Marker>
            )
          ))}

          {target?.kind === 'asset' && !result && (
            <CircleMarker
              center={[target.lat, target.lon]}
              radius={11}
              pathOptions={{ color: COLORS.accent, fillColor: COLORS.accent, fillOpacity: 0.35 }}
            />
          )}
          {target?.kind === 'road' && !result && pos[target.nodes[0]] && (
            <CircleMarker
              center={pos[target.nodes[0]]}
              radius={7}
              pathOptions={{ color: COLORS.accent, fillColor: COLORS.accent, fillOpacity: 0.8 }}
            />
          )}
        </MapContainer>
      )}
    </div>
  )
}
