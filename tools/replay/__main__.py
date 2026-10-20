"""Validate a private cassette without revealing its raw content."""
import argparse
import json

from marine_kb.replay import CassetteError, ModelCassette


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Check integrity/completion; print only counts")
    inspect.add_argument("--cassette", required=True)
    args = parser.parse_args(argv)
    try:
        cassette = ModelCassette(args.cassette)
        result = {"integrity": "verified", "request_replay": "not_executed", **cassette.summary()}
        cassette.close(complete=False)
    except CassetteError as exc:
        print(json.dumps({"integrity": "failed", "error_code": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
