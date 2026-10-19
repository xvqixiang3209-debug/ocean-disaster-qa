"""Regression cases found in the complete 160-question run."""
import json
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch

from forecast_runtime.service import period
from forecast_runtime.storage import BEIJING
from marine_kb.qa import MarineQuestionService,_decode_model_answer,_evidence_passages,current_context
from marine_kb.guidance import decode_guidance
from marine_kb.web_supplement import public_search_queries
from test_marine_qa import FakeStore
import test_forecast_runtime as forecast_fixture

class QueryRegressionTests(unittest.TestCase):
    def setUp(self):
        fixture=forecast_fixture.ForecastTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        for name in ('r','now','record','start','end'):setattr(self,name,getattr(fixture,name))
    def test_chinese_cross_day_interval_keeps_hours(self):
        start,end=period('2026年10月17日21时至10月20日21时舟山浪高多少？',now=self.now)
        self.assertEqual(start,datetime(2026,10,17,21,tzinfo=BEIJING))
        self.assertEqual(end-start,timedelta(hours=72))

    def test_iso_interval_and_explicit_hour_duration(self):
        start,end=period('2026-10-17T21:00至2026-10-20T21:00',now=self.now)
        self.assertEqual(start.hour,21);self.assertEqual(end-start,timedelta(hours=72))
        start,end=period('2026年10月17日21时未来72小时',now=self.now)
        self.assertEqual(end-start,timedelta(hours=71))

    def test_two_chinese_coordinate_points_are_not_a_regional_proxy(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            value=self.r.query('未来3小时舟山近岸东经121.05度北纬28.05度和近海东经121.1度北纬28.1度的浪高是多少？')
        self.assertEqual(value['kind'],'bundle')
        self.assertEqual([r['location']['lon'] for r in value['items']],[121.05,121.1])
        self.assertTrue(all(not r['location']['regional_representative_assumed'] for r in value['items']))

    def test_system_description_is_not_an_active_typhoon_lookup(self):
        self.assertFalse(self.r.matches('当前这个台风海洋灾害问答系统能做什么？'))
        self.assertFalse(self.r.matches('当前这个系统的台风实况和网格多久更新？'))

    def test_future_named_batch_does_not_hide_issued_run(self):
        self.r.store.register_grid(dict(self.record,id='future',batch=(self.now+timedelta(hours=2)).isoformat()))
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            result=self.r.grid('surge',121.05,28.05,self.start,self.end)
        self.assertEqual(result['batch'],self.record['batch']);self.assertFalse(result['future_batch'])

    def test_complete_issued_run_is_preferred_to_partial_time_coverage(self):
        newer=dict(self.record,id='partial',batch=self.now.isoformat(),
                   valid_from=(self.start+timedelta(hours=1)).isoformat())
        self.r.store.register_grid(newer)
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            grid=self.r.grid('surge',121.05,28.05,self.start,self.end)
            coasts=self.r.coastal(self.start,self.end)
        self.assertEqual(grid['batch'],self.record['batch'])
        self.assertEqual(coasts['selected_batch'],self.record['batch'])

class AnswerRecoveryTests(unittest.TestCase):
    def test_incomplete_answer_can_cite_valid_known_facts(self):
        text='当地政府组织渔船回港避风，未给出本次完成时限。'
        evidence=[{'text':text,'passages':_evidence_passages(text)}]
        raw=json.dumps({'answerability':'insufficient','answer':'已有回港避风要求，但缺少完成时限。[资料1]',
                        'evidence_quotes':[{'citation':1,'passage_id':1}]})
        answer,coverage,refs=_decode_model_answer(raw,evidence)
        self.assertEqual(coverage,'partial');self.assertEqual(refs,{1})
        invalid=json.loads(raw);invalid['answer']=invalid['answer'].replace('资料1','资料9')
        with self.assertRaises(ValueError):
            _decode_model_answer(json.dumps(invalid),evidence)

    def test_hypothetical_and_method_questions_are_not_live_observations(self):
        for question in ['假设舟山发布红色警报，我现在该做什么？','未来重现期水位怎么算？','当前这个系统的数据哪里来？']:
            self.assertFalse(current_context(question)[1],question)
        self.assertTrue(current_context('当前舟山浪高多少？')[1])

    def test_specialist_questions_keep_their_actual_search_topic(self):
        for question,term in [('离岸流怎么自救？','离岸流'),('台风天要透析拿药怎么办？','透析'),
                              ('家里进水后如何清理消毒？','消毒'),('台风前要备哪些物资？','家庭应急物资')]:
            self.assertIn(term,' '.join(public_search_queries(question)))
        self.assertNotIn('SECRET_X91',' '.join(public_search_queries('内部SECRET_X91要准备应急物资')))

    def test_general_explanation_is_marked_and_does_not_become_a_source(self):
        def caller(model,system,prompt,**kwargs):
            p=json.loads(prompt)
            if 'normalized_question' in system:return json.dumps({'normalized_question':p['question']})
            if '"scope":"general"' in system:
                return json.dumps({'scope':'general','explanation':'风暴增水表示天气作用引起的水位升高。可以先区分天文潮与总潮位，再结合位置和预报时段分析。','missing_inputs':['具体位置和时段']},ensure_ascii=False)
            return json.dumps({'answerability':'insufficient','answer':'暂缺当前有效的业务记录，不能确认。'})
        qa=MarineQuestionService(FakeStore(),model_caller=caller,external_enabled=True,structured_answers=True,general_explanations=True)
        result=qa.answer('目前本次风暴增水有多大？')
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertTrue(result['answer_meta']['general_knowledge_used'])
        self.assertIn('未作为已核验的当前业务事实',result['answer'])
        self.assertEqual(result['rag_sources'],[])

    def test_general_explanation_cannot_supply_missing_business_numbers(self):
        for answer in ['目前最大增水为5米，全部安全。','当前已发布红色警报，现在肯定安全。','请看[资料1]并打13812345678。']:
            with self.assertRaises(ValueError):
                decode_guidance(json.dumps({'scope':'general','explanation':answer*3,'missing_inputs':[]}))

if __name__=='__main__':unittest.main()
