from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
import json
import unittest

from flask import Flask

from forecast_runtime.activity_policy_queries import policy_question,query_policies,policy_summary
from forecast_runtime.recreation import activity_question
from forecast_runtime.api import create_blueprint
from forecast_runtime.storage import BEIJING


class PolicyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,10,18,tzinfo=BEIJING)
        self.end=self.start.replace(hour=23)
        self.runtime=SimpleNamespace(coasts=lambda:[],base_result=lambda a,b:{
            'start':a.isoformat(),'end':b.isoformat(),'as_of':a.isoformat(),'trial':True,'assumptions':[]})

    def query(self,text):return query_policies(self.runtime,text,self.start,self.end)

    def test_national_rules_are_available_without_a_forecast_point(self):
        result=self.query('海钓的规则有哪些？')
        self.assertEqual(result['kind'],'activity_rules')
        self.assertTrue(result['location']['not_a_forecast_sampling_point'])
        self.assertTrue(result['items'][0]['eligible_policies'])

    def test_province_lookup_returns_all_four_activity_rules(self):
        result=self.query('浙江赶海、游泳、岸钓和船钓分别适用哪些规则？')
        self.assertEqual({r['activity'] for r in result['items']},
                         {'tidal_flat','swimming','shore_fishing','boat_fishing'})
        self.assertTrue(all(r['eligible_policies'] for r in result['items']))
        self.assertFalse(result['trial'])
        text=policy_summary(result)
        self.assertNotIn('major_above',text)
        self.assertNotIn('low_tide_reference_minutes',text)
        self.assertNotIn('_catalog',text)

    def test_protected_rules_require_an_explicit_area(self):
        def ids(result):return {p['id'] for row in result['items'] for p in row['eligible_policies']}
        protected='zhoushan-protected-area-fishing-2023'
        self.assertIn(protected,ids(self.query('马鞍列岛保护区岸钓要哪些条件和手续？')))
        self.assertNotIn(protected,ids(self.query('朱家尖岸钓要哪些条件和手续？')))
        self.assertNotIn(protected,ids(self.query('浙江省岸钓要哪些条件和手续？')))

    def test_dated_shore_activity_stays_on_numerical_query_route(self):
        text='2026年10月18日朱家尖岸钓需要哪些条件，可以去吗？'
        self.assertFalse(policy_question(text))
        self.assertTrue(activity_question(text))
        self.assertTrue(policy_question('浙江游泳有哪些规则？'))

    def test_rule_endpoint_does_not_download_grids(self):
        runtime=self.runtime
        runtime.query=Mock(side_effect=AssertionError('rule lookup must not query NC'))
        app=Flask(__name__);app.register_blueprint(create_blueprint(runtime))
        response=app.test_client().get('/api/forecast/activity/rules')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['kind'],'activity_rules')
        self.assertEqual(len(response.json['items']),4)
        runtime.query.assert_not_called()


if __name__=='__main__':unittest.main()
