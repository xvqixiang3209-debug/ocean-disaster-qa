"""Local lexical RAG retrieval. This module does not claim embedding retrieval."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Any
from .answer_design import RESPONSE_CHANGE
from .query_intents import focused_queries, support_score


TERM_GROUPS = {
    "intensity": ("台风强度", "强度", "风速", "风力", "等级", "气压"),
    "surge": ("风暴潮", "增水", "潮位", "高潮位", "验潮", "超警"),
    "wave": ("海浪", "波高", "有效波高", "波浪", "浪高", "波周期"),
    "response": ("应对", "措施", "防御", "防台", "防汛", "应急", "转移", "避风", "撤离", "处置", "防范"),
    "damage": ("灾情", "损失", "经济损失", "受灾", "死亡", "房屋", "养殖", "农作物", "倒塌", "影响"),
    "warning": ("预警", "警报", "超警", "警戒", "发布", "分级"),
    "landfall": ("登陆", "登陆地点", "登陆时间", "经过", "路径"),
    "red_tide": ("赤潮", "有害藻", "藻华"),
    "tsunami": ("海啸", "地震海啸"),
    "sea_level": ("海平面", "海水入侵", "咸潮"),
}
TYPO_REPLACEMENTS = {"汝河": "如何", "汝何": "如何", "强渡": "强度", "风暴朝": "风暴潮", "海啸预抱": "海啸预报", "有哪些说应对": "有哪些应对"}
STORM_ALIASES = {
    "利奇马": "lekima", "麦莎": "matsa", "巴威": "bavi", "卡努": "khanun", "沙德尔": "saudel",
    "桑美": "saomai", "云娜": "rananim", "韦帕": "wipha", "摩羯": "yagi", "温比亚": "rumbia",
    "烟花": "in-fa", "梅花": "muifa", "灿都": "chanthu", "杜苏芮": "doksuri", "山陀儿": "krathon",
    "天兔": "usagi", "菲特": "fitow", "潭美": "trami", "苏力": "soulik", "海葵": "haikui",
    "天秤": "tembin", "苏拉": "saola", "海棠": "haitang", "罗莎": "krosa", "莫拉克": "morakot",
    "灿鸿": "chan-hom", "苏迪罗": "soudelor", "马勒卡": "malakas", "鲇鱼": "megi", "玛莉亚": "maria",
    "安比": "ampil", "云雀": "jongdari", "米娜": "mitag", "黑格比": "hagupit", "美莎克": "maysak",
    "白海豚": "dolphin", "贝碧嘉": "bebinca", "普拉桑": "pulasan", "南玛都": "nanmadol",
}
COMMON_QUERY_WORDS = ("请问", "请", "告诉我", "我想知道", "能不能", "可以", "什么", "多少", "哪些", "如何", "怎样", "情况", "相关", "资料", "这个", "这次", "此次", "台风", "浙江省", "浙江", "关于", "是否", "的是", "一下", "一下子", "有没有", "有什么", "有何", "影响", "今年", "去年", "目前", "最新")


def normalize_question(question: str) -> str:
    text = " ".join(str(question or "").strip().split())[:2000]
    for typo, correction in TYPO_REPLACEMENTS.items():
        text = text.replace(typo, correction)
    return text


def topic_groups(question: str) -> dict[str, tuple[str, ...]]:
    return {name: terms for name, terms in TERM_GROUPS.items() if any(term in question for term in terms)}


def lexical_terms(question: str) -> set[str]:
    text = normalize_question(question).lower()
    for token in sorted(COMMON_QUERY_WORDS, key=len, reverse=True):
        text = text.replace(token, " ")
    terms = set(re.findall(r"[a-z][a-z0-9-]{2,}|\d{4,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def requested_topics(question: str) -> set[str]:
    terms = lexical_terms(question)
    identities = {part for name in STORM_ALIASES for part in (name, STORM_ALIASES[name])}
    return {term for term in terms if not re.fullmatch(r"\d+|[a-z-]+", term) and term not in identities}


def source_text(item: dict[str, Any]) -> str:
    from .phase2 import enabled
    if enabled('MARINE_EVIDENCE_UNITS') and item.get('source_text'):
        return str(item['source_text'])
    return str(item.get("text") or item.get("content") or "")


def evidence_focus(question: str, text: str) -> bool:
    """A related event or another variable's unit is not a definition answer."""
    compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
    if '响应' in question and re.search(RESPONSE_CHANGE, question):
        if not re.search(r'(?:降级|降低|终止|调整|结束|解除).{0,16}(?:响应|应急行动)|(?:响应|应急行动).{0,16}(?:降级|降低|终止|调整|结束|解除)', compact):
            return False
    unit = re.search(r'([\u4e00-\u9fff]{2,12}?)的?单位(?:是|为|采用|用)', question)
    if unit:
        subject = unit[1].removesuffix('的')
        return bool(re.search(re.escape(subject) + r'的?单位(?:为|是|[:：])', compact))
    definition = re.fullmatch(r'(?:请问)?什么是([^？?。]{2,20})[？?。]?', question)
    if not definition:
        definition = re.fullmatch(r'(?:请问)?([^？?。]{2,20}?)(?:是什么|是指什么|的定义是什么)[？?。]?', question)
    if definition:
        subject = definition[1]
        # Keep this conservative: a product forecasting an amount does not
        # explain the term even if every word in the question occurs in it.
        return bool(re.search(re.escape(subject) + r'(?:[A-Za-z-]{1,80})?(?:是指|指的是|指|是(?:由|因|在)|为(?:由|因|气象)|由于|由|相对于)', compact))
    return True


