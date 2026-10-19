"""Confirm the exported document preserves the exact measured answers."""
from html.parser import HTMLParser
from pathlib import Path
import hashlib,json

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'tmp/gap137_upgrade_20261018'

class ActualAnswers(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.active=False;self.current=[];self.answers=[];self.records=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='pre' and attrs.get('class')=='actual':self.active=True;self.current=[]
        if tag=='details' and attrs.get('class')=='record':self.records+=1
    def handle_data(self,text):
        if self.active:self.current.append(text)
    def handle_endtag(self,tag):
        if tag=='pre' and self.active:
            self.answers.append(''.join(self.current));self.active=False

def load(path):return json.loads(path.read_text(encoding='utf8'))
def answer(row):return row['reply'].get('answer') or row['reply'].get('error') or '未返回回答正文'

def main():
    path=ROOT/'exports/137题原因分类与证据补齐改造报告_20261018.html'
    before={r['id']:r for r in load(ROOT/'tmp/qa_full160_phase163_20261018_final/answers.json')['items']}
    after=load(ROOT/'tmp/qa_full160_phase164_20261018_final/answers.json')['items']
    variants=load(WORK/'variants_release_scoped/answers.json')['items']
    expected=[answer(r) for r in variants]
    for row in after:expected.extend([answer(row),answer(before[row['id']])])
    parser=ActualAnswers();parser.feed(path.read_text(encoding='utf8'))
    assert parser.answers==expected,'Exported answer text differs from measured output'
    assert parser.records==160
    data=load(WORK/'evaluation.json')
    assert len(data['causes'])==137 and sum(data['cause_counts'].values())==137
    assert all(data['runtime_integrity'].values())
    assert data['source_catalog_after']['all_original_source_rows_unchanged']
    result={'exported_actual_answer_blocks':len(expected),'questions':parser.records,
        'classified_baseline_questions':137,'verbatim_answer_integrity':'pass',
        'runtime_fingerprint_files':len(data['runtime_integrity']),
        'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (WORK/'report_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
