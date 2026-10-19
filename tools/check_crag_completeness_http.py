"""Save actual family smoke tests without replacing full160 answers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import argparse,json,time
from pathlib import Path
import requests

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--ids',default='Q-003,Q-007,Q-012,Q-050,Q-055,R-004,R-010,R-037');a=p.parse_args();a.output.mkdir(exist_ok=False)
 m=json.loads(Path('tmp/qa_full160_crag180_20261019_before/manifest.json').read_text('utf-8'))
 cases=[(r['id'],r['question']) for r in m['items'] if r['id'] in a.ids.split(',')]
 cases += [('X01','查询2026年10月18日08时至18时东经122.4北纬29.9海域浪高和波向；请区分波向角度与来向定义。'),('X02','2010年至2020年西北太平洋热带气旋各月份样本数是多少？这能说明舟山某天安全吗？'),
           ('X03','查询2026年10月20日06时至12时东经121.8北纬28.5海域风速浪高；说明为什么不能等同当地实况。')]
 def run(case):
  ident,q=case;s=requests.Session();s.trust_env=False;t=s.get('http://127.0.0.1:8769/api/assistant/session').json()['token'];start=time.monotonic()
  resp=s.post('http://127.0.0.1:8769/api/assistant/ask',json={'question':q},headers={'X-Assistant-Token':t},timeout=300);r=resp.json()
  e=s.get('http://127.0.0.1:8769'+r['data_url']).json() if r.get('data_url') else None
  row={'id':ident,'question':q,'http_status':resp.status_code,'reply':r,'business_evidence':e,'seconds':round(time.monotonic()-start,2),'tested_at':datetime.now().astimezone().isoformat()}
  (a.output/(ident+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'id':ident,'http':resp.status_code,'kind':r.get('answer_kind'),'validation':r.get('validation'),'seconds':row['seconds']},ensure_ascii=False),flush=True)
 with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(run,cases))
if __name__=='__main__':main()
