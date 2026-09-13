async function getJSON(path, opts) {
  const res = await fetch(path, opts)
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail)
  }
  return res.json()
}

const post = (path, body) =>
  getJSON(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const api = {
  getCity: () => getJSON('/city'),
  getFacilities: () => getJSON('/facilities'),
  getDisruptionTypes: () => getJSON('/disruption-types'),
  getDependencies: () => getJSON('/dependencies'),
  getValidation: () => getJSON('/validation'),
  getAreas: () => getJSON('/areas'),
  search: (q) => getJSON(`/search?q=${encodeURIComponent(q)}`),
  simulate: (incidents) => post('/simulate-incidents', { incidents }),
  analyze: (incident) => post('/analyze', incident),
  getCriticality: () => getJSON('/criticality'),
}
