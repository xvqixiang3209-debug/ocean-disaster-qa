"""Repeatable read-only corpus regression, never calls DeepSeek or web search."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marine_kb.qa import MarineQuestionService
from marine_kb.semantic import LocalSemanticIndex
from marine_kb.storage import KnowledgeStore
from typhoon.rag import RagKnowledgeBase


class ReadOnlyStore(KnowledgeStore):
    def __init__(self, path):
        self.database = Path(path).resolve()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()


class ReadOnlyLegacy(RagKnowledgeBase):
    def __init__(self, path):
        self.database_path = Path(path).resolve()

    def _connect(self):
        db = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True, timeout=60)
        db.row_factory = sqlite3.Row
        return db


def run():
    store = ReadOnlyStore(ROOT / "data/marine_kb/knowledge.sqlite3")
    store.semantic_index = LocalSemanticIndex(store.database, ROOT / "data/marine_kb/semantic")
    legacy = ReadOnlyLegacy(ROOT / "typhoon/data/typhoon_rag.sqlite3")
    qa = MarineQuestionService(store, legacy_rag=legacy)
    cases = [
        ("matsa_damage", "2005年麦莎台风灾情损失如何", "2005211N09141", "historical"),
        ("matsa_response", "2005年麦莎台风浙江有哪些应对措施", "2005211N09141", "historical"),
        ("lekima_damage", "2019年利奇马台风灾情损失如何", "2019214N15134", "historical"),
        ("lekima_response", "2019年利奇马台风有哪些应对措施", "2019214N15134", "historical"),
        ("lekima_surge_scope", "2019年利奇马台风最大增水是多少", None, "no_mitag_contamination"),
        ("wave_24h_rule", "海浪警报标准中的24小时是什么意思", None, "unreviewed_rule_refusal"),
        ("usagi_2013_bulletin", "2013年浙江省天兔台风造成哪些损失", None, "historical_or_honest_gap"),
        ("fitow_2013_bulletin", "2013年浙江省菲特台风造成哪些损失", None, "historical_or_honest_gap"),
        ("bulletin_2013", "2013年浙江省海洋灾害公报记录了哪些灾害损失", None, "historical_or_honest_gap"),
        ("current_landfall", "今年浙江登陆几个台风", None, "current_refusal"),
        ("typo_selected", "台风强度汝河", "2005211N09141", "historical"),
        ("red_tide", "赤潮灾害有什么影响", None, "historical_or_honest_gap"),
    ]
    records = []
    for case_id, question, sid, expectation in cases:
        result = qa.answer(question, sid=sid)
        answer = result["answer"]
        checks = {
            "no_external_calls": result["answer_meta"].get("model") is None and result["web_sources"] == [],
            "source_citations_present": not result["rag_sources"] or "[资料1]" in answer,
            "historical_labeled": not result["rag_sources"] or "历史资料" in answer,
        }
        if expectation == "current_refusal":
            checks["does_not_use_archival_for_current"] = not result["rag_sources"] and result["answer_meta"]["used_mode"] == "no_evidence"
        elif expectation == "unreviewed_rule_refusal":
            checks["no_pending_standard_as_authority"] = not result["rag_sources"]
        elif expectation == "no_mitag_contamination":
            checks["no_mitag_1_52"] = "1.52" not in answer and "米娜" not in answer
            checks["preserves_two_sources"] = "3.12" in answer and "3.11" in answer
        elif expectation == "historical":
            checks["historical_evidence_found"] = bool(result["rag_sources"])
        if case_id == "usagi_2013_bulletin":
            checks["no_annual_total_assigned_to_usagi"] = "1281636" not in answer and "281636" not in answer
        if case_id == "fitow_2013_bulletin":
            checks["individual_loss_found"] = "233759" in answer
            checks["no_trami_or_annual_loss"] = all(value not in answer for value in ("44118", "2789", "282267", "1281636"))
        if case_id == "bulletin_2013":
            checks["correct_bulletin_year"] = all("2013" in source["filename"] for source in result["rag_sources"])
        records.append({"case_id": case_id, "question": question, "sid": sid, "expected_boundary": expectation, "checks": checks,
                        "automated_checks_passed": all(checks.values()), "answer": answer,
                        "sources": result["rag_sources"], "answer_meta": result["answer_meta"]})
    selected_qa = MarineQuestionService(store)
    selected_checks = []
    for label, question, sid, needle in (
        ("利奇马新增公报应对", "2019年LEKIMA台风 浙江有哪些应对措施", "2019214N15134", "Ⅰ级"),
        ("菲特新增公报损失", "2013年FITOW台风 造成哪些损失", "2013272N10135", "233759"),
    ):
        result = selected_qa.answer(question, sid=sid)
        selected_checks.append({"case": label, "question": question, "sid": sid, "passed": bool(result["rag_sources"]) and needle in result["answer"],
                                "answer": result["answer"], "sources": result["rag_sources"], "mode": result["answer_meta"]["used_mode"]})
    output = {"generated_at": datetime.now().astimezone().isoformat(), "scope": "当前新库和旧RAG只读回归，不调用外部模型/搜索，不等于专家答案验收或检索召回率评估",
              "records": records, "case_count": len(records), "automated_pass_count": sum(row["automated_checks_passed"] for row in records),
              "selected_new_library_checks": selected_checks,
              "refused_cases": [row["case_id"] for row in records if row["answer_meta"]["used_mode"] == "no_evidence"],
              "known_limits": ["历史摘录可能不构成完整答案，全年统计需结构化统计口径与完整来源", "海浪标准待审，不将24小时时效误写为发布频次", "旧PDF图表乱码未OCR，未提供的数据不补造", "模型摘要未经真实付费API验证，默认外发关闭"]}
    target = ROOT / "docs/首期问答回归.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "cases": len(records), "passed": output["automated_pass_count"],
                      "results": [{"id": row["case_id"], "sources": len(row["sources"]), "checks": row["checks"]} for row in records]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
