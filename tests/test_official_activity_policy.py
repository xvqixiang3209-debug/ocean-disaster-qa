"""Official rules must retain geography, dates, variable meaning and permission scope."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from forecast_runtime.activity_rules import activity_gates, load_rules, numerical_screen, tide_reference, stamp
from forecast_runtime.policy_catalog import load_catalog
from forecast_runtime.storage import BEIJING


class OfficialActivityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 17, 8, tzinfo=BEIJING)
        self.end = self.start + timedelta(hours=2)
        self.now = self.start.replace(hour=20)
        self.clock = patch('forecast_runtime.policy_catalog.local_now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.location = {'key': 'zhujiajian', 'site_id': 'nansha'}
        self.products = [{'product': key, 'state': 'ready', 'unit': unit, 'partial': False,
            'series': [{'time': (self.start + timedelta(hours=i)).isoformat(), 'value': value} for i in range(3)]}
            for key, unit, value in [('WAV', 'm', 3), ('SSW', 'm/s', 16), ('OCU', 'm/s', 1.2), ('SST', 'degC', 26)]]

    def rules(self, activity='swimming', location=None):
        return load_rules(location=location or self.location, activity=activity, start=self.start, end=self.end)

    def quality(self, temperature=26, grade='优', **changes):
        return {'site_id': 'nansha', 'state': 'current', 'source_url': 'https://example.gov.cn/measurement',
                'published_at': '2026-10-17', 'observed_at': '2026-10-17',
                'valid_from': '2026-10-17', 'valid_to': '2026-10-17',
                'water_temperature_c': temperature, 'water_quality_grade': grade, **changes}

    def test_default_has_no_invented_wind_wave_current_limits(self):
        rules = self.rules('tidal_flat')
        self.assertEqual(rules['schema'], 2)
        self.assertEqual(rules['limits'], {})
        self.assertIsNone(rules['low_tide_reference_minutes'])
        result = numerical_screen(self.products, rules, self.start, self.end)
        self.assertEqual(result['state'], 'not_evaluated')
        self.assertEqual(result['hourly'][0]['values']['WAV']['value'], 3)
        self.assertFalse(result['candidate_windows'])
        self.assertTrue(result['missing_rules'])

    def test_no_fixed_low_tide_offset_in_official_mode(self):
        tide = {'state': 'ready', 'series': [{'time': self.start.isoformat(), 'value_cm': 100},
            {'time': self.end.isoformat(), 'value_cm': 120}],
            'low_tides': [{'time': self.start.isoformat(), 'value_cm': 100}]}
        result = tide_reference(tide, self.rules('tidal_flat'), self.start, self.end)
        self.assertEqual(result['low_tide_reference_windows'], [])
        self.assertEqual(len(result['low_tide_reference_events']), 1)
        self.assertEqual(result['return_window_rule_state'], 'no_official_fixed_offset_rule')

    def test_official_rule_gate_does_not_grant_permission(self):
        for activity in ('swimming', 'tidal_flat', 'shore_fishing', 'boat_fishing'):
            with self.subTest(activity=activity):
                gates, closures, site = activity_gates(activity, {}, self.location, self.start, self.end, self.rules(activity))
                by_kind = {row['kind']: row for row in gates}
                self.assertEqual(by_kind['validated_activity_rule']['state'], 'passed')
                self.assertEqual(by_kind['activity_permission']['state'], 'missing')
                self.assertTrue(by_kind['validated_activity_rule']['not_an_activity_permission'])
                self.assertFalse(closures)

    def test_daishan_not_applied_to_zhujiajian_or_province(self):
        for location in (self.location, {'province': 'zhejiang'}, {'province': 'zhejiang', 'county': 'xiangshan'}):
            selection = self.rules('boat_fishing', location)['policy_selection']
            self.assertNotIn('daishan-recreational-fishery-risk-2022', [p['id'] for p in selection['eligible_policies']])
            self.assertTrue(any(p['id'] == 'daishan-recreational-fishery-risk-2022' for p in selection['excluded_policies']))
        selection = self.rules('boat_fishing', {'key': 'daishan'})['policy_selection']
        self.assertIn('daishan-recreational-fishery-risk-2022', [p['id'] for p in selection['reference_policies']])
        self.assertNotIn('daishan-recreational-fishery-risk-2022', [p['id'] for p in selection['eligible_policies']])

    def test_withdrawn_and_unknown_full_text_stay_out_of_active_rules(self):
        selection = self.rules()['policy_selection']
        active = [p['id'] for p in selection['eligible_policies']]
        self.assertNotIn('zhoushan-bathing-service-db3309-2025-withdrawn', active)
        self.assertNotIn('cn-bathing-service-gbt34420-2017', active)
        self.assertTrue(any(p['reason'] == 'expired_or_withdrawn' for p in selection['excluded_policies']))
        for policy in selection['eligible_policies'] + selection['reference_policies']:
            self.assertIn('checked_at', policy['source']['verification'])
            self.assertTrue(policy['source']['paragraph'])

    def test_protected_area_needs_explicit_boundary_identity(self):
        self.assertNotIn('zhoushan-protected-area-fishing-2023',
                         [p['id'] for p in self.rules('shore_fishing')['policy_selection']['eligible_policies']])
        where = {**self.location, 'protected_area_id': 'zhongjieshan_islands'}
        rules = self.rules('shore_fishing', where)
        self.assertIn('zhoushan-protected-area-fishing-2023', [p['id'] for p in rules['policy_selection']['eligible_policies']])
        gates, _, _ = activity_gates('shore_fishing', {}, where, self.start, self.end, rules)
        self.assertIn('fishing_permit', [row['kind'] for row in gates])

    def test_protected_area_permit_application_includes_authority_materials_and_decision_deadline(self):
        for area in ('ma_an_islands', 'zhongjieshan_islands'):
            with self.subTest(area=area):
                rules = self.rules('shore_fishing', {**self.location, 'protected_area_id': area})
                policy = next(row for row in rules['policy_selection']['eligible_policies']
                              if row['id'] == 'zhoushan-protected-area-fishing-2023')
                procedure = policy['permit_application']
                self.assertEqual(procedure['receiving_authority'], '保护区所在地县（区）渔业行政主管部门')
                self.assertEqual(procedure['application_channels'], ['线上', '线下'])
                self.assertIn('具有完全民事行为能力', procedure['eligibility_conditions'])
                self.assertIn('通过海钓活动安全管理和资源保护等相关知识考试的证明', procedure['required_materials'])
                self.assertIn('服从海钓安全管理的承诺书', procedure['required_materials'])
                self.assertEqual(procedure['delegated_application']['additional_required_material'], '委托书')
                deadline = procedure['decision_deadline']
                self.assertEqual((deadline['maximum'], deadline['unit']), (10, 'working_days'))
                self.assertEqual(deadline['starts_from'], '受理海钓许可申请之日')
                self.assertFalse(deadline['approval_guaranteed'])
                self.assertTrue(procedure['does_not_establish_an_issued_permit'])
                for phrase in ('第10条', '线上或线下', '完全民事行为能力', '有效身份证明', '承诺书', '委托书', '10个工作日', '不代表保证获批'):
                    self.assertIn(phrase, policy['summary'])
                self.assertEqual(set(procedure['scope_protected_area_ids']), {'ma_an_islands', 'zhongjieshan_islands'})
                self.assertIn('第10条', procedure['source_articles'])
                self.assertTrue(procedure['source_url'].endswith('.pdf#page=4'))

    def test_protected_area_permit_exports_match_catalog_without_extending_scope(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root/'exports/浙江沿海活动官方规则表_20261017.json').read_text(encoding='utf-8'))
        catalog = load_catalog()
        policy = next(row for row in catalog['policies'] if row['id'] == 'zhoushan-protected-area-fishing-2023')
        exported = next(row for row in report['source_catalog']['policies'] if row['id'] == policy['id'])
        self.assertEqual(exported, policy)
        self.assertEqual(report['catalog_version'], catalog['version'])
        self.assertEqual({row['protected_area_id'] for row in report['protected_area_rules']},
                         {'ma_an_islands', 'zhongjieshan_islands'})
        self.assertTrue(all(row['policy']['permit_application'] == policy['permit_application']
                            for row in report['protected_area_rules']))
        # The ordinary Zhujiajian rows must not acquire a protected-area permit
        # procedure merely because this separate local section now exists.
        self.assertFalse(any(row['id'] == policy['id'] for activity in report['table'] for row in activity['active_policies']))
        text = (root/'exports/浙江沿海活动官方规则表_20261017.txt').read_text(encoding='utf-8')
        for phrase in ('仅马鞍、中街山列岛两保护区', '第10条', '第11条', '第12条', '10个工作日内', '委托书', '不保证获批'):
            self.assertIn(phrase, text)

    def test_recreational_fishery_management_clause_does_not_imply_personal_permit_exemption(self):
        rules = self.rules('boat_fishing', {**self.location, 'protected_area_id': 'ma_an_islands'})
        policy = next(row for row in rules['policy_selection']['eligible_policies']
                      if row['id'] == 'zhoushan-protected-area-fishing-2023')
        for field in ('summary', 'not_applicable'):
            for phrase in ('休闲渔业', '不能仅据此推定免办第4条要求的个人海钓许可证', '具体手续另向主管部门核实'):
                self.assertIn(phrase, policy[field])
        self.assertIn('fishing_permit', [row['kind'] for row in policy['requirements']])
        self.assertEqual(set(policy['scope']['protected_area_ids']), {'ma_an_islands', 'zhongjieshan_islands'})
        root = Path(__file__).resolve().parents[1]
        text = (root/'exports/浙江沿海活动官方规则表_20261017.txt').read_text(encoding='utf-8')
        self.assertIn('不能仅据此推定免办第4条要求的个人海钓许可证', text)

    def test_unknown_coordinate_does_not_invent_zhejiang_administration(self):
        rules = self.rules('boat_fishing', {'key': 'user_point', 'lon': 122.4, 'lat': 29.9})
        self.assertFalse(rules['policy_selection']['eligible_policies'])

    def test_future_or_outside_effective_period_rule_cannot_be_used(self):
        early = datetime(2016, 1, 1, tzinfo=BEIJING)
        rules = load_rules(location=self.location, activity='swimming', start=early, end=early)
        self.assertFalse(rules['policy_selection']['eligible_policies'])
        wrong = self.rules('swimming', {**self.location, 'site_type': 'rocky_unmanaged_shore'})
        self.assertFalse(wrong['policy_selection']['eligible_policies'])

    def test_measured_water_environment_has_positive_and_negative_boundaries(self):
        cases = [(20, '优', 'passed', '适宜'), (28, '优', 'passed', '适宜'),
                 (28.1, '优', 'passed', '较适宜'), (33, '良', 'passed', '较适宜'),
                 (19.9, '优', 'failed', '不适宜'), (33.1, '优', 'failed', '不适宜'),
                 (26, '差', 'failed', '不适宜')]
        for temperature, grade, state, label in cases:
            with self.subTest(temperature=temperature, grade=grade):
                result = numerical_screen(self.products, self.rules(), self.start, self.end,
                                          context={'water_quality': self.quality(temperature, grade)})
                self.assertEqual(result['state'], state)
                self.assertEqual(result['numeric_evaluations'][0]['classification'], label)
                self.assertFalse(result['candidate_windows'])
                self.assertTrue(result['not_an_activity_permission'])

    def test_negative_quality_can_fail_without_temperature_but_good_quality_cannot_pass(self):
        for grade, expected in [('差', 'failed'), ('优', 'insufficient'), ('良', 'insufficient')]:
            result = numerical_screen([], self.rules(), self.start, self.end,
                                      context={'water_quality': self.quality(None, grade)})
            self.assertEqual(result['state'], expected)

    def test_sst_or_historical_or_other_site_cannot_be_used_as_bathing_measurement(self):
        for changes in ({'site_id': 'dongsha'}, {'observed_at': '2026-10-16', 'valid_to': '2026-10-18'},
                        {'published_at': '2026-10-18'}, {'state': 'historical'}):
            result = numerical_screen(self.products, self.rules(), self.start, self.end,
                                      context={'water_quality': self.quality(**changes)})
            self.assertEqual(result['state'], 'insufficient')
        result = numerical_screen(self.products, self.rules(), self.start, self.end)
        self.assertEqual(result['state'], 'insufficient')
        self.assertEqual(result['available_data'][-1]['product'], 'SST')

    def executable_gust_fixture(self):
        # This fixture simulates a future official current-status confirmation;
        # the bundled Daishan policy deliberately remains reference-only.
        rules = self.rules('boat_fishing', {'key': 'daishan'})
        for policy in rules['_catalog']['policies']:
            if policy['id'] == 'daishan-recreational-fishery-risk-2022':
                policy.update(status='current', execution='evaluate')
        return load_rules(rules)

    def test_assumed_ssw_gust_cannot_satisfy_three_second_gust_rule(self):
        result = numerical_screen(self.products, self.executable_gust_fixture(), self.start, self.end,
                                  context={'recreational_fishery_operation': True})
        self.assertEqual(result['state'], 'unsupported_semantics')
        self.assertEqual(result['risk_classes'][0]['state'], 'unsupported_semantics')

    def test_risk_class_is_separate_from_admission_failure(self):
        products = deepcopy(self.products)
        wind = next(row for row in products if row['product'] == 'SSW')
        wind['semantics'] = {'quantity': 'forecast_maximum_3_second_gust_speed', 'verification': 'verified',
                             'source_url': 'https://example.gov.cn/metadata'}
        for row in wind['series']:
            row['value'] = 13.8
        lesser = numerical_screen(products, self.executable_gust_fixture(), self.start, self.end,
                                  context={'recreational_fishery_operation': True})
        self.assertEqual(lesser['risk_classes'][0]['wind_impact'], 'lesser')
        wind['series'][1]['value'] = 13.81
        major = numerical_screen(products, self.executable_gust_fixture(), self.start, self.end,
                                 context={'recreational_fishery_operation': True})
        self.assertEqual(major['risk_classes'][0]['wind_impact'], 'major')
        self.assertEqual(major['risk_classes'][0]['composite_risk'], 'not_evaluated')
        self.assertEqual(major['state'], 'not_evaluated')
        self.assertEqual(major['candidate_windows'], [])

    def test_catalog_rejects_missing_source_or_invalid_numeric_metadata(self):
        catalog = load_catalog()
        catalog['policies'][0]['source']['url'] = 'https://example.com/advice'
        with self.assertRaises(ValueError):
            load_catalog(catalog)
        catalog = load_catalog()
        catalog['policies'][0]['numeric_rules'][0]['maximum'] = math.nan
        with self.assertRaises(ValueError):
            load_catalog(catalog)

    def test_fingerprint_tracks_catalog_activity_and_location(self):
        rules = self.rules()
        self.assertEqual(load_rules(rules)['fingerprint'], rules['fingerprint'])
        self.assertNotEqual(self.rules('tidal_flat')['fingerprint'], rules['fingerprint'])
        changed = deepcopy(rules)
        changed['_catalog']['version'] = 'future-fixture-version'
        self.assertNotEqual(load_rules(changed)['fingerprint'], rules['fingerprint'])

    def test_explicit_schema1_override_retains_legacy_screening(self):
        rules = load_rules({'schema': 1, 'version': 'explicit-test-only', 'status': 'provisional',
                            'limits': {'WAV': {'maximum': 4, 'unit': 'm'},
                                       'SSW': {'maximum': 20, 'unit': 'm/s'},
                                       'OCU': {'maximum': 2, 'unit': 'm/s'}}})
        self.assertEqual(numerical_screen(self.products, rules, self.start, self.end)['state'], 'passed')
        self.assertEqual(load_rules(rules)['fingerprint'], rules['fingerprint'])

    def test_matched_negative_traffic_control_is_not_just_missing(self):
        kinds = ['activity_permission', 'site_representativeness', 'forecast_semantics', 'site_hazard_check',
                 'boat_eligibility', 'marine_traffic_control']
        records = [{'kind': kind, 'activity': 'boat_fishing', 'location_key': 'zhujiajian', 'site_id': 'nansha',
                    'state': 'verified_open' if kind == 'activity_permission' else 'verified',
                    'value': False if kind == 'marine_traffic_control' else True,
                    'valid_from': '2026-10-17', 'valid_to': '2026-10-17',
                    'published_at': '2026-10-16', 'source_url': 'https://example.gov.cn/handover'} for kind in kinds]
        gates, _, _ = activity_gates('boat_fishing', {'activity_evidence': records}, self.location,
                                      self.start, self.end, self.rules('boat_fishing'))
        control = next(row for row in gates if row['kind'] == 'marine_traffic_control')
        self.assertEqual(control['state'], 'failed')
        records[-1]['value'] = True
        gates, _, _ = activity_gates('boat_fishing', {'activity_evidence': records}, self.location,
                                      self.start, self.end, self.rules('boat_fishing'))
        self.assertTrue(all(row['state'] == 'passed' for row in gates))

    def permission(self, state, first='2026-10-01', last='2026-10-31', published='2026-10-01', **changes):
        return {'kind': 'activity_permission', 'activity': 'swimming', 'location_key': 'zhujiajian',
                'site_id': 'nansha', 'state': state, 'value': 'open' if state == 'verified_open' else 'closed',
                'valid_from': first, 'valid_to': last, 'published_at': published,
                'source_url': 'https://example.gov.cn/' + published.replace(':', '-') + '/' + state, **changes}

    def permission_gates(self, notices, resolved, first=None, last=None, context_extra=None):
        first, last = first or self.start, last or self.end
        representative = {**self.permission('verified_open'), 'kind': 'site_representativeness',
                          'state': 'verified', 'value': True}
        context = {'activity_evidence': [representative] + resolved, 'notice_evidence': notices,
                   **(context_extra or {})}
        rules = load_rules(location=self.location, activity='swimming', start=first, end=last)
        return activity_gates('swimming', context, self.location, first, last, rules)

    def test_new_dated_reopening_supersedes_old_month_long_closure(self):
        closed = self.permission('verified_closed')
        opened = self.permission('verified_open', '2026-10-17', published='2026-10-17')
        first, last = self.start + timedelta(days=1), self.end + timedelta(days=1)
        gates, closures, _ = self.permission_gates([closed, opened], [opened], first, last)
        self.assertEqual(closures, [])
        self.assertEqual(next(row for row in gates if row['kind'] == 'activity_permission')['state'], 'passed')

    def test_reopening_only_removes_covered_closure_intervals(self):
        closed = self.permission('verified_closed')
        opened = self.permission('verified_open', '2026-10-18', '2026-10-19', '2026-10-17')
        last = self.end + timedelta(days=3)
        _, closures, _ = self.permission_gates([closed, opened], [opened], last=last)
        self.assertEqual(len(closures), 2)
        intervals = [(stamp(row['effective_closure_interval']['start']), stamp(row['effective_closure_interval']['end'])) for row in closures]
        self.assertEqual(intervals, [(self.start, datetime(2026, 10, 17, 23, 59, 59, 999999, tzinfo=BEIJING)),
                                     (datetime(2026, 10, 20, tzinfo=BEIJING), last)])
        self.assertTrue(all(row['supersession_applied'] for row in closures))
        self.assertTrue(all(row['source_valid_from'] == '2026-10-01' for row in closures))

    def test_later_new_closure_can_override_reopening_only_where_it_applies(self):
        closed = self.permission('verified_closed')
        opened = self.permission('verified_open', '2026-10-17', published='2026-10-17T06:00:00+08:00')
        closed_again = self.permission('verified_closed', '2026-10-18', '2026-10-18', '2026-10-17T07:00:00+08:00')
        first, last = self.start + timedelta(days=1), self.end + timedelta(days=2)
        _, closures, _ = self.permission_gates([closed, opened, closed_again], [opened, closed_again], first, last)
        self.assertEqual(len(closures), 1)
        self.assertEqual(closures[0]['published_at'], closed_again['published_at'])
        self.assertEqual(stamp(closures[0]['effective_closure_interval']['end']), datetime(2026, 10, 18, 23, 59, 59, 999999, tzinfo=BEIJING))

    def test_other_site_or_activity_reopening_never_cancels_a_closure(self):
        closed = self.permission('verified_closed')
        for changes in ({'site_id': 'dongsha'}, {'activity': 'shore_fishing'}, {'location_key': 'another-region'}):
            opened = self.permission('verified_open', published='2026-10-17', **changes)
            _, closures, _ = self.permission_gates([closed, opened], [opened])
            self.assertEqual(len(closures), 1)

    def test_raw_open_notice_can_cancel_old_closure_but_never_grants_permission(self):
        closed = self.permission('verified_closed')
        opened = self.permission('verified_open', published='2026-10-17')
        gates, closures, _ = self.permission_gates([closed, opened], [])
        self.assertEqual(closures, [])
        self.assertEqual(next(row for row in gates if row['kind'] == 'activity_permission')['state'], 'missing')

    def test_conflicting_latest_notices_do_not_assert_verified_open_or_closed(self):
        closed = self.permission('verified_closed', published='2026-10-17')
        opened = self.permission('verified_open', published='2026-10-17')
        gates, closures, _ = self.permission_gates([closed, opened], [opened])
        self.assertEqual(closures, [])
        self.assertEqual(next(row for row in gates if row['kind'] == 'activity_permission')['state'], 'missing')

    def test_future_publication_is_unavailable_even_for_a_future_query(self):
        first, last = self.start + timedelta(days=1), self.end + timedelta(days=1)
        for state in ('verified_closed', 'verified_open'):
            notice = self.permission(state, '2026-10-18', published='2026-10-18')
            gates, closures, _ = self.permission_gates([notice], [notice], first, last)
            self.assertEqual(closures, [])
            self.assertEqual(next(row for row in gates if row['kind'] == 'activity_permission')['state'], 'missing')
        quality = self.quality(observed_at='2026-10-18', published_at='2026-10-18',
                               valid_from='2026-10-18', valid_to='2026-10-18', reported_swimming_suitability='适宜')
        gates, _, _ = self.permission_gates([], [], first, last, {'water_quality': quality})
        self.assertEqual(next(row for row in gates if row['kind'] == 'water_quality')['state'], 'missing')
        rules = load_rules(location=self.location, activity='swimming', start=first, end=last)
        result = numerical_screen([], rules, first, last, context={'water_quality': quality})
        self.assertEqual(result['state'], 'insufficient')

    def test_historical_reconstruction_does_not_borrow_later_reopening(self):
        closed = self.permission('verified_closed')
        opened = self.permission('verified_open', '2026-10-17', published='2026-10-17')
        first, last = self.start - timedelta(days=1), self.end + timedelta(days=1)
        _, closures, _ = self.permission_gates([closed, opened], [opened], first, last)
        self.assertEqual(len(closures), 1)
        self.assertEqual(closures[0]['published_at'], '2026-10-01')
        self.assertEqual(stamp(closures[0]['knowledge_cutoff']), first)

    def test_policy_published_tomorrow_is_excluded_from_future_query_today(self):
        rules = self.rules()
        future = deepcopy(rules['_catalog']['policies'][0])
        future.update(id='future-official-method-fixture', published_at='2026-10-18', effective_from='2026-10-18')
        rules['_catalog']['policies'].append(future)
        first, last = self.start + timedelta(days=2), self.end + timedelta(days=2)
        selected = load_rules(rules, start=first, end=last, as_of='2026-10-20')['policy_selection']
        self.assertNotIn(future['id'], [row['id'] for row in selected['eligible_policies']])
        row = next(row for row in selected['excluded_policies'] if row['id'] == future['id'])
        self.assertEqual(row['reason'], 'not_published_by_knowledge_cutoff')
        self.assertEqual(stamp(selected['request']['as_of']), self.now)

    def test_reusing_rules_for_a_changed_period_gets_a_new_current_knowledge_cutoff(self):
        earlier_rules = self.rules()
        first, last = self.start.replace(hour=21), self.start.replace(hour=22)
        opened = self.permission('verified_open', published='2026-10-17T12:00:00+08:00')
        representative = {**self.permission('verified_open'), 'kind': 'site_representativeness',
                          'state': 'verified', 'value': True}
        context = {'activity_evidence': [representative, opened], 'notice_evidence': [opened],
                   'water_quality': self.quality(published_at='2026-10-17T12:00:00+08:00')}
        result = numerical_screen([], earlier_rules, first, last, context=context)
        self.assertEqual(result['state'], 'passed')
        self.assertEqual(stamp(result['policy_selection']['request']['as_of']), self.now)
        gates, _, _ = activity_gates('swimming', context, self.location, first, last, earlier_rules)
        permission = next(row for row in gates if row['kind'] == 'activity_permission')
        self.assertEqual(permission['state'], 'passed')
        self.assertEqual(stamp(permission['knowledge_cutoff']), self.now)

    def test_a_future_observation_cannot_be_known_from_an_earlier_publication(self):
        first, last = self.start + timedelta(days=1), self.end + timedelta(days=1)
        quality = self.quality(observed_at='2026-10-18', published_at='2026-10-17',
                               valid_from='2026-10-18', valid_to='2026-10-18')
        rules = load_rules(location=self.location, activity='swimming', start=first, end=last)
        self.assertEqual(numerical_screen([], rules, first, last, context={'water_quality': quality})['state'], 'insufficient')


if __name__ == '__main__':
    unittest.main()
