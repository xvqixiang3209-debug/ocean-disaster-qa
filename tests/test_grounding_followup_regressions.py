"""Follow-up P2/P3/P5 regressions; no model calls or live retrieval.

Tests marked recorded inspect exact private P1-P6 recording inputs. Synthetic
typed proofs exercise validators, not a claim of new end-to-end model quality.
"""
import json
from pathlib import Path

import pytest

from marine_kb.quantities import quantities, validate_quantity_units
from marine_kb.relation_guard import check, health_authority
from marine_kb.typed_grounding import apply_review, prepare_draft


def facet(kind='explanation', objects=()):
    return {'fid': 'f1', 'id': 1, 'question': '蚊虫特别多，会不会传染病？' if kind == 'risk_relation' else '这些事项有什么区别？',
            'kind': kind, 'objects': list(objects), 'required_outputs': ['explanation'],
            'allowed_gaps': ['evidence_not_found']}


def source(text, citation=1, **extra):
    return {'citation': citation, 'text': text, 'passages': [{'id': 1, 'text': text}], **extra}


def decode(value, evidence):
    from marine_kb.qa import _decode_model_answer
    return _decode_model_answer(json.dumps(value, ensure_ascii=False), evidence)


def test_v2_health_identity_is_canonical_and_preserves_host_boundary():
    assert health_authority({'doc_card': {'url': 'https://www.chinacdc.cn/a'}})
    assert health_authority({'doc_card': {'url': 'https://www.nhc.gov.cn/a'}})
    assert health_authority({'doc_card': {'url': 'https://wjw.zj.gov.cn/a'}})
    assert not health_authority({'doc_card': {'url': 'https://chinacdc.cn.evil.example/a'}})
    assert not health_authority({'doc_card': {'url': 'https://evil.example/a'},
                                 'metadata': {'url': 'https://www.chinacdc.cn/a'}})
    # A plain government site requires a declared health-authority origin.
    assert not health_authority({'doc_card': {'url': 'https://www.zj.gov.cn/a'}})
    assert health_authority({'doc_card': {'url': 'https://www.zj.gov.cn/a', 'issuer': '浙江省卫生健康委员会'}})


def test_source_fix_does_not_certify_wrong_mosquito_relationship():
    fs = [facet('risk_relation')]
    official = [{'doc_card': {'url': 'https://www.chinacdc.cn/a'}}]
    assert not check('蚊虫可能传播登革热[资料1]。', fs, official)
    issues = check('蚊虫可能传播钩体病[资料1]。', fs, official)
    assert any(i['code'] == 'relation_vector_mismatch' for i in issues)
    assert not any(i['code'] == 'relation_health_source_required' for i in issues)
    assert any(i['code'] == 'relation_mixed_vector_context'
               for i in check('鼠类和蚊虫可能传播出血热、登革热[资料1]。', fs, official))


@pytest.mark.parametrize('text', ['鼠类可传播流行性出血热，蚊虫可传播登革热[资料1]。',
                                '蚊虫可传播登革热，鼠类可传播流行性出血热[资料1]。',
                                '钩体病并非蚊媒病；蚊虫可能传播登革热[资料1]。'])
def test_different_explicit_subjects_in_compound_sentence_are_not_conflated(text):
    issues = check(text, [facet('risk_relation')], [{'doc_card': {'url': 'https://www.chinacdc.cn/a'}}])
    assert not any(i['code'] == 'relation_vector_mismatch' for i in issues)


def test_correction_in_one_clause_cannot_hide_wrong_relation_in_next_clause():
    issues = check('钩体病不是蚊媒病，蚊虫传播流行性出血热[资料1]。',
                   [facet('risk_relation')], [{'doc_card': {'url': 'https://www.chinacdc.cn/a'}}])
    assert any(i['code'] == 'relation_vector_mismatch' for i in issues)


@pytest.mark.parametrize('formula', ['“自主申请、不建不补；逐户统计、发放到户”',
                                    '「自主申请。逐户统计；发放到户」',
                                    '《办事指南（试行；宁波版）》'])
def test_policy_formula_remains_one_exact_atom(formula):
    text = '甲事项按'+formula+'办理[资料1]；乙事项先审核后发放[资料2]。'
    fs = [facet(objects=['甲事项', '乙事项'])]
    atoms = prepare_draft({'answer': text}, fs)['atoms']
    assert len(atoms) == 2
    assert formula in atoms[0]['text']
    assert all(text[a['start']:a['end']] == a['text'] for a in atoms)


