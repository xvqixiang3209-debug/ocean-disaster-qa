"""Unified presentation, without migrating bytes or relaxing source controls."""
from pathlib import Path
import hashlib
import json
import sqlite3

import pytest

from marine_kb.storage import KnowledgeStore
from marine_kb.library_catalog import LibraryCatalog


class Legacy:
    def __init__(self, path):
        self.database_path = path

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def status(self):
        with self._connect() as db:
            return {'document_count': db.execute('SELECT count(*) FROM knowledge_documents').fetchone()[0],
                    'chunk_count': db.execute('SELECT coalesce(sum(chunk_count),0) FROM knowledge_documents').fetchone()[0]}


@pytest.fixture
def stores(tmp_path):
    store = KnowledgeStore(tmp_path/'marine.sqlite3')
    legacy = Legacy(tmp_path/'legacy.sqlite3')
    with legacy._connect() as db:
        db.execute('''CREATE TABLE knowledge_documents(
            document_id TEXT PRIMARY KEY, filename TEXT, source_kind TEXT, sha256 TEXT,
            byte_size INTEGER, imported_at TEXT, status TEXT, chunk_count INTEGER,
            char_count INTEGER, metadata_json TEXT, error_message TEXT)''')
    return store, legacy


def add_marine(store, filename, *, relative_path=None, status='indexed', category='历史报告',
               created_at='2026-10-20T10:00:00+00:00', data=b'marine text', metadata=None):
    raw = store.database.parent/'raw'/filename
    raw.parent.mkdir(exist_ok=True)
    raw.write_bytes(data)
    source_id, _ = store.register(filename=filename, relative_path=relative_path or filename,
        sha256=hashlib.sha256(data).hexdigest(), size=len(data), raw_path=raw, category=category,
        metadata=metadata)
    store.finish(source_id, status=status, quality_status='parsed', metadata=metadata or {}, warnings=[],
                 chunks=[{'locator': 'paragraph:1', 'text': data.decode('utf-8'), 'metadata': {}}])
    with store.connect() as db:
        db.execute('UPDATE sources SET created_at=? WHERE source_id=?', (created_at, source_id))
    return source_id, raw


def add_legacy(legacy, document_id, filename, *, status='ready', source_kind='folder_upload',
               imported_at='2026-10-20T12:00:00+00:00', metadata=None):
    with legacy._connect() as db:
        db.execute('INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (document_id, filename, source_kind, hashlib.sha256(filename.encode()).hexdigest(),
             123, imported_at, status, 3, 90,
             metadata if isinstance(metadata, str) else json.dumps(metadata or {}, ensure_ascii=False), None))
    return 'legacy:'+document_id


def test_all_list_joins_without_reimporting_and_retains_library_identity(stores):
    store, legacy = stores
    marine_id, raw = add_marine(store, '专业预案.pdf')
    legacy_id = add_legacy(legacy, 'historical-one', '历史灾害.docx')
    result = LibraryCatalog(store, legacy).list_sources(library='all')
    assert result['total'] == 2 and result['page'] == 1 and result['page_size'] == 40
    assert result['counts'] == {'marine': 1, 'legacy': 1}
    assert [item['source_id'] for item in result['items']] == [legacy_id, marine_id]
    assert {item['library'] for item in result['items']} == {'marine', 'legacy'}
    assert all(item['library_label'] for item in result['items'])
    old = next(item for item in result['items'] if item['library'] == 'legacy')
    assert old['read_only'] is True and old['original_available'] is False
    assert old['status'] == 'indexed' and old['category'] == 'folder_upload'
    assert raw.is_file()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 1
    with legacy._connect() as db:
        assert db.execute('SELECT count(*) FROM knowledge_documents').fetchone()[0] == 1


def test_legacy_more_than_two_hundred_records_are_all_pageable(stores):
    store, legacy = stores
    for index in range(257):
        add_legacy(legacy, f'{index:04d}', f'历史文件{index:04d}.pdf')
    catalog = LibraryCatalog(store, legacy)
    pages = [catalog.list_sources(library='legacy', page=page, limit=40) for page in range(1, 8)]
    ids = [item['source_id'] for page in pages for item in page['items']]
    assert all(page['total'] == 257 for page in pages)
    assert len(ids) == len(set(ids)) == 257
    assert set(ids) == {'legacy:'+f'{index:04d}' for index in range(257)}


