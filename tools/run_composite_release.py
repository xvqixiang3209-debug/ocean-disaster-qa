"""Actual HTTP checks for mixed activity, spatial and unmodelled-impact tasks."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import argparse,hashlib,json,requests,time

ROOT=Path(__file__).resolve().parents[1]
CASES=[
 ('activity_flood','2026年10月19日朱家尖南沙的浪高和风速范围是多少，适合游泳吗？这片岸段会不会海水倒灌？'),
 ('spatial_flood','2026年10月19日，东经122.40度、北纬29.90度的地表高程是多少？附近海水倒灌会不会淹没这里？'),
 ('county_flood','2026年10月19日温岭沿岸最高总潮位和最大增水各是多少？会不会海水倒灌？'),
 ('hygiene','家里地面和墙壁被海水泡过，水退后怎么清理，需要全部喷消毒剂吗？请给官方依据。'),
 ('two_dates_flood','2026年10月19日朱家尖南沙适合游泳吗？2026年10月20日朱家尖东沙适合岸钓吗？这两个地方会不会海水倒灌？'),
]


def kinds(result):
 return {result.get('kind')}|set().union(*(kinds(r) for r in result.get('items',[])))


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();out=args.output
 out.mkdir(parents=True,exist_ok=False);s=requests.Session();s.trust_env=False;base='http://127.0.0.1:8769'
 identity=s.get(base+'/api/assistant/status',timeout=20).json()
 assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in identity['runtime_source_sha256'].items())
 (out/'cases_before_run.json').write_text(json.dumps(CASES,ensure_ascii=False,indent=2),encoding='utf8')
 def ask(case):
  key,q=case;s=requests.Session();s.trust_env=False;clock=time.monotonic()
  token=s.get(base+'/api/assistant/session',timeout=20).json()['token']
  r=s.post(base+'/api/assistant/ask',json={'question':q},headers={'X-Assistant-Token':token},timeout=(15,300));reply=r.json()
  evidence=s.get(base+reply['data_url'],timeout=40).json() if reply.get('data_url') else None
  row={'id':key,'question':q,'http_status':r.status_code,'reply':reply,'business_evidence':evidence,'seconds':round(time.monotonic()-clock,2)}
  (out/(key+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
  print(key,r.status_code,kinds(evidence) if evidence else None,reply.get('validation'),flush=True)
  return row
 with ThreadPoolExecutor(max_workers=2) as pool:rows=[f.result() for f in as_completed([pool.submit(ask,c) for c in CASES])]
 (out/'answers.json').write_text(json.dumps({'identity':identity,'items':rows},ensure_ascii=False,indent=2),encoding='utf8')
 by={r['id']:r for r in rows}
 assert all(r['http_status']==200 for r in rows)
 assert {'activity','coastal'}<=kinds(by['activity_flood']['business_evidence'])
 assert 'spatial' in kinds(by['spatial_flood']['business_evidence'])
 coast=by['county_flood']['business_evidence']['items'][0]
 assert coast['requested_count']<=20 and all('温岭' in r['county'] for r in coast['results'])
 assert any('ndcpa.gov.cn' in s.get('url','') for s in by['hygiene']['reply'].get('sources',[]))
 assert 'activity' in kinds(by['two_dates_flood']['business_evidence'])
 (out/'checks.json').write_text(json.dumps({'http_ok':True,'activity_not_overridden':True,'spatial_not_overridden':True,
      'county_scope_preserved':True,'official_hygiene_source':True,'two_date_activity_preserved':True},ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
