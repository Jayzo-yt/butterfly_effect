import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Glyph } from '../glyphs'

/** The first thing you do, so it lives in the command bar rather than in a
 *  rail. Operators know "KMC Hospital" and "NH 66"; nobody knows
 *  way/256638639, so ids are neither searchable nor shown. */
/** OSM keeps alternate spellings in one `name`, separated by semicolons:
 *  "Malpe-Manipal Road;Malpe - Manipal Road". Show the first. */
const firstName = (name) => (name || '').split(';')[0].trim() || name

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
      <svg className="search-glass" viewBox="0 0 24 24" width="13" height="13"
           fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="11" cy="11" r="6" />
        <path d="M16 16l4 4" />
      </svg>
      <input
        type="search"
        value={query}
        placeholder="Search asset, road or area"
        aria-label="Search asset, road or area"
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
                setQuery(firstName(r.name))
              }}>
                <span className="search-icon">
                  <Glyph category={r.kind === 'road' ? 'road' : r.kind === 'area' ? 'area' : r.category}
                         size={13} />
                </span>
                <span className="search-name">{firstName(r.name)}</span>
                <span className="search-kind">{r.kind}</span>
                <span className="search-meta">
                  {r.type}{r.area ? `, ${r.area}` : ''}
                  {r.supplies > 0 ? ` · supplies ${r.supplies} asset${r.supplies === 1 ? '' : 's'}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="search-empty">
          Nothing named &ldquo;{results.query}&rdquo; here. Try a category &mdash; hospital, water
          tower &mdash; or a shorter name.
        </p>
      ))}
    </div>
  )
}
