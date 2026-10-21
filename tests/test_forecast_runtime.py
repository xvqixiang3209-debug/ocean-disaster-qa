"""Meaningful numerical/contract tests, using synthetic grids and no network."""
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np
from flask import Flask

from forecast_runtime.api import create_blueprint
from forecast_runtime.grids import inspect_grid,GridReader
from forecast_runtime.service import ForecastRuntime,affine,period
from forecast_runtime.storage import BEIJING,read_sql


def grid_file(path,variables,unit='m',time_units='hours since 2026-10-16 20:00:00'):
    with h5py.File(path,'w') as nc:
        nc.create_dataset('lon',data=[121.0,121.05,121.1])
        nc.create_dataset('lat',data=[28.0,28.05,28.1])
        t=nc.create_dataset('time',data=[1,2,3]);t.attrs['units']=time_units
        for name,values in variables.items():
            data=np.full((3,3,3),values,dtype=np.float32) if np.isscalar(values) else np.asarray(values,dtype=np.float32)
            ds=nc.create_dataset(name,data=data);ds.attrs['unit']=unit


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.r=ForecastRuntime(self.root)
        self.now=datetime(2026,10,16,21,tzinfo=BEIJING)
        self.start=self.now;self.end=self.now+timedelta(hours=2)
        self.coast={'Id':1,'name':'测试岸段','Prefecture':'台州市','County':'测试县','lon':121.05,'lat':28.05,
                    'thresholds_cm':{'blue':100,'yellow':200,'orange':None,'red':400},'geometry':None,
                    'NumStation':'0001','ObsOriStid':None,'FcstStation':'0001','Formula':None}
        self.profile={'code':'TEST','station_id':'0001','name':'测试站','station_name':'测试站','state':'ready',
                      'base85_cm':100,'lon':121.05,'lat':28.05,'mapping':'datafile_exact','filename':'TEST2026'}
        with closing(self.r.store.connect()) as db,db:
            for kind,key,payload in [('site','0001',{'StationID':'0001'}),('coast','1',self.coast)]:
                db.execute('INSERT INTO entities VALUES (?,?,?)',(kind,key,json.dumps(payload)))
            db.execute('INSERT INTO tide_files VALUES (?,?)',('TEST',json.dumps(self.profile)))
            for i,value in enumerate([300,100,200]):db.execute('INSERT INTO tide_hours VALUES (?,?,?)',('TEST',(self.start+timedelta(hours=i)).isoformat(),value))
        path=self.root/'PZJ_BEN_OSM_ZJS_202610162000_SWL_00_L2.nc'
        a=np.stack([np.full((3,3),v) for v in [1,3,.5]])
        grid_file(path,{'SWL':a});self.record=inspect_grid(path);self.r.store.register_grid(self.record)

    def config(self,**changes):
        data=json.loads(self.r.config_path.read_text(encoding='utf-8'));data.update(changes)
        self.r.config_path.write_text(json.dumps(data),encoding='utf-8')

    def coastal(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):return self.r.coastal(self.start,self.end)

    def test_hourly_alignment_not_sum_of_unrelated_maxima(self):
        result=self.coastal();row=result['results'][0]
        self.assertEqual(row['maximum']['total_cm'],400)
        self.assertNotEqual(row['maximum']['total_cm'],600)
        self.assertEqual(row['level'],'red')
        self.assertTrue(result['trial'])

    def test_total_water_level_never_adds_astronomical_tide(self):
        self.config(swl_kind='total_water_level')
        result=self.coastal();self.assertEqual(result['results'][0]['maximum']['total_cm'],300)
        self.assertIn('不再次叠加',self.r.summary(result))

    def test_missing_tide_is_not_zero(self):
        with closing(self.r.store.connect()) as db,db:db.execute('DELETE FROM tide_hours')
        result=self.coastal();self.assertEqual(result['usable_count'],0);self.assertEqual(result['missing_count'],1)

    def test_invalid_intermediate_threshold_not_invented(self):
        self.coast['thresholds_cm']['red']=1000
        with closing(self.r.store.connect()) as db,db:db.execute('UPDATE entities SET payload=? WHERE kind=?',(json.dumps(self.coast),'coast'))
        self.assertEqual(self.coastal()['results'][0]['level'],'yellow')

    def test_missing_reference_can_still_use_total_water_level(self):
        self.config(swl_kind='total_water_level',nearest_reference_enabled=False)
        with closing(self.r.store.connect()) as db,db:db.execute('DELETE FROM tide_files')
        self.assertEqual(self.coastal()['usable_count'],1)

    def test_archived_valid_forecast_does_not_expire_with_execution_clock(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now+timedelta(days=2)):
            result=self.r.coastal(self.start,self.end)
        self.assertEqual(result['usable_count'],1)
        row=result['results'][0]
        self.assertEqual(row['batch_age_hours'],1)
        self.assertEqual(row['batch_age_at_query_hours'],49)

    def test_forecast_old_at_requested_start_is_still_rejected(self):
        self.config(max_batch_age_hours=.5)
        self.assertEqual(self.coastal()['usable_count'],0)

    def test_future_forecast_checks_freshness_at_now_not_future_start(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            value=self.r.grid('surge',121.05,28.05,self.start,self.end,freshness_at=self.now+timedelta(days=3))
        self.assertEqual(value['batch_age_hours'],1);self.assertFalse(value['stale'])

    def test_coastal_acquisition_once_and_historical_cutoff_forwarded(self):
        self.r.coastal_acquisition=True
        self.r._local.request_cutoff=self.now
        try:
            with patch.object(self.r.acquisition,'ensure',return_value={'state':'ready'}) as acquire:
                result=self.coastal()
            acquire.assert_called_once_with(self.start,self.end,products=['SWL'],as_of=self.now)
            self.assertEqual(result['data_acquisition']['state'],'ready')
        finally:del self.r._local.request_cutoff

    def test_zhejiang_region_selects_registered_zhejiang_cities(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            value=self.r.coastal(self.start,self.end,'浙江省')
        self.assertEqual(value['requested_count'],1);self.assertEqual(value['usable_count'],1)

    def test_surge_only_province_query_routes_to_coastal(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            value=self.r.query('2026年10月16日21时至23时浙江沿海预报增水有多大？')
        self.assertEqual(value['kind'],'coastal');self.assertTrue(value['requested_metrics']['surge'])

    def test_province_query_not_narrowed_by_coast_in_another_subquestion(self):
        other=dict(self.coast,Id=2,name='第二岸段')
        with closing(self.r.store.connect()) as db,db:db.execute('INSERT INTO entities VALUES (?,?,?)',('coast','2',json.dumps(other)))
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            value=self.r.query('2026年10月16日21时测试岸段和浙江沿海最高潮位多少？',_only='coastal')
        self.assertEqual(value['requested_count'],2)

    def test_missing_tide_does_not_discard_independent_swl_peak(self):
        from forecast_runtime.answer_facts import delivery_facts
        with closing(self.r.store.connect()) as db,db:db.execute('DELETE FROM tide_hours')
        value=self.coastal();facts=delivery_facts(value)
        self.assertEqual(value['usable_count'],0);self.assertEqual(value['swl_usable_count'],1)
        self.assertEqual(value['state'],'partial')
        self.assertEqual(next(f for f in facts if f.get('metric')=='最大风暴增水')['value'],300)
        self.assertFalse(any(f.get('metric')=='最高总潮位' for f in facts))

    def test_same_issue_resolution_fallback_never_uses_older_issue(self):
        high=dict(self.record,id='high',level='L5');self.r.store.register_grid(high)
        original=self.r.parsed.point
        def read(record,*args,**kwargs):
            return {'state':'missing_grid','series':[]} if record['id']=='high' else original(record,*args,**kwargs)
        with patch.object(self.r.parsed,'point',side_effect=read):
            self.assertEqual(self.coastal()['usable_count'],1)

    def test_explicit_correction_can_be_changed_without_reimport(self):
        self.config(tide_datum='subtract_base85')
        result=self.r.tide('TEST',self.start,self.end)
        self.assertEqual(result['series'][0]['value_cm'],200)
        self.assertEqual(result['series'][0]['raw_value_cm'],300)
        self.config(station_tide_overrides={'TEST':'raw_already_1985'})
        self.assertEqual(self.r.tide('TEST',self.start,self.end)['series'][0]['value_cm'],300)

    def test_no_batch_splicing(self):
        old=dict(self.record,id='old',batch='2026-10-15T20:00:00+08:00')
        self.r.store.register_grid(old)
        value=self.r.grid('surge',121.05,28.05,self.start,self.end)
        self.assertEqual(value['batch'],self.record['batch'])

    def test_as_of_query_does_not_use_a_later_issue(self):
        newer=dict(self.record,id='not_yet_issued',batch='2026-10-17T22:00:00+08:00')
        self.r.store.register_grid(newer)
        with patch('forecast_runtime.service.local_now',return_value=self.now+timedelta(days=2)):
            value=self.r.query('截至2026年10月17日21时30分，查询2026年10月16日21时至10月16日23时浙江岸段最高潮位多少？')
        self.assertEqual(value['selected_batch'],self.record['batch'])
        self.assertFalse(hasattr(self.r._local,'request_cutoff'))

    def test_coast_comparison_does_not_fill_latest_batch_with_older_grid(self):
        newer=dict(self.record,id='newer',batch='2026-10-16T21:00:00+08:00',bounds=[130,28,131,29])
        self.r.store.register_grid(newer)
        result=self.coastal()
        self.assertEqual(result['selected_batch'],newer['batch'])
        self.assertEqual(result['usable_count'],0)

    def test_morning_request_is_not_replaced_with_evening_data(self):
        start,end=period('今天上午',now=self.now)
        self.assertEqual(start.hour,0);self.assertEqual(end.hour,11)

    def test_outside_spatial_bounds_has_no_values(self):
        self.assertEqual(self.r.grid('wave',150,28,self.start,self.end)['state'],'no_data')

    def test_coastal_formula_is_parsed_not_executed(self):
        self.assertEqual(affine('1.5x-10',100),140)
        with self.assertRaises(ValueError):affine('__import__("os").system("bad")',100)

    def test_short_date_and_72_hours_preserves_duration(self):
        start,end=period(date='2026-10-17',hours=72,now=self.now)
        self.assertEqual(end-start,timedelta(hours=71))
        self.assertEqual(start.hour,0)

    def test_tomorrow_and_explicit_date(self):
        start,end=period('明天',now=self.now)
        self.assertEqual(start.day,17);self.assertEqual(end.hour,23)
        start,_=period('2026年10月18日未来48小时',now=self.now);self.assertEqual(start.day,18)

    def test_definitions_and_historical_questions_keep_existing_route(self):
        for question in ['什么是风暴增水？','2019年利奇马最大增水多少？','应急响应如何终止？']:
            self.assertFalse(self.r.matches(question))
        self.assertTrue(self.r.matches('舟山未来72小时浪高多少？'))

    def test_normative_forecast_quantities_do_not_query_actual_grids(self):
        for question in [
            '按GB/T19721.5-2022，常规海温预报每日最低发布频次和时效分别是多少？',
            '按GB/T19721.6-2023，应急海流预报每日最低频次是多少？',
            '按GB/T19721.6-2023表A.1，西西北和北西北的角度范围是多少？',
            '按GB/T19721.2-2017，近岸海域海浪红色警报的波高条件是什么？',
            '按照海温预报规范，定点数值采用平均值还是瞬时值？',
        ]:
            self.assertFalse(self.r.matches(question), question)

    def test_reference_to_standard_does_not_hide_actual_forecast_sibling(self):
        for question in [
            '按GB/T19721.2-2017解释有效波高，再查询舟山今天浪高多少？',
            '按GB/T19721.5-2022说明时效，并查询2026年10月18日朱家尖海温多少？',
            '依据海浪预报标准，提取NC网格东经122度北纬30度的波高。',
        ]:
            self.assertTrue(self.r.matches(question), question)

    def test_other_business_domains_cannot_be_replaced_with_forecast_numbers(self):
        for question in ['哪些岸段会淹没，开始进水潮位多少？','50年一遇潮位多少？','预报误差有多大？','台风强度等级有哪些？','台风中心过境时风小了，可以出去看看吗？','我市哪些岸段是高风险区？']:
            self.assertFalse(self.r.matches(question),question)

    def test_changed_assumptions_invalidate_prior_answer_context(self):
        source={'metadata':{'assumption_id':self.r.config_id()}}
        self.assertTrue(self.r.evidence_allowed(source));self.config(swl_kind='total_water_level')
        self.assertFalse(self.r.evidence_allowed(source))

    def test_read_only_api_has_no_local_paths_or_credentials_in_status(self):
        app=Flask(__name__);app.register_blueprint(create_blueprint(self.r));client=app.test_client()
        response=client.get('/api/forecast/status');text=response.get_data(as_text=True)
        self.assertEqual(response.status_code,200);self.assertNotIn(str(self.root),text)
        self.assertNotIn('ZJOCEAN_GRID_SALT',text)
        self.assertEqual(client.post('/api/forecast/status').status_code,405)
        self.assertEqual(client.get('/api/forecast/tide?station=TEST&date=2026-10-16').status_code,200)
        self.assertEqual(client.get('/api/forecast/tide?station=unknown').status_code,400)

    def test_vector_wind_magnitude_and_fill_values(self):
        path=self.root/'PZJ_BEN_OSM_ZJS_202610162000_SSW_00_L2.nc'
        grid_file(path,{'u10':3,'v10':4},unit='m/s');record=inspect_grid(path)
        value=self.r.reader.point(record,'SSW',121.05,28.05,self.start.isoformat(),self.end.isoformat())
        self.assertEqual(value['series'][0]['value'],5)

    def test_land_missing_grid_uses_bounded_nearby_valid_cell(self):
        path=self.root/'PZJ_BEN_OSM_ZJS_202610162000_WAV_00_L2.nc'
        data=np.ones((3,3,3));data[:,1,1]=-9999;grid_file(path,{'WAV':data})
        value=self.r.reader.point(inspect_grid(path),'WAV',121.05,28.05,self.start.isoformat(),self.end.isoformat())
        self.assertGreater(value['distance_km'],0);self.assertEqual(len(value['series']),3)

    def test_filename_time_fallback_is_explicit_assumption(self):
        path=self.root/'PZJ_BEN_OSM_ZJS_202610162000_WAV_00_L2.nc'
        grid_file(path,{'WAV':1},time_units='hours since initial time')
        record=inspect_grid(path)
        self.assertEqual(record['time_origin'],'filename_assumed')
        self.assertEqual(record['times'][0],'2026-10-16T21:00:00+08:00')

    def test_sql_dump_never_executes_destructive_statements(self):
        path=self.root/'dump.sql'
        path.write_text("DROP TABLE IF EXISTS `m`;\nCREATE TABLE `m` (\n `id` varchar(10),\n `name` text\n) ENGINE=InnoDB;\nINSERT INTO `m` VALUES ('0001', 'a,b');\n",encoding='utf-8')
        self.assertEqual(read_sql(path,'m'),[{'id':'0001','name':'a,b'}])

    def test_snapshot_refresh_error_keeps_cache_marked_stale(self):
        self.r.store.state('typhoons',{'retrieved_at':self.now.isoformat(),'list':[{'nameCN':'测试台风'}]})
        with patch('forecast_runtime.sync.requests.post',side_effect=TimeoutError):
            value=self.r.sync.typhoons(refresh=True)
        self.assertTrue(value['cache_stale']);self.assertEqual(value['list'][0]['nameCN'],'测试台风')

    def test_activity_response_without_state_key_is_a_valid_query(self):
        with patch.object(self.r.sync,'typhoons',return_value={'list':[],'cache_stale':False,'retrieved_at':self.now.isoformat()}):
            value=self.r.query('现在有几个台风在活动？')
        self.assertEqual(value['state'],'no_active_typhoons')
        self.assertEqual(value['start'],value['end'])
        self.assertIn('活动台风数',self.r.summary(value))

    def test_incompatible_vertical_datums_are_not_silently_combined(self):
        self.config(threshold_datum='MSL')
        with self.assertRaisesRegex(ValueError,'1985统一基准'):self.r.coastal(self.start,self.end)

    def test_one_query_freezes_assumptions_even_if_config_file_changes(self):
        original=self.r.reference
        def change_config(*args):
            self.config(swl_kind='total_water_level')
            return original(*args)
        with patch.object(self.r,'reference',side_effect=change_config):
            result=self.coastal()
        self.assertEqual(result['swl_kind'],'surge')
        self.assertEqual(result['results'][0]['maximum']['total_cm'],400)
        self.assertEqual(self.r.config()['swl_kind'],'total_water_level')

    def test_historical_typhoon_identity_cannot_route_to_live_api(self):
        self.assertFalse(self.r.matches('2019年利奇马台风最新位置是什么？'))

    def test_compound_question_queries_tide_and_wave_independently(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            result=self.r.query('TEST未来3小时天文潮和浪高各是多少？')
        self.assertEqual(result['kind'],'bundle')
        self.assertEqual(result['items'][0]['kind'],'tide')
        self.assertEqual(result['items'][0]['state'],'ready')
        self.assertEqual(result['items'][1]['kind'],'grids')
        self.assertEqual(result['state'],'ready')
