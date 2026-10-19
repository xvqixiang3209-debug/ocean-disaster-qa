"""Regressions from the real 160-question run, including adversarial claims."""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from forecast_runtime.storage import BEIJING
from forecast_runtime.service import period,as_of_cutoff
from forecast_runtime.routing import administrative_question,numeric_request,analogue_question
from forecast_runtime.recreation import activity_question
from forecast_runtime.answer_facts import delivery_facts,fact_text
from forecast_runtime.answer_facts import delivery_table
from forecast_runtime.recreation import requested_activities,unsupported_activities
from marine_kb.guidance import system_question
from marine_kb.qa import _question_parts,_decode_model_answer,_evidence_passages
from marine_kb.qa import _cite_tool_quantity_headings
from marine_kb.qa import MarineQuestionService
from marine_kb.business_validation import validate_business_delivery
from marine_kb.quantities import validate_quantity_units
from marine_kb.quantities import quantities


def test_meeting_and_issue_times_do_not_replace_valid_range():
    q='截至2026年10月17日21时30分的会商，采用2026年10月17日20时批次，给出2026年10月18日00时至10月21日00时舟山风浪。'
    a,b=period(q)
    assert (a.isoformat(),b.isoformat())==('2026-10-18T00:00:00+08:00','2026-10-21T00:00:00+08:00')
    assert as_of_cutoff(q)==datetime(2026,10,17,21,30,tzinfo=BEIJING)


def test_end_before_start_is_not_silently_replaced_with_today():
    with pytest.raises(ValueError):period('2026年10月21日00时至10月18日00时浪高多少')


def test_admin_policy_and_beach_visit_are_different_routes():
    assert administrative_question('浙江新一轮沿海警戒潮位核定的工作要求和时间进度是什么？')
    assert not numeric_request('浙江新一轮沿海警戒潮位核定的工作要求和时间进度是什么？')
    assert activity_question('2026年10月18日8时至18时去朱家尖南沙海边安全吗？有没有开放公告？')
    assert system_question('当前这个台风海洋灾害问答系统能做什么？不能做什么？')
    assert analogue_question('台风蔷琵跟历史上哪次台风比较像？那次损失多大？')


def test_normative_changes_and_hotel_refunds_do_not_request_marine_calculation():
    q='截至2026年10月17日，现行海洋灾害应急预案和警戒潮位核定规范有哪些关键变化？'
    assert administrative_question(q) and not numeric_request(q)
    assert not activity_question('因台风不能入住舟山朱家尖海边民宿酒店，订房能退吗？景区什么时候重新开？')
    assert activity_question('朱家尖酒店退订后，2026年10月18日还能游泳吗？')


def test_unconfigured_activity_is_retained_instead_of_substituted():
    assert activity_question('2026年10月18日朱家尖能玩摩托艇吗？')
    assert unsupported_activities('能赶海游泳岸钓船钓和玩摩托艇吗？')==['水上摩托艇']
    assert requested_activities('2026年10月18日能玩摩托艇吗？')==[]
    result={'start':'2026-10-18T08:00:00+08:00','end':'2026-10-18T18:00:00+08:00',
            'unsupported_requested_activities':['水上摩托艇'], 'tide':{
                'site':{'name':'参考站'},'high_tides':[{'time':'2026-10-18T14:04:00+08:00','value_cm':370}], 'low_tides':[]}}
    text=delivery_table(result,[{'kind':'marine_product','product':'SST','values':[24.22,24.38],'unit':'℃'}],question='能游泳和玩摩托艇吗？什么时候恢复开放？')
    assert '水上摩托艇：尚未接入' in text and '14:04' in text and '370 cm' in text and '复开时刻' in text


def test_single_requested_coastal_metric_does_not_force_an_unasked_peak():
    result={'kind':'coastal','swl_kind':'surge','requested_metrics':{'surge':True,'total':False},
        'results':[{'name':'甲镇','state':'ready','maximum':{'total_cm':685.86,'time':'2026-10-20T17:00:00+08:00'},
                    'series':[{'swl_cm':164.84,'time':'2026-10-18T18:00:00+08:00'}]}]}
    facts=delivery_facts(result)
    assert next(f for f in facts if f.get('metric')=='最高总潮位')['required'] is False
    assert next(f for f in facts if f.get('metric')=='最大风暴增水')['required'] is True


