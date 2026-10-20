from copy import deepcopy

import pytest

from forecast_runtime.answer_facts import delivery_facts, delivery_table, fact_table, fact_text, typhoon_requested_fields
from marine_kb.business_validation import validate_business_delivery


def _storm(number=1, **point_changes):
    point={'time':'2026-10-20 17:00:00','lon':133.8+number/100,'lat':16.7,
           'maxSpeed':62,'pressure':915,'moveDirection':'WNW','moveSpeed':24,
           'windScale':17,'intensity':'SuperTY'}
    point.update(point_changes)
    return {'typhoonID':202630+number,'nameCN':'示例台风'+str(number),'points':[point]}


def _result(storms=None, question='截至2026年10月20日，现在有几个台风在活动？最新的位置、强度和移动方向是什么？', **changes):
    storms=[_storm()] if storms is None else storms
    result={'kind':'typhoons','state':'ready' if storms else 'no_active_typhoons',
            'request_question':question,'list':storms,'requested_as_of':'2026-10-20T18:40:00+08:00',
            'as_of':'2026-10-20T18:40:00+08:00','retrieved_at':'2026-10-20T18:39:00+08:00',
            'historical_snapshot':False,'snapshot_scope':'complete_api_response',
            'selection_scope':'all_active','activity_count_verified':True,
            'activity_list_count':len(storms),'activity_list_count_verified':True,
            'query_time_mode':'current','cache_stale':False}
    result.update(changes)
    return result


def _validate(text,facts):
    validate_business_delivery(text,{'required_business_facts':facts},{1})


@pytest.mark.parametrize('count',[0,1,4])
def test_complete_activity_counts_include_zero_and_every_storm(count):
    result=_result([_storm(i+1) for i in range(count)])
    facts=delivery_facts(result)
    assert next(f['value'] for f in facts if f['kind']=='typhoon_count')==count
    assert len([f for f in facts if f['kind']=='typhoon_identity'])==count
    text=delivery_table(result,facts)
    assert f'接口完整活动列表有{count}个台风' in text
    assert 'SWL' not in text and '高程基准' not in text and 'NC海况' in text
    _validate(text,facts)


def test_count_does_not_disappear_without_points_or_wind_speed():
    result=_result([{'typhoonID':202631,'nameCN':'无点台风','points':[]}])
    facts=delivery_facts(result)
    assert next(f['value'] for f in facts if f['kind']=='typhoon_count')==1
    gaps=[f for f in facts if f['kind']=='typhoon_field_gap']
    assert {f['field'] for f in gaps}=={'position','intensity','direction'}
    assert all(f['status']=='missing' and 'value' not in f for f in gaps)
    _validate(fact_table(facts),facts)


def test_missing_wind_is_not_zero_and_other_strength_fields_survive():
    result=_result([_storm(maxSpeed=None)])
    facts=delivery_facts(result)
    assert any(f['kind']=='typhoon_field_gap' and f.get('source_field')=='maxSpeed' for f in facts)
    assert any(f['kind']=='typhoon_field' and f.get('source_field')=='pressure' for f in facts)
    assert not any(f.get('value')==0 and f.get('source_field')=='maxSpeed' for f in facts)
    _validate(fact_table(facts),facts)


def test_named_subset_keeps_original_complete_activity_count():
    result=_result(selection_scope='named_subset',activity_list_count=3)
    facts=delivery_facts(result)
    assert next(f['value'] for f in facts if f['kind']=='typhoon_count')==3
    assert next(f['value'] for f in facts if f['kind']=='typhoon_selected_count')==1
    _validate(fact_table(facts),facts)


def test_partial_snapshot_cannot_promote_count_from_a_contradictory_flag():
    result=_result(snapshot_scope='partial_records',activity_list_count_verified=True)
    facts=delivery_facts(result)
    assert not any(f['kind']=='typhoon_count' for f in facts)
    assert any(f['kind']=='typhoon_scope' for f in facts)


@pytest.mark.parametrize('reason',['partial','unavailable','future','named_subset'])
def test_unverified_list_is_not_a_zero_or_complete_count(reason):
    changes={'activity_list_count_verified':False,'activity_count_verified':False,
             'snapshot_scope':'partial_records'}
    if reason=='unavailable':changes['state']='unavailable'
    if reason=='future':changes['query_time_mode']='future'
    if reason=='named_subset':changes['selection_scope']='named_subset'
    result=_result([],question='现在有几个活动台风？',**changes)
    facts=delivery_facts(result)
    assert not any(f['kind']=='typhoon_count' for f in facts)
    assert any(f['kind']=='typhoon_scope' and f['status']=='missing' for f in facts)
    text=fact_table(facts)
    assert '完整活动台风数量未知' in text
    _validate(text,facts)
    with pytest.raises(ValueError,match='unverified_active_typhoon_count'):
        _validate(text+'\n当前有0个活动台风[资料1]。',facts)


@pytest.mark.parametrize('count',[0,1,3])
def test_count_only_question_does_not_require_unasked_wind_or_position(count):
    result=_result([_storm(i+1) for i in range(count)],question='现在有几个活动台风？')
    facts=delivery_facts(result)
    assert len(facts)==1 and facts[0]['kind']=='typhoon_count'
    _validate(fact_table(facts),facts)


def test_named_position_question_does_not_require_activity_total():
    result=_result(question='示例台风1最新位置在哪里？',selection_scope='named_subset')
    facts=delivery_facts(result)
    assert not any(f['field']=='count' for f in facts)
    assert {f['field'] for f in facts}<={'identity','position'}
    _validate(fact_table(facts),facts)


