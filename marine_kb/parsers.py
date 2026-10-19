"""Local, provenance-preserving parsers; never send source material to an LLM.

``parsed`` can mean structured-only: numerical workbooks and GeoJSON expose
metadata for a separate governed ingestion path, not textual RAG evidence.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from importlib.metadata import PackageNotFoundError, version

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_PAGES = 1500
MAX_UNITS = 25000
MAX_TEXT_CHARS = 10_000_000
MAX_ZIP_UNCOMPRESSED = 500 * 1024 * 1024
MAX_ZIP_ENTRIES = 20000
MAX_SHEETS = 100
MAX_COLUMNS = 2000
MAX_WORKBOOK_ROWS = 250000
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_WORD_SECONDS = 90
OCR_PIPELINE_VERSION = "marine-ocr-v2-ppocrv3-scale2-det1280"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".wps", ".pptx", ".xlsx", ".txt", ".md", ".csv", ".tsv", ".json", ".geojson"}


def _result(status="parsed", units=None, metadata=None, warnings=None):
    return {"units": units or [], "metadata": metadata or {}, "warnings": warnings or [], "status": status}


def _clean(value):
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def _unit(locator, text, kind="text", **metadata):
    return {"locator": locator, "text": _clean(text), "kind": kind, "metadata": metadata}


def _text_quality(text):
    compact = re.sub(r"\s", "", text)
    if not compact:
        return "empty"
    # Broken PDF character maps commonly yield /G00xx, (cid:42), replacement
    # glyphs or private-use characters. A small amount does not erase a page.
    bad = sum(len(m.group()) for m in re.finditer(r"/G[0-9A-Fa-f]{2,8}|\(cid:\d+\)|\ufffd|[\ue000-\uf8ff]", compact))
    if bad >= 8 and bad / len(compact) > 0.035:
        return "garbled"
    content = re.sub(r"(?:购买|下载|版权所有|仅供|水印|www\.|https?://)[^\n]{0,100}", "", text)
    if len(compact) < 30 or len(re.sub(r"\s", "", content)) < 20:
        return "sparse"
    return "usable"


def _check_ooxml(path):
    """Do not extract archives; reject pathological OOXML containers early."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES or sum(x.file_size for x in entries) > MAX_ZIP_UNCOMPRESSED:
            raise ValueError("OOXML exceeds decompression limits")
        for entry in entries:
            if entry.flag_bits & 1:
                raise ValueError("Encrypted OOXML is not supported")
            if entry.file_size > 20 * 1024 * 1024 and entry.file_size / max(1, entry.compress_size) > 500:
                raise ValueError("Suspicious OOXML compression ratio")
        return set(archive.namelist())


def _is_word_ooxml(path):
    """Identify the top-level container, not embedded ZIPs inside OLE files.

    zipfile.is_zipfile alone accepts old .doc/.wps files with embedded theme or
    drawing archives because ZIP permits a prefix. Their actual document body
    still lives in the OLE compound container and needs the Word converter.
    """
    with Path(path).open("rb") as stream:
        signature = stream.read(8)
    if not signature.startswith(b"PK\x03\x04"):
        return False
    if not zipfile.is_zipfile(path):
        return False
    return "word/document.xml" in _check_ooxml(path)


