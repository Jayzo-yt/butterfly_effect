"""Which asset depends on which — derived, never invented.

The old model wired every hospital to its nearest substation and water tower
inside the ingestion script, baked the result into the data file as
`{"H1": ["S3", "W1"]}`, and gave the operator no way to tell a real
relationship from a guess. This replaces that with a model that states, for
every link, where it came from and how much to trust it.

A link exists only when the data supports one:

  explicit       an operator-supplied dependency file said so          (high)
  service_area   the consumer sits inside a supplier's plausible
                 service radius, and that supplier is the nearest one  (medium)
  distant        the nearest supplier is beyond its normal service
                 radius, so the link is possible but weak              (low)

When a network has no mapped infrastructure at all, no links are invented for
it. It is recorded in `gaps` and the UI says "dependency data unavailable"
rather than showing a cascade that does not exist. OSM has substations and
water towers for Manipal but no distribution network, no pumping stations and
no treatment plant, so the water chain here is genuinely incomplete — and the
report says so instead of papering over it.
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .infrastructure import CATEGORIES, label_of


@dataclass(frozen=True)
class Network:
    key: str
    label: str
    # Supply chain, upstream first. Each tier draws from the nearest asset in
    # the closest non-empty tier above it; consumers draw from the last
    # non-empty tier.
    tiers: Tuple[str, ...]
    # How far a delivery asset plausibly serves. Beyond this a link is still
    # reported, but as low confidence.
    service_range_m: float
    # Transmission between tiers runs much further than local distribution.
    transmission_range_m: float
    failure_effect: str
    # How long a consumer keeps going after supply stops — an electricity cut
    # is felt immediately, a water cut only once storage runs down. Drives the
    # incident timeline.
    propagation_delay_min: int = 5


NETWORKS: Dict[str, Network] = {n.key: n for n in [
    Network("power", "Power", ("power_plant", "substation"), 2500, 25000,
            "loses mains electricity", propagation_delay_min=2),
    Network("water", "Water", ("water_works", "pumping_station", "water_tower"), 3000, 15000,
            "loses piped water supply", propagation_delay_min=45),
    Network("telecom", "Communications", ("telecom",), 4000, 20000,
            "loses mobile and data coverage", propagation_delay_min=2),
]}

# Which network an asset *supplies*, derived from the tier tables above — so a
# failure at any utility asset knocks out the right network with no per-asset
# rule anywhere.
NETWORK_OF_CATEGORY: Dict[str, str] = {
    cat: net.key for net in NETWORKS.values() for cat in net.tiers
}

CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1}


@dataclass
class Link:
    source: str          # supplier facility id
    target: str          # consumer facility id
    network: str
    basis: str           # explicit | service_area | distant
    confidence: str
    distance_m: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target,
            "network": self.network, "network_label": NETWORKS[self.network].label,
            "basis": self.basis, "confidence": self.confidence,
            "distance_m": round(self.distance_m), "reason": self.reason,
        }


@dataclass
class DependencyModel:
    links: List[Link] = field(default_factory=list)
    by_consumer: Dict[str, List[Link]] = field(default_factory=dict)
    by_supplier: Dict[str, List[Link]] = field(default_factory=dict)
    gaps: List[dict] = field(default_factory=list)
    coverage: Dict[str, dict] = field(default_factory=dict)

    def add(self, link: Link):
        self.links.append(link)
        self.by_consumer.setdefault(link.target, []).append(link)
        self.by_supplier.setdefault(link.source, []).append(link)

    def dependents_of(self, facility_id: str) -> List[Link]:
        return self.by_supplier.get(facility_id, [])

    def suppliers_of(self, facility_id: str) -> List[Link]:
        return self.by_consumer.get(facility_id, [])

    def to_dict(self) -> dict:
        return {
            "links": [l.to_dict() for l in self.links],
            "gaps": self.gaps,
            "coverage": self.coverage,
            "networks": [{"key": n.key, "label": n.label,
                          "chain": [label_of(t) for t in n.tiers]}
                         for n in NETWORKS.values()],
        }


def _meters(a, b) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    mean_lat = math.radians((lat1 + lat2) / 2)
    return math.hypot((lon2 - lon1) * 111_320 * math.cos(mean_lat),
                      (lat2 - lat1) * 110_540)


def _nearest(point, candidates):
    """(id, distance) of the closest candidate, or (None, inf)."""
    best, best_d = None, math.inf
    for fid, geo in candidates:
        d = _meters(point, geo)
        if d < best_d:
            best, best_d = fid, d
    return best, best_d


def load_overrides(path: Optional[Path]) -> Dict[str, List[dict]]:
    """Operator-stated dependencies, which always beat a derived one.

    Format: {"consumer_facility_id": [{"source": "supplier_id",
                                       "network": "power"}, ...]}
    This is the hook for real utility data when a municipality provides it;
    until then the file simply does not exist and everything is derived.
    """
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build(facilities, overrides: Optional[dict] = None) -> DependencyModel:
    """Derive the dependency model from the facility inventory.

    `facilities` are impact.Facility objects (or anything with id / category /
    lat / lon / display_name).
    """
    model = DependencyModel()
    overrides = overrides or {}
    index = {f.id: f for f in facilities}
    geo = {f.id: (f.lat, f.lon) for f in facilities}

    def name(fid):
        f = index.get(fid)
        return getattr(f, "display_name", None) or fid

    # --- explicit links first; a derived link never overwrites one ----------
    explicit = set()
    for consumer, entries in overrides.items():
        for entry in entries:
            supplier, network = entry.get("source"), entry.get("network")
            if consumer not in index or supplier not in index or network not in NETWORKS:
                continue
            model.add(Link(
                source=supplier, target=consumer, network=network, basis="explicit",
                confidence="high", distance_m=_meters(geo[supplier], geo[consumer]),
                reason=f"{name(consumer)} is recorded as supplied by {name(supplier)}",
            ))
            explicit.add((consumer, network))

    for net in NETWORKS.values():
        tiers = [(key, [(f.id, geo[f.id]) for f in facilities if f.category == key])
                 for key in net.tiers]
        present = [(key, assets) for key, assets in tiers if assets]

        if not present:
            model.gaps.append({
                "network": net.key, "label": net.label,
                "message": (f"No {net.label.lower()} infrastructure is mapped in this "
                            f"extract, so {net.label.lower()} dependencies cannot be "
                            f"modelled."),
                "missing_tiers": [label_of(t) for t in net.tiers],
            })
            model.coverage[net.key] = {"suppliers": 0, "consumers_linked": 0,
                                       "consumers_unlinked": 0, "chain_complete": False}
            continue

        missing = [label_of(key) for key, assets in tiers if not assets]
        if missing:
            model.gaps.append({
                "network": net.key, "label": net.label,
                "message": (f"{net.label} chain is incomplete: no "
                            f"{', '.join(m.lower() for m in missing)} mapped. "
                            f"Propagation starts from the {present[-1][0].replace('_', ' ')} "
                            f"tier only."),
                "missing_tiers": missing,
            })

        # --- tier to tier (upstream supplies downstream) --------------------
        for upper, lower in zip(present, present[1:]):
            up_key, up_assets = upper
            low_key, low_assets = lower
            for fid, point in low_assets:
                supplier, distance = _nearest(point, up_assets)
                if supplier is None or distance > net.transmission_range_m:
                    continue
                model.add(Link(
                    source=supplier, target=fid, network=net.key,
                    basis="service_area", confidence="medium", distance_m=distance,
                    reason=(f"{name(fid)} is the {label_of(low_key).lower()} closest to "
                            f"{name(supplier)} ({distance / 1000:.1f} km); no distribution "
                            f"network is mapped, so the nearest {label_of(up_key).lower()} "
                            f"is taken as its source"),
                ))

        # --- delivery tier to consumers -------------------------------------
        delivery_key, delivery_assets = present[-1]
        linked = unlinked = 0
        for f in facilities:
            cat = CATEGORIES.get(f.category)
            if not cat or net.key not in cat.needs:
                continue
            if f.category in net.tiers or (f.id, net.key) in explicit:
                continue
            supplier, distance = _nearest(geo[f.id], delivery_assets)
            if supplier is None or supplier == f.id:
                continue
            if distance > net.service_range_m * 2.5:
                unlinked += 1
                continue
            within = distance <= net.service_range_m
            model.add(Link(
                source=supplier, target=f.id, network=net.key,
                basis="service_area" if within else "distant",
                confidence="medium" if within else "low",
                distance_m=distance,
                reason=(f"{name(f.id)} lies {distance / 1000:.1f} km from {name(supplier)}, "
                        f"the nearest {label_of(delivery_key).lower()}"
                        + ("" if within else
                           f" — beyond its usual {net.service_range_m / 1000:.1f} km service "
                           f"range, so this link is weak")),
            ))
            linked += 1

        model.coverage[net.key] = {
            "suppliers": sum(len(a) for _, a in present),
            "consumers_linked": linked,
            "consumers_unlinked": unlinked,
            "chain_complete": not missing,
            "delivery_tier": label_of(delivery_key),
        }
        if unlinked:
            model.gaps.append({
                "network": net.key, "label": net.label,
                "message": (f"{unlinked} facilities that need {net.label.lower()} have no "
                            f"mapped supplier within range; their {net.label.lower()} "
                            f"dependency is unknown."),
                "missing_tiers": [],
            })

    return model


def demo():
    """Self-check: derived links must be real, ranked and gap-aware."""
    from ..analysis.impact import Facility

    fac = [
        Facility(id="sub", name="Central substation", category="substation", lat=13.35, lon=74.79),
        Facility(id="tower", name="Manipal water tower", category="water_tower", lat=13.352, lon=74.792),
        Facility(id="hosp", name="District Hospital", category="hospital", lat=13.353, lon=74.793),
        Facility(id="far", name="Far clinic", category="clinic", lat=13.60, lon=75.10),
    ]
    model = build(fac)
    nets = {l.network for l in model.suppliers_of("hosp")}
    assert nets == {"power", "water"}, nets
    assert not model.suppliers_of("far"), "a facility 30 km from any supplier must not be linked"
    assert any(g["network"] == "telecom" for g in model.gaps), "missing telecom must be a gap"
    assert any("incomplete" in g["message"] for g in model.gaps if g["network"] == "water")
    assert all(l.confidence in CONFIDENCE_ORDER for l in model.links)
    print(f"ok — {len(model.links)} links, {len(model.gaps)} gaps")


if __name__ == "__main__":
    demo()
