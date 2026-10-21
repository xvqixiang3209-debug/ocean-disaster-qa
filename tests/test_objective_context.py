from datetime import datetime
from types import SimpleNamespace
import pytest
from marine_kb.objective_context import product_context, product_question_plan
from marine_kb.conversation import ConversationStore

CONTEXT = {'dataset_id': 'ecmwf_202610192000_247997aeca80',
           'start': '2026-10-20T20:00:00+08:00', 'end': '2026-10-23T20:00:00+08:00', 'hours': 72}
NOW = datetime.fromisoformat('2026-10-20T23:30:00+08:00')
HISTORY = [{'question': '生成浙江72小时报告', 'objective_context': CONTEXT,
            'answer': '不可信历史答案：999米、另一个数据集、300小时'}]


def test_followup_inherits_input_slots_not_generated_facts():
    q = '同一批次和时段，北部、中部、南部三个参考分区中哪个浪高最大？请列出具体数值、位置和时刻。'
    plan = product_question_plan(q, HISTORY, now=NOW)
    assert plan['context_used'] and plan['normalizer'] == 'objective_input_slots'
    assert 'ecmwf_202610192000_247997aeca80' in plan['normalized_question']
    assert '2026年10月20日20时00分起未来72小时' in plan['normalized_question']
    assert '999米' not in plan['normalized_question']
    from forecast_runtime.objective_bridge import requested_window, objective_question
    start, hours = requested_window(plan['normalized_question'], NOW)
    assert start.isoformat() == CONTEXT['start'] and hours == 72
    assert objective_question(plan['normalized_question'])


@pytest.mark.parametrize('q', ['什么是海平面气压？', '这个预警等级是什么意思？', '同一时段福建的海浪呢？', '用中国机构路径查询相似台风'])
def test_new_topic_does_not_carry_zhejiang_context(q):
    plan = product_question_plan(q, HISTORY, now=NOW)
    assert not plan or not plan['context_used']
    if plan:
        assert plan['normalized_question'] == q


def test_explicit_date_duration_dataset_correction_wins():
    p = product_question_plan('同一批次改成明天8时起未来24小时，使用数据集ID ecmwf_202610200800_newbatch', HISTORY, now=NOW)
    assert '2026年10月21日08时00分起未来24小时' in p['normalized_question']
    assert 'ecmwf_202610200800_newbatch' in p['normalized_question']
    assert CONTEXT['dataset_id'] not in p['normalized_question']


def test_clock_only_correction_keeps_previous_day():
    p = product_question_plan('同一批次改成8时起未来48小时', HISTORY, now=NOW.replace(day=25))
    assert '2026年10月20日08时00分起未来48小时' in p['normalized_question']


@pytest.mark.parametrize('q', ['最大浪高是多少？', '最低气压出现在几点？', '北部和南部风速哪个大？', '图件在哪里下载？'])
def test_short_product_followups_keep_selected_window(q):
    plan = product_question_plan(q, HISTORY, now=NOW)
    assert plan['context_used']
    assert '2026年10月20日20时00分起未来72小时' in plan['normalized_question']


def test_no_stale_context_across_new_topic_and_no_client_history_override():
    assert product_question_plan('同一批次和时段哪个浪最大', HISTORY + [{'question': '相似台风有哪些'}], now=NOW) is None
    assert product_question_plan('同一批次和时段哪个浪最大', [{'answer': str(CONTEXT)}], now=NOW) is None
    assert product_question_plan('同一批次和时段哪个浪最大', None, now=NOW) is None


@pytest.mark.parametrize('patch', [{'dataset_id': '../../private'}, {'start': 'invalid'}, {'start':'2026-10-20'}, {'end':'2026-10-30T20:00:00+08:00'}])
def test_invalid_context_not_accepted(patch):
    assert product_context({**CONTEXT, **patch}) is None


def test_store_records_only_authoritative_input_slots():
    store = ConversationStore()
    _, conversation = store.get('owner')
    store.append(conversation, '报告', {'answer':'数值不作为上下文证据', 'answer_meta':{
        'used_mode':'business_data', 'objective_context': {**CONTEXT, 'secret':'not-copied'}}})
    assert conversation.turns[-1]['objective_context'] == CONTEXT
    store.append(conversation, '新主题', {'answer':'', 'answer_meta':{}})
    assert 'objective_context' not in conversation.turns[-1]


