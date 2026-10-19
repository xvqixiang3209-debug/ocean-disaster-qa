"""Prepare Zhejiang-envelope source-grid indexes and exercise actual queries."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spatial_runtime import SpatialRuntime, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--bbox", nargs=4, type=float, default=[118, 27, 124, 32], metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--query-only", action="store_true")
    args = parser.parse_args()
    if not args.query_only:
        prepare(args.root, args.bbox)
    runtime = SpatialRuntime(args.root)
    samples = {"status": runtime.status(),
               "zhujiajian_point": runtime.point(122.39, 29.89),
               "hangzhou_point": runtime.point(120.15, 30.27),
               "zhujiajian_population": runtime.population_bbox(122.35, 29.85, 122.45, 29.95)}
    output = runtime.base / "verification_queries.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(samples, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
