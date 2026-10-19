import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import requests

from forecast_runtime.activity_sources import (
    ActivitySources, OPENING_URL, QUALITY_URL, MAX_BYTES,
    OPERATOR_INDEX_URL, QUALITY_INDEX_URL,
    parse_opening_page, parse_quality_page, parse_operator_notice, discover_articles,
)
from forecast_runtime.storage import BEIJING


OPENING = '''<html><head><meta name="pubDate" content="2023-09-29">
<meta name="pubdate" content="2026-10-01 08:58"></head><body><nav>无关导航</nav>
发布日期：2026-10-01<script>南沙景点调整为0:00-23:59</script>
<div>景区营业时间调整通知：</div>
<p>一、10月1日-10月7日</p><p>南沙景点调整为7：00-21：00</p><p>东沙景点调整为4：30-21：00</p>
<p>二、10月8日-10月31日</p><p>南沙景点调整为8：00-18：00</p><p>东沙景点调整为5：00-18：00（日出票请至现场购票）</p>
<p>三、11月1日-12月31日</p><p>东沙景点营业时间调整为5：30-16：30</p>
<p>南沙景点、乌石塘景区、白山景区营业时间调整为8：00-16：30</p>
<footer>Produced By 导航噪声</footer></body></html>'''
QUALITY = '''<html><body>首页 导航
<h1>2026年部分沿海城市海水浴场水质概况（第18期）</h1>
<div>发布时间：2026-10-12 | 来源：国家海洋环境监测中心</div>
<p>2026年9月25日—9月30日，开展水质监测。</p>
<table><tr><th>序号</th><th>省份</th><th>浴场名称</th><th>监测日期</th></tr>
<tr><td>32</td><td>浙江省</td><td>舟山朱家尖海水浴场</td><td>9 月 28 日</td><td>27.0</td><td>优</td><td>适宜</td><td>-</td></tr></table>
<p>注：①评价依据为《海水浴场监测与评价指南》（HY/T 0276-2019）。</p>
<div>【关闭】文章推荐 首页其他新闻</div></body></html>'''


class Store:
    def __init__(self):
        self.data = {}

    def state(self, key, value=None):
        if value is not None:
            self.data[key] = value
            return value
        return self.data.get(key)


class Response:
    def __init__(self, url, body, status=200, headers=None):
        self.url, self.body, self.status_code = url, body.encode(), status
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class ActivityParserTests(unittest.TestCase):
    def test_operator_date_schedule_and_admission_scope(self):
        value = parse_opening_page(OPENING)
        self.assertEqual(value['source']['published_at'], '2026-10-01')
        october = [r for r in value['opening_hours'] if r['valid_from'] == '2026-10-08']
        self.assertEqual([(r['site'], r['start_time'], r['end_time']) for r in october],
                         [('南沙景点', '08:00', '18:00'), ('东沙景点', '05:00', '18:00')])
        self.assertTrue(all(r['scope'] == 'scenic_admission_only' for r in value['opening_hours']))
        self.assertEqual(len([r for r in value['opening_hours'] if r['valid_from'] == '2026-11-01']), 4)
        self.assertNotIn('无关导航', value['source']['text_excerpt'])

    def test_lowercase_current_metadata_used_if_visible_date_missing(self):
        value = parse_opening_page(OPENING.replace('发布日期：2026-10-01', ''))
        self.assertEqual(value['source']['published_at'], '2026-10-01')

    def test_quality_uses_observation_date_not_url_or_publication(self):
        value = parse_quality_page(QUALITY)
        self.assertEqual(value['source']['published_at'], '2026-10-12')
        self.assertEqual(value['water_quality']['observed_at'], '2026-09-28')
        self.assertEqual(value['water_quality']['water_temperature_c'], 27.0)
        self.assertNotIn('其他新闻', value['source']['text_excerpt'])

    def test_changed_or_incomplete_pages_fail_closed(self):
        for parser, body in [(parse_opening_page, '<html>导航可访问但没有公告</html>'),
                             (parse_quality_page, QUALITY.replace('9 月 28 日', '-')),
                             (parse_quality_page, QUALITY.replace('27.0', '浴场关闭，未监测'))]:
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(ValueError):
                    parser(body)

    def test_discovery_requires_same_host_article_path_and_relevant_anchor(self):
        html = f'''<a href="{OPENING_URL}">景区营业时间调整通知</a>
        <a href="https://evil.example/art/2026/10/18/art_1_1.html">南沙游泳开放公告</a>
        <a href="/admin/delete">南沙游泳开放公告</a>
        <script><a href="/art/2026/10/18/art_1_1.html">伪造公告</a></script>'''
        self.assertEqual(discover_articles(html, OPERATOR_INDEX_URL, 'operator_schedule'), [OPENING_URL])
        self.assertEqual(discover_articles('<a href="./202610/t20261014_1182943.shtml">2026年部分沿海城市海水浴场水质概况（第18期）</a>',
                        QUALITY_INDEX_URL, 'bathing_water_quality'), [QUALITY_URL])

    @staticmethod
    def notice(text, published='2026-10-17'):
        return f'<html><div>发布日期：{published}</div><article><p>{text}</p></article></html>'

    def test_dated_activity_notice_never_becomes_other_site_or_admission_permission(self):
        value = parse_operator_notice(self.notice('2026年10月17日至10月18日，南沙景区暂停游泳，恢复时间另行通知。'), OPENING_URL)
        rows = value['activity_evidence']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['site_id'], rows[0]['activity'], rows[0]['state']),
                         ('nansha', 'swimming', 'verified_closed'))
        self.assertEqual(rows[0]['valid_to'], '2026-10-18')
        with self.assertRaises(ValueError):
            parse_operator_notice(self.notice('2026年10月17日南沙景区正常营业，可购游泳圈。'), OPENING_URL)

    def test_open_ended_notice_remains_unresolved_and_nav_is_not_article(self):
        value = parse_operator_notice(self.notice('南沙景区自2026年10月17日起禁止游泳，恢复时间另行通知。'), OPENING_URL)
        self.assertEqual(value['activity_evidence'], [])
        self.assertIsNone(value['unresolved_notice_events'][0]['valid_to'])
        with self.assertRaises(ValueError):
            parse_operator_notice('<nav>发布日期：2026-10-17 南沙2026年10月17日允许游泳</nav>', OPENING_URL)

    def test_negated_permission_is_not_open_and_mixed_activities_are_separate(self):
        row = parse_operator_notice(self.notice('2026年10月17日南沙景区不允许游泳。'), OPENING_URL)['activity_evidence'][0]
        self.assertEqual(row['state'], 'verified_closed')
        parsed=parse_operator_notice(self.notice('2026年10月17日南沙景区禁止海钓，允许游泳。'), OPENING_URL)
        states={row['activity']:row['state'] for row in parsed['activity_evidence']}
        self.assertEqual(states,{'shore_fishing':'verified_closed','swimming':'verified_open'})

    def test_quality_monitor_is_not_automatically_a_nansha_or_dongsha_match(self):
        result = parse_quality_page(QUALITY)['water_quality']
        self.assertEqual(result['site_id'], 'zhujiajian_bathing_water_monitor')
        self.assertEqual(result['valid_to'], '2026-09-28')
        self.assertEqual(result['site_match_state'], 'regional_monitor_not_specific_beach')


class ActivityFetchTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.reader = ActivitySources(self.store)
        self.now = datetime(2026, 10, 17, 17, tzinfo=BEIJING)

    @staticmethod
    def response(url, **kwargs):
        if url == OPERATOR_INDEX_URL:
            return Response(url, f'<a href="{OPENING_URL}">景区营业时间调整通知</a>')
        if url == QUALITY_INDEX_URL:
            return Response(url, f'<a href="{QUALITY_URL}">2026年部分沿海城市海水浴场水质概况（第18期）</a>')
        return Response(url, OPENING if url == OPENING_URL else QUALITY)

    def test_cached_records_reclassified_for_query_day_and_never_grant_permission(self):
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=self.response) as get:
            october = self.reader.fetch('zhujiajian', '2026-10-17')
            november = self.reader.fetch('zhujiajian', '2026-11-05')
            same_day = self.reader.fetch('zhujiajian', '2026-09-28')
        self.assertEqual(get.call_count, 4)
        self.assertEqual(october['state'], 'available')
        self.assertEqual(october['water_quality']['state'], 'historical')
        self.assertEqual(october['water_quality']['observed_at'], '2026-09-28')
        self.assertEqual(november['opening_hours'][0]['start_time'], '05:30')
        self.assertEqual(same_day['water_quality']['state'], 'unavailable')
        self.assertNotIn('water_temperature_c',same_day['water_quality'])
        self.assertTrue(any(source['status']=='not_published_by_as_of' for source in same_day['sources']))
        self.assertTrue(all(v == 'unverified' for v in october['activity_permission'].values()))
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))

    def test_expired_cache_refetches_and_failure_does_not_reuse_as_current(self):
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=self.response):
            self.reader.fetch('zhujiajian', '2026-10-17')
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now + timedelta(hours=2)), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=requests.Timeout):
            value = self.reader.fetch('zhujiajian', '2026-10-17')
        self.assertEqual(value['state'], 'unavailable')
        self.assertEqual(value['opening_hours'], [])
        self.assertEqual(value['water_quality']['state'], 'unavailable')
        self.assertTrue(all(not r['text_excerpt'] for r in value['sources']))

    def test_http_redirect_and_oversized_response_rejected(self):
        responses = []
        def response(url, **kwargs):
            value = Response(url, OPENING, status=302) if urlsplit_for_test(url) == 'www.zhujiajian.com' else Response(
                url, QUALITY, headers={'Content-Type': 'text/html', 'Content-Length': str(MAX_BYTES + 1)})
            responses.append(value)
            return value
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=response):
            value = self.reader.fetch('zhujiajian', '2026-10-17')
        self.assertEqual(value['state'], 'unavailable')
        self.assertTrue(all(r.closed for r in responses))

    def test_unknown_location_and_invalid_date_never_request(self):
        with patch('forecast_runtime.activity_sources.requests.get') as get:
            self.assertEqual(self.reader.fetch('other', '2026-10-17')['state'], 'unavailable')
            self.assertEqual(self.reader.fetch('zhujiajian', '2026-19-01')['reason'], 'invalid_query_date')
        get.assert_not_called()

    def test_explicit_refresh_bypasses_fresh_cache(self):
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=self.response) as get:
            self.reader.fetch('zhujiajian', '2026-10-17')
            value = self.reader.fetch('zhujiajian', '2026-10-17', refresh=True)
        self.assertEqual(get.call_count, 8)
        self.assertFalse(value['cached'])

    def test_discovered_new_notice_and_matching_site_required(self):
        notice_url = 'https://www.zhujiajian.com/art/2026/10/17/art_2247_99999.html'
        def response(url, **kwargs):
            if url == OPERATOR_INDEX_URL:
                return Response(url, f'<a href="{notice_url}">南沙游泳暂停公告</a>')
            if url == notice_url:
                return Response(url, ActivityParserTests.notice('2026年10月17日至10月18日，南沙景区暂停游泳。'))
            return self.response(url, **kwargs)
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=response):
            nansha = self.reader.fetch('zhujiajian', '2026-10-17', site_name='南沙')
            dongsha = self.reader.fetch('zhujiajian', '2026-10-17', site_name='东沙')
            regional = self.reader.fetch('zhujiajian', '2026-10-17')
        self.assertEqual(nansha['activity_permission']['swimming'], 'verified_closed')
        self.assertEqual(dongsha['activity_permission']['swimming'], 'unverified')
        self.assertEqual(regional['activity_permission']['swimming'], 'unverified')
        self.assertTrue(all(row['site_id'] == 'nansha' for row in regional['activity_evidence']))

    def test_later_notice_supersedes_old_permit_and_failed_discovery_cannot_grant(self):
        old_url = 'https://www.zhujiajian.com/art/2026/10/15/art_2247_99997.html'
        new_url = 'https://www.zhujiajian.com/art/2026/10/17/art_2247_99998.html'
        def response(url, **kwargs):
            if url == OPERATOR_INDEX_URL:
                return Response(url, f'<a href="{old_url}">南沙游泳开放公告</a><a href="{new_url}">南沙游泳暂停公告</a>')
            if url == old_url:
                return Response(url, ActivityParserTests.notice('2026年10月15日至10月20日南沙景区允许游泳。', '2026-10-15'))
            if url == new_url:
                return Response(url, ActivityParserTests.notice('南沙景区自2026年10月17日起暂停游泳，恢复另行通知。'))
            return self.response(url, **kwargs)
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=response):
            value = self.reader.fetch('zhujiajian', '2026-10-17', site_name='南沙')
        self.assertEqual(value['activity_permission']['swimming'], 'unverified')
        self.assertEqual(value['activity_evidence'], [])
        self.assertTrue(value['unresolved_permissions'])

    def test_changed_empty_official_index_cannot_certify_permissions(self):
        def response(url, **kwargs):
            if url == OPERATOR_INDEX_URL:
                return Response(url, '<html>网页模板已变化</html>')
            return self.response(url, **kwargs)
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=response):
            value = self.reader.fetch('zhujiajian', '2026-10-17', site_name='南沙')
        self.assertFalse(value['discovery_complete'])
        self.assertEqual(value['state'], 'partial')
        self.assertEqual(value['activity_evidence'], [])

    def test_bounded_newer_closure_supersedes_old_opening(self):
        old_url = 'https://www.zhujiajian.com/art/2026/10/15/art_2247_99997.html'
        new_url = 'https://www.zhujiajian.com/art/2026/10/17/art_2247_99998.html'
        def response(url, **kwargs):
            if url == OPERATOR_INDEX_URL:
                return Response(url, f'<a href="{old_url}">南沙游泳开放公告</a><a href="{new_url}">南沙游泳暂停公告</a>')
            if url == old_url:
                return Response(url, ActivityParserTests.notice('2026年10月15日至10月20日南沙景区允许游泳。', '2026-10-15'))
            if url == new_url:
                return Response(url, ActivityParserTests.notice('2026年10月17日至10月18日南沙景区暂停游泳。'))
            return self.response(url, **kwargs)
        with patch('forecast_runtime.activity_sources.local_now', return_value=self.now), \
             patch('forecast_runtime.activity_sources.requests.get', side_effect=response):
            value = self.reader.fetch('zhujiajian', '2026-10-17', site_name='南沙')
        self.assertEqual(value['activity_permission']['swimming'], 'verified_closed')
        self.assertTrue(all(row['state'] == 'verified_closed' for row in value['activity_evidence']))
        self.assertEqual(len(value['notice_evidence']), 2)


def urlsplit_for_test(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname


if __name__ == '__main__':
    unittest.main()