def test_product_route_does_not_call_model_to_rewrite_input_slots():
    from marine_kb.qa import MarineQuestionService
    qa = object.__new__(MarineQuestionService)
    qa.forecast_data = SimpleNamespace(objective=object())
    plan = qa.prepare_question('同一批次和时段北部哪个时刻浪最大？', history=HISTORY)
    assert plan['normalizer'] == 'objective_input_slots'
    assert '2026年10月20日20时00分' in plan['normalized_question']


@pytest.mark.parametrize('q', ['同一时段浙江有哪些海浪预警？','这些台风目前在哪，风速多少？','美国东海岸最低气压是多少？'])
def test_other_business_objects_never_become_nc_reports(q):
    p = product_question_plan(q, HISTORY, now=NOW)
    assert p is None or p['normalized_question'] == q
    assert p is None or not p['context_used']


@pytest.mark.parametrize('q,part', [
    ('同批次改成08:30未来48小时','2026年10月20日08时30分起未来48小时'),
    ('同批次改成明天，其他不变','2026年10月21日20时00分起未来72小时'),
    ('同一批次不是10月20日20时，是10月21日8时，查询24小时','2026年10月21日08时00分起未来24小时'),
    ('同批次改成2026-10-21 18时至10-22 6时','2026年10月21日18时00分起未来12小时'),
])
def test_corrections_preserve_and_replace_each_time_slot(q,part):
    p = product_question_plan(q, HISTORY, now=NOW)
    assert part in p['normalized_question']


def test_latest_batch_correction_drops_the_old_identifier():
    p = product_question_plan('换成最新批次，同一时段的浪高最大多少？', HISTORY, now=NOW)
    assert '数据集ID' not in p['normalized_question']
    assert CONTEXT['dataset_id'] not in p['normalized_question']


@pytest.mark.parametrize('q',['同一时段福建近海海浪图再生成一张','生成福建近海客观预报海浪图'])
def test_unsupported_region_explicitly_clarified_not_changed_to_zhejiang(q):
    p = product_question_plan(q, HISTORY, now=NOW)
    assert p['normalizer']=='objective_scope_boundary' and p.get('clarification')


def test_mixed_product_and_live_question_not_silently_dropped():
    p = product_question_plan('图件下载，另外查询台风最新位置和强度', HISTORY, now=NOW)
    assert p['normalizer']=='objective_mixed_scope' and p.get('clarification')


def test_specific_warning_standard_duration_not_stolen_by_concept_route():
    from marine_kb.warning_knowledge import matches
    assert not matches('海浪警报标准中的24小时是什么意思？')


MAP_CONTEXT = {'dataset_id': 'zjocean_l5_202610202000_9c8fcfe64cd6',
               'start': '2026-10-21T08:00:00+08:00', 'end': '2026-10-21T09:00:00+08:00',
               'hours': 1, 'view_kind': 'field', 'field': 'wave',
               'valid_time': '2026-10-21T08:00:00+08:00', 'bounds': [118, 26, 126, 33]}
MAP_HISTORY = [{'question': '显示浙江明天8时海浪图', 'objective_context': MAP_CONTEXT,
                'answer': '这段旧答案不是数据，不能继承其中的999米或时间。'}]


def test_map_context_preserves_only_validated_input_slots():
    assert product_context({**MAP_CONTEXT, 'secret': 'never copy'}) == MAP_CONTEXT
    store = ConversationStore(); _, conversation = store.get('owner')
    store.append(conversation, '地图', {'answer': '', 'answer_meta': {'objective_context': MAP_CONTEXT}})
    assert conversation.turns[-1]['objective_context'] == MAP_CONTEXT


