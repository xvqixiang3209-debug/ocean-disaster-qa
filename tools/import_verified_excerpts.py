"""Admit separately reviewed verbatim excerpts, preserving quarantined parents.

The manifest is curated only after rendering and checking each cited page.
This does not approve a complete OCR document or certify a current edition.
"""
from pathlib import Path
import argparse,hashlib,json,sqlite3,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from marine_kb.storage import KnowledgeStore

def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);a=p.parse_args()
    m=json.loads(a.manifest.read_text(encoding='utf8'))
    store=KnowledgeStore(ROOT/'data/marine_kb/knowledge.sqlite3')
    with store.connect() as db:
        parent=db.execute('select * from sources where source_id=?',(m['parent_source_id'],)).fetchone()
    if parent is None:raise ValueError('Parent source missing')
    raw=Path(parent['raw_path']);digest=hashlib.sha256(raw.read_bytes()).hexdigest()
    if digest!=m['parent_sha256'] or digest!=parent['sha256']:raise ValueError('Parent changed')
    if m.get('review_method')!='rendered_page_verification' or not m.get('reviewed_pages'):raise ValueError('Missing page review')
    for row in m['excerpts']:
        if row['page'] not in m['reviewed_pages'] or len(row['text'])<20:raise ValueError('Unreviewed excerpt')
    text=m['note']+'\n\n'+'\n\n'.join(row['locator']+'\n'+row['text'] for row in m['excerpts'])
    data=text.encode('utf8');sha=hashlib.sha256(data).hexdigest()
    path=ROOT/'data/marine_kb/files/raw'/(sha+'.txt')
    if path.exists() and path.read_bytes()!=data:raise ValueError('Immutable raw collision')
    path.write_bytes(data)
    meta={'source_type':'official_standard_excerpt','external_allowed':True,
          'review_scope':'versioned_reference','standard_id':m['standard_id'],
          'edition_current_verified':False,'edition_status':'versioned_reference',
          'parent_source_id':m['parent_source_id'],'parent_sha256':digest,
          'parent_filename':parent['filename'],'reviewed_pages':m['reviewed_pages'],
          'review_method':m['review_method'],'review_manifest_sha256':hashlib.sha256(a.manifest.read_bytes()).hexdigest(),
          'version_catalog_url':m.get('version_catalog_url'),'complete_text_read':True,
          'scope_note':m['note'],'formula_transcription_included':False}
    key,created=store.register(filename=m['filename'],relative_path='verified_excerpts/'+m['filename'],
        sha256=sha,size=len(data),raw_path=path,category='标准规范',metadata=meta)
    if created:
        store.finish(key,status='pending_review',quality_status='parsed',metadata=meta,
            warnings=['仅限原页已核对的文字节选；公式、未核页面和当前版本适用性未纳入批准。'],
            chunks=[{'text':r['text'],'locator':r['locator'],'metadata':{'parent_pdf_page':r['page']}} for r in m['excerpts']])
        store.review(key,'approved','已逐页核对：'+m['note'],external_allowed=True)
    print(json.dumps({'source_id':key,'created':created,'parent_status_unchanged':store.get_source(m['parent_source_id'])['status'],
        'excerpt_count':len(m['excerpts']),'reviewed_pages':m['reviewed_pages']},ensure_ascii=False))

if __name__=='__main__':main()
