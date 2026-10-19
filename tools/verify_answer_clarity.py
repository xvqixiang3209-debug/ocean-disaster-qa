"""Opt-in live regression: uses the deployment's authorized model and sources.

Writes answers and diagnostic metadata for manual review, not a factual score.
Run from the app directory: python tools/verify_answer_clarity.py
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import json
from pathlib import Path
import sys
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

QUESTIONS = [
    '什么时候可以降级或终止响应？要走什么程序？',
    '浙江海洋灾害应急响应什么时候可以终止？由谁决定？',
    '上海海洋灾害应急响应什么时候可以终止？由谁决定？',
    '自然资源部现行海洋灾害应急预案中，终止响应要走什么程序？',
    '什么条件下要启动“停课停工停产停运停业”？由谁决定、怎么公告？',
    '台风、风暴潮、海浪、海啸到底有什么区别？我该关心哪一个？',
    '什么是“风暴增水”？它和天文大潮碰在一起会怎么样？',
    '台风只在夏天有吗？几月到几月要特别防？',
    '有效波高是什么意思？它与最大波高有什么区别？',
    '什么是天文潮？为什么会有涨潮和落潮？',
    '风浪和涌浪有什么区别？',
    '海啸与风暴潮有什么区别？',
    '海冰是怎么形成的？会造成哪些影响？',
    '赤潮是什么？为什么有的赤潮有毒？',
    '什么是警戒潮位？它和潮位基准有什么关系？',
    '2019年利奇马最大增水是多少，出现在什么站？',
    '2005年麦莎台风在浙江有哪些应对措施？',
    '2013年菲特台风造成了哪些损失？',
    '用模式表计算哪些站超警，分别超什么级别？',
    '现在舟山海浪危险吗，可以出海吗？',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ids', type=int, nargs='+', help='Rerun only the affected case IDs (1-20).')
    options = parser.parse_args()
    selected = set(options.ids or range(1, len(QUESTIONS) + 1))
    if not selected <= set(range(1, len(QUESTIONS) + 1)):
        parser.error('ids must be between 1 and 20')
    from server import app
    qa = app.extensions['marine_kb']['qa']
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    directory = APP / 'tmp' / ('answer_clarity_' + stamp)
    directory.mkdir(parents=True, exist_ok=True)

    def check(index, question):
        started = time.monotonic()
        try:
            result = qa.answer(question, allow_web=True)
        except Exception as exc:
            result = {'answer': '', 'answer_meta': {'used_mode':'exception','error_type':type(exc).__name__}}
        result.update(question=question, elapsed_seconds=round(time.monotonic() - started, 2))
        (directory / f'{index:02d}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return index, result

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(check, index, question) for index, question in enumerate(QUESTIONS, 1) if index in selected]
        for future in as_completed(futures):
            index, result = future.result()
            results.append((index, result))
            meta = result['answer_meta']
            print(json.dumps({'id':index,'mode':meta.get('used_mode'),'coverage':meta.get('answerability'),
                              'route':meta.get('route'),'seconds':result['elapsed_seconds'],
                              'policy':meta.get('answer_policy'),'web_searched':meta.get('web_search_executed'),
                              'web_selected':meta.get('web_selected_count'),'design':meta.get('answer_design')}, ensure_ascii=False), flush=True)
    results.sort()
    report = ['# 回答清晰度实测记录', '', '真实调用已配置的DeepSeek及联网服务。以下是运行记录与人工复核材料，不代表事实正确率或业务验收通过率。', '']
    for index, result in results:
        meta = result['answer_meta']
        report += [f"## {index:02d} {result['question']}", '', result['answer'] or '未生成回答。', '',
                   f"运行状态：{meta.get('used_mode')}；覆盖：{meta.get('answerability','未标记')}；用时：{result['elapsed_seconds']}秒。",
                   f"检索策略：{meta.get('answer_policy','未适用')}；实际网络搜索：{meta.get('web_search_executed','未适用')}；入选网页：{meta.get('web_selected_count','未适用')}；结构：{meta.get('answer_design','未适用')}。", '',
                   '来源：' + '；'.join(row.get('title','') for row in result.get('rag_sources',[]) + result.get('web_sources',[])), '']
    (directory / 'review.md').write_text('\n'.join(report), encoding='utf-8')
    print('REPORT=' + str(directory / 'review.md'), flush=True)


if __name__ == '__main__':
    main()
