"""Scope regression at semantic boundaries, not question-number exceptions."""
from copy import deepcopy

import pytest

from marine_kb.facet_contract import build_contract
from marine_kb.scope_gate import classify_gap, gate, gate_answer, gate_atoms, reconcile_gap_resolution


def fact(text, fid='f1', aid='local1_1_c1a1', kind='direct_fact', passage=1):
    return {'aid': aid, 'type': kind, 'text': text, 'facet_ids': [fid],
            'evidence_quotes': [{'citation': 1, 'passage_id': passage, 'quote': text}]}


@pytest.mark.parametrize('gap', [
    '浙江省气象灾害预警发布平台的独立访问网址',
    '浙江海洋预报网的具体访问网址',
    '沿海应急发布平台的独立链接',
])
def test_nominal_channel_gap_is_classified_and_exact_critic_can_remove(gap):
    facets = build_contract('有哪些官方渠道可以查台风和海洋预警？')
    assert classify_gap(gap, facets[0]) != 'unclassified'
    result = gate([gap], facets, [{'kind': 'unasked_gap', 'text': gap}])
    assert not result['kept'] and result['removed']
    assert result['answerability_upgrade_allowed'] is False


def test_explicit_url_request_is_not_automatically_demoted():
    gap = '官方平台的独立网址'
    assert gate([gap], build_contract('请给官方平台的独立网址。'), [])['kept'] == [gap]
    result = gate([gap], build_contract('请给官方平台的独立网址。'), [{'kind': 'unasked_gap', 'text': gap}])
    assert result['kept'] == [gap] and result['decisions'][0]['protected']


def test_unasked_local_outbreak_is_scope_note_without_critic():
    gap = '宁波当地登革热病例监测或疫情暴发情况的依据'
    contract = build_contract('洪水后蚊虫变多是否就说明宁波已经暴发登革热？')
    assert contract[0]['kind'] == 'risk_relation'
    assert classify_gap(gap, contract[0]) == 'live_status_not_available'
    result = gate([gap], contract, [])
    assert result['kept'] == [] and result['scope_notes']
    assert result['removed'][0]['rule'] == 'contract_scope_note'


@pytest.mark.parametrize('question', [
    '宁波当前是否已经暴发登革热？',
    '今天南沙浴场开放吗？',
    '宁波今天有多少例登革热病例？',
])
def test_asked_current_state_and_numbers_are_hard_even_when_critic_mislabels(question):
    gap = '尚未取得当地当日病例、开放状态或疫情监测资料'
    result = gate([gap], build_contract(question), [{'kind': 'unasked_gap', 'text': gap}])
    assert result['kept'] == [gap]


def test_mixed_personal_eligibility_and_optional_subsidy_are_independently_audited():
    question = '我是象山县养殖户，损失了能赔吗？'
    gap = '象山当地养殖保险（网箱、池塘）的承保范围、投保条件、保费补贴及理赔流程依据'
    result = gate([{'fid': 'f1', 'text': gap}], build_contract(question),
                  [{'kind': 'unasked_gap', 'text': gap}])
    assert len(result['kept']) == 1
    assert '承保范围' in result['kept'][0] and '投保条件' in result['kept'][0]
    assert '保费补贴' not in result['kept'][0]
    assert result['replacements'][0]['original'] == gap
    assert any(row['text'] == '保费补贴' and row['action'] == 'remove_unasked_gap'
               for row in result['decisions'])
    assert all(row['protected'] for row in result['decisions']
               if '投保' in row['text'] or '承保范围' in row['text'])


def test_asked_subsidy_is_not_removed_without_a_critic():
    gap = '保费补贴'
    assert gate([gap], build_contract('养殖保险保费补贴如何申请？'), [])['kept'] == [gap]
    assert gate([gap], build_contract('养殖保险保费补贴如何申请？'),
                [{'kind': 'unasked_gap', 'text': gap}])['kept'] == [gap]


@pytest.mark.parametrize('gap', [
    '承保范围和投保条件，如果未参保则不得赔付；保费补贴',
    '《资格、补贴及理赔办法》原文尚未取得',
    '缆绳、框架和网衣的检查方法不足',
])
def test_conditions_titles_and_inspection_objects_are_not_mechanically_split(gap):
    result = gate([gap], build_contract('养殖户损失能赔吗？'), [])
    assert result['kept'] == [gap]
    assert len(result['decisions']) == 1


