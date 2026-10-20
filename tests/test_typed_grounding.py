"""P3 structural tests. Judge responses here are synthetic, not model runs."""
import copy
import json

import pytest

from marine_kb.facet_delivery import evaluate_delivery, publishable, render_with_gaps
from marine_kb.qa import _decode_model_answer
from marine_kb.recovery import recover_locally
from marine_kb.typed_grounding import apply_review, prepare_draft, review_atoms, find_judge_conflicts, resolve_judge_conflicts


def facet(fid='f1', kind='explanation', objects=(), required=('explanation',), **kw):
    return {'fid': fid, 'id': int(fid[1:]), 'question': '、'.join(objects) + '如何办理？', 'kind': kind,
            'objects': list(objects), 'required_outputs': list(required),
            'allowed_gaps': ['evidence_not_found'], **kw}


def evidence(*texts):
    return [{'citation': n, 'title': '来源'+str(n), 'text': text,
             'passages': [{'id': 1, 'text': text}]} for n, text in enumerate(texts, 1)]


def decode(value, sources):
    return _decode_model_answer(json.dumps(value, ensure_ascii=False), sources)


def typed(raw, facets, sources, decide=None):
    atoms = prepare_draft(raw, facets)['atoms']
    rows = []
    for i, atom in enumerate(atoms):
        row = {'aid': atom['aid'], 'text': atom['text'], 'status': 'supported', 'type': 'direct_fact',
               'facet_ids': atom['facet_ids'], 'refs': [{'citation': 1, 'passage_id': 1}]}
        if decide:
            row.update(decide(atom, i) or {})
        rows.append(row)
    return apply_review(raw, {'atoms': rows}, sources, facets, decode)


def test_semicolon_partial_recovery_preserves_exact_siblings():
    fs = [facet('f1', objects=['甲制度']), facet('f2', objects=['乙制度']), facet('f3', objects=['丙制度'])]
    raw = {'answer': '甲制度的补助额尚无依据；乙制度由县级审核后发放[资料1]；丙制度依保险合同申请理赔[资料2]。'}
    sources = evidence('乙制度由县级审核后发放。', '丙制度依保险合同申请理赔。')
    result = typed(raw, fs, sources, lambda a, i: {'status': 'unsupported', 'refs': []} if i == 0 else {'refs': [{'citation': i, 'passage_id': 1}]})
    assert len(result['accepted_atoms']) == 2
    assert '乙制度由县级审核后发放' in result['answer']
    assert '丙制度依保险合同申请理赔' in result['answer']
    assert '甲制度的补助额尚无依据' not in result['answer']
    for atom in result['accepted_atoms']:
        assert raw['answer'][atom['start']:atom['end']] == atom['text']


@pytest.mark.parametrize('sentence', ['若潮位升高；则立即撤离。', '只有审批通过；才能发放。', '除海钓以外；其他活动暂停。'])
def test_conditional_scope_never_split(sentence):
    assert len(prepare_draft({'answer': sentence}, [facet()])['atoms']) == 1


def test_parallel_clauses_split_only_with_distinct_explicit_subjects():
    fs = [facet('f1', objects=['甲制度']), facet('f2', objects=['乙制度'])]
    parts = prepare_draft({'answer': '甲制度属于救助，而乙制度属于保险。'}, fs)['atoms']
    assert len(parts) == 2 and parts[1]['text'].startswith('而乙制度')
    assert len(prepare_draft({'answer': '甲制度不批准，但可以提出申请。'}, fs)['atoms']) == 1


