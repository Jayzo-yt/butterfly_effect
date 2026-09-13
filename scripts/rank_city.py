"""Precompute the criticality ranking so the server boots warm, and test
whether that ranking actually survives the model's biggest assumption.

Two jobs, because they share the expensive machinery:

    python scripts/rank_city.py data/city/manipal.json
    python scripts/rank_city.py data/city/manipal.json --sensitivity 30

The ranking is written next to the city file as <city>.criticality.json and
loaded at startup by src/api/main.py — leave-one-out over 640 nodes takes
about a minute, which is a bad way to begin a live demo.

**Sensitivity is the important part.** Which substation feeds which hospital
is a nearest-asset guess (OSM has no distribution network here — see
assumptions.md). If the ranking only holds for our particular guess, it is
arithmetic on an assumption. This re-runs the ranking over randomised
hospital-to-utility wirings and reports how often each asset still lands in
the top few.
"""
import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.analysis.criticality import (  # noqa: E402
    betweenness_baseline,
    leave_one_out_ranking,
    representative_junctions,
)
from src.graph.multiplex import CityGraph  # noqa: E402


def rank(city: CityGraph, candidates=None) -> dict:
    hospitals = city.nodes_of_type("hospital")
    junctions = representative_junctions(city)
    od_pairs = [(j, h) for j in junctions for h in hospitals]
    return leave_one_out_ranking(city, od_pairs, junctions, hospitals, candidates=candidates)


def top_n(scores: dict, n: int) -> list:
    return [node for node, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:n]]


def precompute(city_path: Path) -> Path:
    city = CityGraph.load(city_path)
    payload = {"outcome_scores": rank(city), "betweenness_baseline": betweenness_baseline(city)}
    out = city_path.with_suffix(".criticality.json")
    out.write_text(json.dumps(payload))
    return out


def sensitivity(city_path: Path, runs: int, top: int = 5) -> dict:
    """Re-rank under randomised dependency wirings.

    Only the hospital->utility assignment is randomised; the road network,
    the assets themselves and their positions are all real and stay fixed.
    """
    base = CityGraph.load(city_path)
    hospitals = base.nodes_of_type("hospital")
    utilities = list(base.G_utility.nodes)
    substations = [u for u in utilities if base.G_utility.nodes[u]["type"] == "substation"]
    water = [u for u in utilities if base.G_utility.nodes[u]["type"] != "substation"]
    if not substations or not water:
        raise SystemExit("city has no utility layer to randomise")

    # Only the assets that could plausibly place need re-scoring: the utility
    # layer (whose wiring is what we are varying) plus the road nodes that
    # already rank highest. Re-scoring all ~4000 nodes per run would take
    # hours and answer the same question.
    cached = city_path.with_suffix(".criticality.json")
    if not cached.exists():
        raise SystemExit(f"run `python {Path(__file__).name} {city_path}` first "
                         "— sensitivity re-scores the ranked assets, so it needs the ranking")
    ranked = json.loads(cached.read_text())["outcome_scores"]
    candidates = set(utilities) | set(top_n(ranked, 40))
    print(f"  re-scoring {len(candidates)} candidate assets per run "
          f"(of {len(ranked)} total)")

    appearances = Counter()
    utility_set = set(utilities)
    runs_with_utility = 0
    rng = random.Random(0)
    for i in range(runs):
        city = CityGraph.load(city_path)
        for h in hospitals:
            city.set_dependency(h, [rng.choice(substations), rng.choice(water)])
        leaders = top_n(rank(city, candidates=candidates), top)
        for node in leaders:
            appearances[node] += 1
        if any(n in utility_set for n in leaders):
            runs_with_utility += 1
        print(f"  run {i + 1}/{runs}", end="\r", flush=True)

    print(" " * 40, end="\r")
    return {
        "per_asset": {node: count / runs for node, count in appearances.items()},
        # The claim that actually survives: not "this particular asset is
        # critical" — which moves with the wiring guess — but "an asset
        # centrality cannot see reaches the top regardless of the guess".
        "runs_with_utility_in_top": runs_with_utility / runs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city", type=Path)
    ap.add_argument("--sensitivity", type=int, metavar="RUNS",
                    help="re-rank under N randomised wirings instead of precomputing")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    if args.sensitivity:
        print(f"Re-ranking under {args.sensitivity} randomised hospital->utility wirings...")
        out = sensitivity(args.city, args.sensitivity, args.top)
        stability = out["per_asset"]
        baseline_top = top_n(json.loads(
            args.city.with_suffix(".criticality.json").read_text()
        )["outcome_scores"], args.top) if args.city.with_suffix(".criticality.json").exists() else []

        print(f"\nHow often each asset stays in the top {args.top}:")
        for node, share in sorted(stability.items(), key=lambda kv: -kv[1]):
            mark = "  <- in our wiring's top" if node in baseline_top else ""
            print(f"  {node:16s} {share:6.0%}{mark}")

        robust = [n for n, s in stability.items() if s >= 0.8]
        print(f"\nHolds the top {args.top} in >=80% of wirings: "
              f"{', '.join(robust) if robust else 'none'}")
        print(f"An asset invisible to centrality reaches the top {args.top} in "
              f"{out['runs_with_utility_in_top']:.0%} of wirings.")
        print("\nThe second number is the defensible claim. Which utility asset ranks")
        print("highest depends on the wiring we assumed; that one of them ranks highly")
        print("does not.")

        out_path = args.city.with_suffix(".sensitivity.json")
        out_path.write_text(json.dumps({
            "runs": args.sensitivity,
            "top": args.top,
            "per_asset": stability,
            "runs_with_utility_in_top": out["runs_with_utility_in_top"],
        }))
        print(f"\nWrote {out_path} — served by the API so the ranking can show its "
              "own confidence.")
    else:
        out = precompute(args.city)
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
