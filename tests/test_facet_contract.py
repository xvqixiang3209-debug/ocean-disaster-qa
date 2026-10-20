import json
from datetime import datetime
from pathlib import Path

import pytest

from marine_kb.facet_contract import build_contract, FACET_KINDS
from marine_kb.conversation import TurnState, resolve_turn
from marine_kb.evidence_applicability import CN

RECORDS = json.loads((Path(__file__).parent / 'fixtures/p0_scope_cases_24.json').read_text('utf8'))['cases']


@pytest.mark.parametrize('case', RECORDS, ids=[row['case_id'] for row in RECORDS])
def test_every_recorded_question_has_serializable_contract(case):
    rows = build_contract(case['question'])
    assert rows
    assert all(row['kind'] in FACET_KINDS for row in rows)
    assert all(row['id'] > 0 and row['fid'] == f"f{row['id']}" for row in rows)
    assert len({row['id'] for row in rows}) == len(rows)
    assert json.loads(json.dumps(rows, ensure_ascii=False)) == rows


def test_planner_legacy_ids_and_sibling_place_binding_are_preserved():
    parts = [{'id': 5, 'question': '浙江由谁决定终止响应？'},
             {'id': 8, 'question': '福建由谁决定终止响应？'}]
    rows = build_contract('浙江与福建由谁决定终止响应？', parts)
    assert [row['id'] for row in rows] == [5, 8]
    assert [row['places'] for row in rows] == [['浙江'], ['福建']]
    assert rows[0]['key'] == build_contract('浙江与福建由谁决定终止响应？', parts)[0]['key']


def test_distinction_and_per_object_basis_are_separate():
    q = '过渡期生活救助、冬春救助和巨灾保险赔付是不是一回事？分别依据什么办理？'
    parts = [{'id': 1, 'question': '三项是不是一回事？'},
             {'id': 2, 'question': '过渡期生活救助依据什么办理？'},
             {'id': 3, 'question': '冬春救助依据什么办理？'},
             {'id': 4, 'question': '巨灾保险赔付依据什么办理？'}]
    rows = build_contract(q, parts)
    assert [row['kind'] for row in rows] == ['distinction', 'basis_document', 'basis_document', 'basis_document']
    assert rows[2]['objects'] == ['冬春救助']
    assert 'verdict' in rows[0]['required_outputs']


@pytest.mark.parametrize('q,kind,role', [
    ('舟山深水网箱台风前锚泊系统怎么检查？', 'method_steps', 'context'),
    ('温岭对虾池塘进排水闸要检查什么？', 'method_steps', 'context'),
    ('宁波居民怎么防蚊？', 'method_steps', 'context'),
    ('宁波蚊虫变多是否就说明已经暴发登革热？', 'risk_relation', 'context'),
    ('浙江正式台风预警在哪查？', 'channel', 'jurisdiction_required'),
    ('2026年10月20日朱家尖浴场开放吗？', 'status_at_time', 'jurisdiction_required'),
    ('宁波巨灾保险今年赔了多少？', 'numeric_value', 'jurisdiction_required'),
    ('宁波受灾后能申请什么政府救助？', 'eligibility', 'jurisdiction_required'),
    ('温州当地现行网箱加固标准怎么执行？', 'method_steps', 'jurisdiction_required'),
])
def test_scope_rules_generalize(q, kind, role):
    row = build_contract(q)[0]
    assert row['kind'] == kind
    assert row['place_role'] == role


def test_risk_relation_does_not_allow_unasked_local_outbreak_gap():
    row = build_contract('宁波蚊子多是否就意味着登革热已经暴发？')[0]
    assert row['allowed_gaps'] == ['evidence_not_found']


@pytest.mark.parametrize('new_place', ['舟山', '温州', '福建'])
def test_place_followup_replaces_parent_and_keeps_objects(new_place):
    prev = TurnState(storm_sid='202630', places=('宁波',), objects=('潮位',))
    state = resolve_turn(f'那{new_place}呢？', prev)
    assert state.places == (new_place,)
    assert state.storm_sid == '202630'
    assert state.objects == ('潮位',)
    assert 'places' not in state.inherited_fields
    assert state.fingerprint() != prev.fingerprint()


@pytest.mark.parametrize('q,expected', [('不是宁波，是舟山', '舟山'), ('地点改成温州', '温州'), ('换成福建呢', '福建')])
def test_explicit_place_correction_drops_negated_place(q, expected):
    assert resolve_turn(q, TurnState(places=('宁波',))).places == (expected,)


