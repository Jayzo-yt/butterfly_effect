// Leaflet sets these via SVG setAttribute, which does not resolve CSS custom
// properties — so map colours need real values here, kept in sync with the
// tokens of the same name in index.css.
//
// The severity ramp is the loud one; `accent` is interactive-only and appears
// on the map just for the pre-run selection marker.
export const COLORS = {
  critical: '#ff5f52',
  high: '#ff9040',
  moderate: '#e8b43c',
  low: '#7e8ca3',
  clear: '#3cc583',
  edge: '#33425c',
  accent: '#5b7cff',
  water: '#4aa8d8',
  graticule: '#1b2434',
}

// One network, one colour, wherever a dependency is drawn — the map line, the
// cascade chain and the round badge all read from here.
export const NETWORK_COLOR = {
  power: '#e8b43c',
  water: '#4aa8d8',
  telecom: '#9b8cff',
}
