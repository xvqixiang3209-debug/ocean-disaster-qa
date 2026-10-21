"""Bridge the question interface to the existing map analogue algorithm."""
from datetime import datetime
import re

from .storage import BEIJING
from .sync import select_typhoon_payload
from typhoon.schema import TyphoonInputError


def query_analogue(runtime,question,start,end):
    base={**runtime.base_result(start,end),'kind':'analogue'}
    service=getattr(runtime,'analogue_service',None)
    if service is None:return {**base,'state':'unavailable','reason':'当前问答实例未连接历史台风路径检索服务。'}
    cutoff=getattr(runtime._local,'request_cutoff',None)
    time_mode=(getattr(runtime._local,'request_time',None) or {}).get('mode')
    explicit=re.findall(r'(?:标识|编号|ID|id)\s*[:：]?\s*(\d{4,8})',question)
    if time_mode=='historical' and cutoff and hasattr(runtime,'store'):
        snapshot=runtime.store.typhoon_snapshot(cutoff,explicit,question,allow_partial=True)
        if snapshot is None:
            return {**base,'state':'no_data','reason':'未保存所问截至时刻之前的台风路径快照，不使用之后取得的实时路径回填。'}
    else:snapshot=runtime.sync.typhoons()
    storms=snapshot.get('list') or []
    if explicit:
        storms=[s for s in storms if str(s.get('typhoonID')) in explicit]
    else:
        named=[s for s in storms if any(n and str(n) in question for n in (s.get('nameCN'),s.get('nameEN')))]
        if named:storms=named
        quoted=re.findall(r'(?:台风\s*[“「\"]([^”」\"]+)[”」\"]|[“「\"]([^”」\"]+)[”」\"]\s*台风)',question)
        requested_names={name for pair in quoted for name in pair if name}
        if requested_names and not named:
            storms=[]
    if len(storms)!=1:
        return {**base,'state':'needs_clarification','reason':'未能唯一匹配所问台风的接口记录，请明确台风名称和接口标识；不改用另一场台风。'}
    storm=storms[0]
    agency=None
    for name in ('中国气象局','日本气象厅','CMA','JMA','JTWC','HKO','中国','日本','美国','香港'):
        if (name in question and re.search(re.escape(name)+r'.{0,16}(?:预报|预测)路径',question)
                or name in {'CMA','JMA','JTWC','HKO'} and re.search(r'\b'+name+r'\b',question,re.I)):
            agency=name;break
    kind='forecast' if agency or re.search(r'预报路径|预测路径|未来.{0,8}路径',question) else 'observed'
    try:
        payload=select_typhoon_payload(snapshot,storm_id=storm['typhoonID'],agency=agency,track_kind=kind,cutoff=cutoff)
    except TyphoonInputError as exc:
        return {**base,'state':'needs_clarification','reason':str(exc),'choices':getattr(exc,'choices',[])}
    except FileNotFoundError as exc:
        return {**base,'state':'no_data','reason':str(exc)}
    points=payload['track']
    try:
        from .analogue_delivery import requested_top_k
        top_k=requested_top_k(question)
        if not 1<=top_k<=10:
            return {**base,'state':'needs_clarification','reason':'相似台风数量支持1至10个，请明确这个范围内的数量。'}
        matched=service.find_similar(payload,top_k=top_k)
    except (ValueError,RuntimeError,FileNotFoundError) as exc:
        return {**base,'state':'unavailable','reason':type(exc).__name__+'：路径相似检索暂不可用。'}
    rows=[]
    for row in matched.get('analogues',[])[:top_k]:
        item={k:row.get(k) for k in ('rank','sid','name','season','similarity','score','similarity_score','track_similarity','track_dtw_km','analysis','historical_report','storm_surge') if row.get(k) is not None}
        item['historical_report_state']='ready' if row.get('historical_report') else 'missing'
        surge=item.get('storm_surge') or {}
        item['storm_surge_data_state']='ready' if not surge.get('is_estimate',True) and surge.get('record_count',0)>0 else 'missing'
        item['missing_information']=[]
        if item['historical_report_state']=='missing':item['missing_information'].append('未取得与该历史个例关联的灾害报告，未填补灾损数值。')
        if item['storm_surge_data_state']=='missing':item['missing_information'].append('未取得该个例的可核对实测风暴潮记录，不推定为零或一般影响。')
        rows.append(item)
    return {**base,'state':'ready','current':payload | {'track_count':len(points),'track':points[-2:]},
            'analogue_count':len(rows),'analogues':rows,'method':matched.get('method'),
            'cache_stale':snapshot.get('cache_stale',False),'provisional_units':True,
            'track_context':payload.get('track_context'),'data_status':payload.get('data_status'),
            'historical_data_state':'partial' if any(row['missing_information'] for row in rows) else 'ready',
            'limitation':'排名为路径相似，不推定本次损失；历史灾损只按匹配案例的报告口径解释。'}
