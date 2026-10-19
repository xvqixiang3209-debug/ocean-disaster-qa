"""Translate bounded spatial requests into reproducible raster queries."""
from __future__ import annotations

import json
import math
import re

from .recreation import resolve_location


def spatial_question(question):
    if re.search(r'什么是|定义|原理|如何计算|怎么算|计算方法', question):
        return False
    return bool(re.search(r'海拔|地形高程|地表高程|地形数据|人口格网|常住人口|人口估算|人口数据|范围.{0,12}人口', question))


def parse_bbox(question):
    number=r'(-?\d+(?:\.\d+)?)'
    span=number+r'\s*(?:度|°)?\s*(?:至|到|~|～|—|-)\s*'+number+r'\s*(?:度|°)?'
    lon=re.search(r'(?:东经|经度)\s*'+span,question)
    lat=re.search(r'(?:北纬|纬度)\s*'+span,question)
    if lon and lat:
        west,east=map(float,lon.groups());south,north=map(float,lat.groups())
        return checked_bbox(west,south,east,north)
    match=re.search(r'(?:bbox|矩形范围|范围坐标)\s*[:：=（(]?\s*'+number+r'\s*[,，]\s*'+number+r'\s*[,，]\s*'+number+r'\s*[,，]\s*'+number,question,re.I)
    return checked_bbox(*map(float,match.groups())) if match else None


def checked_bbox(west,south,east,north):
    values=[float(v) for v in (west,south,east,north)]
    if not all(math.isfinite(v) for v in values):raise ValueError('范围坐标必须是有限数值')
    west,south,east,north=values
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        raise ValueError('矩形范围请按西、南、东、北填写，且西小于东、南小于北')
    if (east-west)*(north-south)>30.000001:raise ValueError('单次人口查询范围不超过浙江覆盖包络30平方度')
    return west,south,east,north


def query_spatial(runtime,question,start,end):
    result={**runtime.base_result(start,end),'kind':'spatial','state':'no_data',
            'trial':False,'label':'空间栅格查询结果','assumptions':[],
            'query_nature':'static_raster_reference_not_live_observation',
            'population_year':2020,'not_evacuation_count':True,'not_inundation_model':True,
            'limitations':['地形为区域地表高程参考，不是海滩或返程路线实测。',
                '人口为2020年常住人口格网估计，不是当前游客、在场人员或应转移名单。',
                '没有统一垂直基准和堤闸、海岸连通性资料，不据此计算精确淹没。']}
    location=resolve_location(runtime,question)
    if location and location.get('coordinate_conflict'):
        return {**result,'state':'needs_location','location':location,'reason':location['coordinate_note']}
    population=bool(re.search(r'人口',question))
    terrain=bool(re.search(r'海拔|高程|地形',question))
    bounds=parse_bbox(question) if population else None
    if population:
        if bounds:
            result['population']=runtime.spatial.population_bbox(*bounds)
            result['state']=result['population']['state']
        else:
            result.update(state='needs_scope',reason='人口统计需要明确范围。请提供西、南、东、北边界，例如东经122.35至122.45度、北纬29.85至29.95度；当前不能把区域参考点当朱家尖全岛边界。')
    if terrain:
        if location:
            result['location']=location
            result['terrain']=runtime.spatial.point(location['lon'],location['lat'])
            if not population:result['state']=result['terrain']['state']
            elif result['state']!='ready' and result['terrain']['state']=='ready':result['partial']=True
        else:
            result.setdefault('reason','高程查询需要具体位置或经纬度；一个点的高程不代表整个区域。')
            if not population:result['state']='needs_location'
    if re.search(r'现在|当前|今天|实时|202[1-9]年|转移|游客|在场',question) and population:
        result['requested_current_population_unavailable']=True
    return result


def spatial_summary(result):
    return '本地栅格实际查询结果；人口的年份固定为2020年，不随提问日期改变。\n'+json.dumps(result,ensure_ascii=False,separators=(',',':'))
