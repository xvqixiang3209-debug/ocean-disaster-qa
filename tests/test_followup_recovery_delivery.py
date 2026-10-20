"""Recorded-output counterexamples plus unseen object/intent variants.

These tests re-evaluate already recorded atoms. They do not execute a model,
assert newly generated answers or treat a better coverage label as accuracy.
"""
import copy
import json
from pathlib import Path

import pytest

from marine_kb.facet_delivery import (
    covered_outputs, evaluate_delivery, render_answer, render_with_gaps,
    semantic_match,
)
from marine_kb.recovery import recover_locally


def fact(text, fid='f1', aid='a1', **kwargs):
    return {'aid': aid, 'type': 'direct_fact', 'text': text,
            'facet_ids': [fid], 'evidence_quotes': [
                {'citation': 1, 'passage_id': 1, 'quote': text}], **kwargs}


def facet(fid='f1', *, kind='method_steps', objects=None, question='如何处理？', outputs=None):
    return {'fid': fid, 'kind': kind, 'question': question,
            'objects': objects or [], 'required_outputs': outputs or ['steps'],
            'allowed_gaps': ['evidence_not_found']}


@pytest.fixture(scope='module')
def recorded():
    path = Path(__file__).parent / 'fixtures/p1_p6_followup_delivery_cases.json'
    return json.loads(path.read_text(encoding='utf-8'))['cases']


def test_recorded_weather_channel_cannot_be_delivered_by_marine_sibling(recorded):
    case = recorded['C2']
    rows = evaluate_delivery(case['facets'], case['accepted_atoms'])
    assert case['old_delivery'][0]['status'] == 'full'
    assert rows[0]['status'] == 'missing'
    assert rows[0]['object_conflict_atom_ids']
    assert rows[1]['status'] == 'full'
    rendered = render_answer(case['facets'], case['accepted_atoms'])
    assert '查询气象部门的台风预警去哪里查' not in rendered
    assert '查询海洋部门的海浪警报' in rendered


@pytest.mark.parametrize('case_id', ['M1_extra_2', 'M1_extra_3'])
def test_recorded_possible_entry_and_allergy_do_not_answer_infection_risk(recorded, case_id):
    case = recorded[case_id]
    rows = evaluate_delivery(case['facets'], case['accepted_atoms'])
    assert case['old_delivery'][0]['status'] == 'full'
    assert rows[0]['status'] != 'full'
    assert {'conditions', 'relation'} <= set(rows[0]['missing_outputs'])
    assert rows[1]['status'] == 'full'
    assert rows[0]['object_conflict_atom_ids']


def test_recorded_cleanup_is_not_bite_protection(recorded):
    case = recorded['M2']
    target = case['facets'][2]
    cleanup = [a for a in case['accepted_atoms'] if 'f3' in a['facet_ids'] and '翻盆倒罐' in a['text']]
    assert cleanup and all(not semantic_match(a, target, case['facets']) for a in cleanup)
    # Valid, supported personal protection is retained, so the appropriate
    # facet remains full after cleanup-only sibling atoms are excluded.
    assert evaluate_delivery(case['facets'], case['accepted_atoms'])[2]['status'] == 'full'


def test_recorded_aquaculture_instructions_cannot_satisfy_compensation(recorded):
    case = recorded['A1']
    target = case['facets'][2]
    engineering = [a for a in case['accepted_atoms'] if 'f3' in a['facet_ids'] and '检查缆绳' in a['text']]
    assert engineering and all(not semantic_match(a, target, case['facets']) for a in engineering)
    rows = evaluate_delivery(case['facets'], case['accepted_atoms'])
    assert rows[2]['object_conflict_atom_ids']
    assert rows[0]['status'] == rows[1]['status'] == 'full'


