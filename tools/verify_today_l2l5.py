"""Opt-in real localhost acceptance: latest issue, cache reuse, plots and report."""
import argparse, hashlib, json, time
from datetime import datetime, timedelta
from pathlib import Path
import sys
import requests
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from forecast_runtime.storage import ForecastStore, BEIJING

def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def fingerprints():
 records=ForecastStore(ROOT/'data/forecast_runtime').grids()
 return {r['filename']:{'size':(p:=Path(r['path'])).stat().st_size,'mtime_ns':p.stat().st_mtime_ns,'sha256':sha(p),
  'batch':r['batch'],'level':r['level'],'product':r['product']} for r in records if r['product'] in {'WAV','SSW'}}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');args=parser.parse_args()
 now=datetime.now(BEIJING);begin=(now+timedelta(hours=1)).replace(minute=0,second=0,microsecond=0)
 date=begin.strftime('%Y年%m月%d日%H时')
 cases=[('WAVE',f'使用最新批次，显示浙江近海{date}的海浪分布图，并给出最大波高和实际覆盖范围。'),
        ('WIND','同一时刻换成海面风分布图，给出最大风速和实际覆盖范围。'),
        ('REUSE',f'使用最新批次，显示浙江近海{date}的海浪分布图，并给出最大波高和实际覆盖范围。'),
        ('REPORT',f'使用最新批次，从{date}起生成浙江未来72小时海浪和海面风影响报告，给出所用批次、实际覆盖、极值和发生时刻，并提供图件与报告下载。')]
 if not args.run:print(json.dumps({'mode':'dry_run','cases':cases},ensure_ascii=False));return
 out=ROOT/'tmp'/('today_l2l5_'+now.strftime('%Y%m%d_%H%M%S'));out.mkdir()
 session=requests.Session();base='http://127.0.0.1:8774'
 initial=session.get(base+'/api/assistant/session',timeout=15);initial.raise_for_status();token=initial.json()['token']
 catalog=session.get(base+'/api/objective/catalog',timeout=15).json();save(out/'catalog_before.json',catalog)
 before=fingerprints();save(out/'files_before.json',before)
 conversation=None;rows=[]
 for cid,q in cases:
  tick=time.monotonic();response=session.post(base+'/api/assistant/ask',json={'question':q,'conversation_id':conversation},
    headers={'X-Assistant-Token':token},timeout=180)
  response.raise_for_status();reply=response.json();conversation=reply.get('conversation_id') or conversation
  row={'id':cid,'question':q,'reply':reply,'seconds':round(time.monotonic()-tick,3),'http_status':response.status_code}
  if reply.get('data_url'):row['tool_result']=session.get(base+reply['data_url'],timeout=20).json()
  action=reply.get('map_action');assert action and action['source_family']=='zjocean_l2_l5'
  assert row['tool_result']['batch'][:10]==now.date().isoformat(),'latest_batch_not_today'
  if cid!='REPORT':
   assert action['valid_time']==begin.isoformat(),'incorrect_valid_time'
   params={k:action[k] for k in ['dataset_id','field','valid_time']};params.update(bounds=','.join(map(str,action['bounds'])),maxsize=180)
   if action['field']=='wind':params['sea_mask']='wave_valid'
   frame=session.get(base+'/api/objective/frame',params=params,timeout=20);frame.raise_for_status();data=frame.json()
   save(out/(cid+'_frame.json'),data);assert data['valid_time']==begin.isoformat() and data['stats']['valid_count']>0
   assert str(data['stats']['maximum']['value']) in reply['answer'],'computed_value_missing_from_published_answer'
   assert '通用解释与处理思路' not in reply['answer'],'generic_guidance_replaced_nc_delivery'
   image=session.get(base+'/api/objective/image.png',params=params,timeout=30);image.raise_for_status()
   assert image.content.startswith(b'\x89PNG\r\n\x1a\n');(out/(cid+'.png')).write_bytes(image.content)
   row['frame_stats']=data['stats'];row['png_sha256']=hashlib.sha256(image.content).hexdigest()
  else:
   tool=row['tool_result'];assert tool['start']==begin.isoformat() and tool['end']==(begin+timedelta(hours=72)).isoformat()
   assert tool['requested_fields']==['wave','wind']
   for field in ['wave','wind']:
    assert str(tool['fields'][field]['maximum']['value']) in reply['answer'],'report_value_missing_from_answer'
   for item in reply['objective_artifacts']:
    artifact=session.get(base+item['url'],timeout=30);artifact.raise_for_status()
    assert hashlib.sha256(artifact.content).hexdigest()==item['sha256'];(out/item['filename']).write_bytes(artifact.content)
   assert (out/'wave_peak.png').exists() and (out/'wind_peak.png').exists()
  # Windows filenames are case-insensitive: REPORT.json would overwrite
  # the downloaded report.json artifact. Keep transaction files distinct.
  save(out/(cid+'.result.json'),row);rows.append(row)
  print(json.dumps({'id':cid,'dataset':action['dataset_id'],'business_state':reply.get('business_state'),
    'valid_time':action['valid_time'],'seconds':row['seconds']},ensure_ascii=False),flush=True)
 after=fingerprints();save(out/'files_after.json',after)
 common=set(before)&set(after);unchanged=all(before[name]==after[name] for name in common)
 assert unchanged,'existing_source_bytes_or_mtime_changed'
 assert rows[0]['reply']['map_action']==rows[2]['reply']['map_action']
 assert rows[0]['tool_result']['frames'][0]['stats']==rows[2]['tool_result']['frames'][0]['stats']
 summary={'tested_at':now.isoformat(),'start':begin.isoformat(),'end':(begin+timedelta(hours=72)).isoformat(),
  'preview':base,'run':out.name,'initial_catalog_latest':catalog['datasets'][0]['dataset_id'],
  'existing_sources_unchanged':unchanged,'existing_source_count':len(common),'new_files':sorted(set(after)-set(before)),
  'repeated_answer_same_map_action':True,'repeated_answer_same_stats':True,'cases':[{
   'id':r['id'],'business_state':r['reply'].get('business_state'),'dataset_id':r['reply'].get('objective_dataset_id'),
   'seconds':r['seconds'],'map_action':r['reply'].get('map_action')} for r in rows]}
 save(out/'summary.json',summary);print(json.dumps({'complete':str(out),'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
