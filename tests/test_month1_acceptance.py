import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from marine_kb.acceptance import CRITERIA, MonthOneAcceptance
from marine_kb.storage import KnowledgeStore


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = KnowledgeStore(self.root / 'knowledge.sqlite3')
        self.manifest = self.root / 'month1_acceptance_candidates.json'
        self.manifest.write_text(json.dumps({'cases': [{'id': 'M1-S01', 'question': '测试问题'}]}), encoding='utf-8')
        self.report_path = self.root / 'month1_acceptance_results.json'
        self.report = {'candidate_manifest_sha256': hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            'generated_at': '2026-10-15', 'candidates': [{'id': 'M1-S01', 'answer': '海门站3.12米。[资料1]',
                'sources': [{'source_id': 'source-1', 'locator': '第3页'}], 'technical_checks_passed': True}]}
        self.report_path.write_text(json.dumps(self.report), encoding='utf-8')
        self.service = MonthOneAcceptance(self.store, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        case = self.service.snapshot()['items'][0]
        return {'answer_sha256': case['answer_sha256'], 'manifest_sha256': case['manifest_sha256'],
            'reviewer': '验收人员', 'reference_answer': '海门站最大增水为3.12米。',
            'evidence_note': '已核对原件第3页，保留单位与时间口径', 'note': '已核定题目与来源范围',
            'sample_confirmed': True, 'criteria': dict.fromkeys(CRITERIA, True), 'verdict': 'passed'}

    def test_technical_success_never_counts_as_human_pass(self):
        summary = self.service.snapshot()['summary']
        self.assertEqual(summary['passed_count'], 0)
        self.assertFalse(summary['target_met'])
        result = self.service.review('M1-S01', self.payload())
        self.assertEqual(result['summary']['passed_count'], 1)
        self.assertEqual(result['summary']['sample_confirmed_count'], 1)
        self.assertFalse(result['summary']['target_met'])

    def test_pass_requires_sample_confirmation_reference_and_all_criteria(self):
        for key in ('sample_confirmed', 'reference_answer', 'criteria'):
            body = self.payload()
            body[key] = {} if key == 'criteria' else ''
            with self.assertRaises(ValueError):
                self.service.review('M1-S01', body)
        self.assertEqual(self.service.snapshot()['summary']['passed_count'], 0)

    def test_new_answer_invalidates_old_review_and_stale_submission(self):
        body = self.payload()
        self.service.review('M1-S01', body)
        self.report['candidates'][0]['answer'] = '修改后的回答。[资料1]'
        self.report_path.write_text(json.dumps(self.report), encoding='utf-8')
        result = self.service.snapshot()
        self.assertEqual(result['summary']['passed_count'], 0)
        self.assertTrue(result['items'][0]['review_stale'])
        self.assertEqual(len(result['items'][0]['review_history']), 1)
        with self.assertRaises(ValueError):
            self.service.review('M1-S01', body)

    def test_missing_evidence_cannot_pass_and_later_failure_supersedes_pass(self):
        body = self.payload()
        self.service.review('M1-S01', body)
        body['verdict'] = 'failed'
        self.assertEqual(self.service.review('M1-S01', body)['summary']['passed_count'], 0)
        self.report['candidates'][0]['sources'] = []
        self.report_path.write_text(json.dumps(self.report), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.service.review('M1-S01', self.payload())