def test_all_pagination_uses_global_dates_and_stable_tie_order(stores):
    store, legacy = stores
    old, _ = add_marine(store, '旧专业.pdf', created_at='2026-10-18T00:00:00+00:00')
    middle = add_legacy(legacy, 'm', '中间历史.pdf', imported_at='2026-10-19T00:00:00+00:00')
    new, _ = add_marine(store, '新专业.pdf', created_at='2026-10-20T00:00:00+00:00')
    latest = add_legacy(legacy, 'z', '最新历史.pdf', imported_at='2026-10-21T00:00:00+00:00')
    catalog = LibraryCatalog(store, legacy)
    first = catalog.list_sources(library='all', page=1, limit=2)
    second = catalog.list_sources(library='all', page=2, limit=2)
    assert [row['source_id'] for row in first['items']+second['items']] == [latest, new, middle, old]
    tied = add_legacy(legacy, 'a', '并列历史.pdf', imported_at='2026-10-21T00:00:00+00:00')
    reference = [row['source_id'] for row in catalog.list_sources(library='all', limit=100)['items']]
    assert reference[:2] in ([tied, latest], [latest, tied])
    paged = [catalog.list_sources(library='all', page=page, limit=1)['items'][0]['source_id'] for page in range(1, 6)]
    assert paged == reference and len(set(paged)) == 5


@pytest.mark.parametrize('needle', ['%', '_'])
def test_query_matches_literal_percent_and_underscore_in_filename_and_relative_path(stores, needle):
    store, legacy = stores
    marine_id, _ = add_marine(store, '专业.pdf', relative_path=f'目录{needle}字面/专业.pdf')
    old_id = add_legacy(legacy, 'literal', f'历史{needle}字面.pdf')
    add_marine(store, '普通.pdf')
    add_legacy(legacy, 'ordinary', '历史普通.pdf')
    result = LibraryCatalog(store, legacy).list_sources(library='all', q=needle)
    assert result['total'] == 2
    assert {row['source_id'] for row in result['items']} == {marine_id, old_id}


def test_query_finds_legacy_relative_path_without_invalid_json_crash(stores):
    store, legacy = stores
    wanted = add_legacy(legacy, 'relative', '文件.pdf', metadata={'relative_path':'风暴潮资料/目录%_专题/文件.pdf'})
    add_legacy(legacy, 'broken-json', '普通.docx', metadata='{invalid json')
    result = LibraryCatalog(store, legacy).list_sources(library='legacy', q='目录%_专题')
    assert result['total'] == 1 and result['items'][0]['source_id'] == wanted
    assert LibraryCatalog(store, legacy).list_sources(library='all')['total'] == 2


def test_status_category_and_library_filters_keep_non_ready_legacy_out(stores):
    store, legacy = stores
    approved, _ = add_marine(store, '标准.pdf', status='approved', category='标准规范')
    pending, _ = add_marine(store, '候审.pdf', status='pending_review', category='标准规范')
    ready = add_legacy(legacy, 'ready', '历史.pdf', source_kind='folder_upload')
    add_legacy(legacy, 'failed', '失败.pdf', status='error')
    catalog = LibraryCatalog(store, legacy)
    assert catalog.list_sources(library='all')['total'] == 3
    assert {row['source_id'] for row in catalog.list_sources(library='all', status='approved')['items']} == {approved}
    assert {row['source_id'] for row in catalog.list_sources(library='all', status='pending_review')['items']} == {pending}
    assert {row['source_id'] for row in catalog.list_sources(library='all', status='indexed')['items']} == {ready}
    assert catalog.list_sources(library='legacy', status='error')['items'] == []
    assert catalog.list_sources(library='all', category='标准规范')['total'] == 2
    assert catalog.list_sources(library='all', category='folder_upload')['items'][0]['source_id'] == ready


