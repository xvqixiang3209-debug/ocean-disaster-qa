"""Explicit private P0 baseline recording. Default invocation is a dry run.

Each case is a complete QA transaction, potentially using multiple paid model
calls. This tool never contacts the running production HTTP service. It builds a
separate Flask test client after --run and --record-private are both specified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CASES = ROOT / 'exports' / '公开服务问答检索修复_原因与12题实际对照_20261019.json'


def case_plan(source, scope='24'):
    data = json.loads(Path(source).read_text(encoding='utf-8-sig'))
    base = [{'case_id':str(row['id']), 'question':row['question'], 'phase':'baseline', 'repeat':1}
            for row in data['items']]
    if len(base) != 12 or len({row['case_id'] for row in base}) != 12:
        raise ValueError('p0_requires_twelve_unique_cases')
    if scope == '12':
        return base
    selected = ['G3', 'A2', 'C3', 'M1']
    by_id = {row['case_id']:row for row in base}
    if not all(key in by_id for key in selected):
        raise ValueError('p0_required_cases_missing')
    return base + [{**by_id[key], 'phase':'additional_repeat', 'repeat':repeat,
                    'case_id':f'{key}_extra_{repeat}'} for key in selected for repeat in range(1,4)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--scope', choices=('12','24'), default='24')
    parser.add_argument('--run', action='store_true', help='Execute paid real-model QA; requires human authorization')
    parser.add_argument('--record-private', action='store_true', help='Allow raw prompts/results in local tmp/replay only')
    args = parser.parse_args(argv)
    cases = case_plan(args.cases, args.scope)
    summary = {'mode':'run' if args.run else 'dry_run', 'qa_transactions':len(cases),
               'actual_model_calls':'multiple per transaction; counted by trace',
               'case_ids':[row['case_id'] for row in cases],
               'source_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest()}
    if not args.run:
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0
    if not args.record_private:
        parser.error('--run requires --record-private; recordings contain sensitive source text')
    # Set before importing server; never restart or connect to a running service.
    os.environ['MARINE_FORECAST_WORKER'] = 'false'
    os.environ['MARINE_WARNING_WORKER'] = 'false'
    os.environ['MARINE_TRACE'] = 'true'
    from marine_kb.replay import ModelCassette, RecordingCaller
    from server import app, marine_qa
    if not marine_qa.external_enabled or not marine_qa.model_configured:
        raise RuntimeError('p0_external_model_not_configured_or_enabled')
    if not marine_qa.web_supplement or not marine_qa.web_supplement.enabled:
        raise RuntimeError('p0_official_web_not_enabled')
    destination = ROOT / 'tmp' / 'replay' / ('p0_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True)
    summary.update(started_at=datetime.now(timezone.utc).isoformat(),
                   note='Private model calls, public retrieval snapshots and responses; not an expert acceptance score.')
    (destination/'run_plan.json').write_text(json.dumps({**summary,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'private_output':str(destination),'planned_qa_transactions':len(cases)},ensure_ascii=False),flush=True)
    original_caller = marine_qa.model_caller
    original_fetch = marine_qa.web_supplement.fetch
    original_local = marine_qa._collect_local_hits
    try:
        for index, case in enumerate(cases,1):
            path = destination / (case['case_id'] + '.cassette.jsonl')
            result_path = destination / (case['case_id'] + '.result.json')
            (destination/(case['case_id']+'.started.json')).write_text(json.dumps({
                'case_id':case['case_id'],'started_at':datetime.now(timezone.utc).isoformat(),
                'attempt':1,'automatic_transaction_retry':False},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':case['case_id'],'started':index,'total':len(cases)},ensure_ascii=False),flush=True)
            with ModelCassette(path, mode='record', allow_sensitive_recording=True) as journal:
                marine_qa.model_caller = RecordingCaller(original_caller,journal)
                # Whole retrieval boundaries freeze caches, failure metadata,
                # selected raw text and the local side-effect metadata together.
                # This is not a nested model/reader recorder, avoiding duplicate
                # journal entries that an offline harness could not consume.
                def fetch(question, **options):
                    return journal.invoke('public_retrieval', {'question':question,'options':options},
                                          lambda: original_fetch(question,**options))
                def local(question, normalized, queries, parts, meta, dynamic, sid):
                    def live():
                        hits=original_local(question,normalized,queries,parts,meta,dynamic,sid)
                        return {'hits':hits,'metadata':dict(meta)}
                    value=journal.invoke('local_retrieval', {'question':question,'normalized':normalized,
                        'queries':queries,'parts':parts,'dynamic':dynamic,'sid':sid},live)
                    meta.update(value['metadata'])
                    return value['hits']
                marine_qa.web_supplement.fetch = fetch
                marine_qa._collect_local_hits = local
                with app.test_client() as client:
                    session=client.get('/api/assistant/session',base_url='http://127.0.0.1')
                    token=session.get_json()['token']
                    response=client.post('/api/assistant/ask',json={'question':case['question']},
                        headers={'X-Assistant-Token':token},base_url='http://127.0.0.1')
                    value={'case':case,'http_status':response.status_code,'reply':response.get_json(),
                           'completed_at':datetime.now(timezone.utc).isoformat()}
                    result_path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':case['case_id'],'completed':index,'total':len(cases),
                              'http_status':response.status_code},ensure_ascii=False),flush=True)
    finally:
        marine_qa.model_caller=original_caller
        marine_qa.web_supplement.fetch=original_fetch
        marine_qa._collect_local_hits=original_local
    print(json.dumps({'private_output':str(destination),'qa_transactions':len(cases)},ensure_ascii=False))
    (destination/'run_completed.json').write_text(json.dumps({
        'completed_at':datetime.now(timezone.utc).isoformat(),'qa_transactions':len(cases),
        'automatic_transaction_retries':0},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
