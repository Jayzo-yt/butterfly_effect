const LAYERS = [
  ['roads', 'Road network'],
  ['facilities', 'Assets'],
  ['zone', 'Impact zone'],
  ['cascade', 'Dependency links'],
  ['evacuation', 'Evacuation routes'],
  ['affectedOnly', 'Affected assets only'],
]

export default function LayerFilters({ layers, setLayers }) {
  return (
    <section className="key">
      <h2 className="panel-title">Map layers</h2>
      <div className="layers">
        {LAYERS.map(([key, label]) => (
          <label key={key} className="layer-toggle">
            <input
              type="checkbox"
              checked={layers[key]}
              onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })}
            />
            {label}
          </label>
        ))}
      </div>
    </section>
  )
}
