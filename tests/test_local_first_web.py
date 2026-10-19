import json
import unittest
from datetime import datetime, timezone

from marine_kb.qa import MarineQuestionService
from marine_kb.web_supplement import fresh_public_page, public_search_queries
from test_marine_qa import FakeStore, source


def response(answer, coverage='full', refs=(1,), missing=()):
    return json.dumps({'answerability': coverage, 'answer': answer,
                       'evidence_quotes': [{'citation': index, 'passage_id': 1} for index in refs],
                       'missing_information': list(missing)}, ensure_ascii=False)


def public_hit():
    return source('台风并非只在夏季生成，一年四季都有可能，七月至九月最活跃。', source_id='web:season',
                  chunk_id='web:1', status='public_web', url='https://www.cma.gov.cn/season.html',
                  metadata={'source_type': 'public_web', 'external_allowed': True})


class Supplement:
    enabled = True
    def __init__(self, hits=None):
        self.calls = []
        self.hits = [public_hit()] if hits is None else hits
    def fetch(self, question, *, dynamic=False, subquestions=None):
        self.calls.append((question, dynamic))
        return self.hits, {'web_supplement_state': 'success' if self.hits else 'unavailable'}


class LocalFirstWebTests(unittest.TestCase):
    def service(self, answers, supplement, local=True):
        self.payloads = []
        answers = iter(answers)
        def caller(model, system, prompt, **kwargs):
            payload = json.loads(prompt)
            self.payloads.append(payload)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question': payload['question']})
            return next(answers)
        hits = [source(metadata={'external_allowed': True})] if local else []
        return MarineQuestionService(FakeStore(hits), model_caller=caller, external_enabled=True,
                                     structured_answers=True, web_supplement=supplement, search_policy='local_first')

    def test_complete_local_answer_does_not_search(self):
        supplement = Supplement()
        qa = self.service([response('麦莎台风组织渔船回港避风。[资料1]')], supplement)
        result = qa.answer('麦莎台风应对措施', allow_web=True)
        self.assertEqual(supplement.calls, [])
        self.assertEqual(result['answer_meta']['web_supplement_state'], 'not_needed')
        self.assertEqual(result['answer_meta']['local_answerability'], 'full')

    def test_partial_local_answer_searches_then_synthesizes_both_sources(self):
        supplement = Supplement()
        qa = self.service([response('渔船回港避风。[资料1]', 'partial', missing=['缺少台风季节资料']),
                           response('渔船回港避风。[资料1]台风并非只在夏季生成。[资料2]', refs=(1,2))], supplement)
        result = qa.answer('麦莎台风应对措施', allow_web=True)
        self.assertEqual(len(supplement.calls), 1)
        self.assertEqual(result['answer_meta']['route'], 'hybrid_rag')
        self.assertEqual(result['answer_meta']['local_answerability'], 'partial')
        self.assertEqual(len(result['rag_sources']), 1)
        self.assertEqual(len(result['web_sources']), 1)
        self.assertNotIn('组织渔船', str(supplement.calls))

    def test_empty_local_corpus_searches_without_generating_an_initial_refusal(self):
        supplement = Supplement()
        qa = self.service([response('台风并非只在夏季生成。[资料1]')], supplement, local=False)
        result = qa.answer('台风只在夏天有吗？几月到几月要特别防？', allow_web=True)
        self.assertEqual(len(self.payloads), 2)  # planner + final synthesized answer
        self.assertEqual(result['answer_meta']['route'], 'web_fallback')
        self.assertEqual(result['answer_meta']['local_evidence_count'], 0)

    def test_nonempty_missing_information_triggers_supplement_even_if_model_says_full(self):
        supplement = Supplement()
        qa = self.service([response('回港避风。[资料1]', missing=['缺少依据']),
                           response('渔船回港避风。[资料1]', 'partial', missing=['仍缺少依据'])], supplement)
        result = qa.answer('麦莎台风应对措施', allow_web=True)
        self.assertEqual(len(supplement.calls), 1)
        self.assertEqual(result['answer_meta']['local_answerability'], 'partial')

    def test_failed_search_keeps_supported_partial_answer(self):
        supplement = Supplement([])
        qa = self.service([response('回港避风。[资料1]', 'partial', missing=['其他措施'])], supplement)
        result = qa.answer('麦莎台风应对措施', allow_web=True)
        self.assertEqual(result['answer_meta']['used_mode'], 'llm')
        self.assertIn('回港避风', result['answer'])
        self.assertEqual(result['answer_meta']['web_supplement_state'], 'unavailable')

    def test_mode_calculation_missing_dictionary_is_not_replaced_by_web_numbers(self):
        supplement = Supplement()
        qa = self.service([], supplement, local=False)
        result = qa.answer('用模式表计算哪些站超警', allow_web=True)
        self.assertEqual(result['answer_meta']['route'], 'structured_data_unavailable')
        self.assertEqual(supplement.calls, [])

    def test_current_question_searches_but_does_not_send_historical_evidence(self):
        supplement = Supplement()
        qa = self.service([response('缺少有效预警时段，无法确认当前情况。', 'insufficient', refs=())], supplement)
        result = qa.answer('当前台风是否影响浙江？', allow_web=True)
        self.assertTrue(supplement.calls[0][1])
        self.assertEqual(result['answer_meta']['local_evidence_count'], 0)
        self.assertNotIn('组织渔船', str(self.payloads))

    def test_season_historical_and_defense_questions_all_get_public_queries(self):
        for question in ('台风只在夏天有吗？几月到几月要特别防？', '2019年利奇马台风造成多少损失？',
                         '浙江遇到海浪应该怎么避险？'):
            queries = public_search_queries(question)
            self.assertTrue(queries)
            self.assertNotIn('定义', ' '.join(queries))
        private_question = '内部项目SECRET_X91的2019年利奇马台风损失是多少？'
        self.assertNotIn('SECRET_X91', str(public_search_queries(private_question)))
        self.assertIn('2019', str(public_search_queries(private_question)))
        self.assertIn('利奇马', str(public_search_queries(private_question)))

    def test_public_read_time_is_not_a_publication_date(self):
        now = datetime(2026,10,15,tzinfo=timezone.utc)
        self.assertFalse(fresh_public_page({'read_at':now.isoformat()}, now))
        self.assertFalse(fresh_public_page({'published_at':'2020-10-15'}, now))
        self.assertTrue(fresh_public_page({'published_at':'2026-10-15 08:00:00'}, now))

