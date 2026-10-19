import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from marine_kb.storage import KnowledgeStore
from marine_kb.ingestion import IngestionService, safe_relative, split_units
from marine_kb.api import create_blueprint
from flask import Flask


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = KnowledgeStore(self.root / 'knowledge.sqlite3')
        self.ingest = IngestionService(self.store, self.root / 'files', parser=lambda p: {
            'units': [{'locator':'第1页', 'text':p.read_text(encoding='utf-8')}],
            'metadata':{},'warnings':[],'status':'parsed'})

    def tearDown(self):
        self.ingest.pool.shutdown(wait=True)
        self.tmp.cleanup()

    def add(self, name='历史总结/2019利奇马.txt', text='2019年利奇马台风海门站最大增水3.12米。'):
        entry = self.ingest.stage(io.BytesIO(text.encode()), name)
        return self.ingest.import_entry(entry)

    def test_index_and_review_gate(self):
        row = self.add()
        self.assertEqual(row['status'], 'indexed')
        self.assertEqual(len(self.store.search_candidates('海门 最大增水')), 1)
        self.store.review(row['source_id'], 'rejected', '该文件仅作测试')
        self.assertEqual(self.store.search_candidates('海门 最大增水'), [])

    def test_draft_and_standard_not_production(self):
        row = self.add('标准/风暴潮报批稿.txt')
        self.assertEqual(row['status'], 'pending_review')
        self.assertEqual(self.store.search_candidates('海门'), [])
        self.assertEqual(len(self.store.search_candidates('海门', include_pending=True)), 1)
        with self.assertRaises(ValueError):
            self.store.review(row['source_id'], 'approved', '测试审核无效')

    def test_idempotent(self):
        a, b = self.add(), self.add()
        self.assertEqual(a['source_id'], b['source_id'])
        self.assertTrue(b['reused'])
        self.assertEqual(self.store.status()['source_count'], 1)

    def test_root_inventory_sums_all_files_and_review_history_is_visible(self):
        first = self.add('归档/one.txt', '2019利奇马历史资料，最大增水3.12米。')
        self.add('归档/two.txt', '2019利奇马历史资料，回港避风。')
        self.store.review(first['source_id'], 'pending_review', '需核对资料原件')
        self.store.review(first['source_id'], 'rejected', '内容与原件不一致')
        root = self.store.status()['source_roots'][0]
        self.assertEqual(root['source_count'], 2)
        self.assertEqual(sum(root['status_counts'].values()), 2)
        self.assertEqual(root['status_counts'], {'indexed': 1, 'rejected': 1})
        history = self.store.detail(first['source_id'])['review_history']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['status'], 'rejected')

    def test_zip_slip_encrypted_nested_boundaries(self):
        for name in ('../a.txt', 'C:/evil.txt', '/absolute.txt'):
            with self.assertRaises(ValueError):
                safe_relative(name)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('../evil.txt', 'danger')
        stream.seek(0)
        entry = self.ingest.stage(stream, 'upload.zip')
        with self.assertRaises(ValueError):
            self.ingest._archive(entry)

    def test_source_pages_and_chunk_limits(self):
        chunks = split_units([{'locator':'第1页','text':'台风知识。'*500}, {'locator':'第2页','text':'增水。'*200}])
        self.assertTrue(all(len(c['text']) <= 1100 for c in chunks))
        self.assertTrue(all('第1页' in c['locator'] or '第2页' in c['locator'] for c in chunks))

    def test_local_api_token_and_request_validation(self):
        app = Flask(__name__)
        app.register_blueprint(create_blueprint(self.store, self.ingest, None))
        c = app.test_client()
        token = c.get('/api/knowledge/status').json['csrf_token']
        self.assertEqual(c.post('/api/knowledge/import').status_code, 403)
        self.assertEqual(c.post('/api/knowledge/import', headers={'X-Knowledge-Token':token}).status_code, 400)
        self.assertEqual(c.get('/api/knowledge/status', environ_base={'REMOTE_ADDR':'10.0.0.2'}).status_code, 403)
        self.assertEqual(c.get('/api/knowledge/status', headers={'Host':'attacker.example'}).status_code, 403)

    def test_original_download_is_attachment_and_cannot_escape(self):
        item = self.add()
        app = Flask(__name__)
        app.register_blueprint(create_blueprint(self.store, self.ingest, None))
        c = app.test_client()
        url = '/api/knowledge/sources/' + item['source_id'] + '/file'
        response = c.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('3.12', response.data.decode())
        response.close()
        self.assertEqual(c.get(url, environ_base={'REMOTE_ADDR':'10.0.0.3'}).status_code, 403)
        with self.store.connect() as db:
            db.execute('UPDATE sources SET raw_path=? WHERE source_id=?', (str(self.store.database), item['source_id']))
        self.assertEqual(c.get(url).status_code, 403)

    def test_compact_jobs_preserve_full_detail(self):
        job = self.store.new_job([{}] * 100)
        self.store.update_job(job, results=[{'filename':str(i),'status':'indexed'} for i in range(100)])
        self.assertEqual(len(self.store.jobs()[0]['results']), 10)
        self.assertTrue(self.store.jobs()[0]['results_truncated'])
        self.assertEqual(len(self.store.job(job)['results']), 100)


if __name__ == '__main__':
    unittest.main()
