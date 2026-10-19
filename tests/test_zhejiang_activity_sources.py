from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

import pytest

from forecast_runtime.activity_locations import ACTIVITY_SITES
from forecast_runtime.activity_sources import ActivitySources, QUALITY_URL, OPENING_URL, parse_quality_page, parse_operator_notice
from forecast_runtime.storage import BEIJING


QUALITY = '''<html><h1>2026年部分沿海城市海水浴场水质概况（第18期）</h1>
<p>发布时间：2026-10-12</p><table>
<tr><td>32</td><td>浙江省</td><td>舟山朱家尖海水浴场</td><td>9月28日</td><td>27.0</td><td>优</td><td>适宜</td></tr>
<tr><td>33</td><td>舟山嵊泗基湖沙滩海水浴场</td><td>9月27日</td><td>26.9</td><td>优</td><td>适宜</td></tr>
<tr><td>34</td><td>宁波象山松兰山海滨浴场</td><td>9月28日</td><td>浴场关闭，未监测</td><td>-</td></tr>
<tr><td>35</td><td>温岭金沙滩浴场</td><td>9月27日</td><td>28.3</td><td>良</td><td>较适宜</td></tr>
<tr><td>36</td><td>温州渔寮大沙滩海水浴场</td><td>9月26日</td><td>29.3</td><td>优</td><td>较适宜</td></tr>
<tr><td>37</td><td>温州南麂大沙岙海水浴场</td><td>9月26日</td><td>30.0</td><td>优</td><td>较适宜</td></tr>
<tr><td>38</td><td>温州洞头大沙岙海水浴场</td><td>9月26日</td><td>28.2</td><td>优</td><td>较适宜</td></tr>
</table><p>注：评价依据为HY/T 0276-2019。</p></html>'''


class Store:
    def __init__(self):
        self.values = {}

    def state(self, key, value=None):
        if value is not None:
            self.values[key] = deepcopy(value)
        return deepcopy(self.values.get(key))


def location(key):
    site = ACTIVITY_SITES[key]
    return {'key': key, 'site_id': key, 'quality_site': site['quality_site'],
            'province': '浙江省', 'city': site['city'], 'county': site['county']}


@pytest.mark.parametrize('key,temperature', [('jihu', 26.9), ('wenling_jinshatan', 28.3),
    ('yuliao', 29.3), ('nanji_dashaao', 30.0), ('dongtou_dashaao', 28.2)])
def test_exact_named_monitor_selection_preserves_own_values(key, temperature):
    result = parse_quality_page(QUALITY, QUALITY_URL, ACTIVITY_SITES[key]['quality_site'])
    quality = result['water_quality']
    assert quality['site_id'] == key
    assert quality['water_temperature_c'] == temperature
    assert quality['site_match_state'] == 'exact_named_monitor'
    assert quality['scope'] == 'bathing_water_observation_only'


def test_same_short_dashaao_name_never_splices_two_stations():
    first = parse_quality_page(QUALITY, QUALITY_URL, '温州南麂大沙岙海水浴场')['water_quality']
    second = parse_quality_page(QUALITY, QUALITY_URL, '温州洞头大沙岙海水浴场')['water_quality']
    assert first['site_id'] != second['site_id']
    assert first['water_temperature_c'] == 30
    assert second['water_temperature_c'] == 28.2
    with pytest.raises(ValueError, match='quality_row_not_unique'):
        parse_quality_page(QUALITY, QUALITY_URL, '大沙岙')


def test_closed_monitor_row_has_no_synthetic_measurement_or_open_permission():
    result = parse_quality_page(QUALITY, QUALITY_URL, '宁波象山松兰山海滨浴场')
    assert result['water_quality']['measurement_state'] == 'not_monitored'
    assert result['water_quality']['water_temperature_c'] is None
    assert result['water_quality']['water_quality_grade'] is None
    assert 'activity_evidence' not in result
    assert result['source']['valid_to'] == '2026-09-28'


def test_future_report_does_not_change_observation_day_or_claim_current_quality():
    reader = ActivitySources(Store())
    record = parse_quality_page(QUALITY, QUALITY_URL, '温州洞头大沙岙海水浴场')
    with patch.object(reader, '_zhejiang_quality_records', return_value=([record], [
        {'kind': 'official_source_index', 'url': QUALITY_URL, 'status': 'parsed'}])):
        result = reader.fetch_for_location(location('dongtou_dashaao'), '2026-10-17')
    assert result['water_quality']['state'] == 'historical'
    assert result['water_quality']['observed_at'] == '2026-09-26'
    assert result['water_quality']['site_id'] == 'dongtou_dashaao'
    assert not result['discovery_complete']
    assert set(result['activity_permission'].values()) == {'unverified'}
    assert result['activity_evidence'] == []


