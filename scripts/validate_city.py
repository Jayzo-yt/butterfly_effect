"""Validate an ingested city extract, and optionally repair it.

    python scripts/validate_city.py data/city/manipal.json
    python scripts/validate_city.py data/city/manipal.json --fix

--fix rewrites the file with self-loops, dangling edges, duplicate edges and
coordinate-less nodes removed, then re-validates. Exit code is non-zero while
anything fatal remains, so this can gate a deploy.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.graph.validate import clean, dedupe_facilities, format_report, report  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="data/city/manipal.json")
    ap.add_argument("--fix", action="store_true", help="remove invalid elements and rewrite")
    args = ap.parse_args()

    path = Path(args.path)
    data = json.loads(path.read_text(encoding="utf-8"))
    poi = path.with_suffix(".poi.json")
    facilities = json.loads(poi.read_text(encoding="utf-8"))["facilities"] if poi.exists() else None

    print(f"Validating {path}")
    rep = report(data, facilities)
    print(format_report(rep))

    # --fix repairs anything repairable, not only what fails validation:
    # duplicate assets are a warning, but they put the same hospital twice in
    # every operator-facing list, which is its own kind of wrong.
    if args.fix:
        data, removed = clean(data)
        for line in removed[:20]:
            print(f"  removed {line}")
        if len(removed) > 20:
            print(f"  ...and {len(removed) - 20} more")
        if facilities:
            kept, dropped = dedupe_facilities(
                [f for f in facilities if f.get("lat") is not None])
            if dropped:
                payload = json.loads(poi.read_text(encoding="utf-8"))
                payload["facilities"] = kept
                poi.write_text(json.dumps(payload), encoding="utf-8")
                facilities = kept
                print(f"  removed {len(dropped)} duplicate assets from {poi.name}")
                for gone, twin in dropped[:8]:
                    print(f"    {gone!r} is the same asset as {twin!r}")

        backup = path.with_suffix(".prefix.json")
        backup.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
        path.write_text(json.dumps(data), encoding="utf-8")
        print(f"\nRewrote {path} (previous version kept at {backup.name})")
        rep = report(data, facilities)
        print(format_report(rep))

    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
