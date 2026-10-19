"""Capture public-service family regressions through the browser's HTTP API."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
CASES=[
 ('M1','mosquito','灾后蚊子特别多，会不会传染病？怎么防？','R-094'),
 ('M2','mosquito','台风暴雨后舟山家里花盆和院子有积水，蚊虫很多，怎么清理孳生地和防叮咬？',None),
 ('M3','mosquito','洪水退后宁波蚊虫变多，是否就说明当地已经暴发登革热？应该如何防蚊？',None),
 ('C1','channels','浙江有哪些官方渠道可以查台风和海洋预警？','Q-050'),
 ('C2','channels','宁波居民想查询气象部门的台风预警和海洋部门的海浪警报，分别去哪里查？',None),
 ('C3','channels','浙江海洋预报网上有台风路径，是不是就能替代气象台发布的台风预警？我应怎样查询两类信息？',None),
 ('A1','aquaculture','我是象山县养殖户，台风前怎么加固网箱和池塘？损失了能赔吗？','R-080'),
 ('A2','aquaculture','温州海上重力式网箱台风前要检查哪些缆绳、框架和网衣部位？请给有出处的技术步骤。',None),
 ('A3','aquaculture','台州海水养殖池塘防台风时，堤埂、进排水设施和备用设备怎么检查加固？',None),
 ('G1','relief','假设宁波市鄞州区家里因台风受灾严重，能申请什么政府救助？','R-097'),
 ('G2','relief','台风把家里住房损坏，想申请政府倒损住房恢复重建救助，应该先向村里还是保险公司申请，程序是什么？',None),
 ('G3','relief','台风后政府的过渡期生活救助、冬春救助和巨灾保险赔付是不是一回事？分别依据什么办理？',None),
]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--workers',type=int,default=3);args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    base='http://127.0.0.1:8769'
    client=requests.Session();client.trust_env=False
    build=client.get(base+'/api/assistant/status',timeout=15).json();client.close()
    for name,digest in build['runtime_source_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    (args.output/'runtime.json').write_text(json.dumps(build,ensure_ascii=False,indent=2),encoding='utf-8')
    def task(case):
        ident,family,question,old=case;path=args.output/(ident+'.json')
        if path.exists():return json.loads(path.read_text(encoding='utf-8'))
        s=requests.Session();s.trust_env=False;started=datetime.now(timezone.utc).isoformat()
        try:
            token=s.get(base+'/api/assistant/session',timeout=20).json()['token']
            response=s.post(base+'/api/assistant/ask',json={'question':question},headers={'X-Assistant-Token':token},timeout=300)
            reply=response.json()
            record={'id':ident,'family':family,'question':question,'baseline_id':old,'http_status':response.status_code,
                    'started_at':started,'completed_at':datetime.now(timezone.utc).isoformat(),'reply':reply,
                    'answer_sha256':hashlib.sha256(reply.get('answer','').encode()).hexdigest()}
        except Exception as exc:record={'id':ident,'family':family,'question':question,'transport_error':type(exc).__name__}
        finally:s.close()
        path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');return record
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for f in as_completed([pool.submit(task,c) for c in CASES]):
            r=f.result();results.append(r)
            print(json.dumps({'completed':len(results),'id':r['id'],'http':r.get('http_status'),
                  'answerability':r.get('reply',{}).get('answerability')},ensure_ascii=False),flush=True)
    order={c[0]:i for i,c in enumerate(CASES)};results.sort(key=lambda r:order[r['id']])
    (args.output/'answers.json').write_text(json.dumps({'items':results,'expert_accepted':False},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
