"""Recovery must preserve identity and cannot silently re-approve OCR pages."""
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "tools/recover_pending_text_20261021.py"
SPEC = importlib.util.spec_from_file_location("pending_text_recovery", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_selection_has_no_thirty_or_two_hundred_page_truncation():
    pages = [{"page": number, "text_layer_quality": "empty"} for number in range(1, 451)]
    assert M.choose_ocr_pages(pages) == list(range(1, 451))


def test_selection_does_not_re_ocr_usable_text():
    pages = [{"page": 3, "text_layer_quality": "usable"}, {"page": 10, "text_layer_quality": "garbled"}]
    assert M.choose_ocr_pages(pages) == [10]


@pytest.mark.parametrize("field,value", [("source_sha256", "other"), ("page", 2), ("pipeline_version", "other")])
def test_cache_identity_checked_even_when_confidence_high(tmp_path, field, value):
    payload = {"source_sha256": "sha", "page": 1, "pipeline_version": "v", "confidence_mean": .999}
    payload[field] = value
    path = tmp_path / "page.json"; path.write_text(json.dumps(payload), encoding="utf-8")
    assert M.reusable_payload(path, "sha", 1, "v") is None


def test_sparse_text_is_not_accepted_merely_for_high_confidence():
    assert not M.ocr_eligible({"status": "ok", "text": "1", "confidence_mean": .999})


def test_low_confidence_and_error_pages_remain_quarantined():
    text = "这是完整的技术正文，用于验证本地识别结果不得自动越过质量边界和业务审核。" * 2
    assert not M.ocr_eligible({"status": "ok", "text": text, "confidence_mean": .77})
    assert not M.ocr_eligible({"status": "error", "text": text, "confidence_mean": .99})
    assert M.ocr_eligible({"status": "ok", "text": text, "confidence_mean": .90})


def test_source_hash_or_size_change_blocks_recovery(tmp_path):
    root = tmp_path / "raw"; root.mkdir(); path = root / "source.pdf"; path.write_bytes(b"original")
    row = {"raw_path": str(path), "bytes": len(b"original"), "sha256": M.file_sha(path)}
    assert M.source_identity(row, root) == path.resolve()
    path.write_bytes(b"altered!")
    with pytest.raises(ValueError, match="SHA256 or size"):
        M.source_identity(row, root)


def test_source_outside_raw_root_rejected_even_if_hash_matches(tmp_path):
    root = tmp_path / "raw"; root.mkdir(); path = tmp_path / "outside.pdf"; path.write_bytes(b"source")
    row = {"raw_path": str(path), "bytes": 6, "sha256": M.file_sha(path)}
    with pytest.raises(ValueError, match="under the registered raw root"):
        M.source_identity(row, root)


def test_tile_merge_only_deduplicates_identical_overlap():
    rows = [{"text": "发布一次", "confidence": .9, "bbox": [10, 100, 110, 120]},
            {"text": "发布一次", "confidence": .95, "bbox": [10, 101, 110, 121]},
            {"text": "发布一次", "confidence": .99, "bbox": [10, 300, 110, 320]},
            {"text": "发布两次", "confidence": .98, "bbox": [10, 100, 110, 120]}]
    merged = M.merge_tiled_lines(rows)
    assert len(merged) == 3
    assert len([r for r in merged if r['text'] == '发布一次']) == 2
