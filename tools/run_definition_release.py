"""Exercise foundational question families through the actual HTTP route.

The checks are coarse release probes, not expert scoring of every statement.
All answers, including failures, are preserved without selecting a best retry.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import run_autonomous_holdout as runner

CASES=[
 ('disaster_compare','台风、风暴潮、海浪、海啸到底有什么区别？作为浙江沿海居民，我该关心哪一个？'),
 ('comparison_table','请用表格说明台风、风暴潮、海浪和海啸的本质与成因，再说明浙江沿海居民分别要关注什么。'),
 ('surge_tide','风暴增水和天文潮位有什么关系？总水位能不能直接用增水数值代替？'),
 ('typhoon_season','台风只在夏季发生吗？浙江沿海通常哪些月份更要防范？'),
]


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();runner.CASES=CASES;runner.main()
 items=json.loads((args.output/'answers.json').read_text(encoding='utf8'))['items']
 by_id={r['id']:r for r in items}
 def answer(key):return by_id[key]['reply'].get('answer','')
 def grounded(row):
  reply=row['reply'];refs={int(n) for n in re.findall(r'\[资料(\d+)\]',reply.get('answer',''))}
  return bool(refs&{s.get('citation_id') for s in reply.get('sources',[])})
 checks={
  'http_and_visible_sources':all(r['http_status']==200 and grounded(r) for r in items),
  'four_concepts_retained':all(all(word in answer(key) for word in ('台风','风暴潮','海浪','海啸')) for key in ('disaster_compare','comparison_table')),
  'not_numeric_only_recovery':all(not r['reply'].get('evidence_diagnostics',{}).get('numeric_supported_partial_recovery') for r in items),
  'tide_surge_terms':all(word in answer('surge_tide') for word in ('天文潮','增水','水位')),
  'season_months_present':bool(re.search(r'[一二三四五六七八九十\d][^。\n]{0,10}月',answer('typhoon_season'))),
 }
 (args.output/'checks.json').write_text(json.dumps({'note':'粗粒度内容保留检查，不代替逐句核验。','checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(checks,ensure_ascii=False),flush=True)
 if not all(checks.values()):raise SystemExit('definition_release_probe_failed')


if __name__=='__main__':main()
