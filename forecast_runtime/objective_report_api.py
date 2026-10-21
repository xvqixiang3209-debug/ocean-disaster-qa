"""Bounded report production and opaque-ID artifact downloads."""
import math
from flask import Blueprint,jsonify,request,send_file
from .objective_scope import available_scopes,resolve_scope


def report_parameters(args):
    """Resolve public inputs without accepting a caller-authored scope card."""
    question=args.get('question','')
    identifier=args.get('scope')
    known={item['id']:item for item in available_scopes()}
    if identifier and identifier not in known:
        raise ValueError('未知的报告范围，请选择已登记地区或使用bounds框选')
    bounds=args.get('bounds')
    if identifier:
        scope=known[identifier]
        if bounds:
            explicit=resolve_scope('',bounds=bounds)
            if explicit['bounds']!=scope['bounds']:
                raise ValueError('所选地区与bounds不一致，请重新选择范围')
    else:
        scope=resolve_scope(question,bounds=bounds)
    fields=args.get('fields')
    fields=fields.split(',') if fields else None
    thresholds={}
    for name in ('wave','wind'):
        value=args.get(name+'_threshold')
        if value not in (None,''):
            try:value=float(value)
            except (ValueError,TypeError) as exc:raise ValueError('筛查阈值必须是非负有限数值') from exc
            if not math.isfinite(value) or value<0:raise ValueError('筛查阈值必须是非负有限数值')
            thresholds[name]={'value':value,'label':'用户指定筛查值，非正式预警阈值'}
    return {'scope':scope,'bounds':scope['bounds'],'requested_fields':fields,
            'thresholds':thresholds or None}


def create_blueprint(reports):
    bp=Blueprint('objective_reports',__name__,url_prefix='/api/objective')

    @bp.errorhandler(ValueError)
    def invalid(exc):return jsonify(error=str(exc)[:300]),400

    @bp.get('/report')
    def report():
        hours=int(request.args.get('hours',72))
        if not 1<=hours<=168:raise ValueError('报告时段支持1至168小时')
        return jsonify(reports.report(question=request.args.get('question',''),start=request.args.get('start'),
            dataset_id=request.args.get('dataset_id'),hours=hours,**report_parameters(request.args)))

    @bp.get('/reports/<report_id>/<filename>')
    def download(report_id,filename):
        try:path=reports.artifact_path(report_id,filename)
        except (ValueError,FileNotFoundError):return jsonify(error='未找到该报告文件'),404
        return send_file(path,download_name=filename,as_attachment=request.args.get('download')=='1',conditional=True)
    return bp
