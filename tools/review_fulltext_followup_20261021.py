"""Read-only fulltext audit evidence preparation. No catalogue or model calls."""
from pathlib import Path
import json,hashlib,sys,re,unicodedata
import pypdfium2 as pdfium
from pypdf import PdfReader
from PIL import Image,ImageDraw,ImageFont
APP=Path(__file__).resolve().parents[1]
OUT=APP/'tmp/source_review_followup_20261021/fulltext'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def norm(t):return re.sub(r'\s+','',unicodedata.normalize('NFKC',t))
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n','utf-8')
def render(d,p,out,width=1200):
 page=d[p-1];w,h=page.get_size();bm=page.render(scale=width/w,may_draw_forms=False,draw_annots=False);im=bm.to_pil().convert('RGB');im.save(out);page.close();bm.close();return im

def main():
 seeds=json.loads((OUT/'inventory_seed.json').read_text('utf-8')); docs=[];body=[]
 font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',22)
 for seed in seeds:
  s=seed['source'];old=seed['recovery'];sid=s['source_id'];p=Path(s['raw_path']);dest=OUT/sid;dest.mkdir(exist_ok=True)
  actual=sha(p)
  if actual!=s['sha256']:raise ValueError('source hash changed '+sid)
  rd=PdfReader(str(p));fd=pdfium.PdfDocument(str(p));records=[];units={u.get('metadata',{}).get('page'):u for u in old.get('parsed',{}).get('units',[]) if u.get('metadata',{}).get('page')}
  for n,pg in enumerate(rd.pages,1):
   tx=pg.extract_text() or '';fp=fd[n-1];tp=fp.get_textpage();ft=tp.get_text_bounded();tp.close();fp.close()
   native=norm(tx);full=units.get(n,{}).get('text','')
   if not full:full=(APP/'tmp/source_review_20261021/ocr'/sid/f'page_{n:04d}.ocr.txt').read_text('utf-8') if (APP/'tmp/source_review_20261021/ocr'/sid/f'page_{n:04d}.ocr.txt').exists() else ''
   prior=old['page_records'][n-1]
   risks=[]
   if prior.get('recovery_status')=='quarantined':risks.append('prior_quarantine_blank_sparse_lowconf_or_missing; image review required')
   if prior.get('recovery_status')=='ocr_unreviewed':risks.append('OCR transcript not visually verified')
   combined=norm(full or tx)
   if re.search(r'表[0-9A-ZＡ-Ｚ一二三四五六七八九]|表格|记录表|调查表',combined):risks.append('table row/column relations require original-page comparison')
   if re.search(r'公式|由式|按式|式中|计算式|[∑Σ√≈≤≥≠]|[a-zA-Z]\s*=',full+tx):risks.append('formula signs/subscripts/variables require original-page comparison')
   if re.search(r'[犃-狿-]',tx):risks.append('native font map yields invalid Latin/math glyphs')
   for suffix,t in [('pypdf.txt',tx),('pdfium.txt',ft),('transcript.txt',full)]:
    (dest/f'page_{n:04d}.{suffix}').write_text(t,'utf-8')
   records.append({'page':n,'source_sha256':actual,'prior_recovery_status':prior.get('recovery_status'),'native_pypdf_chars':len(tx),'native_pdfium_chars':len(ft),'normalized_native_agreement':norm(tx)==norm(ft),'pypdf_path':str((dest/f'page_{n:04d}.pypdf.txt').resolve()),'pdfium_path':str((dest/f'page_{n:04d}.pdfium.txt').resolve()),'transcript_path':str((dest/f'page_{n:04d}.transcript.txt').resolve()),'transcript_sha256':sha(dest/f'page_{n:04d}.transcript.txt'),'risks':risks,'visual_review':False,'fulltext_approved':False})
  # First actual scope/body page, excluding foreword/contents.
  candidates=[r['page'] for r in records if '范围' in norm((dest/f"page_{r['page']:04d}.transcript.txt").read_text('utf-8')) and ('适用于' in norm((dest/f"page_{r['page']:04d}.transcript.txt").read_text('utf-8')) or '规定了' in norm((dest/f"page_{r['page']:04d}.transcript.txt").read_text('utf-8')))]
  bp=candidates[0] if candidates else min(4,len(rd.pages))
  im=render(fd,bp,dest/f'page_{bp:04d}.body.png');im.close();body.append({'source_id':sid,'page':bp,'path':str((dest/f'page_{bp:04d}.body.png').resolve()),'sha256':sha(dest/f'page_{bp:04d}.body.png')})
  d={'source_id':sid,'filename':s['filename'],'source_path':str(p),'source_sha256':actual,'registered_source_sha256_verified':True,'physical_page_count':len(rd.pages),'prior_physical_page_count':old.get('physical_page_count'),'page_count_matches_previous':len(rd.pages)==old.get('physical_page_count'),'all_physical_pages_extracted':True,'pages':records,'body_page':bp,'official_identity_audit':json.loads(s['metadata']).get('version_audit_20261021'),'database_mode':'read_only','approval_action':False}
  dump(dest/'audit_inventory.json',d);docs.append(d);fd.close()
 # readable 4-up actual source pages, 7 sheets max; no approval implied.
 for i in range(0,len(body),4):
  group=body[i:i+4];sheet=Image.new('RGB',(2400,3500),'#e5e7eb');dr=ImageDraw.Draw(sheet)
  for j,x in enumerate(group):
   im=Image.open(x['path']);im.thumbnail((1180,1650));xx=(j%2)*1200+(1200-im.width)//2;yy=(j//2)*1750+65;sheet.paste(im,(xx,yy));dr.text(((j%2)*1200+10,(j//2)*1750+8),f"{x['source_id']}  physical page {x['page']}",font=font,fill='black')
  sheet.save(OUT/f'body_contact_{i//4+1:02d}.png');sheet.close()
 dump(OUT/'all_page_inventory.json',{'documents':docs,'body_images':body,'source_count':len(docs),'physical_pages':sum(x['physical_page_count'] for x in docs),'external_model_calls':0,'production_database_mutations':0,'note':'Extraction and rendered evidence are not automatic approval.'})
 print(json.dumps({'sources':len(docs),'pages':sum(x['physical_page_count'] for x in docs),'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__':main()
