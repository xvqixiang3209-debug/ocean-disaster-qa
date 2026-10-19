from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest

from flask import Flask

from forecast_runtime.service import period
from forecast_runtime.spatial_api import create_blueprint
from forecast_runtime.spatial_queries import parse_bbox,spatial_question,query_spatial
from forecast_runtime.storage import BEIJING
import test_forecast_runtime as fixture


class TimeWindowTests(unittest.TestCase):
    def test_same_date_explicit_window_keeps_both_hours(self):
        now=datetime(2026,10,17,20,tzinfo=BEIJING)
        start,end=period('2026年10月18日8点至18点朱家尖能游泳吗？',now=now)
        self.assertEqual((start.day,start.hour,end.day,end.hour),(18,8,18,18))
        start,end=period('明天上午8点到下午4点朱家尖能赶海吗？',now=now)
        self.assertEqual((start.day,start.hour,end.day,end.hour),(18,8,18,16))

    def test_overnight_must_be_explicit(self):
        now=datetime(2026,10,17,20,tzinfo=BEIJING)
        start,end=period('2026年10月18日22点至次日2点朱家尖海钓',now=now)
        self.assertEqual((start.day,start.hour,end.day,end.hour),(18,22,19,2))
        with self.assertRaises(ValueError):period('2026年10月18日18点至8点',now=now)


class SpatialIntegrationTests(unittest.TestCase):
    def setUp(self):
        base=fixture.ForecastTests();base.setUp();self.addCleanup(base.doCleanups)
        self.r,self.start,self.end=base.r,base.start,base.end
        self.r.spatial=Mock()
        self.r.spatial.point.return_value={'kind':'elevation','state':'ready','elevation_m':76,
                                          'vertical_datum':'EGM96'}
        self.r.spatial.population_bbox.return_value={'kind':'population_bbox','state':'ready',
                'estimated_population':46254,'year':2020,'partial_coverage':False}

    def test_explicit_bbox_is_queried_not_a_named_island_total(self):
        q='2020年朱家尖周边东经122.35至122.45度、北纬29.85至29.95度范围人口估算是多少？'
        self.assertTrue(self.r.matches(q))
        result=self.r.query(q)
        self.assertEqual(result['kind'],'spatial')
        self.r.spatial.population_bbox.assert_called_once_with(122.35,29.85,122.45,29.95)
        self.assertEqual(result['population']['year'],2020)
        self.assertTrue(result['not_evacuation_count'])

    def test_population_missing_boundary_does_not_invent_scope(self):
        result=query_spatial(self.r,'朱家尖当前常住人口是多少？',self.start,self.end)
        self.assertEqual(result['state'],'needs_scope')
        self.assertTrue(result['requested_current_population_unavailable'])
        self.r.spatial.population_bbox.assert_not_called()

    def test_terrain_reads_requested_coordinate(self):
        result=query_spatial(self.r,'朱家尖东经122.39度、北纬29.89度海拔多少？',self.start,self.end)
        self.r.spatial.point.assert_called_once_with(122.39,29.89)
        self.assertEqual(result['terrain']['elevation_m'],76)
        self.assertTrue(result['not_inundation_model'])

    def test_compound_activity_preserves_population_subquestion(self):
        with patch('forecast_runtime.service.query_activity',return_value={'kind':'activity','state':'ready'}):
            result=self.r.query('明天朱家尖能游泳吗？东经122.35至122.45度、北纬29.85至29.95度范围人口估算多少？')
        self.assertEqual([r['kind'] for r in result['items']],['activity','spatial'])
        self.r.spatial.population_bbox.assert_called_once()

    def test_methods_stay_on_knowledge_route(self):
        self.assertFalse(spatial_question('人口格网是什么，如何计算？'))
        self.assertFalse(spatial_question('地形高程的定义是什么？'))

    def test_bad_bbox_is_rejected(self):
        for question in ['bbox:124,27,118,32','范围坐标:118,32,124,27']:
            with self.assertRaises(ValueError):parse_bbox(question)


class SpatialAPITests(unittest.TestCase):
    def setUp(self):
        self.spatial=Mock()
        self.spatial.status.return_value={'state':'ready'}
        self.spatial.point.return_value={'state':'ready','elevation_m':76}
        self.spatial.population_bbox.return_value={'state':'ready','estimated_population':46254,'year':2020}
        app=Flask(__name__);app.register_blueprint(create_blueprint(SimpleNamespace(spatial=self.spatial)))
        self.client=app.test_client()

    def test_valid_queries_and_finite_bounds(self):
        self.assertEqual(self.client.get('/api/spatial/status').json['state'],'ready')
        self.assertEqual(self.client.get('/api/spatial/point?lon=122.39&lat=29.89').json['elevation_m'],76)
        result=self.client.get('/api/spatial/population?west=122.35&south=29.85&east=122.45&north=29.95')
        self.assertEqual(result.json['estimated_population'],46254)
        for url in ['/api/spatial/point?lon=nan&lat=29.89','/api/spatial/point?lon=122',
                    '/api/spatial/population?west=124&south=27&east=118&north=32']:
            self.assertEqual(self.client.get(url).status_code,400)


if __name__=='__main__':unittest.main()
