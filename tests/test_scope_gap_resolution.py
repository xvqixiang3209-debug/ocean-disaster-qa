from copy import deepcopy

import pytest

from marine_kb.facet_contract import build_contract
from marine_kb.scope_gate import reconcile_gap_resolution
from marine_kb.facet_delivery import evaluate_delivery


def setup(kind='numeric_value', missing='尚未取得舟山2026年10月20日浪高数值'):
    f = build_contract('2026年10月20日舟山浪高多少？')[0]
    f['kind'] = kind
    gap = {'aid': 'c1a1', 'type': 'gap', 'gap_type': 'business_data_missing',
           'text': missing, 'facet_ids': ['f1'], 'evidence_quotes': []}
    text = '2026年10月20日舟山浪高2米。'
    recovered = {'aid': 'local1_1_c1a1', 'type': 'direct_fact', 'text': text,
                 'facet_ids': ['f1'], 'evidence_quotes': [{'citation': 2, 'passage_id': 1, 'quote': text}]}
    return f, gap, recovered


def test_new_bound_recovery_resolves_gap_but_cannot_itself_promote_answer():
    f, gap, new = setup()
    before = deepcopy([gap, new])
    assert evaluate_delivery([f], [gap, new])[0]['status'] == 'partial'
    result = reconcile_gap_resolution([gap['text']], [f], [gap, new])
    assert result['kept'] == [] and result['removed_gap_atom_ids'] == ['c1a1']
    assert result['accepted_atoms'] == [new]
    assert result['answerability_upgrade_allowed'] is False
    assert [gap, new] == before


@pytest.mark.parametrize('mutation', ['frozen', 'other_fid', 'no_quotes', 'card_only', 'wrong_day', 'wrong_place', 'no_time', 'no_unit'])
def test_incomplete_or_old_fact_cannot_clear_gap(mutation):
    f, gap, new = setup()
    if mutation == 'frozen':
        new['aid'] = 'c2a1'
    elif mutation == 'other_fid':
        new['facet_ids'] = ['f2']
    elif mutation == 'no_quotes':
        new['evidence_quotes'] = []
    elif mutation == 'card_only':
        new['evidence_quotes'][0]['passage_id'] = 0
    else:
        replacements = {'wrong_day': ('20日', '21日'), 'wrong_place': ('舟山', '宁波'),
                        'no_time': ('2026年10月20日', ''), 'no_unit': ('2米', '2')}
        old, target = replacements[mutation]
        new['text'] = new['text'].replace(old, target)
    result = reconcile_gap_resolution([gap['text']], [f], [gap, new])
    assert result['kept'] == [gap['text']] and not result['resolved']


def test_without_explicit_missing_binding_no_fuzzy_resolution():
    f, gap, new = setup()
    text = '还缺该地今天的海况结果'
    assert reconcile_gap_resolution([text], [f], [gap, new])['kept'] == [text]
    assert reconcile_gap_resolution([{'fid': 'f1', 'text': gap['text']}], [f], [new])['kept'] == []


@pytest.mark.parametrize('protected', ['现行版本未核实', '个人资格未核实', '高程基准未核实',
                                       '尚缺全部官方预警完整清单', '保单赔付条件尚未核定'])
def test_protected_gap_never_resolved_by_new_numbers(protected):
    f, gap, new = setup(missing=protected)
    result = reconcile_gap_resolution([protected], [f], [gap, new])
    assert result['kept'] == [protected]


def test_current_warning_status_can_resolve_bounded_data_gap():
    f = build_contract('2026年10月20日舟山当前海浪警报状态？')[0]
    text = '2026年10月20日舟山已发布海浪橙色警报。'
    missing = {'fid': 'f1', 'text': '尚未取得舟山2026年10月20日海浪警报当前状态'}
    new = {'aid': 'local1_1_c1a1', 'type': 'direct_fact', 'text': text, 'facet_ids': ['f1'],
           'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': text}]}
    assert reconcile_gap_resolution([missing], [f], [new])['kept'] == []


def test_opening_permission_is_not_cleared_by_generic_new_status():
    f = build_contract('2026年10月20日朱家尖浴场是否开放？')[0]
    missing = {'fid': 'f1', 'text': '未取得朱家尖浴场当日开放状态'}
    text = '2026年10月20日朱家尖浴场开放。'
    new = {'aid': 'local1_1_c1a1', 'type': 'direct_fact', 'text': text, 'facet_ids': ['f1'],
           'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': text}]}
    result = reconcile_gap_resolution([missing], [f], [new])
    assert result['kept'] == [missing] and not result['resolved']