def test_local_procedure_retains_material_source_scope_without_claiming_currentness():
    text = '保险机构按照合同支付赔款[资料1]。'
    fs = [facet()]
    atom = prepare_draft({'answer': text}, fs)['atoms'][0]
    row = {'aid': atom['aid'], 'text': atom['text'], 'type': 'direct_fact', 'status': 'supported',
           'facet_ids': ['f1'], 'refs': [{'citation': 1, 'passage_id': 1}]}
    result = apply_review({'answer': text}, {'atoms': [row]},
                          [source('保险机构按照合同支付赔款。', doc_card={'jurisdiction': '宁波'})], fs, decode)
    kept = result['accepted_atoms'][0]
    assert kept['text'] == text
    assert '按所引宁波资料' in kept['display_text']
    assert '现行' not in kept['display_text'] and '全国' not in kept['display_text']


def test_section_locator_is_not_wind_speed_but_real_knots_still_checked():
    assert not quantities('依据文件第6.3节、第6—7节办理。')
    assert len(quantities('风速6.3节，阵风7节。')) == 2
    validate_quantity_units('依据文件第6.3节办理[资料1]。', [source('文件6.3规定办理流程。')])
    with pytest.raises(ValueError):
        validate_quantity_units('风速6.3节[资料1]。', [source('文件6.3规定办理流程。')])


def test_typed_short_navigation_passes_canonical_ids_hashes_and_full_body():
    from marine_kb.evidence_units import evidence_view
    sources = evidence_view([{'citation': 1, 'title': '浙江天气网', 'url': 'https://www.zj.gov.cn/weather.html',
                              'text': '天气预警\n\n台风发布\n\n浙江省气象灾害预警发布平台'}])
    text = '查询浙江气象台台风预警，可进入浙江天气网“天气预警”栏目和“台风发布”入口[资料1]。'
    fs = [facet()]
    atoms = prepare_draft({'answer': text}, fs)['atoms']
    assert len(atoms) == 1
    review = {'atoms': [{'aid': atoms[0]['aid'], 'text': text, 'status': 'supported', 'type': 'direct_fact',
                         'facet_ids': ['f1'], 'refs': [{'citation': 1, 'passage_id': 1}, {'citation': 1, 'passage_id': 2}]}]}
    result = apply_review({'answer': text}, review, sources, fs, decode)
    assert len(result['accepted_atoms']) == 1
    assert '天气预警' in result['answer'] and '台风发布' in result['answer']


@pytest.mark.parametrize('damage', ['uncited_number', 'body_hash', 'passage_offset', 'unquoted_fact'])
def test_typed_short_navigation_does_not_borrow_other_numbers_or_certify_facts(damage):
    from marine_kb.evidence_units import evidence_view
    sources = evidence_view([{'citation': 1, 'title': '浙江天气网', 'url': 'https://www.zj.gov.cn/weather.html',
                              'text': '天气预警\n\n台风发布\n\n其它地点历史最大波高10米。'}])
    text = '进入浙江天气网“天气预警”栏目查看“台风发布”入口[资料1]。'
    if damage == 'uncited_number':
        text = '进入浙江天气网“天气预警”栏目，当前波高10米[资料1]。'
    elif damage == 'body_hash':
        sources[0]['body_sha256'] = '0'*64
    elif damage == 'passage_offset':
        sources[0]['passages'][1]['start'] += 1
    else:
        text = '天气预警已经解除，可以出海[资料1]。'
    fs = [facet()]
    atom = prepare_draft({'answer': text}, fs)['atoms'][0]
    review = {'atoms': [{'aid': atom['aid'], 'text': text, 'status': 'supported', 'type': 'direct_fact',
                         'facet_ids': ['f1'], 'refs': [{'citation': 1, 'passage_id': 1}]}]}
    result = apply_review({'answer': text}, review, sources, fs, decode)
    assert not result['accepted_atoms']


def synthesis_result(texts, conclusion, slots, *, field='category', operator='not_equivalent', kind='distinction'):
    fs = [facet(kind)]
    raw = {'answer': '\n'.join(t[:-1]+f'[资料{i}]。' for i, t in enumerate(texts, 1))+'\n'+conclusion}
    atoms = prepare_draft(raw, fs)['atoms']
    rows = [{'aid': a['aid'], 'text': a['text'], 'type': 'direct_fact', 'status': 'supported',
             'facet_ids': ['f1'], 'refs': [{'citation': i, 'passage_id': 1}]} for i, a in enumerate(atoms[:-1], 1)]
    assert len(rows) == len(texts)
    proof = {'aid': atoms[-1]['aid'], 'text': atoms[-1]['text'], 'type': 'synthesis', 'status': 'synthesis',
             'facet_ids': ['f1'], 'premises': [r['aid'] for r in rows],
             'inference': {'operator': operator, 'field': field,
                           'comparisons': [{'premise_id': row['aid'], 'subject': s, 'predicate': p, 'value': v}
                                           for row, (s, p, v) in zip(rows, slots)]}}
    return apply_review(raw, {'atoms': rows+[proof]}, [source(t, i) for i, t in enumerate(texts, 1)], fs, decode)