def test_sibling_current_state_owns_its_gap_even_if_other_sibling_is_general():
    contract = build_contract('台风预警渠道有哪些？当前宁波已发布哪些预警？')
    gap = {'fid': 'f2', 'text': '宁波当日预警发布状态尚未取得'}
    result = gate([gap], contract, [{'kind': 'unasked_gap', 'text': gap['text']}])
    assert result['kept'] == [gap['text']]
    assert result['decisions'][0]['facet_ids'] == ['f2']


def test_gap_atoms_use_same_scope_rules_without_modifying_facts_or_promoting():
    facets = build_contract('有哪些官方海洋预警渠道？')
    accepted = [fact('官方海洋预报平台设灾害预警栏目。', aid='c1a1'),
                {'aid': 'c1a2', 'type': 'gap', 'text': '官方平台的独立访问网址',
                 'gap_type': 'evidence_not_found', 'facet_ids': ['f1']}]
    before = deepcopy(accepted)
    result = gate_atoms(accepted, facets)
    assert result['accepted_atoms'] == [accepted[0]]
    assert result['removed_gap_atom_ids'] == ['c1a2']
    assert accepted == before and not result['answerability_upgrade_allowed']


def test_mixed_body_limitation_never_loses_retained_personal_condition():
    text = '缺少本地网箱专用规范；保单是否赔付尚未核定。'
    contract = build_contract('网箱锚泊怎么检查？')
    result = gate_answer(text, [text], contract, [{'kind': 'unasked_gap', 'text': text}])
    assert '保单' in result['answer'] and not result['removed_answer_units']


def test_new_bound_channel_delivery_resolves_stale_metadata_gap():
    facets = build_contract('查询海浪警报有哪些渠道和查询入口？')
    gap = {'fid': 'f1', 'text': '官方海浪警报查询渠道及入口尚未取得'}
    new = fact('查询海浪警报，可在国家海洋预报台官网 https://example.gov.cn 进入海浪警报栏目。')
    result = reconcile_gap_resolution([gap], facets, [new])
    assert result['kept'] == []
    assert result['resolved'][0]['rule'] == 'resolved_by_new_grounded_facet_delivery'
    assert not result['answerability_upgrade_allowed']


@pytest.mark.parametrize('mutation', ['frozen', 'other_fid', 'metadata', 'no_quotes', 'no_url'])
def test_stale_url_gap_not_resolved_by_old_sibling_metadata_or_incomplete_recovery(mutation):
    facets = build_contract('请给查询海浪警报的官网网址和入口。')
    gap = {'fid': 'f1', 'text': '官网网址及海浪警报查询入口尚未取得'}
    new = fact('查询海浪警报，可在国家海洋预报台官网 https://example.gov.cn 进入海浪警报栏目。')
    if mutation == 'frozen':
        new['aid'] = 'c1a1'
    elif mutation == 'other_fid':
        new['facet_ids'] = ['f2']
    elif mutation == 'metadata':
        new['type'] = 'document_identity'
        new['evidence_quotes'][0]['passage_id'] = 0
    elif mutation == 'no_quotes':
        new['evidence_quotes'] = []
    else:
        new['text'] = new['text'].replace(' https://example.gov.cn', '')
    result = reconcile_gap_resolution([gap], facets, [new])
    assert result['kept'] == [gap]


@pytest.mark.parametrize('gap', [
    '个人是否已投保及具体保险合同条款', 'SWL定义和高程基准尚未核定',
    '所引文件的现行有效性和修订版本尚未核实', '尚缺浙江全部官方预警完整清单',
])
def test_complete_looking_new_text_never_resolves_separate_business_adjudication(gap):
    facets = build_contract('查询海浪警报有哪些渠道和入口？')
    new = fact('查询海浪警报，可在国家海洋预报台官网 https://example.gov.cn 进入海浪警报栏目。')
    missing = {'fid': 'f1', 'text': gap}
    assert reconcile_gap_resolution([missing], facets, [new])['kept'] == [missing]
