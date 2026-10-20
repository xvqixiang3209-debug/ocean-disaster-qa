"""Object/time scope checks for deterministic tool handoff; no model calls."""
from copy import deepcopy

import pytest

from forecast_runtime.answer_facts import delivery_facts, fact_text, typhoon_requested_fields
from marine_kb.evidence_units import evidence_view
from marine_kb.facet_contract import build_contract
from marine_kb.protected_tool_delivery import _owners, verified_tool_atoms, resolve_tool_gaps
from marine_kb.business_validation import validate_business_delivery


def _setup(question='截至2026年10月20日，现在有几个台风在活动？最新的位置、强度和移动方向是什么？'):
    result={'kind':'typhoons','state':'ready','request_question':question,
            'list':[{'nameCN':'蔷琵','typhoonID':202630,'points':[{
                'time':'2026-10-20 17:00:00','lon':133.8,'lat':16.7,'maxSpeed':62,
                'pressure':915,'moveDirection':'WNW','moveSpeed':24}]}],
            'as_of':'2026-10-20T18:40:00+08:00','requested_as_of':'2026-10-20T18:40:00+08:00',
            'retrieved_at':'2026-10-20T18:39:00+08:00','query_time_mode':'current',
            'snapshot_scope':'complete_api_response','selection_scope':'all_active',
            'activity_count_verified':True,'activity_list_count_verified':True,
            'activity_list_count':1,'cache_stale':False}
    facts=delivery_facts(result)
    hit={'source_id':'forecast:'+'b'*24,'text':fact_text(facts),'title':'台风接口查询结果',
         'status':'indexed','metadata':{'source_type':'business_forecast','external_allowed':True}}
    meta={'business_evidence_id':'b'*24,'required_business_facts':facts}
    facets=build_contract(question)
    atoms,_,_=verified_tool_atoms(meta,evidence_view([hit]),facets)
    return result,facts,facets,atoms


@pytest.mark.parametrize('gap',[
    '缺少10月19日活动台风数量','缺少昨天活动台风数量','缺少前天活动台风数量',
    '缺少10月19号活动台风数量','缺少19号活动台风数量','缺少19日活动台风数量',
    '缺少2026年10月20日08时活动台风数量','缺少今天08时活动台风数量',
    '缺少2026年10月20号08时活动台风数量',
    '缺少08:00活动台风数量','缺少2026-10-20T08:00活动台风数量',
    '缺少台风海神2026年10月20日的位置记录','缺少台风“海神”2026年10月20日的位置记录',
    '缺少接口标识202631在2026年10月20日的位置记录',
    '缺少台风202631在2026年10月20日的位置记录',
    '缺少202631号台风在2026年10月20日的位置记录',
])
def test_another_day_hour_or_storm_gap_cannot_be_cleared(gap):
    _,_,facets,atoms=_setup()
    assert resolve_tool_gaps([gap],facets,atoms)['kept']==[gap]


@pytest.mark.parametrize('gap',[
    '缺少2026年10月20日活动台风数量','缺少10月20日活动台风数量',
    '缺少20号活动台风数量','缺少10月20号活动台风数量',
    '缺少今天活动台风数量','缺少台风蔷琵2026年10月20日的位置记录',
    '缺少接口标识202630在2026年10月20日的位置记录',
    '缺少台风202630在2026年10月20日的位置记录',
    '缺少202630号台风在2026年10月20日的位置记录',
])
def test_same_scope_fields_resolve_their_own_gap(gap):
    _,_,facets,atoms=_setup()
    assert resolve_tool_gaps([gap],facets,atoms)['kept']==[]


def test_explicit_gap_facet_id_cannot_be_satisfied_by_a_sibling():
    _,_,facets,atoms=_setup()
    gap={'text':'缺少2026年10月20日的位置记录','facet_ids':['f1']}
    assert resolve_tool_gaps([gap],facets,atoms)['kept']==[gap]


@pytest.mark.parametrize('question',[
    '2026年10月19日台风蔷琵的位置在哪里？',
    '2026年10月20日08时台风蔷琵的位置在哪里？',
    '2026年10月20日台风海神的位置在哪里？',
    '2026年10月20日台风“海神”的位置在哪里？',
    '2026年10月20日接口标识202631台风位置在哪里？',
])
def test_tool_field_is_not_owned_by_a_different_time_or_storm(question):
    _,facts,_,_=_setup()
    fact=next(f for f in facts if f.get('field')=='position')
    assert _owners(fact,build_contract(question))==[]


def test_multiple_objects_and_dates_do_not_share_one_record():
    _,facts,_,_=_setup()
    fact=next(f for f in facts if f.get('field')=='position')
    facets=build_contract('2026年10月19日台风海神的位置在哪里？2026年10月20日台风蔷琵的位置在哪里？')
    assert _owners(fact,facets)==['f2']


def test_identity_does_not_fill_a_count_facet_or_another_storm():
    _,facts,facets,_=_setup()
    fact=next(f for f in facts if f.get('field')=='identity')
    assert _owners(fact,facets)==['f2']
    assert not _owners(fact,build_contract('台风海神最新位置是什么？'))


@pytest.mark.parametrize('question,expected',[
    ('今天活动台风有几个、分别叫什么？',{'count','identity'}),
    ('目前台风有多少个？',{'count'}),
    ('活动台风共有几个？',{'count'}),
    ('目前有多少台风在活动？',{'count'}),
    ('台风有几个分别叫什么？',{'count','identity'}),
    ('台风中心最大风速是多少？',{'identity','intensity'}),
    ('台风移动速度是多少？',{'identity','speed'}),
    ('查询时间与记录时间分别是什么？',{'identity','query_time','record_time'}),
    ('接口什么时候取得？',{'snapshot_time'}),
    ('位置记录是几点的？',{'identity','record_time'}),
    ('位置和记录时间分别是什么？',{'identity','position','record_time'}),
    ('接口取得时间和位置分别是什么？',{'snapshot_time','identity','position'}),
])
def test_requested_fields_distinguish_counts_field_values_and_timing(question,expected):
    assert typhoon_requested_fields(question)==expected


def test_time_facts_are_distinct_actual_values_with_their_own_checks():
    _,facts,_,_=_setup('台风查询时间与位置记录时间分别是什么？接口什么时候取得？')
    timing={f['field']:f for f in facts if f['kind']=='typhoon_timing'}
    assert timing['query_time']['value']=='2026-10-20T18:40:00+08:00'
    assert timing['snapshot_time']['value']=='2026-10-20T18:39:00+08:00'
    assert timing['record_time']['value']=='2026-10-20 17:00:00'
    text='\n'.join(fact_text([f])+'[资料1]' for f in facts)
    validate_business_delivery(text,{'required_business_facts':facts},{1})
    assert not any(f.get('field')=='position' for f in facts)
