"""Read-only retrieval diagnostics using the production search/reader classes."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from marine_kb.web_supplement import OfficialWebSupplement
from typhoon.web_reader import WebPageReader
from typhoon.web_search import WebSearchClient

CASES={
 'eye':'台风眼过境时风突然停了，是不是台风已经过去了？',
 'grades':'热带风暴、强热带风暴、台风、强台风、超强台风怎么分？',
 'rescue':'在海上遇到危险怎么求救？12395是什么电话？',
 'supplies':'宁波市鄞州区一个三口之家为台风应急要不要储备水和粮？储备多少合适？',
 'rip':'在海边遇到离岸流或者被卷出去怎么办？',
 'rag':'怎么构建知识库与检索增强（RAG），保证专业结论可溯源？',
}
def collect(key,question):
    service=OfficialWebSupplement(WebSearchClient(enabled=True,provider='auto',timeout_seconds=8,max_results=4),
        WebPageReader(enabled=True,timeout_seconds=8,max_pages=6,max_chars=32000,max_bytes=3000000,
                      allow_official_documents=True),enabled=True)
    start=time.monotonic();hits,meta=service.fetch(question,subquestions=[question],refresh=True)
    r={'id':key,'question':question,'seconds':time.monotonic()-start,'hits':hits,'meta':meta}
    (ROOT/'tmp/gap137_upgrade_20261018'/('probe_'+key+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'id':key,'seconds':round(r['seconds'],2),'titles':[h['title'] for h in hits],
         'reads_failed':meta.get('web_body_read_failures'),'states':meta.get('web_search_states')},ensure_ascii=False),flush=True)
    return r
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(collect,k,q) for k,q in CASES.items()]
        for future in as_completed(futures):future.result()
