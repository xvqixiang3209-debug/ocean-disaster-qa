"""Explicit, optimistic, reversible application of source review manifests.

Parsing recovery and approval are separate decisions. No uploaded file, model
output or parser confidence is itself an approval instruction.
"""
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .storage import dump, tokens


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _validate_decision(item, metadata, chunks):
    decision = item.get('decision', 'repair_only')
    if decision not in {'repair_only', 'approve', 'archive'}:
        raise ValueError('unknown_review_decision')
    # Source permission is an independent action, never inherited from a review
    # manifest. Retain the catalogue's explicit override as well.
    if any(k in item.get('metadata_patch', {}) for k in ('external_allowed', 'external_permission_override')):
        raise ValueError('review_must_not_change_external_permission')
    if decision in {'approve', 'archive'}:
        if decision == 'approve' and (not chunks or metadata.get('is_draft')):
            raise ValueError('draft_or_empty_not_approvable')
        if decision == 'archive' and item.get('chunks') is not None:
            raise ValueError('archive_must_preserve_existing_chunks')
        proof = item.get('verification') or {}
        if proof.get('method') not in {'official_text_comparison', 'rendered_page_verification', 'native_text_integrity'}:
            raise ValueError('missing_verification_method')
        if not proof.get('artifacts') or not item.get('note', '').strip():
            raise ValueError('missing_review_evidence')
        for artifact in proof['artifacts']:
            if not isinstance(artifact, dict) or not artifact.get('path') or not artifact.get('sha256'):
                raise ValueError('review_artifact_requires_hash')
            path = Path(artifact['path'])
            if not path.is_absolute() or not path.is_file() or path.is_symlink() or sha256(path) != artifact['sha256']:
                raise ValueError('review_artifact_integrity_failed')
        # OCR approval requires an explicit physical page inventory. The caller
        # must have actually reviewed it; this check only guards omissions.
        if decision == 'approve' and (metadata.get('ocr', {}).get('recovered_pages') or metadata.get('requires_human_review')):
            if proof.get('method') != 'rendered_page_verification':
                raise ValueError('ocr_requires_rendered_page_review')
            total = metadata.get('page_count')
            if not isinstance(total, int) or sorted(set(proof.get('reviewed_pages', []))) != list(range(1, total + 1)):
                raise ValueError('full_document_approval_requires_every_page')
    return decision


