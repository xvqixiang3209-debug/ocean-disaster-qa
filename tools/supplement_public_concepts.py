"""Archive verified public concept excerpts after the immutable 160 benchmark.

This adds reusable concept evidence, never stored answers or current business
rules. The benchmark is preserved; subsequent targeted checks are separate.
"""
import argparse,hashlib,io,json,re,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from typhoon.web_reader import WebPageReader
from marine_kb.storage import KnowledgeStore
from marine_kb.ingestion import IngestionService,split_units

RUN=ROOT/'tmp/qa_full160_phase162_20261018_final'
SOURCES=[
 {'key':'cyclone_seasons','filename':'台风季节与秋台风 常年台风活跃月份 官方科普.txt',
  'url':'https://e.weather.com.cn/news/2026/08/4770458.shtml',
  'publisher':'中国天气网 中国气象局公共气象服务门户网站',
  'patterns':[r'一年四季.{0,250}']},
 {'key':'return_period','filename':'百年一遇与重现期 洪水年发生概率 官方科普.txt',
  'url':'https://www.jiangmen.gov.cn/gzhd/zhzw/slj/content/post_3523250.html',
  'publisher':'江门市水利局',
  'patterns':[r'答[：:].{0,600}']},
]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--import-after-benchmark',action='store_true');args=parser.parse_args()
    out=RUN/'public_concept_supplement';out.mkdir(exist_ok=True)
    prepared=out/'prepared.json'
    if not prepared.exists():
        reader=WebPageReader(max_pages=3,timeout_seconds=12,max_chars=32000)
        rows=reader.enrich([{'url':s['url'],'title':s['filename']} for s in SOURCES])
        documents=[]
        for source,page in zip(SOURCES,rows):
            assert page['read_status'] in {'success','partial'},page.get('read_error')
            body=page.get('content','');paragraphs=[]
            for part in body.split('\n\n'):
                if any(re.search(pattern,part) for pattern in source['patterns']):
                    paragraphs.append(part)
            assert paragraphs,source['key']
            # Save bounded, labelled verbatim passages and full acquisition
            # trace internally. No rewriting or candidate answers are indexed.
            excerpt='\n\n'.join(dict.fromkeys(paragraphs))
            text='官方科普原文节选\n来源标题：'+str(page.get('page_title') or source['filename'])+'\n发布机构：'+source['publisher']+'\n原始网页：'+source['url']+'\n抓取时间：'+str(page.get('read_at'))+'\n用途：概念与常年规律解释，不作为当前预警、地方行动命令或工程标准。\n\n'+excerpt
            (out/(source['key']+'.txt')).write_text(text,encoding='utf8')
            documents.append({**source,'text':text,'published_at':page.get('published_at'),
                'read_at':page.get('read_at'),'page_content_sha256':page.get('content_sha256'),
                'selected_excerpt_sha256':hashlib.sha256(excerpt.encode()).hexdigest()})
        prepared.write_text(json.dumps(documents,ensure_ascii=False,indent=2),encoding='utf8')
    docs=json.loads(prepared.read_text(encoding='utf8'))
    if not args.import_after_benchmark:
        print(json.dumps([{'key':d['key'],'text':d['text']} for d in docs],ensure_ascii=False));return
    benchmark=json.loads((RUN/'answers.json').read_text(encoding='utf8'))
    assert len(benchmark['items'])==160,'Do not modify corpus while benchmark is running'
    if (out/'imported.json').exists():
        print('Concept supplements already imported');return
    store=KnowledgeStore(ROOT/'data/marine_kb/knowledge.sqlite3')
    service=IngestionService(store,ROOT/'data/marine_kb/files')
    results=[]
    for d in docs:
        data=d['text'].encode('utf8');entry=service.stage(io.BytesIO(data),'官方科普补充/'+d['filename'])
        source_id,_=store.register(filename=d['filename'],relative_path=entry['relative_path'],sha256=entry['sha256'],
            size=entry['bytes'],raw_path=entry['path'],category='公开科普')
        meta={'source_type':'official_public_knowledge','external_allowed':True,'source_url':d['url'],
            'publication_date':d['published_at'],'retrieved_at':d['read_at'],'publisher':d['publisher'],
            'review_scope':'knowledge_reference','review_method':'official_body_technical_check',
            'scope_note':'仅用于概念解释和常年规律，不验证实时风险或工程标准。',
            'text_form':'verified_public_summary_not_verbatim' if d.get('text_is_summary') else 'official_excerpt',
            'page_content_sha256':d['page_content_sha256'],'selected_excerpt_sha256':d['selected_excerpt_sha256'],
            'is_draft':False,'edition_status':'not_applicable_to_general_knowledge'}
        chunks=split_units([{'locator':'官方网页相关原文节选','text':d['text']}])
        store.finish(source_id,status='indexed',quality_status='parsed',metadata=meta,warnings=[],chunks=chunks)
        store.review(source_id,'approved','已核对官方公开网页事实及出处；摘要明确标注非原文，仅用于通用科普，未认定地方业务规则现行有效。',external_allowed=True)
        results.append({'source_id':source_id,'filename':d['filename'],'url':d['url'],'chunks':len(chunks),
                        'imported_at':datetime.now().astimezone().isoformat(),'after_benchmark':True})
    (out/'imported.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(results,ensure_ascii=False))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
