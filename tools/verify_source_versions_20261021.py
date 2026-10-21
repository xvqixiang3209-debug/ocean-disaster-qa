"""Read-only official edition audit; never changes source review decisions."""
from __future__ import annotations
import concurrent.futures
import hashlib
import html
import json
import pathlib
import re
from datetime import datetime, timezone, timedelta
import requests
from lxml import html as lxml_html
from docx import Document
from urllib.parse import urljoin

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'tmp/source_review_20261021/official'
OUT.mkdir(parents=True, exist_ok=True)

def clean(value):
    value = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', value, flags=re.S|re.I)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()

def save_url(url, label, params=None):
    cachelabel=re.sub(r'[^a-zA-Z0-9_.-]+','_',label)
    cached=list(OUT.glob(cachelabel+'_*.html'))
    if len(cached)==1:
        data=cached[0].read_bytes()
        resolved=requests.Request('GET',url,params=params).prepare().url
        return resolved, data.decode('utf-8',errors='replace'), str(cached[0]), hashlib.sha256(data).hexdigest()
    response = requests.get(url, params=params, timeout=40)
    response.raise_for_status()
    digest=hashlib.sha256(response.content).hexdigest()
    path=OUT / (re.sub(r'[^a-zA-Z0-9_.-]+','_',label)+'_'+digest[:12]+'.html')
    path.write_bytes(response.content)
    return response.url, response.content.decode('utf-8',errors='replace'), str(path), digest

def gb_check(code):
    url, body, path, digest = save_url('https://openstd.samr.gov.cn/bzgk/std/std_list',code+'_list',{'p.p2':code,'p.p1':0})
    candidates=[]
    for tr in re.findall(r'<tr\b[^>]*>.*?</tr>',body,re.S):
        cells=[clean(x) for x in re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.S)]
        if len(cells)>=9 and re.sub(r'\s+','',cells[1])==re.sub(r'\s+','',code):
            m=re.search(r"showInfo\('([A-F0-9]+)'\)",tr)
            if m:
                du,db,dp,dh=save_url('https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno='+m[1],code+'_detail')
                candidates.append({'standard_number':code,'title':cells[4], 'official_state':cells[6], 'publication_date':cells[7][:10], 'effective_date':cells[8][:10], 'evidence_url':du,'evidence_path':dp,'evidence_sha256':dh,'list_url':url,'list_path':path,'list_sha256':digest,'identity_matched':True,'evidence_summary':f'精确标准号匹配；官方标准状态为{cells[6]}。'})
    return candidates[0] if len(candidates)==1 else {'standard_number':code,'official_state':'unknown','evidence_url':url,'evidence_path':path,'evidence_sha256':digest,'identity_matched':False,'error':'not exactly one matching standard row'}

