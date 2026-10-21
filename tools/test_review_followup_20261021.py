"""Opt-in, bounded real QA for reviewed source families; keep original replies."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from tools.test_source_review_questions_20261021 import run_case

APP = Path(__file__).resolve().parents[1]
CASES = {
    'ice': '按GB/T19721.3-2017，海冰警报时效至少多长？浮冰范围和冰厚各用什么单位，时间采用什么时制？',
    'temperature': '按GB/T19721.5-2022，常规海温预报每日最低发布频次和时效分别是多少？定点数值采用预报时效内平均值还是瞬时值？',
    'current': '按GB/T19721.6-2023，流向表示海水流去还是流来的方向？应急海流预报每日最低频次和预报时效分别是多少？',
    'directions': '按GB/T19721.6-2023表A.1，西西北和北西北的中心角度、角度范围分别是多少？请保留区间开闭端点。',
    'wave': '按GB/T19721.2-2017，近岸海域海浪红色警报的波高条件是什么？请区分近岸海域条件与沿岸有效波高条件。',
    'general': '按GB/T14914.1-2018，海洋观测的基本任务是什么？观测资料质量控制有什么基本要求？请分别给出处。',
    'province': '根据浙防指〔2024〕4号印发的浙江省防汛防台抗旱应急预案，“三张单”分别是什么？原2020年应急预案是否继续有效？',
    'numeric': '分别查询Surge_detem_TY0216_2026041310.xlsx和Waterlevel_detem_TY0216_2026041310.xlsx的大戢山05/15:00原值。能仅凭文件名认定它们是2026年4月5日的厘米潮位并直接相减吗？',
}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run', action='store_true')
    cli.add_argument('--label', default='after')
    cli.add_argument('--ids', nargs='+', choices=list(CASES), default=list(CASES))
    cli.add_argument('--base', default='http://127.0.0.1:8774')
    args = cli.parse_args()
    if not args.run:
        print(json.dumps(CASES, ensure_ascii=False)); return
    out = APP / 'tmp/source_review_followup_20261021/questions' / args.label
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [(key, pool.submit(run_case, args.base, (key, CASES[key]))) for key in args.ids]
        for key, future in futures:
            try:
                row = future.result()
            except Exception as exc:
                row = {'id': key, 'question': CASES[key], 'error': type(exc).__name__ + ': ' + str(exc)}
            (out / (key + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            rows.append(row)
            print(json.dumps({'id': key, 'http_status': row.get('http_status'),
                              'answerability': row.get('actual_reply', {}).get('answerability'),
                              'error': row.get('error')}, ensure_ascii=False), flush=True)
    (out / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
