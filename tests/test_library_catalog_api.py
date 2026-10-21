"""Offline Flask coverage for unified catalogue reads and admin boundaries."""
from pathlib import Path
import hashlib
import sqlite3
from unittest.mock import Mock

from flask import Flask
import pytest

from marine_kb.api import create_blueprint
from marine_kb.qa import MarineQuestionService
from marine_kb.storage import KnowledgeStore


class FakeLegacy:
    def __init__(self, path):
        self.database_path = path
        self.delete_document = Mock(side_effect=AssertionError('Catalogue must not delete'))

    def _connect(self):
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        return db


@pytest.fixture
def api(tmp_path):
    store = KnowledgeStore(tmp_path/'marine.sqlite3')
    raw = tmp_path/'raw'/'professional.txt'
    raw.parent.mkdir()
    content = b'synthetic professional document text'
    raw.write_bytes(content)
    source_id, _ = store.register(filename='专业预案.txt', relative_path='专业资料/专业预案.txt',
        sha256=hashlib.sha256(content).hexdigest(), size=len(content), raw_path=raw, category='防御响应')
    store.finish(source_id, status='indexed', quality_status='parsed', metadata={}, warnings=[],
                 chunks=[{'locator':'paragraph:1','text':content.decode(),'metadata':{}}])
    legacy = FakeLegacy(tmp_path/'legacy.sqlite3')
    with legacy._connect() as db:
        db.executescript('''CREATE TABLE knowledge_documents(
            document_id TEXT PRIMARY KEY,filename TEXT,source_kind TEXT,status TEXT,
            chunk_count INTEGER,imported_at TEXT,sha256 TEXT,metadata_json TEXT);
            CREATE TABLE knowledge_chunks(
            chunk_id INTEGER PRIMARY KEY,document_id TEXT,chunk_index INTEGER,content TEXT);''')
        db.execute('INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?,?)',
            ('old-one','历史台风.txt','folder_upload','ready',1,'2026-10-20T12:00:00+00:00','a'*64,'{}'))
        db.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?)',
            (1,'old-one',0,'synthetic retained historical document text'))
        db.execute('INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?,?)',
            ('failed-one','失败资料.txt','folder_upload','error',0,'2026-10-20T13:00:00+00:00','b'*64,'{}'))
    qa = MarineQuestionService(store, legacy_rag=legacy)
    qa.model_caller = Mock(side_effect=AssertionError('No model calls from catalogue'))
    qa._call_model = Mock(side_effect=AssertionError('No model calls from catalogue'))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(create_blueprint(store, None, qa, requirements_path=tmp_path/'absent-requirements.json',
                                            acceptance_directory=tmp_path/'acceptance'))
    return {'app':app,'client':app.test_client(),'store':store,'legacy':legacy,'qa':qa,
            'source_id':source_id,'raw':raw}


def get(api, path, **kwargs):
    return api['client'].get('/api/knowledge'+path, base_url='http://127.0.0.1', **kwargs)


