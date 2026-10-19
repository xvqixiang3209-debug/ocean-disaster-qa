"""Capture public original bodies for the added discovery routes, never answers."""
from pathlib import Path
import hashlib,json,sys
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from typhoon.web_reader import WebPageReader
from marine_kb.public_sources import primary_routes

questions=['什么是海水倒灌？','海平面上升为什么加重风暴潮？','警报里的近岸海域和近海海域指哪里？','浙江海洋预警有哪些官方查询渠道？',
           '宁波市鄞州区停水停电怎么报修？','2025年我国海洋灾害损失和主要灾种是什么？']
urls={r['url']:r for q in questions for r in primary_routes(q) if not r['url'].startswith('https://arxiv.org')}
reader=WebPageReader(max_pages=10,max_chars=100000,allow_official_documents=True)
pages=reader.enrich(list(urls.values())[:10])
out={'captured_at':datetime.now().astimezone().isoformat(),'kind':'public_source_bodies','content_scope':'extracted_article_body',
     'items':[dict(p,content_sha256=hashlib.sha256(str(p.get('content','')).encode('utf-8')).hexdigest()) for p in pages]}
(ROOT/'tmp/crag182_official_sources.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'url':r.get('url'),'state':r.get('read_status'),'characters':len(r.get('content',''))} for r in pages],ensure_ascii=False))
