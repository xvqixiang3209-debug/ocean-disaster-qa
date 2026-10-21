"""Read-only review groups derived from retained source metadata.

These labels describe the next review step. They never grant approval,
current-edition status, external permissions, or retrieval eligibility.
"""
from collections import Counter
import json


GROUPS = (
    {'id': 'structured', 'label': '原值已恢复、业务口径待确认',
     'description': '原始数值或岸段双版本已恢复，单位、时间、基准或业务采用版本仍需确认。'},
    {'id': 'partial', 'label': '部分内容已审核',
     'description': '已有独立批准的原页节选；原文件全文仍待核对。'},
    {'id': 'fulltext', 'label': '全文待核对',
     'description': '尚未批准全文，仍需核对正文、公式、表格及适用范围。'},
    {'id': 'version', 'label': '版本待确认',
     'description': '正式版本、后继版本、发布稿或当前效力仍需确认。'},
)
GROUP_IDS = frozenset(item['id'] for item in GROUPS)
GROUP_LABELS = {item['id']: item['label'] for item in GROUPS}
NOTE = '仅对专业资料库的待复核来源记录按下一步工作互斥分组；分组不改变审核状态、检索准入或外发授权，历史资料库不参与分组。'
VERSION_BLOCKERS = frozenset({
    'unverified', 'draft_formal_successor_identified_body_missing',
    'superseded_original_keep_historical', 'annual_bulletin_submission_final_missing',
    'official_published_edition_current_status_unconfirmed',
})


def metadata_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def validate_review_group(value):
    if value and (not isinstance(value, str) or value not in GROUP_IDS):
        raise ValueError('未知的待复核分组')
    return value or ''


def _linked_count(metadata, key):
    value = metadata.get(key)
    return len(value) if isinstance(value, list) else 0


def _version_status(metadata):
    # The retained October audit is date-qualified. Also accept the generic
    # field for subsequent imports, without treating arbitrary keys as audits.
    for key in ('version_audit_20261021', 'version_audit'):
        audit = metadata.get(key)
        if isinstance(audit, dict) and isinstance(audit.get('version_status'), str) and audit['version_status']:
            return audit['version_status']
    return ''


def classify_review_group(status, metadata=None, *, library='marine', source_id=''):
    if status != 'pending_review' or library == 'legacy' or str(source_id).startswith('legacy:'):
        return None
    meta = metadata_dict(metadata)
    state = meta.get('structured_archive_state')
    if state == 'raw_values_recovered':
        group, reason = 'structured', '原始数值已恢复；单位、完整日期和基准待核。'
    elif state == 'coast_versions_recovered':
        group, reason = 'structured', '岸段双版本已对照；冲突阈值及业务采用版本待核。'
    else:
        version_status = _version_status(meta)
        blocked = (version_status in VERSION_BLOCKERS or
                   meta.get('is_draft') is True and meta.get('edition_current_verified') is not True)
        if blocked:
            group, reason = 'version', '正式版本、发布稿或当前效力尚未核定；需确认版本后继续复核。'
            if _linked_count(meta, 'approved_excerpt_source_ids'):
                reason += '已有独立批准的节选，原文件版本问题仍待确认。'
        elif _linked_count(meta, 'approved_excerpt_source_ids'):
            group, reason = 'partial', '已有独立批准的原页节选；原文件全文尚未批准。'
        else:
            group, reason = 'fulltext', '正文、公式、表格及适用范围仍待完整核对。'
    return {'id': group, 'label': GROUP_LABELS[group], 'reason': reason}


def review_group_id(status, metadata):
    group = classify_review_group(status, metadata)
    return group['id'] if group else None


def review_progress(status, metadata=None, *, library='marine', source_id=''):
    """Return only generated display text, never retained paths or metadata."""
    if library == 'legacy' or str(source_id).startswith('legacy:'):
        return ''
    meta = metadata_dict(metadata)
    if status == 'archived':
        return '正式替代正文已入库；旧原件与切片留档，不用于默认问答'
    if status == 'approved' and meta.get('normative_body_verified') is True:
        return '规范性业务正文及附录已核；非业务前置/参考内容保留在原件'
    if meta.get('source_type') == 'official_document_card':
        return '已核文档身份；不含正文条款'
    if meta.get('source_type') in ('official_standard_excerpt', 'reviewed_historical_excerpt'):
        return '已核原页节选；原文件全文状态另列'
    if meta.get('structured_archive_state') == 'raw_values_recovered':
        return '原始数值已恢复；单位、完整日期和基准待核'
    if meta.get('structured_archive_state') == 'coast_versions_recovered':
        return '岸段双版本已对照；冲突阈值待核'
    notes = []
    if _linked_count(meta, 'formal_replacement_source_ids'):
        notes.append('正式替代正文已入库')
    excerpts = _linked_count(meta, 'approved_excerpt_source_ids')
    if excerpts:
        notes.append('已核节选 ' + str(excerpts) + ' 份')
    if status == 'pending_review' and meta.get('edition_current_verified') is True:
        notes.append('官方版本已查证，正文仍待核')
    return '；'.join(notes)


def group_summary(counts):
    counts = Counter(counts)
    return {'total': sum(counts.get(item['id'], 0) for item in GROUPS),
            'groups': [{**item, 'count': counts.get(item['id'], 0)} for item in GROUPS], 'note': NOTE}
