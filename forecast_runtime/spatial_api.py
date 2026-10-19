"""Read-only public data queries for the local spatial runtime."""
import math

from flask import Blueprint,jsonify,request

from .spatial_queries import checked_bbox


def create_blueprint(runtime):
    bp=Blueprint('spatial_runtime',__name__,url_prefix='/api/spatial')

    @bp.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    def coordinate(name,minimum,maximum):
        try:value=float(request.args[name])
        except (KeyError,TypeError,ValueError):raise ValueError('缺少或无效的坐标参数：'+name)
        if not math.isfinite(value) or not minimum<=value<=maximum:raise ValueError('坐标超出范围：'+name)
        return value

    @bp.errorhandler(ValueError)
    def invalid(error):return jsonify(error=str(error)),400

    @bp.get('/status')
    def status():return jsonify(runtime.spatial.status())

    @bp.get('/point')
    def point():return jsonify(runtime.spatial.point(coordinate('lon',-180,180),coordinate('lat',-90,90)))

    @bp.get('/population')
    def population():
        bounds=checked_bbox(coordinate('west',-180,180),coordinate('south',-90,90),
                            coordinate('east',-180,180),coordinate('north',-90,90))
        return jsonify(runtime.spatial.population_bbox(*bounds))

    return bp
