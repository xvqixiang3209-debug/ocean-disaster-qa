from threading import local
from unittest.mock import patch
import pytest
from forecast_runtime.impact_inputs import standalone_impact_inputs_question
from forecast_runtime.service import ForecastRuntime


def test_boundary_input_fallback_is_only_for_its_own_unmodelled_question():
    assert standalone_impact_inputs_question('2026年10月18日宁波鄞州区会海水倒灌吗？哪些低洼地段会被淹？')
    for question in ['2026年10月18日朱家尖游泳合适吗？会不会海水倒灌？',
                     '2026年10月18日朱家尖浪高、风速多少？会不会倒灌？',
                     '2026年10月18日朱家尖地表高程多少，是否被淹？']:
        assert not standalone_impact_inputs_question(question)
    assert standalone_impact_inputs_question('2026年10月18日宁波岸段总潮位多少，会不会倒灌？')


def test_real_query_preserves_activity_task_in_a_compound_question():
    runtime=ForecastRuntime.__new__(ForecastRuntime);runtime._local=local();runtime._local.config={};runtime.coasts=lambda:[]
    runtime.base_result=lambda start,end:{'start':start.isoformat(),'end':end.isoformat()}
    activity={'kind':'activity','state':'ready','products':[]}
    impact={'kind':'bundle','purpose':'impact_boundary_inputs','state':'no_data','impact_assessment':{'state':'not_computed'},'items':[]}
    with patch('forecast_runtime.service.query_activity',return_value=activity) as query_activity, \
         patch('forecast_runtime.service.query_impact_inputs',return_value=impact) as query_impact:
        result=runtime.query('2026年10月18日朱家尖适合游泳吗？会不会海水倒灌？')
    query_activity.assert_called_once();query_impact.assert_called_once()
    assert result['items']==[activity,impact]


def test_real_query_preserves_spatial_task_in_a_compound_question():
    runtime=ForecastRuntime.__new__(ForecastRuntime);runtime._local=local();runtime._local.config={}
    runtime.base_result=lambda start,end:{'start':start.isoformat(),'end':end.isoformat()}
    spatial={'kind':'spatial','state':'ready','terrain':{'elevation_m':12}}
    impact={'kind':'bundle','purpose':'impact_boundary_inputs','state':'no_data','impact_assessment':{'state':'not_computed'},'items':[]}
    with patch('forecast_runtime.service.query_spatial',return_value=spatial) as query_spatial, \
         patch('forecast_runtime.service.query_impact_inputs',return_value=impact) as query_impact:
        result=runtime.query('2026年10月18日朱家尖地表高程多少，是否被淹？')
    query_spatial.assert_called_once();query_impact.assert_called_once()
    assert result['items']==[spatial,impact]


def test_nested_result_flags_keep_activity_and_spatial_instructions():
    from forecast_runtime.service import contains_result
    result={'kind':'bundle','items':[{'kind':'bundle','items':[{'kind':'activity'},{'kind':'spatial'}]}]}
    assert contains_result(result,'activity') and contains_result(result,'spatial')
    assert not contains_result(result,'typhoons')