def test_source_cache_is_separate_for_named_stations_and_missing_operator_is_explicit():
    reader = ActivitySources(Store())
    def records(target):
        return [parse_quality_page(QUALITY, QUALITY_URL, target)], []
    with patch.object(reader, '_zhejiang_quality_records', side_effect=records) as fetch:
        one = reader.fetch_for_location(location('jihu'), '2026-10-17')
        two = reader.fetch_for_location(location('yuliao'), '2026-10-17')
        again = reader.fetch_for_location(location('jihu'), '2026-10-17')
    assert fetch.call_count == 2
    assert one['water_quality']['site_id'] == again['water_quality']['site_id'] == 'jihu'
    assert two['water_quality']['site_id'] == 'yuliao'
    assert again['cached']
    assert 'operator' in again['reason']


@pytest.mark.parametrize('requested', [
    {'key': 'coast:1160', 'site_id': None, 'quality_site': None},
    {'key': 'dongtou_dashaao', 'site_id': 'dongtou_dashaao', 'quality_site': '舟山朱家尖海水浴场'},
    {**location('dongtou_dashaao'), 'coordinate_conflict': True},
    {**location('nanji_dashaao'), 'coordinate_site_unverified': True},
])
def test_admin_or_coordinate_conflict_never_borrows_zhujiajian_or_other_monitor(requested):
    reader = ActivitySources(Store())
    with patch.object(reader, '_zhejiang_quality_records') as fetch:
        result = reader.fetch_for_location(requested, '2026-10-17')
    fetch.assert_not_called()
    assert result['state'] == 'unavailable'
    assert result['water_quality']['state'] == 'unavailable'
    assert result['activity_evidence'] == []


def test_changed_or_incomplete_quality_rows_fail_closed():
    for changed in [QUALITY.replace('28.2', '缺测'), QUALITY.replace('28.2', '50'),
                    QUALITY.replace('9月26日', '11月26日')]:
        with pytest.raises(ValueError):
            parse_quality_page(changed, QUALITY_URL, '温州洞头大沙岙海水浴场')


def test_date_matching_still_records_publication_not_yet_available():
    reader = ActivitySources(Store())
    record = parse_quality_page(QUALITY, QUALITY_URL, '温州洞头大沙岙海水浴场')
    with patch.object(reader, '_zhejiang_quality_records', return_value=([record], [])):
        result = reader.fetch_for_location(location('dongtou_dashaao'), '2026-09-26')
    assert result['water_quality']['state'] == 'unavailable'
    assert result['sources'][0]['published_by_query_date'] is False
    assert result['sources'][0]['published_by_as_of'] is False
    assert result['sources'][0]['status'] == 'not_published_by_as_of'
    assert result['sources'][0]['text_excerpt'] == ''
    assert result['activity_evidence'] == []


def notice(text, published='2026-10-17'):
    return parse_operator_notice(f'<html><div>发布日期：{published}</div><article><p>{text}</p></article></html>', OPENING_URL)


@pytest.mark.parametrize('text,state', [
    ('2026年10月18日南沙尚未恢复游泳', 'verified_closed'),
    ('2026年10月18日南沙不允许游泳', 'verified_closed'),
    ('2026年10月18日南沙未恢复开放泳区', 'verified_closed'),
    ('2026年10月18日南沙允许游泳，禁止携带宠物进入', 'verified_open'),
    ('2026年10月18日南沙允许游泳，但禁止携带宠物进入', 'verified_open'),
    ('2026年10月18日南沙允许游泳，严禁吸烟', 'verified_open'),
    ('2026年10月18日南沙游泳活动暂停', 'verified_closed'),
])
def test_operator_directives_bind_to_activity_and_respect_negation(text, state):
    result = notice(text)
    rows = [row for row in result['activity_evidence'] if row['activity'] == 'swimming']
    assert len(rows) == 1
    assert rows[0]['state'] == state
    assert rows[0]['site_id'] == 'nansha'


@pytest.mark.parametrize('text', [
    '2026年10月18日南沙恢复游泳时间另行通知',
    '2026年10月18日南沙没有禁止游泳',
    '2026年10月18日南沙预计恢复游泳',
    '2026年10月18日南沙如果天气良好允许游泳',
    '2026年10月18日南沙禁止携带宠物进入游泳区',
    '2026年10月18日南沙允许游泳8:00至18:00',
])
def test_uncertain_or_unrelated_operator_prose_never_grants_or_blanks_closes(text):
    result = notice(text)
    assert result['activity_evidence'] == []
    assert result['unresolved_notice_events']
    assert all(row['state'] == 'unresolved' for row in result['unresolved_notice_events'])


def test_two_activities_keep_their_own_directive_and_site():
    result = notice('2026年10月18日南沙禁止海钓，允许游泳；东沙暂停游泳')
    states = {(row['site_id'], row['activity']): row['state'] for row in result['activity_evidence']}
    assert states == {('nansha', 'shore_fishing'): 'verified_closed',
                      ('nansha', 'swimming'): 'verified_open',
                      ('dongsha', 'swimming'): 'verified_closed'}