@pytest.mark.parametrize('patch', [
    {'field': 'SWL'}, {'valid_time': '2026-10-21T08:00:00'}, {'valid_time': 'invalid'},
    {'valid_time': '2026-10-21T09:00:00+08:00'}, {'end': '2026-10-21T10:00:00+08:00'},
    {'bounds': [126, 26, 118, 33]}, {'bounds': [118, 33, 126, 26]},
    {'bounds': [float('nan'), 26, 126, 33]}, {'bounds': [118, 26, 181, 33]},
    {'bounds': [118, 26, 126]}, {'bounds': [False, 26, 126, 33]},
    {'bounds': None}, {'view_kind': 'unknown'},
])
def test_bad_map_context_is_rejected_not_reinterpreted_as_report(patch):
    assert product_context({**MAP_CONTEXT, **patch}) is None


@pytest.mark.parametrize('q,field,label', [
    ('同一时刻换成风场', 'wind', '海面风'),
    ('同一时刻换成气压图', 'pressure', '气压'),
    ('这个海浪图，换成风场', 'wind', '海面风'),
    ('同一时刻看看浪高', 'wave', '海浪'),
])
def test_map_followup_switches_field_preserving_dataset_time_bounds(q, field, label):
    p = product_question_plan(q, MAP_HISTORY, now=NOW)
    assert p['context_used'] and p['objective_view_kind'] == 'field'
    assert p['objective_field'] == field
    assert p['objective_bounds'] == MAP_CONTEXT['bounds']
    text = p['normalized_question']
    assert text.startswith('浙江客观预报地图查询，')
    assert '数据集ID ' + MAP_CONTEXT['dataset_id'] in text
    assert '地图要素：' + label in text
    assert '地图范围：118,26,126,33' in text
    assert '从2026年10月21日08时00分起未来1小时。本轮问题：' + q in text
    assert '999米' not in text


@pytest.mark.parametrize('q,expected', [
    ('明天8时呢', '2026年10月21日08时00分'),
    ('同一时刻改成后天09:30', '2026年10月22日09时30分'),
    ('同批次改成10月23日18时', '2026年10月23日18时00分'),
])
def test_map_time_corrections_keep_field_and_dataset(q, expected):
    p = product_question_plan(q, MAP_HISTORY, now=NOW)
    assert p['context_used'] and p['objective_field'] == 'wave'
    assert expected + '起未来1小时' in p['normalized_question']


@pytest.mark.parametrize('q', ['生成浙江未来72小时报告', '同一批次生成浙江海浪报告'])
def test_request_report_exits_map_mode_and_keeps_dataset(q):
    p = product_question_plan(q, MAP_HISTORY, now=NOW)
    assert p['context_used'] and '数据集ID ' + MAP_CONTEXT['dataset_id'] in p['normalized_question']
    assert '起未来72小时' in p['normalized_question']
    assert '地图查询' not in p['normalized_question']
    assert '地图要素' not in p['normalized_question']
    assert 'objective_view_kind' not in p


def test_latest_batch_keeps_map_time_field_bounds_but_drops_identifier():
    p = product_question_plan('换成最新批次，同一时刻看风场', MAP_HISTORY, now=NOW)
    assert p['context_used'] and p['objective_field'] == 'wind'
    assert '数据集ID' not in p['normalized_question']
    assert MAP_CONTEXT['dataset_id'] not in p['normalized_question']
    assert '地图范围：118,26,126,33' in p['normalized_question']
    assert '2026年10月21日08时00分起未来1小时' in p['normalized_question']


def test_multiple_new_fields_are_not_silently_replaced_by_previous_wave():
    p = product_question_plan('同一时刻风场和气压都给我看看', MAP_HISTORY, now=NOW)
    assert p['context_used'] and '地图要素：海浪' not in p['normalized_question']
    assert '风场和气压' in p['normalized_question'] and '起未来1小时' in p['normalized_question']


@pytest.mark.parametrize('q', ['查询相似台风', '这些台风现在在哪里？', '明天朱家尖能游泳吗？', '这个预警等级是什么意思？'])
def test_unrelated_business_does_not_inherit_map_context(q):
    p = product_question_plan(q, MAP_HISTORY, now=NOW)
    assert p is None or (not p['context_used'] and p['normalized_question'] == q)