def scope_case_text(question: str, text: str) -> tuple[str, bool]:
    """Trim cross-event book chunks at explicit case headings, never guess facts."""
    targets = [(cn, en) for cn, en in STORM_ALIASES.items() if cn in question or en in question.lower()]
    if not targets:
        return text, False
    headings = list(re.finditer(r"(?m)(?:^|\n)\s*(?:\d{1,3}[．.）)]\s*)?(?:\d{4}\s*号(?:、\d{4}\s*号)?台风风暴潮灾害[^\n]*|\d{4}\s*(?:号)?(?:强)?(?:台风)?[“\"‘‚][^”\"’‛]+[”\"’‛](?:台风风暴潮灾害)?)", text))
    names = [word for pair in targets for word in pair]
    target_positions = [text.lower().find(name.lower()) for name in names if text.lower().find(name.lower()) >= 0]
    if not target_positions:
        return text, False
    # Prefer the actual named case heading, not a figure caption or a preceding
    # case's comparison with a later typhoon.
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        opening = text[heading.start():min(end, heading.end() + 120)].lower()
        if any(name.lower() in opening for name in names):
            segment = text[heading.start():end].strip()
            major = re.search(r"(?m)^\s*[一二三四五六七八九十]+、[^\n]*(?:海浪|赤潮|海啸|灾害)", segment)
            if major:
                segment = segment[:major.start()].strip()
            return segment, segment != text
    if not headings:
        return text, False
    target_position = min(target_positions)
    preceding = [match.start() for match in headings if match.start() <= target_position]
    start = max(preceding) if preceding else 0
    next_headings = [match.start() for match in headings if match.start() > target_position]
    end = min(next_headings) if next_headings else len(text)
    if start == 0 and end == len(text):
        return text, False
    return text[start:end].strip(), True


