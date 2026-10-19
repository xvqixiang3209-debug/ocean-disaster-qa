"""Publication state contracts; synthetic fixtures never enter the real archive."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import copy
import unittest

from flask import Flask
from forecast_runtime.storage import ForecastStore, BEIJING
from forecast_runtime.official_warnings import OfficialWarnings, normalize_notice, query_spec, warning_question, warning_table, answer_warnings
from forecast_runtime.service import ForecastRuntime
from forecast_runtime.api import create_blueprint

NOW=datetime(2026,10,19,10,tzinfo=BEIJING)

def notice(ident='one', date='2026-10-17 08:00:00', body=None, level='黄色', title='风暴潮警报', code='cold-01'):
    return dict(id=ident,alarmDate=date,updateDate=date,title=title,subtitle='',code=code,level=level,
                orgUnit='国家海洋预报台',description=body or '<p>预计：10月17日上午到18日上午，河北秦皇岛沿海将出现40到80厘米的风暴增水。</p><p>河北省秦皇岛市的风暴潮预警级别为黄色。</p><p>48小时预报：18日中午到傍晚，河北省秦皇岛市的风暴潮预警级别为蓝色。</p>')

def envelope(*items):return {'success':True,'obj':{'day':list(items)}}

class OfficialWarningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ForecastStore(Path(self.tmp.name))
        self.archive=OfficialWarnings(self.store)
        self.archive.ensure_year=lambda *a,**kw:None
        self.clock=patch('forecast_runtime.official_warnings.local_now',return_value=NOW)
        self.clock.start();self.addCleanup(self.clock.stop)

    def ingest(self,*items, fetched_at='2026-10-19T09:00:00+08:00'):
        return self.archive.ingest('nmefc','storm:1',2026,envelope(*items),fetched_at=fetched_at)

    def test_horizon_does_not_mix_local_levels(self):
        self.ingest(notice())
        result=self.archive.query('截至2026年10月17日12时，秦皇岛有哪些风暴潮警报？')
        self.assertEqual(result['total'],1)
        row=result['items'][0]
        self.assertEqual(row['matched_levels'],['黄色'])
        self.assertEqual(row['forecasts'][1]['areas'][0]['level'],'蓝色')
        self.assertNotIn('蓝色',warning_table(result))

    def test_standalone_level_binds_preceding_coastal_sentence(self):
        self.ingest(notice(body='预计：10月17日上午至18日上午，浙江宁波到福建漳州沿海将出现50到120厘米的风暴增水。风暴潮预警级别为蓝色。'))
        result=self.archive.query('2026年10月17日浙江有哪些风暴潮警报？')
        self.assertEqual(result['items'][0]['matched_levels'],['蓝色'])

    def test_table_header_unit_is_retained_with_values(self):
        item=notice();item['tblCon']=[[
            ['影响地区','预警级别','参考站','日期','高潮时','高潮值（cm）','警戒潮位*（cm）'],
            ['河北秦皇岛','黄色','秦皇岛','17日','12:00','126','126（黄色）']]]
        self.ingest(item)
        table=warning_table(self.archive.query('2026年10月17日秦皇岛有哪些风暴潮警报？'))
        self.assertIn('高潮值（cm）：126 cm',table)

    def test_old_cancellations_not_current_ledger_rows(self):
        self.ingest(notice('old','2026-09-06 15:00:00',body='目前河北省秦皇岛市风暴潮影响减弱，国家海洋预报台发布解除通报。',title='风暴潮解除通报'))
        self.assertEqual(self.archive.query('2026年10月17日秦皇岛风暴潮警报解除了吗？')['total'],0)

    def test_deepseek_generation_metadata_and_complete_ledger_retained(self):
        self.ingest(notice())
        runtime=ForecastRuntime(Path(self.tmp.name)/'runtime');runtime.warnings=self.archive
        class QA:
            external_enabled=True;model_caller=True;model_name='configured-model'
            def _public_sources(self,question,hits):return [{'metadata':hits[0]['metadata']}]
            def _organized_answer(self,*args):return {'answer':'秦皇岛为黄色警报[资料1]。','answer_meta':{'used_mode':'llm','generation_used':True}}
        result=answer_warnings(runtime,'2026年10月17日秦皇岛有哪些风暴潮警报？',QA(),{'used_mode':'local_extractive'})
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertIn('本次匹配公告清单',result['answer'])
        self.assertFalse(result['answer_meta']['business_trial'])

    def test_local_level_not_global_title_level(self):
        item=notice(level='橙色',body='预计：10月17日上午至18日上午，浙江省温州市的风暴潮预警级别为橙色。浙江省宁波市、舟山市的风暴潮预警级别为蓝色。')
        self.ingest(item)
        self.assertEqual(self.archive.query('2026年10月17日宁波有哪些风暴潮警报？')['items'][0]['matched_levels'],['蓝色'])
        self.assertEqual(self.archive.query('2026年10月17日温州有哪些风暴潮警报？')['items'][0]['matched_levels'],['橙色'])

    def test_future_release_not_visible_at_noon(self):
        self.ingest(notice(),notice('two','2026-10-17 16:00:00',level='蓝色',body='预计：10月17日下午到18日上午，河北秦皇岛沿海将出现40到80厘米的风暴增水。河北省秦皇岛市的风暴潮预警级别为蓝色。',code='cold-02'))
        a=self.archive.query('截至2026年10月17日12时秦皇岛有哪些风暴潮警报？')
        b=self.archive.query('截至2026年10月17日20时秦皇岛有哪些风暴潮警报？')
        self.assertEqual(a['items'][0]['matched_levels'],['黄色'])
        self.assertEqual(b['items'][0]['matched_levels'],['蓝色'])
        self.assertEqual(self.archive.query('截至2026年10月17日20时秦皇岛发布了哪些风暴潮警报？')['total'],1)

    def test_specific_publication_reference_not_an_asof_clock(self):
        item=notice(code='261016-02');item['updateDate']='2026-10-17 08:03:00';self.ingest(item)
        question='国家海洋预报台2026年10月17日08:00发布的秦皇岛风暴潮黄色警报（编号261016-02）覆盖哪里？什么时候发布的？'
        self.assertTrue(warning_question(question))
        self.assertEqual(self.archive.query(question)['total'],1)

    def test_new_scope_replaces_old_scope_before_region_filter(self):
        self.ingest(notice(),notice('two','2026-10-17 16:00:00',body='预计：10月17日下午到18日上午，浙江省温州市沿海有风暴潮增水。浙江省温州市的风暴潮预警级别为蓝色。',code='cold-02'))
        self.assertEqual(self.archive.query('截至2026年10月17日20时秦皇岛有哪些风暴潮警报？')['total'],0)

    def test_planned_and_actual_cancellation(self):
        item=notice(body='预计：10月17日上午到18日上午，河北秦皇岛沿海将出现40到80厘米的风暴增水。河北省秦皇岛市的风暴潮预警级别为黄色。预计明日下午视情况解除警报。')
        self.ingest(item)
        self.assertFalse(self.archive.query('2026年10月17日秦皇岛有哪些风暴潮警报？')['items'][0]['is_cancellation'])
        self.ingest(notice('end','2026-10-18 16:00:00',body='目前河北省秦皇岛市沿海风暴潮影响已经减弱，本台发布风暴潮解除通报。',title='风暴潮解除通报',code='cold-03'))
        self.assertEqual(self.archive.query('2026年10月18日秦皇岛有哪些风暴潮警报？')['total'],0)
        result=self.archive.query('2026年10月18日秦皇岛风暴潮警报是否解除？')
        self.assertTrue(result['items'][0]['is_cancellation'])

    def test_publication_range_keeps_every_update(self):
        self.ingest(notice(),notice('two','2026-10-17 16:00:00',code='cold-02'))
        self.assertEqual(self.archive.query('2026年10月17日秦皇岛发布了哪些风暴潮警报？')['total'],2)

    def test_body_month_conflict_quarantined(self):
        self.ingest(notice(body='预计：8月21日至25日，河北秦皇岛沿海将出现40到80厘米的风暴增水。河北省秦皇岛市的风暴潮预警级别为黄色。'))
        result=self.archive.query('2026年10月17日秦皇岛有哪些风暴潮警报？')
        self.assertEqual(result['total'],0);self.assertEqual(len(result['date_conflicts']),1)

    def test_versions_are_immutable(self):
        self.ingest(notice())
        revised=notice();revised['description']+='原文追加订正说明。'
        self.ingest(revised,fetched_at='2026-10-19T09:30:00+08:00')
        self.ingest(revised,fetched_at='2026-10-19T09:40:00+08:00')
        self.assertEqual(self.archive.status()['version_count'],2)
        self.assertIn('追加订正',self.archive.query('2026年10月17日秦皇岛有哪些风暴潮警报？')['items'][0]['body'])

    def test_later_revised_body_cannot_backfill_noon(self):
        item=notice();item['updateDate']='2026-10-17 16:00:00';self.ingest(item)
        self.assertEqual(self.archive.query('截至2026年10月17日12时秦皇岛有哪些风暴潮警报？')['total'],0)

    def test_failure_not_zero_warning_claim(self):
        self.archive._failure('zhejiang','storm',2026,'zhejiang_service_token_missing')
        result=self.archive.query('2026年10月17日浙江有哪些风暴潮警报？')
        self.assertFalse(result['coverage_complete']);self.assertEqual(result['state'],'partial')
        self.assertIn('不等于',result['empty_meaning'])
        result=self.archive.query('2026年10月17日秦皇岛有哪些风暴潮警报？')
        self.assertFalse(result['coverage_complete'])
        self.assertNotIn('浙江',''.join(result['missing_information']))
        self.assertTrue(result['missing_information'])

    def test_no_implicit_active_lifetime_for_undated_body(self):
        self.ingest(notice(body='河北省秦皇岛市的风暴潮预警级别为黄色，请有关部门做好防范工作。'))
        self.assertEqual(self.archive.query('2026年10月18日秦皇岛有哪些风暴潮警报？')['total'],0)

    def test_unknown_region_not_replaced_by_province(self):
        self.ingest(notice())
        self.assertEqual(self.archive.query('2026年10月17日嵊泗县有哪些风暴潮警报？')['total'],0)
        result=self.archive.query('2026年10月17日舟山朱家尖有哪些风暴潮警报？')
        self.assertEqual(result['query']['regions'],['朱家尖']);self.assertEqual(result['total'],0)

    def test_new_year_fetches_previous_year_for_carryover(self):
        calls=[];self.archive.ensure_year=lambda year,**kw:calls.append(year)
        self.archive.query('截至2026年1月1日12时浙江有哪些海浪警报？')
        self.assertEqual(calls,[2025,2026])

    def test_route_criteria_remain_knowledge_questions(self):
        for question in ['海浪预警分级标准是什么？','浙江风暴潮预警发布条件是什么？','假如浙江发布海浪橙色警报怎么办？','浙江海浪预警命中率是多少？']:
            self.assertFalse(warning_question(question),question)
        self.assertTrue(warning_question('截至2026年10月17日浙江已发布哪些海洋灾害预警？'))
        self.assertTrue(warning_question('2026年7月10日浙江发布了哪些海浪和风暴潮预警？各是什么等级，何时发布，覆盖哪些地区？'))
        self.assertFalse(warning_question('海浪预警何时发布？'))

    def test_dates_and_range_validation(self):
        with self.assertRaises(ValueError):query_spec('2026年10月20日浙江有哪些海浪预警？',NOW)
        with self.assertRaises(ValueError):query_spec('2026年8月1日至2026年10月17日浙江海浪预警发布记录',NOW)
        spec=query_spec('截至2026年10月17日12时30分，秦皇岛有哪些风暴潮预警？',NOW)
        self.assertEqual(spec['as_of'],'2026-10-17T12:30:00+08:00')
        self.assertEqual(query_spec('截至2026年10月17日下午3时秦皇岛海洋预警',NOW)['as_of'],'2026-10-17T15:00:00+08:00')
        self.assertEqual(query_spec('截至2026-10-17T12:30秦皇岛海洋预警',NOW)['as_of'],'2026-10-17T12:30:00+08:00')
        spec=query_spec('2026年10月17日至18日秦皇岛风暴潮预警发布记录',NOW)
        self.assertEqual(spec['mode'],'published');self.assertEqual(spec['end'],'2026-10-18T23:59:59+08:00')

    def test_information_not_a_color_warning(self):
        item=notice(title='海啸信息',level='信息',body='时间：2026年10月17日07时32分。墨西哥格雷罗州海域发生地震，不会对我国沿岸造成影响。自然资源部海啸预警中心将继续跟踪。')
        row=normalize_notice(item,'nmefc','bore','bore:','2026-10-19T09:00:00+08:00','snapshot')
        self.assertEqual(row['published_at'],'2026-10-17T07:32:00+08:00')
        self.assertTrue(row['is_information']);self.assertEqual(row['level'],'信息')
        self.archive.ingest('nmefc','bore:',2026,envelope(item))
        self.assertEqual(self.archive.query('2026年10月17日国家海洋预报台有哪些海啸警报？')['total'],0)
        self.assertEqual(self.archive.query('2026年10月17日国家海洋预报台海啸信息发布记录')['total'],1)

    def test_untrusted_original_url_rejected(self):
        with self.assertRaises(ValueError):self.archive._original_body({'htmlUrl':'https://example.com/body.html'})

    def test_original_body_cache_invalidates_on_list_revision(self):
        raw={'htmlUrl':'https://obs.nmefc.cn/Warning/TsunamiAdvice/test/body.html','updateDate':'2026-10-19 08:00:00'}
        class Response:
            status_code=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,size):yield '<p>海啸信息：官方测试正文；不会对我国沿岸造成影响。</p>'.encode()
        with patch('forecast_runtime.official_warnings.requests.get',return_value=Response()) as get:
            self.archive._original_body(raw);self.archive._original_body(raw)
            self.assertEqual(get.call_count,1)
            self.archive._original_body({**raw,'updateDate':'2026-10-19 09:00:00'})
            self.assertEqual(get.call_count,2)

    def test_api_and_route_ignore_selected_typhoon(self):
        runtime=ForecastRuntime(Path(self.tmp.name)/'runtime');runtime.warnings=self.archive
        self.ingest(notice())
        question='2026年10月17日秦皇岛有哪些风暴潮警报？'
        self.assertTrue(runtime.matches(question,sid='201909'))
        self.assertEqual(runtime.query(question)['kind'],'official_warnings')
        app=Flask(__name__);app.register_blueprint(create_blueprint(runtime))
        with app.test_client() as client:
            response=client.get('/api/forecast/warnings',query_string={'question':question})
            self.assertEqual(response.status_code,200);data=response.get_json()
            self.assertFalse(data['trial']);self.assertEqual(data['total'],1)
            self.assertEqual(client.get('/api/forecast/warnings/notices/'+data['items'][0]['id']).get_json()['version_count'],1)
            self.assertEqual(client.get('/api/forecast/warnings',query_string={'date':'2026-10-20'}).status_code,400)

