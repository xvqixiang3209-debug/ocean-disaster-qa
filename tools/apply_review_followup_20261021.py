"""Apply source-bound, explicitly scoped review results with a full backup."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

from marine_kb.ingestion import split_units
from marine_kb.model_authorization import evidence_key, text_digest
from marine_kb.parsers import _text_quality
from marine_kb.review_batch import apply_batch, sha256
from marine_kb.storage import KnowledgeStore
from tools.apply_source_review_20261021 import import_text

APP = Path(__file__).resolve().parents[1]
WORK = APP / 'tmp/source_review_followup_20261021'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def artifact(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': sha256(path)}


def verify_units(units):
    for unit in units:
        meta = unit['metadata']
        for prefix in ('reviewed_text', 'evidence_image'):
            if sha256(meta[prefix + '_path']) != meta[prefix + '_sha256']:
                raise ValueError('reviewed_excerpt_artifact_mismatch')


def base_item(store, source_id, digest):
    source = store.get_source(source_id)
    with store.connect() as db:
        row = db.execute('SELECT raw_path FROM sources WHERE source_id=?', (source_id,)).fetchone()
    if source is None or source['sha256'] != digest or row is None or sha256(row['raw_path']) != digest:
        raise ValueError('source_identity_changed')
    return source, {'source_id': source_id, 'expected_sha256': digest,
                    'expected_updated_at': source['updated_at'], 'decision': 'repair_only'}


def prepare(store):
    full = load(WORK / 'fulltext/followup_fulltext_review_manifest.json')
    bodies = load(WORK / 'fulltext/reviewed_two_normative_bodies.json')['documents']
    root = load(WORK / 'root_short/root_reviewed_documents.json')
    independent = load(WORK / 'root_short/root_transcript_independent_check.json')
    if independent.get('required_corrections'):
        raise ValueError('unresolved_independent_correction')
    versions = load(WORK / 'versions/version_followup.json')
    batch_id = 'review-followup-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    items = []
    for doc in root + bodies:
        source, item = base_item(store, doc['source_id'], doc['source_sha256'])
        root_doc = 'source_images' in doc
        if root_doc:
            units = doc['units']; pages = doc['reviewed_pages']; images = doc['source_images']
            proof_path = WORK / 'root_short/root_reviewed_documents.json'
            scope = doc['review_scope_note']
        else:
            if not doc['all_normative_clauses_and_appendices_reviewed'] or not doc['can_approve_business_body']:
                raise ValueError('normative_body_not_completely_reviewed')
            units = doc['parsed']['units']; pages = doc['actual_visually_inspected_pages']
            proof_path = Path(doc['artifact_path'])
            if sha256(proof_path) != doc['artifact_sha256']:
                raise ValueError('normative_manifest_mismatch')
            verify_units(units)
            images = [{'path': row['metadata']['evidence_image_path'],
                       'sha256': row['metadata']['evidence_image_sha256']} for row in units
                      if row.get('metadata', {}).get('evidence_image_path')]
            scope = doc['approved_text_scope']
        for row in images:
            if sha256(row['path']) != row['sha256']:
                raise ValueError('reviewed_image_changed')
        if sorted(set(pages)) != list(range(1, source['metadata']['page_count'] + 1)):
            raise ValueError('not_all_physical_pages_inspected')
        proof = [artifact(proof_path)] + [{'path': row['path'], 'sha256': row['sha256']} for row in images]
        if root_doc:
            proof.append(artifact(WORK / 'root_short/root_transcript_independent_check.json'))
        item.update(decision='approve', chunks=split_units(units, size=3500, overlap=0),
                    metadata_patch={'approved_text_scope': scope, 'qa_scope_complete': True,
                                    'complete_text_read': False, 'requires_human_review': False,
                                    'normative_body_verified': True,
                                    'verified_physical_pages': pages, 'quarantined_pages': [],
                                    'review_followup_20261021': {'state': 'normative_business_body_approved',
                                      'reviewed_pages': pages, 'scope': scope}},
                    quality_status='parsed', warnings=[scope],
                    verification={'method': 'rendered_page_verification', 'reviewed_pages': pages, 'artifacts': proof},
                    review_scope='versioned_reference',
                    note='已逐物理页目视核对；只准入校录的完整规范性业务正文及规范性附录，非业务前置/参考信息保留原PDF但不采为问答条款。' + scope)
        items.append(item)
    for doc in full['documents']:
        if doc['decision'] != 'can_approve_selected_clauses_only':
            continue
        source, item = base_item(store, doc['source_id'], doc['source_sha256'])
        verify_units(doc['reviewed_excerpts'])
        item.update(metadata_patch={'review_followup_20261021': {
            'state': 'selected_clauses_verified', 'reviewed_pages': doc['actual_visually_inspected_pages'],
            'scope': '仅本轮所列条款；其余正文、公式、表格仍须按页核对。',
            'manifest_sha256': sha256(WORK / 'fulltext/followup_fulltext_review_manifest.json')}},
                    note='本轮已核对所列业务条款，独立节选准入；原件未审核部分继续保留待审。')
        if doc['source_id'] == '90f3b1d4388dbda395dbc758':
            retained = []
            for chunk in store.detail(source['source_id'])['chunks']:
                if _text_quality(chunk['text']) == 'usable':
                    retained.append({'locator': chunk['locator'], 'text': chunk['text'], 'metadata': chunk['metadata']})
            new_pages = []
            for entry in full['additional_local_ocr']:
                if entry['source_id'] != source['source_id']:
                    continue
                cache = Path(entry['cache_path'])
                if sha256(cache) != entry['cache_sha256']:
                    raise ValueError('additional_ocr_changed')
                value = load(cache)
                if value['source_sha256'] != source['sha256'] or value['page'] != entry['page']:
                    raise ValueError('additional_ocr_wrong_source')
                if value['status'] == 'ok' and _text_quality(value['text']) == 'usable':
                    retained.append({'locator': f"page:{value['page']}", 'text': value['text'],
                                     'metadata': {'page': value['page'], 'quality': 'ocr_unreviewed',
                                                  'method': 'local_rapidocr', 'requires_human_review': True,
                                                  'confidence_mean': value['confidence_mean']}})
                    new_pages.append(value['page'])
            retained.sort(key=lambda row: row['metadata'].get('page', 0))
            item['chunks'] = split_units(retained)
            item['metadata_patch'].update(requires_human_review=True,
                 additional_ocr_recovered_pages=new_pages,
                 watermark_text_layer_bug={'fixed': True, 'note': '订单号水印不再被误当正文；扫描页重新安排本地OCR，OCR未自动批准。'})
            item['warnings'] = ['已修复水印文字层误判并补OCR；仅独立已核节选可用，其他OCR与公式表格仍待核。']
        items.append(item)
    for doc in versions['items']:
        source, item = base_item(store, doc['source_id'], doc['expected_sha256'])
        item.update(metadata_patch={'version_followup_20261021': {
            'version_resolution': doc['version_resolution'], 'unresolved_items': doc.get('unresolved_items', []),
            'needs_owner_confirmation': doc.get('needs_owner_confirmation', True),
            'evidence_manifest_sha256': sha256(WORK / 'versions/version_followup.json')}},
                    note=doc.get('reason') or '本轮追加官方版本检索证据；未取得的正式版本/全文不猜定。')
        if doc.get('archive_scope') == 'superseded_original' and doc.get('archive_candidate'):
            replacement = doc['replacement_verification']
            item.update(decision='archive', archive_replacements=[{'source_id': replacement['source_id'],
                                                                'sha256': replacement['raw_sha256']}],
                        verification={'method': 'official_text_comparison', 'artifacts': [artifact(WORK / 'versions/version_followup.json')]})
        items.append(item)
    for doc in versions.get('existing_source_enrichments', []):
        source, item = base_item(store, doc['source_id'], doc['expected_sha256'])
        for evidence in (doc['official_evidence'], doc['comparison_evidence']):
            if sha256(evidence['path']) != evidence['sha256']:
                raise ValueError('official_version_capture_changed')
        item.update(metadata_patch=doc['suggested_metadata_patch'],
                    note='现有已批准2024省防汛防台预案与官方38页原生文字逐页完全相同；仅补版本证据，原有批准范围与外发设置不变。')
        items.append(item)
    return {'batch_id': batch_id,
            'authorization': '用户2026-10-21明确要求继续处理28份全文和11份版本资料，自动核查/OCR/联网补齐、列缺项并测试相关问答。',
            'items': items}


def import_excerpts(store):
    full = load(WORK / 'fulltext/followup_fulltext_review_manifest.json')
    added = []; unique = {}; links = {}
    for doc in full['documents']:
        if doc['decision'] != 'can_approve_selected_clauses_only':
            continue
        parent = store.get_source(doc['source_id'])
        verify_units(doc['reviewed_excerpts'])
        units = doc['reviewed_excerpts']
        text = '\n\n'.join(row['locator'] + '\n' + row['text'] for row in units)
        key = (parent['sha256'], hashlib.sha256(text.encode()).hexdigest())
        if key in unique:
            identity = unique[key]
            links[parent['source_id']] = [identity]
            added.append({'source_id': identity, 'created': False, 'parent_source_id': parent['source_id'], 'same_raw_alias_reused': True})
            continue
        meta = {key: parent['metadata'][key] for key in ('standard_id', 'edition_current_verified', 'edition_status',
                    'edition_checked_at', 'edition_verification_url', 'publication_date', 'effective_date') if key in parent['metadata']}
        meta.update(source_type='official_standard_excerpt', is_draft=False,
                    external_allowed=parent['metadata'].get('external_allowed', False),
                    parent_source_id=parent['source_id'], parent_sha256=parent['sha256'],
                    parent_filename=parent['filename'],
                    reviewed_pages=doc['actual_visually_inspected_pages'],
                    review_method='rendered_page_verification', review_scope='versioned_reference',
                    complete_text_read=True, scope_note='仅批准本轮逐页核对的所列业务条款；原件其他正文、公式和图表未随节选批准。',
                    review_manifest_sha256=sha256(WORK / 'fulltext/followup_fulltext_review_manifest.json'))
        if 'external_permission_override' in parent['metadata']:
            meta['external_permission_override'] = parent['metadata']['external_permission_override']
        title = (meta.get('standard_id') or Path(parent['filename']).stem).replace('/', '_') + '_业务条款已核节选_' + parent['sha256'][:10] + '_20261021'
        result = import_text(store, title=title, text=text, metadata=meta, units=units,
                             note='已目视核对所列原页条款及证据SHA；只批准所列范围，未审公式和图表不作答案依据。', category=parent['category'])
        unique[key] = result['source_id']; links[parent['source_id']] = [result['source_id']]
        added.append({**result, 'parent_source_id': parent['source_id']})
    plan = {'batch_id': 'review-followup-lineage-' + datetime.now().strftime('%Y%m%d-%H%M%S'),
            'authorization': '延续用户本轮核查审核请求；为已核节选登记明确父原件关系，权限不变。', 'items': []}
    for sid, values in links.items():
        source = store.get_source(sid)
        old = source['metadata'].get('approved_excerpt_source_ids') or []
        plan['items'].append({'source_id': sid, 'expected_sha256': source['sha256'], 'expected_updated_at': source['updated_at'],
                              'decision': 'repair_only', 'metadata_patch': {'approved_excerpt_source_ids': list(dict.fromkeys(old + values))},
                              'note': '本轮已核业务节选已独立准入；原件全文状态和外发设置保持不变。'})
    if plan['items']:
        result = apply_batch(store.database, plan, backup_dir=store.database.parent / 'review_backups')
        save(WORK / 'lineage_application_result.json', result)
    return added


def authorize_verified_chunks(store, ids, backup):
    path = store.database.parent / 'model_evidence_authorizations.json'
    shutil.copy2(path, backup / 'model_evidence_authorizations.before.json')
    data = load(path); before = dict(data['entries']); sources = []
    for sid in sorted(set(ids)):
        detail = store.detail(sid, limit=30000); source = detail['source']
        if source['status'] != 'approved' or source['metadata'].get('external_permission_override') is False:
            continue
        count = 0
        for chunk in detail['chunks']:
            if chunk['metadata'].get('external_permission_override') is False:
                continue
            data['entries'][evidence_key(chunk)] = text_digest(chunk['text']); count += 1
        sources.append({'source_id': sid, 'chunk_count': count, 'scope': '本轮已核准的规范性中文业务正文/条款及规范性附录，仅用于既有DeepSeek资料问答。'})
    data.setdefault('reviewed_batches', []).append({'authorized_at': datetime.now().astimezone().isoformat(),
        'basis': '本轮用户继续要求核查资料并测试相关问答；仅限已核文字逐片SHA，不外发未审OCR或整库数值，不覆盖原件明确拒绝。', 'sources': sources})
    data.setdefault('counts', {})['review_followup_new_chunk_entries'] = len(set(data['entries']) - set(before))
    save(path, data)
    return {'source_count': len(sources), 'new_entries': len(set(data['entries']) - set(before)),
            'old_entries_preserved': all(data['entries'][key] == value for key, value in before.items())}


def main():
    cli = argparse.ArgumentParser(description=__doc__); cli.add_argument('--apply', action='store_true'); args = cli.parse_args()
    store = KnowledgeStore(APP / 'data/marine_kb/knowledge.sqlite3')
    plan = prepare(store); save(WORK / 'application_plan.json', plan)
    print(json.dumps({'items': len(plan['items']), 'decisions': {key: sum(x['decision'] == key for x in plan['items']) for key in ('approve', 'archive', 'repair_only')}, 'apply': args.apply}, ensure_ascii=False), flush=True)
    if not args.apply:
        return
    result = apply_batch(store.database, plan, backup_dir=store.database.parent / 'review_backups')
    save(WORK / 'application_result.json', result)
    excerpts = import_excerpts(store); save(WORK / 'excerpt_additions.json', excerpts)
    ids = [row['source_id'] for row in result['items'] if row['decision'] == 'approve'] + [row['source_id'] for row in excerpts]
    authorization = authorize_verified_chunks(store, ids, Path(result['backup']).parent)
    save(WORK / 'qa_authorization.json', authorization)
    print(json.dumps({'backup': result['backup'], 'new_excerpt_sources': sum(row['created'] for row in excerpts),
                      'authorization': authorization}, ensure_ascii=False))


if __name__ == '__main__':
    main()