@pytest.mark.parametrize('place', ['温岭', '岱山', '平阳'])
def test_unseen_channel_variants_require_requested_business_object(place):
    fs = [facet(kind='channel', objects=['台风预警'],
                question=place + '居民如何查询气象部门台风预警？', outputs=['channel_name', 'access_path']),
          facet('f2', kind='channel', objects=['海浪警报'],
                question=place + '居民如何查询海洋部门海浪警报？', outputs=['channel_name', 'access_path'])]
    wrong = fact('进入某省海洋预报网，查看“灾害预警”板块的海浪警报。')
    assert not semantic_match(wrong, fs[0], fs)
    assert evaluate_delivery(fs, [wrong])[0]['status'] != 'full'
    right = fact('进入某省天气网，查看“天气预警”栏目中的台风预警。')
    assert evaluate_delivery(fs, [right])[0]['status'] == 'full'


@pytest.mark.parametrize('text', ['可通过网站平台查询。', '相关平台由应急管理部门提供。', '查询渠道是一个网站。'])
def test_generic_channel_words_do_not_fill_named_channel_and_path(text):
    f = facet(kind='channel', outputs=['channel_name', 'access_path'])
    assert evaluate_delivery([f], [fact(text)])[0]['status'] != 'full'


def test_mosquito_risk_needs_actual_disease_relation_and_boundary():
    f = facet(kind='risk_relation', question='暴雨后蚊虫增多，会不会传染疾病？', outputs=['relation', 'conditions'])
    wrong = [fact('蚊虫可能进入住宅时，床上设置蚊帐。'),
             fact('如果出现过敏反应，应及时就医。', aid='a2')]
    assert not covered_outputs(f, wrong)
    supported = fact('携带病原体的蚊虫叮咬可传播疾病，蚊虫增多不等于已经发生疫情。')
    assert evaluate_delivery([f], [supported])[0]['status'] == 'full'
    allergy_only = fact('被蚊虫叮咬有过敏风险，如果出现过敏反应应及时就医。')
    assert evaluate_delivery([f], [allergy_only])[0]['status'] != 'full'


def test_personal_actions_are_counted_and_duplicate_steps_are_not():
    f = facet(question='如何防叮咬？')
    good = [fact('户外穿长袖长裤。'), fact('安装纱窗。', aid='a2'), fact('睡觉挂蚊帐。', aid='a3')]
    assert evaluate_delivery([f], good)[0]['status'] == 'full'
    duplicate = [fact('检查网衣破损。'), fact('1. 检查网衣破损[资料2]。', aid='a2')]
    assert evaluate_delivery([facet()], duplicate)[0]['status'] != 'full'
    assert render_answer([facet()], duplicate).count('检查网衣破损') == 1


def test_omitted_subject_and_different_actions_remain_legitimate():
    f = facet(objects=['网箱'], question='台风前如何加固网箱？')
    own = [fact('检查锚链连接。'), fact('固定框架连接点。', aid='a2')]
    assert evaluate_delivery([f], own)[0]['status'] == 'full'


def test_recovery_uses_full_registry_but_only_target_can_be_added():
    fs = [facet(objects=['网箱'], question='如何加固网箱？'),
          facet('f2', objects=['池塘'], question='如何加固池塘？')]
    frozen = fact('网箱检查锚链，并固定连接点。')
    initial = {'accepted_atoms': [frozen]}
    before = copy.deepcopy(initial)
    seen = {}

    def regenerate(failed, accepted, evidence, rejected, *, facet_registry, target_fids):
        seen['targets'] = target_fids
        seen['registry'] = facet_registry
        assert [f['fid'] for f in failed] == ['f2']
        facet_registry[0]['question'] = 'callback private copy'
        return {'answer': 'unused structural output'}

    def ground(raw, evidence, registry, *, target_fids, facet_registry):
        assert [f['fid'] for f in registry] == ['f1', 'f2']
        assert registry[0]['question'] == fs[0]['question']
        assert target_fids == ['f2']
        return {'accepted_atoms': [
            fact('网箱检查锚链，并固定连接点。', 'f2', 'wrong'),
            fact('池塘清理排水沟，并加固堤埂。', 'f2', 'right'),
            fact('网箱增加检查次数。', 'f1', 'sibling')]}

    result = recover_locally(initial, [], fs, regenerate=regenerate, ground=ground, budget=lambda stage: True)
    assert initial == before and result['accepted_atoms'][0] == frozen
    assert [f['fid'] for f in seen['registry']] == ['f1', 'f2']
    assert len(result['accepted_atoms']) == 2
    errors = {r.get('error') for r in result['recovery_trace']}
    assert {'foreign_object_or_intent_rejected', 'rewritten_sibling_rejected'} <= errors


