"""Regression coverage for historical analogue selection and its loss follow-up."""
from datetime import datetime
from threading import local
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from forecast_runtime import historical_comparison as history, service
from forecast_runtime.analogue import query_analogue
from forecast_runtime.routing import analogue_question
from forecast_runtime.storage import BEIJING


Q014='截至2026年10月17日，台风“蔷琵”（浙江海洋接口标识202630）跟历史上哪一次台风比较像？那次损失多大？'


@pytest.mark.parametrize('question',[
    Q014,
    '截至2026年10月17日，找一场与蔷琵台风相似的历史台风，再说那次损失多大。',
    '这次台风跟历史上哪些台风比较像？相似台风当年损失多少？',
    '查询当前台风最相似的历史台风及它的风暴潮损失。',
    Q014+'2026年10月18日朱家尖能游泳吗？',
])
def test_unknown_analogue_selection_is_not_named_event_comparison(question):
    assert analogue_question(question)
    assert not history.comparison_question(question)


@pytest.mark.parametrize('question',[
    '比较2013年菲特与2019年利奇马的最大增水和经济损失。',
    '比较2019年利奇马和2019年米娜的风暴潮损失，两次台风影响相似吗？',
    '跟历史台风2019年利奇马相比，蔷琵截至2026年10月17日预报的风暴潮影响是偏重还是偏轻？',
    '比较2022年梅花与2023年卡努在宁波的最高潮位和增水，哪次台风影响较大、过程相似吗？',
    '比较2019年利奇马和2019年米娜海门站最高潮位；2026年10月18日朱家尖能游泳吗？',
])
def test_named_event_metrics_keep_the_comparison_route(question):
    assert history.comparison_question(question)


def runtime():
    rt=service.ForecastRuntime.__new__(service.ForecastRuntime)
    rt._local=local();rt._local.config={};rt.coasts=lambda:[]
    rt.base_result=lambda start,end:{'start':start.isoformat(),'end':end.isoformat()}
    return rt


def test_q014_uses_analogue_and_preserves_its_attached_historical_report():
    result={'kind':'analogue','state':'ready','analogues':[{'sid':'actual-history-sid','season':1953,'name':'RITA',
            'historical_report':{'source_file':'matched-event-report','direct_economic_loss_cny':1230000}}]}
    with patch('forecast_runtime.analogue.query_analogue',return_value=result) as route, \
         patch.object(service,'query_comparison',side_effect=AssertionError('wrong historical comparison route')):
        answer=runtime().query(Q014)
    route.assert_called_once()
    assert answer==result
    assert answer['analogues'][0]['historical_report']['direct_economic_loss_cny']==1230000


def test_analogue_and_activity_both_survive_compound_routing():
    analogue={'kind':'analogue','state':'ready','analogues':[]}
    activity={'kind':'activity','state':'ready','products':[]}
    with patch('forecast_runtime.analogue.query_analogue',return_value=analogue) as route, \
         patch.object(service,'query_activity',return_value=activity) as act, \
         patch.object(service,'query_comparison',side_effect=AssertionError('wrong comparison route')):
        answer=runtime().query(Q014+'2026年10月18日朱家尖能游泳吗？')
    route.assert_called_once();act.assert_called_once()
    assert answer['kind']=='bundle'
    assert {item['kind'] for item in answer['items']}=={'analogue','activity'}
    assert act.call_args.args[2].date().isoformat()=='2026-10-18'


def test_named_comparison_and_activity_both_survive():
    comparison={'kind':'historical_comparison','state':'ready','comparisons':[]}
    activity={'kind':'activity','state':'ready','products':[]}
    question='比较2019年利奇马和2019年米娜的海门站最高潮位；2026年10月18日朱家尖能游泳吗？'
    with patch.object(service,'query_comparison',return_value=comparison) as route, \
         patch.object(service,'query_activity',return_value=activity) as act, \
         patch('forecast_runtime.analogue.query_analogue',side_effect=AssertionError('wrong analogue route')):
        answer=runtime().query(question)
    route.assert_called_once();act.assert_called_once()
    assert {item['kind'] for item in answer['items']}=={'historical_comparison','activity'}


def test_existing_analogue_adapter_keeps_identity_bound_loss_and_does_not_fill_absence():
    d=datetime(2026,10,17,tzinfo=BEIJING)
    storm={'typhoonID':202630,'nameCN':'蔷琵','points':[
        {'time':'2026-10-17T12:00:00+08:00','lat':12,'lon':146,'maxSpeed':50,'pressure':940},
        {'time':'2026-10-17T15:00:00+08:00','lat':13,'lon':145,'maxSpeed':52,'pressure':935}]}
    matched=[{'rank':1,'sid':'s1','name':'RITA','season':1953,'historical_report':{'source_file':'s1-report','direct_economic_loss_cny':1230000}},
             {'rank':2,'sid':'s2','name':'VAL','season':1985,'storm_surge':{'is_estimate':True,'severity':'一般','origin':'proxy'}}]
    rt=SimpleNamespace(base_result=lambda start,end:{'start':start.isoformat(),'end':end.isoformat()},
        analogue_service=SimpleNamespace(find_similar=lambda payload,top_k:{'analogues':matched}),
        sync=SimpleNamespace(typhoons=lambda:{'list':[storm]}),
        _local=SimpleNamespace(request_cutoff=datetime(2026,10,17,23,59,59,tzinfo=BEIJING)))
    answer=query_analogue(rt,Q014,d,d)
    assert answer['analogues'][0]['historical_report']['source_file']=='s1-report'
    assert 'historical_report' not in answer['analogues'][1]
    assert 'direct_economic_loss_cny' not in str(answer['analogues'][1])
