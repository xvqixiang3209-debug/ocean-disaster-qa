import json
import unittest

from marine_kb.qa import MarineQuestionService, _question_parts, _part_coverage
from marine_kb.web_supplement import public_search_queries, focused_public_text, canonical_public_url
from test_marine_qa import FakeStore, source


QUESTION = '什么条件下要启动“停课停工停产停运停业”？由谁决定、怎么公告？'


class SubquestionTests(unittest.TestCase):
    def test_fallback_splits_three_actual_facets(self):
        parts = _question_parts(QUESTION)
        self.assertEqual(len(parts), 3)
        self.assertIn('启动', parts[0]['question'])
        self.assertIn('决定', parts[1]['question'])
        self.assertIn('公告', parts[2]['question'])

    def test_one_supported_part_cannot_mark_whole_question_complete(self):
        parts = _question_parts(QUESTION)
        decoded = {'subquestion_coverage':[{'id':1,'status':'full','citations':[1]}]}
        coverage = _part_coverage(decoded, parts, 'full', {1})
        self.assertEqual([row['status'] for row in coverage], ['full','insufficient','insufficient'])

    def test_unknown_citation_does_not_cover_a_subquestion(self):
        parts = _question_parts(QUESTION)
        decoded = {'subquestion_coverage':[{'id':2,'status':'full','citations':[99]}]}
        self.assertEqual(_part_coverage(decoded, parts, 'full', {1})[1]['status'], 'insufficient')

    def test_tracking_parameters_do_not_create_duplicate_policy_sources(self):
        self.assertEqual(canonical_public_url('https://www.example.gov.cn/a.html?eqid=tracking&utm_source=feed'),
                         'https://www.example.gov.cn/a.html')
        self.assertEqual(canonical_public_url('https://www.example.gov.cn/a?id=123'),
                         'https://www.example.gov.cn/a?id=123')

    def test_long_webpage_retains_decision_and_publication_at_the_end(self):
        introduction = '\n\n'.join(['五停工作需要认真组织启动条件预警响应落实。' * 30 for _ in range(12)])
        decision = '规范工作流程：行业主管部门向属地政府提出五停建议，属地政府决定采取措施，各行业和镇村负责落实。'
        announcement = '畅通信息渠道：通过广播、电视、短信、网站、微博微信等渠道发布五停信息，并将指令直达责任人。'
        body = introduction + '\n\n' + decision + '\n\n' + announcement
        focused = focused_public_text(body, QUESTION, ['由谁决定？','怎么公告？'])
        self.assertIn(decision, focused)
        self.assertIn(announcement, focused)
        self.assertLessEqual(len(focused), 2400)

    def test_response_searches_cover_conditions_and_procedures(self):
        queries = public_search_queries('什么时候可以降级或终止响应？要走什么程序？',
                                        subquestions=['何时降级或终止响应？','要走什么程序？'])
        self.assertTrue(any('终止' in query for query in queries))
        self.assertTrue(any('审批' in query for query in queries))
        self.assertTrue(all('海洋灾害' in query for query in queries))

    def test_scope_passage_is_kept_with_procedure_evidence(self):
        scope = '本预案适用于自然资源部组织开展的海洋灾害观测预警和调查评估工作，不是各地方救灾预案。'
        body = scope + '\n\n' + ('应急程序签发审批调整响应工作需要开展会商。' * 100)
        focused = focused_public_text(body, '响应终止需要什么程序？', ['要走什么程序？'])
        self.assertIn(scope, focused)

    def test_missing_facets_get_their_own_searches_without_researching_condition(self):
        queries = public_search_queries(QUESTION, subquestions=['由谁决定？','怎么公告？'])
        self.assertTrue(any('决定' in query for query in queries))
        self.assertTrue(any('通告' in query for query in queries))
        self.assertFalse(any('启动条件' in query for query in queries))
        self.assertTrue(all('浙江' in query and '"五停"' in query for query in queries))

    def test_local_and_web_are_synthesized_by_subquestion(self):
        lookups, payloads = [], []
        local = source('浙江五停指引：停课停工停产停运停业达到规定条件启动，停止户外活动并开展避险。',
                       title='浙江五停工作指引',filename='浙江五停工作指引.txt',metadata={'external_allowed':True})
        web = source('本地五停措施经指挥部决定，通过正式通告发布。', source_id='web:policy', chunk_id='web:2',
                     url='https://www.example.gov.cn/policy', status='public_web',
                     metadata={'external_allowed':True,'source_type':'public_web'})
        class Web:
            enabled=True
            def fetch(self, question, *, dynamic=False, subquestions=None):
                lookups.extend(subquestions)
                return [web], {'web_supplement_state':'success','web_default_jurisdiction':'浙江'}
        def caller(model, system, prompt, **options):
            payload=json.loads(prompt)
            payloads.append(payload)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question':QUESTION,'subquestions':['启动条件是什么？','由谁决定？','怎么公告？']})
            mixed = any(row['metadata'].get('source_type')=='public_web' for row in payload['evidence'])
            rows=[{'id':1,'status':'full','citations':[1]},
                  {'id':2,'status':'full' if mixed else 'insufficient','citations':[2] if mixed else []},
                  {'id':3,'status':'full' if mixed else 'insufficient','citations':[2] if mixed else []}]
            return json.dumps({'answerability':'full',
                'answer':'达到规定条件启动。[资料1]'+('经指挥部决定，以正式通告发布。[资料2]' if mixed else ''),
                'evidence_quotes':[{'citation':index,'passage_id':1} for index in ([1,2] if mixed else [1])],
                'missing_information':[], 'subquestion_coverage':rows})
        service=MarineQuestionService(FakeStore([local]),model_caller=caller,external_enabled=True,
                                      structured_answers=True,web_supplement=Web(),search_policy='local_first')
        result=service.answer(QUESTION,allow_web=True)
        self.assertEqual(lookups,['由谁决定？','怎么公告？'])
        self.assertEqual(result['answer_meta']['local_answerability'],'partial')
        self.assertEqual(result['answer_meta']['answerability'],'full')
        self.assertEqual(len(result['rag_sources']),1)
        self.assertEqual(len(result['web_sources']),1)
        self.assertEqual([row['status'] for row in result['answer_meta']['subquestion_coverage']],['full']*3)
        self.assertEqual(len(payloads[-1]['subquestions']),3)

