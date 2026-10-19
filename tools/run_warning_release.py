"""Separate color-meaning questions from quantitative warning-level research."""
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import run_autonomous_holdout as runner

CASES=[
 ('warning_four_colors','警戒潮位的蓝、黄、橙、红四级是怎么定的？到哪一级我才需要行动？'),
 ('warning_color_rephrase','黄色警戒潮位和橙色警戒潮位有什么区别，到了蓝色就要准备吗？'),
 ('warning_technical_control','四色警戒潮位的重现期如何计算，能否把别的城市阈值直接用于舟山？'),
]


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 runner.CASES=CASES;runner.main()
 rows=json.loads((a.output/'answers.json').read_text(encoding='utf8'))['items'];by={r['id']:r for r in rows}
 answer=lambda key:by[key]['reply'].get('answer','')
 checks={
  'http_ok':all(r['http_status']==200 for r in rows),
  'sources_returned':all(r['reply'].get('sources') for r in rows),
  'four_colors_explained':all(color in answer('warning_four_colors') for color in ('蓝','黄','橙','红')) and '戒备' in answer('warning_four_colors'),
  'paraphrase_explains_preparation':all(word in answer('warning_color_rephrase') for word in ('黄色','橙色','蓝色')) and bool(re.search(r'戒备|准备|防范',answer('warning_color_rephrase'))),
  'no_retained_validation_error':all(not (r['reply'].get('validation') or {}).get('error') for r in rows),
 }
 (a.output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(checks,ensure_ascii=False),flush=True)
 if not all(checks.values()):raise SystemExit('warning_release_probe_failed')


if __name__=='__main__':main()
