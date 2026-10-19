"""Separate post-benchmark checks after two public concept sources are added."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import hashlib,json,sys
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'tmp/qa_full160_phase162_20261018_final'


def main():
    original=json.loads((RUN/'answers.json').read_text(encoding='utf8'))['items']
    lookup={r['id']:r for r in original}
    questions=[('Q-003',lookup['Q-003']['question']),('Q-040',lookup['Q-040']['question']),
        ('P-001','福建的秋冬季还可能有台风吗？应该只在夏季关注吗？'),
        ('P-002','浙江沿海居民应该在哪些月份重点关注台风？全年都可能有吗？'),
        ('P-003','百年一遇是不是一百年才会发生一次？和重现期有什么关系？'),
        ('P-004','重现期越长，表示同一量级的洪水每年更常见还是更少见？')]
    out=RUN/'post_supplement';out.mkdir(exist_ok=True)
    def ask(item):
        ident,question=item;path=out/(ident+'.json')
        if path.exists():return json.loads(path.read_text(encoding='utf8'))
        s=requests.Session();s.trust_env=False
        token=s.get('http://127.0.0.1:8769/api/assistant/session',timeout=30).json()['token']
        response=s.post('http://127.0.0.1:8769/api/assistant/ask',json={'question':question},
            headers={'X-Assistant-Token':token},timeout=(15,240))
        value={'id':ident,'question':question,'tested_at':datetime.now().astimezone().isoformat(),
            'http_status':response.status_code,'reply':response.json(),'after_corpus_supplement':True,
            'not_in_160_score':True,'expert_accepted':False}
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
        return value
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(ask,entry) for entry in questions]):
            row=future.result();rows.append(row)
            print(row['id'],row['http_status'],row['reply'].get('mode'),row['reply'].get('answerability'),flush=True)
    rows.sort(key=lambda r:r['id'])
    (out/'results.json').write_text(json.dumps({'items':rows,'note':'知识资料补充后的追加验证，不覆盖正式160题任何响应或盲评结果。'},ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
