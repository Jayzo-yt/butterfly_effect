/** Which single consequence to lead with.
 *
 *  The engine returns everything it found and grades each of them; it does not
 *  rank them against one another, because "worst" depends on who is asking.
 *  This is the display's answer to that question, kept in one place so the
 *  verdict panel and the cascade chain always point at the same asset.
 *
 *  Nothing here invents a figure. The order is: furthest down the chain, then
 *  the engine's own severity grade, then the group an incident room triages
 *  first — and the panel prints which of those rules picked the asset.
 */
export const LEVEL_RANK = { critical: 4, high: 3, moderate: 2, low: 1, none: 0 }

export const GROUP_RANK = {
  Emergency: 6, Healthcare: 5, Utilities: 4,
  Communications: 3, Transport: 2, 'Public services': 1, Commercial: 0,
}

export function primaryConsequence(result) {
  const indirect = result.cascade.nodes.filter((n) => n.round > 0)
  if (indirect.length) {
    const best = [...indirect].sort((a, b) => (
      b.round - a.round
      || LEVEL_RANK[b.level] - LEVEL_RANK[a.level]
      || (GROUP_RANK[b.group] ?? 0) - (GROUP_RANK[a.group] ?? 0)
      || b.impact - a.impact
    ))[0]
    return {
      kind: 'cascade',
      node: best,
      name: best.name,
      what: `${best.via_network_label} supply lost — ${best.category_label.toLowerCase()}`,
      note: `Round ${best.round} of the cascade, from ${best.via_source_name}. `
        + `${best.confidence[0].toUpperCase()}${best.confidence.slice(1)} confidence.`,
      level: best.level,
      lat: best.lat,
      lon: best.lon,
    }
  }

  const cut = result.facilities.filter((f) => f.cut_off && !f.is_incident_site)
  if (cut.length) {
    const best = [...cut].sort((a, b) => (GROUP_RANK[b.group] ?? 0) - (GROUP_RANK[a.group] ?? 0))[0]
    return {
      kind: 'access',
      name: best.name,
      what: 'No remaining route',
      note: best.reason,
      level: 'critical',
      lat: best.lat,
      lon: best.lon,
    }
  }

  const worst = [...result.facilities]
    .filter((f) => f.level !== 'none' && !f.is_incident_site)
    .sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || b.added_min - a.added_min)[0]
  if (worst) {
    return {
      kind: 'access',
      name: worst.name,
      what: `Access ${worst.added_min} min slower`,
      note: worst.reason,
      level: worst.level,
      lat: worst.lat,
      lon: worst.lon,
    }
  }

  // A quiet result is a result. Nothing here reaches for drama the model did
  // not find.
  return {
    kind: 'none',
    name: 'Nothing beyond the incident site',
    what: '',
    note: result.summary.explanation,
  }
}
