"""Opt-in five real same-session transactions; private recording, no retries."""
import argparse, hashlib, json, sys, time, uuid
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
CASES=[
 ('MAP','显示浙江近海2026年10月21日08时的海浪分布图，并给出该范围最大波高。'),
 ('WIND','同一时刻换成海面风分布图，给出最大风速。'),
 ('TIME','明天10时呢？'),
 ('REPORT','同一批次，从2026年10月21日00时起，生成浙江未来72小时海浪和海面风影响报告，给出具体数据、分区和重点时段，并提供图件及报告下载。'),
 ('PRESSURE','同一批次，显示2026年10月21日08时浙江近海的气压图。'),
]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true');args=p.parse_args()
 if not args.run:print(json.dumps({'mode':'dry_run','cases':CASES},ensure_ascii=False));return
 from tools.record_p1_p6_live import forced_environment,capture_sources,compare_sources,write_json
 from marine_kb.replay import ModelCassette,RecordingCaller
 folder=ROOT/'tmp/replay'/('l2l5_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
 def capture(phase):
  value=capture_sources(folder,phase)
  value[Path(__file__).relative_to(ROOT).as_posix()]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
  write_json(folder/('source_'+phase+'.json'),value);return value
 with forced_environment():
  before=capture('before')
  from server import app,marine_qa
  caller=marine_qa.model_caller;attempted=0
  print(json.dumps({'private_run':folder.name,'qa_transactions':5}),flush=True)
  with app.test_client() as client:
   token=client.get('/api/assistant/session',base_url='http://127.0.0.1').get_json()['token'];conversation=None
   try:
    for cid,question in CASES:
     started=time.monotonic();record={'id':cid,'question':question,'started_at':datetime.now(timezone.utc).isoformat()}
     with ModelCassette(folder/(cid+'.cassette.jsonl'),mode='record',allow_sensitive_recording=True) as tape:
      marine_qa.model_caller=RecordingCaller(caller,tape)
      response=client.post('/api/assistant/ask',json={'question':question,'conversation_id':conversation},headers={'X-Assistant-Token':token},base_url='http://127.0.0.1')
      reply=response.get_json();record.update(http_status=response.status_code,reply=reply);conversation=reply.get('conversation_id') or conversation
      url=reply.get('data_url')
      if url and url.startswith('/api/forecast/evidence/'):record['tool_result']=client.get(url,base_url='http://127.0.0.1').get_json()
      record['cassette_summary']=tape.summary()
     record.update(elapsed_seconds=round(time.monotonic()-started,3),completed_at=datetime.now(timezone.utc).isoformat());write_json(folder/(cid+'.result.json'),record);attempted+=1
     print(json.dumps({'id':cid,'http_status':response.status_code,'route':reply.get('answer_meta',{}).get('route'),'action':reply.get('map_action'),'seconds':record['elapsed_seconds']},ensure_ascii=False),flush=True)
   finally:
    marine_qa.model_caller=caller;after=capture('after');stability=compare_sources(before,after)
    write_json(folder/'run_completed.json',{'attempted':attempted,'source_stability':stability,'automatic_retries':0})
   if not stability['stable']:raise RuntimeError('source_changed_during_live_run')
 print(json.dumps({'complete':folder.name,'attempted':attempted}),flush=True)
if __name__=='__main__':main()
