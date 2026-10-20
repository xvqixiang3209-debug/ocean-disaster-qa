"""Bounded, opt-in real QA recording from a reviewed case plan. No retries.

Dry-run by default; raw model IO is private under tmp/replay. A running service
is not changed: the normal Flask API is exercised in an isolated test client.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.record_p1_p6_live import forced_environment,capture_sources,compare_sources,write_json,digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--record-private',action='store_true')
    parser.add_argument('--max-transactions',type=int,default=3)
    args=parser.parse_args()
    raw=args.plan.read_bytes();plan=json.loads(raw.decode('utf-8-sig'));cases=plan['cases']
    if not 1<=len(cases)<=args.max_transactions<=12:raise ValueError('invalid_bounded_case_count')
    if len({c['id'] for c in cases})!=len(cases):raise ValueError('duplicate_case_id')
    if any(not isinstance(c['question'],str) or not 1<=len(c['question'])<=2000 for c in cases):raise ValueError('invalid_case_question')
    if not args.run:
        print(json.dumps({'mode':'dry_run','transactions':len(cases),'cases':cases},ensure_ascii=False));return
    if not args.record_private:parser.error('--run requires --record-private')
    from marine_kb.replay import ModelCassette,RecordingCaller
    destination=ROOT/'tmp/replay'/('time_delivery_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    destination.mkdir(parents=True,exist_ok=False)
    write_json(destination/'run_plan.json',{'cases':cases,'plan_sha256':digest(raw),
        'transactions_authorized':args.max_transactions,'automatic_retries':0,
        'authorization_context':'User requested current, historical and multi-subquestion real-system testing after code changes.',
        'private_raw_recording':True,'expert_accepted':False})
    print(json.dumps({'private_run':destination.name,'transactions':len(cases)},ensure_ascii=False),flush=True)
    with forced_environment():
        before=capture_sources(destination,'before')
        (destination/'runner.py').write_bytes(Path(__file__).read_bytes())
        from server import app,marine_qa
        if not marine_qa.external_enabled or not marine_qa.model_configured:raise RuntimeError('external_model_not_enabled')
        caller=marine_qa.model_caller;fetch=marine_qa.web_supplement.fetch;local=marine_qa._collect_local_hits
        results=[]
        try:
            for case in cases:
                started=time.monotonic()
                record={'id':case['id'],'question':case['question'],'started_at':datetime.now(timezone.utc).isoformat()}
                with ModelCassette(destination/(case['id']+'.cassette.jsonl'),mode='record',allow_sensitive_recording=True) as journal:
                    marine_qa.model_caller=RecordingCaller(caller,journal)
                    def wrapped_fetch(question,**options):
                        return journal.invoke('public_retrieval',{'question':question,'options':options},lambda:fetch(question,**options))
                    def wrapped_local(question,normalized,queries,parts,meta,dynamic,sid):
                        def live():
                            hits=local(question,normalized,queries,parts,meta,dynamic,sid)
                            return {'hits':hits,'metadata':dict(meta)}
                        value=journal.invoke('local_retrieval',{'question':question,'normalized':normalized,'queries':queries,
                            'parts':parts,'dynamic':dynamic,'sid':sid},live)
                        meta.update(value['metadata']);return value['hits']
                    marine_qa.web_supplement.fetch=wrapped_fetch;marine_qa._collect_local_hits=wrapped_local
                    with app.test_client() as client:
                        token=client.get('/api/assistant/session',base_url='http://127.0.0.1').get_json()['token']
                        response=client.post('/api/assistant/ask',json={'question':case['question']},
                            headers={'X-Assistant-Token':token},base_url='http://127.0.0.1')
                        record.update(http_status=response.status_code,reply=response.get_json())
                        data_url=(record.get('reply')or{}).get('data_url')
                        if data_url and data_url.startswith('/api/forecast/evidence/'):
                            tool=client.get(data_url,base_url='http://127.0.0.1')
                            record['tool_result']=tool.get_json()
                record.update(elapsed_seconds=round(time.monotonic()-started,3),completed_at=datetime.now(timezone.utc).isoformat())
                write_json(destination/(case['id']+'.result.json'),record);results.append(record)
                print(json.dumps({'id':case['id'],'http_status':record['http_status'],'seconds':record['elapsed_seconds'],
                    'answerability':(record.get('reply')or{}).get('answerability')},ensure_ascii=False),flush=True)
        finally:
            marine_qa.model_caller=caller;marine_qa.web_supplement.fetch=fetch;marine_qa._collect_local_hits=local
            after=capture_sources(destination,'after');stability=compare_sources(before,after)
            write_json(destination/'run_completed.json',{'attempted':len(results),'source_stability':stability,'automatic_retries':0})
        if not stability['stable']:raise RuntimeError('source_changed_during_live_run')
    print(json.dumps({'complete':destination.name,'transactions':len(results)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