def public_source(label,title,url,issued,effective,body,raw_path,version_status,**extra):
    path=OUT/(label+'_正文.txt')
    header=f'{title}\n来源：{url}\n文件日期：{issued}\n实施日期：{effective or "原发布页未另列"}\n版本核查：{version_status}\n\n'
    path.write_text(header+body.strip()+'\n',encoding='utf-8')
    return {'title':title,'body_file':str(path),'url':url,'document_issued_at':issued,'effective_date':effective,'version_verified':True,'version_status':version_status,'raw_file':str(raw_path),'body_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),**extra}

def collect_public_sources():
    public=[]
    # Official consolidated regulation, not the earlier 2012 original in the archive.
    url,body,raw,digest=save_url('https://xzfg.moj.gov.cn/front/law/detail?LawID=1683','regulation_2023')
    tree=lxml_html.fromstring(body)
    chunks=tree.xpath('//div[contains(@class,"law-chapter")]')
    assert len(chunks)==1
    text='\n'.join(t.strip() for t in chunks[0].itertext() if t.strip())
    assert '2023年7月20日' in text and '第三十八条' in text and '备案' in text
    public.append(public_source('海洋观测预报管理条例_2023修订','海洋观测预报管理条例（2023年修订）',url,'2023-07-20',None,text,raw,'official_consolidated_revision',supersedes_original_source_ids=['d7271e295521bbcdaebc77d0'],raw_sha256=digest))
    # Official department rule text. Formal 2017 text verified; no claim that
    # absence of a later result proves current validity.
    url,body,raw,digest=save_url('https://www.moj.gov.cn/pub/sfbgw/flfggz/flfggzbmgz/201707/t20170724_146021.html','data_management_2017')
    tree=lxml_html.fromstring(body)
    chunks=tree.xpath('//div[contains(@class,"news_content_style")]')
    assert len(chunks)==1
    text='\n'.join(t.strip() for t in chunks[0].itertext() if t.strip())
    assert text.startswith('第一条') and '本办法自公布之日起施行' in text
    public.append(public_source('海洋观测资料管理办法_2017正式发布','海洋观测资料管理办法（2017年正式发布文本）',url,'2017-06-07','2017-06-07',text,raw,'official_published_edition_current_status_unconfirmed',matches_original_source_ids=['a7e3e3d4986f6401487eb0b2'],raw_sha256=digest))
    # Beijing regulator's official consolidated text was retrievable through
    # the web reader, while the direct HTTP fetch returned 403. Preserve that
    # distinction instead of calling the reader capture original HTML.
    capture=OUT/'station_2019_tool_capture.txt'
    data=capture.read_text(encoding='utf-8')
    text='\n'.join(m[2] for m in re.finditer(r'^L(\d+):\s?(.*)$',data,re.M) if 19<=int(m[1])<=133)
    text=re.sub(r'cite[^†]+†([^]+)',r'\1',text)
    assert '第二十四条' in text and '三十日内提交备案报告' in text
    public.append(public_source('海洋观测站点管理办法_2019修正','海洋观测站点管理办法（2019年修正）','https://ghzrzyw.beijing.gov.cn/zhengwuxinxi/zcfg/gz/201912/t20191213_1167136.html','2019-07-24','2019-07-24',text,capture,'official_consolidated_revision_valid_on_page',supersedes_original_source_ids=['18b1dc484ab4409f836e8ebb'],raw_format='web_reader_capture_not_html',raw_sha256=hashlib.sha256(capture.read_bytes()).hexdigest()))
    # Current marine emergency plan: the official repost includes the notice
    # and a native Word attachment that avoids OCR loss in the scanned archive.
    url,body,raw,digest=save_url('http://hunan.gov.cn/zqt/zcsd/202606/t20260629_34014765.html','marine_emergency_2026_notice')
    tree=lxml_html.fromstring(body)
    links=[a for a in tree.xpath('//a[@href]') if '海洋灾害应急预案' in a.text_content() and '.docx' in a.get('href','')]
    assert len(links)==1
    attachment_url=urljoin(url,links[0].get('href'))
    ap=OUT/'海洋灾害应急预案_自然资办函2026_1023号_官方附件.docx'
    if not ap.exists():
        r=requests.get(attachment_url,timeout=40);r.raise_for_status();ap.write_bytes(r.content)
    d=Document(ap)
    lines=[]
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    for el in d.element.body:
        if el.tag.endswith('}p'):
            value=Paragraph(el,d).text
            if value.strip():lines.append(value)
        elif el.tag.endswith('}tbl'):
            table=Table(el,d)
            lines.append('\n'.join(' | '.join(c.text.replace('\n','；') for c in row.cells) for row in table.rows))
    notice='自然资源部办公厅关于印发海洋灾害应急预案的通知\n自然资办函〔2026〕1023号\n2026年6月15日\n《自然资源部办公厅关于印发〈海洋灾害应急预案〉的通知》（自然资办函〔2022〕1825号）同时废止。\n\n'
    text=notice+'\n'.join(lines)
    assert '4.7 响应终止' in text and '2026' in text
    public.append(public_source('海洋灾害应急预案_2026正式发布','海洋灾害应急预案（自然资办函〔2026〕1023号）',url,'2026-06-15',None,text,raw,'official_published_current_replaces_2022',matches_original_source_ids=['1241a1c45da43f0f8631be87'],attachment_file=str(ap),attachment_url=attachment_url,attachment_sha256=hashlib.sha256(ap.read_bytes()).hexdigest(),native_table_count=len(d.tables),embedded_image_count=len(d.inline_shapes),scope_note='正文与表格按Word块顺序提取；附件含1幅嵌图，其图中内容未转换为正文证据。',raw_sha256=digest))
    from pypdf import PdfReader
    bu='https://zjjcmspublic.oss-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn/jcms_files/jcms1/web1568/site/attach/0/ea16b96dc414453d9ea2a0f518c98ab1.pdf'
    bp=OUT/'2020年浙江省海洋灾害公报_官方正式发布版.pdf'
    if not bp.exists():
        rr=requests.get(bu,timeout=45);rr.raise_for_status();bp.write_bytes(rr.content)
    reader=PdfReader(bp)
    lines=[f'【原PDF第{i+1}页】\n'+(page.extract_text() or '') for i,page in enumerate(reader.pages)]
    text='\n\n'.join(lines)
    assert '2020' in text and '现予以发布' in text and len(reader.pages)==32
    public.append(public_source('2020年浙江省海洋灾害公报_正式发布','2020年浙江省海洋灾害公报（正式发布版）',bu,'2021-05',None,text,bp,'official_final_annual_bulletin',supersedes_original_source_ids=['3fa8f1d5f28b9110844c43e1'],date_precision='month',page_count=32,scope_note='官方PDF原文按页提取；图片内数据未OCR，复杂表格仍以原PDF列关系为准。',raw_sha256=hashlib.sha256(bp.read_bytes()).hexdigest()))
    target=ROOT/'tmp/source_review_20261021/public_sources.json'
    target.write_text(json.dumps({'public_sources':public},ensure_ascii=False,indent=2),encoding='utf-8')
    return public

def enrich_version_audit():
    audit_path=ROOT/'tmp/source_review_20261021/version_audit.json'
    sources=json.loads(audit_path.read_text(encoding='utf-8'))
    by_id={s['source_id']:s for s in sources}
    checked=datetime.now(timezone(timedelta(hours=8))).isoformat()
    statuses=[
      ('90f3b1d4388dbda395dbc758','HY/T 059-2002','海洋站自动化观测通用技术要求','2002-09-11','2002-10-01','https://std.samr.gov.cn/hb/search/stdHBDetailed?id=8B1827F18E22BB19E05397BE0A0AB44A',None),
      ('41c26f25dba26ccdfbf39c1f','HY/T 180-2015','基准潮位核定技术指南','2015-07-30','2015-10-01','https://std.samr.gov.cn/hb/search/stdHBDetailed?id=8B1827F21F8FBB19E05397BE0A0AB44A',None),
      ('7637a8a67912f7356e163df6','HY/T 0374-2023','海温预报产品制作规范','2023-11-01','2024-01-01','https://www.ndls.org.cn/standard/detail/5d22dc5b40df04b26aa158c4174e9578','ndls_374_capture.txt'),
      ('62c3401ab7b1f7fdf7faa768','HY/T 0381-2023','海洋动力灾害隐患排查技术规程','2023-11-01','2024-01-01','https://www.ndls.org.cn/standard/detail/c93e7b82d01511fe30d976c297045729','ndls_381_capture.txt'),
      ('7210b836dfa1f8417526bc35','DB33/T 2142-2018','风暴潮灾害重点防御区划定技术导则','2018-09-28','2018-10-28','https://std.samr.gov.cn/db/search/stdDBDetailed?id=998D2858B4C898C7E05397BE0A0A95D8',None),
      ('0ce7127ff49394da462cf9e8','DB33/T 2577-2023','海洋灾害隐患排查技术导则','2023-04-12','2023-05-12','https://std.samr.gov.cn/db/search/stdDBDetailed?id=FFA4D0311D42B7AEE05397BE0A0A5907',None),
      ('78436143232a8511364c638a','HY/T 195-2015','风暴潮漫堤预报技术指南','2016-02-16','2016-06-01','https://www.ndls.org.cn/standard/index?a209=%E6%B5%99%E6%B1%9F%E7%9C%81%E6%B5%B7%E6%B4%8B%E7%9B%91%E6%B5%8B%E9%A2%84%E6%8A%A5%E4%B8%AD%E5%BF%83','ndls_195_282_search_capture.txt'),
      ('11e42585ad5f99327c2eff5a','HY/T 0282-2020','风暴潮灾害重点防御区划定技术导则','2020-05-29','2020-09-01','https://www.ndls.org.cn/standard/index?a209=%E6%B5%99%E6%B1%9F%E7%9C%81%E6%B5%B7%E6%B4%8B%E7%9B%91%E6%B5%8B%E9%A2%84%E6%8A%A5%E4%B8%AD%E5%BF%83','ndls_195_282_search_capture.txt'),
    ]
    for sid,code,title,issued,effective,url,capture in statuses:
        if capture:
            path=OUT/capture;digest=hashlib.sha256(path.read_bytes()).hexdigest();content=path.read_text(encoding='utf-8');fmt='web_reader_capture' if 'search' not in capture else 'official_registry_search_capture'
            assert code in content and '现行' in content
        else:
            _,content,path,digest=save_url(url,code+'_registry');fmt='html'
            assert code in clean(content) and title in clean(content) and '现行' in clean(content)
        evidence={'standard_number':code,'title':title,'official_state':'现行','publication_date':issued,'effective_date':effective,'evidence_url':url,'evidence_path':str(path),'evidence_sha256':digest,'raw_format':fmt,'evidence_summary':'官方目录逐项匹配标准号、名称和现行状态；目录不代替正文条款。'}
        by_id[sid]['standard_number']=code
        by_id[sid]['version_audit']={'checked_at':checked,'version_status':'official_current','evidence':[evidence],'review_decision':'identity_audit_only_no_approval'}
    formal=[
      ('a400048b3600e7504376b621','HY/T 0273-2019','2019-12-20','2020-02-01','https://www.ndls.org.cn/standard/detail/c0f3e2a923e93d2c6086fa38205eb9da','ndls_surge_tool_capture.txt'),
      ('4d3813f875e7fef790f64971','HY/T 0273.2-2023','2023-11-01','2024-01-01','https://www.ndls.org.cn/standard/detail/61255b0f1e51c1c3e777642c6de4c0ca','ndls_wave_tool_capture.txt'),
      ('f46d3871139cd102ed92a08f','HY/T 0273.3-2021','2021-02-09','2021-06-01','https://www.ndls.org.cn/standard/detail/7f336c0ba9510e75025107b522ded370','ndls_tsunami_tool_capture.txt'),
      ('c161333c65ddc4e9f0b41cdb','HY/T 0273.5-2021','2021-02-09','2021-06-01','https://www.ndls.org.cn/standard/detail/c719280f6f968c7b5cbb12d3c23fef13','ndls_sealevel_tool_capture.txt'),
    ]
    for sid,code,issued,effective,url,capture in formal:
        path=OUT/capture;body=path.read_text(encoding='utf-8');assert code in body and '现行' in body
        by_id[sid]['version_audit']={'checked_at':checked,'version_status':'draft_formal_successor_identified_body_missing','formal_standard_number':code,'evidence':[{'standard_number':code,'official_state':'现行','publication_date':issued,'effective_date':effective,'evidence_url':url,'evidence_path':str(path),'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'raw_format':'web_reader_capture','evidence_summary':'已核定正式标准身份；现有本地文件仍是草稿，尚未取得官方可下载正式全文。'}],'replacement_file':None,'needs_confirmation':'取得正式标准全文，逐条比对后再准入正式规则；保留草稿只作历史研究。','review_decision':'do_not_approve_local_draft_as_formal'}
    for s in collect_public_sources():
        for sid in s.get('supersedes_original_source_ids',[]):
            by_id[sid]['version_audit']={'checked_at':checked,'version_status':'superseded_original_keep_historical','evidence':[{'evidence_url':s['url'],'evidence_path':s['raw_file'],'evidence_summary':'官方已发布后续修订或正式版本。','document_issued_at':s['document_issued_at']}],'replacement_file':s['body_file'],'review_decision':'historical_reference_only'}
        for sid in s.get('matches_original_source_ids',[]):
            by_id[sid]['version_audit']={'checked_at':checked,'version_status':s['version_status'],'evidence':[{'evidence_url':s['url'],'evidence_path':s['raw_file'],'evidence_summary':'官方发布身份与当地存档版本相符；正文恢复参照公开正式版。','document_issued_at':s['document_issued_at']}],'replacement_file':s['body_file'],'review_decision':'identity_audit_only_no_approval'}
    for sid in ['2d13ebbeb564dabfd0ff59d8','515c0f7c91a3e2b813b861b1','0d37494bc6a1bac8ba700281','e117c75c1b004c6d7f7f8d26','0fdab3a7316e374674e3b344']:
        by_id[sid]['version_audit']['version_status']='event_specific_archive_not_current_general_rule'
        by_id[sid]['version_audit']['review_decision']='historical_event_reference_only_after_text_check'
        by_id[sid]['version_audit']['needs_confirmation']='未找到逐份对应的公开发布页；档案只能说明该次台风阶段任务，不作为当前通用启动条件。'
    for sid in ['2eda32486bb918bb14e07922','fc003a85d1e79a86edba68f4','c3e458ea7b26fb4154b63fab','e9126f24b5145f84da45bf2c']:
        by_id[sid]['version_audit']['needs_confirmation']='公开检索未找到逐份对应的发文号、正式通知及现行有效性声明；需资料提供方确认版本，不能用县级同名文件替代省级原件。'
    by_id['5d63b002eca050766926d500']['version_audit']['version_status']='annual_bulletin_submission_final_missing'
    by_id['5d63b002eca050766926d500']['version_audit']['needs_confirmation']='2014上报版未找到对应官方正式全文；历史事件数据可先逐项比对，不能将整份上报稿改标正式。'
    audit_path.write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf-8')
    return sources

def main():
    identities=json.loads((ROOT/'tmp/source_review_20261021/pending_text_identity.json').read_text(encoding='utf-8'))
    groups={}
    for source in identities:
        filename=source['filename'].replace('GB∕T','GB/T').replace('GB_T','GB/T').replace('GB-T','GB/T').replace('GBT+','GB/T ')
        match=re.search(r'GB\s*/?\s*T\s*(\d+(?:\.\d+)?)-([0-9]{4})',filename)
        if not match and source['source_id']=='2827bae8683656877d0d3232': code='GB/T 19721.4-2022'
        else: code=f'GB/T {match[1]}-{match[2]}' if match else None
        source['standard_number']=code
        if code:groups.setdefault(code,[]).append(source)
    results={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(gb_check,code):code for code in groups}
        for future in concurrent.futures.as_completed(futures):
            code=futures[future]
            try:results[code]=future.result()
            except Exception as exc:results[code]={'standard_number':code,'official_state':'unknown','error':f'{type(exc).__name__}: {exc}'}
            print(code,results[code].get('official_state'),flush=True)
    for source in identities:
        result=results.get(source['standard_number'],{})
        source['version_audit']={'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'version_status':{'现行':'official_current','废止':'official_withdrawn'}.get(result.get('official_state'),'unverified'),'evidence':[result] if result else [],'review_decision':'identity_audit_only_no_approval'}
    path=ROOT/'tmp/source_review_20261021/version_audit.json'
    path.write_text(json.dumps(identities,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'gb_status_summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    required_captures=['station_2019_tool_capture.txt','ndls_wave_tool_capture.txt','ndls_surge_tool_capture.txt','ndls_tsunami_tool_capture.txt','ndls_sealevel_tool_capture.txt','ndls_374_capture.txt','ndls_381_capture.txt','ndls_195_282_search_capture.txt']
    if all((OUT/name).is_file() for name in required_captures):
        enrich_version_audit()
    print('written',path,flush=True)

if __name__=='__main__':main()
