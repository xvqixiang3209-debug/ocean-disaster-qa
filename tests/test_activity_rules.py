"""Activity decisions require independent dated evidence, not only low waves."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from forecast_runtime.activity_rules import load_rules,numerical_screen,tide_reference
from forecast_runtime.recreation import evaluate_activities,requested_activities
from forecast_runtime.storage import BEIJING
from forecast_runtime.activity_sources import ActivitySources,parse_operator_notice,OPENING_URL


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,10,17,8,tzinfo=BEIJING);self.end=self.start+timedelta(hours=2)
        policy_clock=patch('forecast_runtime.policy_catalog.local_now',return_value=self.start+timedelta(hours=4))
        policy_clock.start();self.addCleanup(policy_clock.stop)
        self.times=[(self.start+timedelta(hours=i)).isoformat() for i in range(3)]
        self.products=[{'product':key,'state':'ready','unit':unit,'partial':False,
                        'series':[{'time':when,'value':value} for when in self.times]}
                       for key,unit,value in [('WAV','m',.2),('SSW','m/s',3),('OCU','m/s',.1)]]
        self.location={'key':'zhujiajian','coordinate_status':'user_supplied'}
        self.context={'activity_evidence':[],'water_quality':{'state':'historical'}}
        # These tests retain the explicit legacy override contract. The new
        # deployed default is exercised by test_official_activity_policy.
        self.rules=load_rules(json.loads((Path(__file__).parent/'fixtures'/'legacy_activity_rules.json').read_text(encoding='utf-8')))

    def evidence(self,kind,activity='swimming',**changes):
        value={'kind':kind,'activity':activity,'location_key':'zhujiajian','site_id':'test-beach',
               'state':'verified_open' if kind=='activity_permission' else 'verified',
               'value':'passed','valid_from':'2026-10-17','valid_to':'2026-10-17',
               'published_at':'2026-10-16','source_url':'https://example.gov.cn/test-record'}
        return value|changes

    def complete(self,activity='swimming'):
        self.context['site_id']='test-beach'
        kinds=['activity_permission','site_representativeness','forecast_semantics','site_hazard_check']
        kinds+= {'swimming':['lifeguard'],'tidal_flat':['tidal_return_plan'],
                 'shore_fishing':['fishing_site'],'boat_fishing':['boat_eligibility']}[activity]
        self.context['activity_evidence']=[self.evidence(kind,activity) for kind in kinds]
        self.context['water_quality']={'state':'current','site_id':'test-beach',
                'observed_at':'2026-10-17','published_at':'2026-10-17',
                'valid_from':'2026-10-17','valid_to':'2026-10-17',
                'source_url':'https://example.gov.cn/water','reported_swimming_suitability':'适宜'}
        self.rules.update(status='validated',validated_by='TEST fixture only',
                location_key='zhujiajian',site_id='test-beach',applicable_activities=[activity],
                source_url='https://example.gov.cn/test-rule',valid_from='2026-10-01',valid_to='2026-10-31')

    def assess(self,question='今天朱家尖能游泳吗',tide=None):
        return evaluate_activities(question,self.products,tide,self.context,self.location,self.start,self.end,self.rules)[0]

    def test_low_values_yield_candidate_samples_but_not_swimming_permission(self):
        result=self.assess()
        self.assertEqual(result['state'],'insufficient_evidence')
        self.assertEqual(result['numerical_screening']['state'],'passed')
        self.assertEqual(result['candidate_windows'][0]['sample_count'],3)
        self.assertTrue(result['candidate_windows'][0]['not_a_safe_window'])
        self.assertTrue(any(gate['kind']=='water_quality' and gate['state']=='missing' for gate in result['evidence_gates']))

    def test_complete_matched_evidence_can_recommend_conditionally(self):
        self.complete();result=self.assess()
        self.assertEqual(result['state'],'conditional_recommendation')
        self.assertEqual(result['permission_state'],'verified_open')
        self.assertIn('不是安全保证',result['recommendation'])

    def test_current_but_different_beach_quality_cannot_approve(self):
        self.complete();self.context['water_quality']['site_id']='another-beach'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_historical_and_future_publication_evidence_cannot_approve(self):
        self.complete();self.context['water_quality']['valid_to']='2026-09-28'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')
        self.complete();self.context['activity_evidence'][-1]['published_at']='2026-10-18'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_rules_must_be_validated_and_current_for_conditional_advice(self):
        self.complete();self.rules['status']='provisional'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_validated_rule_for_other_activity_or_site_cannot_approve(self):
        self.complete();self.rules['site_id']='other-beach'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')
        self.complete();self.rules['applicable_activities']=['boat_fishing']
        self.assertEqual(self.assess()['state'],'insufficient_evidence')
        self.complete();self.rules['valid_to']='2026-10-16'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_low_forecast_never_overrides_matched_official_closure(self):
        self.complete();self.context['activity_evidence'].append(self.evidence('activity_permission',state='verified_closed',published_at='2026-10-17T07:00:00+08:00'))
        self.assertEqual(self.assess()['state'],'official_closed')

    def test_partial_period_closure_exposed_instead_of_ignored(self):
        self.complete();self.context['activity_evidence'].append(self.evidence('activity_permission',state='verified_closed',
            valid_from='2026-10-17T09:00:00+08:00',valid_to='2026-10-17T09:30:00+08:00',published_at='2026-10-17T07:00:00+08:00'))
        result=self.assess();self.assertEqual(result['state'],'official_closed')
        self.assertEqual(result['official_closures'][0]['valid_from'],'2026-10-17T09:00:00+08:00')

    def test_wrong_activity_or_place_closure_not_used(self):
        self.context['activity_evidence']=[self.evidence('activity_permission',activity='boat_fishing',state='verified_closed'),
            self.evidence('activity_permission',location_key='other-place',state='verified_closed')]
        self.assertNotEqual(self.assess()['state'],'official_closed')

    def test_nansha_closure_cannot_close_dongsha_or_entire_island(self):
        self.context['activity_evidence']=[self.evidence('activity_permission',site_id='nansha',state='verified_closed')]
        self.context['site_id']='dongsha'
        self.assertFalse(self.assess()['official_closures'])
        self.context['site_id']=None
        regional=self.assess()
        self.assertEqual(regional['state'],'insufficient_evidence')
        self.assertEqual(regional['permission_state'],'partial_restrictions')
        self.assertEqual(regional['official_closures'][0]['site_id'],'nansha')
        self.assertIn('不能推定全岛',regional['recommendation'])
        self.context['site_id']='nansha'
        self.assertEqual(self.assess()['state'],'official_closed')

    def test_complete_other_beach_evidence_cannot_approve_requested_beach(self):
        self.complete();self.context['site_id']='dongsha'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')
        self.context['site_id']=None
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_source_adapter_later_day_closure_blocks_overlapping_multiday_activity(self):
        # Exercise the actual adapter's start-date filtering, then the activity
        # evaluator bridge. No network and no fabricated current-day permit.
        notice=parse_operator_notice('<html>发布日期：2026-10-17<article>'
            '2026年10月18日，南沙景区暂停游泳。</article></html>',OPENING_URL)
        store=Mock();store.state.return_value=None
        adapter=ActivitySources(store)
        with patch.object(adapter,'_records',return_value=([notice],[{'status':'parsed'}])),\
             patch('forecast_runtime.activity_sources.local_now',return_value=self.start):
            context=adapter.fetch('zhujiajian',self.start.date(),site_name='南沙')
        self.assertEqual(context['activity_evidence'],[])
        self.assertEqual(context['notice_evidence'][0]['query_state'],'outside_query_interval')
        last=datetime(2026,10,19,10,tzinfo=BEIJING)
        row=evaluate_activities('10月17日至19日南沙能游泳吗',self.products,None,context,
            self.location,self.start,last,self.rules)[0]
        self.assertEqual(row['state'],'official_closed')
        self.assertEqual(row['official_closures'][0]['valid_from'],'2026-10-18')
        context['site_id']='dongsha'
        other=evaluate_activities('东沙能游泳吗',self.products,None,context,
            self.location,self.start,last,self.rules)[0]
        self.assertFalse(other['official_closures'])

    def test_notice_bridge_deduplicates_and_checks_publication_and_valid_dates(self):
        self.context['site_id']='nansha'
        closed=self.evidence('activity_permission',site_id='nansha',state='verified_closed',
            valid_from='2026-10-18',valid_to='2026-10-18',published_at='2026-10-17')
        self.context['activity_evidence']=[closed]
        self.context['notice_evidence']=[closed|{'query_state':'outside_query_interval'}]
        last=datetime(2026,10,19,10,tzinfo=BEIJING)
        def assess():return evaluate_activities('南沙能游泳吗',self.products,None,self.context,
            self.location,self.start,last,self.rules)[0]
        self.assertEqual(len(assess()['official_closures']),1)
        self.context['activity_evidence']=[]
        self.context['notice_evidence']=[closed|{'published_at':'2026-10-20'}]
        self.assertFalse(assess()['official_closures'])
        self.context['notice_evidence']=[closed|{'valid_from':'2026-10-20','valid_to':'2026-10-20'}]
        self.assertFalse(assess()['official_closures'])
        self.context['notice_evidence']=[closed|{'valid_from':'2026-10-18','valid_to':'2026-10-17'}]
        self.assertFalse(assess()['official_closures'])

    def test_future_open_notice_does_not_grant_permission_across_days(self):
        self.context['site_id']='nansha'
        self.context['notice_evidence']=[self.evidence('activity_permission',site_id='nansha',
            state='verified_open',valid_from='2026-10-18',valid_to='2026-10-19')]
        last=datetime(2026,10,19,10,tzinfo=BEIJING)
        row=evaluate_activities('南沙能游泳吗',self.products,None,self.context,
            self.location,self.start,last,self.rules)[0]
        self.assertEqual(row['permission_state'],'not_established')
        self.assertNotEqual(row['state'],'conditional_recommendation')

    def test_high_waves_are_screen_failure_and_not_a_formal_ban(self):
        self.products[0]['series'][1]['value']=1.2
        result=self.assess();self.assertEqual(result['state'],'not_recommended')
        self.assertIn('不是官方禁令',result['recommendation'])
        self.assertEqual(result['numerical_screening']['hourly'][1]['exceeded_products'],['WAV'])
        self.assertEqual(len(result['candidate_windows']),2)

    def test_missing_hour_and_unknown_unit_do_not_pass_screen(self):
        self.products[0]['series'].pop(1)
        self.assertEqual(self.assess()['numerical_screening']['state'],'insufficient')
        self.products[1]['unit']='unknown'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_mixed_site_evidence_cannot_be_combined(self):
        self.complete();self.context['activity_evidence'][-1]['site_id']='different-site'
        self.assertEqual(self.assess()['state'],'insufficient_evidence')

    def test_tidal_flat_exposes_tide_phase_and_reference_not_safe_window(self):
        tide={'state':'ready','series':[{'time':when,'value_cm':v} for when,v in zip(self.times,[200,100,180])],
              'low_tides':[{'time':self.times[1],'value_cm':100}]}
        result=self.assess('今天朱家尖能赶海吗',tide)
        self.assertEqual([row['phase'] for row in result['tide_reference']['process']],['falling','low_tide','rising'])
        self.assertEqual([row['interval_trend']['direction'] for row in result['tide_reference']['process']],
                         ['falling','rising','rising'])
        self.assertTrue(result['tide_reference']['low_tide_reference_windows'][0]['not_a_safe_access_window'])
        self.assertEqual(result['state'],'insufficient_evidence')
        self.complete('tidal_flat')
        self.assertEqual(self.assess('今天朱家尖能赶海吗',tide)['state'],'conditional_recommendation')

    def test_missing_return_plan_remains_a_gate_even_with_tides(self):
        self.complete('tidal_flat');self.context['activity_evidence']=self.context['activity_evidence'][:-1]
        tide={'state':'ready','series':[],'low_tides':[]}
        self.assertEqual(self.assess('今天能赶海吗',tide)['state'],'insufficient_evidence')

    def test_hourly_net_fall_does_not_move_a_1404_high_tide_to_1400(self):
        start=datetime(2026,10,18,12,tzinfo=BEIJING)
        tide={'state':'ready','series':[{'time':(start+timedelta(hours=i)).isoformat(),'value_cm':value}
              for i,value in enumerate([341,362,370,364,349,329])],
              'high_tides':[{'time':'2026-10-18T14:04:00+08:00','value_cm':370}], 'low_tides':[]}
        result=tide_reference(tide,self.rules,start,start+timedelta(hours=5))
        at14,at15=result['process'][2:4]
        self.assertEqual(at14['value_cm'],370)
        self.assertEqual(at14['phase'],'rising')
        self.assertEqual(at14['phase_basis']['next_extremum']['time'],'2026-10-18T14:04:00+08:00')
        self.assertEqual(at15['phase'],'falling')
        self.assertEqual(at14['interval_trend']['direction'],'falling')
        self.assertEqual(at14['interval_trend']['delta_cm'],-6)
        self.assertTrue(at14['interval_trend']['contained_extrema'])
        self.assertIn('不代表整段同向涨落',at14['interval_trend']['label'])
        self.assertTrue(result['not_a_safe_access_window'])

    def test_low_turn_inside_net_rising_interval_is_still_before_low_tide(self):
        start=datetime(2026,10,18,9,tzinfo=BEIJING)
        tide={'state':'ready','series':[{'time':start.isoformat(),'value_cm':100},
              {'time':(start+timedelta(hours=1)).isoformat(),'value_cm':110}],
              'low_tides':[{'time':'2026-10-18T09:16:00+08:00','value_cm':98}], 'high_tides':[]}
        first=tide_reference(tide,self.rules)['process'][0]
        self.assertEqual(first['phase'],'falling')
        self.assertEqual(first['interval_trend']['direction'],'rising')

    def test_hourly_samples_without_exact_extrema_do_not_assert_instantaneous_phase(self):
        tide={'state':'ready','series':[{'time':when,'value_cm':value}
              for when,value in zip(self.times,[100,110,120])], 'low_tides':[], 'high_tides':[]}
        value=tide_reference(tide,self.rules)
        self.assertTrue(all(row['phase']=='unknown' for row in value['process']))
        self.assertTrue(all(row['interval_trend']['direction']=='rising' for row in value['process']))

    def test_empty_or_gapped_tide_series_cannot_grant_tidal_activity(self):
        self.complete('tidal_flat')
        self.assertEqual(self.assess('今天能赶海吗',{'state':'ready','series':[]})['state'],'insufficient_evidence')
        tide={'state':'ready','series':[{'time':self.times[0],'value_cm':200},{'time':self.times[2],'value_cm':100}]}
        result=self.assess('今天能赶海吗',tide)
        self.assertEqual(result['state'],'insufficient_evidence')
        self.assertTrue(all(row['phase']=='unknown' for row in result['tide_reference']['process']))

    def test_matched_unsuitable_water_quality_is_negative_evidence(self):
        self.complete();self.context['water_quality']['reported_swimming_suitability']='不适宜'
        result=self.assess();self.assertEqual(result['state'],'not_recommended')
        self.assertTrue(any(gate['kind']=='water_quality' and gate['state']=='failed' for gate in result['evidence_gates']))

    def test_failed_site_hazard_check_is_not_treated_as_merely_missing(self):
        self.complete();self.context['activity_evidence'][-2]['value']='unsafe'
        self.assertEqual(self.assess()['state'],'not_recommended')

    def test_shore_and_boat_are_independent_activities(self):
        self.assertEqual(requested_activities('今天能出海船钓吗'),['boat_fishing'])
        self.assertEqual(requested_activities('今天能岸钓、船钓吗'),['shore_fishing','boat_fishing'])
        self.complete('shore_fishing')
        self.assertEqual(self.assess('今天能岸钓吗')['state'],'conditional_recommendation')
        self.assertEqual(self.assess('今天能船钓吗')['state'],'insufficient_evidence')

    def test_rule_fingerprint_is_stable_but_changes_for_limit_revision(self):
        self.assertEqual(load_rules(self.rules)['fingerprint'],self.rules['fingerprint'])
        changed=deepcopy(self.rules);changed['limits']['WAV']['maximum']=.4
        self.assertNotEqual(load_rules(changed)['fingerprint'],self.rules['fingerprint'])


if __name__=='__main__':unittest.main()
