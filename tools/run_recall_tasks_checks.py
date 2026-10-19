"""Paired real HTTP probes; no ideal answers or credentials in artifacts."""
import argparse, json, hashlib, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
import requests

CASES=[
 ('season_original','台风只在夏天有吗？几月到几月要特别防？'),
 ('season_other_region','福建秋冬还可能有台风吗？一年中主要防范哪些月份？'),
 ('season_paraphrase','浙江沿海一年四季都要留意热带气旋吗？通常哪个季节较活跃？'),
 ('return_period','百年一遇是不是今年遇到后就要再等一百年？'),
 ('definition','有效波高和肉眼看到的最大一阵浪有什么不同？'),
 ('two_beaches','2026年10月18日08时至18时，朱家尖南沙和东沙分别能游泳吗？请分别给海况和开放依据。'),
 ('two_regions','2026年10月18日松兰山可以岸钓吗；渔寮可以赶海吗？分别给数据和还需核实的条件。'),
 ('two_dates','2026年10月18日松兰山能岸钓吗；2026年10月19日渔寮能游泳吗？'),
 ('reference_object','2026年10月18日朱家尖南沙浴场能游泳吗？东沙浴场的开放消息可以作为依据吗？'),
 ('four_activities','2026年10月18日朱家尖赶海、游泳、岸钓和船钓分别需要什么条件？哪些已有数据？'),
 ('historical_cutoff','截至2026年10月17日21时30分，查询10月18日08时至18时朱家尖的浪高和阵风，说明采用批次。'),
 ('outside_service','2026年10月18日连云港海州湾浴场能游泳吗？请核对对应场地。'),
]

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=['before','after'],required=True)
    p.add_argument('--case',action='append')
    args=p.parse_args();root=Path(__file__).resolve().parents[1]
    out=root/'tmp/recall_tasks_upgrade_20261018'/args.stage;out.mkdir(parents=True,exist_ok=True)
    base='http://127.0.0.1:8769'
    client=requests.Session();client.trust_env=False
    identity=client.get(base+'/api/assistant/status',timeout=30).json()
    (out/'identity.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2),encoding='utf8')
    def ask(case):
        key,q=case;path=out/(key+'.json')
        if path.exists():return json.loads(path.read_text(encoding='utf8'))
        s=requests.Session();s.trust_env=False;clock=time.monotonic()
        token=s.get(base+'/api/assistant/session',timeout=30).json()['token']
        response=s.post(base+'/api/assistant/ask',json={'question':q},headers={'X-Assistant-Token':token},timeout=(15,300))
        reply=response.json();evidence={}
        if reply.get('data_url'):evidence=s.get(base+reply['data_url'],timeout=45).json()
        row={'id':key,'question':q,'stage':args.stage,'completed_at':datetime.now().astimezone().isoformat(),
             'http_status':response.status_code,'reply':reply,'business_evidence':evidence,'elapsed_seconds':round(time.monotonic()-clock,2),
             'answer_sha256':hashlib.sha256(reply.get('answer','').encode()).hexdigest(),'expert_accepted':False}
        path.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8');return row
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(ask,case) for case in CASES if not args.case or case[0] in args.case]):
            row=f.result();rows.append(row);print(json.dumps({'completed':len(rows),'stage':args.stage,'id':row['id'],'http':row['http_status'],'mode':row['reply'].get('mode')},ensure_ascii=False),flush=True)
    rows.sort(key=lambda r:r['id'])
    (out/'answers.json').write_text(json.dumps({'items':rows,'identity':identity},ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':
    import sys
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
