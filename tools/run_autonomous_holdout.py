"""Additional phrasing checks, frozen before observing their answers.

These are development generalisation probes, not independent expert scoring.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from pathlib import Path
import argparse,hashlib,json,time,requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/autonomous_v165_20261018/additional_cases'
CASES=[
 ('wave_boundary','有效波高恰好6米时应该叫巨浪还是狂浪？有效波高是单个最高浪吗？'),
 ('family7','我们一家七口，台风前按照官方家庭应急基础清单最低要备多少饮用水？这个量是每天的吗？'),
 ('county_boundary','2026年10月19日温岭沿岸会发生漫堤吗？请先列出能实际提取的潮位和增水数据，再说明判断限制。'),
 ('paper_scope','截至2026年10月17日，国外用神经网络预报海平面的研究有哪些进展和局限？能直接把论文的误差成绩作为浙江业务准确率吗？'),
 ('clock_scope','截至2026年10月16日，这个系统已经接入了多少网格文件、每隔多久更新？如果没有当日记录，请区分现在的配置。'),
 ('warning_difference','我在沿海，台风预警颜色和风暴潮警报颜色不同，该只看较高的一个吗？两者分别提示什么风险？'),
]


def main():
 global OUT
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
 if args.output:OUT=args.output
 OUT.mkdir(parents=True,exist_ok=False)
 (OUT/'cases_before_run.json').write_text(json.dumps(CASES,ensure_ascii=False,indent=2),encoding='utf8')
 session=requests.Session();session.trust_env=False;base='http://127.0.0.1:8769'
 identity=session.get(base+'/api/assistant/status',timeout=20).json()
 assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in identity['runtime_source_sha256'].items())
 (OUT/'identity.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2),encoding='utf8')
 def ask(case):
  key,q=case;s=requests.Session();s.trust_env=False;clock=time.monotonic()
  token=s.get(base+'/api/assistant/session',timeout=20).json()['token']
  response=s.post(base+'/api/assistant/ask',json={'question':q},headers={'X-Assistant-Token':token},timeout=(15,300))
  reply=response.json();evidence=s.get(base+reply['data_url'],timeout=45).json() if reply.get('data_url') else None
  record={'id':key,'question':q,'http_status':response.status_code,'reply':reply,'business_evidence':evidence,
          'completed_at':datetime.now().astimezone().isoformat(),'seconds':round(time.monotonic()-clock,2)}
  (OUT/(key+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
  print(key,response.status_code,len(reply.get('sources',[])),reply.get('answerability'),(reply.get('validation') or {}).get('error'),flush=True)
  return record
 with ThreadPoolExecutor(max_workers=2) as pool:records=[future.result() for future in as_completed([pool.submit(ask,c) for c in CASES])]
 assert identity==session.get(base+'/api/assistant/status',timeout=20).json()
 (OUT/'answers.json').write_text(json.dumps({'identity':identity,'items':records},ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
