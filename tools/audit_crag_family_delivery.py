import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from forecast_runtime.answer_facts import delivery_facts
from marine_kb.business_validation import validate_business_delivery
out=[]
for folder in ['tmp/crag182_family_final','tmp/crag182_unseen_family_variants']:
 for p in (ROOT/folder).glob('*.json'):
  r=json.loads(p.read_text('utf-8'));facts=delivery_facts(r.get('business_evidence') or {});state='not_applicable'
  if facts:
   try:
    validate_business_delivery(r['reply']['answer'],{'required_business_facts':facts},set(s.get('citation_id') for s in r['reply'].get('sources',[])))
    state='pass'
   except Exception as exc:state=str(exc)
  out.append({'id':r['id'],'record':str(p.relative_to(ROOT)),'http':r['http_status'],'typed_facts':len(facts),'delivery':state,'validation':r['reply'].get('validation')})
(ROOT/'tmp/crag182_family_delivery_audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{k:v for k,v in r.items() if k!='validation'} for r in out],ensure_ascii=False))
