"""Command-line entry point for building the local IBTrACS knowledge base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DEFAULT_DATABASE, DEFAULT_IBTRACS_ZIP, DEFAULT_STORM_SURGE_ARCHIVE
from .knowledge_base import build_database
from .storm_surge_import import import_storm_surge_archive


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Western Pacific typhoon knowledge base")
    parser.add_argument("--source", default=str(DEFAULT_IBTRACS_ZIP), help="Path to ibtracs.zip")
    parser.add_argument("--output", default=str(DEFAULT_DATABASE), help="Output SQLite path")
    parser.add_argument("--force", action="store_true", help="Replace an existing database")
    parser.add_argument(
        "--storm-surge-source",
        default=str(DEFAULT_STORM_SURGE_ARCHIVE),
        help="Path to the historical storm-surge RAR archive",
    )
    parser.add_argument("--skip-storm-surge", action="store_true", help="Build only IBTrACS tables")
    args = parser.parse_args()

    def progress(storms, points):
        print(f"Processed {storms} storms / {points} track points...", flush=True)

    result = build_database(args.source, args.output, force=args.force, progress=progress)
    if not args.skip_storm_surge:
        surge_source = Path(args.storm_surge_source)
        if surge_source.exists():
            result["storm_surge"] = import_storm_surge_archive(
                surge_source,
                args.output,
                args.source,
                progress=lambda index, total, filename: (
                    print(f"Processed storm-surge workbook {index}/{total}: {filename}", flush=True)
                    if index == total or index % 10 == 0
                    else None
                ),
            )
        else:
            result["storm_surge"] = {"imported": False, "reason": f"archive not found: {surge_source}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