def test_exact_atomic_duplicate_reuses_frozen_aid_for_synthesis_premise():
    f = facet(kind='distinction', question='甲制度与乙制度有哪些区别？', outputs=['verdict', 'comparison'])
    frozen = fact('甲制度属于救助。', aid='old')
    new = [fact('1. 甲制度属于救助[资料2]。', aid='same'),
           fact('乙制度属于保险。', aid='other'),
           fact('两种制度不同。', aid='synthesis', type='synthesis', premises=['same', 'other'])]
    result = recover_locally({'accepted_atoms': [frozen]}, [], [f],
                             regenerate=lambda *args: {}, ground=lambda *args: {'accepted_atoms': new},
                             budget=lambda stage: True)
    assert len(result['accepted_atoms']) == 3
    synthesis = result['accepted_atoms'][-1]
    assert synthesis['premises'][0] == 'old'
    assert synthesis['premises'][1] == result['accepted_atoms'][1]['aid']


def test_numeric_leading_decimal_is_not_treated_as_numbered_list():
    f = facet(kind='numeric_value', outputs=['value', 'unit', 'place', 'time', 'provenance'])
    a = fact('1.25米是2026年10月20日舟山的浪高。')
    b = fact('25米是2026年10月20日舟山的浪高。', aid='b')
    # The callback is a structural fixture; a real grounding decoder still
    # verifies every value. This checks that dedup never changes that value.
    result = recover_locally({'accepted_atoms': [a]}, [], [f], regenerate=lambda *args: {},
                             ground=lambda *args: {'accepted_atoms': [b]}, budget=lambda stage: True)
    # The numeric facet is already delivered, so no needless rewrite occurs.
    assert result['accepted_atoms'] == [a]
    from marine_kb.recovery import _claim_key
    assert _claim_key(a) != _claim_key(b)


def test_rejected_foreign_premise_invalidates_all_dependent_syntheses():
    fs = [facet(objects=['甲事项']), facet('f2', kind='distinction', objects=['乙事项'],
                outputs=['verdict', 'comparison'])]
    frozen = fact('甲事项检查并固定连接点。')
    new = [fact('甲事项的程序事实。', 'f2', 'foreign'),
           fact('乙事项的第一结论。', 'f2', 'dependent', type='synthesis', premises=['foreign']),
           fact('乙事项的第二结论。', 'f2', 'transitive', type='synthesis', premises=['dependent'])]
    result = recover_locally({'accepted_atoms': [frozen]}, [], fs,
                             regenerate=lambda *args: {}, ground=lambda *args: {'accepted_atoms': new},
                             budget=lambda stage: True)
    assert result['accepted_atoms'] == [frozen]
    rejected = [r for r in result['recovery_trace'] if r.get('error') == 'rejected_synthesis_premise']
    assert {r['aid'] for r in rejected} == {'dependent', 'transitive'}


def test_gap_rendering_uses_chinese_outputs_and_formatted_time():
    f = facet(kind='risk_relation', objects=['蚊虫'], outputs=['relation', 'conditions'])
    f['time'] = {'start': '2026-10-20', 'end': '2026-10-21', 'as_of': None}
    text = render_with_gaps([f], [])
    assert 'relation' not in text and 'conditions' not in text
    assert "{'start':" not in text
    assert '对象与风险之间的关系' in text and '适用条件或判断边界' in text
    assert '2026-10-20至2026-10-21' in text
