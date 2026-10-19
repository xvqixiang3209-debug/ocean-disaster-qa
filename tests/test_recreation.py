"""An activity query must retrieve real series and keep permission separate."""
from datetime import timedelta
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock,patch
import unittest

from forecast_runtime.recreation import activity_question,resolve_location,evaluate_activities
from forecast_runtime.grids import inspect_grid
import test_forecast_runtime as fixture
from marine_kb.conversation import followup_question


class ActivityTests(unittest.TestCase):
    def setUp(self):
        base=fixture.ForecastTests();base.setUp();self.addCleanup(base.doCleanups)
        self.r,self.root,self.now,self.start,self.end=base.r,base.root,base.now,base.start,base.end
        for product,variables,unit in [('WAV',{'WAV':.2},'m'),('SSW',{'u10':2,'v10':3},'m/s'),
            ('OCU',{'u':.1,'v':.2},'m/s'),('SST',{'SST':24},'Celsis D')]:
            path=self.root/('PZJ_BEN_OSM_ZJS_202610162000_'+product+'_00_L2.nc')
            fixture.grid_file(path,variables,unit=unit);self.r.store.register_grid(inspect_grid(path))
        self.r.acquisition=Mock();self.r.acquisition.ensure.return_value={'state':'ready'}
        self.context={'state':'available','sources':[],'opening_hours':[
            {'site':'测试景区','start_time':'08:00','end_time':'18:00'}],
            'water_quality':{'state':'historical','observed_at':'2026-09-28','water_quality_grade':'优'},
            'activity_permission':dict.fromkeys(['tidal_flat','swimming','shore_fishing'],'unverified')}
        self.r.activity_sources=Mock();self.r.activity_sources.fetch.return_value=self.context

    def test_exact_activity_phrase_routes_before_safe_question_exclusions(self):
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            self.assertTrue(self.r.matches('2026年10月17日还能去舟山朱家尖海边赶海、游泳、海钓吗？'))
            self.assertTrue(self.r.matches('今天朱家尖游泳是否安全？'))
        self.assertFalse(activity_question('离岸流是什么，游泳如何自救？'))

    def test_nc_extracted_persisted_and_historical_quality_does_not_grant_permission(self):
        locations={'zhujiajian':{'name':'测试参考点','aliases':['朱家尖','南沙'],'lon':121.05,'lat':28.05,
            'coast_id':'1','coordinate_status':'configured_regional_sample_not_beach_survey'}}
        with patch('forecast_runtime.service.local_now',return_value=self.now),\
             patch('forecast_runtime.recreation.local_now',return_value=self.now),\
             patch('forecast_runtime.recreation.LOCATIONS',locations):
            result=self.r.query('2026年10月16日21时未来3小时朱家尖赶海、游泳、海钓可以吗？')
        self.assertEqual(result['kind'],'activity');self.assertEqual(result['state'],'ready')
        self.assertEqual({v['product'] for v in result['products']},{'WAV','SSW','OCU','SST','SWL'})
        self.assertTrue(all(v.get('series') for v in result['products']))
        self.assertTrue(result['tide']['series'])
        self.assertEqual(result['permission_state'],'not_established')
        self.assertTrue(all(v['state']=='insufficient_evidence' for v in result['activity_assessments']))
        self.assertEqual([v['numerical_screening']['state'] for v in result['activity_assessments']],
                         ['not_evaluated','insufficient','not_evaluated'])
        self.assertFalse(result['activity_assessments'][0]['candidate_windows'])
        self.assertEqual(result['activity_rule_mode'],'official_catalog')
        swimming=next(v for v in result['activity_assessments'] if v['activity']=='swimming')
        self.assertTrue(any('水质' in text for text in swimming['missing_conditions']))
        with self.r.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM grid_point_series').fetchone()[0],5)
        self.r.acquisition.ensure.assert_called_once()

    def test_bare_nansha_is_not_silently_geocoded_to_zhujiajian(self):
        self.assertIsNone(resolve_location(self.r,'广州南沙今天能游泳吗？'))
        self.assertIsNone(resolve_location(self.r,'今天南沙能游泳吗？'))
        point=resolve_location(self.r,'舟山朱家尖东经121.05度北纬28.05度今天能游泳吗？')
        self.assertEqual((point['lon'],point['lat']),(121.05,28.05))
        self.assertEqual(point['coordinate_status'],'user_supplied')
        self.assertTrue(point['coordinate_conflict']);self.assertNotIn('coast_id',point)

    def test_cross_day_tide_does_not_reuse_first_day_admission_schedule(self):
        context={**self.context,'query_date':'2026-10-31'}
        tide={'state':'ready','low_tides':[{'time':'2026-11-01T17:00:00+08:00','value_cm':100}]}
        result=evaluate_activities('赶海可以吗？',[],tide,context,{'coordinate_status':'user_supplied'})
        low=next(v for v in result[0]['numeric_basis'] if 'reference_low_tide' in v)
        self.assertFalse(low['admission_context_available']);self.assertEqual(low['admission_sites_at_that_time'],[])

    def test_activity_and_coastal_subquestions_are_both_queried(self):
        with patch('forecast_runtime.service.query_activity',return_value={'kind':'activity','state':'ready'}),\
             patch.object(self.r,'coastal',return_value={'kind':'coastal','state':'ready'}) as coastal:
            result=self.r.query('今天朱家尖能游泳吗，哪些岸段超警？')
        self.assertEqual([v['kind'] for v in result['items']],['activity','coastal'])
        coastal.assert_called_once()

    def test_tide_followup_uses_the_known_place_reference(self):
        locations={'zhujiajian':{'name':'测试参考点','aliases':['朱家尖','南沙'],'lon':121.05,'lat':28.05,
            'coast_id':'1','coordinate_status':'configured_regional_sample_not_beach_survey'}}
        with patch('forecast_runtime.service.local_now',return_value=self.now),\
             patch('forecast_runtime.recreation.LOCATIONS',locations):
            question='2026年10月16日21时未来3小时朱家尖什么时候退潮？'
            self.assertTrue(self.r.matches(question));result=self.r.query(question)
        self.assertEqual(result['kind'],'tide');self.assertEqual(result['site']['code'],'TEST')
        self.assertEqual(len(result['series']),3)

    def test_time_and_marine_element_followup_keeps_location_context(self):
        self.assertTrue(followup_question('那今天的高潮和低潮分别是几点？'))
        self.assertTrue(followup_question('明天的浪高是多少？'))
        self.assertFalse(followup_question('今天舟山浪高是多少？'))

    def test_background_and_question_downloads_do_not_share_partial_file(self):
        content=(self.root/'PZJ_BEN_OSM_ZJS_202610162000_WAV_00_L2.nc').read_bytes()
        gate=Barrier(2)
        class Response:
            status_code=200
            def __enter__(self):return self
            def __exit__(self,*args):return None
            def raise_for_status(self):return None
            def iter_content(self,*args):
                yield content[:100];gate.wait(timeout=10);yield content[100:]
        url='https://example.oss.aliyuncs.com/PZJ_BEN_OSM_ZJS_202610162000_WAV_00_L2.nc'
        with patch('forecast_runtime.sync.requests.get',side_effect=lambda *args,**kwargs:Response()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.r.sync.download,'MGrid/PZJ_BEN_L2/',url) for _ in range(2)]
                values=[job.result(timeout=20) for job in jobs]
        self.assertEqual({row['state'] for row in values},{'downloaded','existing'})
        self.assertEqual(len(list((self.root/'grids').rglob('*.nc'))),1)
        self.assertFalse(list((self.root/'grids').rglob('*.part')))

    def test_as_of_excludes_a_later_run_even_if_it_covers_requested_hours(self):
        old=self.r.store.grids()[0]
        self.r.store.register_grid(dict(old,id='later',batch=self.now.isoformat()))
        with patch('forecast_runtime.service.local_now',return_value=self.now):
            result=self.r.grid('surge',121.05,28.05,self.start,self.end,as_of=self.now-timedelta(hours=1))
        self.assertEqual(result['batch'],old['batch'])

    def test_ideal_grid_conditions_alone_never_establish_safe_swimming(self):
        products=[{'product':p,'state':'ready','series':[{'value':0}],
            'minimum':{'value':0},'maximum':{'value':0},'unit':'m'} for p in ('WAV','SSW','OCU')]
        result=evaluate_activities('今天能游泳吗？',products,None,self.context,{'coordinate_status':'user_supplied'})
        self.assertEqual(result[0]['state'],'insufficient_evidence')

    def test_activity_cache_keys_separate_activities_and_rules(self):
        from forecast_runtime.recreation import query_activity
        from forecast_runtime.activity_rules import load_rules
        with patch('forecast_runtime.recreation.local_now',return_value=self.now):
            first=query_activity(self.r,'东经121.05度北纬28.05度游泳可以吗',self.start,self.end)
            second=query_activity(self.r,'东经121.05度北纬28.05度海钓可以吗',self.start,self.end)
            self.r.activity_rules=load_rules();self.r.activity_rules['version']='test-next'
            third=query_activity(self.r,'东经121.05度北纬28.05度游泳可以吗',self.start,self.end)
        self.assertEqual(len({r['request_key'] for r in (first,second,third)}),3)
        self.assertNotEqual(first['activity_rule_fingerprint'],third['activity_rule_fingerprint'])

    def test_spatial_context_is_optional_and_never_an_inundation_calculation(self):
        from forecast_runtime.recreation import query_activity
        self.r.spatial=Mock();self.r.spatial.point.return_value={'state':'ready','elevation_m':2}
        with patch('forecast_runtime.recreation.local_now',return_value=self.now):
            result=query_activity(self.r,'东经121.05度北纬28.05度赶海可以吗',self.start,self.end)
        self.r.spatial.point.assert_called_once_with(121.05,28.05)
        self.assertTrue(result['spatial_context']['not_an_inundation_calculation'])
        self.assertNotIn('inundation_depth',result['spatial_context'])

    def test_language_summary_separates_peak_phase_from_hourly_net_trend(self):
        from forecast_runtime.recreation import activity_summary
        from forecast_runtime.activity_rules import tide_reference,load_rules
        start=self.start.replace(hour=14,minute=0,second=0,microsecond=0)
        peak={'time':(start+timedelta(minutes=4)).isoformat(),'value_cm':370}
        tide={'state':'ready','site':{'code':'TEST','name':'测试潮站','filename':'test-tide'},
              'series':[{'time':start.isoformat(),'value_cm':370},
                        {'time':(start+timedelta(hours=1)).isoformat(),'value_cm':364}],
              'high_tides':[peak], 'low_tides':[]}
        reference=tide_reference(tide,load_rules())
        assessment={'activity':'tidal_flat','label':'赶海','recommendation':'证据不足',
                    'state':'insufficient_evidence','permission_state':'not_established',
                    'missing_conditions':[],'evidence_gates':[],'official_closures':[],
                    'numeric_basis':[],'numerical_screening':{'state':'passed','hourly':[]},
                    'tide_reference':reference}
        result={'products':[{'product':'WAV','state':'ready'}], 'tide':tide,
                'activity_assessments':[assessment], 'start':start.isoformat(),
                'end':(start+timedelta(hours=1)).isoformat(), 'as_of':start.isoformat(),
                'location':{},'forecast_issue_cutoff':start.isoformat(),'limitations':[],'assumptions':[]}
        summary=json.loads(activity_summary(result))
        first=summary['逐时潮过程'][0]
        self.assertEqual(first[1],370)
        self.assertIn('高潮前',first[2])
        self.assertIn('净下降',first[4])
        self.assertIn('不是整点瞬时潮相',summary['逐时潮过程列'][4])
        self.assertIn('不能把区间趋势当成整点瞬时涨落潮',summary['潮相解释'])
        self.assertEqual(summary['潮汐转折说明'][0]['高低潮依据']['next_extremum']['time'],peak['time'])


if __name__=='__main__':unittest.main()
