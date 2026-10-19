from types import SimpleNamespace
import unittest
from flask import Flask
from typhoon.api import create_typhoon_blueprint


class SelectedQAPolicyTests(unittest.TestCase):
    def test_selected_case_question_uses_unified_hybrid_service(self):
        history, calls = [], []
        sessions = SimpleNamespace(
            select=lambda request_id, sid: {'season':2019, 'name':'利奇马'},
            append_history=lambda *args: history.append(args))
        conversations = SimpleNamespace(sessions=sessions, ask=lambda *a, **k: self.fail('Legacy model was used'))
        class QA:
            require_model_answer = True
            def answer(self, question, **options):
                calls.append((question, options))
                return {'answer':'历史资料记载采取了防御措施。[资料1]', 'rag_sources':[],
                        'web_sources':[{'url':'https://www.cma.gov.cn/a','title':'官方资料'}],
                        'answer_meta':{'used_mode':'llm','answer_policy':'always_local_and_official_web'}}
        app = Flask(__name__)
        app.extensions['marine_kb'] = {'qa':QA()}
        app.register_blueprint(create_typhoon_blueprint(SimpleNamespace(conversations=conversations)))
        result = app.test_client().post('/api/typhoon/conversation/ask', json={
            'request_id':'request1','sid':'sid1','question':'采取了哪些防御措施？'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(calls[0][1], {'sid':'sid1','allow_web':True})
        self.assertIn('2019年利奇马台风', calls[0][0])
        self.assertEqual(len(history), 2)
        self.assertEqual(result.json['answer_meta']['answer_policy'], 'always_local_and_official_web')
        result = app.test_client().post('/api/typhoon/conversation/ask', json={
            'request_id':'request1','sid':'sid1','question':'最大风速是多少？'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(calls[-1][1], {'sid':'sid1','allow_web':True})

