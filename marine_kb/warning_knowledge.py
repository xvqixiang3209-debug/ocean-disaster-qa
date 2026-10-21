"""Official, subject-owned marine warning concepts; never a live warning query.

The facts are evidence summaries, rather than arbitrary passage concatenation.
The optional model organizes existing fact IDs and approved headings only.  It
cannot change their subjects, remove a required fact, or append business gaps.
This intentionally bypasses the generic recovery path for these stable basics.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re


DATA_PATH = Path(__file__).with_name("data") / "warning_concepts.json"
TOPICS = {
    "wave_warning": ("海浪预警是什么意思", ("海浪预警", "海浪警报")),
    "surge_warning": ("风暴潮预警是什么意思", ("风暴潮预警", "风暴潮警报")),
    "wave_surge_comparison": ("海浪预警与风暴潮预警的区别", ("两种预警的区别",)),
    "warning_levels": ("预警等级表示什么", ("预警含义与四色等级", "预警等级")),
    "emergency_response": ("应急响应表示什么", ("应急响应",)),
    "warning_response_comparison": ("预警等级与应急响应的区别", ("预警与响应的区别",)),
}

LAYOUT_PROMPT = (
    "你为海洋灾害概念答案组织清晰结构。只返回JSON："
    '{"sections":[{"id":"给定section_id","heading":"给定允许标题",'
    '"fact_ids":["该section的全部fact_id"]}]}。'
    "每个section和fact必须恰好出现一次，不跨section搬移事实。"
    "你可调整段落和事实顺序、选择通俗标题，不输出任何其他内容。"
    "事实已核对官方原文，正文由程序渲染，不能新增阈值、缺项、当前预警或启动条件。"
)


@lru_cache(maxsize=1)
def _data():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    sources = {row["id"]: row for row in data["sources"]}
    supports = {f'{source["id"]}:{excerpt["id"]}'
                for source in sources.values() for excerpt in source["excerpts"]}
    for fact in data["facts"]:
        if fact["topic"] not in TOPICS or not fact["supports"]:
            raise ValueError("invalid_warning_concept_fact")
        if not set(fact["supports"]) <= supports:
            raise ValueError("warning_concept_missing_original_excerpt")
    return data


def matches(question):
    """Accept stable definitions only, evaluated on the *original* user turn.

    Mixed concept/business questions stay with the full evidence dispatcher,
    rather than losing the date, live-list or administrative part here.
    """
    q = re.sub(r"\s+", "", str(question or ""))
    if not q or len(q) > 1000:
        return False
    if re.search(r'\d|标准|规范|条款|第.{0,12}条', q):
        # A numeric phrase or a named standard is a specific provision query,
        # even if it ends in “是什么意思”. A generic concept is not its answer.
        return False
    if re.search(r"海啸|海冰|台风(?:预警|警报)|暴雨|雷电|地震|森林火险", q):
        return False
    if not (re.search(r"(?:海浪|风暴潮|海洋灾害).{0,4}(?:预警|警报)", q)
            or ("预警" in q and "应急响应" in q)):
        return False
    # Concrete date/time and state inquiries must retain their operational route.
    if re.search(r"\d{4}|\d{1,2}月\d{1,2}[日号]|今天|昨日|昨天|明天|当日|当天|"
                 r"截至|目前|当前|现在|本次|这次|此次|最近|最新|实时|清单|记录|"
                 r"已(?:经)?发布|发布了|正在|是否生效|有效时间|有效期|覆盖哪些|哪些海域", q):
        return False
    if re.search(r"阈值|判据|多少(?:米|厘米|级|时|分)|几米|几级|多大(?:浪|风)|"
                 r"达到多少|启动|终止|降级|解除|停航|停运|审批|申请|谁决定|由谁|"
                 r"什么时候|何时|怎么办|如何应对|怎么防|防范措施|采取.{0,4}措施|"
                 r"计算|怎么算|如何算|提取|下载|接口|制作|生成.{0,6}报告|"
                 r"可以.{0,6}(?:游泳|赶海|海钓|出海)|能不能", q):
        return False
    return bool(re.search(r"什么意思|是什么意思|是什么|什么是|含义|概念|定义|"
                          r"区别|不同|一回事|一样|相同|是不是|如何理解|怎么理解|意味着|代表什么|表示什么|科普|"
                          r"颜色|四色|蓝黄橙红|红橙黄蓝|从低到高|从高到低|等级.{0,6}(?:表示|代表|含义)", q))


def _topics(question):
    q = str(question)
    wave, surge = "海浪" in q, "风暴潮" in q
    response = "应急响应" in q
    topics = []
    if wave:
        topics.append("wave_warning")
    if surge:
        topics.append("surge_warning")
    if wave and surge and re.search(r"区别|不同|一回事|一样|相同|是不是", q) and not response:
        topics.append("wave_surge_comparison")
    if re.search(r"等级|颜色|四色|蓝黄橙红|红橙黄蓝", q) or response or not (wave or surge):
        topics.append("warning_levels")
    if response:
        topics.extend(("emergency_response", "warning_response_comparison"))
    return topics


def build(question):
    """Return complete owned sections plus only the official passages they use.

    Callers may retrieve additional evidence for other routes, but must not pass
    a normalized multi-question reconstruction to ``matches`` in place of the
    original turn.  ``None`` means this route does not own the whole question.
    """
    if not matches(question):
        return None
    data = _data()
    sections = []
    required_supports = set()
    for topic in _topics(question):
        facts = [deepcopy(row) for row in data["facts"] if row["topic"] == topic]
        required_supports.update(support for fact in facts for support in fact["supports"])
        title, alternatives = TOPICS[topic]
        sections.append({"id": topic, "heading": title,
                         "allowed_headings": [title, *alternatives], "facts": facts})
    hits = []
    for source in data["sources"]:
        excerpts = [row for row in source["excerpts"]
                    if f'{source["id"]}:{row["id"]}' in required_supports]
        if not excerpts:
            continue
        hits.append({
            "source_id": "warning-concept:" + source["id"],
            "chunk_id": source["id"] + ":concepts", "title": source["title"],
            "filename": source["title"], "url": source["url"],
            "locator": "；".join(dict.fromkeys(row["locator"] for row in excerpts)),
            "status": "approved", "category": "官方海洋预警概念",
            "text": "\n\n".join(f'{row["locator"]}：{row["text"]}' for row in excerpts),
            "metadata": {"source_type": "public_web", "external_allowed": True,
                         "publisher": source["publisher"], "published_at": source["published_at"],
                         "retrieved_at": source["fetched_at"], "sha256": source["sha256"],
                         "read_status": "partial", "text_is_excerpt": True,
                         "primary_scope": "stable_warning_concepts",
                         "review_method": "official_concept_original_excerpt",
                         "source_version_scope": "所列官方文献版本的基础概念，不作当日预警清单或地方启动规则"},
            "retrieval_details": {"direct_evidence": True, "concept_card": True,
                                  "excerpt_ids": [row["id"] for row in excerpts]},
        })
    return {"question": str(question), "topics": [row["id"] for row in sections],
            "sections": sections, "hits": hits, "card_version": data["reviewed_at"]}


def _layout(sections, raw):
    """A model may change structure; every fact remains with its owner."""
    value = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    if set(value) != {"sections"} or not isinstance(value["sections"], list):
        raise ValueError("invalid_concept_layout_schema")
    expected = {row["id"]: row for row in sections}
    seen = set()
    result = []
    for row in value["sections"]:
        if not isinstance(row, dict) or set(row) != {"id", "heading", "fact_ids"}:
            raise ValueError("invalid_concept_layout_section")
        section_id = row["id"]
        if section_id not in expected or section_id in seen:
            raise ValueError("concept_layout_wrong_or_duplicate_owner")
        section = expected[section_id]
        facts = {fact["id"]: fact for fact in section["facts"]}
        if (not isinstance(row["fact_ids"], list)
                or len(row["fact_ids"]) != len(facts)
                or not all(isinstance(key, str) for key in row["fact_ids"])
                or set(row["fact_ids"]) != set(facts)):
            raise ValueError("concept_layout_removed_or_moved_fact")
        if row["heading"] not in section["allowed_headings"]:
            raise ValueError("concept_layout_unverified_heading")
        result.append({**section, "heading": row["heading"],
                       "facts": [facts[key] for key in row["fact_ids"]]})
        seen.add(section_id)
    if seen != set(expected):
        raise ValueError("concept_layout_missing_subquestion")
    return result


def _render(sections, hits):
    citations = {row["source_id"].split(":", 1)[1]: index
                 for index, row in enumerate(hits, 1)}
    paragraphs = []
    for section in sections:
        text = []
        for fact in section["facts"]:
            source_ids = dict.fromkeys(support.split(":", 1)[0] for support in fact["supports"])
            refs = "".join(f"[资料{citations[source_id]}]" for source_id in source_ids)
            text.append(fact["text"] + refs)
        paragraphs.append("**" + section["heading"] + "**\n\n" + "\n\n".join(text))
    return "\n\n".join(paragraphs)


def _sources(hits):
    return [{"citation": f"资料{index}", "citation_id": index, "source_id": row["source_id"],
             "chunk_id": row["chunk_id"], "title": row["title"], "filename": row["filename"],
             "url": row["url"], "locator": row["locator"], "source_type": "public_web",
             "status": "approved", "review_label": "官方原文已核对 · 基础概念证据卡",
             "evidence_kind": "官方文献正文及有据归纳", "excerpt": row["text"][:1200],
             "quality_warning": "", "metadata": deepcopy(row["metadata"])}
            for index, row in enumerate(hits, 1)]


def source_allowed(source):
    """Recheck a retained concept citation before using its answer as context."""
    identifier = str(source.get('source_id') or '').removeprefix('warning-concept:')
    metadata = source.get('metadata') or {}
    return bool(metadata.get('external_allowed') and any(
        row['id'] == identifier and row['sha256'] == metadata.get('sha256') for row in _data()['sources']))


def answer(question, qa=None, model=None, meta=None, allow_web=False):
    """Return a normal QA reply or None, with the shared joint retriever.

    When enabled, DeepSeek arranges verified facts, rather than regenerating and
    then deleting their scientific/institutional relationships.  Model errors
    and invalid layouts fall back to the complete same-source concept answer.
    Card acquisition time remains explicit. Additional local/web candidates
    do not silently overwrite the card's checked institutional relationships.
    """
    payload = build(question)
    if payload is None:
        return None
    sections, hits = payload["sections"], payload["hits"]
    retrieval = {}
    if (qa is not None and allow_web and getattr(qa, 'crag_enabled', False)
            and callable(getattr(qa, 'joint_evidence', None))):
        try:
            retrieval = {**(meta or {}), 'subquestions':[row['heading'] for row in sections]}
            qa.joint_evidence(question, retrieval, allow_web, pinned=hits)
        except Exception as exc:
            retrieval['concept_retrieval_error'] = type(exc).__name__
    organized = False
    layout_error = None
    if qa is not None and getattr(qa, "external_enabled", False) and getattr(qa, "model_caller", None):
        try:
            prompt = json.dumps({"question": question, "sections": sections}, ensure_ascii=False)
            raw = qa._call_model(model or qa.model_name, LAYOUT_PROMPT, prompt)
            sections = _layout(sections, raw)
            organized = True
        except Exception as exc:
            layout_error = str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__
    sources = _sources(hits)
    diagnostics = {key:retrieval[key] for key in (
        'local_retrieval_executed','local_evidence_count','web_search_executed','web_search_queries',
        'web_search_states','web_body_candidate_count','web_body_read_failures','concept_retrieval_error') if key in retrieval}
    metadata = {**(meta or {}), **diagnostics, "route": "official_warning_concepts",
                "used_mode": "llm" if organized else "official_concept_cards",
                "model": (model or getattr(qa, "model_name", None)) if organized else None,
                "answerability": "full", "business_state": "ready", "dynamic_question": False,
                "generation_used": organized, "generation_scope": "verified_fact_layout_only",
                "official_warning_concepts": True, "concept_card_version": payload["card_version"],
                "normalized_question": str(question), "missing_information": [],
                "fallback": bool(layout_error), "concept_layout_error": layout_error,
                "web_allowed": bool(allow_web), "live_web_search_performed": bool(retrieval.get('web_search_executed')),
                "concept_evidence_policy": "checked_official_cards_after_joint_retrieval",
                "as_of": (meta or {}).get("as_of") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "subquestion_coverage": [{"id": row["id"], "question": row["heading"],
                                          "status": "full", "fact_ids": [fact["id"] for fact in row["facts"]]}
                                         for row in sections],
                "concept_fact_ownership": {fact["id"]: row["id"] for row in sections for fact in row["facts"]}}
    return {"answer": _render(sections, hits), "rag_sources": [], "web_sources": sources,
            "answer_meta": metadata}
