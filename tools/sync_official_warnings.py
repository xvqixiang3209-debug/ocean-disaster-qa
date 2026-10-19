"""Acquire immutable official warning bodies and history, without printing tokens."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forecast_runtime.storage import ForecastStore,local_now
from forecast_runtime.official_warnings import OfficialWarnings

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year',type=int,default=local_now().year)
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    archive=OfficialWarnings(ForecastStore(ROOT/'data/forecast_runtime'))
    feeds=archive.ensure_year(args.year,refresh=args.refresh)
    print(json.dumps({'year':args.year,**archive.status()},ensure_ascii=False,indent=2))
    return 0 if all(f.get('complete') for f in feeds if f['provider']=='nmefc') else 1

if __name__=='__main__':raise SystemExit(main())
