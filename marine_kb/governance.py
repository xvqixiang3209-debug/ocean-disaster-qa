"""Official-source admission is separate from current-rule certification."""
import re
import unicodedata
from datetime import date


def draft_markers(relative, text):
    pattern = r'报批稿|征求意见稿|上报版|HY/T\s*[×xX](?:\s*[×xX]){1,}|草案'
    # A published notice can instruct staff to consult a draft. That does not
    # make the notice itself a draft; retain the reference separately.
    referenced = re.compile(r'(?:按照|依据|根据|参照|遵循|引用)\s*《[^》]{0,250}(?:征求意见稿|报批稿|草案)[^》]{0,80}》')
    reference_found = bool(referenced.search(text))
    cleaned = referenced.sub('〔引用文件，版本另行标注〕', text)
    return {'is_draft':bool(re.search(pattern, relative+'\n'+cleaned)),
            'references_draft':reference_found}


def reference_scope_allowed(question, item, *, allow_versioned_explanation=False):
    """Unknown currency may support a versioned explanation, not default rules."""
    meta = item.get('metadata') or {}
    if meta.get('review_scope') != 'versioned_reference':
        return True
    overview=bool(re.search(r'产品.*(?:格式|业务标准|制作)|(?:标准|规范).*(?:有哪些|目录|介绍)|有哪些.*(?:标准|规范)|质控|技术流程|技术方法|具体方法',question))
    if overview and not re.search(r'现行|当前|最新|现在|目前|法定|必须|应当|今天|截至',question):
        # A versioned explanation/catalogue is useful without certifying
        # currency. Pending sources remain gated by the retrieval layer.
        return True
    if allow_versioned_explanation and not re.search(r'现行|当前|最新|现在|目前|法定|必须|应当|今天', question):
        return True
    rule = re.search(r'标准|规范|条款|阈值|法定|分级条件|判定条件|发布条件|启动条件|应急响应|警戒潮位|警报等级', question)
    if not rule:
        return True
    if meta.get('edition_current_verified') is True:
        try:
            age = (date.today()-date.fromisoformat(meta.get('edition_checked_at',''))).days
            if 0 <= age <= 30:
                return True
        except (TypeError,ValueError):
            pass
    normalized = unicodedata.normalize('NFKC', question)
    identity = str(meta.get('standard_id') or '')
    compact = re.sub(r'[\s/+_—–－-]', '', normalized).lower()
    code = re.sub(r'[\s/+_—–－-]', '', identity).lower()
    if code and code in compact:
        return True
    title = re.sub(r'\.(?:pdf|docx?|wps|txt|md)$', '', str(item.get('title') or item.get('filename') or ''), flags=re.I)
    title = re.sub(r'^\d+', '', title)
    books = re.findall(r'《([^》]{4,100})》', question)
    if books and any(book in title for book in books) and re.search(r'这份|该文件|所提供|文件中|原文|历史|当时|\d{4}年.*(?:版|公布|发布|修订|修正)', question):
        return True
    return bool(len(title) >= 6 and title in question and re.search(r'这份|该文件|所提供|文件中|原文|历史|当时|\d{4}年', question))
