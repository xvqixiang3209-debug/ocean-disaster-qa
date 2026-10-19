"""Restore exact original-answer anchors for saved reviews; never alter judgments."""
from pathlib import Path
import json,hashlib,sys
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from review_full160_crag import validate,save
from marine_kb.model_json import model_json
before=ROOT/'tmp/qa_full160_crag180_20261019_before';after=ROOT/'tmp/qa_full160_crag182_20261019_after';dest=after/'paired_review_v3'
fixed=[]
for p in sorted(dest.glob('?-*.json')):
 old=json.loads(p.read_text('utf-8'))
 if old.get('review') or old.get('error_type')!='ValueError' or not old.get('last_response'):continue
 ident=old['id'];a=json.loads((after/(ident+'.json')).read_text('utf-8'));b=json.loads((before/(ident+'.json')).read_text('utf-8'))
 order=['before','after'] if int(hashlib.sha256(ident.encode()).hexdigest(),16)%2 else ['after','before']
 value=model_json(old['last_response']);packets={'before':b['reply']['answer'],'after':a['reply']['answer']}
 try:
  for tag,name in zip(('A','B'),order):validate(value[tag],packets[name])
 except (ValueError,KeyError):continue
 archive=dest/'prior_errors';archive.mkdir(exist_ok=True);save(archive/(ident+'-unanchored.json'),old)
 review={'before':value['A' if order[0]=='before' else 'B'],'after':value['A' if order[0]=='after' else 'B'],
         'winner':order[0 if value['winner']=='A' else 1] if value.get('winner') in {'A','B'} else 'tie',
         'comparison_reason':value.get('comparison_reason'),'blind_order':order,
         'baseline_answer_sha256':hashlib.sha256(packets['before'].encode()).hexdigest()}
 save(p,{'id':ident,'answer_sha256':hashlib.sha256(packets['after'].encode()).hexdigest(),'review':review,
         'reviewed_at':datetime.now().astimezone().isoformat(),'expert_accepted':False,'method':'auxiliary_DeepSeek_original_span_review',
         'review_anchor_recovered':True,'anchor_note':'Only long exact overlaps were anchored to actual original answers. The saved model labels, causes and comparisons are unchanged. Original output archived.'})
 fixed.append(ident)
print(json.dumps({'restored_exact_answer_anchors':fixed},ensure_ascii=False))
