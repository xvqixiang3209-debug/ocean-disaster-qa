import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from marine_kb.answer_design import answer_design
from marine_kb.evidence_selection import select_evidence
from marine_kb.qa import MarineQuestionService
from marine_kb.qa import _validate_model_answer, _repair_details, _unit_equivalences
from marine_kb.web_supplement import OfficialWebSupplement
from test_marine_qa import FakeStore, source
from test_web_supplement import Search, Reader
from typhoon.web_search import WebSearchClient


QUESTION = '台风有哪些应对措施？'


def local_hit(**kwargs):
    return source('台风应对措施：组织渔船回港避风，转移危险区域人员。',
                  metadata={'external_allowed':True}, **kwargs)


def web_hit(**kwargs):
    return source('台风应对措施：关注官方预警，停止危险海域作业。',
                  title='台风防御措施', source_id='web:1', chunk_id='web:1', status='public_web',
                  url='https://www.example.gov.cn/defense.html',
                  metadata={'source_type':'public_web','external_allowed':True}, **kwargs)


class Supplement:
    enabled = True
    def __init__(self, hits=None, fail=False):
        self.hits = [web_hit()] if hits is None else hits
        self.fail, self.calls = fail, []
    def fetch(self, question, *, dynamic=False, subquestions=None, refresh=False):
        self.calls.append({'question':question,'dynamic':dynamic,'subquestions':subquestions,'refresh':refresh})
        if self.fail:
            raise TimeoutError('network')
        return self.hits, {'web_supplement_state':'success' if self.hits else 'unavailable','web_search_executed':True}


class AlwaysHybridTests(unittest.TestCase):
    def service(self, supplement, hits=None):
        self.payloads=[]
        self.model_options=[]
        def caller(model, system, prompt, **kwargs):
            payload=json.loads(prompt)
            self.payloads.append(payload)
            self.model_options.append(kwargs)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question':payload['question']})
            refs=list(range(1,len(payload['evidence'])+1))
            answer='渔船回港避风。[资料1]' if refs else '缺少获准证据，无法回答。'
            if len(refs)>1:
                answer += '关注官方预警，停止危险海域作业。[资料2]'
            return json.dumps({'answerability':'full' if refs else 'insufficient','answer':answer,
                               'evidence_quotes':[{'citation':index,'passage_id':1} for index in refs]})
        return MarineQuestionService(FakeStore([local_hit()] if hits is None else hits),
            model_caller=caller, external_enabled=True, structured_answers=True, web_supplement=supplement)

    def test_complete_local_evidence_always_searches_before_one_synthesis(self):
        supplement=Supplement()
        qa=self.service(supplement)
        result=qa.answer(QUESTION, allow_web=True)
        self.assertEqual(len(supplement.calls),1)
        self.assertTrue(supplement.calls[0]['refresh'])
        self.assertEqual(len(self.payloads),2)  # planner + single combined generation
        self.assertTrue(all(options.get('response_format') == {'type':'json_object'} for options in self.model_options))
        self.assertEqual(len(self.payloads[-1]['evidence']),2)
        self.assertEqual(result['answer_meta']['answer_policy'],'always_local_and_official_web')
        self.assertEqual(result['answer_meta']['route'],'hybrid_rag')
        self.assertEqual(len(result['rag_sources']),1)
        self.assertEqual(len(result['web_sources']),1)

    def test_search_failure_still_generates_from_authorized_local_evidence(self):
        qa=self.service(Supplement(fail=True))
        result=qa.answer(QUESTION, allow_web=True)
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertEqual(result['answer_meta']['web_supplement_state'],'unavailable')
        self.assertEqual(len(result['rag_sources']),1)

    def test_explicit_web_disable_is_respected(self):
        supplement=Supplement()
        result=self.service(supplement).answer(QUESTION, allow_web=False)
        self.assertEqual(supplement.calls,[])
        self.assertEqual(result['answer_meta']['web_supplement_state'],'not_requested')

    def test_private_source_body_does_not_enter_web_requests_or_model_without_consent(self):
        supplement=Supplement()
        hit=source('台风应对措施：PRIVATE_DOCUMENT_993。',metadata={'external_allowed':False})
        result=self.service(supplement,[hit]).answer(QUESTION,allow_web=True)
        self.assertNotIn('PRIVATE_DOCUMENT_993',str(supplement.calls))
        self.assertNotIn('PRIVATE_DOCUMENT_993',str(self.payloads))
        self.assertEqual(result['answer_meta']['blocked_evidence_count'],1)
        self.assertEqual(len(result['web_sources']),1)

    def test_unresolved_mode_dictionary_is_never_replaced_by_public_numbers(self):
        supplement=Supplement()
        result=self.service(supplement).answer('用模式表计算各站超警级别',allow_web=True)
        self.assertEqual(supplement.calls,[])
        self.assertEqual(result['answer_meta']['route'],'structured_data_unavailable')

    def test_local_retrieval_and_public_search_are_concurrent(self):
        local_started, web_started=threading.Event(),threading.Event()
        class Store(FakeStore):
            def search_candidates(self, query, **kwargs):
                local_started.set()
                if not web_started.wait(2):
                    raise AssertionError('web did not start while local retrieval waited')
                return super().search_candidates(query,**kwargs)
        class Web(Supplement):
            def fetch(self, *args, **kwargs):
                web_started.set()
                if not local_started.wait(2):
                    raise AssertionError('local retrieval did not run concurrently')
                return super().fetch(*args,**kwargs)
        qa=self.service(Web())
        qa.store=Store([local_hit()])
        result=qa.answer(QUESTION,allow_web=True)
        self.assertEqual(result['answer_meta']['local_selected_count'],1)
        self.assertEqual(result['answer_meta']['web_selected_count'],1)

    def test_repeat_questions_trigger_new_search_instead_of_supplement_cache(self):
        search=Search()
        web=OfficialWebSupplement(search,Reader(),enabled=True,reference_pages={})
        web.fetch('什么是台风？',refresh=True)
        _,meta=web.fetch('什么是台风？',refresh=True)
        self.assertEqual(len(search.queries),2)
        self.assertFalse(meta['web_supplement_cache_hit'])

    def test_irrelevant_search_results_do_not_enter_answer_evidence(self):
        noise=source('城市公园花卉展览与园林景观设计介绍。',title='海洋灾害相关资料',
                     source_id='web:noise',chunk_id='noise',url='https://www.example.gov.cn/noise')
        result=self.service(Supplement([noise,web_hit()])).answer(QUESTION,allow_web=True)
        self.assertEqual(result['answer_meta']['web_selected_count'],1)
        self.assertNotIn('园林',str(self.payloads[-1]))


