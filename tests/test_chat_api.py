import unittest

from flask import Flask

from marine_kb.chat_api import create_chat_blueprint


class QA:
    def __init__(self):
        self.calls = []

    def answer(self, question, **options):
        self.calls.append((question, options))
        return {'answer': '历史资料记载3.12米。[资料1]',
                'rag_sources': [{'title': '历史报告', 'locator': '第3页'}],
                'answer_meta': {'used_mode': 'local_extractive'}}


class ChatAPITests(unittest.TestCase):
    def setUp(self):
        self.qa = QA()
        self.app = Flask(__name__)
        self.app.register_blueprint(create_chat_blueprint(self.qa))
        self.client = self.app.test_client()
        self.token = self.client.get('/api/assistant/session').json['token']

    def post(self, body, **kwargs):
        return self.client.post('/api/assistant/ask', json=body,
                                headers={'X-Assistant-Token': self.token}, **kwargs)

    def test_question_does_not_allow_client_model_or_privacy_overrides(self):
        result = self.post({'question': '  利奇马最大增水是多少？ ', 'allow_web': True,
                            'model': 'untrusted-model', 'external_allowed': True})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.qa.calls, [('利奇马最大增水是多少？', {'allow_web': True})])
        self.assertEqual(result.json['mode'], 'local_extractive')
        self.assertIn('资料1', result.json['answer'])
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertEqual(set(self.client.get('/api/assistant/session').json), {'token'})

    def test_local_and_session_boundary(self):
        self.assertEqual(self.client.post('/api/assistant/ask', json={'question': 'test'}).status_code, 403)
        self.assertEqual(self.client.get('/api/assistant/session', base_url='http://untrusted.example').status_code, 403)
        self.assertEqual(self.client.get('/api/assistant/session', environ_base={'REMOTE_ADDR':'10.1.2.3'}).status_code, 403)
        self.assertEqual(self.client.get('/api/assistant/session', headers={'Sec-Fetch-Site':'cross-site'}).status_code, 403)
        self.assertEqual(self.qa.calls, [])

    def test_rejects_invalid_questions_without_calling_qa(self):
        for body in ([], {}, {'question': 123}, {'question': ''}, {'question': ' '}, {'question': 'a'*2001}):
            self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.post({'question':'a'*17000}).status_code, 413)
        self.assertEqual(self.qa.calls, [])

    def test_internal_errors_are_not_exposed(self):
        def fail(*args, **kwargs):
            raise RuntimeError('secret-key-private-endpoint')
        self.qa.answer = fail
        result = self.post({'question':'利奇马最大增水？'})
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('secret-key', result.get_data(as_text=True))

    def test_generation_failure_is_retryable_and_not_disguised_as_an_answer(self):
        self.qa.answer = lambda *a, **k: {'answer':'DeepSeek暂时未能生成回答，请稍后重试。',
                                        'answer_meta':{'used_mode':'model_error'}}
        result = self.post({'question':'利奇马最大增水？'})
        self.assertEqual(result.status_code,503)
        self.assertIn('DeepSeek', result.json['error'])

    def test_web_citations_are_returned_with_local_citations_in_number_order(self):
        self.qa.answer = lambda *a, **k: {'answer':'台风与风暴潮不同。[资料1][资料2]',
            'rag_sources':[{'citation_id':2, 'title':'历史书'}],
            'web_sources':[{'citation_id':1, 'source_type':'public_web', 'url':'https://www.cma.gov.cn/a'}],
            'answer_meta':{'used_mode':'llm'}}
        result = self.post({'question':'台风与风暴潮有什么区别？', 'allow_web':False})
        self.assertEqual(result.status_code, 200)
        self.assertEqual([row['citation_id'] for row in result.json['sources']], [1, 2])
        self.assertEqual(result.json['sources'][0]['source_type'], 'public_web')