def test_summary_names_registration_counts_and_never_claims_legacy_originals(stores):
    store, legacy = stores
    add_marine(store, '同一文件.pdf', relative_path='甲/同一文件.pdf')
    add_marine(store, '同一文件.pdf', relative_path='乙/同一文件.pdf')
    add_legacy(legacy, 'ready', '历史.pdf')
    add_legacy(legacy, 'error', '失败.pdf', status='error')
    summary = LibraryCatalog(store, legacy).summary()
    libraries = {row['id']:row for row in summary['libraries']}
    assert summary['query_mode'] == 'joint_local_retrieval'
    assert libraries['marine']['record_count'] == 2
    assert libraries['legacy']['record_count'] == 1
    assert libraries['legacy']['total_registered'] == 2
    assert libraries['legacy']['original_available'] is False
    assert '未去重' in summary['count_note'] and '不是独立有效文档数' in summary['count_note']


def test_items_do_not_expose_raw_paths_or_private_metadata(stores):
    store, legacy = stores
    metadata = {'api_key':'FIXTURE_SECRET_DO_NOT_EXPOSE', 'path':'E:\\private\\file.pdf',
                'signed_url':'https://example.invalid/a?token=FIXTURE_SECRET_DO_NOT_EXPOSE',
                'external_allowed':False}
    add_marine(store, '专业.pdf', metadata=metadata)
    add_legacy(legacy, 'ready', '历史.pdf', metadata={**metadata, 'original_available':True})
    catalog = LibraryCatalog(store, legacy)
    text = json.dumps({'list':catalog.list_sources(library='all'), 'summary':catalog.summary()}, ensure_ascii=False)
    assert 'FIXTURE_SECRET_DO_NOT_EXPOSE' not in text
    assert 'raw_path' not in text and 'private' not in text and 'signed_url' not in text
    assert str(store.database.parent) not in text
    assert next(row for row in catalog.list_sources(library='legacy')['items'])['original_available'] is False


def test_missing_legacy_still_serves_new_catalogue(stores):
    store, _ = stores
    source_id, _ = add_marine(store, '专业.pdf')
    catalog = LibraryCatalog(store)
    assert catalog.list_sources(library='all')['items'][0]['source_id'] == source_id
    assert catalog.list_sources(library='legacy')['items'] == []
    summary = catalog.summary()
    legacy = next(row for row in summary['libraries'] if row['id']=='legacy')
    assert legacy['available'] is False and legacy['record_count'] is None


def test_mixed_timestamp_offsets_are_sorted_by_real_instant_not_text(stores):
    store, legacy = stores
    earlier, _ = add_marine(store, '北京时间较早.pdf', created_at='2026-10-21T08:30:00+08:00')
    later = add_legacy(legacy, 'utc-later', 'UTC较晚.pdf', imported_at='2026-10-21T01:00:00+00:00')
    result = LibraryCatalog(store, legacy).list_sources(library='all')
    assert [row['source_id'] for row in result['items']] == [later, earlier]


@pytest.mark.parametrize('failure', ['connection', 'missing_table'])
def test_unavailable_legacy_is_graceful_and_does_not_publish_exception_path(stores, tmp_path, failure):
    store, _ = stores
    add_marine(store, '专业.pdf')
    if failure == 'connection':
        class BrokenLegacy:
            def _connect(self):
                raise sqlite3.OperationalError('E:\\private\\FIXTURE_SECRET_DO_NOT_EXPOSE.sqlite3')
        legacy = BrokenLegacy()
    else:
        path = tmp_path/'missing-table.sqlite3'
        sqlite3.connect(path).close()
        legacy = Legacy(path)
    catalog = LibraryCatalog(store, legacy)
    result = catalog.list_sources(library='all')
    assert result['total'] == 1 and result['items'][0]['library'] == 'marine'
    assert result['library_errors']
    text = json.dumps({'list':result,'summary':catalog.summary()})
    assert 'FIXTURE_SECRET_DO_NOT_EXPOSE' not in text and 'private' not in text


@pytest.mark.parametrize('kwargs', [{'library':'unknown'}, {'page':0}, {'page':-1}, {'page':'bad'},
    {'limit':0}, {'limit':-1}, {'limit':'bad'}])
def test_invalid_catalogue_inputs_raise_without_mutation(stores, kwargs):
    store, legacy = stores
    with pytest.raises(ValueError):
        LibraryCatalog(store, legacy).list_sources(**kwargs)