def test_property_comparison_accepts_different_exact_predicate_wording():
    result = synthesis_result(['甲事项属于政府救助。', '乙事项是保险理赔。'], '二者不是同一制度。',
                              [('甲事项', '属于', '政府救助'), ('乙事项', '是', '保险理赔')])
    assert any(a['type'] == 'synthesis' for a in result['accepted_atoms'])
    result = synthesis_result(['甲项目用于灾后生活保障。', '乙项目旨在修复受损设施。'], '二者用途不同。',
                              [('甲项目', '用于', '灾后生活保障'), ('乙项目', '旨在', '修复受损设施')],
                              field='purpose', operator='different')
    assert any(a['type'] == 'synthesis' for a in result['accepted_atoms'])


@pytest.mark.parametrize('conclusion', ['二者不是同一制度。', '二者可以同时领取。', '二者一定获赔。'])
def test_property_difference_never_proves_identity_or_permission(conclusion):
    result = synthesis_result(['甲项目用于灾后生活保障。', '乙项目旨在修复受损设施。'], conclusion,
                              [('甲项目', '用于', '灾后生活保障'), ('乙项目', '旨在', '修复受损设施')],
                              field='purpose', operator='different')
    assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])


def test_relation_tokens_cannot_trade_condition_negation_or_actor():
    for text, slots in [
        ('甲事项不属于政府救助。', [('甲事项', '属于', '政府救助'), ('乙事项', '是', '保险理赔')]),
        ('如果材料齐全，甲事项属于政府救助。', [('甲事项', '属于', '政府救助'), ('乙事项', '是', '保险理赔')]),
    ]:
        result = synthesis_result([text, '乙事项是保险理赔。'], '二者不是同一制度。', slots)
        assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])
    result = synthesis_result(['甲部门批准救助。', '乙部门批准赔付。'], '二者不是一回事。',
                              [('甲部门', '批准', '救助'), ('乙部门', '批准', '赔付')])
    assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])


def test_bounded_risk_inference_is_not_a_diagnosis_or_absence_claim():
    conclusion = '这些资料提示存在传播风险，不等于当地已经发生疫情。'
    slots = [('蚊虫', '可能传播', '登革热')]
    result = synthesis_result(['蚊虫可能传播登革热。'], conclusion, slots,
                              field='occurrence', operator='risk_not_occurrence', kind='risk_relation')
    assert any(a['type'] == 'synthesis' for a in result['accepted_atoms'])
    for bad in ('蚊虫不会传播登革热。', '当地没有疫情。', '这些资料提示存在传播风险，不等于当地已经发生疫情，可以放心游泳。',
                '无需防蚊，不等于当地已经发生疫情。'):
        result = synthesis_result(['蚊虫可能传播登革热。'], bad, slots,
                                  field='occurrence', operator='risk_not_occurrence', kind='risk_relation')
        assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])


def test_actual_observation_cannot_be_downgraded_into_merely_risk():
    result = synthesis_result(['蚊虫已经传播登革热。'], '这些资料提示存在传播风险，不等于当地已经发生疫情。',
                              [('蚊虫', '已经传播', '登革热')], field='occurrence',
                              operator='risk_not_occurrence', kind='risk_relation')
    assert not any(a['type'] == 'synthesis' for a in result['accepted_atoms'])


RECORDINGS = Path(__file__).resolve().parents[1]/'tmp/replay/p1_p6_20261020_092625_18d96920'
CASES = ['M1', 'M2', 'M3', 'C1', 'C2', 'C3', 'A1', 'A2', 'A3', 'G1', 'G2', 'G3'] + [f'{q}_extra_{n}' for q in ('G3', 'A2', 'C3', 'M1') for n in (1, 2, 3)]


def recorded_model_requests(case):
    from marine_kb.replay import _unwire
    path = RECORDINGS/(case+'.cassette.jsonl')
    if not path.exists():
        pytest.skip('private live recordings omitted in a code-only checkout')
    for line in path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row.get('kind') != 'model' or row.get('record_type') != 'interaction':
            continue
        args = _unwire(row['request']).get('args', [])
        if len(args) < 3:
            continue
        try:
            yield json.loads(args[2])
        except (ValueError, TypeError):
            continue


