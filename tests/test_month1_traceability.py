"""Source navigation and model failures must remain local and traceable."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from flask import Flask

from marine_kb.api import create_blueprint
from marine_kb.qa import MarineQuestionService
from marine_kb.storage import KnowledgeStore


class Legacy:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db


class TraceabilityTests(unittest.TestCase):
    def test_legacy_citation_resolves_to_text_without_fabricating_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = Legacy(root / 'legacy.sqlite3')
            with closing(legacy._connect()) as db:
                db.executescript('''
                    CREATE TABLE knowledge_documents(document_id,filename,source_kind,status,chunk_count,imported_at,sha256);
                    CREATE TABLE knowledge_chunks(chunk_id,document_id,chunk_index,content);
                    INSERT INTO knowledge_documents VALUES('DOC-1','历史报告.txt','historical_report','ready',1,'2026-10-06','testhash');
                    INSERT INTO knowledge_chunks VALUES(7,'DOC-1',0,'海门站最大增水3.12米。');
                ''')
                db.commit()
            store = KnowledgeStore(root / 'new.sqlite3')
            qa = MarineQuestionService(store, legacy_rag=legacy)
            app = Flask(__name__)
            app.register_blueprint(create_blueprint(store, None, qa))
            client = app.test_client()
            response = client.get('/api/knowledge/sources/legacy:DOC-1')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['source']['read_only'])
            self.assertFalse(response.json['source']['original_available'])
            self.assertIn('3.12', response.json['chunks'][0]['text'])
            self.assertEqual(client.get('/api/knowledge/sources/legacy:DOC-1/file').status_code, 404)
            self.assertEqual(client.get('/api/knowledge/sources/legacy:missing').status_code, 404)

    def test_model_failure_never_returns_credentials_or_server_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = KnowledgeStore(Path(directory) / 'new.sqlite3')
            def fail(*args, **kwargs):
                raise RuntimeError('secret-token-and-private-endpoint')
            qa = MarineQuestionService(store, model_caller=fail,
                                       external_enabled=True, model_configured=True)
            app = Flask(__name__)
            app.register_blueprint(create_blueprint(store, None, qa))
            client = app.test_client()
            token = client.get('/api/knowledge/status').json['csrf_token']
            self.assertEqual(client.post('/api/knowledge/model-test').status_code, 403)
            response = client.post('/api/knowledge/model-test', headers={'X-Knowledge-Token': token})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json['state'], 'failed')
            self.assertNotIn('secret-token', response.get_data(as_text=True))
            self.assertIn('RuntimeError', response.json['error'])
