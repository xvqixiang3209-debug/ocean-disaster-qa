import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from marine_kb.review_batch import apply_batch
from marine_kb.storage import KnowledgeStore


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / 'knowledge.sqlite3'
    store = KnowledgeStore(database)
    raw = tmp_path / 'files/raw/a.txt'
    raw.parent.mkdir(parents=True)
    raw.write_text('原件文本', encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    identity, _ = store.register(filename='管理办法.txt', relative_path='法规/管理办法.txt', sha256=digest,
                                 size=raw.stat().st_size, raw_path=raw, category='标准规范')
    store.finish(identity, status='pending_review', quality_status='parsed',
                 metadata={'external_allowed': False, 'is_draft': False}, warnings=[],
                 chunks=[{'locator': 'page:1', 'text': '一条已经留存的原文', 'metadata': {}}])
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM sources WHERE source_id=?', (identity,)).fetchone())
        chunk = dict(db.execute('SELECT * FROM chunks WHERE source_id=?', (identity,)).fetchone())
        db.execute('INSERT INTO links VALUES(?,?,?,?,?,?)', (chunk['chunk_id'], 'SID1', 1, 'approved', 'manual', 'evidence'))
    artifact = tmp_path / 'comparison.txt'
    artifact.write_text('已核对原件文字与转换结果；文件版本另行核定。', encoding='utf-8')
    proof = {'method': 'native_text_integrity', 'artifacts': [{'path': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    item = {'source_id': identity, 'expected_sha256': digest, 'expected_updated_at': row['updated_at'],
            'decision': 'approve', 'note': '核对原件与存储文本一致，按所列版本参考。', 'verification': proof}
    return store, raw, item, chunk, tmp_path


def apply(setup, item=None):
    store, raw, original, chunk, tmp = setup
    return apply_batch(store.database, {'authorization': '用户明确授权代为核查审核', 'batch_id': 'batch', 'items': [item or original]}, backup_dir=tmp / 'backups')


def test_approval_preserves_ids_links_permissions_and_backup(setup):
    store, raw, item, chunk, tmp = setup
    result = apply(setup)
    assert result['items'][0]['unchanged_chunks'] == 1
    assert store.get_source(item['source_id'])['status'] == 'approved'
    assert store.get_source(item['source_id'])['metadata']['external_allowed'] is False
    with store.connect() as db:
        assert db.execute('SELECT chunk_id FROM chunks').fetchone()[0] == chunk['chunk_id']
        assert db.execute('SELECT count(*) FROM links').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM review_log').fetchone()[0] == 1
    with sqlite3.connect(result['backup']) as db:
        assert db.execute('SELECT status FROM sources').fetchone()[0] == 'pending_review'


def test_repair_keeps_pending_and_gives_changed_text_new_id(setup):
    store, raw, item, chunk, tmp = setup
    item.update(decision='repair_only', chunks=[{'locator': 'page:1', 'text': '恢复后的新正文', 'metadata': {}}])
    apply(setup)
    assert store.get_source(item['source_id'])['status'] == 'pending_review'
    with store.connect() as db:
        assert db.execute('SELECT chunk_id FROM chunks').fetchone()[0] > chunk['chunk_id']
        assert db.execute('SELECT count(*) FROM links').fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH '恢复'").fetchone()[0] == 1


@pytest.mark.parametrize('patch', [{'external_allowed': True}, {'external_permission_override': True}])
def test_review_cannot_broaden_external_permission(setup, patch):
    setup[2]['metadata_patch'] = patch
    with pytest.raises(ValueError, match='external_permission'):
        apply(setup)
    assert setup[0].get_source(setup[2]['source_id'])['status'] == 'pending_review'


def test_chunk_cannot_broaden_external_permission(setup):
    setup[2]['chunks'] = [{'locator': 'page:1', 'text': '新正文', 'metadata': {'external_allowed': True}}]
    with pytest.raises(ValueError, match='external_permission'):
        apply(setup)


@pytest.mark.parametrize('change,error', [
    ({'metadata_patch': {'is_draft': True}}, 'draft_or_empty'),
    ({'verification': {}}, 'missing_verification'),
    ({'chunks': []}, 'draft_or_empty'),
    ({'expected_updated_at': 'stale'}, 'source_changed'),
    ({'expected_sha256': 'stale'}, 'source_changed'),
])
def test_invalid_plans_roll_back(setup, change, error):
    setup[2].update(change)
    with pytest.raises(ValueError, match=error):
        apply(setup)
    with setup[0].connect() as db:
        assert db.execute('SELECT count(*) FROM review_log').fetchone()[0] == 0


def test_raw_integrity_checked_before_approval(setup):
    setup[1].write_text('原件被修改', encoding='utf-8')
    with pytest.raises(ValueError, match='raw_integrity'):
        apply(setup)


def test_artifact_integrity_checked_before_approval(setup):
    setup[2]['verification']['artifacts'][0]['sha256'] = 'not-the-file'
    with pytest.raises(ValueError, match='artifact_integrity'):
        apply(setup)


def test_ocr_approval_requires_every_physical_page(setup):
    setup[2]['metadata_patch'] = {'requires_human_review': True, 'page_count': 3, 'ocr': {'recovered_pages': 3}}
    setup[2]['verification'].update(method='rendered_page_verification', reviewed_pages=[1, 2])
    with pytest.raises(ValueError, match='every_page'):
        apply(setup)


def test_full_ocr_inventory_can_be_recorded(setup):
    setup[2]['metadata_patch'] = {'requires_human_review': True, 'page_count': 2, 'ocr': {'recovered_pages': 2}}
    setup[2]['verification'].update(method='rendered_page_verification', reviewed_pages=[1, 2])
    assert apply(setup)['items'][0]['status'] == 'approved'


def test_late_failure_rolls_back_prior_source(setup):
    store, raw, item, chunk, tmp = setup
    second = {**item, 'source_id': 'missing'}
    with pytest.raises(ValueError, match='source_changed'):
        apply_batch(store.database, {'authorization': '授权', 'batch_id': 'batch', 'items': [item, second]}, backup_dir=tmp / 'backups')
    assert store.get_source(item['source_id'])['status'] == 'pending_review'


def test_omitted_chunk_permission_stays_closed(setup):
    store, raw, item, chunk, tmp = setup
    with store.connect() as db:
        db.execute('UPDATE chunks SET metadata=? WHERE chunk_id=?', (json.dumps({'external_permission_override': False}), chunk['chunk_id']))
    item['chunks'] = [{'locator': chunk['locator'], 'text': chunk['text'], 'metadata': {}}]
    apply(setup)
    assert store.detail(item['source_id'])['chunks'][0]['metadata']['external_permission_override'] is False


def test_unreviewed_repair_does_not_retain_approved_status(setup):
    store, raw, item, chunk, tmp = setup
    store.review(item['source_id'], 'approved', '原文已审核确认')
    with store.connect() as db:
        item['expected_updated_at'] = db.execute('SELECT updated_at FROM sources WHERE source_id=?', (item['source_id'],)).fetchone()[0]
    item.update(decision='repair_only', chunks=[{'locator': 'page:2', 'text': '新增未核正文', 'metadata': {}}])
    with pytest.raises(ValueError, match='cannot_retain_published'):
        apply(setup)


def archive_item(setup, *, source_type='official_public_text'):
    store, raw, item, chunk, tmp = setup
    replacement = raw.parent / 'formal.txt'
    replacement.write_text('正式替代全文条文', encoding='utf-8')
    digest = hashlib.sha256(replacement.read_bytes()).hexdigest()
    sid, _ = store.register(filename='正式管理办法.txt', relative_path='正式管理办法.txt', sha256=digest,
                            size=replacement.stat().st_size, raw_path=replacement, category='标准规范')
    store.finish(sid, status='approved', quality_status='parsed',
                 metadata={'source_type': source_type, 'is_draft': False}, warnings=[],
                 chunks=[{'locator': '第1条', 'text': '正式替代全文条文', 'metadata': {}}])
    with store.connect() as db:
        db.execute('UPDATE sources SET metadata=? WHERE source_id=?',
                   (json.dumps({'formal_replacement_source_ids': [sid], 'external_allowed': False}), item['source_id']))
    item.update(decision='archive', archive_replacements=[{'source_id': sid, 'sha256': digest}],
                note='已取得正式发布替代正文，旧原件及切片保留归档。')
    return item


def test_archive_keeps_original_and_chunks_with_a_verified_replacement(setup):
    item = archive_item(setup)
    result = apply(setup, item)
    source = setup[0].get_source(item['source_id'])
    assert source['status'] == 'archived' and source['metadata']['external_allowed'] is False
    assert setup[1].read_text(encoding='utf-8') == '原件文本'
    assert setup[0].detail(item['source_id'])['chunks'][0]['chunk_id'] == setup[3]['chunk_id']
    assert Path(result['backup']).is_file()
    from marine_kb.retrieval import usable_source
    assert not usable_source({**source, 'text': '旧正文'}, include_pending=True)


def test_archive_cannot_use_an_identity_card_in_place_of_formal_text(setup):
    item = archive_item(setup, source_type='official_document_card')
    with pytest.raises(ValueError, match='formal_body_not_identity_card'):
        apply(setup, item)
    assert setup[0].get_source(item['source_id'])['status'] == 'pending_review'


def test_archive_cannot_rewrite_chunks_or_invent_a_replacement(setup):
    item = archive_item(setup)
    item['chunks'] = [{'locator': 'new', 'text': '改变后的正文', 'metadata': {}}]
    with pytest.raises(ValueError, match='preserve_existing_chunks'):
        apply(setup, item)