class EvidenceSelectionTests(unittest.TestCase):
    def test_length_equivalence_does_not_mix_speed_or_area_units(self):
        evidence=[{'text':'最大增水为3.12 m，风速为3.12 m/s，面积为3.12 m²。'},
                  {'text':'海门站最大增水312厘米。'}]
        pairs=_unit_equivalences(evidence)
        self.assertEqual(len(pairs),1)
        self.assertEqual(pairs[0]['left']['value'],'3.12')
        self.assertEqual(pairs[0]['right']['value'],'312')
        self.assertIn('不表示站点',pairs[0]['note'])

    def test_full_width_decimal_in_source_supports_same_number_without_relaxing_values(self):
        evidence=[{'text':'冰块高度为1．5米。'}]
        _validate_model_answer('冰块高度为1.5米。[资料1]',evidence)
        with self.assertRaisesRegex(ValueError,'quantity_not_supported_by_cited_text'):
            _validate_model_answer('冰块高度为9.9米。[资料1]',evidence)

    def test_numeric_repair_points_to_unsupported_calculation_without_accepting_it(self):
        raw=json.dumps({'answer':'两份资料差0.01米。[资料1]'})
        details=_repair_details(raw,[{'text':'最大增水为3.12米。'}])
        self.assertEqual(details['invalid_draft_lines'][0]['unsupported_quantities'],['0.01'])
        self.assertEqual(details['allowed_citations'],['[资料1]'])

    def test_search_operator_domain_is_not_a_topic_match(self):
        self.assertFalse(WebSearchClient._relevant('海字字典 https://baike.baidu.com/item/海',
                         '海冰 成因 site:weather.com.cn', ()))

    def test_irrelevant_first_provider_does_not_stop_search_and_non_storm_keeps_subject(self):
        class Client(WebSearchClient):
            def __init__(self):
                super().__init__(provider='auto')
                self.queries=[]
            def _fetch_results(self, provider, query, timeout, deadline):
                self.queries.append(query)
                text = '海字的汉语解释' if len(self.queries) == 1 else '海冰的形成与危害'
                return [{'title':text,'snippet':text,'url':'https://www.example.gov.cn/article'}]
        client=Client()
        result=client.search_with_status('海冰 形成 原理 site:gov.cn',subject='海冰')
        self.assertEqual(len(result.attempts),2)
        self.assertEqual(result.state,'success')
        self.assertFalse(any('台风' in query for query in client.queries))

    def test_marine_response_does_not_use_rainstorm_announcement(self):
        hit=source('市防指根据雨情水情变化决定终止防暴雨应急响应。',title='宁德市防暴雨响应终止通知')
        self.assertEqual(select_evidence('浙江海洋灾害应急响应什么时候可以终止？',[hit]),[])

    def test_repealed_version_year_is_not_new_document_issue_year(self):
        evidence=[{'text':'自然资源部办公厅2026年6月15日印发预案，2022年1825号同时废止。',
                   'metadata':{'document_issued_at':'2026-06-15'}}]
        with self.assertRaisesRegex(ValueError,'document_version_year_mismatch'):
            _validate_model_answer('海洋灾害应急预案为2022年发布版本。[资料1]',evidence)
        _validate_model_answer('海洋灾害应急预案为2026年发布版本。[资料1]',evidence)

    def test_ranks_body_relevance_not_search_order_or_title_alone(self):
        noise=source('城市园林和公园游览设施建设情况。',title='台风防御措施')
        selected=select_evidence(QUESTION,[noise,web_hit()],limit=1)
        self.assertEqual(selected[0]['source_id'],'web:1')

    def test_combined_concepts_keep_distinct_sources_and_limit_total(self):
        hits=[source(text,title=title,source_id=str(i),chunk_id=i) for i,(title,text) in enumerate([
            ('台风定义','台风是热带气旋的一种，形成于热带洋面。'),
            ('风暴潮定义','风暴潮是气象因素造成的海面异常升高。'),
            ('海浪定义','海浪是海水表面的波动现象，通常由风引起。'),
            ('海啸定义','海啸是海底地震等扰动引起的长周期海浪。')])]
        selected=select_evidence('台风、风暴潮、海浪、海啸有什么区别？',hits,limit=4)
        self.assertEqual(len(selected),4)
        self.assertEqual({hit['title'] for hit in selected},{hit['title'] for hit in hits})

    def test_duplicate_pages_and_body_do_not_fill_source_budget(self):
        hit=web_hit()
        duplicate={**hit,'url':hit['url'].replace('https:','http:')}
        self.assertEqual(len(select_evidence(QUESTION,[hit,duplicate],limit=4)),1)


