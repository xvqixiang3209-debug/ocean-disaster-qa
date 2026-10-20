"""Independent publication-floor negatives, not recorded judge opinions."""
import pytest

from marine_kb.facet_contract import build_contract
from marine_kb.facet_delivery import covered_outputs, evaluate_delivery, publishable


def atom(text, *, aid='a1', type='direct_fact', refs=True, **kwargs):
    return {'aid': aid, 'text': text, 'type': type, 'facet_ids': ['f1'],
            'evidence_quotes': [{'citation': 1, 'passage_id': 1, 'quote': text}] if refs else [], **kwargs}


def contract(kind, outputs, **kwargs):
    return {'id': 1, 'fid': 'f1', 'question': '独立业务问题', 'kind': kind,
            'required_outputs': outputs, 'allowed_gaps': ['evidence_not_found'], **kwargs}


@pytest.mark.parametrize('missing', ['place', 'time', 'provenance'])
def test_numeric_tuple_needs_every_bound_cell(missing):
    f = contract('numeric_value', ['value', 'unit', 'place', 'time', 'provenance'], places=['舟山'])
    text = '2026年10月20日舟山浪高2米。'
    if missing == 'place':
        text = text.replace('舟山', '')
    if missing == 'time':
        text = text.replace('2026年10月20日', '')
    rows = evaluate_delivery([f], [atom(text, refs=missing != 'provenance')])
    assert rows[0]['status'] == 'partial'
    assert not publishable([f], rows)


def test_numeric_complete_tuple_and_mismatched_sibling_negative():
    f = contract('numeric_value', ['value', 'unit', 'place', 'time', 'provenance'], places=['舟山'])
    good = evaluate_delivery([f], [atom('2026年10月20日舟山浪高2米。')])
    assert good[0]['status'] == 'full' and publishable([f], good)
    mixed = [atom('舟山浪高2米。'), atom('2026年10月20日宁波浪高3米。', aid='a2')]
    bad = evaluate_delivery([f], mixed)
    assert bad[0]['status'] == 'partial' and not publishable([f], bad)


def test_pure_date_is_not_a_business_value():
    f = contract('numeric_value', ['value', 'unit', 'place', 'time', 'provenance'])
    row = evaluate_delivery([f], [atom('本次发布时间为2026年10月20日08时。')])[0]
    assert 'value' in row['missing_outputs'] and 'unit' in row['missing_outputs']


def test_basis_identity_and_body_both_needed():
    f = contract('basis_document', ['document_identity', 'basis_content'])
    name = atom('依据《某省救助预案》办理。', type='document_identity')
    assert evaluate_delivery([f], [name])[0]['status'] == 'missing'
    body = atom('县级审核后发放救助资金。', aid='a2')
    assert evaluate_delivery([f], [body])[0]['status'] == 'partial'
    assert evaluate_delivery([f], [name, body])[0]['status'] == 'full'


def test_unrelated_fact_is_not_basis_content():
    f = contract('basis_document', ['document_identity', 'basis_content'])
    name = atom('依据《某省救助预案》办理。', type='document_identity')
    unrelated = atom('页面上有蓝色图标。', aid='a2')
    assert evaluate_delivery([f], [name, unrelated])[0]['status'] == 'partial'


@pytest.mark.parametrize('text,expected', [
    ('请先提交申请并报送材料。', 'partial'),
    ('由县级应急部门审核，县级政府批准后发放资金。', 'full'),
])
def test_procedure_needs_actors_and_actions(text, expected):
    f = contract('procedure', ['actors', 'steps'])
    assert evaluate_delivery([f], [atom(text)])[0]['status'] == expected


def test_repeated_same_verb_can_be_two_supported_method_steps():
    f = contract('method_steps', ['steps'])
    own = [atom('检查锚链连接。'), atom('检查网衣破损。', aid='a2')]
    assert evaluate_delivery([f], own)[0]['status'] == 'full'


@pytest.mark.parametrize('output,text', [
    ('definition', '请检查锚链连接。'),
    ('conditions', '有两个申请渠道。'),
    ('application_path', '可提出申请。'),
    ('provenance', '2026年10月20日舟山浪高2米。'),
])
def test_single_generic_fact_does_not_fill_special_outputs(output, text):
    f = contract('explanation', [output])
    assert output not in covered_outputs(f, [atom(text, refs=output != 'provenance')])


def test_status_has_scoped_date_place_and_body_provenance():
    f = contract('status_at_time', ['status', 'place', 'valid_time', 'provenance'], places=['朱家尖'])
    good = evaluate_delivery([f], [atom('2026年10月20日朱家尖南沙浴场开放。')])
    assert good[0]['status'] == 'full' and publishable([f], good)
    bad = evaluate_delivery([f], [atom('2026年10月20日南沙浴场开放。')])
    assert bad[0]['status'] == 'partial' and not publishable([f], bad)


def test_real_gap_always_prevents_full_even_when_other_outputs_present():
    f = contract('explanation', ['answer'], allowed_extra='unused')
    own = [atom('本项可以向主管部门咨询。'),
           atom('尚缺该项规定正文。', aid='a2', type='gap', gap_type='evidence_not_found', refs=False)]
    assert evaluate_delivery([f], own)[0]['status'] == 'partial'


def test_current_legal_version_gap_is_protected_from_scope_removal():
    from marine_kb.scope_gate import gate
    q = '现行过渡期生活救助依据什么办理？'
    missing = '尚未核实所引文件的现行版本和修订废止关系'
    result = gate([missing], build_contract(q), [{'kind': 'unasked_gap', 'text': missing}])
    assert result['kept'] == [missing] and result['decisions'][0]['protected']