def test_removed_heading_does_not_change_procedure_owner():
    fs = [facet('f1', objects=['过渡期生活救助']), facet('f2', objects=['冬春救助'])]
    raw = {'answer': '1. 过渡期生活救助：依据不足。\n2. 冬春救助：这条依据有误。\n办理时，基层按“户报、村评、乡审、县定”的程序进行申报审批[资料1]。'}
    atoms = prepare_draft(raw, fs)['atoms']
    procedure = next(a for a in atoms if '户报' in a['text'])
    assert procedure['facet_ids'] == ['f2'] and procedure['binding'] == 'inherited_heading'
    sources = evidence('办理时，基层按“户报、村评、乡审、县定”的程序进行申报审批。')
    def judge(a, i):
        if '户报' not in a['text']:
            return {'status': 'unsupported', 'refs': []}
        return {'facet_ids': ['f1']}
    result = typed(raw, fs, sources, judge)
    assert not result['accepted_atoms']
    assert any(d['drop_rule'] == 'typed_grounding_facet_reassignment' for d in result['decisions'])


def test_atom_cannot_be_rewritten_or_joined_by_model():
    raw = {'answer': '县级审核后发放救助资金[资料1]。'}
    result = typed(raw, [facet()], evidence('县级审核后发放救助资金。'), lambda a, i: {'text': '县级立即发放救助资金'})
    assert result['decisions'][0]['drop_rule'] == 'rejected_atom_not_substring'


@pytest.mark.parametrize('bad_ref', [0, 2, -1])
def test_body_facts_cannot_use_card_or_unknown_passage(bad_ref):
    raw = {'answer': '该站最高水位为2米[资料1]。'}
    result = typed(raw, [facet()], evidence('该站最高水位为2米。'), lambda a, i: {'refs': [{'citation': 1, 'passage_id': bad_ref}]})
    assert not result['accepted_atoms']


def test_wrong_source_and_wrong_unit_rejected_by_existing_decoder():
    for source in ('该站最高水位为2厘米。', '该站最高水位为3米。'):
        result = typed({'answer': '该站最高水位为2米[资料1]。'}, [facet()], evidence(source))
        assert not result['accepted_atoms']
    result = typed({'answer': '该站最高水位为2米[资料1]。'}, [facet()], evidence('该站最高水位为2米。'))
    assert result['accepted_atoms']


def card_evidence(title='关于印发《某市救助预案》的通知'):
    return [{'citation': 1, 'title': title, 'text': '县级审核后发放。',
             'doc_card': {'title': title, 'document_names': ['某市救助预案'], 'issuer': '某市政府', 'effective_from': '2025-04-30'},
             'passages': [{'id': 0, 'text': '文件：'+title+'；施行日期：2025-04-30'}, {'id': 1, 'text': '县级审核后发放。'}]}]


def test_document_identity_checks_card_names_not_arbitrary_body_facts():
    source = card_evidence()
    kind = lambda a, i: {'type': 'document_identity', 'refs': [{'citation': 1, 'passage_id': 0}]}
    good = typed({'answer': '依据《某市救助预案》办理[资料1]。'}, [facet(kind='basis_document', required=['per_object_basis'])], source, kind)
    assert good['accepted_atoms'] and not good['publishable']
    for bad in ('依据《另一市救助预案》办理[资料1]。', '《某市救助预案》规定每人20元[资料1]。', '《某市救助预案》全国适用[资料1]。'):
        assert not typed({'answer': bad}, [facet()], source, kind)['accepted_atoms']


def test_document_identity_dates_do_not_exchange_issued_and_effective_roles():
    source = card_evidence()
    kind = lambda a, i: {'type':'document_identity', 'refs':[{'citation':1,'passage_id':0}]}
    good = typed({'answer':'该方案自2025年4月30日起施行[资料1]。'}, [facet()], source, kind)
    assert good['accepted_atoms'] and not good['publishable']
    bad = typed({'answer':'该方案于2025年4月30日印发[资料1]。'}, [facet()], source, kind)
    assert not bad['accepted_atoms']
    assert bad['decisions'][0]['drop_rule'] == 'document_identity_date_mismatch'


