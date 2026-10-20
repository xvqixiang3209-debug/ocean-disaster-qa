import hashlib
import json
from types import SimpleNamespace

from marine_kb.public_cache import PublicReferenceReader
from marine_kb.web_supplement import OfficialWebSupplement, irrelevant_interactive_path
from typhoon.web_reader import WebPageReader
from typhoon.web_search import SearchOutcome


def test_irrelevant_js_maps_skipped_but_alert_channels_retained():
    for host in ('typhoon.nmc.cn','typhoon.slt.zj.gov.cn'):
        row = {'url':'https://'+host+'/web.html'}
        assert irrelevant_interactive_path('台风后政府生活救助如何申请？',row)
        assert irrelevant_interactive_path('台州池塘和网箱如何加固？',row)
        assert not irrelevant_interactive_path('台风路径可以代替海洋灾害预警吗？',row)
        assert not irrelevant_interactive_path('预警通过哪些官方渠道查询？',row)
    assert not irrelevant_interactive_path('救助如何申请？',{'url':'https://www.mem.gov.cn/typhoon/article.html'})


def test_cdc_fallback_and_trust_are_opt_in_strict_domain_boundaries():
    from marine_kb.public_sources import primary_routes
    from marine_kb.web_supplement import trusted_url
    old = primary_routes('台风后蚊子很多，应该怎样防蚊？')
    new = primary_routes('台风后蚊子很多，应该怎样防蚊？',connector_v2=True)
    assert not any('chinacdc.cn' in row['url'] for row in old)
    assert 'chinacdc.cn' in new[0]['url']
    assert not trusted_url('https://www.chinacdc.cn/x.html')
    assert trusted_url('https://www.chinacdc.cn/x.html',allow_health=True)
    assert not trusted_url('https://www.chinacdc.cn.evil.example/x.html',allow_health=True)
    assert not trusted_url('https://user:password@www.chinacdc.cn/x.html',allow_health=True)
    assert not trusted_url('https://www.chinacdc.cn:8080/x.html',allow_health=True)
    assert not any('chinacdc.cn' in row['url'] for row in primary_routes('台风路径如何查询？',connector_v2=True))


def test_skip_is_before_reader_and_feature_gated():
    class Search:
        def search_with_status(self,*args,**kwargs):
            return SearchOutcome(state='success',results=[{'title':'台风路径','url':'https://typhoon.nmc.cn/web.html'}])
    class Reader:
        def __init__(self): self.rows=[]
        def enrich(self,rows): self.rows += rows; return []
    reader=Reader()
    _,meta=OfficialWebSupplement(Search(),reader,enabled=True,reference_pages={},connector_v2=True).fetch('台风后政府救助如何申请？')
    assert reader.rows == []
    assert meta['web_candidates_skipped'][0]['state']=='skipped_irrelevant'
    old_reader=Reader()
    OfficialWebSupplement(Search(),old_reader,enabled=True,reference_pages={},connector_v2=False).fetch('台风后政府救助如何申请？')
    assert old_reader.rows


def test_snapshot_preserves_acquisition_hash_without_certifying_currentness(tmp_path):
    body = '国家官方正文。核对救助程序和办理材料，不代表今日办理窗口已开放。'
    class Reader:
        def enrich(self,rows):
            return [{**row,'read_status':'success','content':body,'read_at':'2026-10-19T01:00:00+00:00',
                     'published_at':'2024-02-04','edition_current_verified':False} for row in rows]
    cache=PublicReferenceReader(Reader(),tmp_path,connector_v2=True)
    row={'url':'https://www.mem.gov.cn/example.html','_primary_scope':'technical_reference'}
    cache.enrich([row]); page=cache.enrich([row])[0]
    snapshots=list((tmp_path/'snapshots').rglob('*.json'))
    assert len(snapshots)==1
    record=json.loads(snapshots[0].read_text(encoding='utf-8'))
    assert record['retrieved_at']=='2026-10-19T01:00:00+00:00'
    assert record['body_sha256']==hashlib.sha256(body.encode()).hexdigest()
    assert page['published_at']=='2024-02-04'
    assert page['edition_current_verified'] is False
    assert record['page']['content']==body


def test_raw_capture_opt_in_and_access_checked(tmp_path):
    reader=WebPageReader(capture_root=tmp_path)
    downloaded=SimpleNamespace(url='https://www.mem.gov.cn/article.html',body=b'<html>public</html>',status=200,truncated=False)
    result={'read_at':'2026-10-20T00:00:00+08:00','read_status':'blocked','content_sha256':'abc'}
    reader._capture_public_response(downloaded,result)
    assert not list(tmp_path.rglob('*'))
    result['read_status']='success'
    reader._capture_public_response(downloaded,result)
    assert len(list(tmp_path.rglob('*.bin')))==1
    assert result['capture_response_sha256']==hashlib.sha256(downloaded.body).hexdigest()
    outsider=SimpleNamespace(url='https://unknown.example/private',body=b'no',status=200,truncated=False)
    reader._capture_public_response(outsider,result)
    assert len(list(tmp_path.rglob('*.bin')))==1


def test_exhausted_request_budget_prevents_new_search_and_reader(monkeypatch):
    from marine_kb import phase2
    budget = phase2.AnswerBudget(max_seconds=0)
    token = phase2._BUDGET.set(budget)
    class Never:
        def search_with_status(self,*args,**kwargs): raise AssertionError('network forbidden')
        def enrich(self,*args,**kwargs): raise AssertionError('reader forbidden')
    try:
        hits,meta = OfficialWebSupplement(Never(),Never(),enabled=True,connector_v2=True).fetch('冬春救助怎么申请？')
        assert hits == [] and meta['web_supplement_state']=='budget_exhausted'
        page = WebPageReader()._read_page('https://www.mem.gov.cn/article.html')
        assert page['read_error']=='answer_time_budget_exceeded'
    finally:
        phase2._BUDGET.reset(token)
