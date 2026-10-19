"""Bridge the question interface to the existing map analogue algorithm."""
from datetime import datetime
import re

from .storage import BEIJING


def query_analogue(runtime,question,start,end):
    base={**runtime.base_result(start,end),'kind':'analogue'}
    service=getattr(runtime,'analogue_service',None)
    if service is None:return {**base,'state':'unavailable','reason':'当前问答实例未连接历史台风路径检索服务。'}
    snapshot=runtime.sync.typhoons()
    storms=snapshot.get('list') or []
    explicit=re.findall(r'(?:标识|编号|ID|id)\s*[:：]?\s*(\d{6,})',question)
    if explicit:
        storms=[s for s in storms if str(s.get('typhoonID')) in explicit]
    else:
        named=[s for s in storms if any(n and str(n) in question for n in (s.get('nameCN'),s.get('nameEN')))]
        if named:storms=named
    if len(storms)!=1:
        return {**base,'state':'needs_clarification','reason':'未能唯一匹配所问台风的接口记录，请明确台风名称和接口标识；不改用另一场台风。'}
    storm=storms[0];cutoff=getattr(runtime._local,'request_cutoff',None)
    points=[]
    for p in storm.get('points') or []:
        t=datetime.fromisoformat(p['time']);t=t.astimezone(BEIJING) if t.tzinfo else t.replace(tzinfo=BEIJING)
        if cutoff and t>cutoff:continue
        points.append({'time':t.isoformat(),'lat':p['lat'],'lon':p['lon'],
                       'wind_kt':float(p['maxSpeed'])/.514444 if p.get('maxSpeed') is not None else None,
                       'pressure_hpa':p.get('pressure')})
    if len(points)<2:return {**base,'state':'no_data','reason':'所问台风在指定截至时刻前不足两个可用路径点。'}
    points.sort(key=lambda p:p['time'])
    payload={'sid':str(storm['typhoonID']),'name':storm.get('nameCN') or storm.get('nameEN'),
             'source':'ZJOCEAN','issued_at':points[-1]['time'],'track':points}
    try:
        matched=service.find_similar(payload,top_k=3)
    except (ValueError,RuntimeError,FileNotFoundError) as exc:
        return {**base,'state':'unavailable','reason':type(exc).__name__+'：路径相似检索暂不可用。'}
    rows=[]
    for row in matched.get('analogues',[])[:3]:
        rows.append({k:row.get(k) for k in ('rank','sid','name','season','similarity','score','track_similarity','analysis','historical_report','storm_surge') if row.get(k) is not None})
    return {**base,'state':'ready','current':payload | {'track_count':len(points),'track':points[-2:]},
            'analogue_count':len(rows),'analogues':rows,'method':matched.get('method'),
            'cache_stale':snapshot.get('cache_stale',False),'provisional_units':True,
            'limitation':'排名为路径相似，不推定本次损失；历史灾损只按匹配案例的报告口径解释。'}
