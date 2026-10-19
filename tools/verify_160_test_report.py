"""Check all question identities and all actual answer lines survived Word export."""
import json,re,hashlib
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from pypdf import PdfReader
from docx import Document
APP=Path(__file__).resolve().parents[1]
RUN=APP/'tmp/qa_eval_20261017'
DOC=APP/'exports/海洋灾害160题实测问答与数据缺项_20261017.docx'
report=json.loads((RUN/'evaluated_answers.json').read_text(encoding='utf-8'))
with ZipFile(DOC) as z:
    tree=ET.fromstring(z.read('word/document.xml'))
text=''.join(tree.itertext())
def norm(s):return re.sub(r'\s+','',s.replace('**','').replace('`','').replace('|',''))
corpus=norm(text); missing=[]
for row in report['items']:
    assert norm(row['original_question']) in corpus,row['id']
    assert norm(row['question']) in corpus,row['id']
    for line in row['result']['answer'].splitlines():
        if not line.strip() or re.fullmatch(r'[\s|:\-]+',line):continue
        content=re.sub(r'^(?:#{1,6}|[-*])\s+','',line.strip())
        if norm(content) not in corpus:missing.append({'id':row['id'],'line':line})
doc=Document(DOC);headings=[p.text for p in doc.paragraphs if p.style.name=='Heading 2']
assert len(headings)==160 and len({h[:5] for h in headings})==160
assert sum(report['assessment_counts'].values())==160
assert not missing,missing[:5]
pdf=PdfReader(next((RUN/'render').glob('*.pdf')))
assert len(pdf.pages)==len(list((RUN/'render').glob('page-*.png')))==107
qa={'questions':160,'unique_question_headings':160,'all_original_questions_present':True,
    'all_concrete_questions_present':True,'all_actual_answer_lines_present':True,
    'counts':report['assessment_counts'],'pages':107,'visual_review_pages':list(range(1,108)),
    'render_method':'packaged render_docx.py with existing native Word PDF adapter; 110 DPI PNG; original size side-by-side page review',
    'docx_sha256':hashlib.sha256(DOC.read_bytes()).hexdigest(),'expert_correctness_accepted':False}
(RUN/'quality_check.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in qa.items() if k!='visual_review_pages'},ensure_ascii=False))