def _parse_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        return _result("unsupported", warnings=["PDF 已加密：未尝试破解或猜测密码。"])
    count = len(reader.pages)
    if count > MAX_PAGES:
        return _result("unsupported", metadata={"page_count": count}, warnings=[f"PDF 超过 {MAX_PAGES} 页解析上限。"])
    units, quarantined, warnings = [], [], []
    total_chars = 0
    for page_number, page in enumerate(reader.pages, 1):
        try:
            text = _clean(page.extract_text() or "")
        except Exception as exc:
            quarantined.append({"page": page_number, "reason": "extraction_error", "detail": str(exc)[:200]})
            continue
        quality = _text_quality(text)
        if quality in {"empty", "garbled"}:
            quarantined.append({"page": page_number, "reason": quality})
            continue
        if quality == "sparse":
            # Short cover/title pages are retained as contextual material but
            # explicitly not eligible as autonomous answer evidence.
            quarantined.append({"page": page_number, "reason": "sparse"})
            continue
        total_chars += len(text)
        if total_chars > MAX_TEXT_CHARS:
            warnings.append("达到单文件正文字符上限；后续页未解析，不能宣称已读取全文。")
            break
        units.append(_unit(f"page:{page_number}", text, page=page_number, quality=quality, layout="pdf_text_layer", tables_require_review=True))
    ocr_info = {"enabled": _env_bool("MARINE_LOCAL_OCR_ENABLED"), "recovered_pages": 0, "requires_human_review": False}
    if quarantined and ocr_info["enabled"]:
        recovered, ocr_info, ocr_warnings = _recover_pdf_ocr(path, quarantined)
        warnings.extend(ocr_warnings)
        recovered_pages = {unit["metadata"]["page"] for unit in recovered}
        quarantined = [item for item in quarantined if item["page"] not in recovered_pages]
        units.extend(recovered)
        units.sort(key=lambda unit: unit["metadata"]["page"])
    if quarantined:
        warnings.append(f"{len(quarantined)} 页无可用文本、文本稀疏或乱码，已隔离等待 OCR/人工复核，未作为正文证据。")
    return _result("parsed" if units else "needs_ocr", units, {"format": "pdf", "page_count": count, "usable_pages": len(units), "quarantined_pages": quarantined, "ocr": ocr_info, "requires_human_review": ocr_info.get("requires_human_review", False), "extraction_method": "text_layer+local_ocr" if ocr_info.get("recovered_pages") else "text_layer", "complete_text_read": not quarantined and not warnings}, warnings)