def test_overlarge_page_size_clamped_and_empty_late_page_retains_total(stores):
    store, legacy = stores
    add_marine(store, '专业.pdf')
    catalog = LibraryCatalog(store, legacy)
    assert catalog.list_sources(library='all', limit=1000)['page_size'] == 100
    result = catalog.list_sources(library='all', page=99)
    assert result['items'] == [] and result['total'] == 1


def test_review_groups_filter_before_count_and_pagination_without_changing_sources(stores):
    store, legacy = stores
    groups = {
        'structured': {'structured_archive_state': 'raw_values_recovered'},
        'partial': {'approved_excerpt_source_ids': ['already-approved']},
        'fulltext': {'edition_current_verified': True},
        'version': {'version_audit_20261021': {'version_status': 'unverified'}},
    }
    expected = {}
    for group, metadata in groups.items():
        expected[group] = {add_marine(store, group+str(index)+'.pdf', status='pending_review',
            metadata=metadata)[0] for index in range(3)}
    approved, _ = add_marine(store, 'approved-card.pdf', status='approved',
                            metadata={'source_type': 'official_document_card'})
    add_legacy(legacy, 'old', '历史.pdf', metadata=groups['structured'])
    catalog = LibraryCatalog(store, legacy)
    with store.connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM sources ORDER BY source_id')]
    summary = store.status()['review_groups']
    assert summary['total'] == store.status()['status_counts']['pending_review'] == 12
    assert {row['id']: row['count'] for row in summary['groups']} == dict.fromkeys(groups, 3)
    for group in groups:
        pages = [catalog.list_sources(library='all', review_group=group, limit=1, page=page)
                 for page in range(1, 4)]
        assert all(page['total'] == 3 and page['counts'] == {'marine': 3, 'legacy': 0} for page in pages)
        assert [page['has_next'] for page in pages] == [True, True, False]
        assert {page['items'][0]['source_id'] for page in pages} == expected[group]
        assert all(page['items'][0]['review_group']['id'] == group for page in pages)
        assert catalog.list_sources(library='legacy', review_group=group)['items'] == []
        assert catalog.list_sources(library='marine', review_group=group, status='approved')['total'] == 0
        old_pages = [store.list_sources(review_group=group, limit=1, page=page) for page in range(1, 4)]
        assert {page['items'][0]['source_id'] for page in old_pages} == expected[group]
        assert all(page['total'] == 3 for page in old_pages)
    items = catalog.list_sources(library='all', limit=100)['items']
    card = next(row for row in items if row['source_id'] == approved)
    assert card['review_group'] is None and card['review_progress'] == '已核文档身份；不含正文条款'
    old = next(row for row in items if row['library'] == 'legacy')
    assert old['review_group'] is None and old['review_progress'] == ''
    with store.connect() as db:
        assert before == [tuple(row) for row in db.execute('SELECT * FROM sources ORDER BY source_id')]


def test_grouped_federated_items_expose_only_safe_derived_progress(stores):
    store, legacy = stores
    metadata = {'structured_archive_state': 'raw_values_recovered', 'api_key': 'FIXTURE_SECRET_DO_NOT_EXPOSE',
                'features': [{'raw_path': 'E:/private/FIXTURE_SECRET_DO_NOT_EXPOSE'}]}
    add_marine(store, '数值.xlsx', status='pending_review', metadata=metadata)
    result = LibraryCatalog(store, legacy).list_sources(library='all', review_group='structured')
    assert result['items'][0]['review_group']['id'] == 'structured'
    assert '原始数值已恢复' in result['items'][0]['review_progress']
    text = json.dumps(result, ensure_ascii=False)
    assert 'FIXTURE_SECRET_DO_NOT_EXPOSE' not in text and 'raw_path' not in text and 'metadata' not in text


def test_unknown_review_groups_are_rejected_by_both_catalogues(stores):
    store, legacy = stores
    with pytest.raises(ValueError, match='未知的待复核分组'):
        LibraryCatalog(store, legacy).list_sources(review_group='unknown')
    with pytest.raises(ValueError, match='未知的待复核分组'):
        store.list_sources(review_group='unknown')
