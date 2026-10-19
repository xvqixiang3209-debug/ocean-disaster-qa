"""Create independent SQLite snapshots, never overwrite an existing phase-one DB."""
from pathlib import Path
import argparse
import sqlite3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    app = Path(__file__).resolve().parents[1]
    destination = app / "typhoon" / "data"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("typhoon_history.sqlite3", "typhoon_rag.sqlite3"):
        source = (args.source / "typhoon" / "data" / name).resolve(strict=True)
        target = destination / name
        if target.exists():
            print(f"Preserved existing {target.name}")
            continue
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(target) as dst:
                src.backup(dst)
                result = dst.execute("PRAGMA quick_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(result)
        print(f"Independent snapshot: {target.name} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