class GlobalStructureTests(unittest.TestCase):
    def test_json_mode_options_reach_actual_model_sdk_boundary(self):
        from model_layer import call_model
        captured=[]
        def create(**options):
            captured.append(options)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
        class Client:
            def __init__(self, **options):
                self.chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        config={'mock':{'api_key':'unit-test','base_url':'https://example.invalid','code':'mock','provider':'deepseek'}}
        with patch('model_layer.MODELS',config), patch('model_layer.OpenAI',Client):
            call_model('mock','Return JSON','test',stream=False,response_format={'type':'json_object'},max_tokens=4096)
            call_model('mock','Return plain text','test',stream=False)
        self.assertEqual(captured[0]['response_format'],{'type':'json_object'})
        self.assertEqual(captured[0]['max_tokens'],4096)
        self.assertNotIn('response_format',captured[1])
        self.assertNotIn('max_tokens',captured[1])

    def test_question_intents_get_appropriate_structures(self):
        cases={
            '什么是应急响应？':'definition',
            '应急响应和预警有什么区别？':'comparison',
            '终止响应需要什么程序？':'policy_procedure',
            '2019年利奇马最大增水多少？':'data_lookup',
            '2005年麦莎在浙江有哪些应对措施？':'historical_case',
            '海边遇到海浪如何避险？':'action_advice',
            '现在舟山可以出海吗？':'current_situation',
            '哪里可以查海浪预报产品？':'product_lookup',
            '风暴潮为什么会形成？':'cause_effect',
            '台风几月份最活跃？':'season',
            '有效波高怎么计算？':'calculation',
        }
        for question,expected in cases.items():
            with self.subTest(question=question):
                design=answer_design(question,[])
                self.assertEqual(design['kind'],expected)
                self.assertIn('synthesis',design)
                self.assertIn('self_check',design)
