"""Resume local page-level OCR without changing source files, approvals, or SQL.

The recovery plan is intentionally not an approval plan: OCR confidence is not
proof that a decimal point, inequality, formula, or table relation is correct.
Each source and page carries its own identity and review boundary.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.parsers import _clean, _ocr_version, _text_quality, _unit, parse_file


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_key(digest: str, page: int, pipeline: str) -> str:
    return hashlib.sha256(f"{digest}:{page}:{pipeline}".encode()).hexdigest()


def valid_payload(payload, digest, page, pipeline):
    return (isinstance(payload, dict) and payload.get("source_sha256") == digest
            and payload.get("page") == page and payload.get("pipeline_version") == pipeline)


def reusable_payload(path, digest, page, pipeline):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if valid_payload(payload, digest, page, pipeline) else None


def choose_ocr_pages(page_records):
    """All failed physical pages; no implicit first-30/first-200 slice."""
    return [row["page"] for row in page_records if row["text_layer_quality"] != "usable"]


def ocr_eligible(payload):
    if not payload or payload.get("status") != "ok":
        return False
    try:
        confidence = float(payload.get("confidence_mean", 0))
    except (ValueError, TypeError):
        return False
    return _text_quality(_clean(payload.get("text", ""))) == "usable" and confidence >= .78


def source_identity(row, raw_root):
    raw = Path(row["raw_path"])
    resolved = raw.resolve(strict=True)
    if raw.is_symlink() or not resolved.is_relative_to(raw_root.resolve()) or not resolved.is_file():
        raise ValueError("Source must be an immutable regular file under the registered raw root")
    if resolved.stat().st_size != row["bytes"] or file_sha(resolved) != row["sha256"]:
        raise ValueError("Registered source SHA256 or size differs")
    return resolved


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute_job(job, timeout):
    try:
        result = subprocess.run([sys.executable, str(APP / "marine_kb/parsers.py"),
                                 "--local-ocr-worker", str(job)], capture_output=True,
                                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"job": str(job), "returncode": result.returncode,
                "completed_lines": len(result.stdout.splitlines()),
                "error": result.stderr.decode("utf-8", errors="replace")[-600:] if result.returncode else None}
    except subprocess.TimeoutExpired:
        return {"job": str(job), "returncode": None, "timed_out": True,
                "note": "Completed atomic page cache entries remain reusable; rerun resumes remaining pages."}


def raster_sample(path, page_number, destination):
    import pypdfium2 as pdfium
    from PIL import ImageStat
    document = pdfium.PdfDocument(str(path))
    page = bitmap = picture = None
    try:
        page = document[page_number - 1]
        width, height = page.get_size()
        scale = min(1.5, 1800 / max(width, height))
        bitmap = page.render(scale=scale, may_draw_forms=False, draw_annots=False)
        picture = bitmap.to_pil().convert("RGB")
        destination.parent.mkdir(parents=True, exist_ok=True)
        picture.save(destination, format="PNG")
        small = picture.convert("L"); small.thumbnail((400, 550))
        hist = small.histogram()
        dark_ratio = sum(hist[:200]) / max(1, sum(hist))
        return {"path": str(destination.resolve()), "sha256": file_sha(destination),
                "render_scale": scale, "dark_pixel_fraction": dark_ratio,
                "mechanically_near_blank": dark_ratio < .0005,
                "visually_reviewed": False}
    finally:
        if picture is not None: picture.close()
        if bitmap is not None: bitmap.close()
        if page is not None: page.close()
        document.close()


def merge_tiled_lines(lines):
    """Deduplicate only overlapping copies; repeated text elsewhere is retained."""
    kept = []
    for row in sorted(lines, key=lambda item: -item["confidence"]):
        x0, y0, x1, y1 = row["bbox"]
        duplicate = False
        for prior in kept:
            px0, py0, px1, py1 = prior["bbox"]
            overlap = max(0, min(x1, px1) - max(x0, px0)) * max(0, min(y1, py1) - max(y0, py0))
            denominator = max(1, min((x1-x0)*(y1-y0), (px1-px0)*(py1-py0)))
            if overlap / denominator > .65 and re.sub(r"\s", "", row["text"]) == re.sub(r"\s", "", prior["text"]):
                duplicate = True; break
        if not duplicate: kept.append(row)
    return sorted(kept, key=lambda row: (round(row["bbox"][1] / 12), row["bbox"][0]))


def tile_pdf_page(path, page_number, expected_sha, output, grayscale_cutoff=None):
    """Local overlapping bands retain small lines lost by full-page resizing.

    This is an alternate transcript, never a synthetic correction or approval.
    Absolute page boxes make every detected line traceable to its image region.
    """
    import numpy as np
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR
    import rapidocr_onnxruntime.utils as rapid_utils
    actual_sha = file_sha(path)
    if actual_sha != expected_sha: raise ValueError("Tile OCR source SHA256 differs")
    original_options = rapid_utils.SessionOptions
    def bounded_options():
        options = original_options(); options.intra_op_num_threads = 2; options.inter_op_num_threads = 1
        return options
    rapid_utils.SessionOptions = bounded_options
    engine = RapidOCR(det_model_path=None, det_limit_side_len=1600, det_limit_type="max")
    output.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(path)); page = bitmap = picture = None
    try:
        if page_number < 1 or page_number > len(document): raise ValueError("Invalid physical page")
        page = document[page_number - 1]; width, height = page.get_size()
        scale = min(2.6, 1600 / width, 4000 / height)
        bitmap = page.render(scale=scale, may_draw_forms=False, draw_annots=False)
        picture = bitmap.to_pil().convert("RGB"); picture.save(output / "original_page.png")
        if grayscale_cutoff is not None:
            if not 80 <= grayscale_cutoff <= 220: raise ValueError("Contrast cutoff outside supported range")
            contrasted = picture.convert("L").point(lambda value: 0 if value <= grayscale_cutoff else 255).convert("RGB")
            picture.close(); picture = contrasted
            picture.save(output / "ocr_contrast_input.png")
        lines, bands = [], []
        tile_height = min(1100, picture.height)
        step = max(1, tile_height - 160)
        for start in range(0, picture.height, step):
            end = min(picture.height, start + tile_height)
            if end - start < 100: break
            crop = picture.crop((0, start, picture.width, end))
            detected, elapsed = engine(np.asarray(crop)); crop.close()
            bands.append({"top": start, "bottom": end, "line_count": len(detected or []), "stage_seconds": elapsed})
            for box, text, confidence in detected or []:
                xs = [float(point[0]) for point in box]; ys = [float(point[1]) + start for point in box]
                lines.append({"text": str(text), "confidence": float(confidence),
                              "bbox": [min(xs), min(ys), max(xs), max(ys)], "tile_top": start})
        merged = merge_tiled_lines(lines)
        result = {"source_sha256": actual_sha, "page": page_number,
                  "method": "local_rapidocr_overlapping_bands", "pipeline_version": _ocr_version() + "-bands-v1",
                  "render_scale": scale, "image_width": picture.width, "image_height": picture.height,
                  "grayscale_cutoff": grayscale_cutoff,
                  "bands": bands, "lines": merged, "text": "\n".join(row["text"] for row in merged),
                  "requires_human_review": True, "approved": False,
                  "note": "备用分带识别可能改善漏行，表格/公式及数值须对照原页，不能用OCR置信度自动通过。"}
        write_json(output / "tile_ocr.json", result)
        (output / "tile_ocr.txt").write_text(result["text"], encoding="utf-8")
        return result
    finally:
        if picture is not None: picture.close()
        if bitmap is not None: bitmap.close()
        if page is not None: page.close()
        document.close()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, default=APP / "data/marine_kb/knowledge.sqlite3")
    cli.add_argument("--output", type=Path, default=APP / "tmp/source_review_20261021/ocr")
    cli.add_argument("--workers", type=int, default=2)
    cli.add_argument("--timeout", type=int, default=300)
    cli.add_argument("--skip-ocr", action="store_true", help="Inventory and reuse existing cache only")
    cli.add_argument("--tile-pdf", type=Path, help="Alternate local high-resolution band OCR of one source page")
    cli.add_argument("--tile-page", type=int)
    cli.add_argument("--tile-sha256")
    cli.add_argument("--tile-grayscale-cutoff", type=int)
    args = cli.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    if args.tile_pdf is not None:
        if args.tile_page is None or not args.tile_sha256: cli.error("Tile mode requires --tile-page and --tile-sha256")
        result = tile_pdf_page(args.tile_pdf.resolve(strict=True), args.tile_page, args.tile_sha256, output, args.tile_grayscale_cutoff)
        print(json.dumps({"page": result["page"], "lines": len(result["lines"]), "output": str(output),
                          "approved": False}, ensure_ascii=False))
        return
    database = args.database.resolve()
    raw_root = database.parent / "files/raw"
    pipeline = _ocr_version()
    if pipeline is None: raise RuntimeError("Local OCR packages unavailable; no cloud fallback")
    cache = output / "page_cache"; cache.mkdir(exist_ok=True)
    prior_cache = APP / "data/ocr_cache"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row; connection.execute("PRAGMA query_only=ON")
    rows = [dict(row) for row in connection.execute("SELECT * FROM sources WHERE status='pending_review' AND category NOT IN ('模式结果','空间数据') ORDER BY category, filename")]
    frozen = [(row["source_id"], row["sha256"], row["updated_at"], row["status"]) for row in rows]
    documents, jobs, seen_jobs = [], [], set()
    from pypdf import PdfReader
    for row in rows:
        source_dir = output / row["source_id"]; source_dir.mkdir(exist_ok=True)
        result = {"source_id": row["source_id"], "filename": row["filename"],
                  "category": row["category"], "source_sha256": row["sha256"],
                  "previous_updated_at": row["updated_at"], "previous_status": row["status"],
                  "previous_quality_status": row["quality_status"],
                  "previous_metadata": json.loads(row["metadata"]),
                  "previous_warnings": json.loads(row["warnings"]),
                  "approval_action": False, "external_permission_changed": False,
                  "page_records": []}
        try:
            path = source_identity(row, raw_root)
            result["raw_path"] = str(path)
            if path.suffix.lower() != ".pdf":
                # Reuse the content-addressed Word conversion cache where present.
                word_caches = [APP / "data/marine_kb/office_cache", APP / "data/word_cache", APP / "data/marine_kb/word_cache"]
                for word_cache in word_caches:
                    if (word_cache / (row["sha256"] + ".docx")).is_file():
                        os.environ["MARINE_KB_WORD_CACHE"] = str(word_cache)
                        break
                os.environ["MARINE_KB_DISABLE_WORD_COM"] = "1"
                parsed = parse_file(path)
                result.update({"format": path.suffix.lower().lstrip("."), "parsed": parsed,
                               "requires_human_review": True, "remaining_reason": "转换结果/嵌入图片及正式版本需分别核对"})
            else:
                reader = PdfReader(str(path), strict=False)
                if reader.is_encrypted: raise ValueError("Encrypted PDF is not decrypted")
                records = []
                for page_number, page in enumerate(reader.pages, 1):
                    try:
                        text = _clean(page.extract_text() or "")
                        quality = _text_quality(text)
                    except Exception:
                        text, quality = "", "extraction_error"
                    (source_dir / f"page_{page_number:04d}.text_layer.txt").write_text(text, encoding="utf-8")
                    records.append({"page": page_number, "locator": f"page:{page_number}",
                                    "text_layer_quality": quality, "text_layer_chars": len(text),
                                    "text_layer_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                    "text_layer": text, "source_sha256": row["sha256"],
                                    "visually_reviewed": False})
                result.update({"format": "pdf", "physical_page_count": len(reader.pages), "page_records": records})
                selected = choose_ocr_pages(records)
                missing = []
                for page in selected:
                    key = cache_key(row["sha256"], page, pipeline)
                    existing = reusable_payload(cache / (key + ".json"), row["sha256"], page, pipeline)
                    if existing is None:
                        existing = reusable_payload(prior_cache / (key + ".json"), row["sha256"], page, pipeline)
                        if existing is not None:
                            shutil.copy2(prior_cache / (key + ".json"), cache / (key + ".json"))
                    if existing is None: missing.append(page)
                result["ocr_pages_selected"] = selected
                result["ocr_pages_newly_scheduled"] = missing
                # Separate bounded jobs cover the full selection, including pages > 200.
                for offset in range(0, len(missing), 100):
                    batch = missing[offset:offset + 100]
                    identity = (row["sha256"], tuple(batch))
                    if not batch or identity in seen_jobs: continue
                    seen_jobs.add(identity)
                    job = output / "jobs" / f"{row['source_id']}_{offset:04d}.json"
                    write_json(job, {"source": str(path), "sha256": row["sha256"],
                                     "pages": batch, "cache_dir": str(cache), "pipeline_version": pipeline})
                    jobs.append(job)
            write_json(source_dir / "inventory.json", result)
        except Exception as exc:
            result["error"] = type(exc).__name__ + ": " + str(exc)[:300]
        documents.append(result)
    write_json(output / "inventory.json", {"documents": documents, "job_count": len(jobs),
                                           "new_page_count": sum(len(json.loads(p.read_text('utf-8'))['pages']) for p in jobs)})
    print(json.dumps({"stage": "inventory", "sources": len(documents), "jobs": len(jobs),
                      "new_pages": sum(len(json.loads(p.read_text('utf-8'))['pages']) for p in jobs)}, ensure_ascii=False), flush=True)
    worker_results = []
    if not args.skip_ocr:
        with ThreadPoolExecutor(max_workers=max(1, min(3, args.workers))) as executor:
            futures = {executor.submit(execute_job, path, max(30, min(900, args.timeout))): path for path in jobs}
            for future in as_completed(futures):
                result = future.result(); worker_results.append(result)
                print(json.dumps({"stage": "ocr_job", "job": Path(result["job"]).name,
                                  "returncode": result.get("returncode"),
                                  "completed_pages": result.get("completed_lines")}, ensure_ascii=False), flush=True)
    for source in documents:
        if source.get("error") or source.get("format") != "pdf":
            write_json(output / source["source_id"] / "recovery.json", source)
            continue
        units = []; page_records = source["page_records"]
        for record in page_records:
            page = record["page"]; text = record.pop("text_layer")
            before = record["text_layer_quality"]
            if before == "usable":
                record.update({"method": "pdf_text_layer", "recovery_status": "usable_text_layer"})
                units.append(_unit(f"page:{page}", text, page=page, quality="usable", layout="pdf_text_layer", tables_require_review=True))
                continue
            key = cache_key(source["source_sha256"], page, pipeline)
            payload = reusable_payload(cache / (key + ".json"), source["source_sha256"], page, pipeline)
            eligible = ocr_eligible(payload)
            after_text = _clean(payload.get("text", "")) if payload else ""
            record.update({"method": "local_rapidocr", "recovery_status": "ocr_unreviewed" if eligible else "quarantined",
                           "ocr_text_chars": len(after_text), "ocr_confidence_mean": (payload or {}).get("confidence_mean"),
                           "ocr_confidence_min": (payload or {}).get("confidence_min"),
                           "low_confidence_lines": (payload or {}).get("low_confidence_lines"),
                           "cache_path": str(cache / (key + ".json")),
                           "remaining_reason": None if eligible else "空白/过稀/低置信或未完成；需看原页，不能自动计为已读正文"})
            (output / source["source_id"] / f"page_{page:04d}.ocr.txt").write_text(after_text, encoding="utf-8")
            if eligible:
                units.append(_unit(f"page:{page}", after_text, page=page, quality="ocr_unreviewed", method="local_rapidocr",
                                   layout="ocr_reading_order_not_table_structure", confidence_mean=payload["confidence_mean"],
                                   confidence_min=payload.get("confidence_min"), low_confidence_lines=payload.get("low_confidence_lines"),
                                   ocr_pipeline_version=pipeline, requires_human_review=True, tables_require_review=True, approved=False))
        counts = Counter(row["recovery_status"] for row in page_records)
        source["page_status_counts"] = dict(counts)
        source["newly_recovered_pages"] = [row["page"] for row in page_records
                                            if row["page"] in source["ocr_pages_newly_scheduled"] and row["recovery_status"] == "ocr_unreviewed"]
        quarantined = [{"page": row["page"], "reason": row.get("remaining_reason")} for row in page_records if row["recovery_status"] == "quarantined"]
        source["parsed"] = {"status": "parsed" if units else "needs_ocr", "units": units,
                            "metadata": {"format": "pdf", "page_count": len(page_records), "usable_pages": len(units),
                                         "quarantined_pages": quarantined, "requires_human_review": True,
                                         "complete_text_read": not quarantined,
                                         "all_physical_pages_attempted": True,
                                         "extraction_method": "text_layer+resumable_local_ocr",
                                         "ocr": {"pipeline_version": pipeline, "recovered_pages": counts.get("ocr_unreviewed", 0),
                                                 "requires_human_review": bool(counts.get("ocr_unreviewed")),
                                                 "attempted_pages": source["ocr_pages_selected"], "page_limit_removed_by_batches": True}},
                            "warnings": ["本次已按物理页补跑全部缺文页；OCR页仍未逐页人工核对，公式/数值/表格关系不可凭置信分数认定正确。"]}
        sample_pages = sorted({1, len(page_records), *source["newly_recovered_pages"][:2],
                               *[row["page"] for row in page_records if row["recovery_status"] == "quarantined"]})
        source["rendered_samples"] = []
        for page in sample_pages:
            try:
                sample = raster_sample(Path(source["raw_path"]), page,
                                       output / source["source_id"] / f"page_{page:04d}.png")
                sample["page"] = page; source["rendered_samples"].append(sample)
            except Exception as exc:
                source["rendered_samples"].append({"page": page, "error": type(exc).__name__})
        write_json(output / source["source_id"] / "recovery.json", source)
    current = [tuple(row) for row in connection.execute("SELECT source_id,sha256,updated_at,status FROM sources WHERE status='pending_review' AND category NOT IN ('模式结果','空间数据') ORDER BY category,filename")]
    connection.close()
    plan = {"generated_at": datetime.now(timezone.utc).isoformat(), "database": str(database),
            "source_catalogue_unchanged": frozen == current, "database_mode": "read_only",
            "approval_actions": 0, "external_model_calls": 0, "pipeline": pipeline,
            "source_count": len(documents), "physical_pdf_pages": sum(d.get("physical_page_count", 0) for d in documents),
            "newly_recovered_page_count": sum(len(d.get("newly_recovered_pages", [])) for d in documents),
            "page_status_counts": dict(sum((Counter(d.get("page_status_counts", {})) for d in documents), Counter())),
            "worker_results": worker_results, "documents": documents,
            "review_boundary": "恢复不等于批准。原件未改、权限未放宽。未目视核对的OCR页保留未审；正式版本由独立证据核定。"}
    write_json(output / "recovery_plan.json", plan)
    print(json.dumps({key: value for key, value in plan.items() if key not in {"documents", "worker_results"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
