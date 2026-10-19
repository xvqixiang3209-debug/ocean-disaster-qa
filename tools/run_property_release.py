"""Actual property-risk and public-help probes; retain every actual reply."""
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import run_autonomous_holdout as runner

CASES=[
 ('property_scope','我住在宁波市鄞州区咸祥镇的一处沿海住宅，一楼居住并有地下车位，2026年10月18日00时至10月21日00时会不会进水？要不要提前挪车？目前没有提供具体门牌、房屋入口和地下车库入口高程及排水工况。'),
 ('property_and_method','2026年10月19日朱家尖沿海住宅会不会进水？应该如何提前挪车？'),
 ('rescue_phone','在海上遇到危险怎么求救？12395是什么电话？'),
 ('plumbing_control','2026年10月19日厨房水管破裂进水，会不会漏到楼下？应该怎么处理？'),
]


def walk(value):
 if not isinstance(value,dict):return []
 return [value]+[child for item in value.get('items',[]) for child in walk(item)]


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 runner.CASES=CASES;runner.main()
 rows=json.loads((a.output/'answers.json').read_text(encoding='utf8'))['items'];by={r['id']:r for r in rows}
 def kinds(key):return {v.get('kind') for v in walk(by[key].get('business_evidence'))}
 checks={
  'http_ok':all(r['http_status']==200 for r in rows),
  'both_property_queries_fetch_coastal':all('coastal' in kinds(key) for key in ('property_scope','property_and_method')),
  'property_keeps_uncomputed_boundary':all(any(v.get('impact_assessment',{}).get('state')=='not_computed' for v in walk(by[key].get('business_evidence'))) for key in ('property_scope','property_and_method')),
  'rescue_has_cited_phone':bool(re.search(r'12395',by['rescue_phone']['reply'].get('answer',''))) and bool(by['rescue_phone']['reply'].get('sources')),
  'no_retained_validation_error':all(not (r['reply'].get('validation') or {}).get('error') for r in rows),
  'plumbing_is_not_coastal':not kinds('plumbing_control')&{'coastal','activity','grids'},
 }
 (a.output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(checks,ensure_ascii=False),flush=True)
 if not all(checks.values()):raise SystemExit('property_release_probe_failed')


if __name__=='__main__':main()