def test_phase_question_routes_to_combined_water_level():
    assert numeric_request('2026年10月18日00时至10月21日00时坎门岸段天文潮和风暴增水最不利叠加相位在什么时候？')


def test_planner_cannot_drop_explicit_reopening_subquestion():
    q='朱家尖南沙海边安全吗？有没有正式开放公告？关闭后多久恢复开放？'
    rows=_question_parts(q,['海边安全吗？'])
    assert len(rows)==3 and '恢复开放' in rows[-1]['question']


def test_coast_id_is_not_a_water_level_and_ties_are_retained():
    r={'kind':'coastal','swl_kind':'surge','maximum':1093,'requested_count':3,'usable_count':2,'missing_count':1,
       'results':[{'id':1093,'name':'甲镇','state':'ready','maximum':{'total_cm':685.86,'time':'2026-10-20T17:00:00+08:00'},
                   'series':[{'swl_cm':164.84,'time':'2026-10-18T18:00:00+08:00'}]},
                  {'id':7,'name':'乙镇','state':'ready','maximum':{'total_cm':500,'time':'2026-10-20T17:00:00+08:00'},
                   'series':[{'swl_cm':164.84,'time':'2026-10-18T18:00:00+08:00'}]}]}
    facts=delivery_facts(r)
    assert 1093 not in [f.get('value') for f in facts]
    assert len([f for f in facts if f.get('metric')=='最大风暴增水'])==2
    assert '685.86 cm' in fact_text(facts)


def test_same_number_in_wrong_unit_is_rejected():
    with pytest.raises(ValueError,match='unit_not_supported'):
        validate_quantity_units('浪高1厘米[资料1]。',[{'text':'浪高1米。'}])
    with pytest.raises(ValueError,match='unit_not_supported'):
        validate_quantity_units('浪高0.1—2米[资料1]。',[{'text':'浪高0.2—2米。'}])
    validate_quantity_units('浪高100厘米[资料1]。',[{'text':'浪高1米。'}])
    with pytest.raises(ValueError,match='unit_not_supported'):
        validate_quantity_units('规定1小时[资料1]。',[{'text':'浪高1米。'}])
    validate_quantity_units('规定48小时[资料1]。',[{'text':'规定48小时内报送。'}])


def test_clock_before_three_digit_measurement_is_not_a_thousands_separator():
    assert [float(r[0]) for r in quantities('高潮14:04，370厘米。')]==[370]
    assert [float(r[0]) for r in quantities('人口1,234人，费用12,345.67元。')]==[1234,12345.67]
    validate_quantity_units('高潮14:04，370厘米[资料1]。',[{'text':'高潮370厘米。'}])
    with pytest.raises(ValueError):
        validate_quantity_units('高潮14:04，371厘米[资料1]。',[{'text':'高潮370厘米。'}])


def test_program_record_age_has_an_explicit_duration_unit():
    validate_quantity_units('记录距查询时刻1.0小时[资料1]。',[
        {'text':'{"记录距查询时刻小时":1.0}', 'metadata':{'source_type':'business_forecast'}}])


def test_dated_bathing_observation_has_an_explicit_temperature_unit():
    evidence=[{'text':'{"observed_at":"2026-09-28","water_temperature_c":27.0}',
               'metadata':{'source_type':'business_forecast'}}]
    validate_quantity_units('历史水温27.0℃[资料1]。',evidence)
    with pytest.raises(ValueError):validate_quantity_units('历史水温28.0℃[资料1]。',evidence)


def test_net_decline_uses_the_signed_delta_with_an_explicit_cm_field():
    evidence=[{'text':'{"interval_trend":{"delta_cm":-6}}',
               'metadata':{'source_type':'business_forecast'}}]
    validate_quantity_units('采样区间净下降6 cm[资料1]。',evidence)
    with pytest.raises(ValueError):validate_quantity_units('采样区间净上升6 cm[资料1]。',evidence)


