"""Rank extracted evidence for relevance and facet coverage, never search order."""
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit

from .answer_design import is_policy_lookup, response_parts
from .retrieval import STORM_ALIASES, lexical_terms, source_text, evidence_focus, _named_clause_focus
from .evidence_applicability import facet_support


TOPICS = ('浮标', '海洋观测', '雷达', '卫星', '风暴增水', '天文潮', '风暴潮', '有效波高', '警戒潮位', '海洋灾害', '应急响应',
          '海平面', '潮位基准', '海水倒灌', '海啸', '海浪', '台风', '赤潮', '海冰', '涌浪', '风浪',
          '潮位', '潮汐', '海温', '海流', '五停', '停课', '停工', '停产', '停运', '停业')
PLACES = ('浙江', '上海', '福建', '广东', '海南', '广西', '江苏', '山东', '河北', '天津', '辽宁',
          '宁波', '舟山', '温州', '台州', '杭州', '自然资源部')
ALIASES = {'海浪': ('wave',), '海啸': ('tsunami',), '台风': ('typhoon', 'tropicalcyclone'),
           '风暴潮': ('stormsurge',), '有效波高': ('significantwaveheight',), '海冰': ('seaice',)}
FACETS = [(r'降级|降低', ('降级', '降低', '调整', '启动标准')),
          (r'终止|结束响应|解除响应', ('终止', '结束', '解除')),
          (r'程序|流程|审批|由谁|决定', ('签发', '批准', '决定', '办公室', '通知', '程序')),
          (r'公告|通告|公布', ('公告', '通告', '发布', '短信', '广播')),
          (r'防范|防御|避险|措施', ('防范', '避险', '应对', '措施', '转移', '撤离')),
          (r'定义|什么是|是什么|区别', ('是指', '指的是', '称为', '定义', '由', 'is', 'are'))]


def _compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text))).lower()


def _targets(question):
    return [term for term in TOPICS if term in question and not any(
        term != longer and term in longer and longer in question for longer in TOPICS)]


def _score(question, hit):
    from .query_intents import support_score
    from .public_sources import primary_routes
    applicability = hit.get('metadata', {}).get('applicability', {})
    if applicability.get('use') == 'excluded':
        return None
    body, title = _compact(source_text(hit)), _compact(hit.get('title', ''))
    words = lexical_terms(question)
    overlap = sum(word in body for word in words)
    title_overlap = sum(word in title for word in words)
    topics = _targets(question)
    matched = [topic for topic in topics if topic in body or
               hit.get('named_document_match') and topic in title or
               any(alias in body for alias in ALIASES.get(topic, ()))]
    primary=any(row['url']==hit.get('url') for row in primary_routes(question))
    if not body or (topics and not matched and not primary) or (not topics and not overlap and not primary):
        return None
    if '海洋灾害' in question and not re.search(r'海洋|海浪|风暴潮|海啸|海冰|赤潮', body) and not primary:
        return None
    if response_parts(question) and not evidence_focus(question, source_text(hit)):
        return None
    if re.search(r'基础问题库|需求清单|搜索结果|站点地图', str(hit.get('title', ''))):
        return None
    score = 4 * overlap / max(1, len(words)) + 2 * title_overlap / max(1, len(words))
    if primary:score+=5
    score += support_score(question, source_text(hit), str(hit.get('title', '')))
    if (hit.get('metadata',{}).get('source_type')=='official_public_knowledge'
            and support_score(question,source_text(hit),str(hit.get('title','')))>0):
        score += 3  # Explicit general explanations ahead of incidental annual statistics.
    score += 3 * len(matched) / max(1, len(topics))
    if re.search(r'定义|概念|区别|什么是|是什么',question) and re.search(r'定义|什么是|是什么',title):
        score += 4
    for pattern, names in FACETS:
        if re.search(pattern, question):
            score += min(2, sum(name in body for name in names)) * .5
    places = [place for place in PLACES if place in question]
    if places:
        score += 2 if any(place in title or place in body for place in places) else -1
    if is_policy_lookup(question):
        score += 1 if re.search(r'预案|办法|通知|条例|规范|工作指引', str(hit.get('title', ''))) else -.5
    if hit.get('metadata', {}).get('parent_url'):
        score += .4  # Operative text ahead of a short publication notice.
    score += 2 if applicability.get('use') == 'direct' else -1 if applicability.get('use') == 'reference' else 0
    score += _named_clause_focus(question, hit)['score']
    requested_storms=[(chinese,english) for chinese,english in STORM_ALIASES.items()
                      if chinese in question or english in question.lower()]
    # Separate event reports contribute separate sides of a comparison. A
    # candidate need only describe a requested event; it does not thereby
    # support the other event. Facet coverage and answer attribution still
    # operate on each source independently.
    if requested_storms and not any(chinese in body+title or english in body+title
                                   for chinese,english in requested_storms):
        return None
    return round(score, 4)


