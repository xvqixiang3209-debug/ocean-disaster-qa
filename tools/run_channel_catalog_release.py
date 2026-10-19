"""Real HTTP probes for alert channels, standards and assimilation explanations."""
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import run_autonomous_holdout as runner

CASES=[
 ('alert_channel','作为宁波市鄞州区居民，我在哪里能第一时间收到官方预警？'),
 ('standards_catalog','截至2026年10月17日，海洋观测预报与防灾减灾领域现行标准有哪些？'),
 ('assimilation_methods','有哪些数据同化方案？对增水预报改进效果如何？'),
]


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 runner.CASES=CASES;runner.main()
 rows=json.loads((a.output/'answers.json').read_text(encoding='utf8'))['items'];by={r['id']:r for r in rows}
 checks={'http_ok':all(r['http_status']==200 for r in rows),
         'channel_has_source':bool(by['alert_channel']['reply'].get('sources')),
         'catalog_has_source':bool(by['standards_catalog']['reply'].get('sources')),
         'assimilation_has_source':bool(by['assimilation_methods']['reply'].get('sources')),
         'assimilation_uses_method_evidence':any((s.get('metadata') or {}).get('primary_scope')=='research_method' for s in by['assimilation_methods']['reply'].get('sources',[])),
         'assimilation_names_method':bool(re.search(r'变分|卡尔曼|Kalman|隐式',by['assimilation_methods']['reply'].get('answer',''),re.I)),
         'catalog_lists_identified_standard':bool(re.search(r'GB/T|HY/T|GB\s*/\s*T|HY\s*/\s*T',by['standards_catalog']['reply'].get('answer','')))}
 (a.output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(checks,ensure_ascii=False),flush=True)
 if not all(checks.values()):raise SystemExit('channel_catalog_release_probe_failed')


if __name__=='__main__':main()
