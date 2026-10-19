"""Import a storm-surge folder through the same endpoint used by the UI."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    files = sorted(
        (path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in {".doc", ".xls", ".xlsx"}),
        key=lambda path: path.relative_to(folder).as_posix().casefold(),
    )
    if not files:
        raise SystemExit("No DOC/XLS/XLSX files found")

    from server import app

    multipart_files = [
        (io.BytesIO(path.read_bytes()), path.name) for path in files
    ]
    relative_paths = [path.relative_to(folder).as_posix() for path in files]
    with app.test_client() as client:
        response = client.post(
            "/api/typhoon/knowledge/import",
            data={
                "files": multipart_files,
                "relative_paths": relative_paths,
                "chunk_size": "800",
                "overlap": "120",
                "pdf_split_strategy": "pages",
            },
            content_type="multipart/form-data",
        )
    payload = response.get_json() or {}
    print(json.dumps({"status_code": response.status_code, "summary": payload.get("summary")}, ensure_ascii=False, indent=2))
    failures = [item for item in payload.get("files", []) if item.get("status") == "failed"]
    if failures:
        print(json.dumps({"failures": failures}, ensure_ascii=False, indent=2))
    report = args.report or (APP_DIR.parent / "storm_surge_folder_import_result.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    compact_report = {
        "source_folder": folder.as_posix(),
        "status_code": response.status_code,
        "summary": payload.get("summary") or {},
        "failures": failures,
    }
    report.write_text(json.dumps(compact_report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if response.status_code < 400 and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
