/** One icon system, drawn once.
 *
 *  Leaflet markers need the path as an HTML string (a divIcon is built from
 *  markup, not React), and the panels need the same shape as an element. Both
 *  read from here so an asset is never one symbol on the map and another in
 *  the list beside it.
 *
 *  Everything is a 24×24 filled path, one weight, no strokes — so a hospital
 *  at 14px and a hospital at 30px are recognisably the same mark.
 */
export const GLYPH = {
  hospital: 'M9 4h6v5h5v6h-5v5H9v-5H4V9h5z',
  clinic: 'M9 4h6v5h5v6h-5v5H9v-5H4V9h5z',
  health_centre: 'M9 4h6v5h5v6h-5v5H9v-5H4V9h5z',
  pharmacy: 'M9 4h6v5h5v6h-5v5H9v-5H4V9h5z',
  fire_station: 'M12 2c1 4 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3 1-6 1-8z',
  police: 'M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z',
  ambulance_station: 'M3 7h11v5h4l3 3v3h-3a2 2 0 0 1-4 0H8a2 2 0 0 1-4 0H3z',
  substation: 'M13 2 4 14h6l-1 8 9-12h-6z',
  power_plant: 'M13 2 4 14h6l-1 8 9-12h-6z',
  water_tower: 'M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z',
  water_works: 'M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z',
  pumping_station: 'M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z',
  wastewater: 'M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z',
  school: 'M12 3 2 8l10 5 10-5zM6 12v5c0 1.5 3 3 6 3s6-1.5 6-3v-5l-6 3z',
  college: 'M12 3 2 8l10 5 10-5zM6 12v5c0 1.5 3 3 6 3s6-1.5 6-3v-5l-6 3z',
  railway_station: 'M6 3h12v11a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3zM7 19l-2 3h14l-2-3z',
  bus_station: 'M4 4h16v10H4zM5 15h3v3H5zm11 0h3v3h-3z',
  fuel: 'M4 3h9v18H4zM15 7l3 3v7a2 2 0 0 0 3 0V9l-3-3z',
  open_space: 'M4 18h16v3H4zM12 3l7 12H5z',
  shelter: 'M12 3 3 10h3v9h12v-9h3z',
  government: 'M12 2l9 5v2H3V7zM5 11h3v7H5zm5.5 0h3v7h-3zM16 11h3v7h-3zM3 20h18v2H3z',
  telecom: 'M11 8h2v13h-2zM7 3a7 7 0 0 0 0 10M17 3a7 7 0 0 1 0 10',
  market: 'M3 6h18l-1.5 4H4.5zM5 11h14v9H5z',
  mall: 'M3 6h18l-1.5 4H4.5zM5 11h14v9H5z',
  industrial: 'M3 20V10l6 4V10l6 4V7h6v13z',
  post_office: 'M3 6h18v12H3zm1.6 1.4L12 13l7.4-5.6z',
  road: 'M9 3h2v4H9zm4 0h2v4h-2zM9 10h2v4H9zm4 0h2v4h-2zM9 17h2v4H9zm4 0h2v4h-2z',
  area: 'M4 4h7v7H4zm9 0h7v7h-7zM4 13h7v7H4zm9 0h7v7h-7z',
}

export const FALLBACK = 'M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14z'

export const pathFor = (category) => GLYPH[category] ?? FALLBACK

/** The same mark, as an element. */
export function Glyph({ category, size = 14, color = 'currentColor' }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill={color} aria-hidden="true">
      <path d={pathFor(category)} />
    </svg>
  )
}
