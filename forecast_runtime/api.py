"""Read-only forecast data endpoints; downloads run on the server worker/CLI."""
from datetime import datetime
import json
from flask import Blueprint,jsonify,request
from .service import period
from .storage import BEIJING


def create_blueprint(runtime):
    bp=Blueprint('forecast_runtime',__name__,url_prefix='/api/forecast')

    @bp.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    def interval():
        return period(date=request.args.get('date'),hours=int(request.args.get('hours',24)))

    @bp.errorhandler(ValueError)
    def invalid(error):return jsonify(error=str(error)[:300]),400

    @bp.get('/status')
    def status():return jsonify(runtime.status())

    @bp.get('/sites')
    def sites():return jsonify(items=runtime.sites())

    @bp.get('/coasts')
    def coasts():
        features=[]
        for coast in runtime.coasts():
            features.append({'type':'Feature','geometry':coast['geometry'],'properties':{
                'id':coast['Id'],'name':coast['name'],'city':coast['Prefecture'],'county':coast['County'],
                'lon':coast['lon'],'lat':coast['lat'],'thresholds_cm':coast['thresholds_cm']}})
        return jsonify(type='FeatureCollection',features=features,trial=True,assumption_id=runtime.config_id())

    @bp.get('/tide')
    def tide():return jsonify(runtime.tide(request.args.get('station',''),*interval()))

    @bp.get('/grid')
    def grid():
        lon,lat=float(request.args['lon']),float(request.args['lat'])
        if not (-180<=lon<=180 and -90<=lat<=90):raise ValueError('经纬度超出范围')
        return jsonify(runtime.grid(request.args.get('product','wave'),lon,lat,*interval()))

    @bp.get('/coastal')
    def coastal():return jsonify(runtime.coastal(*interval(),region=request.args.get('region',''),coast_id=request.args.get('id')))

    @bp.get('/typhoons')
    def typhoons():return jsonify(runtime.sync.typhoons())

    @bp.get('/warnings')
    def warnings():
        from .official_warnings import warning_question
        question=request.args.get('question','').strip()
        if not question:
            question=(request.args.get('date','当前')+' '+request.args.get('region','全国')+' '+
                      request.args.get('hazard','海洋灾害')+'预警'+
                      ('发布记录' if request.args.get('mode')=='published' else '有哪些，覆盖哪些海域，有效时间是什么？'))
        if not 1<=len(question)<=2000 or not warning_question(question):raise ValueError('请提供日期、地区及需要查询的海洋预警发布记录。')
        return jsonify(runtime.warnings.query(question))

    @bp.get('/warnings/notices/<notice_id>')
    def warning_notice(notice_id):
        import re
        if not re.fullmatch(r'[a-f0-9]{24}',notice_id):return jsonify(error='无效公告编号'),404
        versions=runtime.warnings.versions(notice_id)
        if not versions:return jsonify(error='未取得该公告'),404
        return jsonify(id=notice_id,versions=sorted(versions,key=lambda r:r['fetched_at']),version_count=len(versions))

    @bp.get('/activity')
    def activity():
        # Same query engine as chat; data acquisition is limited to the
        # configured first-party directory and parsed values are persisted.
        question=request.args.get('question','')
        from .recreation import activity_question
        if not 1<=len(question)<=2000 or not activity_question(question):
            raise ValueError('请填写带日期或时间、地点的赶海、游泳或海钓问题')
        return jsonify(runtime.query(question))

    @bp.get('/activity/rules')
    def activity_rules():
        from .activity_policy_queries import query_policies
        question=request.args.get('question','浙江赶海、游泳、岸钓和船钓适用哪些规则？')
        if not 1<=len(question)<=2000:raise ValueError('规则查询问题长度为1至2000字')
        return jsonify(query_policies(runtime,question,*interval()))

    @bp.get('/evidence/<evidence_id>')
    def evidence(evidence_id):
        import re
        if not re.fullmatch(r'[0-9a-f]{24}',evidence_id):return jsonify(error='无效来源编号'),404
        record=runtime.store.state('evidence:'+evidence_id)
        if record is None:return jsonify(error='未找到查询记录'),404
        return jsonify(record)

    return bp


def install(app):
    from pathlib import Path
    from .service import ForecastRuntime
    runtime=ForecastRuntime(Path(app.root_path)/'data'/'forecast_runtime',coastal_acquisition=True)
    app.register_blueprint(create_blueprint(runtime))
    from .spatial_api import create_blueprint as spatial_blueprint
    app.register_blueprint(spatial_blueprint(runtime))
    app.extensions['forecast_runtime']=runtime
    return runtime
