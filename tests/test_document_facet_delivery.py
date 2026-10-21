import pytest
from marine_kb.facet_contract import build_contract, infer_kind
from marine_kb.facet_delivery import evaluate_delivery


@pytest.mark.parametrize('text', [
    'GB/T14914.6-2021的全称是什么', '该标准的适用内容是什么', '该标准的文档版本是什么',
    '上述规定的出处是什么', '请给出出处', '该办法的适用范围是什么',
])
def test_document_identity_scope_is_not_a_concept_definition(text):
    assert infer_kind(text) == 'explanation'


def test_normative_duration_does_not_require_unasked_geography_or_clock():
    f = build_contract('按GB/T19721.2-2017标准，海浪警报时效是多少？')[0]
    assert f['required_outputs'] == ['value', 'unit', 'provenance']
    a = {'aid': 'a', 'type': 'direct_fact', 'text': '海浪警报时效为24h[资料1]。',
         'facet_ids': [f['fid']], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '警报时效为24h'}]}
    assert evaluate_delivery([f], [a])[0]['status'] == 'full'


def test_actual_wave_value_still_requires_scope():
    f = build_contract('今天舟山未来24小时最大波高是多少？')[0]
    assert 'place' in f['required_outputs'] and 'time' in f['required_outputs']


def test_grounded_station_contingency_is_a_real_action_answer():
    f = build_contract('旧站因严重自然灾害无法正常工作时怎么办？')[0]
    a = {'aid': 'a', 'type': 'direct_fact', 'text': '旧站因严重自然灾害无法正常工作时，可以在原址设立临时观测站点，进行一个月的对比观测[资料1]。',
         'facet_ids': [f['fid']], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '在原址设立临时观测站点，进行一个月的对比观测'}]}
    assert evaluate_delivery([f], [a])[0]['status'] == 'full'


@pytest.mark.parametrize('duration', ['至少一年', '十五个工作日', '十个工作日', '一个月', '15个工作日'])
def test_written_duration_counts_as_a_duration_not_missing_data(duration):
    f = build_contract('根据《管理办法》原文，申请使用资料后多久答复？')[0]
    a = {'aid': 'a', 'type': 'direct_fact', 'text': '受理申请后' + duration + '内答复[资料1]。',
         'facet_ids': [f['fid']], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': duration}]}
    assert evaluate_delivery([f], [a])[0]['status'] == 'full'


def test_grounded_provenance_can_answer_its_own_source_sibling():
    fs = build_contract('根据《海洋观测站点管理办法》旧站怎么办？出处是什么？',
                        [{'id': 1, 'question': '根据《海洋观测站点管理办法》旧站怎么办'},
                         {'id': 2, 'question': '上述规定的出处是什么'}])
    atom = {'aid': 'source', 'type': 'direct_fact', 'text': '上述规定出自《海洋观测站点管理办法》第十二条[资料1]。',
            'facet_ids': ['f1'], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '第十二条'}]}
    assert evaluate_delivery(fs, [atom])[1]['status'] == 'full'


def test_ambiguous_provenance_is_not_borrowed_from_another_document():
    fs = build_contract('《甲方管理办法》与《乙方管理办法》分别规定什么？出处是什么？',
                        [{'id': 1, 'question': '《甲方管理办法》规定什么'},
                         {'id': 2, 'question': '《乙方管理办法》规定什么'},
                         {'id': 3, 'question': '出处是什么'}])
    atom = {'aid': 'source', 'type': 'direct_fact', 'text': '出自《甲方管理办法》第十二条[资料1]。',
            'facet_ids': ['f1'], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '第十二条'}]}
    assert evaluate_delivery(fs, [atom])[2]['status'] == 'missing'


def test_publication_year_is_not_a_normative_duration():
    f = build_contract('根据《管理办法》，对比观测多久？')[0]
    atom = {'aid': 'year', 'type': 'direct_fact', 'text': '管理办法于2017年发布[资料1]。',
            'facet_ids': [f['fid']], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '2017年发布'}]}
    assert 'value' not in evaluate_delivery([f], [atom])[0]['covered_outputs']


def test_supported_natural_comparison_wording_is_a_verdict():
    f = build_contract('有效波高和最大波高有什么区别？')[0]
    atom = {'aid': 'contrast', 'type': 'direct_fact',
            'text': '不能把有效波高理解为最大波高，也不能把最大波高当作前1/3波高的平均值[资料1]。',
            'facet_ids': [f['fid']], 'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '波高的平均值'}]}
    assert evaluate_delivery([f], [atom])[0]['status'] == 'full'


def test_grounded_comparison_in_recovery_is_not_reported_missing():
    f = build_contract('有效波高和最大波高有什么区别？')[0]
    atom = {'aid': 'contrast', 'type': 'direct_fact',
            'text': '两者核心区别在统计口径：最大波高是单个最大值，有效波高是前1/3波高的平均值[资料1]。',
            'facet_ids': [f['fid']],
            'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': '最大值；平均值'}]}
    assert evaluate_delivery([f], [atom])[0]['status'] == 'full'
    # A scope note is not an accepted substantive fact, even with this wording.
    # Quote validation itself belongs to the grounding stage before this floor.
    atom['type'] = 'scope_note'
    assert evaluate_delivery([f], [atom])[0]['status'] == 'missing'
