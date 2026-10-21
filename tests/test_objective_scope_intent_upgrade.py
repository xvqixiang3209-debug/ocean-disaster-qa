"""Scope, intent and conversation input contracts; no network or model calls."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from forecast_runtime.objective_bridge import objective_question, requested_window, query_objective, _thresholds, _maximum_query
from forecast_runtime.objective_scope import resolve_scope, available_scopes, ZHEJIANG_BOUNDS
from marine_kb.objective_context import product_context, product_question_plan

NOW = datetime.fromisoformat('2026-10-21T10:35:00+08:00')


@pytest.mark.parametrize('question', [
    '给我一份浙江未来三天风浪分析', '明天朱家尖海况简报',
    '舟山未来两天风浪怎么样？', '做一份宁波近海今后三天的海浪过程分析',
    '看看嘉兴明天8时海面风分布图', '生成台州和温州未来72小时海浪报告',
])
def test_natural_product_intents(question):
    assert objective_question(question)


@pytest.mark.parametrize('question', [
    '什么是风浪分析？', '浙江海浪预警是什么？', '明天朱家尖能游泳吗？',
    '舟山未来两天有哪些海浪预警？', '宁波海面风速与阵风有什么区别？',
    '如何制作浙江海况简报？', '浙江如何计算波高？', '相似台风有哪些？',
])
def test_concepts_and_other_business_not_stolen(question):
    assert not objective_question(question)


@pytest.mark.parametrize('place,identifier', [('浙江','zhejiang'), ('舟山','zhoushan'),
    ('朱家尖','zhujiajian'), ('宁波','ningbo'), ('台州','taizhou'), ('温州','wenzhou'), ('嘉兴','jiaxing')])
def test_place_changes_real_calculation_box(place,identifier):
    scope=resolve_scope(f'给我{place}未来三天风浪分析')
    assert scope['id']==identifier and scope['kind']=='reference_box'
    assert all('参考统计框' in row['name'] for row in scope['regions'])
    if place!='浙江':assert scope['bounds']!=list(ZHEJIANG_BOUNDS)
    assert scope in available_scopes()


def test_leaf_location_and_corrections_win_over_background_city():
    scope=resolve_scope('浙江省舟山市朱家尖明天海况简报')
    assert scope['id']=='zhujiajian'
    assert '无海点' in ''.join(scope['limitations'])
    assert resolve_scope('不是舟山，改成温州风浪报告')['id']=='wenzhou'


def test_multiple_cities_keep_separate_boxes_not_province_regions():
    scope=resolve_scope('宁波和舟山未来72小时风浪对比分析')
    assert {r['id'] for r in scope['regions']}=={'ningbo','zhoushan'}
    assert scope['bounds']==[121.3,28.85,123.15,31.1]


def test_explicit_map_bounds_win_but_are_validated():
    scope=resolve_scope('朱家尖，地图范围：122.1,29.8,122.9,30.2，明天海浪图')
    assert scope['id']=='map_bounds' and scope['bounds']==[122.1,29.8,122.9,30.2]
    with pytest.raises(ValueError):resolve_scope('地图范围：122,30,121,31，风浪报告')
    with pytest.raises(ValueError):resolve_scope('舟山',bounds=[True,28,124,31])


@pytest.mark.parametrize('q,start,hours', [
    ('给我浙江未来三天风浪分析','2026-10-21T10:35:00+08:00',72),
    ('舟山未来两天风浪分析','2026-10-21T10:35:00+08:00',48),
    ('明天朱家尖海况简报','2026-10-22T00:00:00+08:00',72),
    ('浙江10月20日8时起未来一天风浪报告','2026-10-20T08:00:00+08:00',24),
    ('从2026年10月21日8时到10月23日20时浙江风浪报告','2026-10-21T08:00:00+08:00',60),
    ('明天20时到后天8时朱家尖风浪报告','2026-10-22T20:00:00+08:00',12),
    ('明天8时到20时舟山海浪报告','2026-10-22T08:00:00+08:00',12),
])
def test_chinese_duration_and_continuous_explicit_intervals(q,start,hours):
    actual,span=requested_window(q,NOW)
    assert actual.isoformat()==start and span==hours


def test_unsupported_durations_and_reverse_dates_fail():
    with pytest.raises(ValueError):requested_window('浙江未来8天风浪报告',NOW)
    with pytest.raises(ValueError):requested_window('2026年10月23日到10月21日海浪报告',NOW)


def test_explicit_thresholds_have_no_implied_safety_defaults():
    assert _thresholds('浪高超过2米，风速超过10米每秒会持续多久')=={
        'wave':{'value':2.0,'comparison':'>','label':'用户明确筛查阈值'},
        'wind':{'value':10.0,'comparison':'>','label':'用户明确筛查阈值'}}
    assert _thresholds('朱家尖风浪是否安全？') is None


def test_scope_bounds_reach_source_selection_report_and_map_action():
    calls={}
    dataset={'dataset_id':'zjocean_L5_fixture','batch':'2026-10-21T08:00:00+08:00',
             'source_family':'zjocean_l2_l5','fields':{'wave':{'valid_times':['2026-10-21T12:00:00+08:00']}}}
    class Sources:
        def ensure(self,*args):return {'state':'ready'}
        def select(self,*args,**kwargs):calls['selection']=kwargs;return dataset
    class Reports:
        def report(self,**kwargs):
            calls['report']=kwargs
            return {'state':'ready','objective_result':{'dataset_id':dataset['dataset_id'],
                     'fields':{'wave':{'maximum':{'value':2,'time':'2026-10-21T12:00:00+08:00'}}}}}
    runtime=SimpleNamespace(objective=SimpleNamespace(catalogue=lambda:{'datasets':[dataset]}),
        objective_sources=Sources(),objective_reports=Reports(),_local=SimpleNamespace(request_now=NOW))
    result=query_objective(runtime,'给我舟山未来三天海浪分析，浪高超过2米持续多久？')
    expected=resolve_scope('舟山')
    assert calls['selection']['bounds']==calls['report']['bounds']==result['map_action']['bounds']==expected['bounds']
    assert calls['report']['scope']==expected and calls['report']['thresholds']=={
        'wave':{'value':2.0,'comparison':'>','label':'用户明确筛查阈值'}}
    assert result['scope']==expected and calls['report']['hours']==72
    assert result['map_action']['type']=='objective_field'
    maximum=query_objective(runtime,'舟山未来三天海浪逐格最大值图')
    assert maximum['map_action']['type']=='objective_maximum'
    assert maximum['map_action']['start']==NOW.isoformat() and maximum['map_action']['hours']==72
    assert maximum['view_kind']=='maximum' and 'valid_time' not in maximum['map_action']


def test_report_context_retains_place_then_allows_correction_and_chinese_duration():
    scope=resolve_scope('舟山')
    context={'dataset_id':'zjocean_L5_fixture','start':'2026-10-21T08:00:00+08:00',
             'end':'2026-10-24T08:00:00+08:00','hours':72,'scope':scope,'bounds':scope['bounds']}
    assert product_context(context)['scope']==scope
    history=[{'objective_context':context}]
    follow=product_question_plan('同一批次换成朱家尖，未来两天的浪高最大多少？',history,now=NOW)
    assert follow['context_used'] and '朱家尖' in follow['normalized_question']
    assert '起未来48小时' in follow['normalized_question']
    assert resolve_scope(follow['normalized_question'])['id']=='zhujiajian'
    same=product_question_plan('同一时段最高浪高呢？',history,now=NOW)
    assert resolve_scope(same['normalized_question'])['id']=='zhoushan'


def test_report_context_rejects_forged_scope_metadata():
    scope=resolve_scope('舟山')
    context={'dataset_id':'zjocean_L5_fixture','start':'2026-10-21T08:00:00+08:00',
             'end':'2026-10-24T08:00:00+08:00','hours':72,'scope':{**scope,'bounds':[119,25,126,33]}}
    assert product_context(context) is None


@pytest.mark.parametrize('question', ['舟山未来72小时海浪逐格最大值图', '朱家尖海浪时段最大值图',
    '浙江72小时最大值分布海浪图'])
def test_maximum_map_intent_is_not_a_single_snapshot(question):
    assert _maximum_query(question)
    from forecast_runtime.objective_bridge import _field_query
    assert not _field_query(question)


def test_maximum_action_has_period_contract_without_a_snapshot_valid_time():
    from forecast_runtime.objective_bridge import _maximum_action
    dataset={'dataset_id':'zjocean_L5_fixture','source_family':'zjocean_l2_l5','level':'L5','fields':{'wave':{},'wind':{}}}
    action=_maximum_action(dataset,'wave',NOW,72,resolve_scope('舟山')['bounds'])
    assert action['type']=='objective_maximum' and action['hours']==72 and action['start']==NOW.isoformat()
    assert 'valid_time' not in action and action['field']=='wave' and action['level']=='L5'


def test_maximum_context_reuses_period_and_can_change_field_or_snapshot():
    scope=resolve_scope('朱家尖')
    context={'dataset_id':'zjocean_L5_fixture','start':'2026-10-21T08:00:00+08:00',
             'end':'2026-10-24T08:00:00+08:00','hours':72,'view_kind':'maximum','field':'wave',
             'scope':scope,'bounds':scope['bounds']}
    assert product_context(context)==context
    history=[{'objective_context':context}]
    same=product_question_plan('同一时段换成风场',history,now=NOW)
    assert _maximum_query(same['normalized_question'])
    assert '地图要素：海面风' in same['normalized_question'] and '起未来72小时' in same['normalized_question']
    snapshot=product_question_plan('改成明天8时海浪图',history,now=NOW)
    assert not _maximum_query(snapshot['normalized_question'])
    assert snapshot['objective_view_kind']=='field' and '起未来1小时' in snapshot['normalized_question']
    assert '2026年10月22日08时00分' in snapshot['normalized_question']
