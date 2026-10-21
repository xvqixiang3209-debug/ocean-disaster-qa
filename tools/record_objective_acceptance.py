"""Opt-in four QA transactions, same conversation; no automatic retry.

Raw model IO is private. Default dry-run does not import the server or a model.
"""
import argparse
from datetime import datetime,timezone
import hashlib,json,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

CASES=[
 ('KNOWLEDGE','海浪预警和风暴潮预警分别是什么意思，预警等级与应急响应有什么区别？'),
 ('ANALOGUE','用蔷琵（接口标识202630）的中国机构预报路径，查询3个最相似的历史台风，列出名称、年份和路径相似度。'),
 ('REPORT','从2026年10月20日20时起，生成浙江未来72小时海浪、海面风和气压影响报告，给出具体数据、重点海域与时段，并提供图件和报告下载。'),
 ('FOLLOWUP','同一批次和时段，北部、中部、南部三个参考分区中哪个浪高最大？请列出具体数值、位置和时刻。'),
]

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true');p.add_argument('--record-private',action='store_true');args=p.parse_args()
 if not args.run:print(json.dumps({'mode':'dry_run','qa_transactions':len(CASES),'cases':CASES},ensure_ascii=False));return
 if not args.record_private:p.error('--run requires --record-private')
 from tools.record_p1_p6_live import forced_environment,capture_sources,compare_sources,write_json
 from marine_kb.replay import ModelCassette,RecordingCaller
 folder=ROOT/'tmp/replay'/('objective_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
 def capture(phase):
  value=capture_sources(folder,phase)
  for name in ['frontend/index.html','frontend/objective_panel.js','frontend/objective_panel.css','frontend/forecast_panel.js','frontend/map_assistant.js']:
   value[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
  for path in [Path(__file__), *(ROOT/'marine_kb/data').glob('*.json')]:
   if path.is_file():value[path.relative_to(ROOT).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
  write_json(folder/('source_'+phase+'.json'),value);return value
 with forced_environment():
  before=capture('before')
  from server import app,marine_qa
  if not marine_qa.external_enabled or not marine_qa.model_configured:raise RuntimeError('external_model_not_enabled')
  caller=marine_qa.model_caller;attempted=0
  print(json.dumps({'private_run':folder.name,'qa_transactions':4}),flush=True)
  with app.test_client() as client:
   token=client.get('/api/assistant/session',base_url='http://127.0.0.1').get_json()['token'];conversation=None
   try:
    for cid,question in CASES:
     started=time.monotonic();record={'id':cid,'question':question,'started_at':datetime.now(timezone.utc).isoformat()}
     with ModelCassette(folder/(cid+'.cassette.jsonl'),mode='record',allow_sensitive_recording=True) as tape:
      marine_qa.model_caller=RecordingCaller(caller,tape)
      response=client.post('/api/assistant/ask',json={'question':question,'conversation_id':conversation},
        headers={'X-Assistant-Token':token},base_url='http://127.0.0.1')
      record.update(http_status=response.status_code,reply=response.get_json());conversation=record['reply'].get('conversation_id')or conversation
      url=record['reply'].get('data_url')
      if url and url.startswith('/api/forecast/evidence/'):record['tool_result']=client.get(url,base_url='http://127.0.0.1').get_json()
      record['cassette_summary']=tape.summary()
     record.update(elapsed_seconds=round(time.monotonic()-started,3),completed_at=datetime.now(timezone.utc).isoformat());write_json(folder/(cid+'.result.json'),record);attempted+=1
     print(json.dumps({'id':cid,'http_status':record['http_status'],'answerability':record['reply'].get('answerability'),'seconds':record['elapsed_seconds']},ensure_ascii=False),flush=True)
   finally:
    marine_qa.model_caller=caller;after=capture('after');stability=compare_sources(before,after)
    write_json(folder/'run_completed.json',{'attempted':attempted,'source_stability':stability,'automatic_retries':0})
   if not stability['stable']:raise RuntimeError('source_changed_during_live_run')
 print(json.dumps({'complete':folder.name,'attempted':attempted}),flush=True)

if __name__=='__main__':main()
