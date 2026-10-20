"""Conservative relation alarms. The lexicon detects errors; it is not evidence.

This guard never supplies disease advice or legal powers from model memory.
It reports exact draft spans for local rechecking against the cited body.
"""
import json
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse


LEXICON = Path(__file__).resolve().parents[1] / 'data/domain/relation_lexicon.json'


def load_lexicon(path=LEXICON):
    rows = json.loads(Path(path).read_text(encoding='utf-8'))['relations']
    return [r for r in rows if r.get('enabled') and r.get('source_url') and
            r.get('checked_at') and r.get('checked_by') and r.get('excerpt') and r.get('review_method') and
            r.get('excerpt_sha256')==hashlib.sha256(r['excerpt'].encode()).hexdigest()]


def health_authority(source):
    metadata = source.get('metadata') or {}
    card = source.get('doc_card') or {}
    if hasattr(card, 'to_dict'):
        card = card.to_dict()
    # Evidence v2 moves the canonical acquisition URL onto the document card.
    # Prefer that identity over legacy hints: an unrelated trusted link in
    # metadata must not certify a page fetched from an untrusted host.
    canonical = card.get('url') or source.get('url') or metadata.get('url') or ''
    host = (urlparse(str(canonical)).hostname or '').lower().rstrip('.')
    trusted = ('nhc.gov.cn', 'chinacdc.cn', 'ndcpa.gov.cn')
    if any(host == d or host.endswith('.'+d) for d in trusted):
        return True
    if re.fullmatch(r'(?:wsjkw|wjw|cdc|jkzx)\.[a-z0-9.-]+\.gov\.cn',host):return True
    origin = str(metadata.get('origin_issuer') or metadata.get('issuer') or card.get('issuer') or '')
    return host.endswith('.gov.cn') and bool(re.search(r'卫生健康|疾病预防控制|疾控中心', origin))


def _diseases(text, rows):
    found = []
    for row in rows:
        aliases = row.get('aliases') or [row['disease']]
        if any(alias in text for alias in aliases):
            found.append(row)
    # Generic 出血热 cannot equate dengue haemorrhagic fever with HFRS.
    if '登革出血热' in text and not re.search(r'肾综合征出血热|流行性出血热', text):
        found = [r for r in found if r['key'] != 'hfrs']
    return found


