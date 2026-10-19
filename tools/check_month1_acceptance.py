"""Read-only month-one evidence/citation checks, not expert answer scoring.

The fixed 20-question development candidates are mapped to the 160-question
ledger. SQLite connections enforce mode=ro/query_only; no service startup,
ingestion, index rebuild, web request or model request is performed. The only
outputs are this script's JSON/Markdown reports under docs/ by default.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

APP = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(APP))

from marine_kb.qa import MarineQuestionService
from marine_kb.retrieval import usable_source
from marine_kb.storage import KnowledgeStore
from typhoon.rag import RagKnowledgeBase


class ReadOnlyStore(KnowledgeStore):
    def __init__(self, database: Path):
        # Deliberately do not call KnowledgeStore.__init__: it creates tables.
        self.database = database.resolve(strict=True)
        self.semantic_index = None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        try:
            yield db
        finally:
            db.close()


class ReadOnlyLegacy(RagKnowledgeBase):
    def __init__(self, database: Path):
        self.database_path = database.resolve(strict=True)

    def _connect(self):
        db = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db


class ForbiddenExternal:
    """Fail if a later code change accidentally attempts paid or web work."""
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("This acceptance check must not call external models")

    def answer(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("This acceptance check must not search the web")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def db_inventory(store: ReadOnlyStore) -> dict:
    with store.connect() as db:
        states = [dict(row) for row in db.execute(
            "SELECT status,COUNT(*) sources,SUM(chunk_count) chunks FROM sources GROUP BY status ORDER BY status")]
        totals = {"sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                  "chunks": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                  "review_actions": db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]}
    return {**totals, "status_counts": states}


def source_resolves(source: dict, store: ReadOnlyStore, legacy: ReadOnlyLegacy | None) -> bool:
    sid, chunk_id = str(source.get("source_id", "")), str(source.get("chunk_id", ""))
    if sid.startswith("legacy:"):
        if legacy is None or not chunk_id.startswith("legacy:"):
            return False
        with closing(legacy._connect()) as db:
            row = db.execute("SELECT 1 FROM knowledge_chunks WHERE document_id=? AND chunk_id=?",
                             (sid[7:], chunk_id[7:])).fetchone()
    else:
        with store.connect() as db:
            row = db.execute("SELECT 1 FROM chunks WHERE source_id=? AND chunk_id=?", (sid, chunk_id)).fetchone()
    return row is not None


def automatic_checks(case: dict, result: dict, store: ReadOnlyStore,
                     legacy: ReadOnlyLegacy | None, external: ForbiddenExternal) -> dict:
    sources = result.get("rag_sources") or []
    answer = str(result.get("answer") or "")
    meta = result.get("answer_meta") or {}
    refs = {int(value) for value in re.findall(r"\[资料(\d+)\]", answer)}
    checks = {
        "no_model_or_web_calls": external.calls == 0 and meta.get("model") is None and not result.get("web_sources"),
        "nonempty_response": bool(answer.strip()),
    }
    if case.get("expected") == "no_evidence":
        checks.update({"no_archival_answer": not sources and meta.get("used_mode") == "no_evidence",
                       "gap_is_explicit": bool(re.search(r"没有|不足|未启用|未接入|不能替代|未提供", answer))})
    else:
        checks.update({
            "evidence_returned": bool(sources),
            "local_evidence_route": meta.get("route") == "local_rag" and meta.get("used_mode") == "local_extractive",
            "historical_wording_not_current_route": meta.get("dynamic_question") is False,
            "citations_reference_returned_sources": bool(refs) and all(1 <= value <= len(sources) for value in refs),
            "returned_sources_resolve": bool(sources) and all(source_resolves(source, store, legacy) for source in sources),
            "returned_sources_have_locator": bool(sources) and all(source.get("locator") and source.get("title") for source in sources),
            "pending_sources_not_used": bool(sources) and all(source.get("status") in {"indexed", "approved"} for source in sources),
            "historical_scope_is_disclosed": bool(sources) and "历史资料" in answer,
        })
    return checks


def expiry_gate_checks() -> dict:
    # In-memory evidence fixtures exercise eligibility only. They are never
    # inserted into the real database or represented as real source documents.
    base = {"title": "测试用规范（非真实资料）", "category": "标准规范", "status": "approved", "text": "测试文本"}
    blocked = {}
    for status in ("expired", "superseded", "withdrawn", "draft"):
        blocked[status] = not usable_source({**base, "metadata": {"edition_status": status}})
    blocked["pending_standard"] = not usable_source({**base, "status": "pending_review", "metadata": {}})
    return blocked


def write_markdown(report: dict, target: Path) -> None:
    rows = ["# 第1个月基础问答版：本地技术验收记录", "",
            "本记录检查真实知识库的检索、证据编号、来源定位与缺源边界。未调用 DeepSeek 或网页搜索，未写入知识库，也未把原资料批量改为已审核。", "",
            "20题是根据160题台账拆出的开发候选子问题，集中于已有历史个例，未获得业务批准，不是对160题的随机抽样或最终验收。原题中的实时、全量统计、比较或决策要求不因历史子题可答而自动完成。资料可检索不等于足以完整回答，数字、表格、现行有效性与统计口径仍需人工核对。", "",
            f"执行时间：{report['generated_at']}。主库 {report['inventory']['sources']} 份来源，{report['inventory']['chunks']} 个切片。", "",
            f"技术检查：候选题 {report['candidate_summary']['technical_checks_passed']}/20 全部检查项通过；边界题 {report['boundary_summary']['technical_checks_passed']}/{report['boundary_summary']['case_count']} 通过。技术通过只说明链路检查通过。", "",
            "专家正确性未评分，专家通过数为未评定；“20题中至少18题通过”的业务验收目标仍待参考答案、证据范围和验收人员核定。", "",
            "|编号|关联需求|候选问题|证据数|技术检查|人工重点|", "|---|---|---|---:|---|---|"]
    for row in report["candidates"]:
        bad = [key for key, value in row["checks"].items() if not value]
        label = "通过" if not bad else "未通过：" + "、".join(bad)
        clean = lambda value: str(value).replace("|", "／").replace("\n", " ")
        rows.append("|" + "|".join(map(clean, (row["id"], "、".join(row["requirement_ids"]), row["question"], len(row["sources"]), label, row["review_focus"]))) + "|")
    rows.extend(["", "## 缺源与时效边界", "", "|编号|问题|预期处理|技术检查|", "|---|---|---|---|"])
    for row in report["boundaries"]:
        rows.append(f"|{row['id']}|{row['question']}|{row['reason']}|{'通过' if row['technical_checks_passed'] else '未通过'}|")
    rows.extend(["", "过期、被替代、撤销、草稿规范和未审核标准另以内存测试数据检查禁止引用。这些测试数据没有写入知识库。", "",
                 "## 如何进行业务验收", "",
                 "1. 业务负责人核定20题候选范围和原需求映射；本候选集不能代替全部需求验收。",
                 "2. 专家逐题核对来源原件、图表、口径和版本，写入参考答案、证据定位及预期缺源处理。",
                 "3. 现场记录最终回答，分别评价事实正确、切题完整、引用可回查、缺源处理和时间口径。",
                 "4. 经专家确认的逐题结果才计入至少18/20目标；本脚本不会修改需求状态或生成专家通过结论。", ""])
    target.write_text("\n".join(rows), encoding="utf-8")


def run(args) -> dict:
    candidate_path = args.candidates.resolve(strict=True)
    catalog_path = args.catalog.resolve(strict=True)
    declared = json.loads(candidate_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in catalog["items"]}
    if len(by_id) != 160 or len(declared["cases"]) != 20:
        raise ValueError("Expected 160 ledger questions and exactly 20 frozen candidates")
    for case in declared["cases"] + declared["boundary_cases"]:
        if not case.get("requirement_ids") or any(qid not in by_id for qid in case["requirement_ids"]):
            raise ValueError("Unknown requirement mapping: " + case["id"])
    store = ReadOnlyStore(args.database)
    legacy = ReadOnlyLegacy(args.legacy_database) if args.legacy_database.is_file() else None
    files = [store.database] + ([legacy.database_path] if legacy else [])
    before = {path: sha256(path) for path in files}
    if not args.lexical_only:
        try:
            from marine_kb.semantic import LocalSemanticIndex
            store.semantic_index = LocalSemanticIndex(store.database, args.index_dir)
        except ImportError:
            pass
    external = ForbiddenExternal()
    qa = MarineQuestionService(store, legacy_rag=legacy, model_caller=external,
                               web_service=external, external_enabled=False)
    def evaluate(case):
        result = qa.answer(case["question"], sid=case.get("sid"), allow_web=False)
        checks = automatic_checks(case, result, store, legacy, external)
        return {**case, "ledger_questions": [{"id": qid, "question": by_id[qid]["question"]} for qid in case["requirement_ids"]],
                "checks": checks, "technical_checks_passed": all(checks.values()), "answer": result.get("answer"),
                "sources": result.get("rag_sources", []), "answer_meta": result.get("answer_meta", {}),
                "expert_pass": None, "expert_reference_answer": None, "expert_reviewed": False}
    candidates = [evaluate(case) for case in declared["cases"]]
    boundaries = [evaluate(case) for case in declared["boundary_cases"]]
    after = {path: sha256(path) for path in files}
    unchanged = all(before[path] == after[path] for path in files)
    report = {
        "schema_version": 1, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "只读本地检索/引用/路由技术检查；不评定专家答案正确性，不调用模型和网页，不变更审核及需求状态。",
        "candidate_manifest_sha256": sha256(candidate_path), "catalog_sha256": sha256(catalog_path),
        "original_catalog_source_sha256": catalog.get("source_sha256"),
        "ledger_question_count": len(by_id), "selection_note": declared["selection_note"],
        "mapping_scope": declared["mapping_scope"],
        "business_approved": False, "expert_accepted_count": None,
        "expert_acceptance_target": {"required": 18, "total": 20, "evaluated": False},
        "inventory": db_inventory(store), "database_read_only": True, "database_files_unchanged": unchanged,
        "database_integrity": [{"name": "marine_kb" if path == store.database else "legacy_rag",
                                "before_sha256": before[path], "after_sha256": after[path]} for path in files],
        "external_call_count": external.calls, "semantic_status": getattr(store.semantic_index, "last_status", "disabled_or_unavailable"),
        "candidate_summary": {"case_count": len(candidates), "technical_checks_passed": sum(row["technical_checks_passed"] for row in candidates),
                              "source_ready_after_expert_review": None},
        "boundary_summary": {"case_count": len(boundaries), "technical_checks_passed": sum(row["technical_checks_passed"] for row in boundaries)},
        "expiry_eligibility_checks": expiry_gate_checks(), "candidates": candidates, "boundaries": boundaries,
        "limitations": ["检索命中和自动引用检查不证明答案正确、完整或来源已经审定。", "20题集中于少数历史个例，不证明160题或10类业务全面覆盖。", "未验证DeepSeek真实生成、联网可用性、当前接口或正式业务发布能力。", "专家未填写参考答案、未审批候选题、未进行逐题评分，不能宣称18/20目标通过。"],
    }
    if not unchanged or external.calls:
        raise RuntimeError("Read-only/no-external invariant failed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, args.output.with_suffix(".md"))
    print(json.dumps({key: report[key] for key in ("generated_at", "ledger_question_count", "candidate_summary", "boundary_summary", "database_files_unchanged", "external_call_count", "expiry_eligibility_checks", "expert_acceptance_target")}, ensure_ascii=False, indent=2))
    print("report=" + str(args.output.resolve()))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=APP / "data/marine_kb/knowledge.sqlite3")
    parser.add_argument("--legacy-database", type=Path, default=APP / "typhoon/data/typhoon_rag.sqlite3")
    parser.add_argument("--index-dir", type=Path, default=APP / "data/marine_kb/semantic")
    parser.add_argument("--catalog", type=Path, default=APP / "data/requirements/catalog.json")
    parser.add_argument("--candidates", type=Path, default=APP / "docs/month1_acceptance_candidates.json")
    parser.add_argument("--output", type=Path, default=APP / "docs/month1_acceptance_results.json")
    parser.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args()
    report = run(args)
    technical_ok = (report["candidate_summary"]["technical_checks_passed"] == 20
                    and report["boundary_summary"]["technical_checks_passed"] == report["boundary_summary"]["case_count"]
                    and all(report["expiry_eligibility_checks"].values()))
    raise SystemExit(0 if technical_ok else 1)


if __name__ == "__main__":
    main()
