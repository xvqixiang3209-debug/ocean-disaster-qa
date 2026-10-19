"""Development probes from preserved questions plus unseen phrasing variants."""
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse,json,time,sys,hashlib
import requests

ROOT=Path(__file__).resolve().parents[1]
IDS='Q-001 Q-003 Q-005 Q-013 Q-024 Q-037 Q-048 Q-058 R-007 R-020 R-041 R-047 R-060 R-061 R-062 R-063 R-073 R-075'.split()
VARIANTS=[('family5','台风来之前，我们一家五口要备多少饮用水和食品？请引用官方清单，区分每人每天和每人总量。'),
 ('eye_other','热带气旋眼区经过后天气暂时平静，可以出门收东西了吗？'),
 ('qc_other','海洋浮标观测出现尖峰或连续不变的数据，应按哪些质控项目核查？'),
 ('rag_other','怎样让海洋专业问答逐句附证据，搜错资料时自动改检索？请依据研究论文说明方法。'),
 ('strength_other','截至2026年10月17日，接口标识202630的台风蔷琵，各机构预报何时最强、风速分别多少？')]

def main():
 p=argparse.ArgumentParser();p.add_argument('--name',default='probes_candidate1');p.add_argument('--ids',nargs='*');p.add_argument('--variants-only',action='store_true');args=p.parse_args()
 base='http://127.0.0.1:8769';out=ROOT/'tmp/gap137_upgrade_20261018'/args.name;out.mkdir(exist_ok=True)
 rows=json.loads((ROOT/'tmp/qa_full160_phase163_20261018_final/answers.json').read_text(encoding='utf8'))['items']
 cases=[(r['id'],r['question']) for r in rows if r['id'] in (args.ids or IDS)]+([] if args.ids else VARIANTS)
 if args.variants_only:cases=VARIANTS
 s=requests.Session();s.trust_env=False;identity=s.get(base+'/api/assistant/status',timeout=20).json()
 (out/'identity.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2),encoding='utf8')
 def ask(case):
  key,q=case;s=requests.Session();s.trust_env=False;clock=time.monotonic()
  token=s.get(base+'/api/assistant/session',timeout=20).json()['token']
  started=datetime.now().astimezone().isoformat()
  response=s.post(base+'/api/assistant/ask',json={'question':q},headers={'X-Assistant-Token':token},timeout=(15,300))
  reply=response.json();evidence={}
  if reply.get('data_url'):evidence=s.get(base+reply['data_url'],timeout=45).json()
  record={'id':key,'question':q,'started_at':started,'completed_at':datetime.now().astimezone().isoformat(),
    'http_status':response.status_code,'reply':reply,'business_evidence':evidence,'seconds':round(time.monotonic()-clock,2)}
  (out/(key+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
  print(json.dumps({'id':key,'http':response.status_code,'kind':reply.get('answer_kind'),'answerability':reply.get('answerability'),
    'sources':len(reply.get('sources',[])),'error':(reply.get('validation') or {}).get('error'),'seconds':record['seconds']},ensure_ascii=False),flush=True)
  return record
 results=[]
 with ThreadPoolExecutor(max_workers=3) as pool:
  for future in as_completed([pool.submit(ask,case) for case in cases]):results.append(future.result())
 (out/'answers.json').write_text(json.dumps({'items':results,'identity':identity},ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