def select_evidence(question, hits, parts=None, limit=4):
    """Greedy facet diversity on top of body relevance; do not fill with noise."""
    question = question.replace('天文大潮', '天文潮')
    targets = [part['question'] if isinstance(part, dict) else part for part in (parts or [])]
    targets += _targets(question)
    candidates, seen_text, seen_url = [], set(), set()
    for index, hit in enumerate(hits):
        text = _compact(source_text(hit))
        digest = hashlib.sha256(text.encode()).hexdigest()
        # Local chapters share their source-view URL. Deduplicating that URL
        # drops every other chapter and loses evidence for sibling questions.
        # Web pages have already been focused into one multi-facet excerpt.
        url = hit.get('url') if (hit.get('source_type')=='public_web' or
                hit.get('metadata',{}).get('source_type')=='public_web') else None
        if url:
            parsed = urlsplit(url)
            url = (parsed.netloc.lower(), parsed.path, parsed.query)
        if digest in seen_text or url and url in seen_url:
            continue
        # Each child can contribute evidence even when it cannot answer the
        # other children. Scope and facet checks remain in force per child.
        child_targets=[part['question'] if isinstance(part,dict) else part for part in (parts or [])]
        scores = [_score(question, hit)] + [_score(
                    ('海洋灾害 '+target) if '海洋灾害' in question and '海洋灾害' not in target else target, hit)
                    for target in child_targets if facet_support(question, target, hit)]
        scores = [value for value in scores if value is not None]
        if not scores:
            continue
        score = max(scores)
        seen_text.add(digest)
        if url:
            seen_url.add(url)
        covered = set()
        for facet_index, target in enumerate(targets):
            facet_score = _score(target, hit)
            clause = _named_clause_focus(target, hit)
            if clause['duration_requested'] and not clause['duration_supported']:
                # A named act's title/date cannot cover all its deadline
                # siblings. Each needs a body predicate and an actual duration.
                continue
            if target in TOPICS and re.search(r'定义|概念|区别|什么是|是什么',question):
                # Incidental mention in a storm bulletin cannot consume a
                # concept's coverage slot ahead of its actual definition page.
                body=source_text(hit)
                explicit = (target in str(hit.get('title','')) and
                            re.search(r'定义|什么是|是什么',str(hit.get('title','')))) or re.search(
                    re.escape(target)+r'(?:[（(][^）)]{0,60}[）)])?(?:是|指|为|由|和.{1,12}都是)',body)
                if not explicit and not any(alias in body.lower() for alias in ALIASES.get(target,())):
                    continue
            if facet_score is not None and facet_score >= 1.5 and facet_support(question, target, hit):
                covered.add(facet_index)
        candidates.append((score, index, covered, hit))
    selected, covered = [], set()
    while candidates and len(selected) < limit:
        best = max(candidates, key=lambda row: (row[0] + 1.5 * len(row[2] - covered), -row[1]))
        candidates.remove(best)
        score, _, facets, hit = best
        selected.append({**hit, 'selection_score': score})
        covered |= facets
    return selected
