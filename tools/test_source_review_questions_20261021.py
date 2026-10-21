"""Run a bounded, opt-in real user-facing question check; retain actual replies."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import time

import requests

APP = Path(__file__).resolve().parents[1]
CASES = [
    ('station', '根据《海洋观测站点管理办法》（2017年公布版本），迁建的国家基本海洋观测站点要与旧站对比观测多久？旧站因严重自然灾害无法正常工作时怎么办？请给出出处。'),
    ('data', '根据《海洋观测资料管理办法》（2017年公布版本），从事海洋观测的其他单位何时汇交上季度资料？申请使用资料后多久答复、多久交付？请分别回答并引用条款。'),
    ('wave', '按GB/T 19721.2-2017这份标准，海浪警报时效是多少？这是否表示每24小时更新一次？请区分时效和更新频率。'),
    ('quality', 'GB/T 14914.6-2021的全称和适用内容是什么？请给出文档版本和原文出处。'),
]


def run_case(base, case):
    name, question = case
    started = time.monotonic()
    with requests.Session() as session:
        initial = session.get(base + '/api/assistant/session', timeout=20)
        initial.raise_for_status()
        response = session.post(base + '/api/assistant/ask', json={'question': question},
                                headers={'X-Assistant-Token': initial.json()['token']}, timeout=240)
        response.raise_for_status()
        reply = response.json()
    return {'id': name, 'question': question, 'actual_reply': reply,
            'seconds': round(time.monotonic() - started, 3), 'http_status': response.status_code}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run', action='store_true')
    cli.add_argument('--label', default='before')
    cli.add_argument('--base', default='http://127.0.0.1:8774')
    cli.add_argument('--ids', nargs='*', choices=[row[0] for row in CASES])
    args = cli.parse_args()
    if not args.run:
        print(json.dumps(CASES, ensure_ascii=False)); return
    folder = APP / 'tmp/source_review_20261021/questions' / args.label
    folder.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cases = [case for case in CASES if not args.ids or case[0] in args.ids]
        futures = [(case, pool.submit(run_case, args.base, case)) for case in cases]
        rows = []
        for case, future in futures:
            try:
                row = future.result()
            except Exception as exc:
                row = {'id': case[0], 'question': case[1], 'error': type(exc).__name__ + ': ' + str(exc)}
            (folder / (case[0] + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            rows.append(row)
            print(json.dumps({'id': row['id'], 'seconds': row.get('seconds'), 'error': row.get('error'),
                              'state': row.get('actual_reply', {}).get('business_state')}, ensure_ascii=False), flush=True)
    (folder / 'summary.json').write_text(json.dumps({'tested_at': datetime.now().astimezone().isoformat(),
                                                   'base': args.base, 'rows': rows}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