@pytest.mark.parametrize('day', [20, 21, 22])
def test_date_replacement_keeps_entity_but_changes_time(day):
    prev = TurnState(storm_sid='202630', places=('舟山',), time={'start': '2026-10-18T00:00:00+08:00'})
    state = resolve_turn(f'日期改成10月{day}日', prev, now=datetime(2026, 10, 20, tzinfo=CN))
    assert state.places == ('舟山',)
    assert state.storm_sid == '202630'
    assert state.time['start'].startswith(f'2026-10-{day}')


@pytest.mark.parametrize('code', ['202629', '202628', '202627'])
def test_storm_correction_clears_old_time(code):
    prev = TurnState(storm_sid='202630', time={'start': '2026-10-18'}, places=('浙江',))
    state = resolve_turn(f'不是202630，是{code}', prev)
    assert state.storm_sid == code
    assert state.time is None
    assert state.places == ('浙江',)


def test_new_topic_never_inherits_previous_answers_or_entities():
    prev = TurnState(storm_sid='202630', places=('宁波',), objects=('潮位',))
    state = resolve_turn('什么是有效波高？', prev)
    assert state.storm_sid is None and not state.places and not state.objects
    assert TurnState.from_dict(state.as_dict()) == state


@pytest.mark.parametrize('question,old,new', [
    ('那舟山呢？', '宁波', '舟山'),
    ('不是宁波，是温州', '宁波', '温州'),
    ('地点换成福建', '宁波', '福建'),
])
def test_prepare_question_uses_resolved_place_without_model(monkeypatch, question, old, new):
    from marine_kb.qa import MarineQuestionService
    from test_marine_qa import FakeStore
    monkeypatch.setenv('MARINE_FACET_CONTRACT', 'true')
    qa = MarineQuestionService(FakeStore(), external_enabled=False)
    plan = qa.prepare_question(question, history=[{'question': f'{old}2026年10月18日海浪多大？'}])
    assert new in plan['normalized_question']
    assert old not in plan['normalized_question']
    assert plan['turn_state']['places'] == [new]
    assert plan['subquestions'] == plan['facets'] == plan['facet_contract']
    assert all(isinstance(row['id'], int) for row in plan['facets'])


@pytest.mark.parametrize('day', [20, 21, 22])
def test_prepare_question_replaces_date_without_model(monkeypatch, day):
    from marine_kb.qa import MarineQuestionService
    from test_marine_qa import FakeStore
    monkeypatch.setenv('MARINE_FACET_CONTRACT', 'true')
    qa = MarineQuestionService(FakeStore(), external_enabled=False)
    plan = qa.prepare_question(f'日期改成10月{day}日', history=[{'question': '舟山2026年10月18日海浪多大？'}])
    assert plan['turn_state']['time']['start'].startswith(f'2026-10-{day}')
    assert '18日' not in plan['normalized_question']
    assert '舟山' in plan['normalized_question']


@pytest.mark.parametrize('code', ['202629', '202628', '202627'])
def test_prepare_question_replaces_storm_without_model(monkeypatch, code):
    from marine_kb.qa import MarineQuestionService
    from test_marine_qa import FakeStore
    monkeypatch.setenv('MARINE_FACET_CONTRACT', 'true')
    qa = MarineQuestionService(FakeStore(), external_enabled=False)
    plan = qa.prepare_question(f'不是202630，是{code}', history=[{'question': '浙江2026年10月18日台风202630增水多大？'}])
    assert code in plan['normalized_question'] and '202630' not in plan['normalized_question']
    assert plan['turn_state']['time'] is None
    assert '18日' not in plan['normalized_question']


def test_append_stores_only_input_state_when_enabled(monkeypatch):
    from marine_kb.conversation import ConversationStore
    store = ConversationStore()
    _, item = store.get('owner')
    monkeypatch.delenv('MARINE_FACET_CONTRACT', raising=False)
    store.append(item, '宁波潮位多少？', {'answer': '未核实的旧答案'})
    assert 'turn_state' not in item.turns[-1]
    monkeypatch.setenv('MARINE_FACET_CONTRACT', 'true')
    store.append(item, '舟山风速多少？', {'answer': '风速999米每秒'})
    assert item.turn_state['places'] == ['舟山']
    assert '999' not in json.dumps(item.turn_state)
