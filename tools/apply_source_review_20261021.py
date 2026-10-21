"""Apply the reviewed local batch and exact official public replacements.

--apply is explicit. Originals, drafts and unreviewed OCR remain retained.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.ingestion import split_units
from marine_kb.review_batch import apply_batch, sha256
from marine_kb.storage import KnowledgeStore

WORK = APP / 'tmp/source_review_20261021'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def standard_id(filename):
    match = re.search(r'(GB|HY)[ /_+∕-]*T[ /_+∕-]*(\d+(?:\.\d+)?)[—－-](\d{4})', filename, re.I)
    return f'{match[1].upper()}/T {match[2]}-{match[3]}' if match else None


def version_cards():
    cards = dict(load(WORK / 'official/gb_status_summary.json'))
    for row in load(WORK / 'version_audit.json'):
        audit = row['version_audit']
        if audit['version_status'] not in {'official_current', 'draft_formal_successor_identified_body_missing'}:
            continue
        for evidence in audit.get('evidence', []):
            if evidence.get('standard_number') and evidence.get('official_state'):
                evidence = dict(evidence)
                evidence.setdefault('identity_matched', True)
                evidence.setdefault('title', evidence['standard_number'] + '正式标准')
                cards.setdefault(evidence['standard_number'], evidence)
    return cards


def prepare():
    recovery = load(WORK / 'ocr/recovery_plan.json')
    versions = version_cards()
    audit_rows = {x['source_id']: x for x in load(WORK / 'version_audit.json')}
    evidence = WORK / 'doc_comparison.json'
    docs = []
    comparisons = []
    store = KnowledgeStore(APP / 'data/marine_kb/knowledge.sqlite3')
    for item in recovery['documents']:
        parsed = item.get('parsed')
        if not parsed:
            continue
        metadata = {k: v for k, v in parsed['metadata'].items()
                    if k not in {'external_allowed', 'external_permission_override'}}
        audit = audit_rows[item['source_id']]['version_audit']
        metadata['version_audit_20261021'] = audit
        if audit.get('formal_standard_number'):
            metadata['formal_successor_standard_id'] = audit['formal_standard_number']
        if audit['version_status'] == 'superseded_original_keep_historical':
            metadata.update(edition_current_verified=False, edition_status='historical')
        number = standard_id(item['filename'])
        if number in versions:
            verified = versions[number]
            metadata.update(standard_id=number, edition_current_verified=verified['official_state'] == '现行',
                            edition_status='current' if verified['official_state'] == '现行' else 'unverified',
                            edition_verification_url=verified['evidence_url'], edition_checked_at='2026-10-21',
                            publication_date=verified.get('publication_date'), effective_date=verified.get('effective_date'))
        chunks = split_units(parsed['units'])
        decision = 'repair_only'
        verification = None
        note = '本地逐物理页补提取/OCR恢复，保留未核页面、公式和表格的隔离；本操作不升级为全文批准。'
        if item['source_id'] in {'18b1dc484ab4409f836e8ebb', 'a7e3e3d4986f6401487eb0b2'}:
            old = store.detail(item['source_id'])['chunks']
            equal = [(x['locator'], x['text']) for x in chunks] == [(x['locator'], x['text']) for x in old]
            if not equal:
                raise ValueError('management_doc_reparse_differed')
            comparisons.append({'source_id': item['source_id'], 'source_sha256': item['source_sha256'],
                                'stored_chunk_count': len(old), 'sha_bound_word_cache_equal': equal,
                                'reviewed_text_scope': '完整正文，无表格或嵌图；root已读全部条文。'})
            decision = 'approve'
            note = '已核对原件SHA绑定的Word转换正文与已存条文逐段一致，无表格或嵌图；按2017年公布版本准入参考。现行适用性另按官方修订文本核查。'
            metadata.update(conversion_verified=True, review_scope='versioned_reference', complete_text_read=True,
                            edition_current_verified=False, edition_status='historical',
                            publication_date='2017-06-07', effective_date='2017-06-07')
            if item['source_id'] == '18b1dc484ab4409f836e8ebb':
                metadata['superseded_by_edition'] = '2019年修正文本'
        docs.append({'source_id': item['source_id'], 'expected_sha256': item['source_sha256'],
                     'expected_updated_at': item['previous_updated_at'], 'decision': decision,
                     'chunks': chunks, 'metadata_patch': metadata, 'quality_status': parsed['status'],
                     'warnings': parsed['warnings'], 'note': note, 'review_scope': 'versioned_reference'})
    save(evidence, comparisons)
    for item in docs:
        if item['decision'] == 'approve':
            item['verification'] = {'method': 'native_text_integrity', 'artifacts': [{'path': str(evidence), 'sha256': sha256(evidence)}]}
    return {'batch_id': 'text-review-' + datetime.now().strftime('%Y%m%d-%H%M%S'),
            'authorization': '项目负责人2026-10-21明确要求核查审核文字资料，补OCR核正式版本，再处理数值和岸段，完成问答测试。',
            'items': docs}


def import_text(store, *, title, text, metadata, units=None, note, category='标准规范'):
    data = text.encode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    raw = store.database.parent / 'files/raw' / (digest + '.txt')
    if raw.exists() and raw.read_bytes() != data:
        raise ValueError('immutable_collision')
    if not raw.exists():
        raw.write_bytes(data)
    identity, created = store.register(filename=title + '.txt', relative_path='核查补充_20261021/' + title + '.txt',
                                       sha256=digest, size=len(data), raw_path=raw, category=category, metadata=metadata)
    if created:
        chunks = split_units(units or [{'locator': '官方正文', 'text': text}])
        store.finish(identity, status='pending_review', quality_status='parsed', metadata=metadata,
                     warnings=[metadata['scope_note']] if metadata.get('scope_note') else [], chunks=chunks)
        store.review(identity, 'approved', note)
    return {'source_id': identity, 'created': created, 'title': title, 'scope': metadata.get('scope_note')}


def admit_public(store):
    added = []
    for item in load(WORK / 'public_sources.json')['public_sources']:
        body = Path(item['body_file'])
        if sha256(body) != item['body_sha256'] or sha256(item['raw_file']) != item['raw_sha256']:
            raise ValueError('official_body_integrity_failed')
        current = item['version_status'] in {'official_consolidated_revision', 'official_consolidated_revision_valid_on_page', 'official_published_current_replaces_2022'}
        text = body.read_text(encoding='utf-8')
        meta = {'source_type': 'official_public_text', 'external_allowed': True, 'is_draft': False,
                'publication_date': item.get('document_issued_at'), 'effective_date': item.get('effective_date'),
                'document_issued_at': item.get('document_issued_at'), 'edition_status': 'current' if current else 'versioned_reference',
                'edition_current_verified': current, 'edition_checked_at': '2026-10-21',
                'edition_verification_url': item['url'], 'source_url': item['url'], 'read_status': 'read',
                'review_scope': 'versioned_reference', 'complete_text_read': True,
                'review_method': 'official_public_text_comparison', 'source_capture_sha256': item['raw_sha256'],
                'scope_note': item.get('scope_note') or '官方所列版本正文；版本有效性与上位法变更仍按具体条款核对。'}
        if item.get('native_table_count'):
            meta.update(complete_text_read=False, embedded_image_count=item.get('embedded_image_count', 0))
        # One article per unit avoids losing its actor/condition to arbitrary
        # page boundaries; long articles still use the normal chunker.
        pieces = re.split(r'(?=第[一二三四五六七八九十百]+条\s)', text)
        units = [{'locator': '官方正文·' + (re.match(r'第[一二三四五六七八九十百]+条', piece)[0]
                  if re.match(r'第[一二三四五六七八九十百]+条', piece) else '文件身份'), 'text': piece} for piece in pieces if piece.strip()]
        if '公报' in item['title']:
            meta.update(review_scope='historical_reference', event_year=2020, edition_current_verified=False,
                        edition_status='published_historical', scope_note=item.get('scope_note') or '2020年正式历史公报原生正文；图中数值未单独复核。')
        added.append(import_text(store, title=item['title'], text=text, metadata=meta, units=units,
                                 category='灾害公报' if '公报' in item['title'] else '标准规范',
                                 note='官方公开正文已取得并核对标题、文件版本和来源哈希；仅准入正文/原生表格，未识别图中内容不作依据。'))
    versions = version_cards()
    for number, item in versions.items():
        if not item.get('identity_matched') or sha256(item['evidence_path']) != item['evidence_sha256']:
            raise ValueError('official_standard_identity_failed')
        text = (f"标准号：{number}\n标准名称：{item['title']}\n发布日期：{item['publication_date']}\n"
                f"实施日期：{item['effective_date']}\n官方标准状态：{item['official_state']}\n"
                f"查询日期：2026-10-21\n官方来源：{item['evidence_url']}\n"
                '本记录仅为官方文档身份卡，不含标准正文条款；不能据此推断阈值、公式或操作要求。')
        meta = {'source_type': 'official_document_card', 'external_allowed': True, 'is_draft': False,
                'standard_id': number, 'publication_date': item['publication_date'], 'effective_date': item['effective_date'],
                'edition_status': 'current', 'edition_current_verified': item['official_state'] == '现行',
                'edition_checked_at': '2026-10-21', 'edition_verification_url': item['evidence_url'],
                'source_url': item['evidence_url'], 'review_scope': 'versioned_reference',
                'complete_text_read': True, 'scope_note': '仅支持文档名称、编号、日期和标准状态；不含正文条款。'}
        added.append(import_text(store, title=number.replace('/', '_') + '_' + item['title'] + '_官方身份卡',
                                 text=text, metadata=meta, note='精确标准号及名称已与官方标准目录详情页核对，批准身份信息使用范围。'))
    return added


def admit_excerpts(store):
    versions = version_cards()
    added = []
    for path in sorted((WORK / 'ocr').glob('*manifest.json')):
        item = load(path)
        parent = store.get_source(item['parent_source_id'])
        if not parent or parent['sha256'] != item['parent_sha256'] or item['review_method'] != 'rendered_page_verification':
            raise ValueError('reviewed_excerpt_parent_changed')
        for image in item['source_images']:
            if image['page'] not in item['reviewed_pages'] or sha256(image['path']) != image['sha256']:
                raise ValueError('reviewed_page_artifact_changed')
        if any(x['page'] not in item['reviewed_pages'] for x in item['excerpts']):
            raise ValueError('excerpt_page_not_reviewed')
        text = '\n\n'.join(x['locator'] + '\n' + x['text'] for x in item['excerpts'])
        number = item.get('standard_id')
        version = versions.get(number, {})
        # Preserve explicit parent permission. Default closed sources remain
        # closed; public-page evidence and existing authorized exact bytes are
        # handled independently by the established model authorizer.
        meta = {'source_type': 'official_standard_excerpt' if number else 'reviewed_historical_excerpt',
                'external_allowed': parent['metadata'].get('external_allowed', False), 'is_draft': False,
                'parent_source_id': parent['source_id'], 'parent_sha256': parent['sha256'],
                'parent_filename': parent['filename'], 'reviewed_pages': item['reviewed_pages'],
                'review_manifest_sha256': sha256(path), 'review_method': 'rendered_page_verification',
                'review_scope': 'versioned_reference' if number else 'historical_reference',
                'complete_text_read': True, 'standard_id': number,
                'edition_status': 'current' if version else 'historical',
                'edition_current_verified': bool(version and version['official_state'] == '现行'),
                'edition_checked_at': '2026-10-21', 'edition_verification_url': version.get('evidence_url'),
                'scope_note': item['note'], 'event_year': item.get('event_year'),
                'applicable_stage': item.get('applicable_stage')}
        if 'external_permission_override' in parent['metadata']:
            meta['external_permission_override'] = parent['metadata']['external_permission_override']
        added.append(import_text(store, title=Path(item['filename']).stem, text=text, metadata=meta,
                                 units=[{'locator': x['locator'], 'text': x['text'], 'metadata': {'parent_pdf_page': x['page']}} for x in item['excerpts']],
                                 category=item.get('category', '标准规范'), note='已逐页核对：仅批准所列物理页、章条文字节选；原文件全文未升级批准。'))
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    plan = prepare()
    save(WORK / 'application_plan.json', plan)
    print(json.dumps({'sources': len(plan['items']), 'approve_originals': sum(x['decision'] == 'approve' for x in plan['items']),
                      'apply': args.apply}, ensure_ascii=False), flush=True)
    if not args.apply:
        return
    db = APP / 'data/marine_kb/knowledge.sqlite3'
    result = apply_batch(db, plan, backup_dir=db.parent / 'review_backups')
    store = KnowledgeStore(db)
    result['public_additions'] = admit_public(store)
    result['excerpt_additions'] = admit_excerpts(store)
    save(WORK / 'application_result.json', result)
    print(json.dumps({'applied': len(result['items']), 'added': len(result['public_additions']), 'backup': result['backup']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
