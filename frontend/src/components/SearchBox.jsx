import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

/** The first thing you do, so it lives in the command bar rather than in a
 *  rail. Operators know "KMC Hospital" and "NH 66"; nobody knows
 *  way/256638639, so ids are neither searchable nor shown. */
export default function SearchBox({ onPick }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)
  const box = useRef(null)
  // Picking a result puts its name in the box, which would otherwise trigger a
  // fresh search and reopen the list over the panel just filled.
  const justPicked = useRef(false)

  useEffect(() => {
    if (justPicked.current) {
      justPicked.current = false
      return
    }
    if (query.trim().length < 2) {
      setResults(null)
      return
    }
    const id = setTimeout(() => {
      api.search(query.trim())
        .then((r) => { setResults(r); setError(null) })
        .catch((e) => setError(e.message))
    }, 180)
    return () => clearTimeout(id)
  }, [query])

  useEffect(() => {
    const away = (e) => { if (box.current && !box.current.contains(e.target)) setResults(null) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [])

  return (
    <div className="search" ref={box}>
      <input
        type="search"
        value={query}
        placeholder="Find a road, asset or area"
        aria-label="Find a road, asset or area"
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && setResults(null)}
        autoComplete="off"
      />

      {error && <p className="search-empty">Search unavailable: {error}</p>}

      {results && (results.results.length ? (
        <ul className="search-results">
          {results.results.map((r) => (
            <li key={`${r.kind}-${r.id}`}>
              <button onClick={() => {
                justPicked.current = true
                onPick(r)
                setResults(null)
                setQuery(r.name)
              }}>
                <span className="search-name">{r.name}</span>
                <span className="search-meta">
                  {r.type}{r.area ? `, ${r.area}` : ''}
                  {r.supplies > 0 ? ` — supplies ${r.supplies} asset${r.supplies === 1 ? '' : 's'}` : ''}
                  <span>{r.kind === 'road' ? 'road' : r.kind === 'area' ? 'area' : 'asset'}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="search-empty">
          Nothing named “{results.query}” here. Try a category — hospital, water tower — or a
          shorter name.
        </p>
      ))}
    </div>
  )
}
