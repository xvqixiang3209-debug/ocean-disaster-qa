"""Public product inputs must select the same scope and native time window."""
from datetime import datetime,timedelta,timezone

from flask import Flask

from forecast_runtime.objective_api import create_blueprint
from forecast_runtime.objective_report_api import create_blueprint as report_blueprint


BEGIN=datetime(2020,1,1,8,tzinfo=timezone(timedelta(hours=8)))


class Engine:
    def __init__(self):
        self.calls=[]
        self.times=[(BEGIN+timedelta(hours=h)).isoformat() for h in range(0,73,3)]

    def catalogue(self):
        info={'unit':'m','valid_times':self.times,'coverage':[117,24,130,35]}
        return {'datasets':[{'dataset_id':'native_test_123','batch':BEGIN.isoformat(),'fields':{'wave':info,'wind':{**info,'unit':'m/s'}}}]}

    def temporal_maximum(self,**kwargs):
        self.calls.append(kwargs)
        return {'product_type':'temporal_maximum','period_start':kwargs['start']}

    def image_temporal_maximum_png(self,**kwargs):
        self.calls.append(kwargs)
        return b'\x89PNG\r\n\x1a\n'


class Reports:
    def __init__(self):self.calls=[]
    def report(self,**kwargs):
        self.calls.append(kwargs)
        return {'state':'ready','scope':kwargs['scope']}


def client():
    engine,reports=Engine(),Reports()
    app=Flask(__name__);app.register_blueprint(create_blueprint(engine))
    app.register_blueprint(report_blueprint(reports))
    return app.test_client(),engine,reports


def test_report_scope_threshold_and_fields_are_server_resolved():
    c,_,reports=client()
    r=c.get('/api/objective/report',query_string={'scope':'zhujiajian','hours':24,'fields':'wave,wind','wave_threshold':2})
    assert r.status_code==200
    call=reports.calls[-1]
    assert call['scope']['id']=='zhujiajian'
    assert call['bounds']==[122.33,29.82,122.53,30.02]
    assert call['thresholds']['wave']['value']==2
    assert call['requested_fields']==['wave','wind']
    assert '非正式预警' in call['thresholds']['wave']['label']


def test_mismatched_region_bounds_unknown_scope_and_nonfinite_threshold_rejected():
    c,_,reports=client()
    for args in ({'scope':'zhujiajian','bounds':'119,26,126,33'},
                 {'scope':'../../not-a-scope'},{'wind_threshold':'nan'},
                 {'wave_threshold':'-1'},{'wind_threshold':'bad'}):
        assert c.get('/api/objective/report',query_string=args).status_code==400
    assert not reports.calls


def test_catalogue_exposes_only_server_scope_definitions():
    c,_,_=client()
    data=c.get('/api/objective/catalog').json
    assert {'zhejiang','zhoushan','zhujiajian'}<={row['id'] for row in data['report_scopes']}
    assert all(row['kind']=='reference_box' for row in data['report_scopes'])


def test_native_three_hour_axis_is_complete_and_internal_gap_is_partial():
    c,engine,_=client()
    args={'dataset_id':'native_test_123','start':BEGIN.isoformat(),'hours':72,'bounds':'122.33,29.82,122.53,30.02'}
    data=c.get('/api/objective/availability',query_string=args).json
    assert data['complete'] and data['fields']['wave']['temporal_coverage']['expected_count']==25
    lost=engine.times.pop(8)
    data=c.get('/api/objective/availability',query_string=args).json
    assert not data['complete']
    assert lost in data['fields']['wave']['temporal_coverage']['missing_times']
    args['fields']='pressure'
    assert c.get('/api/objective/availability',query_string=args).json['fields']['pressure']['state']=='not_provided'


def test_maximum_api_is_a_period_product_not_a_substituted_instant():
    c,engine,_=client()
    args={'dataset_id':'native_test_123','field':'wave','start':BEGIN.isoformat(),'hours':72,'bounds':'122.33,29.82,122.53,30.02'}
    assert c.get('/api/objective/maximum/frame',query_string=args).json['product_type']=='temporal_maximum'
    assert engine.calls[-1]['start']==BEGIN.isoformat() and engine.calls[-1]['hours']==72
    assert 'valid_time' not in engine.calls[-1]
    assert c.get('/api/objective/maximum/image.png',query_string=args).mimetype=='image/png'
    assert c.get('/api/objective/maximum/frame',query_string={**args,'hours':999}).status_code==400
    del args['start']
    assert c.get('/api/objective/maximum/frame',query_string=args).status_code==400