def synthesis_fixture(conclusion='三者不是一回事。'):
    fs = [facet('f1', kind='distinction', objects=['甲救助', '乙救助', '丙赔付'], required=['verdict']),
          facet('f2', objects=['甲救助']), facet('f3', objects=['乙救助']), facet('f4', objects=['丙赔付'])]
    texts = ['甲救助属于政府救助。', '乙救助属于政府救助。', '丙赔付属于保险理赔。']
    raw = {'answer': conclusion+'\n'+ '\n'.join(t[:-1]+f'[资料{i}]。' for i,t in enumerate(texts,1))}
    atoms = prepare_draft(raw, fs)['atoms']
    rows = [{'aid': a['aid'], 'text': a['text'], 'status': 'supported', 'type': 'direct_fact', 'facet_ids': a['facet_ids'],
             'refs': [{'citation': i, 'passage_id': 1}]} for i,a in enumerate(atoms[1:],1)]
    first = atoms[0]
    row = {'aid': first['aid'], 'text': first['text'], 'status': 'synthesis', 'type': 'synthesis', 'facet_ids': ['f1'],
           'premises': [a['aid'] for a in atoms[1:]], 'inference': {'operator': 'not_equivalent', 'field': 'category',
             'comparisons': [{'premise_id': a['aid'], 'subject': subject, 'predicate': '属于', 'value': value}
                             for a,subject,value in zip(atoms[1:], ['甲救助','乙救助','丙赔付'], ['政府救助','政府救助','保险理赔'])]}}
    return fs, raw, evidence(*texts), {'atoms': [row]+rows}


def test_closed_comparison_synthesis_requires_all_grounded_relations():
    fs, raw, sources, review = synthesis_fixture()
    result = apply_review(raw, review, sources, fs, decode)
    assert any(a['type'] == 'synthesis' for a in result['accepted_atoms'])
    assert result['publishable'] and '三者不是一回事' in result['answer']


@pytest.mark.parametrize('mutate,expected', [
    (lambda r: r['atoms'][1].update(status='unsupported', refs=[]), 'synthesis_premise_not_grounded'),
    (lambda r: r['atoms'][0]['inference']['comparisons'][2].update(subject='甲救助'), 'synthesis_relation_not_in_premise'),
    (lambda r: r['atoms'][0]['inference'].update(operator='causes'), 'synthesis_unchecked_relation'),
    (lambda r: r['atoms'][0]['inference']['comparisons'][2].update(predicate='不属于'), 'synthesis_condition_or_negation'),
])
def test_synthesis_negative_relations_not_just_entity_overlap(mutate, expected):
    fs, raw, sources, review = synthesis_fixture()
    mutate(review)
    result = apply_review(raw, review, sources, fs, decode)
    assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])
    assert next(d for d in result['decisions'] if d['aid'] == review['atoms'][0]['aid'])['drop_rule'] == expected


def test_same_entities_cannot_introduce_permission_or_automatic_entitlement():
    fs, raw, sources, review = synthesis_fixture('三者可以同时领取且一定获赔。')
    # Explicit contract ownership is assigned by the mock judge, not inferred
    # permission; the inference grammar must still reject this conclusion.
    result = apply_review(raw, review, sources, fs, decode)
    assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])


def test_different_issuer_or_basis_is_not_proof_of_different_institution():
    fs, raw, sources, review = synthesis_fixture()
    review['atoms'][0]['inference']['field'] = 'issuer'
    result = apply_review(raw, review, sources, fs, decode)
    assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])
    assert next(d for d in result['decisions'] if d['type']=='synthesis')['drop_rule'] == 'synthesis_comparison_not_identity_proof'


def test_version_scope_note_with_citation_survives_without_globalizing_gap():
    raw = {'answer': '该文件的现行有效性本次尚未核实[资料1]。'}
    result = typed(raw, [facet()], evidence('版本信息未核实。'), lambda a, i: {'status': 'limitation', 'type': 'scope_note', 'refs': []})
    assert result['accepted_atoms'] and '尚未核实' in result['answer']
    assert not result['publishable']


