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
  accent: '#5b7cff',
  graticule: '#141c28',
}

// One network, one colour, wherever a dependency is drawn — the map line, the
// cascade chain and the round badge all read from here.
export const NETWORK_COLOR = {
  power: '#e8b43c',
  water: '#4aa8d8',
  telecom: '#9b8cff',
}

/** The road network has a hierarchy and drawing it flat throws that away.
 *
 *  2,205 of the 3,507 segments in this extract are residential; 188 are trunk.
 *  At one weight the trunk roads vanish into the residential mesh and the map
 *  reads as noise. Weighted by class, the arterial structure of Udupi and
 *  Manipal is legible at a glance — which is what makes a closure on a trunk
 *  road obviously different from one on a side street.
 */
export const ROAD = {
  motorway:       { w: 2.6, c: '#7d9bc4', o: 1 },
  trunk:          { w: 2.4, c: '#7593bd', o: 0.98 },
  trunk_link:     { w: 1.7, c: '#5f7ba3', o: 0.8 },
  primary:        { w: 2.0, c: '#6a88b2', o: 0.94 },
  primary_link:   { w: 1.5, c: '#57739a', o: 0.76 },
  secondary:      { w: 1.6, c: '#55719a', o: 0.84 },
  secondary_link: { w: 1.2, c: '#485f80', o: 0.7 },
  tertiary:       { w: 1.3, c: '#485f82', o: 0.76 },
  residential:    { w: 1.0, c: '#3a4d6b', o: 0.62 },
  living_street:  { w: 1.0, c: '#3a4d6b', o: 0.62 },
  unclassified:   { w: 1.0, c: '#364764', o: 0.56 },
  service:        { w: 0.8, c: '#2f3f58', o: 0.44 },
}

export const ROAD_DEFAULT = { w: 1.0, c: '#364764', o: 0.54 }

export const roadStyle = (klass) => ROAD[klass] ?? ROAD_DEFAULT
