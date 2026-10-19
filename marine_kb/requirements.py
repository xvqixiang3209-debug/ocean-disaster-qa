"""Read-only catalogue and validation helpers for the month-one question ledger."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


QUESTION_STATES = {
    "pending_confirmation": "待业务核定",
    "answerable": "完整可答（待验收）",
    "partial": "部分可答",
    "awaiting_data": "待资料/接口",
    "out_of_scope": "范围外/转人工",
}

SCOPE_LABELS = {"professional": "近期专业流程（建议）", "shared": "通用基础能力（建议）",
                "public_later": "远期公众服务（建议）"}


def planning_scope(item: dict) -> str:
    role = str(item.get("role") or "")
    if "公众" in role:
        return "public_later"
    return "professional" if role else "shared"


def load_catalog(path: str | Path) -> dict:
    target = Path(path)
    if not target.is_file():
        return {"available": False, "question_count": 0, "categories": [], "items": [], "source_sha256": None}
    raw = target.read_bytes()
    catalog = json.loads(raw.decode("utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("items"), list):
        raise ValueError("需求台账格式无效")
    ids = [str(item.get("id", "")) for item in catalog["items"]]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("需求台账题目编号缺失或重复")
    catalog["available"] = True
    for item in catalog["items"]:
        item["planning_scope"] = planning_scope(item)
        item["planning_scope_label"] = SCOPE_LABELS[item["planning_scope"]]
    catalog["catalog_sha256"] = hashlib.sha256(raw).hexdigest()
    return catalog


def status_summary(items: list[dict]) -> dict:
    counts = {key: 0 for key in QUESTION_STATES}
    for item in items:
        key = item.get("status", "pending_confirmation")
        if key not in counts:
            key = "pending_confirmation"
        counts[key] += 1
    month_one = [item for item in items if item.get("first_acceptance_window") == "第1个月"]
    return {
        "question_count": len(items),
        "month_one_question_count": len(month_one),
        "status_counts": counts,
        "month_one_status_counts": {key: sum(item.get("status", "pending_confirmation") == key for item in month_one)
                                    for key in QUESTION_STATES},
        "complete_answerable_count": counts["answerable"],
        "passed_count": 0,
        "scope_counts": {key: sum(planning_scope(item) == key for item in items) for key in SCOPE_LABELS},
        "note": "需求状态不是问答正确率；未建立专家参考答案和独立验收记录前，验收通过数固定为0。",
    }
