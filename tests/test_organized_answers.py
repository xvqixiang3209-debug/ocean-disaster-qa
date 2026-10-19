import json
from pathlib import Path
import tempfile
import unittest

from marine_kb.model_authorization import EvidenceAuthorization, evidence_key, text_digest
from marine_kb.qa import MarineQuestionService, _decode_model_answer, _evidence_passages
from test_marine_qa import FakeCaller, FakeStore, source


def organized(answer='渔船应回港避风。[资料1]', quote='组织渔船回港避风'):
    return json.dumps({'answerability': 'full', 'answer': answer,
                       'evidence_quotes': [{'citation': 1, 'quote': quote}], 'missing_information': []}, ensure_ascii=False)


class OrganizedAnswerTests(unittest.TestCase):
    def test_numbered_passages_preserve_ocr_text_without_model_copying(self):
        text = '海浪是由风引起的海面波动现象袁主要包括风浪和涌浪遥'
        evidence = [{'text': text, 'passages': _evidence_passages(text)}]
        raw = json.dumps({'answerability': 'full', 'answer': '海浪包括风浪和涌浪。[资料1]',
                          'evidence_quotes': [{'citation': 1, 'passage_id': 1}]})
        self.assertEqual(_decode_model_answer(raw, evidence)[2], {1})
        self.assertEqual(evidence[0]['passages'][0]['text'], text)
        self.assertNotIn('quote', json.loads(raw)['evidence_quotes'][0])

    def test_unknown_or_non_integer_passage_is_rejected(self):
        evidence = [{'text': '海浪主要包括风浪和涌浪。', 'passages': _evidence_passages('海浪主要包括风浪和涌浪。')}]
        for passage_id in (99, '1', True):
            with self.subTest(passage_id=passage_id), self.assertRaises(ValueError):
                _decode_model_answer(json.dumps({'answerability': 'full', 'answer': '海浪包括风浪和涌浪。[资料1]',
                    'evidence_quotes': [{'citation': 1, 'passage_id': passage_id}]}), evidence)

    def test_selector_still_requires_all_citations_and_supported_numbers(self):
        text = '海浪主要包括风浪和涌浪，记录波高4米。'
        evidence = [{'text': text, 'passages': _evidence_passages(text)}] * 2
        for answer in ('海浪包括风浪和涌浪。[资料1][资料2]', '记录波高999米。[资料1]'):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                _decode_model_answer(json.dumps({'answerability': 'full', 'answer': answer,
                    'evidence_quotes': [{'citation': 1, 'passage_id': 1}]}), evidence)

    def test_model_receives_passages_and_can_repair_missing_citation(self):
        calls = []
        def caller(model, system, prompt, **kwargs):
            payload = json.loads(prompt)
            calls.append(payload)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question': payload['question']})
            evidence = payload['evidence'][0]
            self.assertNotIn('text', evidence)
            self.assertIn('组织渔船回港避风', str(evidence['passages']))
            quotes = [{'citation': 1, 'passage_id': 1}] if 'validation_feedback' in payload else []
            return json.dumps({'answerability': 'full', 'answer': '渔船应回港避风。[资料1]', 'evidence_quotes': quotes})
        result = self.service(caller).answer('麦莎台风应对措施')
        self.assertEqual(result['answer_meta']['used_mode'], 'llm')
        self.assertEqual(result['answer_meta']['generation_attempts'], 2)
        self.assertEqual(calls[-1]['validation_feedback'], 'citation_without_supporting_quote')

    def service(self, caller, sources=None, authorizer=None):
        return MarineQuestionService(FakeStore(sources if sources is not None else [source(metadata={'external_allowed': True})]),
                                     model_caller=caller, external_enabled=True, structured_answers=True,
                                     require_model_answer=True, evidence_authorizer=authorizer)

    def test_real_answer_replaces_excerpts_and_has_supporting_quote(self):
        result = self.service(FakeCaller(answer=organized())).answer('麦莎台风应对措施')
        self.assertEqual(result['answer_meta']['used_mode'], 'llm')
        self.assertIn('渔船应回港避风', result['answer'])
        self.assertNotIn('原文节选', result['answer'])
        self.assertEqual(len(result['rag_sources']), 1)

    def test_fabricated_support_is_repaired_once_then_blocked(self):
        caller = FakeCaller(answer=organized(quote='沿海所有人员全部立即撤离'))
        result = self.service(caller).answer('麦莎台风应对措施')
        self.assertEqual(result['answer_meta']['used_mode'], 'model_error')
        self.assertEqual(result['answer_meta']['validation_error'], 'quote_not_in_source')
        self.assertEqual(len(caller.calls), 3)  # planner + two answer attempts
        self.assertEqual(result['rag_sources'], [])
        self.assertNotIn('沿海所有人员', result['answer'])

    def test_failure_does_not_silently_return_excerpts(self):
        result = self.service(FakeCaller(fail=True)).answer('麦莎台风应对措施')
        self.assertEqual(result['answer_meta']['used_mode'], 'model_error')
        self.assertNotIn('原文节选', result['answer'])
        self.assertNotIn('private-token', result['answer'])

    def test_unauthorized_body_does_not_enter_prompt(self):
        caller = FakeCaller(answer=json.dumps({'answerability':'insufficient', 'answer':'缺少获准的资料，暂时无法回答。'}))
        result = self.service(caller, [source('麦莎防御措施：回港避风。PRIVATE_DOCUMENT')]).answer('麦莎台风应对措施')
        self.assertNotIn('PRIVATE_DOCUMENT', str(caller.calls))
        self.assertEqual(result['answer_meta']['evidence_sent_count'], 0)
        self.assertEqual(result['answer_meta']['used_mode'], 'no_evidence')

    def test_current_question_does_not_send_archival_evidence(self):
        caller = FakeCaller(answer=json.dumps({'answerability':'insufficient', 'answer':'未接入实时数据，无法判断这次台风的影响。'}))
        result = self.service(caller).answer('这次台风对我这里影响大吗？')
        self.assertNotIn('组织渔船', str(caller.calls))
        self.assertEqual(result['answer_meta']['used_mode'], 'no_evidence')

    def test_authorization_is_bound_to_exact_content(self):
        with tempfile.TemporaryDirectory() as directory:
            hit = source()
            path = Path(directory) / 'consent.json'
            path.write_text(json.dumps({'schema_version':1, 'entries':{evidence_key(hit):text_digest(hit['text'])}}),encoding='utf-8')
            auth = EvidenceAuthorization(path)
            self.assertTrue(auth.allows(hit))
            self.assertFalse(auth.allows({**hit, 'text':hit['text']+'新增材料'}))
            self.assertFalse(auth.allows({**hit, 'chunk_id':2}))
            result = self.service(FakeCaller(answer=organized()), [hit], authorizer=auth.allows).answer('麦莎台风应对措施')
            self.assertEqual(result['answer_meta']['used_mode'],'llm')

    def test_explicit_revocation_overrides_corpus_consent(self):
        caller = FakeCaller(answer=json.dumps({'answerability':'insufficient','answer':'资料未获准发送，暂时无法回答。'}))
        hit = source('麦莎防御措施：回港避风。REVOKED_BODY', metadata={'external_permission_override':False})
        result = self.service(caller, [hit], authorizer=lambda item:True).answer('麦莎台风应对措施')
        self.assertNotIn('REVOKED_BODY', str(caller.calls))
        self.assertEqual(result['answer_meta']['evidence_sent_count'],0)

    def test_subquery_cannot_use_observation_rules_as_warning_rules(self):
        question='海浪警报标准中的24小时是什么意思？'
        hit=source('海浪观测规范24小时。PRIVATE_OBSERVATION', title='海洋观测规范', filename='海洋观测规范.pdf',
                   category='标准规范', status='approved', metadata={'external_allowed':True})
        calls=[]
        def caller(model, system, prompt, **kwargs):
            calls.append(prompt)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question':question,'retrieval_queries':['海浪观测规范24小时']})
            return json.dumps({'answerability':'insufficient','answer':'缺少对应的警报标准，无法确认。'})
        result=self.service(caller,[hit]).answer(question)
        self.assertNotIn('PRIVATE_OBSERVATION',str(calls))
        self.assertEqual(result['answer_meta']['used_mode'],'no_evidence')