def check(answer, facets=(), evidence=(), *, lexicon=None):
    """Return alarms only; scope comes from typed facets and exact source refs."""
    rows = lexicon if lexicon is not None else load_lexicon()
    atoms = answer if isinstance(answer, list) else [{'aid': 'answer', 'text': str(answer)}]
    question = '\n'.join(str(f.get('question') or '') for f in facets)
    mosquito_only = bool(re.search(r'防蚊|蚊虫|蚊媒|蚊子', question)) and not re.search(r'鼠|钩体|出血热|所有传染病', question)
    issues = []
    for atom in atoms:
        text = atom.get('text') or atom.get('display_text') or ''
        chemical = re.search(r'敌敌畏|氯氰菊酯|溴氰菊酯|吡丙醚|双硫磷|灭幼脲|氯菊酯|倍硫磷|次氯酸钠|漂白粉|'
                             r'(?:杀虫|消毒|灭蚊|药剂)[^。\n]{0,20}\d+(?:\.\d+)?\s*(?:%|％|ppm|mg/L|毫克|毫升|倍液)',text,re.I)
        asked_dose=bool(re.search(r'用药|用什么药|药剂|浓度|配药|配制|配比|剂量|稀释|消毒剂|消毒液|漂白粉|杀虫剂',question))
        if chemical and not asked_dose and any(f.get('kind') in {'risk_relation','method_steps'} for f in facets):
            issues.append({'code':'relation_unasked_chemical_dose','aid':atom.get('aid'),'text':chemical[0],
                'start':chemical.start(),'end':chemical.end(),'facet_ids':atom.get('facet_ids') or [],
                'severity':'recheck','instruction':'本题未问用药，删除药剂名称和配药浓度剂量，保留清除积水、纱窗蚊帐等有据措施。'})
        for match in re.finditer(r'[^。！？\n]+[。！？]?', text):
            span = match[0]
            diseases = _diseases(span, rows)
            if not diseases:
                continue
            codes = set()
            # Keep explicit corrections such as “钩体病不是蚊媒病” intact.
            corrective = bool(re.search(r'不是蚊媒|并非蚊媒|不由蚊|不是通过蚊|不能把.{0,30}当[作成]?.{0,8}蚊', span))
            non_mosquito = [r for r in diseases if r['route'] != 'mosquito']
            if non_mosquito:
                # Bind the carrier and disease inside the same clause. The
                # joint sentence “鼠类传播出血热，蚊虫传播登革热” has two
                # valid relations; seeing both entities somewhere in it is
                # not evidence that mosquitoes spread haemorrhagic fever.
                active_vector = None
                for clause in re.split(r'[，,；;]', span):
                    joint = re.search(r'鼠类(?:和|与|及|、)蚊虫|鼠(?:和|与|及)蚊', clause)
                    mosquito = re.search(r'蚊虫|蚊子|蚊媒|伊蚊|库蚊', clause)
                    rodent = re.search(r'鼠类|老鼠|鼠媒', clause)
                    if joint:
                        active_vector = 'joint'
                    elif mosquito and not rodent:
                        active_vector = 'mosquito'
                    elif rodent and not mosquito:
                        active_vector = 'rodent'
                    wrong_disease = [r for r in _diseases(clause, rows) if r['route'] != 'mosquito']
                    if not wrong_disease:
                        continue
                    clause_correction = bool(re.search(r'不是蚊媒|并非蚊媒|不由蚊|不是通过蚊|不能把.{0,30}当[作成]?.{0,8}蚊', clause))
                    if clause_correction:
                        continue
                    if active_vector == 'mosquito' and re.search(r'传播|传染|引起|导致', clause):
                        codes.add('relation_vector_mismatch')
                    if mosquito_only and active_vector == 'joint':
                        codes.add('relation_mixed_vector_context')
            refs = [int(n) for n in re.findall(r'\[资料(\d+)\]', span)]
            asserted = bool(re.search(r'传播|传染|引起|导致|媒介|携带', span))
            if asserted and refs and not any(1 <= n <= len(evidence) and health_authority(evidence[n-1]) for n in refs):
                codes.add('relation_health_source_required')
            for code in sorted(codes):
                issues.append({'code': code, 'aid': atom.get('aid'), 'text': span,
                               'start': match.start(), 'end': match.end(),
                               'facet_ids': atom.get('facet_ids') or [], 'severity': 'recheck',
                               'instruction': '核对该句疾病与传播途径的对应关系；按所问对象拆开说明，仅保留引用正文支持的关系。'})
        # Role checks use the cited body, not a registry of supposed powers.
        for m in re.finditer(r'([^。；\n]{2,35}?(?:办公室|指挥部|政府|主管部门))\s*(提出建议|批准|审批|决定|下达)', text):
            subject, predicate = m[1], m[2]
            refs = [int(n) for n in re.findall(r'\[资料(\d+)\]', text)]
            cited = '\n'.join(str(evidence[n-1].get('text') or '') for n in refs if 1 <= n <= len(evidence))
            # Explicit opposite role in the same source warrants rechecking.
            opposite = '批准|审批|决定' if predicate == '提出建议' else '提出建议'
            if cited and re.search(re.escape(subject)+r'.{0,8}(?:'+opposite+r')', cited) and not re.search(re.escape(subject)+r'.{0,8}'+predicate, cited):
                issues.append({'code':'relation_actor_role_mismatch','aid':atom.get('aid'),
                               'text':m[0],'start':m.start(),'end':m.end(),
                               'facet_ids':atom.get('facet_ids') or [],'severity':'recheck',
                               'instruction':'分别核对提出建议、审批和下达三个动作及其责任主体。'})
    return issues
