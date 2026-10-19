"""Trusted-local CLI bulk ingestion. No public API accepts arbitrary paths."""
from pathlib import Path
import argparse
import json
import os
import sys
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.storage import KnowledgeStore
from marine_kb.ingestion import IngestionService, ALLOWED


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("roots", nargs="+", type=Path)
    cli.add_argument("--only-modern", action="store_true")
    cli.add_argument("--only-legacy", action="store_true")
    cli.add_argument("--limit", type=int, default=0)
    cli.add_argument("--office-cache", type=Path)
    args = cli.parse_args()
    if args.office_cache:
        os.environ["MARINE_KB_WORD_CACHE"] = str(args.office_cache.resolve())
    data = APP / "data" / "marine_kb"
    store = KnowledgeStore(data / "knowledge.sqlite3")
    service = IngestionService(store, data / "files", APP / "typhoon" / "data" / "typhoon_history.sqlite3")
    selected = []
    for root in args.roots:
        root = root.resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Source must be a real directory")
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not Path(parent, d).is_symlink() and not (Path(parent, d).stat().st_file_attributes & 1024 if os.name == "nt" else False)]
            for name in sorted(files):
                path = Path(parent, name)
                if path.is_symlink() or path.suffix.lower() not in ALLOWED or path.suffix.lower() == ".zip":
                    continue
                legacy = path.suffix.lower() in {".doc", ".wps"}
                if args.only_modern and legacy or args.only_legacy and not legacy:
                    continue
                selected.append((path, root.name + "/" + path.relative_to(root).as_posix()))
    if args.limit:
        selected = selected[:args.limit]
    payload = []
    for i, (path, relative) in enumerate(selected, 1):
        with path.open("rb") as stream:
            payload.append(service.stage(stream, relative))
        if i % 200 == 0:
            print(f"Staged {i}/{len(selected)}", flush=True)
    job = service.submit(payload)
    print(f"JOB_ID={job} total={len(payload)}", flush=True)
    while True:
        value = store.job(job)
        print(json.dumps({k: value[k] for k in ("job_id", "status", "completed", "total", "failed", "current_file")}, ensure_ascii=False), flush=True)
        if value["status"] not in {"queued", "running"}:
            break
        time.sleep(10)
    (data / f"import-report-{job}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(store.status(), ensure_ascii=False, default=str)[:2000], flush=True)
    service.pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
