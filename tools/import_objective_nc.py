"""Index local objective NC sources without copying large original files."""
import argparse
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--root', type=Path, default=APP/'data/forecast_runtime/objective')
    args = parser.parse_args()
    from forecast_runtime.objective import ObjectiveForecast
    print(json.dumps(ObjectiveForecast(args.root).register_directory(args.paths), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