def test_unasked_local_gap_cannot_be_retained_under_method_facet():
    raw = {'answer': '本次尚未取得温州当地专用技术规范[资料1]。'}
    result = typed(raw, [facet(kind='method_steps')], evidence('检查网箱锚泊系统。'), lambda a, i: {'status': 'limitation', 'type': 'gap', 'gap_type': 'jurisdiction_rule_not_found', 'refs': []})
    assert not result['accepted_atoms']
    assert result['decisions'][0]['drop_rule'] == 'typed_grounding_unasked_gap'
    # A model relabelling the same missing local norm as generic missing
    # evidence cannot bypass the deterministic contract classifier.
    result = typed(raw, [facet(kind='method_steps')], evidence('检查网箱锚泊系统。'), lambda a, i: {'status':'limitation','type':'gap','gap_type':'evidence_not_found','refs':[]})
    assert not result['accepted_atoms']


def test_date_only_never_passes_minimum_delivery():
    atom = {'aid': 'a', 'type': 'direct_fact', 'text': '该方案自2024年4月30日起施行[资料1]。', 'facet_ids': ['f1']}
    rows = evaluate_delivery([facet()], [atom])
    assert rows[0]['status'] == 'missing' and not publishable([facet()], rows)
    rendered = render_with_gaps([facet()], [atom], evidence('该方案自2024年4月30日起施行。'))
    assert all(x in rendered for x in ('已取得：', '还缺：', '补齐路径：'))


def test_recovery_freezes_siblings_and_bounds_callbacks():
    frozen = {'aid': 'c1a1', 'type': 'direct_fact', 'text': '甲事项检查锚链和固定网箱[资料1]。', 'facet_ids': ['f1'], 'evidence_quotes': []}
    initial = {'accepted_atoms': [frozen], 'decisions': []}
    fs = [facet('f1', objects=['甲事项']), facet('f2', objects=['乙事项'])]
    calls = []
    def retrieve(failed, current):
        calls.append('search'); return [{'citation': 2, **evidence('乙事项清理池塘。')[0]}]
    def regenerate(failed, frozen_atoms, current, rejected):
        calls.append('rewrite'); return {'answer': '乙事项清理池塘[资料2]。'}
    def ground(raw, current, failed):
        calls.append('ground'); return {'accepted_atoms': [{'aid': 'c1a1', 'type': 'direct_fact', 'text': raw['answer'], 'facet_ids': ['f2'], 'evidence_quotes': []}]}
    result = recover_locally(initial, evidence('甲事项检查锚链和固定网箱。'), fs,
                             targeted_retrieve=retrieve, regenerate=regenerate, ground=ground, budget=lambda s: True)
    assert calls == ['search', 'rewrite', 'ground'] and result['accepted_atoms'][0] == frozen
    assert result['accepted_atoms'][1]['aid'].startswith('local1_')
    calls.clear()
    result = recover_locally(initial, [], fs, targeted_retrieve=retrieve, regenerate=regenerate, ground=ground)
    assert not calls and result['render_mode'] == 'with_gaps'


def test_mutating_a_frozen_sibling_is_rejected_without_loss():
    atom = {'aid': 'a', 'type': 'direct_fact', 'text': '甲事项已有事实[资料1]。', 'facet_ids': ['f1'], 'evidence_quotes': []}
    initial = {'accepted_atoms': [atom]}
    def mutate(failed, frozen, current, rejected):
        frozen[0]['text'] = '被修改'; return {'answer': '新稿'}
    result = recover_locally(initial, [], [facet('f1', objects=['甲事项']), facet('f2', objects=['乙事项'])],
                             regenerate=mutate, ground=lambda *a: {}, budget=lambda s: True)
    assert result['accepted_atoms'] == [atom]
    assert result['recovery_trace'][0]['error'] == 'frozen_sibling_mutated'


