"""Read-only conversion of provided legacy documents in bounded Word batches."""
from pathlib import Path
import argparse
import json
import sys
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.parsers import prepare_office_cache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('roots', nargs='+', type=Path)
    args = parser.parse_args()
    paths = []
    for root in args.roots:
        root = root.resolve(strict=True)
        for path in root.rglob('*'):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in {'.doc','.wps'} and not path.name.startswith('~$'):
                paths.append(path)
    cache = APP / 'data' / 'marine_kb' / 'office_cache'
    print(f'Converting {len(paths)} legacy documents read-only', flush=True)
    result = prepare_office_cache(paths, cache, batch_size=40, progress=lambda value: print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in value.items() if k!='files'}, ensure_ascii=False, default=str), flush=True))
    (cache / 'conversion-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, ensure_ascii=False, default=str), flush=True)


if __name__ == '__main__':
    main()