@pytest.mark.parametrize('verified',[True,False])
def test_empty_details_distinguish_no_activity_from_missing_records(verified):
    result=_result([],question='台风最新位置、强度和移动方向是什么？',
                   activity_list_count_verified=verified,activity_count_verified=verified)
    facts=delivery_facts(result)
    available=next(f for f in facts if f['kind']=='typhoon_availability')
    assert available['value']==('no_active_typhoons' if verified else 'unknown')
    assert available['status']==('ready' if verified else 'missing')
    _validate(fact_table(facts),facts)


def test_complete_historical_snapshot_count_is_not_current_state():
    result=_result(question='截至2026年10月20日有几个台风？',query_time_mode='historical',historical_snapshot=True)
    facts=delivery_facts(result)
    assert facts[0]['status']=='ready'
    assert '历史原始快照，只代表当时已保存的接口记录' in fact_text(facts)
    _validate(fact_table(facts),facts)


@pytest.mark.parametrize('field',['count','position','maxSpeed','pressure','moveDirection','moveSpeed'])
def test_each_requested_field_is_required_and_siblings_cannot_replace_it(field):
    result=_result(question='现在有几个活动台风？最新位置、强度、移动方向和移动速度是什么？')
    facts=delivery_facts(result)
    target=next(f for f in facts if f.get('source_field',f.get('field'))==field)
    remaining=[f for f in facts if f is not target]
    with pytest.raises(ValueError,match='missing_typhoon'):
        _validate(fact_table(remaining),facts)


def test_count_cannot_be_satisfied_by_bare_scope_disclaimer():
    facts=delivery_facts(_result(question='现在有几个活动台风？'))
    with pytest.raises(ValueError,match='missing_typhoon_count'):
        _validate('该数量是接口活动列表的数量，不是未来24小时台风数量预测[资料1]。',facts)


def test_wrong_count_rejected_even_when_correct_count_is_also_present():
    facts=delivery_facts(_result(question='现在有几个活动台风？'))
    with pytest.raises(ValueError,match='typhoon_count_mismatch'):
        _validate(fact_table(facts)+'\n目前有2个活动台风[资料1]。',facts)


def test_swapped_coordinates_do_not_pass_field_validation():
    facts=delivery_facts(_result())
    text=fact_table(facts).replace('东经133.81度，北纬16.7度','东经16.7度，北纬133.81度')
    with pytest.raises(ValueError,match='missing_typhoon_field:position'):_validate(text,facts)


@pytest.mark.parametrize('changes',[{'lon':float('nan')},{'lat':float('inf')},{'lon':181},{'lat':91}])
def test_nonfinite_or_out_of_range_coordinates_become_explicit_gap(changes):
    facts=delivery_facts(_result([_storm(**changes)]))
    assert any(f['kind']=='typhoon_field_gap' and f['field']=='position' for f in facts)
    _validate(fact_table(facts),facts)


def test_stale_record_is_delivered_with_original_time_and_scope():
    facts=delivery_facts(_result([_storm(time='2026-10-19 11:00:00')]))
    target=next(f for f in facts if f.get('source_field')=='position')
    assert target['status']=='stale' and target['record_time']=='2026-10-19 11:00:00'
    assert target['requested_as_of']=='2026-10-20T18:40:00+08:00'
    assert target['observed_at']==target['record_time'] and target['age_hours']==31.7
    _validate(fact_table(facts),facts)
    bad=fact_table(facts).replace('记录或缓存可能滞后，仅代表上述记录或快照，不能认定为查询时刻的实时状态','实时状态')
    with pytest.raises(ValueError,match='missing_typhoon_field'):_validate(bad,facts)


def test_stale_cache_cannot_become_fresh_merely_because_point_is_recent():
    facts=delivery_facts(_result(cache_stale=True))
    assert all(f.get('status')=='stale' for f in facts if f['kind'] in {'typhoon_count','typhoon_field'})
    _validate(fact_table(facts),facts)


def test_interface_units_keep_the_unverified_boundary():
    facts=delivery_facts(_result())
    assert any(f.get('source_field')=='maxSpeed' and f['unit_assumed'] for f in facts)
    text=fact_table(facts).replace('（单位按现有接口解释，尚未核定）','')
    with pytest.raises(ValueError,match='missing_typhoon_field:maxSpeed'):_validate(text,facts)


def test_fact_text_is_deterministic_self_contained_and_does_not_mutate_tool_data():
    result=_result();before=deepcopy(result);facts=delivery_facts(result)
    assert result==before
    for fact in facts:
        line=fact_text([fact])
        assert line and len(line)<900 and '\n' not in line
        assert '2026-10-20T18:40:00+08:00' in line and '2026-10-20T18:39:00+08:00' in line
    assert typhoon_requested_fields('现在几个台风？')=={'count'}
    assert {'identity','position','intensity','direction'}<=typhoon_requested_fields('最新位置、强度和移动方向是什么？')


@pytest.mark.parametrize('question',['台风中心最大风速是多少？','这个台风强度有多少级？','台风气压是多少？','它的移动速度是多少？'])
def test_how_much_of_a_field_does_not_request_an_activity_count(question):
    assert 'count' not in typhoon_requested_fields(question)


def test_missing_unasked_pressure_is_not_a_gap_in_a_wind_question():
    facts=delivery_facts(_result([_storm(pressure=None)],question='台风最大风速是多少？'))
    assert not any(f['kind']=='typhoon_field_gap' and f.get('source_field')=='pressure' for f in facts)
    assert typhoon_requested_fields('台风有多强？')=={'identity','intensity'}
    assert typhoon_requested_fields('台风移动得多快？')=={'identity','speed'}
    _validate(fact_table(facts),facts)