def is_question_bank(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") or {}
    label = " ".join(str(item.get(key) or "") for key in ("category", "filename", "title"))
    return metadata.get("source_type") in {"question_bank", "evaluation_questions"} or any(token in label for token in ("基础问题库", "问题库", "需求清单", "question_bank", "evaluation_questions"))


def is_formal_rule(item: dict[str, Any]) -> bool:
    label = " ".join(str(item.get(key) or "") for key in ("category", "filename", "title"))
    return (any(word in label.lower() for word in ("标准", "规范", "预案", "规章", "standard", "regulation", "policy"))
            or (item.get('metadata', {}).get('review_scope') == 'versioned_reference'
                and bool(re.search(r'办法|条例', label))))


def _named_clause_focus(question, item):
    """Prefer the requested predicate inside a named act, not its title/date."""
    if not item.get('named_document_match'):
        return {'score': 0.0, 'duration_requested': False, 'duration_supported': False,
                'quoted_subject_supported': False}
    body = re.sub(r'\s+', '', source_text(item))
    focus = re.sub(r'《[^》]+》|(?:19|20)\d{2}年(?:公布|发布|修订|修正)?版本', '', question)
    words = lexical_terms(focus)
    score = 2 * sum(term in body.lower() for term in words) / max(1, len(words))
    # A short quoted subject is a stronger locator than a document's long
    # repeated title. It still needs exact body support; title matches alone
    # must not displace a provision that actually defines the requested term.
    quoted = re.findall(r'[“\"‘]([^”\"’\n]{2,24})[”\"’]', focus)
    subjects = [re.sub(r'\s+', '', value) for value in quoted]
    if subjects:
        score += 8 * sum(value in body for value in subjects) / len(subjects)
    predicates = re.findall(r'(?:何时|什么时候|多久|多长时间)(?:才能|可以|能够|应当|能|可|会|才)?([\u4e00-\u9fff]{2})', focus)
    predicates += re.findall(r'([\u4e00-\u9fff]{2})(?:多久|多长时间|几年|几个月|几天|几小时)', focus)
    predicates += [name for name in ('时效', '时限', '期限') if name in focus]
    predicates = [word for word in predicates if word not in {'资料', '文件', '标准', '规定', '需要'}]
    duration_supported = False
    if predicates and any(word in body for word in predicates):
        score += 4
        # A signing date is not an answer to an operational duration. Require
        # an actual duration unit, including written-out Chinese numerals.
        if re.search(r'[零一二三四五六七八九十百两\d]+(?:个)?(?:工作日|小时|个月|年|天|h)(?:内|前|以上|以下|的|[。；，\s]|$)', body, re.I):
            score += 2
            duration_supported = True
    return {'score': score, 'duration_requested': bool(predicates), 'duration_supported': duration_supported,
            'quoted_subject_supported': bool(subjects and any(value in body for value in subjects))}


def evidence_kind(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("filename") or "")
    text = source_text(item)
    if is_formal_rule(item):
        return '法规标准条文（所列版本）'
    if any(word in title for word in ("警报", "预报单", "预报202", "海浪预报20", "风暴潮预报20")) or ("预计" in text and any(word in text for word in ("明天", "将出现", "未来"))):
        return "历史预报/警报，非实况"
    if "模式" in title or any(word in text[:80] for word in ("代理估计", "proxy")):
        return "模式或代理估计，非实况"
    return "历史资料记载"


def usable_source(item: dict[str, Any], include_pending: bool = False) -> bool:
    metadata = item.get("metadata") or {}
    if metadata.get('source_type') == 'reference_links':
        return False
    if is_question_bank(item) or not source_text(item).strip():
        return False
    if include_pending:
        return item.get("status") not in {"rejected", "deleted", "quarantined", "error", "archived"}
    if item.get("status") not in {"approved", "indexed"}:
        return False
    if is_formal_rule(item) and item.get("status") != "approved":
        return False
    edition = str(metadata.get("edition_status") or "")
    title = str(item.get("title") or "") + str(item.get("filename") or "")
    if metadata.get("is_draft") or edition in {"draft", "superseded", "withdrawn", "expired", "unknown_draft"} or any(word in title for word in ("征求意见稿", "报批稿", "草案")):
        return False
    return True


def relevance(question: str, item: dict[str, Any]) -> dict[str, Any]:
    """Transparent lexical coverage; it is not a probability of answer correctness."""
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', source_text(item))).lower()
    title = unicodedata.normalize('NFKC', str(item.get("title") or item.get("filename") or "")).lower()
    metadata = item.get('metadata') or {}
    terms = lexical_terms(question)
    terms_hit = sorted(term for term in terms if term in text)
    coverage = len(terms_hit) / max(1, len(terms))
    groups = topic_groups(question)
    matched = [name for name, words in groups.items() if any(word in text for word in words)]
    topic_coverage = len(matched) / max(1, len(groups))
    specific_words = re.findall(r"(?<![\dA-Za-z])(?:19|20)\d{2}(?!\d)|\b[A-Za-z][A-Za-z-]{2,}\b|(?<!\d)\d{4}号?", question)
    corpus = text + " " + title + " " + str(item.get("metadata") or {}).lower()
    alternate_names = {english: chinese for chinese, english in STORM_ALIASES.items()}
    entity_misses = [word for word in specific_words if word.lower().rstrip("号") not in corpus and alternate_names.get(word.lower(), "\0") not in corpus]
    entity_misses.extend(name for name, english in STORM_ALIASES.items() if name in question and name not in corpus and english not in corpus)
    # '2013台设备' is not evidence for the calendar year 2013. Prefer explicit
    # event metadata; otherwise require a dated title or 年 / ISO date context.
    asked_years = re.findall(r"((?:19|20)\d{2})\s*年", question)
    event_year = str(item.get("metadata", {}).get("event_year") or item.get("metadata", {}).get("year") or "")
    if not event_year and "公报" in title:
        title_years = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", title)
        if len(set(title_years)) == 1:
            event_year = title_years[0]
    for year in asked_years:
        edition_request = re.search(r'((?:19|20)\d{2})年(?:公布|发布|修订|修正)?版本', question)
        if (edition_request and edition_request[1] == year and is_formal_rule(item)
                and str(metadata.get('document_issued_at') or metadata.get('publication_date') or '').startswith(year)):
            continue
        if event_year and event_year != year:
            entity_misses.append("event_year:" + year)
        elif not event_year and year not in title and not re.search(rf"(?<!\d){year}\s*(?:年|[-/.]\d{{1,2}})", text):
            entity_misses.append("calendar_year:" + year)
    # Generic overlapping tokens ('浙江'/'台风') never suffice on their own.
    direct = (bool(terms_hit) and coverage >= .25) or bool(groups and topic_coverage >= .5)
    intent_support = support_score(question, source_text(item), title)
    direct = direct or intent_support > 0
    if groups:
        direct = direct and (bool(matched) or intent_support > 0)
    elif requested_topics(question):
        direct = direct and any(term in text for term in requested_topics(question))
    # Hazard identities must match; common '影响/灾害' alone cannot turn a
    # typhoon response document into evidence for red tide or a tsunami.
    hazards = {"red_tide", "tsunami", "sea_level"}
    if any(name in groups and name not in matched for name in hazards):
        direct = False
    if re.search(r"标准|规范|条款|阈值|法定|分级条件|判定条件|发布条件", question):
        if item.get("status") != "approved" or not is_formal_rule(item):
            direct = False
        if re.search(r'警报|预警', question) and not re.search(r'警报|预警|预报|应急|灾害', title):
            direct = False
    if "公报" in question and "公报" not in title:
        direct = False
    targets = [(cn, en) for cn, en in STORM_ALIASES.items() if cn in question or en in question.lower()]
    if targets and re.search(r"损失|灾情|增水|死亡|受灾", question):
        target_words = [word for pair in targets for word in pair]
        other_names = [cn for cn, en in STORM_ALIASES.items() if cn not in target_words and en not in target_words and (cn in text or re.search(r"\b" + re.escape(en) + r"\b", text))]
        if other_names and not item.get("metadata", {}).get("case_boundary_trimmed"):
            direct = False
    if re.search(r"最大增水|最高[潮水]位|最大风速|实际|实况|造成.*损失", question) and not re.search(r"预计|预测|预报|模拟|模式", question):
        if evidence_kind(item) != "历史资料记载":
            direct = False
    precise_fields = {
        "增水": ("增水",), "潮位": ("潮位", "水位"), "死亡": ("死亡", "死者", "遇难"),
        "损失": ("损失", "受灾", "损毁", "受损", "死亡", "倒塌"),
        "倒塌": ("倒塌", "房屋"), "防御措施": TERM_GROUPS["response"], "应对措施": TERM_GROUPS["response"],
    }
    evidence_body = re.sub(r"(?m)^\s*图\s*\d[^\n]*", "", text)
    requested_fields=[(field,words) for field,words in precise_fields.items() if field in question]
    joined=bool(re.search(r'和|与|及|、|分别',question))
    other_subject_match=joined and any(name in question and name in evidence_body
        for name in ('浮标','雷达','卫星','海浪','海温','风速','海流','气压'))
    if requested_fields:
        # A chapter may support one of several requested fields or objects.
        # Event/year identity gates above remain mandatory for every chapter.
        matched_field=any(any(word in evidence_body for word in words) for _,words in requested_fields)
        if not matched_field and not other_subject_match:
            direct=False
    # Explicitly requested document editions are different from an event year.
    # A 2019 consolidated text can mention its 2017 publication history, which
    # is not evidence that its amended clauses are the requested 2017 version.
    edition = re.search(r'((?:19|20)\d{2})年(?:公布|发布|修订|修正)?版本', question)
    if edition and is_formal_rule(item):
        date = str(metadata.get('document_issued_at') or metadata.get('publication_date') or '')
        source_year = re.match(r'(?:19|20)\d{2}', date)
        if source_year and source_year[0] != edition[1]:
            entity_misses.append('document_edition:' + edition[1])
    asked_codes = re.findall(r'(?:GB|HY|DB\d+)\s*[/∕_ -]?\s*T?\s*(\d+(?:\.\d+)?)\s*[-—－]\s*(\d{4})', question, re.I)
    source_identity = str(metadata.get('standard_id') or title)
    own_codes = re.findall(r'(?:GB|HY|DB\d+)\s*[/∕_ +\-]?\s*T?\s*(\d+(?:\.\d+)?)\s*[-—－]\s*(\d{4})', source_identity, re.I)
    if asked_codes and own_codes and not set(asked_codes) & set(own_codes):
        entity_misses.append('document_standard_identity')
    from .document_identity import notice_codes
    asked_notices, own_notices = notice_codes(question), notice_codes(title)
    if asked_notices and own_notices and not set(asked_notices) & set(own_notices):
        entity_misses.append('document_notice_identity')
    if asked_codes and set(asked_codes) & set(own_codes) and re.search(r'全称|名称|版本|编号|出处|发布日期|实施日期|现行', question):
        direct = True
    if _named_clause_focus(question, item)['duration_supported'] and is_formal_rule(item):
        direct = True
    if _named_clause_focus(question, item)['quoted_subject_supported'] and is_formal_rule(item):
        # The operative clause can omit the long notice's broad hazard words.
        # This establishes retrieval relevance, not answer correctness.
        direct = True
    if entity_misses:
        direct = False
    if not evidence_focus(question, source_text(item)):
        direct = False
    return {"coverage": round(coverage, 4), "topic_coverage": round(topic_coverage, 4), "matched_terms": terms_hit[:24], "matched_topics": matched, "entity_misses": entity_misses, "intent_support":intent_support, "direct_evidence": direct}


def rank_candidates(question: str, candidates: list[dict], limit: int = 8, *, sid: str | None = None, include_pending: bool = False) -> list[dict]:
    from .governance import reference_scope_allowed
    ranked = []
    seen = set()
    for raw in candidates:
        item = dict(raw)
        item["metadata"] = dict(raw.get("metadata") or {})
        item["text"] = source_text(item)
        # A later case-boundary trim can only narrow the authorized original.
        item.setdefault('_evidence_digest', hashlib.sha256(item['text'].encode('utf-8')).hexdigest())
        item["text"], trimmed = scope_case_text(question, item["text"])
        if trimmed:
            item["metadata"]["case_boundary_trimmed"] = True
            item["locator"] = str(item.get("locator") or "") + " · 按明确案例标题截取"
        targets = [(cn, en) for cn, en in STORM_ALIASES.items() if cn in question or en in question.lower()]
        if targets and not trimmed and not any(name in str(item.get("title", "")).lower() for pair in targets for name in pair):
            # Within a broad annual report, retain only clauses explicitly
            # attributed to the requested storm. An annual list is not an
            # individual-event loss statement.
            compact = re.sub(r"\s*\n\s*", " ", item["text"])
            target_words = [word for pair in targets for word in pair]
            other_names = [cn for cn, en in STORM_ALIASES.items() if cn not in target_words and en not in target_words and (cn in compact or re.search(r"\b" + re.escape(en) + r"\b", compact.lower()))]
            if other_names:
                clauses = re.split(r"(?<=[。；])|(?=其中\s*\d{4})", compact)
                scoped = [clause.strip() for clause in clauses if any(word in clause.lower() for word in target_words) and not any(name in clause for name in other_names)]
                item["text"] = " ".join(scoped)
                item["metadata"]["named_clause_scoped"] = True
                item["locator"] = str(item.get("locator") or "") + " · 仅明确归属该台风的句段"
        if not usable_source(item, include_pending):
            continue
        if not include_pending and not reference_scope_allowed(question, item):
            continue
        sids = item.get("sids") or []
        if isinstance(sids, str):
            sids = [sids]
        if sid and sids and sid not in sids:
            continue
        if sid and len(sids) > 1 and not targets:
            continue
        if targets:
            names = [name for pair in targets for name in pair]
            without_captions = re.sub(r"(?m)^\s*图\s*\d[^\n]*", "", item["text"]).lower()
            identity_title = str(item.get("title") or item.get("filename") or "").lower()
            if not any(name in without_captions or name in identity_title for name in names):
                # A figure caption names the pictured storm, not necessarily
                # the preceding paragraph of a page-crossing PDF extraction.
                continue
        digest = hashlib.sha256(re.sub(r"\s+", "", item["text"]).encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        details = relevance(question, item)
        raw_score = max(0, float(item.get("score") or 0))
        score = 4 * details["coverage"] + 2 * details["topic_coverage"] + .05 * min(10, math.log1p(raw_score))
        score += details['intent_support']
        if item.get('named_document_match') and details['direct_evidence']:
            score += 3
        score += _named_clause_focus(question, item)['score']
        if (details['direct_evidence'] and details['intent_support'] > 0
                and re.search(r'判据|选取|怎么选|如何选|选站|技术要求|技术流程|发布规则', question)
                and item.get('status') == 'approved' and is_formal_rule(item)):
            # A procedural question needs a substantive rule/method section
            # before old product examples with many repeated matching tokens.
            # This preference never bypasses the version, identity or review
            # gates above, and does not certify a source's current validity.
            score += 1.5
        if details["entity_misses"]:
            score -= 10
        if sid and sid in sids:
            score += .5
        item["retrieval"] = "fts5_lsa_rrf_lexical_rerank" if item.get("semantic_score") is not None else "fts5_bm25_lexical_topic_rerank"
        item["retrieval_details"] = details
        item["score"] = round(score, 5)
        ranked.append(item)
    ranked.sort(key=lambda row: (not row['retrieval_details']['direct_evidence'], -row["score"], str(row.get("chunk_id", ""))))
    # Keep more than one section from the same source, but prevent one long PDF
    # from consuming the entire evidence window.
    result, counts = [], {}
    for item in ranked:
        key = str(item.get("source_id") or item.get("filename") or "")
        if counts.get(key, 0) >= 3:
            continue
        counts[key] = counts.get(key, 0) + 1
        result.append(item)
        if len(result) >= max(1, min(int(limit), 20)):
            break
    return result


def search(store, question: str, limit: int = 8, sid: str | None = None, include_pending: bool = False) -> list[dict]:
    question = normalize_question(question)
    if not question:
        return []
    expansions = [word for group in topic_groups(question).values() for word in group]
    expansions.extend(english for chinese, english in STORM_ALIASES.items() if chinese in question)
    expansions.extend(chinese for chinese, english in STORM_ALIASES.items() if english in question.lower())
    queries = [question] + focused_queries(question)
    if expansions:
        queries.append(question + " " + " ".join(dict.fromkeys(expansions)))
    candidates, ids, rrf = [], set(), {}
    if hasattr(store, 'named_document_candidates'):
        for rank, item in enumerate(store.named_document_candidates(question, limit=80, sid=sid, include_pending=include_pending), 1):
            key = (str(item.get('source_id')), str(item.get('chunk_id')))
            candidates.append(item); ids.add(key); rrf[key] = 1 / (60 + rank)
    for query in queries:
        for rank, item in enumerate(store.search_candidates(query, limit=80, sid=sid, include_pending=include_pending), 1):
            key = (str(item.get("source_id")), str(item.get("chunk_id")))
            rrf[key] = rrf.get(key, 0) + 1 / (60 + rank)
            if key not in ids:
                candidates.append(item)
                ids.add(key)
    semantic = getattr(store, "semantic_index", None)
    if semantic is not None and not include_pending:
        try:
            vector_hits = semantic.search(question, limit=40, sid=sid)
        except Exception:
            vector_hits = []
        by_id = {(str(row.get("source_id")), str(row.get("chunk_id"))): row for row in candidates}
        for rank, item in enumerate(vector_hits, 1):
            key = (str(item.get("source_id")), str(item.get("chunk_id")))
            rrf[key] = rrf.get(key, 0) + 1 / (60 + rank)
            if key not in by_id:
                candidates.append(item)
                by_id[key] = item
            else:
                by_id[key]["semantic_score"] = item.get("semantic_score", item.get("score"))
                by_id[key]["semantic_method"] = item.get("semantic_method")
    for item in candidates:
        key = (str(item.get("source_id")), str(item.get("chunk_id")))
        item["rrf_score"] = rrf.get(key, 0)
        item["score"] = 100 * rrf.get(key, 0)
    return rank_candidates(question, candidates, limit, sid=sid, include_pending=include_pending)


def explicit_appendix_candidates(store, question, selected, limit=2):
    """Resolve same-document references as separate, unchanged original chunks.

    Caller must authorize every returned chunk and reserve its final evidence
    position. Ordinary lexical re-selection can otherwise discard these pages.
    """
    from .governance import reference_scope_allowed
    maximum = max(0, min(int(limit), 4))
    if not maximum or not hasattr(store, 'detail'):
        return []
    asked_selection = bool(re.search(r'选取|怎么选|如何选|选择|选站', question))
    references = []
    for parent in selected:
        if not parent.get('source_id') or not usable_source(parent):
            continue
        if not reference_scope_allowed(question, parent):
            continue
        text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', source_text(parent)))
        for clause in re.split(r'(?<=[。；;])', text):
            for match in re.finditer(r'(?:参见|详见|见)附录([A-Z])(?=[^A-Z]|$)', clause):
                overlap = len(lexical_terms(question) & lexical_terms(clause))
                priority = overlap / max(1, len(lexical_terms(clause)))
                if asked_selection and re.search(r'选择|选取|选站', clause):
                    priority += 4
                if priority <= 0:
                    continue
                references.append((priority, parent, match[1], clause))
    references.sort(key=lambda row: -row[0])
    selected_keys = {(r.get('source_id'), r.get('chunk_id')) for r in selected}
    details, seen_refs, output = {}, set(), []
    for _, parent, label, clause in references:
        source_id = parent['source_id']
        if (source_id, label) in seen_refs:
            continue
        seen_refs.add((source_id, label))
        if source_id not in details:
            details[source_id] = store.detail(source_id, limit=400)
        detail = details[source_id]
        if not detail or detail['source'].get('source_id') != source_id:
            continue
        source, chunks = detail['source'], detail['chunks']
        title = str(source.get('filename') or parent.get('title') or '')
        for index, chunk in enumerate(chunks):
            if chunk.get('source_id') != source_id:
                continue
            compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', source_text(chunk)))
            if not re.match(r'(?:[-—\d]*)?附录' + label + r'(?=[(（\u4e00-\u9fff])', compact):
                continue
            # Include only an explicitly named continuation of a table on the
            # appendix's opening page, not arbitrary neighbouring paragraphs.
            targets = [chunk]
            table_ids = set(re.findall(r'表(' + label + r'\.\d+)', compact))
            for next_chunk in chunks[index + 1:index + 3]:
                if next_chunk.get('source_id') != source_id:
                    break
                next_text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', source_text(next_chunk)))
                if not any(re.match(r'(?:[-—\d]*)?表' + re.escape(table_id) +
                                    r'.{0,100}[（(]续[)）]', next_text) for table_id in table_ids):
                    break
                targets.append(next_chunk)
            for target in targets:
                key = (source_id, target['chunk_id'])
                if key in selected_keys:
                    continue
                candidate = {**source, **target, 'source_id': source_id,
                             'title': title, 'text': source_text(target),
                             'url': parent.get('url'), 'sids': parent.get('sids', []),
                             'metadata': {**source.get('metadata', {}), **target.get('metadata', {})}}
                if not usable_source(candidate) or not reference_scope_allowed(question, candidate):
                    continue
                candidate['_evidence_digest'] = hashlib.sha256(candidate['text'].encode('utf-8')).hexdigest()
                candidate['retrieval'] = 'same_source_explicit_appendix_reference'
                candidate['metadata']['explicit_reference'] = {
                    'source_id': source_id, 'from_chunk_id': parent.get('chunk_id'),
                    'from_locator': parent.get('locator'), 'appendix': label,
                    'reference_clause': clause, 'target_locator': candidate.get('locator')}
                output.append(candidate)
                selected_keys.add(key)
                if len(output) >= maximum:
                    return output
            break
    return output