def test_each_notice_clause_keeps_its_own_effective_day():
    result = notice('2026年10月18日南沙允许游泳，2026年10月19日南沙暂停游泳')
    assert [(row['valid_from'], row['valid_to'], row['state']) for row in result['activity_evidence']] == [
        ('2026-10-18', '2026-10-18', 'verified_open'),
        ('2026-10-19', '2026-10-19', 'verified_closed')]


@pytest.mark.parametrize('text', [
    '如天气条件允许，2026年10月18日南沙恢复游泳',
    '2026年10月18日08:00至18:00，南沙允许游泳',
    '2026年10月18日南沙允许游泳，具体开放时间另行通知',
])
def test_conditional_or_within_day_header_cannot_become_unconditional_whole_day_open(text):
    result = notice(text)
    assert result['activity_evidence'] == []
    assert result['unresolved_notice_events']


@pytest.mark.parametrize('published,query_day,as_of', [
    ('2026-10-18', '2026-10-18', None),
    ('2026-10-17 18:00', '2026-10-18', None),
    ('2026-10-17 11:00', '2026-10-17', '2026-10-17T10:00:00+08:00'),
])
def test_future_publications_never_become_known_for_future_query(published, query_day, as_of):
    reader = ActivitySources(Store())
    record = notice('2026年10月17日至10月20日南沙允许游泳', published)
    with patch('forecast_runtime.activity_sources.local_now', return_value=datetime(2026, 10, 17, 12, tzinfo=BEIJING)), \
         patch.object(reader, '_records', return_value=([record], [{'status': 'parsed'}])):
        result = reader.fetch('zhujiajian', query_day, site_name='南沙', as_of=as_of)
    assert result['activity_evidence'] == []
    assert result['notice_evidence'] == []
    assert result['unresolved_notice_events'] == []
    assert result['activity_permission']['swimming'] == 'unverified'
    assert result['sources'][0]['status'] == 'not_published_by_as_of'
    assert result['sources'][0]['text_excerpt'] == ''


def test_known_advance_notice_can_apply_to_future_date_but_future_notice_cannot_cancel_it():
    reader = ActivitySources(Store())
    early = notice('2026年10月18日南沙允许游泳', '2026-10-16 09:00')
    later = notice('2026年10月18日南沙暂停游泳', '2026-10-18 09:00')
    with patch('forecast_runtime.activity_sources.local_now', return_value=datetime(2026, 10, 17, 12, tzinfo=BEIJING)), \
         patch.object(reader, '_records', return_value=([early, later], [{'status': 'parsed'}])):
        result = reader.fetch('zhujiajian', '2026-10-18', site_name='南沙')
    assert result['activity_permission']['swimming'] == 'verified_open'
    assert len(result['notice_evidence']) == 1
    assert result['notice_evidence'][0]['published_at'] == '2026-10-16'
    assert result['publication_as_of'] == '2026-10-17T12:00:00+08:00'


@pytest.mark.parametrize('key', ['zhujiajian', 'dongtou_dashaao'])
def test_both_adapters_remove_not_yet_published_water_measurements(key):
    reader = ActivitySources(Store())
    future_html = QUALITY.replace('发布时间：2026-10-12', '发布时间：2026-10-18 08:00')
    record = parse_quality_page(future_html, QUALITY_URL, ACTIVITY_SITES[key]['quality_site'])
    method = '_records' if key == 'zhujiajian' else '_zhejiang_quality_records'
    with patch('forecast_runtime.activity_sources.local_now', return_value=datetime(2026, 10, 17, 12, tzinfo=BEIJING)), \
         patch.object(reader, method, return_value=([record], [{'status': 'parsed'}])):
        result = reader.fetch_for_location(location(key), '2026-10-18')
    assert result['water_quality'] == {'state': 'unavailable'}
    assert result['sources'][0]['published_by_as_of'] is False
    assert result['sources'][0]['status'] == 'not_published_by_as_of'
    assert result['sources'][0]['text_excerpt'] == ''


def test_historical_quality_stays_available_with_explicit_date_precision_limitation():
    reader = ActivitySources(Store())
    record = parse_quality_page(QUALITY, QUALITY_URL, ACTIVITY_SITES['dongtou_dashaao']['quality_site'])
    with patch('forecast_runtime.activity_sources.local_now', return_value=datetime(2026, 10, 17, 12, tzinfo=BEIJING)), \
         patch.object(reader, '_zhejiang_quality_records', return_value=([record], [])):
        result = reader.fetch_for_location(location('dongtou_dashaao'), '2026-10-18')
    assert result['water_quality']['water_temperature_c'] == 28.2
    assert result['water_quality']['state'] == 'historical'
    assert result['sources'][0]['publication_time_precision'] == 'date'
    assert '不构成精确历史快照' in result['publication_time_policy']