def _env_bool(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (ValueError, TypeError):
        value = default
    return max(minimum, min(maximum, value))


def _ocr_version():
    try:
        return OCR_PIPELINE_VERSION + "-rapid" + version("rapidocr_onnxruntime") + "-pdfium" + version("pypdfium2")
    except PackageNotFoundError:
        return None


def _recover_pdf_ocr(path, quarantined):
    """Only repair failed text-layer pages, locally, under a hard subprocess limit."""
    info = {"enabled": True, "recovered_pages": 0, "requires_human_review": False, "attempted_pages": [], "failed_pages": [], "timed_out": False, "pipeline_version": _ocr_version()}
    warnings = []
    if info["pipeline_version"] is None:
        warnings.append("已启用本地 OCR，但缺少 rapidocr_onnxruntime / pypdfium2；原页仍隔离，不调用云端 OCR。")
        return [], info, warnings
    max_pages = _bounded_env_int("MARINE_LOCAL_OCR_MAX_PAGES", 30, 1, 200)
    timeout = _bounded_env_int("MARINE_LOCAL_OCR_TIMEOUT", 120, 10, 300)
    candidates = [item["page"] for item in quarantined if item["reason"] in {"empty", "garbled", "sparse", "extraction_error"}]
    selected = candidates[:max_pages]
    info["attempted_pages"] = selected
    info["page_limit"] = max_pages
    info["timeout_seconds"] = timeout
    if len(candidates) > max_pages:
        warnings.append(f"本文件有 {len(candidates)} 页需 OCR，本次仅处理前 {max_pages} 页，剩余页继续隔离。")
    if not selected:
        return [], info, warnings
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_dir = Path(os.environ.get("MARINE_LOCAL_OCR_CACHE", str(Path(__file__).resolve().parent.parent / "data" / "ocr_cache"))).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    pipeline = info["pipeline_version"]
    def cache_path(page):
        key = hashlib.sha256(f"{digest}:{page}:{pipeline}".encode()).hexdigest()
        return cache_dir / (key + ".json")
    missing = [page for page in selected if not cache_path(page).is_file()]
    if missing:
        with tempfile.TemporaryDirectory(prefix="marine-local-ocr-") as temporary:
            manifest = Path(temporary) / "job.json"
            manifest.write_text(json.dumps({"source": str(path.resolve()), "sha256": digest, "pages": missing, "cache_dir": str(cache_dir), "pipeline_version": pipeline}, ensure_ascii=False), encoding="utf-8")
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--local-ocr-worker", str(manifest)], capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    warnings.append("本地 OCR 进程未完整完成：" + (result.stderr or result.stdout).decode("utf-8", errors="replace")[-300:])
            except subprocess.TimeoutExpired:
                info["timed_out"] = True
                warnings.append(f"本地 OCR 达到 {timeout} 秒上限，已停止专用子进程；保留已完成页，其余继续隔离。")
    recovered = []
    for page in selected:
        try:
            payload = json.loads(cache_path(page).read_text(encoding="utf-8"))
            if payload.get("source_sha256") != digest or payload.get("page") != page or payload.get("pipeline_version") != pipeline:
                raise ValueError("cache identity mismatch")
            text = _clean(payload.get("text", ""))
            confidence = float(payload.get("confidence_mean", 0))
            if payload.get("status") != "ok" or _text_quality(text) != "usable" or confidence < 0.78:
                raise ValueError(payload.get("error") or "OCR text quality or confidence insufficient")
            recovered.append(_unit(f"page:{page}", text, page=page, quality="ocr_unreviewed", method="local_rapidocr", layout="ocr_reading_order_not_table_structure", confidence_mean=confidence, confidence_min=payload.get("confidence_min"), low_confidence_lines=payload.get("low_confidence_lines"), line_count=payload.get("line_count"), ocr_pipeline_version=pipeline, requires_human_review=True, tables_require_review=True, approved=False))
        except Exception as exc:
            info["failed_pages"].append({"page": page, "reason": str(exc)[:180]})
    info["recovered_pages"] = len(recovered)
    info["requires_human_review"] = bool(recovered)
    if recovered:
        warnings.append(f"{len(recovered)} 页由纯本地 OCR 恢复，标记为未复核；数字、小数点、≥/≤、单位及表格列关系须对照原页，置信分数不等于事实正确率，不自动批准标准条款。")
    return recovered, info, warnings


def _local_ocr_worker(manifest_path):
    """Dedicated process; importing this module never loads OCR models."""
    import numpy as np
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR
    import rapidocr_onnxruntime.utils as rapid_utils

    job = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    source = Path(job["source"]).resolve()
    cache_dir = Path(job["cache_dir"]).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != job["sha256"] or not cache_dir.is_dir() or job["pipeline_version"] != _ocr_version():
        raise ValueError("OCR source/cache/version validation failed")
    # This released package exposes SessionOptions in its own utils module but
    # has no thread-count config. Bound the factory locally in this worker so
    # three ONNX sessions do not each consume every machine CPU.
    original_options = rapid_utils.SessionOptions
    def bounded_options():
        options = original_options()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        return options
    rapid_utils.SessionOptions = bounded_options
    engine = RapidOCR(det_model_path=None, det_limit_side_len=1280, det_limit_type="max")
    document = pdfium.PdfDocument(str(source))
    try:
        for page_number in job["pages"][:200]:
            if not isinstance(page_number, int) or page_number < 1 or page_number > len(document):
                raise ValueError("Invalid physical page number")
            key = hashlib.sha256(f"{digest}:{page_number}:{job['pipeline_version']}".encode()).hexdigest()
            destination = cache_dir / (key + ".json")
            if destination.parent != cache_dir:
                raise ValueError("Cache path escaped")
            payload = {"source_sha256": digest, "page": page_number, "pipeline_version": job["pipeline_version"], "method": "local_rapidocr", "requires_human_review": True, "approved": False}
            page = bitmap = picture = None
            try:
                page = document[page_number - 1]
                width, height = page.get_size()
                # 144 DPI normally; cap long side and raster pixels for giant
                # posters rather than allocating an unbounded bitmap.
                scale = min(2.0, 2600 / max(width, height), (5_000_000 / max(width * height, 1)) ** 0.5)
                bitmap = page.render(scale=scale, may_draw_forms=False, draw_annots=False)
                picture = bitmap.to_pil().convert("RGB")
                results, elapsed = engine(np.asarray(picture))
                lines = [{"box": row[0], "text": str(row[1]), "confidence": float(row[2])} for row in (results or [])][:1500]
                text = "\n".join(row["text"] for row in lines)
                scores = [row["confidence"] for row in lines]
                payload.update({"status": "ok" if lines else "empty", "text": text, "line_count": len(lines), "confidence_mean": sum(scores) / len(scores) if scores else 0, "confidence_min": min(scores) if scores else 0, "low_confidence_lines": sum(value < .80 for value in scores), "render_scale": scale, "lines": lines, "stage_seconds": elapsed})
            except Exception as exc:
                payload.update({"status": "error", "error": str(exc)[:300], "text": ""})
            finally:
                if picture is not None:
                    picture.close()
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
            # Only generated cache entries are replaced; never edit sources.
            temporary = cache_dir / (key + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(destination)
            print(json.dumps({"page": page_number, "status": payload["status"]}), flush=True)
    finally:
        document.close()


def _parse_docx(path):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    names = _check_ooxml(path)
    if "word/document.xml" not in names:
        return _result("unsupported", warnings=["文件并非有效的 Word OOXML 文档。"])
    document = Document(str(path))
    units, warnings = [], []
    paragraph_number = table_number = 0
    current_heading = ""
    # Iterate body XML, rather than paragraphs then tables, to preserve order.
    for node in document.element.body.iterchildren():
        if node.tag.endswith("}p"):
            paragraph_number += 1
            paragraph = Paragraph(node, document)
            text = _clean(paragraph.text)
            if not text:
                continue
            style = paragraph.style.name if paragraph.style else ""
            if style.lower().startswith("heading") or style.startswith("标题"):
                current_heading = text
            units.append(_unit(f"paragraph:{paragraph_number}", text, paragraph=paragraph_number, heading=current_heading, style=style))
        elif node.tag.endswith("}tbl"):
            table_number += 1
            table = Table(node, document)
            rows = [[_clean(cell.text) for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            header = rows[0]
            if len(rows) == 1:
                units.append(_unit(f"table:{table_number}/rows:1-1", " | ".join(header), "table", table=table_number, row_start=1, row_end=1, header=header, heading=current_heading, merged_cells_may_repeat=True))
                continue
            # Group complete rows; repeat headers in each table segment.
            for offset in range(1, max(2, len(rows)), 20):
                end = min(offset + 20, len(rows))
                group = [header] + rows[offset:end]
                text = "\n".join(" | ".join(cell.replace("\n", " / ") for cell in row) for row in group)
                units.append(_unit(f"table:{table_number}/rows:{offset + 1}-{max(offset + 1, end)}", text, "table", table=table_number, row_start=offset + 1, row_end=end, header=header, heading=current_heading, merged_cells_may_repeat=True))
    images = sum(1 for name in names if name.startswith("word/media/") and not name.endswith("/"))
    if images:
        warnings.append(f"含 {images} 个嵌入媒体，图片内容尚未 OCR，表格版式和图示需人工复核。")
    return _result("parsed" if units else "empty", units, {"format": "docx", "paragraph_count": paragraph_number, "table_count": table_number, "embedded_media_count": images, "page_numbers_available": False}, warnings)


def _parse_pptx(path):
    from pptx import Presentation
    names = _check_ooxml(path)
    presentation = Presentation(str(path))
    if len(presentation.slides) > MAX_PAGES:
        return _result("unsupported", warnings=["PPTX 超过页数上限。"])
    units, warnings = [], []
    image_count = 0
    for page, slide in enumerate(presentation.slides, 1):
        texts, tables = [], []
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
            if shape.has_table:
                tables.append("\n".join(" | ".join(_clean(c.text) for c in row.cells) for row in shape.table.rows))
            if shape.shape_type == 13:
                image_count += 1
        text = _clean("\n".join(texts))
        if text:
            units.append(_unit(f"slide:{page}", text, slide=page))
        for index, table in enumerate(tables, 1):
            units.append(_unit(f"slide:{page}/table:{index}", table, "table", slide=page, table=index))
    if image_count:
        warnings.append(f"含 {image_count} 个图片形状，图片未 OCR；未提取讲者备注。")
    return _result("parsed" if units else "needs_ocr", units, {"format": "pptx", "slide_count": len(presentation.slides), "image_count": image_count}, warnings)


def _serial(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _parse_xlsx(path):
    from openpyxl import load_workbook
    _check_ooxml(path)
    book = load_workbook(str(path), read_only=True, data_only=True, keep_links=False)
    sheets, warnings = [], []
    try:
        if len(book.worksheets) > MAX_SHEETS:
            return _result("unsupported", warnings=["工作簿工作表数量超过解析上限。"])
        for sheet in book.worksheets:
            if (sheet.max_column or 0) > MAX_COLUMNS or (sheet.max_row or 0) > MAX_WORKBOOK_ROWS:
                warnings.append(f"工作表 {sheet.title} 尺寸超过解析上限，未读取。")
                continue
            rows = sheet.iter_rows(values_only=True)
            first = next(rows, ())
            headers = [_serial(value) for value in first]
            row_count = nonempty = 0
            first_time = last_time = None
            time_samples = []
            for row in rows:
                row_count += 1
                if not any(x is not None for x in row):
                    continue
                nonempty += 1
                value = _serial(row[0]) if row else None
                if first_time is None:
                    first_time = value
                last_time = value
                if len(time_samples) < 3:
                    time_samples.append(value)
            short_time = any(isinstance(x, str) and re.match(r"^\d{1,2}/\d{1,2}:\d{2}", x) for x in time_samples)
            if short_time:
                warnings.append(f"工作表 {sheet.title} 时间值缺少完整年月及明确时区；不得仅凭文件名自动补齐。")
            sheets.append({"name": sheet.title, "declared_rows": sheet.max_row, "declared_columns": sheet.max_column, "data_row_count": row_count, "nonempty_data_row_count": nonempty, "headers": headers, "first_time": first_time, "last_time": last_time, "time_samples": time_samples, "incomplete_time": short_time, "unit": None, "vertical_datum": None, "ensemble_statistic": None})
    finally:
        book.close()
    warnings.append("数值工作簿只接收结构摘要，未切为正文证据；单位、高程基准、起报/有效时间及集合统计定义待确认，不按数值大小猜测。")
    return _result("parsed" if sheets else "empty", metadata={"format": "xlsx", "ingest_mode": "structured_only", "sheets": sheets, "sheet_count": len(sheets), "unit_confirmed": False, "time_semantics_confirmed": False}, warnings=warnings)


def _decode(path):
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("Unsupported text encoding; refusing replacement-character decoding")


def _parse_text(path):
    text, encoding = _decode(path)
    units = []
    # Preserve line-based provenance while grouping adjacent paragraphs.
    pending, start = [], 1
    for line_number, line in enumerate(text.splitlines(), 1):
        if not pending:
            start = line_number
        pending.append(line)
        if len("\n".join(pending)) >= 4000:
            units.append(_unit(f"lines:{start}-{line_number}", "\n".join(pending), line_start=start, line_end=line_number))
            pending = []
    if pending and _clean("\n".join(pending)):
        units.append(_unit(f"lines:{start}-{len(text.splitlines())}", "\n".join(pending), line_start=start, line_end=len(text.splitlines())))
    return _result("parsed" if units else "empty", units, {"format": path.suffix.lstrip("."), "encoding": encoding})


def _parse_csv(path):
    text, encoding = _decode(path)
    reader = csv.reader(io.StringIO(text), delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
    header = next(reader, [])
    units, group = [], []
    start, count = 2, 0
    for row_number, row in enumerate(reader, 2):
        count += 1
        group.append(row)
        if len(group) >= 20:
            units.append(_unit(f"rows:{start}-{row_number}", "\n".join(" | ".join(row) for row in [header] + group), "table", header=header, row_start=start, row_end=row_number))
            group, start = [], row_number + 1
    if group:
        units.append(_unit(f"rows:{start}-{count + 1}", "\n".join(" | ".join(row) for row in [header] + group), "table", header=header, row_start=start, row_end=count + 1))
    return _result("parsed" if units else "empty", units, {"format": "csv", "encoding": encoding, "header": header, "data_row_count": count}, ["CSV 保留原始单元格，未推断单位、缺失值含义或表头定义。"])


def _geometry_points(geometry):
    if not isinstance(geometry, dict):
        return
    if geometry.get("type") == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _geometry_points(child)
        return
    stack = [geometry.get("coordinates", [])]
    while stack:
        item = stack.pop()
        if isinstance(item, list) and len(item) >= 2 and all(isinstance(x, (int, float)) for x in item[:2]):
            yield item[:2]
        elif isinstance(item, list):
            stack.extend(item)


def _parse_json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        return _result("unsupported", warnings=["JSON 超过 32 MiB 内存解析上限。"])
    text, encoding = _decode(path)
    data = json.loads(text)
    if isinstance(data, dict) and data.get("type") in {"FeatureCollection", "Feature"}:
        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
        if not isinstance(features, list):
            raise ValueError("GeoJSON features must be a list")
        feature_metadata, types, property_names, points = [], Counter(), set(), []
        warnings = ["GeoJSON 仅进入空间资料待审区；不推断坐标系、高程基准、四色字段单位或阈值适用期。"]
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                warnings.append(f"第 {index + 1} 个要素类型异常，未解析。")
                continue
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            types[geometry.get("type", "missing")] += 1
            property_names.update(props)
            vertices = list(_geometry_points(geometry))
            points.extend(vertices)
            feature_metadata.append({"index": index, "id": feature.get("id"), "properties": props, "geometry_type": geometry.get("type"), "vertex_count": len(vertices)})
        bbox = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)] if points else None
        return _result(metadata={"format": "geojson", "ingest_mode": "structured_only", "feature_count": len(features), "geometry_types": dict(types), "properties": sorted(property_names), "features": feature_metadata, "bbox": bbox, "crs_declared": data.get("crs"), "threshold_unit_confirmed": False}, warnings=warnings)
    units = []
    if isinstance(data, list):
        for index, item in enumerate(data):
            units.append(_unit(f"json:/{index}", json.dumps(item, ensure_ascii=False, indent=2), json_pointer=f"/{index}"))
    elif isinstance(data, dict):
        for key, item in data.items():
            pointer = "/" + str(key).replace("~", "~0").replace("/", "~1")
            units.append(_unit(f"json:{pointer}", json.dumps({key: item}, ensure_ascii=False, indent=2), json_pointer=pointer))
    else:
        units.append(_unit("json:/", json.dumps(data, ensure_ascii=False)))
    return _result("parsed" if units else "empty", units, {"format": "json", "encoding": encoding})


def _parse_legacy_word(path):
    cache_root = os.environ.get("MARINE_KB_WORD_CACHE")
    if cache_root:
        cache_file = Path(cache_root) / (hashlib.sha256(path.read_bytes()).hexdigest() + ".docx")
        if cache_file.is_file() and not cache_file.is_symlink():
            parsed = _parse_docx(cache_file)
            parsed["metadata"].update({"original_format": path.suffix.lstrip("."), "conversion": "local_word_sha256_content_cache"})
            parsed["warnings"].append("旧 Office 文档使用本次本地 Word 转换缓存（按源文件 SHA256 关联）；定位为转换后段落/表格。")
            return parsed
    if os.name != "nt" or os.environ.get("MARINE_KB_DISABLE_WORD_COM") == "1":
        return _result("unsupported", warnings=["旧 DOC/WPS 需要 Windows 本地 Word；当前环境未启用安全转换。"])
    shell = shutil.which("powershell.exe")
    converter = Path(__file__).with_name("convert_office.ps1")
    if not shell or not converter.is_file():
        return _result("unsupported", warnings=["未找到本地 Word 转换脚本或 PowerShell。"])
    timeout = max(10, min(300, int(os.environ.get("MARINE_KB_WORD_TIMEOUT", MAX_WORD_SECONDS))))
    with tempfile.TemporaryDirectory(prefix="marine-kb-word-") as directory:
        output = Path(directory) / "converted.docx"
        owner = Path(directory) / "owned-word.json"
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(converter), "-InputPath", str(path.resolve()), "-OutputPath", str(output), "-OwnerPath", str(owner)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout, creationflags=flags)
        except subprocess.TimeoutExpired:
            # Cleanup verifies both PID and creation ticks of this dedicated
            # instance. Never terminate all WINWORD processes or a user's Word.
            return _result("unsupported", warnings=[f"本地 Word 转换超过 {timeout} 秒，已停止本次转换；未提取正文。"])
        finally:
            _cleanup_word(shell, converter, owner)
        if result.returncode or not output.is_file():
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace")[-600:]
            return _result("unsupported", warnings=["本地 Word 安全转换未成功（可能未安装 Word、格式损坏或受保护）。", detail])
        parsed = _parse_docx(output)
        parsed["metadata"].update({"original_format": path.suffix.lstrip("."), "conversion": "local_word_read_only_macros_disabled_links_disabled"})
        parsed["warnings"].append("旧 Office 文档通过本地 Word 只读转换；定位为转换后段落/表格，不冒充原始页码。")
        return parsed


def _cleanup_word(shell, converter, owner):
    if owner.is_file():
        try:
            subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(converter), "-CleanupOwner", str(owner)], capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass


def prepare_office_cache(paths, cache_dir, batch_size=30, progress=None):
    """Convert actual legacy sources in small local Word batches.

    Set MARINE_KB_WORD_CACHE to this directory before calling parse_file.
    Existing successful SHA256 outputs are reused. Failures never masquerade as
    parsed documents; one timeout affects at most one batch, whose completed
    outputs are kept. Returns counts/errors, not extracted or sent model text.
    """
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    shell = shutil.which("powershell.exe") if os.name == "nt" else None
    converter = Path(__file__).with_name("convert_office.ps1")
    stats = {"converted": 0, "cached": 0, "errors": [], "batches": 0, "files": []}
    if not shell or os.environ.get("MARINE_KB_DISABLE_WORD_COM") == "1":
        stats["errors"].append({"reason": "local_word_unavailable_or_disabled"})
        return stats
    pending, seen = [], set()
    for raw in paths:
        path = Path(raw).resolve()
        if path.suffix.lower() not in {".doc", ".wps"} or not path.is_file() or path.name.startswith("~$") or not 0 < path.stat().st_size <= MAX_FILE_BYTES or _is_word_ooxml(path):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        cached = cache_dir / (digest + ".docx")
        if cached.is_file():
            stats["cached"] += 1
            stats["files"].append({"path": str(path), "sha256": digest, "status": "cached"})
        else:
            pending.append((path, digest))
    batch_size = max(1, min(50, int(batch_size)))
    for start in range(0, len(pending), batch_size):
        group = pending[start:start + batch_size]
        stats["batches"] += 1
        with tempfile.TemporaryDirectory(prefix="marine-kb-word-batch-") as directory:
            folder = Path(directory)
            owner = folder / "owned-word.json"
            manifest = folder / "manifest.json"
            manifest.write_text(json.dumps([{"input": str(path), "output": str(folder / (digest + ".docx"))} for path, digest in group], ensure_ascii=False), encoding="utf-8")
            timeout = min(300, max(90, len(group) * 8 + 30))
            timed_out = False
            batch_detail = ""
            try:
                completed = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(converter), "-BatchManifest", str(manifest), "-OwnerPath", str(owner)], capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                batch_detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace")[-1200:]
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _cleanup_word(shell, converter, owner)
            for path, digest in group:
                converted = folder / (digest + ".docx")
                try:
                    if not converted.is_file() or "word/document.xml" not in _check_ooxml(converted):
                        raise ValueError("batch_timeout" if timed_out else "conversion_failed")
                    destination = cache_dir / converted.name
                    if destination.parent != cache_dir or destination.exists():
                        raise ValueError("unexpected_cache_destination")
                    shutil.copyfile(converted, destination)
                    stats["converted"] += 1
                    stats["files"].append({"path": str(path), "sha256": digest, "status": "converted"})
                except Exception as exc:
                    stats["errors"].append({"path": str(path), "reason": str(exc)[:200], "detail": batch_detail})
                    stats["files"].append({"path": str(path), "sha256": digest, "status": "error", "reason": str(exc)[:200]})
            if progress:
                progress(dict(stats))
    return stats


def parse_file(path: Path) -> dict:
    """Parse a local source without modifying it; return JSON-serializable data."""
    path = Path(path)
    try:
        if not path.is_file():
            return _result("error", warnings=["文件不存在或不是普通文件。"])
        if path.name.startswith("~$"):
            return _result("unsupported", warnings=["Office 临时锁定文件不作为知识资料。"])
        size = path.stat().st_size
        if size == 0:
            return _result("empty", metadata={"size_bytes": 0}, warnings=["空文件，未生成证据。"])
        if size > MAX_FILE_BYTES:
            return _result("unsupported", metadata={"size_bytes": size}, warnings=[f"超过 {MAX_FILE_BYTES // 1024 // 1024} MiB 单文件解析上限。"])
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            return _result("unsupported", warnings=[f"尚未实现 {suffix or '无扩展名'} 格式解析。"])
        if suffix in {".doc", ".wps"} and _is_word_ooxml(path):
            parsed = _parse_docx(path)
            parsed["metadata"]["extension_mismatch"] = True
            parsed["warnings"].append("扩展名为旧 Word，但文件内容为 OOXML，按真实格式解析。")
        else:
            dispatch = {".pdf": _parse_pdf, ".docx": _parse_docx, ".doc": _parse_legacy_word, ".wps": _parse_legacy_word, ".pptx": _parse_pptx, ".xlsx": _parse_xlsx, ".json": _parse_json, ".geojson": _parse_json, ".csv": _parse_csv, ".tsv": _parse_csv, ".txt": _parse_text, ".md": _parse_text}
            parsed = dispatch[suffix](path)
        # Enforce a clear bounded result. Truncation must never be silent.
        bounded, chars = [], 0
        for unit in parsed["units"]:
            if len(bounded) >= MAX_UNITS or chars + len(unit["text"]) > MAX_TEXT_CHARS:
                parsed["warnings"].append("达到单文件证据数量/字符上限，存在未入库内容。")
                parsed["metadata"]["complete_text_read"] = False
                break
            if unit["text"]:
                bounded.append(unit)
                chars += len(unit["text"])
        parsed["units"] = bounded
        parsed["metadata"].update({"size_bytes": size, "source_extension": suffix, "extracted_characters": chars})
        return parsed
    except (ImportError, ModuleNotFoundError) as exc:
        return _result("unsupported", warnings=[f"缺少本地解析依赖：{exc}"])
    except Exception as exc:
        return _result("error", warnings=[f"解析失败：{type(exc).__name__}: {str(exc)[:400]}"])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--local-ocr-worker":
        _local_ocr_worker(sys.argv[2])
    else:
        raise SystemExit("This parser module accepts only an internal local OCR job.")
