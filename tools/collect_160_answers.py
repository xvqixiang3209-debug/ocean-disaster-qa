"""Collect the deployed DeepSeek system's actual answers for a document export."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APP = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output-dir', type=Path, default=APP / ('tmp/qa_export_' + datetime.now().strftime('%Y%m%d_%H%M%S')))
    parser.add_argument('--local-only', action='store_true', help='Disable web supplement for a diagnostic export')
    args = parser.parse_args()
    directory = args.output_dir
    directory.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((APP/'data/requirements/catalog.json').read_text(encoding='utf-8'))
    base = 'http://127.0.0.1:8767'
    with urlopen(base+'/api/knowledge/status', timeout=15) as response:
        status = json.load(response)
    model = status['external_model']
    if not model.get('enabled') or not model.get('answer_generation_required'):
        raise RuntimeError('Deployed DeepSeek generation is not enabled')
    token = status['csrf_token']
    target = directory/'answers.json'
    started = datetime.now().astimezone().isoformat(timespec='seconds')
    completed = {}
    system_version = (APP/'VERSION.txt').read_text(encoding='utf-8').strip()
    for path in directory.glob('[QRM]-*.json'):
        row = json.loads(path.read_text(encoding='utf-8'))
        if (row.get('model') == model['model'] and row.get('system_version') == system_version
                and row.get('allow_web') is (not args.local_only)
                and row.get('question') == next((item['question'] for item in catalog['items'] if item['id']==row['id']),None)):
            completed[row['id']] = row

    def evaluate(item):
        start = time.monotonic()
        attempts = []
        result = None
        for attempt in range(2):
            request = Request(base+'/api/knowledge/ask', method='POST',
                data=json.dumps({'question':item['question'], 'allow_web':not args.local_only},ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type':'application/json','X-Knowledge-Token':token,'Origin':base})
            try:
                with urlopen(request, timeout=180) as response:
                    result = json.load(response)
                attempts.append({'attempt':attempt+1,'mode':result.get('answer_meta',{}).get('used_mode')})
                if result.get('answer_meta',{}).get('used_mode') != 'model_error':
                    break
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                attempts.append({'attempt':attempt+1,'error_type':type(exc).__name__})
        if result is None:
            result = {'answer':'本次未能连接问答服务取得回答，请稍后重试。','rag_sources':[],
                      'answer_meta':{'used_mode':'model_error','route':'export_request_failed'}}
        row = {**item,'model':model['model'],'system_version':system_version,'allow_web':not args.local_only,
               'collected_at':datetime.now().astimezone().isoformat(timespec='seconds'),
               'elapsed_seconds':round(time.monotonic()-start,2),'export_attempts':attempts,'result':result}
        (directory/(item['id']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return row

    pending = [item for item in catalog['items'] if item['id'] not in completed]
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
        futures = {pool.submit(evaluate,item):item for item in pending}
        for future in as_completed(futures):
            row = future.result()
            completed[row['id']] = row
            if len(completed)%10 == 0 or row['result'].get('answer_meta',{}).get('used_mode')=='model_error':
                counts = dict(Counter(r['result'].get('answer_meta',{}).get('used_mode','unknown') for r in completed.values()))
                print(json.dumps({'completed':len(completed),'total':160,'counts':counts,'latest':row['id']},ensure_ascii=False),flush=True)
    rows = [completed[item['id']] for item in catalog['items']]
    assert len(rows)==160 and len({row['id'] for row in rows})==160
    report = {'title':'海洋灾害160题与系统回答','started_at':started,
              'completed_at':datetime.now().astimezone().isoformat(timespec='seconds'),
              'source_name':catalog['source_name'],'source_sha256':catalog['source_sha256'],
              'system_version':(APP/'VERSION.txt').read_text(encoding='utf-8').strip(),
              'model':model['model'],'question_count':160,'category_count':10,
              'counts':dict(Counter(r['result'].get('answer_meta',{}).get('used_mode','unknown') for r in rows)),
              'answerability_counts':dict(Counter(r['result'].get('answer_meta',{}).get('answerability','not_applicable') for r in rows)),
              'expert_accepted':False,'items':rows}
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(target),'counts':report['counts'],'answerability_counts':report['answerability_counts']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
