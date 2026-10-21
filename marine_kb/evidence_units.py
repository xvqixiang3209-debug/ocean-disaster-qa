"""Versioned, exact evidence units shared by retrieval and answer validation.

Offsets refer to the acquired *extracted body*, not HTML bytes. Metadata never
certifies that a document is current and never supports its operative clauses.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def doc_card_sha256(card):
    """Hash all identity/provenance fields, including citation and source URL."""
    row = card.to_dict() if isinstance(card, DocCard) else card
    return digest(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def source_body(hit):
    return str(hit.get('source_text') or hit.get('text') or hit.get('content') or '')


@dataclass(frozen=True)
class DocCard:
    citation: int
    source_id: str
    title: str
    issuer: str | None
    doc_no: str | None
    published_at: str | None
    issued_at: str | None
    effective_from: str | None
    effective_to: str | None
    jurisdiction: str | None
    version_status: str
    url: str | None
    retrieved_at: str | None
    body_sha256: str
    derivation: dict
    document_names: tuple[str, ...] = ()
    parent_notice: dict | None = None
    acquisition_method: str | None = None
    reference_recheck_required: bool = False

    def to_dict(self):
        row = asdict(self)
        row['document_names'] = list(self.document_names)
        return row

    def render(self):
        return ('文件：' + self.title + '；机关：' + (self.issuer or '未核实') +
                '；文号：' + (self.doc_no or '未核实') +
                '；施行：' + (self.effective_from or '未在已取得正文中确认') +
                '；适用地区：' + (self.jurisdiction or '未核实') +
                '；现行状态：' + self.version_status +
                ('；人工参考件取得已超过180天，需复核' if self.reference_recheck_required else '') +
                '。本卡仅核对文件身份，不作为程序、金额或职责条款的依据。')


@dataclass(frozen=True)
class Passage:
    citation: int
    passage_id: int
    text: str
    start: int
    end: int
    section_path: tuple[str, ...]
    sha256: str

    def to_dict(self):
        row = asdict(self)
        row.update(id=self.passage_id, section_path=list(self.section_path), kind='body')
        return row


def _iso_date(match):
    try:
        return datetime(int(match[1]), int(match[2]), int(match[3])).date().isoformat()
    except (ValueError, TypeError):
        return None


def build_doc_card(hit):
    metadata = hit.get('metadata') or {}
    parent = metadata.get('parent_notice_doc_card') or hit.get('_parent_doc_card')
    if not isinstance(parent,dict): parent = None
    body = source_body(hit)
    title = str(hit.get('title') or hit.get('filename') or hit.get('page_title') or '')
    citation = int(hit.get('citation') or 0)
    derivation = {'title': 'title', 'body_sha256': 'extracted_body'}
    # Only title/edge lines supply identity. Attachment names elsewhere cannot
    # silently rename the document we actually fetched.
    spans = [(0, body[:1500])]
    if len(body) > 1500:
        spans.append((max(1500, len(body)-1500), body[max(1500, len(body)-1500):]))
    edge = '\n'.join(text for _, text in spans)
    issuer = None
    matched = re.match(r'^(.{2,45}?(?:人民政府办公厅|人民政府办公室|人民政府|办公厅|办公室|委员会|管理部|资源部|农业农村部|疾控中心|卫生健康委))关于', title)
    if matched:
        issuer = matched[1]; derivation['issuer'] = 'title'
    else:
        # A standalone official signatory line is safer than a prose mention.
        for base, text in reversed(spans):
            matches = list(re.finditer(r'(?m)^\s*([^\n。；]{2,40}(?:人民政府办公厅|人民政府|办公厅|委员会|管理部|资源部|农业农村部|疾控中心))\s*$', text))
            if matches:
                issuer = matches[-1][1].strip()
                derivation['issuer'] = 'body_line:' + str(base+matches[-1].start(1)); break
    if not issuer and (hit.get('issuer') or metadata.get('issuer')):
        issuer = str(hit.get('issuer') or metadata['issuer'])
        derivation['issuer'] = 'meta'
    no = re.search(r'[\u4e00-\u9fff]{1,12}[〔\[（(](?:19|20)\d{2}[〕\]）)]\s*\d+\s*号', title+'\n'+edge)
    doc_no = no[0] if no else None
    if no: derivation['doc_no'] = 'title' if no.start() < len(title) else 'body_edge'
    effective_from = None
    for base, text in spans:
        found = re.search(r'自\s*((?:19|20)\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日\s*起(?:施行|实施|执行)', text)
        if found and _iso_date(found):
            effective_from = _iso_date(found)
            derivation['effective_from'] = 'body_line:' + str(base+found.start()); break
    published = hit.get('published_at') or metadata.get('publication_date')
    issued = metadata.get('document_issued_at') or hit.get('_document_issued_at')
    if parent:
        if not issuer and parent.get('issuer'):
            issuer = parent['issuer']; derivation['issuer'] = 'parent_notice:' + str(parent.get('url') or '')
        if not doc_no and parent.get('doc_no'):
            doc_no = parent['doc_no']; derivation['doc_no'] = 'parent_notice:' + str(parent.get('url') or '')
        if not issued and parent.get('issued_at'):
            issued = parent['issued_at']; derivation['issued_at'] = 'parent_notice:' + str(parent.get('url') or '')
    effective_to = hit.get('effective_to') or metadata.get('effective_to')
    # Structured parser/manifest fields remain explicit metadata, never guesses
    # based on acquisition time or a year in the document title.
    for name, value in (('published_at', published), ('issued_at', issued), ('effective_to', effective_to)):
        if value: derivation.setdefault(name, 'meta')
    if not effective_from and hit.get('effective_from'):
        effective_from = hit['effective_from']; derivation['effective_from'] = 'meta'
    if not effective_from and metadata.get('effective_date'):
        try:
            effective_from = datetime.fromisoformat(str(metadata['effective_date'])).date().isoformat()
            derivation['effective_from'] = 'reviewed_metadata:effective_date'
        except (ValueError, TypeError):
            pass
    assessment = metadata.get('applicability') or {}
    temporal = assessment.get('temporal') or 'version_unverified'
    if metadata.get('effectiveness') == 'current_verified': temporal = 'current_verified'
    if temporal == 'version_unverified' and metadata.get('edition_current_verified') is True and metadata.get('edition_verification_url'):
        try:
            checked = datetime.fromisoformat(str(metadata.get('edition_checked_at', ''))).date()
            if 0 <= (datetime.now(timezone.utc).date() - checked).days <= 30:
                temporal = 'current_verified'
                derivation['version_status'] = 'official_registry:' + str(metadata['edition_verification_url']) + '; checked=' + checked.isoformat()
        except (ValueError, TypeError):
            pass
    jurisdiction = hit.get('jurisdiction') or metadata.get('jurisdiction')
    if jurisdiction:
        derivation['jurisdiction'] = 'meta'
    else:
        places = re.findall(r'浙江|宁波|舟山|温州|台州|杭州|福建|广东|上海|北京', title)
        if places: jurisdiction = places[0]; derivation['jurisdiction'] = 'title'
        elif re.search(r'国务院|国家|中华人民共和国|应急管理部|自然资源部|农业农村部', title):
            jurisdiction = 'national'; derivation['jurisdiction'] = 'title'
    names = tuple(dict.fromkeys(re.findall(r'《([^》]+)》', title)))
    if not names and re.search(r'预案|方案|办法|指南|规范|条例|保险法', title):
        names = (re.sub(r'\s+(?:\d{4}年.*|中国.*|公开版本|印发版本)$', '', title).strip(),)
    if names: derivation['document_names'] = 'title'
    acquisition_method = hit.get('extraction_method') or metadata.get('extraction_method')
    acquired = hit.get('read_at') or metadata.get('retrieved_at')
    stale_reference = False
    if (hit.get('read_from_reviewed_archive') or metadata.get('read_from_reviewed_archive')) and acquired:
        try:
            acquired_stamp = datetime.fromisoformat(str(acquired).replace('Z','+00:00'))
            if acquired_stamp.tzinfo is None: acquired_stamp = acquired_stamp.replace(tzinfo=timezone.utc)
            stale_reference = (datetime.now(timezone.utc)-acquired_stamp).days > 180
        except (ValueError,TypeError): pass
    return DocCard(citation, str(hit.get('source_id') or ''), title, issuer, doc_no,
                   published, issued, effective_from, effective_to, jurisdiction,
                   temporal, hit.get('url') or metadata.get('url'),
                   acquired, digest(body), derivation, names, parent,
                   acquisition_method, stale_reference)


def _section(line):
    line = line.strip()
    if len(line) > 100: return None
    number = re.match(r'^(\d+(?:[.．]\d+)+)(?:[.．、\s]|(?=[\u4e00-\u9fff]))\s*(.{2,65})$', line)
    if number: return number[1].count('.') + number[1].count('．') + 1, line
    if re.match(r'^(?:第[一二三四五六七八九十百\d]+[章节]|[一二三四五六七八九十]+[、．.]|\d+[、．.]\s*\D)', line):
        return 1, line
    if re.match(r'^[（(][一二三四五六七八九十\d]+[）)]', line): return 2, line
    return None


def build_passages(hit, selected_ids=None, *, version=2):
    body = source_body(hit)
    citation = int(hit.get('citation') or 0)
    selected = set(selected_ids) if selected_ids else None
    if version == 1:
        # The old algorithm remains the migration oracle for byte-identical P0
        # cassettes. Import at call time avoids a module-initialization cycle.
        from .qa import _evidence_passages
        text = str(hit.get('text') or '')[:3000]
        offset = 0; rows = []
        for row in _evidence_passages(text):
            start = text.find(row['text'], offset)
            start = max(0, start); offset = start+1
            rows.append(Passage(citation, row['id'], row['text'], start,
                                start+len(row['text']), (), digest(row['text'])))
        return [row for row in rows if selected is None or row.passage_id in selected]
    # Paragraphs and line headings stay in original order. Splitting oversized
    # blocks at sentence boundaries preserves local heading ancestry.
    rows, stack = [], []
    for part in re.finditer(r'[^\n]+(?:\n(?!\n)[^\n]+)*', body):
        start, end = part.span()
        while start < end:
            finish = min(end, start+900)
            if finish < end:
                stops = [m.end() for m in re.finditer(r'[。！？；\n]', body[start:finish])]
                if stops and stops[-1] >= 100: finish = start+stops[-1]
            text = body[start:finish]
            heading = _section(text.splitlines()[0])
            if heading:
                level, label = heading
                stack = [(depth, name) for depth, name in stack if depth < level]
                stack.append((level, label))
            if text.strip():
                rows.append(Passage(citation, len(rows)+1, text, start, finish,
                                    tuple(name for _, name in stack), digest(text)))
            start = finish
    return [row for row in rows if selected is None or row.passage_id in selected]


def evidence_view(hits, crag_plan=None, *, version=2):
    """Build one deterministic view; passage 0 is *identity only* in v2.

    ``crag_plan`` may be a citation->IDs mapping; otherwise selected IDs on
    each hit are used. Callers can omit selection for the initial CRAG review.
    """
    output = []
    for index, original in enumerate(hits, 1):
        hit = {**original, 'citation': index}
        selected = (hit.get('metadata') or {}).get('crag_passage_ids')
        if isinstance(crag_plan, dict):
            selected = crag_plan.get(index, crag_plan.get(str(index), selected))
        # Legacy pinned/no-evaluation hits record [] to mean "no restriction".
        # It must not silently erase the numerical body of a tool result.
        selected = selected or None
        if version == 1:
            from .qa import _evidence_passages
            text = str(hit.get('text') or '')[:3000]
            passages = _evidence_passages(text)
            if selected is not None: passages = [p for p in passages if p['id'] in selected]
            output.append({'citation': index, 'title': hit.get('title') or hit.get('filename'),
                           'locator': hit.get('locator'), 'status': hit.get('status'),
                           'metadata': hit.get('metadata') or {}, 'text': text,
                           'passages': passages})
            continue
        card = build_doc_card(hit)
        candidates = build_passages(hit, selected)
        if selected is None and hit.get('passages'):
            allowed = {p.get('id', p.get('passage_id')) for p in hit['passages'] if isinstance(p, dict)}
            candidates = [p for p in candidates if p.passage_id in allowed]
        identity = {'id': 0, 'passage_id': 0, 'text': card.render(),
                    'kind': 'document_identity', 'sha256': digest(card.render()),
                    'start': None, 'end': None, 'section_path': []}
        passages = [identity] + [row.to_dict() for row in candidates]
        output.append({'citation': index, 'title': card.title,
                       'locator': hit.get('locator'), 'status': hit.get('status'),
                       'metadata': hit.get('metadata') or {}, 'text': source_body(hit),
                       'doc_card': card.to_dict(), 'doc_card_sha256': doc_card_sha256(card),
                       'body_sha256': card.body_sha256,
                       'evidence_view_version': 2, 'passages': passages,
                       'view_sha256': digest(doc_card_sha256(card)+'\n'+'\n'.join(str(p['id'])+':'+p['sha256'] for p in passages))})
    return output


def document_identity_supports(claim, card):
    """Allow exact document names; never make metadata support an operative claim."""
    row = card.to_dict() if isinstance(card, DocCard) else card
    if re.search(r'\d+\s*(?:元|万元|亿元|人|户|天|日|米|厘米|m/s|%)|下达|审核|发放|申请|批准|决定|负责|支付|赔付|应当|必须', claim):
        return False
    names = re.findall(r'《([^》]+)》', claim)
    if names:
        return all(name in row.get('title', '') or name in row.get('document_names', []) for name in names)
    return False
