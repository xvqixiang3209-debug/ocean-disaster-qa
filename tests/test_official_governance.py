from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from marine_kb.governance import draft_markers, reference_scope_allowed
from marine_kb.ingestion import split_units
from marine_kb.parsers import parse_file
from marine_kb.retrieval import evidence_focus, rank_candidates
from marine_kb.qa import MarineQuestionService, _quote
from marine_kb.storage import KnowledgeStore
from tools.review_official_sources import apply, prepare


class GovernanceTests(unittest.TestCase):
    def test_unit_answer_keeps_the_requested_variable(self):
        quote = _quote('潮高的单位是什么？', '潮高的单位为厘米（cm）。波高的单位为米（m）。')
        self.assertIn('潮高的单位为厘米', quote)
        self.assertNotIn('波高', quote)
        self.assertFalse(evidence_focus('潮高的单位是什么？','布放点海底到潮高基准面的高度，单位为米。'))
        self.assertFalse(evidence_focus('潮高的单位是什么？','公式中h为观测时潮高，单位为米。'))

    def test_forecast_amount_does_not_define_a_term(self):
        item = {'text':'预计将出现200厘米风暴增水。','status':'indexed'}
        hits = rank_candidates('什么是风暴增水？',[item])
        self.assertFalse(hits[0]['retrieval_details']['direct_evidence'])
        item['text'] = '风暴增水是由于气象因素导致的海面升高。'
        self.assertTrue(rank_candidates('什么是风暴增水？',[item])[0]['retrieval_details']['direct_evidence'])

    def test_unconfirmed_model_calculation_cannot_use_unrelated_history(self):
        # This route must finish before retrieval, even with an unusable store.
        result = MarineQuestionService(None).answer('根据模式表计算各站超警级别')
        self.assertEqual(result['answer_meta']['route'],'structured_data_unavailable')
        self.assertEqual(result['rag_sources'],[])

    def test_published_notice_referencing_draft_is_not_itself_draft(self):
        value = draft_markers('工作提示单.pdf','按照《联合指挥体系方案（征求意见稿）》开展培训。')
        self.assertFalse(value['is_draft'])
        self.assertTrue(value['references_draft'])
        self.assertTrue(draft_markers('应急预案（征求意见稿）.pdf','正文')['is_draft'])
        self.assertTrue(draft_markers('技术导则.pdf','HY/T × × ×—202X\n（报批稿）')['is_draft'])

    def test_unknown_standard_currency_allows_explicit_version_not_generic_rule(self):
        item = {'title':'海浪预警报产品制作规范','metadata':{'review_scope':'versioned_reference','standard_id':'HY/T 0375-2023'}}
        self.assertFalse(reference_scope_allowed('海浪警报标准是什么？',item))
        self.assertTrue(reference_scope_allowed('按照HY/T 0375-2023，海浪警报产品制作规范规定什么？',item))
        self.assertTrue(reference_scope_allowed('海浪预报产品有哪些类型？',item))
        item['metadata'].update(edition_current_verified=True,edition_checked_at=date.today().isoformat())
        self.assertTrue(reference_scope_allowed('海浪警报标准是什么？',item))
        item['metadata']['edition_checked_at'] = '2000-01-01'
        self.assertFalse(reference_scope_allowed('海浪警报标准是什么？',item))

    def test_observation_rules_cannot_substitute_for_warning_rules(self):
        item = {'title':'海洋观测规范','filename':'海洋观测规范.pdf','category':'标准规范',
            'status':'approved','text':'海浪观测。海浪警报标准24小时。','metadata':{}}
        hits = rank_candidates('海浪警报标准中的24小时是什么意思？',[item])
        self.assertFalse(any(row['retrieval_details']['direct_evidence'] for row in hits))

    def test_batch_admission_leaves_structured_and_ocr_sources_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = KnowledgeStore(root/'knowledge.sqlite3')
            raw = root/'files/raw'; raw.mkdir(parents=True)
            for number, metadata in enumerate(({}, {'requires_human_review':True}, {'ingest_mode':'structured_only'})):
                path = raw/f'{number}.txt'; path.write_text('海洋术语：海浪是海面波动现象。',encoding='utf-8')
                sid,_ = store.register(filename=f'{number}.txt',relative_path=f'官方/{number}.txt',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size,raw_path=path,category='模式结果' if number==2 else '标准规范')
                store.finish(sid,status='pending_review',quality_status='parsed',metadata={**metadata,'external_allowed':False},warnings=[],chunks=[] if number==2 else split_units(parse_file(path)['units']))
            plan = prepare(store.database)
            self.assertEqual(len(plan['admitted']),1)
            self.assertEqual(len(plan['remaining']),2)
            result = apply(store.database,plan,'test-batch')
            self.assertTrue(Path(result['backup']).is_file())
            self.assertEqual(result['status_counts'],{'approved':1,'pending_review':2})
            admitted = store.get_source(plan['admitted'][0]['source_id'])
            self.assertEqual(admitted['metadata']['review_scope'],'versioned_reference')
            self.assertFalse(admitted['metadata']['external_allowed'])
            self.assertFalse(admitted['metadata']['edition_current_verified'])
            self.assertEqual(result['origin_confirmed'],3)
