"""Exercise genuine HTTP follow-ups with server-owned conversation history."""
import argparse,hashlib,json,re
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 out=args.output;out.mkdir(parents=True,exist_ok=False)
 s=requests.Session();s.trust_env=False;base='http://127.0.0.1:8769'
 identity=s.get(base+'/api/assistant/status',timeout=20).json()
 assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in identity['runtime_source_sha256'].items())
 token=s.get(base+'/api/assistant/session',timeout=20).json()['token'];conversation=None;rows=[]
 for key,q in [('family_turn1','我们一家七口，按全国基础版家庭应急物资清单，饮用水至少总共准备多少升？'),
               ('family_turn2','那如果家里只剩四个人，按同一清单饮用水的总量是多少？')]:
  payload={'question':q}
  if conversation:payload['conversation_id']=conversation
  r=s.post(base+'/api/assistant/ask',json=payload,headers={'X-Assistant-Token':token},timeout=(15,300));reply=r.json()
  rows.append({'id':key,'question':q,'http_status':r.status_code,'reply':reply});conversation=reply.get('conversation_id')
  (out/(key+'.json')).write_text(json.dumps(rows[-1],ensure_ascii=False,indent=2),encoding='utf8')
  print(key,r.status_code,reply.get('context_used'),reply.get('resolved_question'),flush=True)
 (out/'answers.json').write_text(json.dumps({'identity':identity,'items':rows},ensure_ascii=False,indent=2),encoding='utf8')
 assert rows[1]['reply'].get('context_used') and re.search(r'12\s*升',rows[1]['reply'].get('answer',''))
 assert rows[1]['reply'].get('sources') and not rows[1]['reply'].get('validation',{}).get('error')


if __name__=='__main__':main()
