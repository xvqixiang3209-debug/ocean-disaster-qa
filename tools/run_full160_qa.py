"""Capture independent questions through the same HTTP route as the live UI.

No ideal answers, model tokens, cookies or provider credentials are exported.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import time

import requests

ROOT=Path(__file__).resolve().parents[1]
ZONE=timezone(timedelta(hours=8))


def now():return datetime.now(ZONE).isoformat(timespec='seconds')
def save(path,value):
    temporary=path.with_suffix(path.suffix+'.writing')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    temporary.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',choices=['http://127.0.0.1:8766','http://127.0.0.1:8769'],default='http://127.0.0.1:8769')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,choices=range(1,5),default=4)
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
    items=manifest['items']
    if len(items)!=160 or len({r['id'] for r in items})!=160:raise ValueError('Expected 160 unique original IDs')
    digest=hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    base=args.base_url
    session=requests.Session();session.trust_env=False
    health=session.get(base+'/api/forecast/activity/rules',timeout=30);health.raise_for_status()
    if health.json().get('kind')!='activity_rules':raise RuntimeError('Official activity rule route is unavailable')
    identity_path=out/'run_identity.json'
    running=session.get(base+'/api/assistant/status',timeout=15);running.raise_for_status();running=running.json()
    for name,digest_on_server in running['runtime_source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest_on_server:raise RuntimeError('Running process is older than the files on disk')
    identity={'run_id':args.run_id,'base_url':base,'manifest_sha256':digest,
              'created_at':now(),'request_route':'/api/assistant/ask','transport':'real HTTP',
              'source_version':(ROOT/'VERSION.txt').read_text(encoding='utf8').strip(),'running_version':running['version'],
              'runtime_source_sha256':running['runtime_source_sha256']}
    if identity_path.exists():
        previous=json.loads(identity_path.read_text(encoding='utf8'))
        for key in ('run_id','base_url','manifest_sha256','runtime_source_sha256'):
            if previous.get(key)!=identity[key]:raise RuntimeError('Refusing to mix a different run or runtime')
        identity=previous
    else:save(identity_path,identity)
    session.close()

    def collect(item):
        path=out/(item['id']+'.json')
        if path.exists():
            record=json.loads(path.read_text(encoding='utf8'))
            if record.get('run_id')!=identity['run_id'] or record.get('manifest_sha256')!=digest:
                raise RuntimeError('Question checkpoint belongs to another run')
            return record
        client=requests.Session();client.trust_env=False
        started=now();clock=time.monotonic();attempts=[]
        try:
            for number in range(1,3):
                attempt={'attempt':number,'started_at':now()}
                try:
                    token_response=client.get(base+'/api/assistant/session',timeout=(15,30))
                    token_response.raise_for_status()
                    token=token_response.json()['token']
                    response=client.post(base+'/api/assistant/ask',json={'question':item['question']},
                        headers={'X-Assistant-Token':token},timeout=(15,300))
                    attempt['http_status']=response.status_code
                    try:reply=response.json()
                    except ValueError:reply={'error':'Non-JSON response','body':response.text[:4000]}
                    attempt['reply']=reply
                    evidence_url=reply.get('data_url')
                    if isinstance(evidence_url,str) and evidence_url.startswith('/api/forecast/evidence/'):
                        evidence_response=client.get(base+evidence_url,timeout=(15,40))
                        attempt['evidence_http_status']=evidence_response.status_code
                        if evidence_response.status_code==200:attempt['business_evidence']=evidence_response.json()
                    attempts.append(attempt)
                    # Application/grounding errors remain measured failures.
                    # Only an explicit temporary provider message is retried.
                    temporary=response.status_code in {502,503,504} and any(phrase in str(reply.get('error','')) for phrase in (
                        'DeepSeek暂时未能生成','DeepSeek暂时无法','模型服务暂时不可用'))
                    if not temporary:break
                except requests.RequestException as exc:
                    attempt.update(transport_error=type(exc).__name__,detail=str(exc)[:350])
                    attempts.append(attempt)
                    if number==2:break
                if number==1:time.sleep(1)
        finally:client.close()
        latest=attempts[-1] if attempts else {}
        record={**item,'run_id':identity['run_id'],'manifest_sha256':digest,'base_url':base,
                'test_transport':'HTTP /api/assistant/ask','independent_question':True,
                'started_at':started,'completed_at':now(),'elapsed_seconds':round(time.monotonic()-clock,2),
                'attempts':attempts,'http_status':latest.get('http_status'),
                'reply':latest.get('reply',{}),'business_evidence':latest.get('business_evidence'),
                'expert_accepted':False}
        save(path,record)
        return record

    records={}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending={executor.submit(collect,item):item for item in items}
        for future in as_completed(pending):
            item=pending[future]
            record=future.result();records[item['id']]=record
            reply=record.get('reply') or {}
            print(json.dumps({'completed':len(records),'id':item['id'],'http':record.get('http_status'),
                'mode':reply.get('mode'),'kind':reply.get('answer_kind'),'seconds':record['elapsed_seconds']},ensure_ascii=False),flush=True)
            save(out/'progress.json',{'at':now(),'completed':len(records),'total':160,
                'http_200':sum(r.get('http_status')==200 for r in records.values())})
    ordered=[records[item['id']] for item in items]
    report={'created_at':now(),'run_identity':identity,'question_count':len(ordered),
            'http_200':sum(r.get('http_status')==200 for r in ordered),'items':ordered,'expert_accepted':False}
    save(out/'answers.json',report)
    end=requests.Session();end.trust_env=False
    try:
        response=end.get(base+'/api/forecast/status',timeout=30);response.raise_for_status()
        save(out/'snapshot_end.json',{'at':now(),'forecast':response.json()})
    finally:end.close()
    print(json.dumps({'completed':len(ordered),'http_200':report['http_200'],'file':str(out/'answers.json')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    import sys
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
