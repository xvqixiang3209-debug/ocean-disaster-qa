"""Audit saved citation selections against their hashes and known local chunks.

This does not score semantic entailment, current document validity or expert
correctness. Public-page/live-tool provenance needs its own source record.
"""
import argparse,hashlib,json,re,sqlite3,unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def compact(text):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text))


def main():
 p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);args=p.parse_args()
 input_path=args.run/'answers.json'
 input_bytes=input_path.read_bytes()
 input_sha256=hashlib.sha256(input_bytes).hexdigest()
 data=json.loads(input_bytes.decode('utf8'))
 rows=[];hash_errors=[];source_errors=[];selection_count=0
 with sqlite3.connect(ROOT/'data/marine_kb/knowledge.sqlite3') as db:
  for item in data['items']:
   selected=0;matched=0
   for source in item['reply'].get('sources',[]):
    source_text=None
    if type(source.get('chunk_id')) is int:
     found=db.execute('select text from chunks where chunk_id=? and source_id=?',(source['chunk_id'],source['source_id'])).fetchone()
     if found:source_text=found[0]
    for passage in source.get('supporting_passages',[]):
     selected+=1;selection_count+=1;quote=passage['quote']
     if hashlib.sha256(quote.encode()).hexdigest()!=passage['sha256']:
      hash_errors.append({'id':item['id'],'citation':source['citation_id']})
     if source_text is not None:
      if compact(quote) not in compact(source_text):source_errors.append({'id':item['id'],'citation':source['citation_id']})
      else:matched+=1
   answer=item['reply']['answer']
   if not isinstance(answer,str):raise TypeError('Actual answer must be a string: '+item['id'])
   rows.append({'id':item['id'],'answer_sha256':hashlib.sha256(answer.encode('utf8')).hexdigest(),
                'selected_passages':selected,'verified_against_local_chunk':matched})
 result={'note':'仅核对实际保存片段的内容哈希及可取得的本地原切片，不等同逐句语义或业务正确率。',
         'input_answers_sha256':input_sha256,'question_count':len(rows),
         'questions_with_saved_selections':sum(r['selected_passages']>0 for r in rows),'selection_count':selection_count,
         'local_chunk_matches':sum(r['verified_against_local_chunk'] for r in rows),
         'hash_mismatches':hash_errors,'local_source_mismatches':source_errors,'items':rows}
 if hashlib.sha256(input_path.read_bytes()).hexdigest()!=input_sha256:
  raise RuntimeError('answers.json changed during selection audit')
 output=args.run/'selected_passage_audit.json'
 temp=output.with_name(output.name+'.tmp')
 temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
 temp.replace(output)
 print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
