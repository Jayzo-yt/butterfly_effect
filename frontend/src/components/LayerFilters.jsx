const LAYERS = [
  ['roads', 'Network'],
  ['facilities', 'Assets'],
  ['zone', 'Impact zone'],
  ['cascade', 'Dependencies'],
  ['evacuation', 'Evacuation'],
  ['graticule', 'Grid'],
  ['affectedOnly', 'Affected only'],
]

export default function LayerFilters({ layers, setLayers }) {
  return (
    <section className="panel instrument">
      <span className="ticks" />
      <div className="head"><span className="cap">Map layers</span></div>
      <div className="layers">
        {LAYERS.map(([key, label]) => (
          <label key={key} className="layer-toggle">
            <input
              type="checkbox"
              checked={layers[key]}
              onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })}
            />
            <span className="layer-pip" />
            <span>{label}</span>
          </label>
        ))}
      </div>
    </section>
  )
}
