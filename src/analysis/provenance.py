"""Where a number came from, and the difference between nothing and no answer.

Two things every figure in this system has to carry.

**Provenance.** "600 occupants" and "13 minutes" look identical on screen and
are not remotely the same claim: one is a planning constant that applies to
every school in the model, the other is a shortest path over observed road
geometry. A reader who cannot tell them apart will trust the wrong one.

**The four kinds of nothing.** A zero is a finding; the absence of an answer is
not. These are different sentences and the model keeps them apart:

    none            measured, and the answer is zero — "no hospital lost access"
    unknown         the data cannot answer — "ambulance availability is not published"
    not_applicable  the question does not arise — "a power cut closes no roads"
    missing_data    the input needed is absent — "no telecom asset is mapped"

Collapsing any of those into 0 is how a model starts lying.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

# --- how a figure was arrived at ---------------------------------------------

MEASURED = "measured"          # observed directly in the source data
CALCULATED = "calculated"      # computed from measured inputs only
ESTIMATED = "estimated"        # computed, but using a documented assumption
ASSUMED = "assumed"            # a judgement value, not derived from anything
UNKNOWN = "unknown"            # the data cannot answer this
NOT_APPLICABLE = "not_applicable"
MISSING_DATA = "missing_data"  # the input that would answer it is not present

# What each provenance is worth as confidence.
CONFIDENCE_OF = {
    MEASURED: "high",
    CALCULATED: "high",
    ESTIMATED: "medium",
    ASSUMED: "low",
    UNKNOWN: "low",
    MISSING_DATA: "low",
    NOT_APPLICABLE: "high",   # knowing it does not apply is a firm answer
}

_RANK = {"high": 3, "medium": 2, "low": 1}
_BAND = {3: "high", 2: "medium", 1: "low"}


@dataclass
class Finding:
    """One reported figure, with its provenance and the sentence behind it."""
    value: Optional[Union[float, int, str]]
    state: str
    basis: str
    unit: str = ""

    @property
    def known(self) -> bool:
        return self.state not in (UNKNOWN, MISSING_DATA, NOT_APPLICABLE)

    @property
    def confidence(self) -> str:
        return CONFIDENCE_OF.get(self.state, "low")

    def to_dict(self) -> dict:
        return {
            "value": self.value if self.known else None,
            "state": self.state,
            "basis": self.basis,
            "unit": self.unit,
            "confidence": self.confidence,
            # A display hint, so the UI never has to re-derive the distinction
            # between "0" and "we cannot say".
            "display": self._display(),
        }

    def _display(self) -> str:
        if self.state == NOT_APPLICABLE:
            return "not applicable"
        if self.state in (UNKNOWN, MISSING_DATA):
            return "unknown"
        if self.value is None:
            return "unknown"
        text = f"{self.value:,}" if isinstance(self.value, (int, float)) else str(self.value)
        if self.unit:
            text = f"{text} {self.unit}"
        return f"~{text}" if self.state in (ESTIMATED, ASSUMED) else text


def measured(value, basis, unit=""):
    return Finding(value, MEASURED, basis, unit)


def calculated(value, basis, unit=""):
    return Finding(value, CALCULATED, basis, unit)


def estimated(value, basis, unit=""):
    return Finding(value, ESTIMATED, basis, unit)


def assumed(value, basis, unit=""):
    return Finding(value, ASSUMED, basis, unit)


def unknown(basis, unit=""):
    return Finding(None, UNKNOWN, basis, unit)


def missing(basis, unit=""):
    return Finding(None, MISSING_DATA, basis, unit)


def not_applicable(basis, unit=""):
    return Finding(None, NOT_APPLICABLE, basis, unit)


# --- confidence, per component ------------------------------------------------

# How much each part of the answer matters to the overall grade. A missing
# telecom layer should not drag the whole simulation to "low" when the road
# impact — the thing an operator acts on first — is solid.
COMPONENT_WEIGHT = {
    "road_impact": 3.0,
    "emergency_routing": 3.0,
    "dependencies": 2.0,
    "population": 1.0,
    "occupancy": 1.0,
    "hazard_extent": 1.5,
    "resources": 1.0,
    "recovery": 1.0,
}

COMPONENT_LABEL = {
    "road_impact": "Road impact",
    "emergency_routing": "Emergency routing",
    "dependencies": "Dependency cascade",
    "population": "Population exposure",
    "occupancy": "Occupancy",
    "hazard_extent": "Hazard extent",
    "resources": "Resource availability",
    "recovery": "Recovery timing",
}


class Confidence:
    """Component grades, and one overall figure that does not collapse to the
    worst of them.

    The old model took the weakest reason and applied it to everything, so a
    simulation whose routing was solid still read "low confidence" because no
    telecom mast is mapped. That teaches an operator to ignore the grade.
    """

    def __init__(self):
        self.components: Dict[str, dict] = {}

    def note(self, component: str, level: str, reason: str) -> None:
        """Record a grade. The worst grade for a given component wins, since a
        component is only as good as its weakest input."""
        held = self.components.get(component)
        if held and _RANK[held["level"]] <= _RANK[level]:
            return
        self.components[component] = {"level": level, "reason": reason}

    def from_finding(self, component: str, finding: Finding, reason: str = "") -> None:
        self.note(component, finding.confidence, reason or finding.basis)

    def overall(self) -> str:
        if not self.components:
            return "low"
        total = weighted = 0.0
        for key, entry in self.components.items():
            w = COMPONENT_WEIGHT.get(key, 1.0)
            total += w
            weighted += w * _RANK[entry["level"]]
        return _BAND[max(1, min(3, round(weighted / total)))]

    def to_dict(self) -> dict:
        components = [
            {"key": key, "label": COMPONENT_LABEL.get(key, key.replace("_", " ").capitalize()),
             "level": entry["level"], "reason": entry["reason"]}
            for key, entry in sorted(self.components.items(),
                                     key=lambda kv: -COMPONENT_WEIGHT.get(kv[0], 1.0))
        ]
        weakest = [c for c in components if c["level"] == "low"]
        return {
            "level": self.overall(),
            "components": components,
            "limits": [c["reason"] for c in weakest],
            "note": ("Graded per component and combined by weight, so a missing dataset in one "
                     "layer does not discredit the layers that are sound."),
        }


def demo():
    assert measured(0, "no hospital lost access")._display() == "0"
    assert unknown("not published")._display() == "unknown"
    assert not_applicable("a power cut closes no roads")._display() == "not applicable"
    assert estimated(600, "category planning figure")._display() == "~600"
    assert not unknown("x").known and measured(0, "x").known

    c = Confidence()
    c.note("road_impact", "high", "observed geometry")
    c.note("emergency_routing", "high", "routed on the network")
    c.note("dependencies", "medium", "service-area inference")
    c.note("occupancy", "low", "category planning figure")
    # One low-weight component must not sink a well-supported result.
    assert c.overall() == "high", c.overall()

    c.note("road_impact", "low", "no geometry")
    assert c.overall() == "medium", c.overall()
    assert c.to_dict()["limits"], "a low component has to surface as a stated limit"
    print("ok — provenance states and weighted confidence")


if __name__ == "__main__":
    demo()
