"""Acceptance through the real running assistant, without changing old reports."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import time
import requests

CASES=[
 ('C01','台风、风暴潮、海浪、海啸到底有什么区别？在浙江沿海我该关注哪一个？'),
 ('C02','什么是风暴增水？它和天文大潮叠加在一起会怎么样？'),
 ('C03','浙江海洋灾害什么时候可以降低或终止应急响应？由谁决定，要走什么程序？'),
 ('C04','浙江防台风什么条件下启动停课、停工、停产、停运、停业？由谁决定、怎么公告？'),
 ('C05','2026年10月18日00时至10月21日00时浙江沿海预报增水有多大，最高潮位会到多少？什么是风暴增水？请区分网格预报与台风“蕾琵”（浙江海洋接口标识202630）的因果归属。'),
 ('C06','截至2026年10月17日12时，国家海洋预报台对秦皇岛发布了哪些风暴潮警报？等级、有效时间和覆盖范围是什么？黄色警报是什么意思？'),
 ('C07','截至2026年10月17日，浙江当前发布了哪些海洋灾害预警？覆盖哪些海域？'),
]

def map_case(base,root):
    output=root/'map';output.mkdir(exist_ok=False)
    session=requests.Session();started=time.monotonic()
    match=session.post(base+'/api/typhoon/similar',json={'top_k':3,'report_mode':'template','compact':True},timeout=120)
    data=match.json();(output/'similar.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    candidates=data.get('analogues') or []
    if match.status_code!=200 or not candidates:
        return {'id':'C08','passed':False,'error_type':'no_map_match'}
    analogue=max(candidates,key=lambda a:len((a.get('historical_report') or {}).get('storm_surge_station_details') or []))
    token=session.get(base+'/api/assistant/session',timeout=20).json()['token']
    question='这个台风各站的最大增水和超警级别是多少？也请联网核对历史记录。'
    response=session.post(base+'/api/assistant/ask',json={'question':question,'selected_sid':analogue['sid'],
        'match_request_id':data['request_id']},headers={'X-Assistant-Token':token},timeout=300)
    actual=response.json();audit=(actual.get('evidence_diagnostics') or {}).get('crag_audit') or {}
    checks={'http_200':response.status_code==200,'historical_case_route':actual.get('evidence_diagnostics',{}).get('route')=='historical_case',
            'local_executed':audit.get('local',{}).get('executed') is True,'web_executed':audit.get('web',{}).get('executed') is True,
            'case_facts_retained':audit.get('used_tool',0)>0}
    row={'id':'C08','question':question,'selected_sid':analogue['sid'],'tested_at':datetime.now().astimezone().isoformat(),
         'seconds':round(time.monotonic()-started,2),'http_status':response.status_code,
         'checks':checks,'passed':all(checks.values()),'actual':actual}
    (output/'result.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'id':'C08','checks':checks,'passed':row['passed']},ensure_ascii=False),flush=True)
    return row

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8769')
    p.add_argument('--output',required=True);p.add_argument('--ids',default='');args=p.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=False);base=args.base.rstrip('/')
    status=requests.get(base+'/api/assistant/status',timeout=30).json()
    (root/'build.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    selected=set(args.ids.split(',')) if args.ids else None
    def run(case):
        ident,question=case;session=requests.Session()
        token=session.get(base+'/api/assistant/session',timeout=20).json()['token'];started=time.monotonic()
        try:
            response=session.post(base+'/api/assistant/ask',json={'question':question},headers={'X-Assistant-Token':token},timeout=300)
            actual=response.json();diag=actual.get('evidence_diagnostics') or {};audit=diag.get('crag_audit') or {}
            evidence=session.get(base+actual['data_url'],timeout=30).json() if actual.get('data_url') else None
            checks={'http_200':response.status_code==200,'local_executed':audit.get('local',{}).get('executed') is True,
                    'web_executed':audit.get('web',{}).get('executed') is True,
                    'crag_evaluated':audit.get('evaluator')=='deepseek_relevance_with_exact_passage_ids',
                    'answer_present':bool(actual.get('answer')),'deepseek_answer':actual.get('mode')=='llm'}
            if ident=='C05':
                checks['coastal_route']=diag.get('route')=='business_forecast'
                checks['surge_and_total_retained']=all(s in actual.get('answer','') for s in ['164.84','685.86'])
                checks['tool_source_retained']=audit.get('used_tool',0)>0
            if ident in {'C06','C07'}:checks['official_route']=diag.get('route')=='official_warnings'
            if ident=='C07':checks['provincial_gap_not_hidden']=bool(actual.get('missing_information'))
            row={'id':ident,'question':question,'tested_at':datetime.now().astimezone().isoformat(),
                 'seconds':round(time.monotonic()-started,2),'http_status':response.status_code,
                 'checks':checks,'passed':all(checks.values()),'actual':actual,'evidence':evidence}
            if ident=='C03' and actual.get('conversation_id'):
                follow=session.post(base+'/api/assistant/ask',json={'question':'那上述响应降级，是不是预警降低后就自动执行？',
                      'conversation_id':actual['conversation_id']},headers={'X-Assistant-Token':token},timeout=300)
                answer=follow.json();fa=(answer.get('evidence_diagnostics') or {}).get('crag_audit') or {}
                row['followup']={'question':'那上述响应降级，是不是预警降低后就自动执行？','http_status':follow.status_code,'actual':answer}
                row['checks']['followup_joint_retrieval']=bool(follow.status_code==200 and answer.get('context_used') and fa.get('local',{}).get('executed') and fa.get('web',{}).get('executed'))
                row['passed']=all(row['checks'].values())
        except Exception as exc:row={'id':ident,'question':question,'passed':False,'error_type':type(exc).__name__}
        (root/(ident+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        a=(row.get('actual',{}).get('evidence_diagnostics') or {}).get('crag_audit') or {}
        print(json.dumps({'id':ident,'passed':row['passed'],'seconds':row.get('seconds'),'checks':row.get('checks'),
            'used_local':a.get('used_local'),'used_web':a.get('used_web'),'used_tool':a.get('used_tool'),
            'error_type':row.get('error_type')},ensure_ascii=False),flush=True)
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(run,[c for c in CASES if not selected or c[0] in selected]))
    if not selected or 'C08' in selected:rows.append(map_case(base,root))
    result={'version':status.get('version'),'passed':sum(r['passed'] for r in rows),'total':len(rows),'results':rows}
    (root/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['passed']==result['total'] else 1

if __name__=='__main__':raise SystemExit(main())