def test_libraries_summary_and_all_sources_are_available_in_same_admin_api(api):
    response = get(api, '/libraries')
    assert response.status_code == 200
    summary = response.get_json()
    assert summary['query_mode'] == 'joint_local_retrieval'
    libraries = {row['id']:row for row in summary['libraries']}
    assert libraries['marine']['record_count'] == 1
    assert libraries['legacy']['record_count'] == 1 and libraries['legacy']['total_registered'] == 2
    assert libraries['legacy']['original_available'] is False
    response = get(api, '/sources?library=all')
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 2 and data['counts'] == {'marine':1,'legacy':1}
    assert {row['source_id'] for row in data['items']} == {api['source_id'],'legacy:old-one'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    api['qa']._call_model.assert_not_called()
    api['qa'].model_caller.assert_not_called()


def test_source_filters_and_pagination_dispatch_to_unified_catalogue(api):
    one = get(api, '/sources?library=all&page=1&limit=1').get_json()
    two = get(api, '/sources?library=all&page=2&limit=1').get_json()
    assert one['total'] == two['total'] == 2
    assert one['page_size'] == two['page_size'] == 1
    assert one['items'][0]['source_id'] != two['items'][0]['source_id']
    legacy = get(api, '/sources?library=legacy&status=indexed&category=folder_upload').get_json()
    assert legacy['total'] == 1 and legacy['items'][0]['source_id'] == 'legacy:old-one'
    assert get(api, '/sources?library=legacy&status=error').get_json()['items'] == []


def test_legacy_source_detail_retains_text_and_explicit_no_original(api):
    response = get(api, '/sources/legacy:old-one')
    assert response.status_code == 200
    detail = response.get_json()
    assert detail['source']['source_id'] == 'legacy:old-one'
    assert detail['source']['read_only'] is True and detail['source']['original_available'] is False
    assert detail['source']['external_allowed'] is False
    assert detail['chunks'][0]['text'] == 'synthetic retained historical document text'
    assert get(api, '/sources/legacy:failed-one').status_code == 404
    response = get(api, '/sources/legacy:old-one/file')
    assert response.status_code == 404 and '未保存原件' in response.get_json()['error']


def test_old_sources_without_library_parameter_remain_marine_only(api):
    response = get(api, '/sources')
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 1 and data['items'][0]['source_id'] == api['source_id']
    assert not any(row['source_id'].startswith('legacy:') for row in data['items'])
    assert get(api, '/sources/'+api['source_id']).status_code == 200


@pytest.mark.parametrize('query', ['library=invalid','library=all&page=0','library=all&page=abc',
                                 'library=legacy&limit=0','library=all&limit=abc','page=abc'])
def test_invalid_library_or_pagination_is_client_error(api, query):
    response = get(api, '/sources?'+query)
    assert response.status_code == 400 and response.get_json()['error']


@pytest.mark.parametrize('path', ['/libraries','/sources?library=all','/sources/legacy:old-one'])
@pytest.mark.parametrize('boundary', ['remote','host','cross_site'])
def test_new_catalogue_and_legacy_details_keep_local_admin_boundary(api, path, boundary):
    kwargs = {}
    base_url = 'http://127.0.0.1'
    if boundary == 'remote':
        kwargs['environ_overrides'] = {'REMOTE_ADDR':'192.0.2.5'}
    elif boundary == 'host':
        base_url = 'http://untrusted.invalid'
    else:
        kwargs['headers'] = {'Sec-Fetch-Site':'cross-site'}
    response = api['client'].get('/api/knowledge'+path, base_url=base_url, **kwargs)
    assert response.status_code == 403
    assert 'items' not in response.get_json() and 'chunks' not in response.get_json()


def test_unified_interfaces_add_no_write_or_delete_route(api):
    for rule in api['app'].url_map.iter_rules():
        if rule.rule in {'/api/knowledge/libraries','/api/knowledge/sources'}:
            assert rule.methods <= {'GET','HEAD','OPTIONS'}
    for method in ('post','delete','put','patch'):
        response = getattr(api['client'], method)('/api/knowledge/libraries', base_url='http://127.0.0.1')
        assert response.status_code in {403,405}
    response = api['client'].delete('/api/knowledge/sources/legacy:old-one', base_url='http://127.0.0.1')
    assert response.status_code in {403,405}
    api['legacy'].delete_document.assert_not_called()
    assert api['raw'].is_file()
    with api['legacy']._connect() as db:
        assert db.execute('SELECT count(*) FROM knowledge_documents').fetchone()[0] == 2


def test_legacy_cannot_be_upgraded_through_professional_source_review(api):
    # A valid admin token still cannot treat legacy IDs as new governed sources.
    status = get(api, '/status').get_json()
    response = api['client'].post('/api/knowledge/sources/legacy:old-one/review',
        base_url='http://127.0.0.1', headers={'X-Knowledge-Token':status['csrf_token']},
        json={'status':'approved','note':'本测试不允许跨库升级'})
    assert response.status_code == 404
    with api['legacy']._connect() as db:
        assert db.execute('SELECT status FROM knowledge_documents WHERE document_id=?',('old-one',)).fetchone()[0] == 'ready'
    api['qa']._call_model.assert_not_called()
    api['qa'].model_caller.assert_not_called()


def test_status_and_both_sources_interfaces_offer_read_only_review_groups(api):
    store = api['store']
    groups = {'structured': {'structured_archive_state': 'raw_values_recovered'},
              'partial': {'approved_excerpt_source_ids': ['approved-excerpt']},
              'fulltext': {}, 'version': {'is_draft': True}}
    for group, metadata in groups.items():
        for index in range(2):
            source_id, _ = store.register(filename=f'{group}{index}.txt', relative_path=f'{group}{index}.txt',
                sha256=hashlib.sha256(f'{group}{index}'.encode()).hexdigest(), size=1,
                raw_path=api['raw'], category='标准规范', metadata=metadata)
            store.finish(source_id, status='pending_review', quality_status='partial', metadata=metadata,
                         warnings=[], chunks=[{'locator':'paragraph:1', 'text':'synthetic text'}])
    status = get(api, '/status').get_json()
    assert status['review_groups']['total'] == status['status_counts']['pending_review'] == 8
    assert {row['id']:row['count'] for row in status['review_groups']['groups']} == dict.fromkeys(groups, 2)
    for prefix in ('', 'library=all&', 'library=marine&'):
        pages = [get(api, f'/sources?{prefix}review_group=partial&limit=1&page={page}').get_json()
                 for page in (1, 2)]
        assert all(page['total'] == 2 and page['page_size'] == 1 for page in pages)
        assert len({page['items'][0]['source_id'] for page in pages}) == 2
        assert all(page['items'][0]['review_group']['id'] == 'partial' for page in pages)
        assert all(page['items'][0]['review_progress'] == '已核节选 1 份' for page in pages)
        assert get(api, f'/sources?{prefix}review_group=unknown').status_code == 400
    assert get(api, '/sources?library=legacy&review_group=structured').get_json()['total'] == 0
    assert get(api, '/sources?library=all&review_group=version&status=approved').get_json()['total'] == 0
    api['qa']._call_model.assert_not_called()
    api['qa'].model_caller.assert_not_called()
