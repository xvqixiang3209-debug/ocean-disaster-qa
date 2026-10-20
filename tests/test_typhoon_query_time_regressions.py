"""Clock, archive isolation and raw-list scope contracts, without live calls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from forecast_runtime.service import ForecastRuntime, as_of_cutoff, query_time_context
from forecast_runtime.storage import BEIJING
from forecast_runtime.routing import grid_request, numeric_request


NOW = datetime(2026, 10, 20, 18, 40, 22, 123456, tzinfo=BEIJING)
SOURCE = 'https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons'


def storm(sid=202601, name='甲台风', time='2026-10-20 17:00:00'):
    return {'typhoonID': sid, 'nameCN': name, 'points': [
        {'time': time, 'lon': 133.8, 'lat': 16.7, 'pressure': 915,
         'maxSpeed': 62, 'moveDirection': 'WNW', 'moveSpeed': 24, 'isActive': 1}]}


def live_value(storms=None):
    return {'source_url': SOURCE, 'source': '浙江海洋预报网活动台风接口',
            'retrieved_at': NOW.isoformat(), 'cache_stale': False,
            'list': [storm()] if storms is None else storms}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr('forecast_runtime.service.local_now', lambda: NOW)
    value = ForecastRuntime(tmp_path / 'forecast')
    value.sync.typhoons = Mock(return_value=live_value())
    return value


@pytest.mark.parametrize('question', [
    '截至2026年10月20日，现在有几个台风在活动？最新位置是什么？',
    '截至2026-10-20，活动台风有几个？',
    '截止2026年10月20日，现在有几个台风？',
    '截至今天，现在有几个台风在活动？',
    '今天有几个台风在活动？',
    '2026年10月20日，有几个台风在活动？',
    '现在有几个台风在活动？',
])
def test_today_queries_use_live_source_instead_of_yesterdays_archive(runtime, question):
    runtime.store.record_typhoon_snapshot({**live_value(),
        'retrieved_at': '2026-10-19T12:40:12+08:00',
        'list': [storm(time='2026-10-19 11:00:00')]}, complete=True)
    result = runtime.query(question)
    runtime.sync.typhoons.assert_called_once_with()
    assert result['query_time_mode'] == 'current'
    assert not result.get('historical_snapshot')
    assert result['list'][0]['points'][0]['time'] == '2026-10-20 17:00:00'
    assert result['as_of'] == result['requested_as_of'] == NOW.isoformat()
    assert result['activity_list_count'] == 1
    assert result['activity_list_count_verified']


def test_advancing_wall_clock_is_read_once_for_query(runtime, monkeypatch):
    calls = []
    def advancing():
        calls.append(True)
        return NOW + timedelta(microseconds=len(calls))
    monkeypatch.setattr('forecast_runtime.service.local_now', advancing)
    result = runtime.query('截至2026年10月20日，现在有几个台风在活动？')
    assert len(calls) == 1
    assert result['query_time_mode'] == 'current'
    assert result['as_of'] == result['requested_as_of']
    runtime.sync.typhoons.assert_called_once()
    assert not hasattr(runtime._local, 'request_now')
    assert not hasattr(runtime._local, 'request_time')
    assert not hasattr(runtime._local, 'request_cutoff')


@pytest.mark.parametrize(('question', 'cutoff'), [
    ('截至2026年10月19日，有几个台风在活动？', '2026-10-19T23:59:59+08:00'),
    ('截至2026年10月20日12时，现在有几个台风在活动？', '2026-10-20T12:00:00+08:00'),
    ('截止今天12时，活动台风有几个？', '2026-10-20T12:00:00+08:00'),
    ('截至昨日，活动台风有几个？', '2026-10-19T23:59:59+08:00'),
    ('昨天有几个台风在活动？', '2026-10-19T23:59:59+08:00'),
])
def test_historical_cutoffs_never_fall_back_to_later_live_data(runtime, question, cutoff):
    runtime.store.record_typhoon_snapshot({**live_value(),
        'retrieved_at': '2026-10-19T12:40:12+08:00',
        'list': [storm(time='2026-10-19 11:00:00')]}, complete=True)
    runtime.store.record_typhoon_snapshot(live_value(), complete=True)
    result = runtime.query(question)
    runtime.sync.typhoons.assert_not_called()
    assert result['query_time_mode'] == 'historical'
    assert result['requested_as_of'] == cutoff
    assert result['retrieved_at'] == '2026-10-19T12:40:12+08:00'
    assert result['list'][0]['points'][0]['time'] == '2026-10-19 11:00:00'


def test_missing_historical_snapshot_is_missing_even_with_current_cache(runtime):
    runtime.store.state('typhoons', live_value())
    result = runtime.query('截至2026年10月18日，活动台风有几个？')
    runtime.sync.typhoons.assert_not_called()
    assert result['state'] == 'historical_snapshot_unavailable'
    assert result['activity_list_count'] is None
    assert not result['activity_list_count_verified']


@pytest.mark.parametrize('question', [
    '截至2026年10月21日，有几个台风在活动？',
    '截至2026年10月20日23时，台风的中心位置是什么？',
    '截至明天，活动台风有几个？',
    '明天有几个台风在活动？',
    '2026年10月21日，有几个台风在活动？',
])
def test_future_observations_are_not_current_activity_lists(runtime, question):
    result = runtime.query(question)
    runtime.sync.typhoons.assert_not_called()
    assert result['query_time_mode'] == 'future'
    assert result['state'] == 'future_observation_unavailable'
    assert result['list'] == []
    assert result['activity_list_count'] is None
    assert not result['activity_list_count_verified']
    summary = json.loads(runtime.summary(result).split('\n', 1)[1])
    assert summary['活动台风数'] is None
    assert '尚未到达' in summary['缺项说明'][0]


def test_future_forecast_valid_date_is_not_a_future_observation_cutoff():
    q = '台风预计2026年10月21日在哪里登陆？'
    assert query_time_context(q, now=NOW)['mode'] == 'current'
    q = '现在有几个台风？2026年10月21日舟山海域浪高是多少？'
    assert query_time_context(q, now=NOW)['mode'] == 'current'


def test_future_asof_still_caps_forecast_issue_selection_at_request_time():
    q = '截至2026年10月21日，2026年10月22日舟山浪高多少？'
    context = query_time_context(q, now=NOW)
    assert context['mode'] == 'future'
    assert context['requested_at'] > NOW
    assert as_of_cutoff(q, now=NOW) == NOW


def test_short_month_day_cutoff_uses_reference_year_and_historical_isolation(runtime):
    runtime.store.record_typhoon_snapshot({**live_value(),
        'retrieved_at': '2026-10-19T12:40:12+08:00',
        'list': [storm(time='2026-10-19 11:00:00')]}, complete=True)
    result=runtime.query('截至10月19日，活动台风有几个？')
    assert result['query_time_mode']=='historical'
    assert result['requested_as_of']=='2026-10-19T23:59:59+08:00'
    runtime.sync.typhoons.assert_not_called()


def test_unsupported_numeric_cutoff_is_rejected_instead_of_silently_live(runtime):
    with pytest.raises(ValueError,match='截至日期格式不支持'):
        runtime.query('截至2026.10.20，活动台风有几个？')
    runtime.sync.typhoons.assert_not_called()
    assert not hasattr(runtime._local,'request_now')
    assert not hasattr(runtime._local,'request_cutoff')


@pytest.mark.parametrize(('token', 'expected'), [
    ('2026年10月20日18时30分', '2026-10-20T18:30:00+08:00'),
    ('2026年10月20日18：30', '2026-10-20T18:30:00+08:00'),
    ('2026-10-20T18:30', '2026-10-20T18:30:00+08:00'),
    ('2026-10-20T18:30:19.125+08:00', '2026-10-20T18:30:19.125000+08:00'),
    ('2026年10月20日18时30分19秒', '2026-10-20T18:30:19+08:00'),
    ('2026年10月20日18时30分19.125秒', '2026-10-20T18:30:19.125000+08:00'),
    ('2026-10-20T10:30:19Z', '2026-10-20T18:30:19+08:00'),
    ('2026-10-20 10:30+00:00', '2026-10-20T18:30:00+08:00'),
    ('2026-10-20T19:30:19+09:00', '2026-10-20T18:30:19+08:00'),
    ('2026-10-20T06:30-0400', '2026-10-20T18:30:00+08:00'),
    ('2026年10月20日18时30分北京时间', '2026-10-20T18:30:00+08:00'),
    ('2026-10-20 10:30 UTC', '2026-10-20T18:30:00+08:00'),
    ('2026-10-20 18:30 UTC+8', '2026-10-20T18:30:00+08:00'),
])
def test_cutoff_preserves_minutes_seconds_precision_and_explicit_timezone(token, expected):
    context = query_time_context('截至'+token+'，活动台风有几个？', now=NOW)
    assert context['mode'] == 'historical'
    assert context['requested_at'].isoformat() == expected
    assert context['cutoff'].isoformat() == expected


def test_cutoff_exact_clock_and_one_microsecond_future_are_distinct():
    same = query_time_context('截至'+NOW.isoformat()+'，活动台风有几个？', now=NOW)
    future = query_time_context('截至'+(NOW+timedelta(microseconds=1)).isoformat()+'，活动台风有几个？', now=NOW)
    assert same['mode'] == 'current'
    assert future['mode'] == 'future'
    assert future['requested_at'] == NOW+timedelta(microseconds=1)
    assert future['cutoff'] == NOW


def test_utc_date_crosses_to_the_right_local_day():
    context = query_time_context('截至2026-10-20T19:30Z，活动台风有几个？', now=NOW)
    assert context['requested_at'].isoformat() == '2026-10-21T03:30:00+08:00'
    assert context['mode'] == 'future'


@pytest.mark.parametrize('token', [
    '2026-10-20T', '2026-10-20T18:', '2026-10-20T18:30 CST',
    '2026-10-20T18:30:61', '2026-10-20T25:30',
    '2026-10-20T18:30+99:00', '2026-10-20T18:30+08:99',
    '2026-10-20T18:30:19.1234567', '2026年10月20日下午6时30分',
])
def test_unsupported_cutoff_cannot_silently_become_a_current_query(runtime, token):
    with pytest.raises(ValueError, match='截至时刻格式不支持'):
        runtime.query('截至'+token+'，活动台风有几个？')
    runtime.sync.typhoons.assert_not_called()
    assert not hasattr(runtime._local, 'request_now')
    assert not hasattr(runtime._local, 'request_time')
    assert not hasattr(runtime._local, 'request_cutoff')
    assert runtime.query('当前有几个台风在活动？')['query_time_mode'] == 'current'


def test_reference_timezone_is_normalized_before_resolving_today():
    utc = NOW.astimezone(timezone.utc)
    assert query_time_context('截至今天，有几个台风在活动？', now=utc)['requested_at'] == NOW


def test_subset_query_does_not_turn_selected_length_into_total_count(runtime):
    runtime.sync.typhoons.return_value = live_value([storm(), storm(202602, '乙台风')])
    result = runtime.query('当前接口标识202601的台风中心位置和强度是什么？')
    assert result['selection_scope'] == 'named_subset'
    assert result['activity_list_count'] == 2
    assert len(result['list']) == 1
    summary = json.loads(runtime.summary(result).split('\n', 1)[1])
    assert summary['活动台风数'] == 2
    assert summary['已选对象数'] == 1


def test_activity_membership_does_not_require_centre_points(runtime):
    runtime.sync.typhoons.return_value = live_value([
        storm(), {'typhoonID': 202602, 'nameCN': '乙台风', 'points': []}])
    result = runtime.query('当前有几个台风在活动？')
    assert result['activity_list_count'] == len(result['list']) == 2
    assert not result['list'][1]['points']


def test_future_centre_points_are_not_published_as_current_observations(runtime):
    s = storm()
    s['points'].append({**s['points'][0], 'time': '2026-10-20 23:00:00', 'lon': 120})
    runtime.sync.typhoons.return_value = live_value([s])
    result = runtime.query('当前台风的最新位置是什么？')
    assert len(result['list'][0]['points']) == 1
    assert result['activity_list_count'] == 1


def test_bundled_live_and_grid_queries_share_one_request_instant(runtime, monkeypatch):
    calls = []
    def advancing():
        calls.append(True)
        return NOW + timedelta(seconds=len(calls))
    monkeypatch.setattr('forecast_runtime.service.local_now', advancing)
    result = runtime.query('截至2026年10月20日，现在有几个台风在活动？舟山海域浪高多少？')
    assert result['kind'] == 'bundle'
    assert {item['kind'] for item in result['items']} == {'typhoons', 'grids'}
    assert len(calls) == 1
    assert {item['as_of'] for item in result['items']} == {result['as_of']}
    assert all(child['as_of'] == result['as_of'] for item in result['items'] for child in item.get('products', []))


MULTI = ('今天活动台风有几个、分别叫什么？各台风最新中心最大风速、最低气压、'
         '移动方向和移动速度分别是多少？请同时写明查询时间与记录时间。')


@pytest.mark.parametrize('question', [
    MULTI, '当前台风中心最大风速是多少？', '台风最新风力等级和中心最低气压是多少？',
    '截至今天，台风各机构预报中心最大风速是多少？',
])
def test_centre_wind_fields_do_not_spawn_an_nc_subtask(runtime, question):
    runtime.grid = Mock(wraps=runtime.grid)
    assert not grid_request(question)
    result = runtime.query(question)
    assert result['kind'] == 'typhoons'
    assert not result['trial']
    runtime.grid.assert_not_called()
    runtime.sync.typhoons.assert_called_once()


@pytest.mark.parametrize('question', [
    '当前台风中心最大风速和舟山海域风速分别是多少？',
    '当前台风中心风速是多少？请同时查询宁波沿海风力。',
    '当前台风中心最大风速与舟山风速是多少？',
])
def test_explicit_local_wind_preserves_a_separate_grid_subtask(runtime, question):
    runtime.grid = Mock(wraps=runtime.grid)
    assert grid_request(question)
    result = runtime.query(question)
    assert result['kind'] == 'bundle'
    assert {item['kind'] for item in result['items']} == {'typhoons', 'grids'}
    assert result['trial']
    assert any(call.args[0] == 'wind' for call in runtime.grid.call_args_list)


def test_city_forecast_clause_is_not_changed_by_a_siblings_cyclone_definition():
    question='2026年10月17日舟山预报风速多少？请同时解释热带气旋等级。'
    assert grid_request(question)
    assert numeric_request(question)
    assert not grid_request('浙江接口提供的台风中心最大风速是多少？')


def test_clock_context_is_cleaned_up_after_a_bad_query(runtime):
    with pytest.raises(ValueError):
        runtime.query('2026年10月22日00时至10月20日00时舟山浪高多少？')
    assert not hasattr(runtime._local, 'request_now')
    assert not hasattr(runtime._local, 'request_time')
    assert not hasattr(runtime._local, 'request_cutoff')


@pytest.mark.parametrize('mode', ['disabled', 'success', 'model_error'])
def test_pure_interface_answers_share_nontrial_label_and_provenance(runtime, mode):
    captured = []
    qa = SimpleNamespace(
        crag_enabled=True, external_enabled=mode != 'disabled',
        model_caller=None if mode == 'disabled' else object(), model_name='test-only',
        joint_evidence=lambda question, meta, allow_web, pinned, **kw: captured.extend(pinned) or pinned,
        _public_sources=lambda question, hits: hits,
        _organized_answer=lambda question, model, hits, meta: {
            'answer': '接口查询结果',
            'answer_meta': {**meta, 'used_mode': mode, 'answerability': 'partial'}})
    answer = runtime.answer('当前有几个台风在活动？', qa, {}, False)
    assert '台风接口查询结果' in answer['answer']
    assert '试算结果（假设口径）' not in answer['answer']
    assert answer['answer_meta']['business_trial'] is False
    assert answer['answer_meta']['official_typhoon_records'] is True
    assert captured[0]['metadata']['official_typhoon_records'] is True
    assert captured[0]['metadata']['trial'] is False
    assert runtime.evidence_allowed(captured[0])
    assert '逐时含首尾采样' not in captured[0]['text']


def test_official_typhoon_marker_cannot_authorize_a_forged_source(runtime):
    assert not runtime.evidence_allowed({'source_id': 'forecast:' + '0' * 24,
        'metadata': {'official_typhoon_records': True, 'assumption_id': runtime.config_id()}})
