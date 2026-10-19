"""Real Zhejiang activity questions, preserving answers and program evidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import requests

from verify_activity_spatial import ROOT,session,ask,save


def in_process_session(base):
    """Exercise the real Flask routes without a listener or background worker."""
    os.environ['MARINE_FORECAST_WORKER']='false'
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from server import app
    class Response:
        def __init__(self,response):self.response=response;self.status_code=response.status_code
        def json(self):return self.response.get_json()
        def raise_for_status(self):
            if self.status_code>=400:raise RuntimeError('Flask response '+str(self.status_code))
    class Client:
        def __init__(self):self.client=app.test_client()
        def get(self,url,**kwargs):
            return Response(self.client.get(url.removeprefix(base),base_url=base,
                environ_overrides={'REMOTE_ADDR':'127.0.0.1'}))
        def post(self,url,**kwargs):
            return Response(self.client.post(url.removeprefix(base),base_url=base,
                json=kwargs.get('json'),headers=kwargs.get('headers'),
                environ_overrides={'REMOTE_ADDR':'127.0.0.1'}))
        def close(self):pass
    client=Client();response=client.get(base+'/api/assistant/session');response.raise_for_status()
    return client,response.json()['token']


CASES=[
 ('ZJJ01','2026年10月18日朱家尖能赶海、游泳、岸钓和船钓吗？请给具体海况和适用官方规则。'),
 ('ZJJ02','2026年10月18日8点至18点，朱家尖南沙可以游泳吗？'),
 ('ZJJ03','2026年10月18日朱家尖岸钓适合吗？给具体数据和规则依据。'),
 ('ZJJ04','2026年10月18日朱家尖可以船钓吗？'),
 ('NB01','2026年10月18日宁波象山松兰山可以游泳吗？给具体海况和水质记录日期。'),
 ('TZ01','2026年10月18日温岭金沙滩可以游泳吗？'),
 ('WZ01','2026年10月18日洞头大沙岙可以游泳吗？'),
 ('WZ02','2026年10月18日苍南渔寮可以赶海吗？'),
 ('ZS01','2026年10月18日嵊泗基湖沙滩可以游泳吗？'),
 ('JX01','2026年10月18日嘉兴市海盐县澉浦镇可以赶海吗？'),
 ('DS01','2026年10月18日岱山县岱东镇可以船钓吗？'),
 ('NJ01','2026年10月18日南麂大沙岙可以游泳吗？'),
 ('ALL01','浙江赶海、游泳、岸钓和船钓分别适用哪些规则？'),
 ('ALL02','2026年10月18日浙江可以赶海吗？'),
 ('PR01','马鞍列岛保护区岸钓需要哪些条件和手续？'),
 ('PR02','马鞍列岛保护区内乘休闲渔船海钓，按什么规定办理手续，可以免办个人海钓许可证吗？'),
 ('M01','2026年10月18日朱家尖东经122.39度、北纬29.89度可以游泳吗？这个点地形高程是多少？'),
]
FOLLOWUP=('ZJJ05','那2026年10月18日下午只看岸钓呢？')


def check(record):
    reply=record.get('reply',{});evidence=record.get('numerical_evidence',{})
    checks={'http_success':record.get('http_status')==200,
            'generated_answer':reply.get('mode')=='llm' and bool(reply.get('answer')),
            'traceable_program_evidence':bool(evidence)}
    activity=next((r for r in evidence.get('items',[evidence]) if r.get('kind')=='activity'),None)
    if activity and activity.get('activity_assessments'):
        assessments=activity['activity_assessments']
        checks['official_rule_mode']=activity.get('activity_rule_mode')=='official_catalog'
        checks['legacy_limits_removed']=all(r['numerical_screening'].get('limits')=={} for r in assessments)
        checks['no_fixed_tide_access_window']=all(not (r.get('tide_reference') or {}).get('low_tide_reference_windows') for r in assessments)
        checks['scoped_official_sources']=all((r.get('official_policy_selection') or {}).get('eligible_policies') for r in assessments)
        checks['no_daishan_reference_as_province_rule']=all('daishan-recreational-fishery-risk-2022' not in {
            p['id'] for p in (r.get('official_policy_selection') or {}).get('eligible_policies',[])} for r in assessments)
        if record['id']=='ZJJ01':checks['all_four_activities']=len(assessments)==4
        if record['id']=='ZJJ02':checks['exact_requested_window']=activity['start'][11:16]=='08:00' and activity['end'][11:16]=='18:00'
        if record['id']=='ZJJ05':checks['shore_only_followup']={r['activity'] for r in assessments}=={'shore_fishing'}
        if record['id'] in {'NB01','TZ01','WZ01','WZ02','ZS01','JX01','DS01'}:
            checks['not_using_zhujiajian_point']=activity.get('location',{}).get('key')!='zhujiajian'
    if record['id'] in {'ALL02','NJ01'}:
        checks['requires_specific_or_reliable_location']=bool(activity) and activity.get('state')=='needs_location'
        checks['no_invented_sampling_point']=bool(activity) and not activity.get('products')
    if record['id']=='ALL02':
        checks['no_false_distant_date_reason']=not bool(re.search(r'尚远|日期太远|超出.*预报',reply.get('answer','')))
    if record['id'] in {'ALL01','PR01','PR02'}:
        checks['rule_lookup_without_nc']=evidence.get('kind')=='activity_rules' and not evidence.get('products')
        policies={p['id'] for r in evidence.get('items',[]) for p in r.get('eligible_policies',[])}
        checks['applicable_official_rules']=bool(policies)
        protected='zhoushan-protected-area-fishing-2023'
        checks['protected_area_scope']=protected in policies if record['id'].startswith('PR') else protected not in policies
        if record['id']=='PR01':
            answer=reply.get('answer','')
            checks['known_permit_procedure_delivered']=all(re.search(pattern,answer) for pattern in (
                r'县|区',r'渔业.*主管部门',r'身份证|身份.*证明',r'考试',r'承诺',r'10\s*个?工作日|十个工作日'))
    if record['id']=='M01':checks['compound_elevation_survives']='76' in reply.get('answer','') and bool(re.search(r'高程|海拔',reply.get('answer','')))
    record['technical_checks']=checks;record['technical_passed']=all(checks.values())
    return record


def export(output,records,base):
    records.sort(key=lambda row:row['id'])
    summary={'created_at':datetime.now().astimezone().isoformat(),'base_url':base,
             'question_count':len(records),'technical_passed':sum(r['technical_passed'] for r in records),
             'test_transport':sorted({r.get('test_transport','HTTP') for r in records}),
             'expert_accuracy_accepted':False,
             'note':'真实DeepSeek、实际NC和官方规则的技术链路核查；不代表现场安全许可或专家正确率。',
             'items':[{'id':r['id'],'question':r['question'],'technical_passed':r['technical_passed'],
                       'checks':r.get('technical_checks',{}),'error_type':r.get('error_type')} for r in records]}
    save(output/'summary.json',summary);save(output/'answers.json',records)
    lines=['浙江沿海活动官方规则接入：真实问答记录',summary['created_at'],summary['note'],
           '请求方式：'+'；'.join(summary['test_transport']),
           f"测试{len(records)}题，技术检查通过{summary['technical_passed']}题",'']
    for row in records:
        reply=row.get('reply',{})
        lines.extend([row['id']+' '+row['question'],reply.get('answer') or reply.get('error') or str(row.get('error_type')),''])
        for source in reply.get('sources',[]):
            url=source.get('url','');url=base+url if url.startswith('/') else url
            lines.append('['+source.get('citation','资料')+'] '+source.get('title','')+' '+url)
        lines.extend(['数据及规则完整证据：'+str(output/(row['id']+'.json')),
                      '技术检查：'+json.dumps(row.get('technical_checks',{}),ensure_ascii=False),''])
    (ROOT/'exports'/'浙江沿海活动官方规则_真实问答_20261017.txt').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({key:summary[key] for key in ('question_count','technical_passed','expert_accuracy_accepted')}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',choices=['http://127.0.0.1:8766','http://127.0.0.1:8768','http://127.0.0.1:8769'],default='http://127.0.0.1:8766')
    parser.add_argument('--output',type=Path,default=ROOT/'tmp'/'official_activity_policy_20261017')
    parser.add_argument('--ids',nargs='*')
    parser.add_argument('--in-process',action='store_true',help='Use actual Flask routes without starting a service; still calls real data providers and DeepSeek.')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.in_process:
        # Import once before concurrent cases construct independent cookie clients.
        in_process_session(args.base_url)[0].close()
    else:
        ready=requests.Session();ready.trust_env=False
        deadline=time.monotonic()+45
        while True:
            try:
                response=ready.get(args.base_url+'/api/spatial/status',timeout=3)
                response.raise_for_status();break
            except requests.RequestException:
                if time.monotonic()>=deadline:raise RuntimeError('新版本地服务尚未就绪，未开始真实问答')
                time.sleep(.5)
        ready.close()
    def run(case):
        client,token=(in_process_session if args.in_process else session)(args.base_url)
        try:
            rows=[check(ask(args.base_url,client,token,case))]
            if case[0]=='ZJJ01' and (not args.ids or 'ZJJ05' in args.ids):
                cid=rows[0].get('reply',{}).get('conversation_id')
                if cid:rows.append(check(ask(args.base_url,client,token,FOLLOWUP,cid)))
            for row in rows:row['test_transport']='Flask in-process routes; real DeepSeek and data' if args.in_process else 'HTTP'
            return rows
        finally:client.close()
    cases=[r for r in CASES if not args.ids or r[0] in args.ids or r[0]=='ZJJ01' and 'ZJJ05' in args.ids]
    records=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(run,case):case for case in cases}
        for future in as_completed(futures):
            case=futures[future]
            try:rows=future.result()
            except Exception as exc:rows=[{'id':case[0],'question':case[1],'technical_passed':False,'error_type':type(exc).__name__}]
            for row in rows:
                save(args.output/(row['id']+'.json'),row);records.append(row)
                print(row['id'],row.get('http_status'),row['technical_passed'],flush=True)
    if args.ids:
        done={r['id'] for r in records}
        for identity in {r[0] for r in CASES}|{'ZJJ05'}:
            path=args.output/(identity+'.json')
            if identity not in done and path.exists():records.append(check(json.loads(path.read_text(encoding='utf-8'))))
    export(args.output,records,args.base_url)
    return 0 if all(r['technical_passed'] for r in records) else 1


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