@pytest.mark.parametrize('case', CASES)
def test_recorded_24_drafts_keep_exact_spans_with_quote_aware_splitting(case):
    count = 0
    for payload in recorded_model_requests(case):
        if not payload.get('atoms'):
            continue
        text = '\n'.join(a['text'] for a in payload['atoms'])
        fs = payload.get('facet_contract') or [facet()]
        parsed = prepare_draft({'answer': text}, fs)
        assert all(parsed['draft']['answer'][a['start']:a['end']] == a['text'] for a in parsed['atoms'])
        count += 1
    assert count > 0


def test_recorded_cdc_relationship_no_longer_loses_canonical_health_source():
    count = 0
    for case in ['M1']+[f'M1_extra_{n}' for n in (1, 2, 3)]:
        for payload in recorded_model_requests(case):
            evidence = payload.get('evidence') or []
            for atom in payload.get('atoms') or []:
                if '蚊媒传染病' not in atom['text']:
                    continue
                for item in evidence:
                    if not (item.get('doc_card') or {}).get('url', '').find('chinacdc.cn') >= 0:
                        continue
                    before = {**item, 'doc_card': {}}
                    assert health_authority(item)
                    assert not health_authority(before)
                    count += 1
    assert count >= 4


def test_recheck_tool_never_reuses_judgement_for_changed_atom_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from marine_kb import typed_grounding, relation_guard, quantities as quantity_module
    from marine_kb.replay import _wire
    from tools.recheck_grounding_recordings import replay_case
    fs = [facet()]
    raw = {'answer': '甲事项先审核，随后发放[资料1]。'}
    new_atoms = prepare_draft(raw, fs)['atoms']
    # An old parser handed the judge one different span. Its judgement is not
    # a model opinion on today's atom, even if the draft meaning is similar.
    old_atoms = [{**new_atoms[0], 'end': new_atoms[0]['end']-1, 'text': new_atoms[0]['text'][:-1]}]
    review = {'atoms': [{'aid': old_atoms[0]['aid'], 'text': old_atoms[0]['text'],
                          'status': 'supported', 'type': 'direct_fact', 'facet_ids': ['f1'],
                          'refs': [{'citation': 1, 'passage_id': 1}]}]}
    def interaction(payload, value):
        return {'record_type': 'interaction', 'kind': 'model', 'outcome': 'return',
                'request': _wire({'args': [None, None, json.dumps(payload, ensure_ascii=False)]}),
                'response': _wire((json.dumps(value, ensure_ascii=False), {}))}
    rows = [interaction({'question': '如何办理'}, raw),
            interaction({'atoms': old_atoms, 'facet_contract': fs, 'evidence': [source('甲事项先审核，随后发放。')]}, review)]
    path = tmp_path/'variant.cassette.jsonl'
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf-8')
    old = SimpleNamespace(prepare_draft=lambda raw, facets: {'atoms': old_atoms})
    def forbidden(*args, **kwargs):
        raise AssertionError('a changed atom must not receive a fabricated/reused judge')
    monkeypatch.setattr(typed_grounding, 'apply_review', forbidden)
    result = replay_case(path, old, relation_guard, quantity_module)
    assert result['counts']['needs_new_judge'] == 1
    assert result['counts']['replayed_judgements'] == 0
    assert result['reviews'][0]['reason'] == 'parser_changed_exact_atom_boundaries'


def test_recheck_restores_only_exact_recorded_full_body_and_never_joins_passages():
    import hashlib
    from marine_kb.replay import _wire
    from tools.recheck_grounding_recordings import recorded_body_bank, restore_evidence_bodies
    body = '天气预警\n\n台风发布\n\n官方菜单'
    digest = hashlib.sha256(body.encode()).hexdigest()
    wire = {'record_type': 'interaction', 'kind': 'public_retrieval', 'outcome': 'return',
            'response': _wire(([{'source_text': body}], {'read_status': 'success'}))}
    bank = recorded_body_bank([wire])
    rows, restored, missing = restore_evidence_bodies([{'body_sha256': digest, 'passages': [{'text': '错误摘段'}]}], bank)
    assert rows[0]['text'] == body and restored == [digest] and not missing
    rows, restored, missing = restore_evidence_bodies([{'body_sha256': '0'*64, 'passages': [{'text': body}]}], bank)
    assert 'text' not in rows[0] and not restored and missing == ['0'*64]
