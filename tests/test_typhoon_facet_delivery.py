"""Object/time/count delivery contracts, independent of a language model."""
import hashlib
import json

import pytest

from marine_kb.facet_contract import build_contract
from marine_kb.facet_delivery import covered_outputs, evaluate_delivery, publishable
from marine_kb.scope_gate import gate, reconcile_gap_resolution


def atom(text, fid='f1', aid='a1', type='direct_fact', **extra):
    return {'aid': aid, 'type': type, 'text': text, 'facet_ids': [fid],
            'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': text}], **extra}


def tool_atom(fact, text, fid='f1', aid='a1'):
    digest = hashlib.sha256(json.dumps(fact, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf8')).hexdigest()
    return atom(text, fid, aid, 'calculation', origin='verified_tool_record',
                tool_fact=fact, tool_fact_hash=digest)


@pytest.mark.parametrize('when', ['今天', '截至2026年10月19日', '截至2026年10月20日'])
def test_count_and_dynamic_fields_have_separate_contracts(when):
    fs = build_contract(f'{when}现在有几个台风在活动？最新的位置、强度和移动方向是什么？')
    assert len(fs) == 2
    assert fs[0]['required_outputs'] == ['count', 'statistical_scope', 'as_of', 'provenance']
    assert fs[0]['place_role'] == 'statistical_scope'
    assert fs[1]['required_outputs'] == ['object_identity', 'position', 'intensity', 'motion_direction', 'valid_time', 'provenance']
    assert fs[1]['place_role'] == 'object_coordinates'
    assert fs[0]['kind'] == 'numeric_value' and fs[1]['kind'] == 'status_at_time'
    assert not {'place', 'unit'} & set(fs[0]['required_outputs'] + fs[1]['required_outputs'])


def test_count_requires_number_not_only_scope_disclaimer_or_wind_speed():
    f = build_contract('2026年10月20日现在有几个台风在活动？')[0]
    for text, typ in [('该数量是接口活动列表的数量。', 'scope_note'),
                      ('2026年10月20日台风“测试甲”中心最大风速50 m/s。', 'direct_fact'),
                      ('2026年10月20日活动台风列表统计口径见原文。', 'direct_fact'),
                      ('2026年10月20日完整活动台风数量未知，已取得1个台风记录，仅为部分记录，不能当作全部数量。', 'direct_fact')]:
        row = evaluate_delivery([f], [atom(text, type=typ)])[0]
        assert 'count' in row['missing_outputs']
        assert row['status'] != 'full'
    complete = atom('截至2026年10月20日18时，接口完整活动台风列表有1个台风。')
    assert evaluate_delivery([f], [complete])[0]['status'] == 'full'


def test_zero_is_a_valid_explicit_count_but_not_missing():
    f = build_contract('2026年10月20日现在有几个台风在活动？')[0]
    assert evaluate_delivery([f], [atom('截至2026年10月20日18时，接口完整活动台风列表有0个台风。')])[0]['status'] == 'full'
    assert evaluate_delivery([f], [atom('2026年10月20日活动台风数量缺测。', type='gap', gap_type='business_data_missing')])[0]['status'] == 'partial'


def test_explicit_regional_count_still_needs_its_region_filter():
    f = build_contract('2026年10月20日浙江有几个活动台风？')[0]
    row = evaluate_delivery([f], [atom('截至2026年10月20日18时接口活动台风列表有1个台风。')])[0]
    assert 'statistical_scope' in row['missing_outputs']
    assert evaluate_delivery([f], [atom('截至2026年10月20日18时浙江范围内活动台风列表有1个台风。')])[0]['status'] == 'full'


def test_general_list_counts_use_scope_not_physical_units():
    f = build_contract('2026年10月20日舟山已发布几条预警？')[0]
    assert f['quantity_type'] == 'count'
    assert 'unit' not in f['required_outputs']
    row = evaluate_delivery([f], [atom('截至2026年10月20日18时，舟山已发布预警清单共有3条预警。')])[0]
    assert row['status'] == 'full'


@pytest.mark.parametrize('object', ['台风预警', '台风警报', '台风路径点', '台风资料文件'])
def test_storm_related_document_or_warning_counts_are_not_storm_counts(object):
    f = build_contract(f'当前{object}数量有多少？')[0]
    assert f['quantity_type'] == 'count'
    assert f.get('domain') != 'typhoon_dynamics'
    assert 'unit' not in f['required_outputs']


@pytest.mark.parametrize('q', ['台风强度怎么定义？', '什么是台风移动速度？', '台风风速是什么意思？', '台风数量怎么计算？'])
def test_storm_concepts_and_calculation_methods_do_not_become_live_fields(q):
    assert build_contract(q)[0].get('domain') != 'typhoon_dynamics'


def test_count_cannot_borrow_a_time_from_unrelated_value_atom():
    f = build_contract('2026年10月20日现在有几个台风在活动？')[0]
    own = [atom('接口完整活动台风列表有1个台风。'),
           atom('2026年10月20日台风“测试甲”风速50 m/s。', aid='a2')]
    row = evaluate_delivery([f], own)[0]
    assert 'as_of' in row['missing_outputs'] and row['status'] == 'partial'


def test_missing_place_keeps_existing_value_unit_time_and_provenance():
    f = build_contract('2026年10月20日舟山浪高多少？')[0]
    row = evaluate_delivery([f], [atom('2026年10月20日浪高2米。')])[0]
    assert row['missing_outputs'] == ['place']
    assert row['status'] == 'partial' and not publishable([f], [row])


def test_physical_values_still_cannot_mix_different_station_tuples():
    f = build_contract('2026年10月20日舟山浪高多少？')[0]
    own = [atom('舟山浪高2米。'), atom('2026年10月20日宁波浪高3米。', aid='a2')]
    row = evaluate_delivery([f], own)[0]
    assert row['status'] == 'partial'
    assert {'time', 'place'} & set(row['missing_outputs'])


def test_dynamic_coordinates_do_not_require_a_city():
    f = build_contract('截至2026年10月20日台风最新的位置、强度和移动方向是什么？')[0]
    text = '2026年10月20日17时，台风“测试甲”（编号202630）中心东经133.8度、北纬16.7度；中心最大风速50 m/s，移动方向为西北偏西。'
    assert evaluate_delivery([f], [atom(text)])[0]['status'] == 'full'
    for old, replacement, output in [('东经133.8度、北纬16.7度', '浙江', 'position'),
                                     ('中心最大风速50 m/s', '中心气压950 hPa', 'intensity'),
                                     ('移动方向为西北偏西', '移动速度22 km/h', 'motion_direction')]:
        row = evaluate_delivery([f], [atom(text.replace(old, replacement))])[0]
        assert output in row['missing_outputs']


def test_stale_current_record_not_current_status_but_still_a_history_fact():
    current = build_contract('台风最新位置是什么？')[0]
    text = '2026年10月19日11时台风“测试甲”（编号202630）中心东经139度、北纬14.3度，记录滞后，不能代表当前实际位置。'
    current_row = evaluate_delivery([current], [atom(text)])[0]
    assert current_row['missing_outputs'] == ['valid_time']
    assert {'position', 'object_identity', 'provenance'} <= set(current_row['covered_outputs'])
    historical = build_contract('2026年10月19日台风位置是什么？')[0]
    assert evaluate_delivery([historical], [atom(text)])[0]['status'] == 'full'


def test_own_subquestion_fields_do_not_fill_a_siblings_count():
    fs = build_contract('今天几个台风在活动？最新位置、强度和移动方向是什么？')
    wrong = atom('2026年10月20日17时台风“测试甲”（编号202630）中心东经133.8度、北纬16.7度，最大风速50 m/s，移动方向为西北偏西。', 'f2')
    rows = evaluate_delivery(fs, [wrong])
    assert rows[0]['status'] == 'missing' and rows[1]['status'] == 'full'
    assert not publishable(fs, rows)


def test_count_nonrequired_unit_and_admin_place_gaps_removed_only_in_isolation():
    f = build_contract('2026年10月20日几个台风在活动？')[0]
    for text in ['缺少计量单位', '未明确行政地点']:
        result = gate([{'fid': 'f1', 'text': text}], [f], [])
        assert not result['kept']
        assert result['decisions'][0]['rule'] == 'dimension_not_required_by_typed_contract'
    for text in ['缺少统计口径', '缺少2026年10月20日数量', '缺少原始出处', '缺少计量单位；未提供当前数量', '缺少高程基准']:
        result = gate([{'fid': 'f1', 'text': text}], [f], [{'kind': 'unasked_gap', 'text': text}])
        assert result['kept']
    physical = build_contract('2026年10月20日舟山浪高多少？')[0]
    assert gate(['缺少计量单位'], [physical], [{'kind': 'unasked_gap', 'text': '缺少计量单位'}])['kept']


def test_new_grounded_count_can_resolve_its_bound_gap_without_admin_location():
    f = build_contract('2026年10月20日有几个台风在活动？')[0]
    missing = {'fid': 'f1', 'text': '尚未取得2026年10月20日台风活动数量'}
    fresh = atom('截至2026年10月20日18时，接口完整活动台风列表有1个台风。', aid='local1_1_count')
    assert reconcile_gap_resolution([missing], [f], [fresh])['kept'] == []
    old = {**fresh, 'text': fresh['text'].replace('20日', '19日')}
    assert reconcile_gap_resolution([missing], [f], [old])['kept']


def test_verified_tool_count_requires_complete_scope_and_ready_status():
    f = build_contract('今天几个台风在活动？')[0]
    fact = {'kind': 'typhoon_count', 'field': 'count', 'value': 1,
            'count_scope': 'complete_api_response', 'as_of': '2026-10-20T18:00:00+08:00', 'status': 'ready'}
    text = '2026年10月20日18时接口活动台风完整列表有1个台风。'
    assert evaluate_delivery([f], [tool_atom(fact, text)])[0]['status'] == 'full'
    partial = {**fact, 'kind': 'typhoon_scope', 'count_scope': 'unverified'}
    assert 'count' in covered_outputs(f, [tool_atom(fact, text)])
    assert 'count' not in covered_outputs(f, [tool_atom(partial, text)])


def test_tool_pressure_does_not_fill_intensity_and_missing_field_not_zero():
    f = build_contract('台风最新位置、强度和移动方向是什么？')[0]
    common = {'kind': 'typhoon_field', 'storm_id': '202630', 'storm_name': '测试甲',
              'time': '2026-10-20T17:00:00+08:00', 'as_of': '2026-10-20T18:00:00+08:00', 'status': 'ready'}
    facts = [tool_atom({**common, 'field': 'position', 'value': {'lon': 133.8, 'lat': 16.7}}, '台风测试位置', aid='a1'),
             tool_atom({**common, 'field': 'pressure', 'value': 950}, '台风测试气压', aid='a2'),
             tool_atom({**common, 'field': 'moveDirection', 'value': 'WNW'}, '台风测试移向', aid='a3')]
    assert evaluate_delivery([f], facts)[0]['missing_outputs'] == ['intensity']
    facts.append(tool_atom({**common, 'field': 'maxSpeed', 'value': None, 'status': 'missing'}, '缺少风速', aid='a4'))
    assert evaluate_delivery([f], facts)[0]['missing_outputs'] == ['intensity']
    facts.append(tool_atom({**common, 'field': 'maxSpeed', 'value': 50, 'unit': 'm/s'}, '最大风速50 m/s', aid='a5'))
    assert evaluate_delivery([f], facts)[0]['status'] == 'full'


def test_modified_tool_fact_cannot_use_hash_bound_schema():
    f = build_contract('台风最新位置是什么？')[0]
    fact = {'kind': 'typhoon_field', 'field': 'position', 'value': {'lon': 133.8, 'lat': 16.7},
            'time': '2026-10-20T17:00:00+08:00', 'storm_id': '202630', 'status': 'ready'}
    a = tool_atom(fact, '无自足位置字段')
    a['tool_fact'] = {**fact, 'value': {'lon': 0, 'lat': 0}}
    assert 'position' not in covered_outputs(f, [a])


def test_explicit_wind_pressure_and_speed_requests_are_separate_fields():
    f = build_contract('台风最新中心最大风速、中心气压和移动速度是多少？')[0]
    assert {'max_wind_speed', 'pressure', 'motion_speed'} <= set(f['required_outputs'])
    pressure = atom('2026年10月20日17时台风“测试甲”（编号202630）中心气压950 hPa。')
    row = evaluate_delivery([f], [pressure])[0]
    assert 'pressure' in row['covered_outputs']
    assert {'max_wind_speed', 'motion_speed'} <= set(row['missing_outputs'])


def test_actual_business_tool_field_schema_keeps_individual_fields():
    from forecast_runtime.answer_facts import typhoon_delivery_facts, typhoon_fact_text
    f = build_contract('截至2026年10月20日现在台风最新位置、强度和移动方向是什么？')[0]
    result = {'kind': 'typhoons', 'state': 'ready', 'requested_fields': ['identity', 'position', 'intensity', 'direction'],
              'as_of': '2026-10-20T18:00:00+08:00', 'retrieved_at': '2026-10-20T18:00:00+08:00',
              'list': [{'typhoonID': '202630', 'nameCN': '测试甲', 'points': [
                  {'time': '2026-10-20T17:00:00+08:00', 'lon': 133.8, 'lat': 16.7,
                   'maxSpeed': 50, 'pressure': 950, 'windScale': 15, 'moveDirection': 'WNW', 'moveSpeed': 22}]}]}
    records = typhoon_delivery_facts(result)
    atoms = [tool_atom(row, typhoon_fact_text(row), aid=f'a{index}') for index, row in enumerate(records)]
    assert evaluate_delivery([f], atoms)[0]['status'] == 'full'
    no_strength = [a for a in atoms if a['tool_fact'].get('source_field') not in {'maxSpeed', 'windScale', 'intensity'}]
    assert 'intensity' in evaluate_delivery([f], no_strength)[0]['missing_outputs']


def test_verified_empty_activity_list_makes_centre_fields_not_applicable():
    f = build_contract('台风最新位置、强度和移动方向是什么？')[0]
    fact = {'kind': 'typhoon_availability', 'field': 'availability', 'value': 'no_active_typhoons',
            'count': 0, 'count_scope': 'complete_api_response', 'as_of': '2026-10-20T18:00:00+08:00',
            'status': 'ready', 'cache_stale': False}
    text = '截至2026年10月20日18时，完整活动列表为0个台风，无台风中心位置、强度和移动方向。'
    assert evaluate_delivery([f], [tool_atom(fact, text)])[0]['status'] == 'full'
    for broken in ({**fact, 'count': 1}, {**fact, 'count_scope': 'unverified'},
                   {**fact, 'status': 'missing'}, {**fact, 'cache_stale': True}, {**fact, 'status': 'stale', 'stale': True}):
        assert evaluate_delivery([f], [tool_atom(broken, text)])[0]['status'] != 'full'


@pytest.mark.parametrize('q,outputs', [
    ('快照取得时间是什么？', ['snapshot_time', 'provenance']),
    ('位置记录时间是什么？', ['record_time', 'provenance']),
    ('查询时间与记录时间分别是什么？', ['record_time', 'query_time', 'provenance']),
    ('请注明接口获取时刻和查询截止时间。', ['snapshot_time', 'query_time', 'provenance']),
])
def test_named_clock_questions_are_not_definition_or_coordinates(q, outputs):
    f = build_contract('截至2026年10月19日18时台风接口记录。' + q,
                       [{'id': 1, 'question': q}])[0]
    assert f['kind'] == 'status_at_time'
    assert f['required_outputs'] == outputs
    assert 'position' not in f['required_outputs'] and 'valid_time' not in f['required_outputs']


def test_activity_api_record_question_still_counts_storms():
    f = build_contract('截至2026年10月19日18时，活动台风接口记录中有几个台风？')[0]
    assert f.get('domain') == 'typhoon_dynamics'
    assert f['required_outputs'] == ['count', 'statistical_scope', 'as_of', 'provenance']


def test_count_clock_question_does_not_request_a_second_count():
    f = build_contract('台风活动数量的统计截止时刻是什么？')[0]
    assert f['required_outputs'] == ['query_time', 'provenance']


def test_list_count_and_names_both_required_without_unasked_wind_fields():
    f = build_contract('今天活动台风有几个、分别叫什么？')[0]
    assert f['required_outputs'] == ['count', 'statistical_scope', 'as_of', 'provenance', 'object_identity']
    count = atom('截至2026年10月20日18时，接口完整活动台风列表有1个台风。')
    assert evaluate_delivery([f], [count])[0]['missing_outputs'] == ['object_identity']
    named = atom('台风“测试甲”（编号202630）为本次接口活动台风记录。', aid='a2')
    assert evaluate_delivery([f], [count, named])[0]['status'] == 'full'


def test_three_clocks_are_independent_even_when_position_record_is_stale():
    f = build_contract('截至2026年10月19日台风查询时间、快照取得时间和位置记录时间分别是什么？')[0]
    fact = {'kind': 'typhoon_field', 'field': 'position', 'value': {'lon': 133.8, 'lat': 16.7},
            'storm_id': '202630', 'storm_name': '测试甲', 'status': 'stale', 'stale': True,
            'as_of': '2026-10-19T18:00:00+08:00', 'retrieved_at': '2026-10-19T17:00:00+08:00',
            'record_time': '2026-10-19T10:00:00+08:00'}
    a = tool_atom(fact, '历史快照，查询截至时刻2026-10-19T18:00:00+08:00；接口取得时间2026-10-19T17:00:00+08:00；位置记录时间2026-10-19T10:00:00+08:00。')
    assert evaluate_delivery([f], [a])[0]['status'] == 'full'
    del fact['record_time']
    no_record = tool_atom(fact, '接口查询与取得时间已提供。')
    assert evaluate_delivery([f], [no_record])[0]['missing_outputs'] == ['record_time']


def test_plain_grounded_clock_label_cannot_borrow_another_clock_value():
    f = build_contract('台风查询时间和位置记录时间分别是什么？')[0]
    text = '查询时间未知；位置记录时间2026年10月19日10时。'
    row = evaluate_delivery([f], [atom(text)])[0]
    assert row['missing_outputs'] == ['query_time']
    complete = text.replace('查询时间未知', '查询截至时刻2026年10月19日18时')
    assert evaluate_delivery([f], [atom(complete)])[0]['status'] == 'full'


def test_stale_tool_value_keeps_known_fields_but_current_time_is_missing():
    f = build_contract('截至2026年10月20日台风最新位置、强度和移动方向是什么？')[0]
    common = {'kind': 'typhoon_field', 'storm_id': '202630', 'storm_name': '测试甲', 'status': 'stale',
              'stale': True, 'time': '2026-10-19T10:00:00+08:00', 'as_of': '2026-10-20T18:00:00+08:00'}
    facts = [tool_atom({**common, 'field': 'position', 'value': {'lon': 133.8, 'lat': 16.7}}, '旧位置记录', aid='a1'),
             tool_atom({**common, 'field': 'intensity', 'source_field': 'maxSpeed', 'value': 50}, '旧风速记录', aid='a2'),
             tool_atom({**common, 'field': 'direction', 'source_field': 'moveDirection', 'value': 'WNW'}, '旧方向记录', aid='a3')]
    row = evaluate_delivery([f], facts)[0]
    assert row['missing_outputs'] == ['valid_time'] and row['status'] == 'partial'
