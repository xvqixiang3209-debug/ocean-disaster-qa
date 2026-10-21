"""Read-only, bounded objective-forecast APIs; source paths stay server-side."""
from flask import Blueprint, Response, jsonify, request
from .objective_scope import available_scopes,resolve_scope


def create_blueprint(engine, sources=None):
    bp = Blueprint('objective_forecast', __name__, url_prefix='/api/objective')

    @bp.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @bp.errorhandler(ValueError)
    def invalid(error): return jsonify(error=str(error)[:300]), 400

    def args():
        try: maxsize = int(request.args.get('maxsize', 180))
        except ValueError as exc: raise ValueError('maxsize必须为整数') from exc
        return dict(dataset_id=request.args.get('dataset_id'), field=request.args.get('field', 'wave'),
                    valid_time=request.args.get('valid_time'), bounds=request.args.get('bounds'),
                    maxsize=maxsize, sea_mask=request.args.get('sea_mask'))

    @bp.get('/catalog')
    def catalog():
        value=engine.catalogue()
        value['report_scopes']=available_scopes()
        if sources:
            from .storage import local_now
            from datetime import timedelta
            now=local_now()
            selected=sources.select(now,now+timedelta(hours=72),latest=True)
            value['default_dataset_id']=selected['dataset_id'] if selected else None
            value['acquisition']=sources.status()
        return jsonify(value)

    @bp.get('/acquisition/status')
    def acquisition_status():return jsonify(sources.status() if sources else {'enabled':False,'state':'not_configured'})

    @bp.get('/frame')
    def frame(): return jsonify(engine.frame(**args()))

    @bp.get('/image.png')
    def image(): return Response(engine.image_png(**args()), mimetype='image/png')

    @bp.get('/point')
    def point():
        return jsonify(engine.point(dataset_id=request.args.get('dataset_id'), field=request.args.get('field', 'wave'),
                                    valid_time=request.args.get('valid_time'), lon=request.args.get('lon'), lat=request.args.get('lat')))

    def period_args():
        try: hours=int(request.args.get('hours',72)); maxsize=int(request.args.get('maxsize',180))
        except (ValueError,TypeError) as exc:raise ValueError('hours和maxsize必须为整数') from exc
        if not 1<=hours<=168:raise ValueError('图件时段支持1至168小时')
        if not request.args.get('start'):raise ValueError('时段图件需要明确start')
        return dict(dataset_id=request.args.get('dataset_id'),field=request.args.get('field','wave'),
                    start=request.args['start'],hours=hours,bounds=request.args.get('bounds'),
                    maxsize=maxsize,sea_mask=request.args.get('sea_mask','wave_valid'))

    @bp.get('/maximum/frame')
    def maximum_frame():return jsonify(engine.temporal_maximum(**period_args()))

    @bp.get('/maximum/image.png')
    def maximum_image():return Response(engine.image_temporal_maximum_png(**period_args()),mimetype='image/png')

    @bp.get('/availability')
    def availability():
        from .objective_time import temporal_coverage
        from .objective_report import _time
        from .storage import local_now
        from datetime import timedelta
        start=_time(request.args.get('start'))
        try: hours=int(request.args.get('hours',72))
        except (ValueError,TypeError) as exc:raise ValueError('hours必须为整数') from exc
        if not 1<=hours<=168:raise ValueError('报告时段支持1至168小时')
        end=start+timedelta(hours=hours)
        identifier=request.args.get('dataset_id')
        catalogue=engine.catalogue()
        dataset=next((row for row in catalogue['datasets'] if row['dataset_id']==identifier),None)
        if dataset is None:raise ValueError('未找到所选数据集')
        scope=resolve_scope(request.args.get('question',''),bounds=request.args.get('bounds'))
        bounds=scope['bounds']
        wanted=request.args.get('fields','wave,wind').split(',')
        if not wanted or any(field not in {'wave','wind','pressure'} for field in wanted):raise ValueError('无效的预报要素')
        fields={}
        issue=_time(dataset['batch']);now=local_now();eligible=issue<=min(start,now)
        for name in wanted:
            info=dataset.get('fields',{}).get(name)
            if not info:
                fields[name]={'state':'not_provided','complete':False};continue
            coverage=temporal_coverage(info['valid_times'],start,end)
            west,south,east,north=info['coverage']
            spatial=west<=bounds[0] and south<=bounds[1] and east>=bounds[2] and north>=bounds[3]
            fields[name]={'state':'ready' if coverage['complete'] and spatial and eligible else 'partial',
                          'temporal_coverage':coverage,'spatial_complete':spatial,
                          'source_bounds':info['coverage'],'unit':info['unit']}
        return jsonify(dataset_id=identifier,batch=dataset['batch'],start=start.isoformat(),end=end.isoformat(),
                       bounds=bounds,eligible_batch=eligible,fields=fields,
                       complete=eligible and all(v['state']=='ready' for v in fields.values()),
                       note='此卡核对文件时间轴与经纬度覆盖；数值缺测及海点数量以实际计算为准。')

    return bp
