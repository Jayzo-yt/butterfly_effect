// Leaflet sets these via SVG setAttribute, which does not resolve CSS custom
// properties — so map colours need real values here, kept in sync with the
// tokens of the same name in index.css.
//
// The severity ramp is the loud one; `accent` is interactive-only and appears
// on the map just for the pre-run selection marker.
export const COLORS = {
  critical: '#ff5b52',
  high: '#ff9142',
  moderate: '#e6b53c',
  low: '#8598a8',
  clear: '#3fc27a',
  edge: '#4e657a',
  accent: '#7b6bff',
}
