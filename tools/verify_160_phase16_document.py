"""Independently verify the serialized Word answer text against raw HTTP data."""
import argparse
from collections import Counter
import hashlib,json,re,zipfile
from pathlib import Path
from lxml import etree

APP=Path(__file__).resolve().parents[1]
RUN=APP/'tmp/qa_full160_phase16_20261017'
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def canon(text):
    lines=[]
    for line in text.replace('\r','').split('\n'):
        if re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*',line):
            continue
        lines.append(re.sub(r'^\s*#{1,6}\s+','',line))
    text=''.join(lines).replace('**','').replace('`','').replace('|','').replace('\\|','')
    return re.sub(r'\s+','',text)

def text(el):
    return ''.join(el.xpath('.//w:t/text()',namespaces=NS))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',type=Path,default=RUN/'reviewed_answers.json')
    ap.add_argument('--docx',type=Path,default=APP/'exports/海洋灾害160题新版实测问答与数据缺项_20261017.docx')
    args=ap.parse_args()
    data=json.loads(args.input.read_text(encoding='utf-8'))
    items={x['id']:x for x in data['items']}
    with zipfile.ZipFile(args.docx) as z:
        xml=etree.fromstring(z.read('word/document.xml'))
    answers={}; seen=[]; qid=None; state=None; sources={}
    for el in xml.find('w:body',namespaces=NS):
        names=el.xpath('.//w:bookmarkStart/@w:name',namespaces=NS)
        if names:
            assert len(names)==1
            qid=names[0][2:].replace('_','-'); seen.append(qid); state=None
        value=text(el)
        if qid and value=='系统实际原答':
            state='answer'; answers[qid]=[]; continue
        if qid and value.startswith('本题实际来源  '):
            state='sources'; sources[qid]=[]; continue
        if qid and value.startswith('追溯记录  '):
            state=None
        if qid and state=='answer':
            answers[qid].append(value)
        if qid and state=='sources':
            sources[qid].append(value)
    errors=[]
    for qid,item in items.items():
        expected=canon(item['reply'].get('answer') or '')
        actual=canon('\n'.join(answers.get(qid,[])))
        if expected!=actual:
            errors.append({'id':qid,'kind':'answer_text_mismatch','expected_chars':len(expected),'actual_chars':len(actual)})
        source_text='\n'.join(sources.get(qid,[]))
        for source in item['reply'].get('sources') or []:
            for key in ('title','locator','url'):
                if source.get(key) and str(source[key]) not in source_text:
                    errors.append({'id':qid,'kind':'source_field_missing','key':key,'value':source[key]})
    assert len(seen)==160 and len(set(seen))==160 and set(seen)==set(items)
    result={'question_count':len(seen),'unique_ids':len(set(seen)),
            'serialized_answer_fidelity_passed':not errors,'source_title_locator_url_passed':not errors,
            'answer_characters':sum(len(x['reply'].get('answer') or '') for x in items.values()),
            'source_count':sum(len(x['reply'].get('sources') or []) for x in items.values()),
            'docx_sha256':hashlib.sha256(args.docx.read_bytes()).hexdigest(),'errors':errors}
    (args.input.parent/'serialized_document_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    assert not errors

if __name__=='__main__': main()
