from types import SimpleNamespace
from unittest.mock import Mock,patch
import json
import unittest

from forecast_runtime.answer_facts import delivery_facts
from marine_kb.business_validation import validate_business_delivery
from marine_kb.qa import MarineQuestionService,CITATION_FORMAT_REPAIR_PROMPT


class BusinessDeliveryTests(unittest.TestCase):
    def test_compound_data_must_survive_final_answer(self):
        meta={'required_business_facts':[{'kind':'elevation','value':76},
              {'kind':'marine_product','product':'WAV','values':[.69,.74]},
              {'kind':'marine_product','product':'SSW','values':[6.75,7.7]}]}
        validate_business_delivery('浪高0.69—0.74米；阵风6.75—7.70 m/s。地形高程为76.0米。',meta,{1})
        with self.assertRaisesRegex(ValueError,'missing_elevation'):
            validate_business_delivery('浪高0.69—0.74米；阵风6.75—7.70 m/s。无法提供高程。',meta,{1})
        with self.assertRaisesRegex(ValueError,'missing_citations'):
            validate_business_delivery('只能介绍一般方法，无法提供数值。',meta,set())

    def test_population_is_a_valid_cell_sum_when_nodata_exist(self):
        meta={'required_business_facts':[{'kind':'population','value':46254,'year':2020,'has_nodata':True}]}
        validate_business_delivery('2020年有效格网估计合计46,254人，另有4655个无数据格网，未按零处理。',meta,{1})
        with self.assertRaisesRegex(ValueError,'valid_cells_scope'):
            validate_business_delivery('2020年该范围有46254人，存在4655个缺测格网。',meta,{1})

    def test_ascii_metre_unit_before_chinese_prose_is_a_delivered_elevation(self):
        meta={'required_business_facts':[{'kind':'elevation','value':76}]}
        # All three real DeepSeek diagnostic samples used this valid format.
        for answer in ['该点地形高程为76 m，基准为EGM96[资料1]。',
                       '该点地表高程为76 m，来源ASTER GDEM V3[资料1]。',
                       '地形高程 76.0 米，垂直基准EGM96[资料1]。',
                       '地形高程为76m来源为ASTER GDEM V3[资料1]。',
                       '地形高程为76 m EGM96[资料1]。']:
            with self.subTest(answer=answer):
                validate_business_delivery(answer,meta,{1})

    def test_elevation_delivery_still_rejects_wrong_quantity_or_non_length_unit(self):
        meta={'required_business_facts':[{'kind':'elevation','value':76}]}
        for answer in ['地形高程为760米[资料1]。','地形高程为76.5米[资料1]。',
                       '地形高程为76 m/s[资料1]。','地形高程为76 mm[资料1]。',
                       '地形高程为76米/秒[资料1]。']:
            with self.subTest(answer=answer),self.assertRaisesRegex(ValueError,'missing_elevation'):
                validate_business_delivery(answer,meta,{1})

    def test_ready_tool_failure_never_turns_into_general_explanation(self):
        original={'answer':'生成失败','answer_meta':{'used_mode':'model_error','business_state':'ready',
                                                   'activity_data_driven':True}}
        service=SimpleNamespace(general_explanations=True,external_enabled=True,model_caller=Mock(),
                                _answer=Mock(return_value=original),_call_model=Mock())
        self.assertIs(MarineQuestionService.answer(service,'有海况和地形数据吗'),original)
        service._call_model.assert_not_called()

    def test_partial_tool_table_survives_general_explanation_fallback(self):
        original={'answer':'历史与预报增水249.51cm、312cm，峰值差62.49cm[资料1]。',
                  'rag_sources':[{'citation_id':1}],
                  'answer_meta':{'used_mode':'business_data','business_state':'partial',
                                 'answerability':'partial','computed_table_fallback':True}}
        service=SimpleNamespace(general_explanations=True,external_enabled=True,model_caller=Mock(),
                                _answer=Mock(return_value=original),_call_model=Mock())
        self.assertIs(MarineQuestionService.answer(service,'历史和预报增水比较'),original)
        service._call_model.assert_not_called()

    def test_tool_location_clarification_never_gets_unverified_general_supplement(self):
        for state in ('needs_location','needs_scope','needs_bounds'):
            original={'answer':'请提供具体岸段或经纬度。','answer_meta':{
                'used_mode':'llm','business_state':state,'activity_data_driven':True,'answerability':'partial'}}
            service=SimpleNamespace(general_explanations=True,external_enabled=True,model_caller=Mock(),
                _answer=Mock(return_value=original),_call_model=Mock())
            with self.subTest(state=state):
                self.assertIs(MarineQuestionService.answer(service,'浙江能赶海吗'),original)
                service._call_model.assert_not_called()

    def test_required_facts_are_taken_from_all_compound_items(self):
        result={'kind':'bundle','items':[{'kind':'activity','products':[{
            'state':'ready','product':'WAV','minimum':{'value':.68849},'maximum':{'value':.73932},'unit':'m'}]},
            {'kind':'spatial','terrain':{'state':'ready','elevation_m':76,'vertical_datum':'EGM96'}}]}
        facts=delivery_facts(result)
        self.assertEqual([row['kind'] for row in facts],['marine_product','elevation'])
        self.assertEqual(facts[0]['values'],[.69,.74])

    def test_static_spatial_source_label_is_distinct_from_hypothesis_trial(self):
        hit={'source_id':'forecast:example','chunk_id':'example','title':'地形查询',
             'text':'地形高程76米，EGM96基准。','status':'indexed',
             'metadata':{'source_type':'business_forecast','trial':False}}
        row=MarineQuestionService._public_sources('高程多少',[hit])[0]
        self.assertEqual(row['evidence_kind'],'本地空间栅格查询')
        self.assertEqual(row['review_label'],'本地栅格查询 · 数据年份和基准见原始结果')
        self.assertFalse(row['metadata']['trial'])
        hit['metadata']['trial']=True
        row=MarineQuestionService._public_sources('高程多少',[hit])[0]
        self.assertIn('假设试算',row['review_label'])
        self.assertEqual(row['evidence_kind'],'业务数据查询及程序计算')

    def test_editorial_rewrite_losing_tool_fact_retains_validated_first_answer(self):
        first='地形高程为76米[资料1]。浪高0.69—0.74米，阵风6.75—7.70米/秒[资料1]。'
        second='浪高0.69—0.74米，阵风6.75—7.70米/秒[资料1]。高程无法提供。'
        def response(answer):return json.dumps({'answerability':'partial','answer':answer,
            'evidence_quotes':[{'citation':1,'passage_id':1}],'missing_information':[]})
        service=MarineQuestionService(None,external_enabled=True,model_caller=Mock(side_effect=[response(first),response(second)]))
        hit={'source_id':'forecast:test','text':'地形高程76米。浪高0.69至0.74米；阵风6.75至7.70米/秒。',
             'metadata':{'source_type':'business_forecast','trial':True}}
        meta={'as_of':'2026-10-17','subquestions':[],
              'required_business_facts':[{'kind':'elevation','value':76},
                {'kind':'marine_product','product':'WAV','values':[.69,.74]},
                {'kind':'marine_product','product':'SSW','values':[6.75,7.7]}]}
        with patch('marine_kb.qa.readability_issues',side_effect=[['需要编辑改写'],[]]):
            result=service._organized_answer('海况和高程多少','test',[hit],meta)
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertEqual(result['answer'],first)
        self.assertEqual(service.model_caller.call_count,2)

    def test_uncited_introduction_repair_retains_known_facts_and_requires_inline_citation(self):
        draft='地形高程为76米。\n\n该点地形高程76米[资料1]。'
        fixed='地形高程为76米[资料1]。'
        calls=[]
        def caller(model,system,prompt,**kwargs):
            payload=json.loads(prompt);calls.append(payload)
            if len(calls)==2:
                detail=payload['validation_details']['invalid_draft_lines'][0]
                self.assertEqual(detail['error'],'uncited_quantity')
                self.assertEqual(detail['uncited_quantities'],['76'])
                self.assertNotIn('unsupported_quantities',detail)
                self.assertEqual(payload['untrusted_draft_for_citation_repair']['answer'],draft)
            return json.dumps({'answerability':'full','answer':draft if len(calls)==1 else fixed,
                'evidence_quotes':[{'citation':1,'passage_id':1}],'missing_information':[]})
        service=MarineQuestionService(None,external_enabled=True,model_caller=caller)
        hit={'source_id':'forecast:test','text':'该点地形高程76米，垂直基准EGM96。',
             'metadata':{'source_type':'business_forecast','trial':False}}
        result=service._organized_answer('高程多少','test',[hit],{'as_of':'2026-10-17',
            'subquestions':[],'required_business_facts':[{'kind':'elevation','value':76}]})
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertEqual(result['answer'],fixed)
        self.assertEqual(len(calls),2)

    def test_bounded_citation_only_repair_preserves_body_after_two_uncited_drafts(self):
        uncited='浪高0.69—0.74米，阵风6.75—7.70米/秒。'
        draft=uncited+'\n这是模式预报[资料1]。'
        calls=[]
        def caller(model,system,prompt,**kwargs):
            calls.append((system,json.loads(prompt)))
            if system==CITATION_FORMAT_REPAIR_PROMPT:
                self.assertEqual(calls[-1][1]['lines'],[{'line':0,'text':uncited}])
                return json.dumps({'line_citations':[{'line':0,'citations':[{'citation':1,'passage_id':1}]}],
                    'unsupported_lines':[]})
            return json.dumps({'answerability':'partial','answer':draft,
                'evidence_quotes':[{'citation':1,'passage_id':1}],'missing_information':[]})
        service=MarineQuestionService(None,external_enabled=True,model_caller=caller)
        hit={'source_id':'forecast:test','text':'浪高0.69至0.74米；阵风6.75至7.70米/秒。',
             'metadata':{'source_type':'business_forecast','trial':True}}
        meta={'as_of':'2026-10-17','subquestions':[],'business_state':'ready',
              'required_business_facts':[{'kind':'marine_product','product':'WAV','values':[.69,.74]},
                {'kind':'marine_product','product':'SSW','values':[6.75,7.7]}]}
        result=service._organized_answer('岸钓海况','test',[hit],meta)
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertEqual(result['answer'],uncited+' [资料1]\n这是模式预报[资料1]。')
        self.assertEqual(result['answer_meta']['citation_format_repairs'],1)
        self.assertEqual(len(calls),3)

    def test_citation_only_repair_cannot_attach_unrelated_or_invalid_evidence(self):
        for draft,passage in [('浪高999米。\n这是模式预报[资料1]。',1),('浪高0.74米。\n这是模式预报[资料1]。',999)]:
            def caller(model,system,prompt,**kwargs):
                if system==CITATION_FORMAT_REPAIR_PROMPT:
                    return json.dumps({'line_citations':[{'line':0,'citations':[{'citation':1,'passage_id':passage}]}],
                        'unsupported_lines':[]})
                return json.dumps({'answerability':'partial','answer':draft,
                    'evidence_quotes':[{'citation':1,'passage_id':1}]})
            service=MarineQuestionService(None,external_enabled=True,model_caller=caller)
            hit={'source_id':'forecast:test','text':'浪高为0.74米，是模式预报。',
                 'metadata':{'source_type':'business_forecast','trial':True}}
            with self.subTest(draft=draft,passage=passage):
                result=service._organized_answer('海况','test',[hit],{'as_of':'2026-10-17','subquestions':[],
                    'business_state':'ready','required_business_facts':[{'kind':'marine_product','product':'WAV','values':[.74]}]})
                self.assertEqual(result['answer_meta']['used_mode'],'model_error')
                self.assertEqual(result['rag_sources'],[])

    def test_citation_repair_requires_the_selected_passage_not_just_same_document(self):
        from marine_kb.qa import _apply_line_citation_choices,_evidence_passages
        text='这是与所问数据无关的历史说明。'*40+'浪高为0.74米。'
        raw=json.dumps({'answerability':'partial','answer':'浪高0.74米。\n这是历史说明[资料1]。',
                        'evidence_quotes':[{'citation':1,'passage_id':1}]})
        evidence=[{'text':text,'passages':_evidence_passages(text)}]
        with self.assertRaisesRegex(ValueError,'quantity_not_supported'):
            _apply_line_citation_choices(raw,{'line_citations':[{'line':0,'citations':[{'citation':1,'passage_id':1}]}],
                                             'unsupported_lines':[]},evidence)

    def test_official_water_rule_open_lower_temperature_boundary_is_preserved(self):
        meta={'official_activity_rules':True}
        # Actual WZ01 generation used this ambiguous inclusive-looking range.
        wrong='水质“优”且水温20—28℃为适宜，“良”或水温28—33℃为较适宜。'
        with self.assertRaisesRegex(ValueError,'official_rule_temperature_interval_boundary'):
            validate_business_delivery(wrong,meta,{1})
        for correct in ['较适宜的水温条件为28<水温≤33℃。',
                        '较适宜的水温条件为大于28且不超过33℃。',
                        '水温28—33℃（不含28）为较适宜。',
                        '该浴场历史实测水温28.2℃，水质优，报告较适宜。']:
            with self.subTest(answer=correct):validate_business_delivery(correct,meta,{1})

    def test_separate_leisure_fishery_management_does_not_imply_permit_exemption(self):
        meta={'official_activity_rules':True}
        wrong_sentences=[
            '保护区内休闲渔船垂钓另按休闲渔业规定办理，不适用上述个人海钓许可程序。',
            '保护区休闲渔船另按休闲渔业管理，所以参与者不需要保护区海钓许可证。',
            '保护区内休闲渔船垂钓免证，可直接海钓。',
            '个人海钓许可程序不适用于保护区内的休闲渔船垂钓。',
            '保护区休闲渔船的海况不能确认，但乘船者无需个人海钓许可。',
            '保护区休闲渔船海钓可以免办个人许可证吗？可以免办个人海钓许可证。']
        for answer in wrong_sentences:
            with self.subTest(answer=answer),self.assertRaisesRegex(ValueError,'official_rule_recreational_fishing_exemption'):
                validate_business_delivery(answer,meta,{1})
        correct_sentences=[
            '保护区内休闲渔船垂钓另按休闲渔业规定管理，不推定免证；个人海钓仍需取得许可。',
            '保护区内休闲渔船另按休闲渔业管理，不等于免办个人海钓许可。',
            '保护区内休闲渔船另按管理，不能据此认定不适用上述个人海钓许可程序。',
            '该条未规定休闲渔船免证，保护区个人海钓需取得许可。',
            '保护区内休闲渔船另按管理，不应将其理解为“不适用个人海钓许可程序”。',
            '保护区个人海钓须许可，休闲渔船另按休闲渔业管理，不能由此推定免证。',
            '保护区内休闲渔船另按管理，不能把第3条理解为免证条款。',
            '保护区内休闲渔船另按管理，并非不需要个人海钓许可。',
            '保护区内休闲渔船另按管理，不能由此推出可以免证。']
        for answer in correct_sentences:
            with self.subTest(answer=answer):validate_business_delivery(answer,meta,{1})

    def test_real_pr02_negated_confirmation_and_question_heading_are_not_waiver_assertions(self):
        meta={'official_activity_rules':True}
        # Captured first real PR02 draft: its legal boundary was correct, but
        # subject-bearing negation and a question heading were falsely blocked.
        actual='适用地区为浙江省马鞍列岛保护区。就现有资料看，不能确认“乘休闲渔船海钓”可以免办个人海钓许可证；资料明确提示，不能仅凭“另按休闲渔业管理规定管理”推定免办个人海钓许可证，具体手续需另向主管部门核实[资料1]。\n2. 能否免办个人海钓许可证：资料明确说明，不能仅据此推定免办第4条要求的个人海钓许可证，也不能推定不适用个人许可程序[资料1]。因此，现有证据不支持“可以免办”的结论。'
        validate_business_delivery(actual,meta,{1})
        for correct in ['保护区内乘休闲渔船海钓，可以免办个人海钓许可证吗？不能据此推定免证。',
                        '保护区内休闲渔船另按管理，是否可免证需向主管部门核实。',
                        '保护区内休闲渔船另按管理，现有证据不支持个人免证。']:
            with self.subTest(answer=correct):validate_business_delivery(correct,meta,{1})


if __name__=='__main__':unittest.main()