def test_recovery_namespaces_new_synthesis_premise_pointers_together():
    frozen = {'aid':'c1a1','type':'direct_fact','text':'甲事项已有事实[资料1]。','facet_ids':['f1'],'evidence_quotes':[]}
    new = [{'aid':'c1a1','type':'direct_fact','text':'乙事项事实甲[资料1]。','facet_ids':['f2'],'evidence_quotes':[]},
           {'aid':'c2a1','type':'synthesis','text':'乙事项结论[资料1]。','facet_ids':['f2'],'evidence_quotes':[],
            'premises':['c1a1'],'inference':{'comparisons':[{'premise_id':'c1a1'}]}}]
    result = recover_locally({'accepted_atoms':[frozen]}, [], [facet('f1'),facet('f2')],
                             regenerate=lambda *args:{'answer':'unused structural fixture'},
                             ground=lambda *args:{'accepted_atoms':new}, budget=lambda stage:True)
    result_new = result['accepted_atoms'][1:]
    assert result_new[1]['premises'] == [result_new[0]['aid']]
    assert result_new[1]['inference']['comparisons'][0]['premise_id'] == result_new[0]['aid']
    assert result['accepted_atoms'][0] == frozen


def test_review_rejects_missing_or_cross_atom_decisions():
    check = {'atoms': [{'aid': 'a'}, {'aid': 'b'}]}
    with pytest.raises(ValueError, match='incomplete_review'):
        review_atoms(check, lambda p: {'atoms': [{'aid': 'a'}, {'aid': 'a'}]})


def duplicate_review():
    raw = {'answer':'县级审核后发放救助资金[资料1]。\n县级审核后发放救助资金[资料1]。'}
    atoms = prepare_draft(raw, [facet()])['atoms']
    review = {'atoms':[{'aid':a['aid'], 'text':a['text'], 'facet_ids':['f1'], 'status':status,
                       'type':'direct_fact', 'refs':[{'citation':1,'passage_id':1}]} for a,status in zip(atoms, ['supported','unsupported'])]}
    return raw, review


def test_exact_duplicate_same_ref_conflict_is_audited_and_not_autoapproved():
    raw, review = duplicate_review()
    conflicts = find_judge_conflicts(raw, review, [facet()])
    assert len(conflicts) == 1 and conflicts[0]['code'] == 'judge_inconsistent'
    result = apply_review(raw, review, evidence('县级审核后发放救助资金。'), [facet()], decode)
    assert not result['accepted_atoms']
    assert all(d['drop_rule']=='judge_inconsistent' for d in result['decisions'])


@pytest.mark.parametrize('change', ['clause', 'source', 'facet'])
def test_different_clauses_or_attribution_are_not_judge_conflicts(change):
    raw, review = duplicate_review()
    if change == 'clause':
        raw['answer'] = raw['answer'].replace('县级审核后发放救助资金[资料1]。\n','县级审核申请材料[资料1]。\n')
        review['atoms'][0]['text'] = prepare_draft(raw, [facet()])['atoms'][0]['text']
    elif change == 'source':
        review['atoms'][1]['refs'][0]['passage_id'] = 2
    else:
        review['atoms'][1]['facet_ids'] = ['f2']
    assert not find_judge_conflicts(raw, review, [facet()])


def test_conflict_rereview_requires_budget_and_stops_after_one_attempt():
    raw, review = duplicate_review()
    calls = []
    def caller(check):
        calls.append(check); return review  # still inconsistent
    denied = resolve_judge_conflicts(raw, review, [], [facet()], caller)
    assert not calls and denied['unresolved']
    result = resolve_judge_conflicts(raw, review, [], [facet()], caller, budget=lambda stage:True)
    assert len(calls) == 1 and result['rechecked'] and result['unresolved']


def test_unseen_same_family_subjects_share_no_hardcoded_program_names():
    fs = [facet('f1', objects=['甲项目']), facet('f2', objects=['乙项目'])]
    raw = {'answer': '1. 甲项目：尚无完整依据。\n2. 乙项目：本句依据错误。\n先检查申请材料，然后完成审核并发放资金[资料1]。'}
    result = typed(raw, fs, evidence('先检查申请材料，然后完成审核并发放资金。'), lambda a, i: {'status': 'unsupported', 'refs': []} if i < 2 else {})
    assert result['accepted_atoms'][-1]['facet_ids'] == ['f2']
    assert '先检查申请材料' not in result['answer'].split('**乙项目如何办理**')[0]