def apply_batch(database, manifest, *, backup_dir):
    """Apply one human-authored manifest after retaining a consistent backup.

    Unchanged chunks keep IDs, FTS entries and entity links. Changed text gets
    new IDs, so existing byte-bound model authorizations cannot cover it.
    """
    database = Path(database).resolve(strict=True)
    items = manifest.get('items') or []
    if not items or len({x['source_id'] for x in items}) != len(items):
        raise ValueError('empty_or_duplicate_sources')
    if not manifest.get('authorization') or not manifest.get('batch_id'):
        raise ValueError('missing_explicit_batch_authorization')
    backup = Path(backup_dir).resolve() / manifest['batch_id'] / 'knowledge.sqlite3'
    backup.parent.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(database, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    with closing(sqlite3.connect(backup)) as target:
        db.backup(target)
    stamp = datetime.now(timezone.utc).isoformat()
    result = []
    try:
        db.execute('BEGIN IMMEDIATE')
        for item in items:
            row = db.execute('SELECT * FROM sources WHERE source_id=?', (item['source_id'],)).fetchone()
            if row is None or row['sha256'] != item['expected_sha256'] or row['updated_at'] != item['expected_updated_at']:
                raise ValueError('source_changed_since_plan')
            raw = Path(row['raw_path']).resolve(strict=True)
            root = database.parent / 'files/raw'
            if not raw.is_relative_to(root.resolve()) or Path(row['raw_path']).is_symlink():
                raise ValueError('raw_path_outside_store')
            if raw.stat().st_size != row['bytes'] or sha256(raw) != row['sha256']:
                raise ValueError('raw_integrity_failed')
            old = db.execute('SELECT * FROM chunks WHERE source_id=? ORDER BY chunk_id', (row['source_id'],)).fetchall()
            proposed = item.get('chunks')
            if proposed is None:
                proposed = [{'locator': x['locator'], 'text': x['text'], 'metadata': json.loads(x['metadata'])} for x in old]
            if len(proposed) > 30000 or any(not x.get('locator') or not x.get('text', '').strip() for x in proposed):
                raise ValueError('invalid_recovered_chunks')
            previous_meta = json.loads(row['metadata'])
            metadata = {**previous_meta, **item.get('metadata_patch', {})}
            old_permissions = {(x['locator'], x['text']): json.loads(x['metadata']) for x in old}
            for chunk in proposed:
                for key in ('external_allowed', 'external_permission_override'):
                    before = old_permissions.get((chunk['locator'], chunk['text']), {})
                    if key in chunk.get('metadata', {}) and chunk['metadata'][key] != before.get(key):
                        raise ValueError('chunk_review_must_not_change_external_permission')
            validation_metadata = dict(metadata)
            if previous_meta.get('requires_human_review') or previous_meta.get('ocr', {}).get('recovered_pages'):
                validation_metadata['requires_human_review'] = True
            decision = _validate_decision(item, validation_metadata, proposed)
            if decision == 'archive':
                replacements = item.get('archive_replacements') or []
                known = previous_meta.get('formal_replacement_source_ids') or []
                if not replacements or any(x.get('source_id') not in known for x in replacements):
                    raise ValueError('archive_requires_registered_formal_replacement')
                for replacement in replacements:
                    target = db.execute('SELECT * FROM sources WHERE source_id=?', (replacement['source_id'],)).fetchone()
                    if target is None or target['status'] != 'approved' or target['sha256'] != replacement.get('sha256'):
                        raise ValueError('archive_replacement_not_approved')
                    target_meta = json.loads(target['metadata'])
                    if target_meta.get('is_draft') or target_meta.get('source_type') != 'official_public_text':
                        raise ValueError('archive_requires_formal_body_not_identity_card')
                    if sha256(target['raw_path']) != target['sha256']:
                        raise ValueError('archive_replacement_integrity_failed')
            if decision == 'repair_only' and row['status'] in {'approved', 'indexed'} and item.get('chunks') is not None:
                if [(x['locator'], x['text']) for x in proposed] != [(x['locator'], x['text']) for x in old]:
                    raise ValueError('unreviewed_recovery_cannot_retain_published_status')
            metadata.update(review_batch_id=manifest['batch_id'], text_recovery_checked_at=stamp)
            if decision == 'approve':
                metadata.update(review_method=item['verification']['method'], reviewed_at=stamp,
                                review_scope=item.get('review_scope', 'versioned_reference'),
                                review_note=item['note'], text_verification=item['verification'])
            elif decision == 'archive':
                metadata.update(archived_at=stamp, archive_reason=item['note'],
                                archive_method='verified_formal_replacement',
                                archive_replacements=item['archive_replacements'])
            else:
                metadata['recovery_note'] = item.get('note', '正文恢复；审核状态未升级。')
            ids = defaultdict(deque)
            for chunk in old:
                ids[(chunk['locator'], chunk['text'])].append(chunk['chunk_id'])
            retained, inserted = set(), 0
            # SQLite can reuse the highest removed rowid. Insert first, before
            # deleting old rows, to keep stale authorizations from binding to a
            # different recovered chunk at the same ID.
            for index, chunk in enumerate(proposed):
                key = (chunk['locator'], chunk['text'])
                chunk_meta = {**chunk.get('metadata', {}), 'chunk_index': index}
                if ids[key]:
                    identity = ids[key].popleft()
                    old_meta = old_permissions[key]
                    for permission in ('external_allowed', 'external_permission_override'):
                        if permission in old_meta:
                            chunk_meta[permission] = old_meta[permission]
                    db.execute('UPDATE chunks SET metadata=? WHERE chunk_id=?', (dump(chunk_meta), identity))
                else:
                    identity = db.execute('INSERT INTO chunks(source_id,locator,text,metadata) VALUES(?,?,?,?)',
                                          (row['source_id'], chunk['locator'], chunk['text'], dump(chunk_meta))).lastrowid
                    db.execute('INSERT INTO chunk_fts(rowid,title,body) VALUES(?,?,?)',
                               (identity, ' '.join(tokens(row['filename'])), ' '.join(tokens(chunk['text']))))
                    inserted += 1
                retained.add(identity)
            removed = [x['chunk_id'] for x in old if x['chunk_id'] not in retained]
            for identity in removed:
                db.execute('DELETE FROM links WHERE chunk_id=?', (identity,))
                db.execute('DELETE FROM chunk_fts WHERE rowid=?', (identity,))
                db.execute('DELETE FROM chunks WHERE chunk_id=?', (identity,))
            status = 'approved' if decision == 'approve' else 'archived' if decision == 'archive' else row['status']
            warnings = item.get('warnings', json.loads(row['warnings']))
            quality = item.get('quality_status', row['quality_status'])
            db.execute('UPDATE sources SET status=?,quality_status=?,metadata=?,warnings=?,chunk_count=?,updated_at=? WHERE source_id=?',
                       (status, quality, dump(metadata), dump(warnings), len(proposed), stamp, row['source_id']))
            db.execute('INSERT INTO review_log(source_id,previous_status,status,note,created_at) VALUES(?,?,?,?,?)',
                       (row['source_id'], row['status'], status, item.get('note', '')[:2000], stamp))
            result.append({'source_id': row['source_id'], 'filename': row['filename'], 'decision': decision,
                           'previous_status': row['status'], 'status': status, 'chunks_before': len(old),
                           'chunks_after': len(proposed), 'new_chunks': inserted, 'removed_chunks': len(removed),
                           'unchanged_chunks': len(retained) - inserted,
                           'external_permission_unchanged': all(metadata.get(k) == previous_meta.get(k) for k in ('external_allowed', 'external_permission_override'))})
        db.commit()
        return {'backup': str(backup), 'applied_at': stamp, 'items': result}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
