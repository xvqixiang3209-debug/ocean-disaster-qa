"""Run real HTTP/DeepSeek questions; retain answers and numerical tool evidence.

This is a development acceptance run, not an expert accuracy score. Never log
session cookies, assistant tokens, model credentials or model request headers.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time

import requests


ROOT=Path(__file__).resolve().parents[1]
CASES=[
    ('A01','2026年10月17日还能去舟山朱家尖海边赶海、游泳、海钓吗？'),
    ('A02','2026年10月18日朱家尖能赶海、游泳、海钓吗？请分别说结论并给具体海况和潮时。'),
    ('A03','2026年10月18日8点至18点，舟山朱家尖南沙可以游泳吗？'),
    ('A04','2026年10月17日朱家尖岸边海钓适合吗？给风速、浪高和参考时段。'),
    ('A05','2026年10月18日朱家尖可以船钓吗？'),
    ('S01','朱家尖东经122.39度、北纬29.89度这个点的地形海拔是多少？'),
    ('S02','朱家尖周边东经122.35至122.45度、北纬29.85至29.95度范围，2020年人口格网估算有多少人？'),
    ('S03','朱家尖今天的常住人口和应转移人数是多少？'),
    ('M01','2026年10月18日朱家尖东经122.39度、北纬29.89度可以游泳吗？这个点地形高程是多少？'),
]
FOLLOWUP=('A06','那2026年10月18日下午只看赶海呢？')


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def session(base):
    client=requests.Session()
    client.trust_env=False
    response=client.get(base+'/api/assistant/session',timeout=20)
    response.raise_for_status()
    return client,response.json()['token']


def ask(base,client,token,case,conversation_id=None):
    case_id,question=case
    started=datetime.now().astimezone()
    body={'question':question}
    if conversation_id:body['conversation_id']=conversation_id
    response=client.post(base+'/api/assistant/ask',json=body,headers={'X-Assistant-Token':token},timeout=300)
    try:reply=response.json()
    except ValueError:reply={'error':'non_json_response'}
    record={'id':case_id,'question':question,'started_at':started.isoformat(),
            'elapsed_seconds':round((datetime.now().astimezone()-started).total_seconds(),2),
            'http_status':response.status_code,'reply':reply,'expert_accuracy_accepted':False}
    url=reply.get('data_url')
    if isinstance(url,str) and url.startswith('/api/forecast/evidence/'):
        evidence=client.get(base+url,timeout=30)
        if evidence.status_code==200:record['numerical_evidence']=evidence.json()
    return record


def check(record):
    reply=record.get('reply',{});evidence=record.get('numerical_evidence',{})
    checks={'http_success':record['http_status']==200,'generated_answer':reply.get('mode')=='llm' and bool(reply.get('answer')),
            'traceable_tool_evidence':bool(evidence)}
    if evidence.get('state')=='ready':
        checks['final_answer_delivers_tool_data']=reply.get('answer_kind')=='tool_grounded' and bool(reply.get('sources')) and '通用解释与处理思路' not in reply.get('answer','')
    items=evidence.get('items',[evidence])
    activity=next((row for row in items if row.get('kind')=='activity'),None)
    if record['id'].startswith('A') or record['id']=='M01':
        checks['activity_engine_used']=activity is not None
        if activity:
            checks['actual_nc_values']=any(row.get('state')=='ready' and row.get('series') for row in activity.get('products',[]))
            checks['activity_rule_fingerprint']=bool(activity.get('activity_rule_fingerprint'))
            checks['separate_permission_state']=activity.get('permission_state') in {'not_established','verified_open','verified_closed'}
            if record['id']=='A03':checks['requested_hours_preserved']=activity['start'][11:16]=='08:00' and activity['end'][11:16]=='18:00'
            if record['id']=='A05':checks['boat_only']=set(row['activity'] for row in activity['activity_assessments'])=={'boat_fishing'}
            if record['id']=='A06':checks['followup_tidal_flat_only']=set(row['activity'] for row in activity['activity_assessments'])=={'tidal_flat'}
    if record['id'] in {'S01','S02','S03','M01'}:
        spatial=next((row for row in items if row.get('kind')=='spatial'),{})
        checks['spatial_engine_used']=bool(spatial)
        if record['id']=='S01':checks['source_pixel_elevation']=spatial.get('terrain',{}).get('elevation_m')==76
        if record['id'] in {'S01','M01'}:
            checks['elevation_in_final_answer']='76' in reply.get('answer','') and bool(re.search(r'高程|海拔',reply.get('answer','')))
        if record['id']=='S02':
            population=spatial.get('population',{})
            checks['source_grid_population']=population.get('estimated_population')==46254 and population.get('year')==2020
            checks['population_scope_in_final_answer']=bool(re.search(r'有效.{0,30}合计|有值.{0,30}合计|合计.{0,30}有效|合计.{0,30}有值',reply.get('answer',''))) and bool(re.search(r'缺测|无数据',reply.get('answer','')))
        if record['id']=='S03':checks['no_invented_population_total']=spatial.get('state')=='needs_scope' and not spatial.get('population')
    record['technical_checks']=checks
    record['technical_passed']=all(checks.values())
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8768')
    parser.add_argument('--ids',nargs='*')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--output',type=Path,default=ROOT/'tmp'/'activity_spatial_20261017')
    args=parser.parse_args()
    if args.base_url not in {'http://127.0.0.1:8767','http://127.0.0.1:8768'}:
        parser.error('只允许本机已核定的8767或8768测试服务')
    args.output.mkdir(parents=True,exist_ok=True)
    ready=requests.Session();ready.trust_env=False
    deadline=time.monotonic()+45
    while True:
        try:
            response=ready.get(args.base_url+'/api/spatial/status',timeout=3)
            response.raise_for_status()
            break
        except requests.RequestException:
            if time.monotonic()>=deadline:raise RuntimeError('本机新版服务尚未就绪，未开始问答测试')
            time.sleep(.5)
    ready.close()

    def run(case):
        client,token=session(args.base_url)
        records=[check(ask(args.base_url,client,token,case))]
        if case[0]=='A01' and (not args.ids or 'A06' in args.ids):
            cid=records[0]['reply'].get('conversation_id')
            if cid:records.append(check(ask(args.base_url,client,token,FOLLOWUP,cid)))
        client.close()
        return records

    cases=[case for case in CASES if not args.ids or case[0] in args.ids or case[0]=='A01' and 'A06' in args.ids]
    records=[]
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,3))) as pool:
        futures={pool.submit(run,case):case for case in cases}
        for future in as_completed(futures):
            case=futures[future]
            try:finished=future.result()
            except Exception as exc:
                finished=[{'id':case[0],'question':case[1],'error_type':type(exc).__name__,'technical_passed':False}]
            for record in finished:
                save(args.output/(record['id']+'.json'),record)
                records.append(record)
                print(record['id'],record.get('http_status'),record['technical_passed'],flush=True)
    if args.ids:
        already={row['id'] for row in records}
        for file in args.output.glob('*.json'):
            if file.stem not in {case[0] for case in CASES}|{'A06'} or file.stem in already:continue
            records.append(check(json.loads(file.read_text(encoding='utf-8'))))
    records.sort(key=lambda row:row['id'])
    summary={'created_at':datetime.now().astimezone().isoformat(),'base_url':args.base_url,
             'question_count':len(records),'technical_passed':sum(row['technical_passed'] for row in records),
             'expert_accuracy_accepted':False,'note':'真实模型与真实数据的链路检查，不是专家正确率或活动安全认证。',
             'items':[{'id':row['id'],'question':row['question'],'technical_passed':row['technical_passed'],
                       'checks':row.get('technical_checks',{}),'error_type':row.get('error_type')} for row in records]}
    save(args.output/'summary.json',summary)
    save(args.output/'answers.json',records)
    text=['朱家尖活动与空间数据真实问答记录',summary['created_at'],summary['note'],
          '测试入口：'+args.base_url,
          '问题数：'+str(summary['question_count'])+'；技术检查通过：'+str(summary['technical_passed']),
          '筛查配置为未完成业务验证的工程参数；带数值回答不代表活动许可。','']
    for row in records:
        reply=row.get('reply',{})
        text.extend([row['id']+' '+row['question'],str(reply.get('answer') or reply.get('error') or row.get('error_type')),'',
                     '技术检查：'+json.dumps(row.get('technical_checks',{}),ensure_ascii=False),''])
        for source in reply.get('sources',[]):
            source_url=source.get('url','')
            if source_url.startswith('/'):source_url=args.base_url+source_url
            text.append('['+source.get('citation','资料')+'] '+source.get('title','')+' '+source_url)
        text.extend(['原始问题、回答及完整数值证据：'+str(args.output/(row['id']+'.json')),''])
    export=ROOT/'exports'/'朱家尖活动与空间数据_实测问答_20261017.txt'
    export.write_text('\n'.join(text),encoding='utf-8')
    print(json.dumps({key:summary[key] for key in ('question_count','technical_passed','expert_accuracy_accepted')},ensure_ascii=False),flush=True)
    return 0 if all(row['technical_passed'] for row in records) else 1


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
