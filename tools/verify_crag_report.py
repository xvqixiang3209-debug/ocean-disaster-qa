"""Verify document answer preservation and rasterize its rendered PDF."""
from pathlib import Path
import json,hashlib,re,argparse
from pypdf import PdfReader
from pdf2image import convert_from_path

def main():
 p=argparse.ArgumentParser();p.add_argument('--docx',type=Path,required=True);p.add_argument('--qa',type=Path,required=True);p.add_argument('--reuse-raster',action='store_true');a=p.parse_args()
 data=json.loads(a.docx.with_suffix('.json').read_text('utf-8'))
 preservation=json.loads(a.docx.with_suffix('.preservation.json').read_text('utf-8'))
 assert len(preservation['items'])==160
 for r,proof in zip(data['items'],preservation['items']):
  assert r['id']==proof['id'] and r['after']['answer_sha256']==proof['raw_sha256'] and proof['preservation_check']=='pass'
 pdf=a.qa/'report.pdf';reader=PdfReader(pdf);text=[page.extract_text() or '' for page in reader.pages]
 missing=[r['id'] for r in data['items'] if r['id'] not in '\n'.join(text)]
 assert not missing,missing
 assert all(len(re.sub(r'\s','',t))>15 for t in text),'Blank render page'
 outside=[]
 for index,page in enumerate(reader.pages,1):
  width,height=float(page.mediabox.width),float(page.mediabox.height)
  def visit(value,cm,tm,font,size):
   if value.strip() and (tm[4]<0 or tm[4]>width or tm[5]<0 or tm[5]>height):
    outside.append({'page':index,'x':tm[4],'y':tm[5],'sample':value[:80]})
  page.extract_text(visitor_text=visit)
 assert not outside,'Text outside PDF page: '+str(outside[:5])
 poppler=Path(r'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin')
 if a.reuse_raster:
  old=json.loads((a.qa/'verification.json').read_text('utf-8'));assert old['pdf_sha256']==hashlib.sha256(pdf.read_bytes()).hexdigest()
  images=old['page_pngs'];assert all(Path(p).exists() for p in images)
 else:images=convert_from_path(str(pdf),dpi=110,output_folder=str(a.qa),fmt='png',thread_count=2,paths_only=True,poppler_path=str(poppler))
 assert len(images)==len(reader.pages)
 result={'answer_preservation':'160/160 SHA256 and per-text-unit checks passed','pdf_pages':len(reader.pages),'all_question_ids_in_pdf':True,
         'page_pngs':images,'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'outside_page_text':outside,'manual_visual_review':'pending'}
 (a.qa/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k!='page_pngs'},ensure_ascii=False))
if __name__=='__main__':main()
