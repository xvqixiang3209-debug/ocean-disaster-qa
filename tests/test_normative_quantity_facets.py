import pytest

from marine_kb.facet_contract import build_contract, normative_delivery_shape
from marine_kb.facet_delivery import evaluate_delivery


def fact(text, fid='f1'):
    return {'aid': 'a', 'type': 'direct_fact', 'text': text,
            'facet_ids': [fid],
            'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': text}]}


@pytest.mark.parametrize('question,quantity_type', [
    ('按GB/T19721.3-2017，海冰警报时效至少多长？', 'normative_duration'),
    ('按GB/T19721.5-2022，常规海温预报每日最低发布频次是多少？', 'normative_frequency'),
    ('按GB/T19721.6-2023，应急海流预报每天至少发布几次？', 'normative_frequency'),
    ('依据HY/T 0273.2-2023，海浪警报时效应是多少？', 'normative_duration'),
    ('按GB/T19721.6-2023表A.1，西西北的中心角度、角度范围分别是多少？', 'normative_angle'),
    ('按GB/T19721.2-2017，近岸海域海浪红色警报的波高条件是什么？', 'normative_quantity'),
])
def test_document_code_alone_establishes_numeric_provision_context(question, quantity_type):
    facet = build_contract(question)[0]
    assert facet['kind'] == 'numeric_value'
    assert facet['quantity_type'] == quantity_type
    assert facet['required_outputs'] == ['value', 'unit', 'provenance']
    assert 'business_data_missing' not in facet['allowed_gaps']


@pytest.mark.parametrize('text', [
    '定点数值采用预报时效内平均值还是瞬时值？',
    '流向表示海水流去还是流来的方向？',
    '浮冰范围和冰厚各用什么单位？',
    '时间采用什么时制？',
])
def test_definitions_do_not_become_operational_numeric_facts(text):
    facet = build_contract('按GB/T19721.6-2023，' + text)[0]
    assert facet['kind'] == 'explanation'
    assert facet['quantity_type'] == 'normative_definition'
    assert facet['required_outputs'] == ['answer', 'support']
    assert evaluate_delivery([facet], [fact('流向表示海水流去的方向[资料1]。')])[0]['status'] == 'full'


def test_parent_context_does_not_import_sibling_forecast_scope_into_a_standard_facet():
    facets = build_contract(
        '按GB/T19721.5-2022，海温预报每日频次是多少？今天舟山海温数值是多少？',
        [{'id': 1, 'question': '常规海温预报每日最低发布频次是多少？'},
         {'id': 2, 'question': '今天舟山海温数值是多少？'}])
    assert facets[0]['quantity_type'] == 'normative_frequency'
    assert facets[0]['place_role'] == 'context'
    assert facets[0]['time'] is None
    assert 'place' not in facets[0]['required_outputs']
    assert facets[1]['kind'] == 'numeric_value'
    assert 'quantity_type' not in facets[1]
    assert facets[1]['required_outputs'] == ['value', 'unit', 'place', 'time', 'provenance']
    assert 'business_data_missing' in facets[1]['allowed_gaps']


@pytest.mark.parametrize('question', [
    '按GB/T19721.5-2022，今天舟山海温数值是多少？',
    '依据海浪标准，2026年10月21日舟山最大波高是多少？',
    '按GB/T19721.2-2017，最新预报产品的时效是多少？',
    '根据海洋预报规范，本次发布的预报时效是多少？',
])
def test_document_reference_never_exempts_an_actual_forecast(question):
    assert not normative_delivery_shape(question)
    assert 'time' in build_contract(question)[0]['required_outputs']


@pytest.mark.parametrize('answer', [
    '常规海温预报每日不少于1次[资料1]。',
    '常规海温预报每天至少发布一次[资料1]。',
    '发布频次为2次/日[资料1]。',
])
def test_normative_frequency_keeps_a_grounded_counter_and_period(answer):
    facet = build_contract('按GB/T19721.5-2022，海温预报每日发布频次是多少？')[0]
    assert evaluate_delivery([facet], [fact(answer)])[0]['status'] == 'full'


@pytest.mark.parametrize('answer', [
    '预报时效为24h[资料1]。',
    '该标准于2022年5月1日发布[资料1]。',
    '一次发布的预报时效为24h[资料1]。',
])
def test_duration_or_publication_date_cannot_fill_a_frequency_request(answer):
    facet = build_contract('按GB/T19721.5-2022，海温预报每日发布频次是多少？')[0]
    result = evaluate_delivery([facet], [fact(answer)])[0]
    assert 'value' not in result['covered_outputs']


@pytest.mark.parametrize('answer', [
    '每日发布一次[资料1]。',
    '海温为25℃[资料1]。',
    '该标准于2022年5月1日发布[资料1]。',
])
def test_frequency_temperature_or_calendar_date_cannot_fill_duration(answer):
    facet = build_contract('按GB/T19721.5-2022，海温预报时效是多少？')[0]
    assert 'value' not in evaluate_delivery([facet], [fact(answer)])[0]['covered_outputs']


def test_unsplit_frequency_and_duration_still_require_both_predicates():
    facet = build_contract('按GB/T19721.5-2022，常规海温预报每日最低发布频次和时效分别是多少？')[0]
    assert facet['required_outputs'] == ['frequency', 'duration', 'provenance']
    only_duration = evaluate_delivery([facet], [fact('预报时效为24h[资料1]。')])[0]
    assert 'duration' in only_duration['covered_outputs']
    assert 'frequency' not in only_duration['covered_outputs']
    both = evaluate_delivery([facet], [fact('每日发布不少于1次，预报时效为24h[资料1]。')])[0]
    assert both['status'] == 'full'


@pytest.mark.parametrize('field,value', [
    ('中心角度', '292.5'), ('角度范围', '(281.25,303.75]'),
])
def test_grounded_angle_row_binds_its_header_unit_to_the_requested_column(field, value):
    facet = build_contract('按GB/T19721.6-2023表A.1，西西北的' + field + '是多少？请保留区间开闭端点。')[0]
    assert not facet['places']
    table = fact('| 西西北 | WNW | 292.5 | (281.25,303.75][资料1] |')
    table['table_header'] = '| 方位 | 符号 | 中心角度/° | 角度范围/° |'
    assert evaluate_delivery([facet], [table])[0]['status'] == 'full'
    # A neighbouring angular column cannot supply this requested field.
    table['text'] = ('| 西西北 | WNW | — | (281.25,303.75][资料1] |'
                     if field == '中心角度' else '| 西西北 | WNW | 292.5[资料1] | — |')
    assert 'value' not in evaluate_delivery([facet], [table])[0]['covered_outputs']


def test_inline_table_is_typed_but_unrelated_bare_number_does_not_borrow_header_unit():
    facet = build_contract('按GB/T19721.6-2023表A.1，西西北的中心角度是多少？')[0]
    table = fact('| 方位 | 中心角度/° |\n|---|---|\n| 西西北 | 292.5[资料1] |')
    assert evaluate_delivery([facet], [table])[0]['status'] == 'full'
    table['text'] = '| 方位 | 中心角度/° |\n普通文字\n| 西西北 | 292.5[资料1] |'
    assert 'value' not in evaluate_delivery([facet], [table])[0]['covered_outputs']
    table['text'] = '| 方位 | 中心角度/° |\n| 西西北 | 292.5 | 303.75[资料1] |'
    assert 'value' not in evaluate_delivery([facet], [table])[0]['covered_outputs']