def test_generic_wind_wave_mapping_does_not_need_a_coordinate():
    assert not numeric_request('风力多少级对应多大的浪？船只什么等级不能出海？')


def test_ready_business_answer_is_not_followed_by_a_generic_data_refusal():
    service=MarineQuestionService.__new__(MarineQuestionService)
    service.general_explanations=True;service.external_enabled=True;service.model_caller=Mock()
    primary={'answer':'接口活动台风1个，位置与风速已列出[资料1]。',
             'answer_meta':{'business_state':'ready','used_mode':'llm','answerability':'partial','required_business_facts':[]}}
    service._answer=Mock(return_value=primary)
    assert service.answer('有几个台风在活动？') is primary
    service.model_caller.assert_not_called()


def test_structured_unit_fields_support_values_but_never_coast_ids():
    evidence=[{'text':'{"maximum":1093,"maximum_record":{"total_cm":685.86},"high":{"value_cm":370}}',
               'metadata':{'source_type':'business_forecast'}}]
    validate_quantity_units('最高总潮位685.86厘米，高潮370厘米[资料1]。',evidence)
    with pytest.raises(ValueError,match='unit_not_supported'):
        validate_quantity_units('最高总潮位1093厘米[资料1]。',evidence)


def test_unicode_minus_preserves_negative_surge_and_rejects_sign_flip():
    evidence=[{'text':'风暴增水-0.87米至0.94米。'}]
    validate_quantity_units('风暴增水−0.87—0.94米[资料1]。',evidence)
    with pytest.raises(ValueError,match='unit_not_supported'):
        validate_quantity_units('风暴增水0.87米[资料1]。',evidence)


def test_selected_passage_must_contain_the_actual_number():
    source='这段只介绍工作安排，不包含任何观测数据。'*30+'有效波高0.74米。'
    raw=json.dumps({'answerability':'partial','answer':'有效波高0.74米[资料1]。','evidence_quotes':[{'citation':1,'passage_id':1}]})
    with pytest.raises(ValueError,match='quantity_not_supported'):
        _decode_model_answer(raw,[{'text':source,'passages':_evidence_passages(source)}])


def test_delivery_binds_place_time_unit_and_citation():
    fact={'kind':'computed','metric':'最高总潮位','value':685.86,'unit':'cm','location':'甲镇','time':'2026-10-20T17:00:00+08:00','citation':1}
    meta={'required_business_facts':[fact]}
    validate_business_delivery('甲镇最高总潮位685.86厘米，10月20日17时[资料1]。',meta,{1})
    for wrong in ['乙镇最高总潮位685.86厘米，10月20日17时[资料1]。',
                  '甲镇最高总潮位685.86米，10月20日17时[资料1]。',
                  '甲镇最高总潮位685.86厘米，10月21日17时[资料1]。',
                  '甲镇最高总潮位685.86厘米，10月20日17时[资料2]。']:
        with pytest.raises(ValueError,match='missing_computed_fact'):validate_business_delivery(wrong,meta,{1,2})


def test_heading_citation_repair_keeps_numbers_and_uses_actual_passage():
    evidence=[{'text':'SSW按10米阵风解释。','passages':[{'id':1,'text':'SSW按10米阵风解释。'}]}]
    good=json.dumps({'answer':'**风速（10米阵风）**','evidence_quotes':[]})
    fixed=json.loads(_cite_tool_quantity_headings(good,evidence,{'required_business_facts':[{'kind':'marine_product'}]}))
    assert fixed['answer']=='**风速（10米阵风）**[资料1]'
    assert fixed['evidence_quotes']==[{'citation':1,'passage_id':1}]
    bad=json.dumps({'answer':'**风速（99米阵风）**','evidence_quotes':[]})
    assert _cite_tool_quantity_headings(bad,evidence,{'required_business_facts':[{'kind':'marine_product'}]})==bad
