"""Cross-module regression of the reviewed product contracts, no external calls."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from forecast_runtime.objective_bridge import answer_objective, query_objective, objective_question
from forecast_runtime.objective_bridge import _field_query
from forecast_runtime.objective_fields import requested_fields
from forecast_runtime.objective_scope import resolve_scope
from marine_kb.objective_context import product_question_plan
from test_objective_l2_bridge import runtime, qa, FIRST, NOW, VALID, L5


@pytest.mark.parametrize('text,fields', [
    ('风浪', ['wave','wind']), ('风和浪', ['wave','wind']),
    ('风浪场', ['wave','wind']), ('浪场和风场', ['wave','wind']),
    ('浪场', ['wave']), ('涌浪', ['wave']), ('风场', ['wind']),
    ('风浪改成海面风', ['wind']), ('风场换成浪场', ['wave']),
])
def test_shared_vocabulary(text,fields):
    assert requested_fields(text)==fields


@pytest.mark.parametrize('field',['风浪','风和浪','浪场','海上风','涌浪','风场'])
def test_field_vocabulary_shared_with_natural_single_time_route(field):
    question='朱家尖明天08时'+field+'怎么样？'
    assert objective_question(question) and _field_query(question)


@pytest.mark.parametrize('place,state', [
    ('厦门','out_of_region'), ('福建','out_of_region'),
    ('东极岛','needs_scope'), ('洋山港','needs_scope'),
    ('珍珠岛','needs_scope'), ('清溪港','needs_scope'),
    ('碧水湾','needs_scope'), ('未知镇','needs_scope'),
])
def test_unsupported_scope_blocks_acquisition_selection_and_calculation(runtime,place,state):
    text=f'绘制{place}2026年10月21日08时海浪二维图'
    assert objective_question(text)
    result=query_objective(runtime,text)
    assert result['state']==state and result['map_action']['type']=='objective_clear'
    assert not result.get('scope') and not result.get('dataset_id')
    assert not runtime.objective.calls and not runtime.objective_sources.ensure_calls
    assert not runtime.objective_sources.select_calls and not runtime.objective_reports.calls


@pytest.mark.parametrize('place,parent', [('嵊泗','舟山'),('普陀','舟山'),('象山','宁波')])
def test_parent_fallback_remains_visible_everywhere(runtime,place,parent):
    result=query_objective(runtime,f'绘制{place}2026年10月21日08时海浪二维图')
    note=result['objective_scope_resolution']['notice']
    assert result['objective_scope_resolution']['state']=='parent_fallback'
    assert place in note and parent in note and '不代表' in note
    assert result['summary'].startswith(note) and result['evidence'].startswith(note)
    bounds=resolve_scope(parent)['bounds']
    assert result['bounds']==runtime.objective_sources.select_calls[0][1]['bounds']==bounds
    assert all(a['bounds']==bounds and a['scope_note']==note for a in result['map_actions'])


@pytest.mark.parametrize('follow', ['同一时刻风浪都显示','同一时刻换成风场和浪场',
                                   '同一时刻海浪和海面风都显示'])
def test_multifield_followups_keep_both_fields_time_and_place(runtime,follow):
    first=answer_objective(runtime,'绘制朱家尖2026年10月21日08时海浪二维图',qa(),{},False)
    history=[{'objective_context':first['answer_meta']['objective_context']}]
    plan=product_question_plan(follow,history,now=NOW)
    assert plan['objective_view_kind']=='field' and plan['objective_fields']==['wave','wind']
    result=answer_objective(runtime,plan['normalized_question'],qa(),{**plan,'current_turn_question':follow},False)
    actions=result['answer_meta']['map_actions']
    assert [a['field'] for a in actions]==['wave','wind']
    assert {a['valid_time'] for a in actions}=={VALID}
    assert all(a['bounds']==resolve_scope('朱家尖')['bounds'] for a in actions)
    assert all(a['time_role']=='shared' and a['group_size']==2 for a in actions)
    assert '6.125 m' in result['answer'] and '18.75 m/s' in result['answer']
    both=result['answer_meta']['objective_context']
    assert both['fields']==['wave','wind']
    next_plan=product_question_plan('同一时刻改成09时', [{'objective_context':both}],now=NOW)
    assert next_plan['objective_fields']==['wave','wind']
    narrow=product_question_plan('同一时刻换成海面风', [{'objective_context':both}],now=NOW)
    assert narrow['objective_fields']==['wind']


@pytest.mark.parametrize('follow,parent', [('换成嵊泗','舟山'),('换成普陀附近','舟山'),('改成象山','宁波')])
def test_explicit_new_place_cannot_keep_zhujiajian(runtime,follow,parent):
    first=answer_objective(runtime,'绘制朱家尖2026年10月21日08时风浪二维图',qa(),{},False)
    history=[{'objective_context':first['answer_meta']['objective_context']}]
    plan=product_question_plan(follow,history,now=NOW)
    assert plan['context_used']
    result=query_objective(runtime,plan['normalized_question'])
    assert result['bounds']==resolve_scope(parent)['bounds']
    assert result['objective_scope_resolution']['state']=='parent_fallback'
    assert '不代表' in result['summary']


def test_unknown_followup_clarifies_and_invalidates_prior_scope(runtime):
    first=answer_objective(runtime,'绘制朱家尖2026年10月21日08时风浪二维图',qa(),{},False)
    history=[{'objective_context':first['answer_meta']['objective_context']}]
    plan=product_question_plan('同一时刻换成珍珠岛',history,now=NOW)
    assert plan['normalizer']=='objective_scope_boundary' and plan['clarification']
    assert not plan['context_used']
    assert plan['objective_scope_resolution']['scope'] is None
    pending=plan['objective_pending_context']
    assert pending['pending_scope'] and 'bounds' not in pending and 'scope' not in pending
    no_place=product_question_plan('同一时刻换成海面风',[{'objective_context':pending}],now=NOW)
    assert no_place['clarification'] and no_place['objective_scope_resolution']['scope'] is None
    corrected=product_question_plan('那换成舟山',[{'objective_context':pending}],now=NOW)
    assert corrected['context_used'] and '朱家尖' not in corrected['normalized_question']
    recovered=query_objective(runtime,corrected['normalized_question'])
    assert recovered['scope']['id']=='zhoushan'
    assert recovered['valid_time']==VALID and recovered['requested_fields']==['wave','wind']


def test_negative_place_correction_is_a_product_followup(runtime):
    first=answer_objective(runtime,'绘制嵊泗2026年10月21日08时风浪二维图',qa(),{},False)
    plan=product_question_plan('不是嵊泗，是舟山',[{'objective_context':first['answer_meta']['objective_context']}],now=NOW)
    assert plan['context_used']
    result=query_objective(runtime,plan['normalized_question'])
    assert result['scope']['id']=='zhoushan' and result['objective_scope_resolution']['state']=='resolved'


def test_report_actions_each_use_own_peak_not_first_field_time(runtime):
    original=runtime.objective_reports.report
    def different_peaks(**kwargs):
        result=original(**kwargs)
        result['objective_result']['fields']['wind']['maximum']['time']=(FIRST+timedelta(hours=9)).isoformat()
        return result
    runtime.objective_reports.report=different_peaks
    result=query_objective(runtime,'从2026年10月21日08时起宁波未来12小时风浪简报')
    actions=result['map_actions']
    assert [a['field'] for a in actions]==['wave','wind']
    assert actions[0]['valid_time']==(FIRST+timedelta(hours=6)).isoformat()
    assert actions[1]['valid_time']==(FIRST+timedelta(hours=9)).isoformat()
    assert all(a['time_role']=='peak_of_this_field' for a in actions)
    assert all(a['window_start']==FIRST.isoformat() and a['window_hours']==12 for a in actions)
    assert result['map_action']==actions[0]
    maximum=query_objective(runtime,'从2026年10月21日08时起宁波未来12小时风浪逐格最大值图')
    assert len(maximum['map_actions'])==2
    for a in maximum['map_actions']:
        assert a['type']=='objective_maximum' and a['hours']==12
        assert a['start']==FIRST.isoformat() and 'valid_time' not in a
        assert a['time_role']=='shared'


def test_default_scope_notice_and_unsupported_field_button(runtime):
    result=query_objective(runtime,'2026年10月21日08时风浪和气压二维图')
    assert result['summary'].startswith('未指定地点')
    assert [a['field'] for a in result['map_actions']]==['wave','wind','pressure']
    assert result['map_actions'][-1]['field_state']=='not_provided'
    assert all(a['dataset_id']==L5 for a in result['map_actions'])


@pytest.mark.parametrize('policy,text,called', [
    (None,'宁波未来12小时风浪简报',False),
    ('off','生成并解读宁波未来12小时风浪报告',False),
    ('on_request','生成并解读宁波未来12小时风浪报告',True),
    ('always','宁波未来12小时风浪简报',True),
    ('invalid','宁波未来12小时风浪简报',False),
])
def test_report_interpretation_policy_is_explicit_and_never_needed_for_program_data(runtime,monkeypatch,policy,text,called):
    if policy is None:monkeypatch.delenv('MARINE_OBJECTIVE_INTERPRETATION',raising=False)
    else:monkeypatch.setenv('MARINE_OBJECTIVE_INTERPRETATION',policy)
    calls=[]
    model=SimpleNamespace(external_enabled=True,model_caller=object(),model_name='mock',crag_enabled=False,
        _public_sources=lambda q,hits:hits,
        _organized_answer=lambda *args:(calls.append(args) or {'answer':'有据解读[资料1]',
            'answer_meta':{'used_mode':'llm','answerability':'full'}}))
    answer=answer_objective(runtime,'从2026年10月21日08时起'+text,model,{},False)
    assert bool(calls)==called
    assert answer['answer_meta']['objective_delivery_verified']
    assert '6.125' in answer['answer']
    assert ('资料解读' in answer['answer'])==called
