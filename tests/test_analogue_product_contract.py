"""Path selection and missing-observation contracts; no network/model calls."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import sqlite3
from threading import local
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
import pytest

from forecast_runtime.sync import LiveTyphoonProvider, select_typhoon_payload
from forecast_runtime.analogue import query_analogue
from forecast_runtime.storage import BEIJING
from typhoon.api import create_typhoon_blueprint
from typhoon.current_provider import CurrentTyphoonFileProvider
from typhoon.schema import TyphoonInputError
from typhoon.service import TyphoonAnalogueService, format_analogue_report
from typhoon.storm_surge import StormSurgeRepository


def snapshot(two=False):
    forecasts={
        '中国':[{'time':'2026-10-21 08:00:00','lat':18,'lon':130,'maxSpeed':60,'pressure':920},
                {'time':'2026-10-22 08:00:00','lat':20,'lon':128,'maxSpeed':55,'pressure':930}],
        '日本':[{'time':'2026-10-21 08:00:00','lat':18,'lon':140,'maxSpeed':50,'pressure':940},
                {'time':'2026-10-22 08:00:00','lat':21,'lon':142,'maxSpeed':45,'pressure':950}],
    }
    storm={'typhoonID':202630,'nameCN':'测试甲','points':[
        {'time':'2026-10-20 14:00:00','lat':15,'lon':135,'maxSpeed':60,'pressure':920},
        {'time':'2026-10-20 17:00:00','lat':16,'lon':133,'maxSpeed':62,'pressure':915,'forecast':forecasts}]}
    storms=[storm]
    if two:
        other=deepcopy(storm);other.update(typhoonID=202631,nameCN='测试乙');storms.append(other)
    return {'list':storms,'retrieved_at':'2026-10-20T18:00:00+08:00','cache_stale':False}


def test_multiple_storms_require_selection_and_never_pick_the_last():
    with pytest.raises(TyphoonInputError) as caught:select_typhoon_payload(snapshot(True))
    assert caught.value.code=='CURRENT_TYPHOON_SELECTION_REQUIRED'
    assert len(caught.value.choices)==2
    assert select_typhoon_payload(snapshot(True),storm_id='202630')['name']=='测试甲'
    assert select_typhoon_payload(snapshot(True),storm_id='202631')['name']=='测试乙'


def test_unknown_storm_does_not_fall_back_to_another():
    with pytest.raises(TyphoonInputError) as caught:select_typhoon_payload(snapshot(),storm_id='209999')
    assert caught.value.code=='CURRENT_TYPHOON_NOT_FOUND'


def test_observed_track_is_separate_from_forecast_and_keeps_time_zone():
    result=select_typhoon_payload(snapshot())
    assert [p['lon'] for p in result['track']]==[135,133]
    assert result['track_context']['track_kind']=='observed'
    assert result['track_context']['forecast_point_count']==0
    assert result['track'][-1]['time']=='2026-10-20T09:00:00Z'
    assert result['data_status']['available_forecast_agencies']==['中国','日本']


@pytest.mark.parametrize('agency,longitude',[('中国',130),('CMA',130),('日本',140),('JMA',140)])
def test_forecast_uses_one_agency_and_the_current_anchor(agency,longitude):
    result=select_typhoon_payload(snapshot(),track_kind='forecast',agency=agency)
    assert len(result['track'])==3
    assert result['track'][0]['lon']==133 and result['track'][1]['lon']==longitude
    assert result['track_context']['forecast_point_count']==2
    assert result['track_context']['observed_point_count']==1
    assert result['track_context']['agency'] in {'中国','日本'}


def test_multiple_agencies_need_an_explicit_choice():
    with pytest.raises(TyphoonInputError) as caught:select_typhoon_payload(snapshot(),track_kind='forecast')
    assert caught.value.code=='FORECAST_AGENCY_REQUIRED'
    assert {x['agency'] for x in caught.value.choices}=={'中国','日本'}


@pytest.mark.parametrize('options',[{'track_kind':'observed','agency':'中国'},
                                   {'track_kind':'invalid'},
                                   {'track_kind':'forecast','agency':'不存在'}])
def test_invalid_selection_is_not_silently_changed(options):
    with pytest.raises(TyphoonInputError):select_typhoon_payload(snapshot(),**options)


def test_absent_forecast_is_not_replaced_by_observed_points():
    data=snapshot();data['list'][0]['points'][-1]['forecast']['中国']=[]
    with pytest.raises(TyphoonInputError):select_typhoon_payload(data,track_kind='forecast',agency='中国')


def test_file_provider_cannot_claim_a_forecast_agency(tmp_path):
    data=select_typhoon_payload(snapshot());path=tmp_path/'current.json';path.write_text(json.dumps(data),encoding='utf-8')
    provider=CurrentTyphoonFileProvider(path)
    assert provider.get_current_typhoon(storm_id='202630')['sid']=='202630'
    with pytest.raises(TyphoonInputError):provider.get_current_typhoon(storm_id='202631')
    with pytest.raises(TyphoonInputError):provider.get_current_typhoon(track_kind='forecast',agency='中国')


def client(data):
    provider=LiveTyphoonProvider(SimpleNamespace(typhoons=lambda:deepcopy(data)))
    application=SimpleNamespace(analyze=lambda payload,**options:{'selected':payload,'options':options})
    app=Flask(__name__);app.register_blueprint(create_typhoon_blueprint(SimpleNamespace(current_provider=provider,application=application)))
    return app.test_client()


def test_api_current_and_similar_share_explicit_selection_contract():
    c=client(snapshot(True))
    missing=c.get('/api/typhoon/current')
    assert missing.status_code==409 and len(missing.json['choices'])==2
    current=c.get('/api/typhoon/current?storm_id=202630&track_kind=forecast&agency=CMA')
    assert current.status_code==200 and current.json['track_context']['agency']=='中国'
    similar=c.post('/api/typhoon/similar',json={'storm_id':'202630','track_kind':'forecast','agency':'CMA','compact':True})
    assert similar.status_code==200
    assert similar.json['selected']['track']==current.json['track']
    assert c.post('/api/typhoon/similar',json={'top_k':3}).status_code==409


@pytest.mark.parametrize('distance,wind,pressure',[(1816,121,915),(0,160,880),(3000,5,1015)])
def test_missing_surge_does_not_become_an_intensity_distance_grade(tmp_path,distance,wind,pressure):
    repo=StormSurgeRepository(tmp_path/'missing.json')
    data=repo.assess({'sid':'not-observed','max_wind':wind,'min_pressure':pressure,'closest_distance_zhejiang':distance},[])
    assert data['severity'] is None and data['origin']=='missing_observation'
    assert data['data_state']=='missing' and data['record_count']==0
    assert data['max_surge_m'] is None and data['max_water_level_m'] is None
    assert '严重' not in data['summary'] and '轻微' not in data['summary']


def test_provided_empty_surge_record_does_not_become_general_severity(tmp_path):
    repo=StormSurgeRepository(tmp_path/'missing.json')
    assert repo.assess({'sid':'not-observed'},provided={})['severity'] is None


def test_real_historical_station_values_are_preserved(tmp_path):
    repo=StormSurgeRepository(tmp_path/'missing.json')
    data=repo.assess({'sid':'history'},provided={'max_surge_m':2.53,'station':'甲站','source':'历史站点记录'})
    assert data['max_surge_m']==2.53 and data['max_surge_station']=='甲站'
    assert data['sources']==['历史站点记录'] and data['record_count']==1
    assert data['is_estimate'] is False


def test_chat_analogue_uses_selected_agency_and_keeps_missing_case_data():
    calls=[]
    d=datetime(2026,10,20,tzinfo=BEIJING)
    rt=SimpleNamespace(base_result=lambda start,end:{'start':start.isoformat(),'end':end.isoformat()},
        analogue_service=SimpleNamespace(find_similar=lambda payload,top_k:(calls.append(payload) or {'analogues':[
            {'rank':1,'sid':'s1','name':'历史甲','season':2000,'similarity_score':55,'storm_surge':{'origin':'missing_observation','is_estimate':True,'record_count':0}},
            {'rank':2,'sid':'s2','name':'历史乙','season':2001,'similarity_score':50,'historical_report':{'source_file':'report','direct_economic_loss_cny':123}}]})),
        sync=SimpleNamespace(typhoons=lambda:snapshot()),_local=SimpleNamespace(request_cutoff=None))
    result=query_analogue(rt,'台风“测试甲”按中国气象局预报路径找相似台风，那次损失多大？',d,d)
    assert result['state']=='ready' and calls[0]['track_context']['agency']=='中国'
    assert result['analogues'][0]['similarity_score']==55
    assert result['analogues'][0]['historical_report_state']=='missing'
    assert 'historical_report' not in result['analogues'][0]
    assert result['analogues'][1]['historical_report']['direct_economic_loss_cny']==123


def test_historical_chat_does_not_fetch_a_later_live_path():
    d=datetime(2026,10,19,tzinfo=BEIJING)
    rt=SimpleNamespace(base_result=lambda start,end:{'start':start.isoformat(),'end':end.isoformat()},
        analogue_service=object(),store=SimpleNamespace(typhoon_snapshot=lambda *a,**k:None),
        sync=SimpleNamespace(typhoons=lambda:pytest.fail('must not fetch live data')),
        _local=SimpleNamespace(request_cutoff=d,request_time={'mode':'historical'}))
    result=query_analogue(rt,'截至2026年10月19日台风相似案例有哪些？',d,d)
    assert result['state']=='no_data' and '之后取得' in result['reason']


def test_saved_real_track_still_matches_offline_without_proxy_grade():
    root=Path(__file__).resolve().parents[1]
    path=root/'data/forecast_runtime/forecast.sqlite3'
    history=root/'typhoon/data/typhoon_history.sqlite3'
    if not path.exists() or not history.exists():pytest.skip('private local source databases are not distributed')
    with sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True) as db:
        raw=db.execute("select payload from state where key='typhoons'").fetchone()
    if not raw:pytest.skip('no saved live snapshot')
    data=json.loads(raw[0]);storm=data['list'][0]
    payload=select_typhoon_payload(data,storm_id=storm['typhoonID'])
    original=sqlite3.connect
    def readonly(path,*args,**kwargs):
        return original('file:'+Path(path).as_posix()+'?mode=ro',uri=True)
    with patch('sqlite3.connect',readonly):
        result=TyphoonAnalogueService().find_similar(payload,top_k=3)
    assert len(result['analogues'])==3 and result['method']['scored_candidate_count']>1
    assert result['method']['input_track']['track_kind']=='observed'
    assert result['current']['storm_surge']['severity'] is None
    assert result['current']['storm_surge']['origin']=='missing_observation'
    report=format_analogue_report(result)
    assert '本次输入：已发生路径' in report
    assert '尚待业务方核定' in report
    assert '风暴潮影响潜势粗略判断' not in report
    assert '总体属于一般量级' not in report
    assert '不从路径排名推定灾害轻重' in report
