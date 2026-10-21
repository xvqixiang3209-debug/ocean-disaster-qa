"""Official publication archive, distinct from model forecasts and alert criteria.

The national service is the public API used by nmefc.cn's warning history page.
Original responses and every changed notice version are retained. A successful
national feed never implies completeness of provincial/municipal publications.
"""
from __future__ import annotations

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from threading import RLock
from urllib.parse import urlencode, urlparse
import hashlib
import json
import os
import re
import sqlite3
from uuid import uuid4

import requests

from .storage import BEIJING, local_now
from .sync import USER_AGENT
from .activity_locations import ACTIVITY_SITES

NMEFC = 'https://www.nmefc.cn/api/data/warnHistoryList'
ZJ_API = 'https://zjocean.org.cn/api/index.php/seawarning/disasterList'
STREAMS = (('wave', '0'), ('wave', '1'), ('storm', '0'), ('storm', '1'), ('ice', ''), ('bore', ''))
HAZARDS = {'wave': '海浪', 'storm': '风暴潮', 'ice': '海冰', 'bore': '海啸'}
PROVINCES = ('浙江', '上海', '江苏', '山东', '辽宁', '河北', '天津', '福建', '广东', '广西', '海南')
CITIES = {'宁波':'浙江', '舟山':'浙江', '温州':'浙江', '台州':'浙江', '嘉兴':'浙江', '杭州':'浙江',
          '秦皇岛':'河北', '唐山':'河北', '沧州':'河北', '大连':'辽宁', '葫芦岛':'辽宁',
          '烟台':'山东', '威海':'山东', '东营':'山东', '潍坊':'山东', '滨州':'山东', '青岛':'山东',
          '福州':'福建', '宁德':'福建', '厦门':'福建', '南通':'江苏', '连云港':'江苏', '盐城':'江苏',
          '惠州':'广东', '汕头':'广东', '深圳':'广东', '海口':'海南', '三亚':'海南'}
SEAS = ('东海南部','东海北部','台湾海峡','巴士海峡','渤海海峡','渤海湾','莱州湾',
        '黄海北部','黄海中部','黄海南部','北部湾','琼州海峡','渤海','黄海','东海','南海')
COLORS = ('红色','橙色','黄色','蓝色','消息','信息')


class PlainHTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0
        self.feed(text or '')
        self.text = re.sub(r'\n[ \t\r\n]*\n', '\n', re.sub(r'[ \t\r]+', ' ', ''.join(self.parts))).strip()

    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','noscript'}: self.skip += 1
        if not self.skip and tag in {'p','div','br','tr','h1','h2','h3'}: self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script','style','noscript'}: self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in {'p','div','tr'}: self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def stamp(value):
    if not value: return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.astimezone(BEIJING) if dt.tzinfo else dt.replace(tzinfo=BEIJING)
    except (ValueError, TypeError): return None


def warning_question(question):
    """Publication facts, rather than hypothetical alerts or publishing criteria."""
    marine = bool(re.search(r'海洋|海浪|风暴潮|海啸|海冰|近岸|近海|海域|沿海', question))
    if not re.search(r'预警|警报|海浪消息|海啸消息|海啸信息', question): return False
    method=bool(re.search(r'官方渠道|查询渠道|(?:怎样|如何|怎么)查询|(?:代替|替代).{0,20}(?:预警|警报)|哪里.{0,6}(?:查|收到|接收)|在哪.{0,6}(?:查|收到|接收)|从哪.{0,6}查|渠道有哪些|怎么写|如何写|模板|必填数据|产品格式',question))
    release=bool(re.search(r'已经发布|已发布|实际发布|发布记录|公告清单|发布了(?:哪些|什么)|(?:截至|当前|现在|今天|20\d{2}年).{0,80}(?:有哪些|哪些).{0,12}(?:预警|警报)',question))
    provision = bool(re.search(r'(?:GB|HY|DB\d+)\s*[/∕_ -]?\s*T?\s*\d+(?:\.\d+)?\s*[-—－]\s*\d{4}|'
                              r'标准|规范|条款|警报时效|预警时效|发布频率|更新频率', question, re.I))
    if provision and not release:
        # A standard's edition year and a clause's “是否/多少小时” do not
        # request alerts actually issued on that date. Keep it in document RAG.
        return False
    if method and not release:return False
    if re.search(r'假设|假如|情景|若发布|如果发布|命中率|漏报率|空报率|TS评分|提前量|技术进展', question): return False
    if '应急响应' in question and not release: return False
    if re.search(r'发布条件|发布标准|分级标准|阈值|含义|是什么意思|怎么划分|如何划分|多久更新|解除条件|如何解除|解除需要', question):
        # A definition sibling must not suppress an explicit release query.
        # Pure criteria/meaning questions still stay in the document route.
        return bool(marine and re.search(r'已经发布|已发布|实际发布|发布记录|公告清单|发布了(?:哪些|什么)|有哪些.{0,12}(?:警报|预警)', question))
    factual = bool(re.search(r'当前|现在|目前|今天|今日|昨天|截至|已发布|发布了|哪些|什么警报|什么预警|有没有|是否|查询|记录|公告|历史|解除|覆盖|有效|等级|级别|20\d{2}|\d{1,2}月\d{1,2}日', question))
    region = any(name in question for name in (*PROVINCES, *CITIES, *SEAS))
    return factual and (marine or region or '这些预警' in question or '这些警报' in question)


def query_spec(question, now=None, coast_places=()):
    now = (now or local_now()).astimezone(BEIJING)
    pattern = r'(?<!\d)(?:(20\d{2})[-年/])?(\d{1,2})[-月/](\d{1,2})日?(?:[T\s]*(上午|下午|晚上|中午|凌晨)?\s*(\d{1,2})(?:[:时点](\d{1,2})?分?))?'
    matches = list(re.finditer(pattern, question))
    def date_of(m):
        y, month, day, part, h, minute = m.groups()
        hour=int(h or 0)
        if part in {'下午','晚上'} and 1<=hour<12:hour+=12
        if part=='中午' and 1<=hour<11:hour+=12
        if part in {'上午','凌晨'} and hour==12:hour=0
        return datetime(int(y or now.year), int(month), int(day), hour, int(minute or 0), tzinfo=BEIJING)
    if matches:
        first = date_of(matches[0]); last = date_of(matches[-1])
        if len(matches)==1:
            short_end=re.match(r'\s*(?:至|到|—|～|-)\s*(\d{1,2})日',question[matches[0].end():])
            if short_end:last=last.replace(day=int(short_end[1]),hour=0,minute=0)
    else:
        day = now - timedelta(days=1) if re.search(r'昨天|昨日',question) else now
        first = last = day.replace(hour=0,minute=0,second=0,microsecond=0)
    if (last.date()-first.date()).days not in range(0,32): raise ValueError('预警历史查询每次支持不超过31天，请分段查询。')
    if first.date() > now.date(): raise ValueError('尚未发生的日期没有已发布预警清单；可以另问海况预报。')
    explicit_clock = bool(matches and matches[-1].group(5))
    code_match=re.search(r'(?:编号|警报编号)\s*[:：]?\s*(\d{4,8}-\d{1,4})',question)
    publication_ref=bool(code_match or re.search(r'(?:时|\d:\d{2})\s*发布的',question))
    asof_requested=bool(re.search(r'截至|当前|现在|目前',question))
    if publication_ref and not asof_requested:explicit_clock=False
    current = bool(not matches and re.search(r'当前|现在|目前|最新',question))
    end = (last if explicit_clock else last.replace(hour=23,minute=59,second=59))
    cutoff = min(now, end)
    publication_day = bool(re.search(r'当天|当日|发布了|发布过|发布记录|发布清单|历史记录|哪些公告|哪些通报|解除记录',question))
    mode = 'published' if (publication_day and not asof_requested) or publication_ref or first.date()!=last.date() else 'as_of'
    requested_regions = [name for name in sorted((*CITIES, *PROVINCES, *SEAS),key=len,reverse=True) if name in question]
    specific=[];parents=set()
    for site in ACTIVITY_SITES.values():
        for alias in site['aliases']:
            if alias in question:specific.append(alias);parents.update((site['city'].removesuffix('市'),'浙江'))
    for name,city in coast_places:
        if len(name)>=2 and name in question:specific.append(name);parents.update((city.removesuffix('市'),'浙江'))
    if specific:
        requested_regions=[r for r in requested_regions if r not in parents]
        requested_regions.extend(n for n in specific if n not in requested_regions and not any(n!=longer and n in longer for longer in specific))
    for m in re.finditer(r'([\u4e00-\u9fff]{2,8})(市|县|区|镇|街道)',question):
        name=m[1]
        for province in PROVINCES:
            if province+'省' in name:name=name.split(province+'省')[-1]
        name=re.sub(r'^(?:截至|现在|目前|今天|今日|昨天|请问|查询|请查|在|针对)+','',name)
        if 2<=len(name)<=5 and not re.search(r'哪些|什么|覆盖|发布|预警|灾害|相关|影响|沿海|近岸|海洋|地方',name):
            if name not in requested_regions:requested_regions.append(name)
    # A named city plus its explicitly stated province is one requested object.
    regions = [r for r in requested_regions if not any(CITIES.get(c)==r for c in requested_regions)]
    hazards = [key for key,name in HAZARDS.items() if name in question]
    return {'question':question,'mode':mode,'start':first.replace(hour=0,minute=0,second=0).isoformat(),
            'end':cutoff.isoformat(),'as_of':cutoff.isoformat(),'current':current,'regions':regions,
            'hazards':hazards or list(HAZARDS),'issuer_scope':'national' if re.search(r'国家海洋(?:预报台|环境预报中心)',question) else 'all_available',
            'notice_code':code_match[1] if code_match else None,
            'publication_time_filter':first.isoformat() if publication_ref and not code_match and matches and matches[0].group(5) else None,
            'include_information':bool(re.search(r'消息|信息|公告|发布记录|历史记录',question)),
            'include_cancellations':bool(re.search(r'解除|通报|发布记录|历史记录|发布过',question))}


def periods(text, published):
    """Keep exact verbal windows. Day bounds support coarse filtering only."""
    text = re.split(r'48小时预报|72小时预报', text, maxsplit=1)[0]
    clauses = []
    for line in re.split(r'[。\n]',text):
        if re.search(r'预计|预报|有效|^\s*\d{1,2}(?:月|日)',line) and re.search(r'\d{1,2}(?:月|日)',line) and not re.search(r'视情况解除|预计.{0,15}解除',line):
            clauses.append(line.strip())
    raw = '；'.join(clauses)
    # Do not create exact hours for 上午/中午/下午/夜间.
    days = []
    month = published.month
    for m in re.finditer(r'(?:(\d{1,2})月)?(\d{1,2})日',raw):
        if m[1]: month=int(m[1])
        try:
            d = datetime(published.year,month,int(m[2]),tzinfo=BEIJING)
            if month==1 and published.month==12:d=d.replace(year=published.year+1)
            days.append(d.date().isoformat())
        except ValueError: pass
    conflicts = bool(days and (min(days) < (published-timedelta(days=3)).date().isoformat()
                              or max(days) > (published+timedelta(days=15)).date().isoformat()))
    return {'valid_period_text':raw or '正文未明确给出预报有效时段',
            'valid_from_day':min(days) if days else None,'valid_to_day':max(days) if days else None,
            'valid_time_precision':'verbatim_part_of_day' if days else 'not_specified',
            'publication_period_conflict':conflicts}


def area_records(text, table, hazard):
    """Bind local levels to their own sentence/table row, never global title."""
    rows=[]
    for sentence in re.split(r'[。\n；;]',text):
        if not sentence.strip():continue
        start=0
        levels=list(re.finditer(r'(?:预警级别|警报级别|警报等级|预警等级)(?:为|是)?\s*(红色|橙色|黄色|蓝色)',sentence))
        for level in levels:
            clause=sentence[start:level.end()].strip(' ，,；;');start=level.end()
            found=[n for n in (*PROVINCES,*CITIES,*SEAS) if n in clause]
            if found:rows.append({'names':list(dict.fromkeys(found)),'level':level[1],'text':clause,'origin':'body'})
            elif rows and rows[-1]['origin']=='body' and rows[-1]['level'] is None and re.fullmatch(r'(?:(?:风暴潮|海浪|海冰|海啸|该近岸海域|该海域|上述沿海|上述海域)的?)?(?:预警级别|警报级别|警报等级|预警等级)(?:为|是)?\s*(?:红色|橙色|黄色|蓝色)',clause):
                rows[-1].update(level=level[1],text=rows[-1]['text']+'；'+clause)
        tail=sentence[start:].strip(' ，,；;')
        found=[n for n in (*PROVINCES,*CITIES,*SEAS) if n in tail]
        if found:rows.append({'names':list(dict.fromkeys(found)),'level':None,'text':tail,'origin':'body'})
    if table:
        try: parsed=json.loads(table) if isinstance(table,str) else table
        except (ValueError,TypeError): parsed=[]
        def walk(value, headers=None):
            if not isinstance(value,list):return
            if value and isinstance(value[0],list) and value[0] and value[0][0]=='影响地区':
                for item in value[1:]:walk(item,value[0])
                return
            if value and all(isinstance(v,(str,int,float)) for v in value):
                names=[n for n in (*PROVINCES,*CITIES) if n in str(value[0])]
                if names:
                    color=next((str(v) for v in value[1:3] if str(v) in COLORS),None)
                    labelled=[]
                    for index,v in enumerate(value):
                        header=str(headers[index]) if headers and index<len(headers) else ''
                        unit=re.search(r'[（(](cm|m|厘米|米)[）)]',header)
                        if unit and re.match(r'^-?\d+(?:\.\d+)?(?:[（(]|$)',str(v)):
                            v=re.sub(r'^(-?\d+(?:\.\d+)?)',lambda m:m[1]+' '+unit[1],str(v))
                        labelled.append((header+'：' if header else '')+str(v))
                    rows.append({'names':names,'level':color,'text':' | '.join(labelled),'origin':'official_table'})
            else:
                for item in value:walk(item)
        walk(parsed)
    return rows


def normalize_notice(raw, provider, hazard, stream, fetched_at, snapshot_sha):
    national = provider=='nmefc'
    published = stamp(raw.get('alarmDate') if national else raw.get('PublishTime') or raw.get('t'))
    if published is None: raise ValueError('notice_publication_time_missing')
    title = str((raw.get('title') or '') + (' '+str(raw['subtitle']) if raw.get('subtitle') else '') if national else raw.get('Name') or raw.get('title') or '')
    html = raw.get('description') if national else raw.get('content')
    text = PlainHTML(html or '').text
    if len(text)<20: raise ValueError('notice_body_missing')
    listing_time=published.isoformat()
    if hazard=='bore':
        # The list alarmDate can be a later upload time; use the signed original.
        m=re.search(r'时间\s*[:：]\s*(20\d{2})年(\d{1,2})月(\d{1,2})日(\d{1,2})时(\d{1,2})分',text)
        if m:published=datetime(*map(int,m.groups()),tzinfo=BEIJING)
    title = title or HAZARDS[hazard]+'公告'
    # Forecast sentences such as 预计明日视情况解除 are never cancellation events.
    revoked = '解除' in title
    level = '解除' if revoked else str(raw.get('level') if national else raw.get('wlevel') or raw.get('level') or '')
    if level not in COLORS and level!='解除':level=next((c for c in COLORS if c in title),None)
    if not level and '消息' in title:level='消息'
    code = str(raw.get('code') or '')
    source_id = str(raw.get('id') or (str(raw.get('PublishTime') or published.isoformat())+'|'+hazard))
    uid=hashlib.sha256((provider+'|'+stream+'|'+source_id).encode()).hexdigest()[:24]
    event=code.rsplit('-',1)[0] if '-' in code else None
    sections=re.split(r'(48\s*小时预报|72\s*小时预报)',text)
    forecasts=[]
    for index in range(0,len(sections),2):
        horizon=24 if index==0 else int(re.search(r'\d+',sections[index-1])[0])
        section=sections[index]
        forecasts.append({'horizon_hours':horizon,'areas':area_records(section,raw.get('tblCon') if index==0 else None,hazard),**periods(section,published)})
    information = level in {'消息','信息'} or ('海啸信息' in title)
    notice={'id':uid,'provider':provider,'publisher':str(raw.get('orgUnit') or ('国家海洋预报台' if national else '浙江省海洋监测预报中心')),
            'hazard':hazard,'hazard_name':HAZARDS[hazard],'stream':stream,'source_id':source_id,'code':code,
            'event_key':event,'title':title,'level':level,'is_cancellation':revoked,'is_information':information,
            'source_listing_time':listing_time,'forecasts':forecasts,
            'published_at':published.isoformat(),'source_updated_at':(stamp(raw.get('updateDate')) or published).isoformat(),
            'fetched_at':fetched_at,'body':text,'body_html':str(html or ''),'table':raw.get('tblCon') or '',
            'source_url':(raw.get('htmlUrl') or 'https://www.nmefc.cn/warningDetails?'+urlencode({'id':source_id})) if national else
                'https://zjocean.org.cn/seaWarning/detail?'+urlencode({'type':{'wave':0,'storm':1,'bore':2}[hazard],'ptime':raw.get('PublishTime') or published.strftime('%Y-%m-%d %H:%M:%S')}),
            'archive_snapshot_sha256':snapshot_sha,'original_body_sha256':raw.get('_original_body_sha256'),
            'areas':forecasts[0]['areas'],
            **periods(text,published)}
    if revoked:notice.update(valid_period_text='本条为实际解除通报；解除发布时间见published_at',valid_from_day=None,valid_to_day=None,publication_period_conflict=False)
    # Raw bodies from unrelated coastal provinces remain archived, not re-labelled Zhejiang.
    return notice


def match_region(notice, regions):
    if not regions:return notice['areas']
    matches=[]
    for area in notice['areas']:
        names=area['names']
        if any(r in names or r in area['text'] or (r in PROVINCES and any(CITIES.get(n)==r for n in names)) for r in regions):matches.append(area)
    return matches


class OfficialWarnings:
    def __init__(self, store, fetcher=None):
        self.store,self.fetcher=store,fetcher or self._request
        self.lock=RLock()
        self.raw_root=store.root/'official_warnings'/'snapshots'
        self.raw_root.mkdir(parents=True,exist_ok=True)
        with closing(store.connect()) as db, db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS official_warning_versions(
                notice_id TEXT NOT NULL, version_sha TEXT NOT NULL, provider TEXT NOT NULL,
                published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(notice_id,version_sha));
              CREATE INDEX IF NOT EXISTS warning_publication_time ON official_warning_versions(published_at);
              CREATE TABLE IF NOT EXISTS official_warning_feeds(
                provider TEXT NOT NULL, stream TEXT NOT NULL, year INTEGER NOT NULL,
                fetched_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(provider,stream,year));
            ''')

    @staticmethod
    def _request(provider, parameters):
        headers={'User-Agent':USER_AGENT,'Referer':'https://www.nmefc.cn/' if provider=='nmefc' else 'https://zjocean.org.cn/seaWarning'}
        if provider=='nmefc':r=requests.get(NMEFC,params=parameters,headers=headers,timeout=(5,20),allow_redirects=False,stream=True)
        else:
            token=os.getenv('ZJOCEAN_WARNING_TOKEN','').strip()
            if not token:raise ValueError('zhejiang_service_token_missing')
            endpoint=ZJ_API.replace('disasterList','tsunamiList') if parameters.get('type')==2 else ZJ_API
            r=requests.post(endpoint,data={**parameters,'token':token},headers=headers,timeout=(5,20),allow_redirects=False,stream=True)
        with r:
            if r.status_code!=200:raise ValueError('official_http_'+str(r.status_code))
            parts=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>8*1024*1024:raise ValueError('official_response_too_large')
                parts.append(chunk)
            try:data=json.loads(b''.join(parts).decode('utf-8-sig'))
            except (ValueError,UnicodeError):raise ValueError('official_response_not_json') from None
        if not isinstance(data,dict):raise ValueError('official_response_schema_changed')
        return data

    def _original_body(self, raw):
        """Download only the official original URL advertised by the list."""
        url=raw.get('htmlUrl') or ''
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='obs.nmefc.cn' or not parsed.path.startswith('/Warning/TsunamiAdvice/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('official_body_url_not_allowed')
        record_sha=hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cache_key='official_warning_body:'+hashlib.sha256(url.encode()).hexdigest()
        cached=self.store.state(cache_key)
        if cached and cached.get('record_sha256')==record_sha and local_now()-stamp(cached['checked_at'])<timedelta(days=1):
            path=self.raw_root/(cached['body_sha256']+'.html')
            if path.is_file():
                blob=path.read_bytes()
                if hashlib.sha256(blob).hexdigest()==cached['body_sha256']:
                    return {**raw,'description':blob.decode('utf-8-sig'),'_original_body_sha256':cached['body_sha256']}
        with requests.get(url,headers={'User-Agent':USER_AGENT},timeout=(5,20),allow_redirects=False,stream=True) as response:
            if response.status_code!=200:raise ValueError('official_body_http_'+str(response.status_code))
            parts=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>2*1024*1024:raise ValueError('official_body_too_large')
                parts.append(chunk)
        blob=b''.join(parts);html=blob.decode('utf-8-sig')
        if '海啸' not in PlainHTML(html).text:raise ValueError('official_body_schema_changed')
        sha=hashlib.sha256(blob).hexdigest();path=self.raw_root/(sha+'.html')
        if not path.exists():path.write_bytes(blob)
        self.store.state(cache_key,{'record_sha256':record_sha,'body_sha256':sha,'checked_at':local_now().isoformat()})
        return {**raw,'description':html,'_original_body_sha256':sha}

    def ingest(self, provider, stream, year, data, fetched_at=None, parameters=None, complete=True):
        fetched_at=fetched_at or local_now().isoformat()
        if provider not in {'nmefc','zhejiang'}:raise ValueError('unknown_warning_provider')
        hazard=stream.split(':')[0]
        if hazard not in HAZARDS:raise ValueError('unknown_warning_hazard')
        blob=json.dumps(data,ensure_ascii=False,sort_keys=True).encode()
        sha=hashlib.sha256(blob).hexdigest();target=self.raw_root/(sha+'.json')
        if not target.exists():
            temp=target.with_name(target.name+'.'+uuid4().hex+'.tmp');temp.write_bytes(blob);temp.replace(target)
        if provider=='nmefc':
            if data.get('success') is not True or not isinstance(data.get('obj'),dict):raise ValueError('official_list_unsuccessful')
            groups=data['obj']
            if any(not isinstance(v,list) for v in groups.values()):raise ValueError('official_list_schema_changed')
            raw_rows=[row for group in groups.values() for row in group]
        else:
            if not isinstance(data.get('data'),list):raise ValueError('official_list_unsuccessful')
            raw_rows=data['data']
        def prepare(raw):
            try:
                if not isinstance(raw,dict):raise ValueError('notice_schema_changed')
                if provider=='nmefc' and hazard=='bore' and not raw.get('description'):
                    raw=self._original_body(raw)
                row=normalize_notice(raw,provider,hazard,stream,fetched_at,sha)
                return (row,None) if stamp(row['published_at']).year==year else (None,None)
            except (ValueError,OSError,requests.RequestException) as exc:
                return None,str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[a-z_0-9]+',str(exc)) else type(exc).__name__
        with ThreadPoolExecutor(max_workers=4) as executor:prepared=list(executor.map(prepare,raw_rows))
        notices=[r for r,e in prepared if r];invalid=[e for r,e in prepared if e]
        report={'provider':provider,'stream':stream,'year':year,'fetched_at':fetched_at,
                'state':'ready' if complete and not invalid else 'partial','complete':bool(complete and not invalid),
                'notice_count':len(notices),'invalid_notice_count':len(invalid),'errors':list(dict.fromkeys(invalid)),
                'snapshot_sha256':sha,'source_url':NMEFC if provider=='nmefc' else ZJ_API,
                'request_parameters':parameters or {},'scope':'国家海洋预报台发布记录' if provider=='nmefc' else '浙江省海洋监测预报中心发布记录'}
        with closing(self.store.connect()) as db, db:
            for row in notices:
                # Same notice with a changed body/level/date is a new immutable version.
                content={k:v for k,v in row.items() if k not in {'fetched_at','archive_snapshot_sha256'}}
                version=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                row['version_sha256']=version
                db.execute('INSERT OR IGNORE INTO official_warning_versions VALUES (?,?,?,?,?,?)',
                    (row['id'],version,provider,row['published_at'],fetched_at,json.dumps(row,ensure_ascii=False)))
            db.execute('INSERT OR REPLACE INTO official_warning_feeds VALUES (?,?,?,?,?)',
                (provider,stream,year,fetched_at,json.dumps(report,ensure_ascii=False)))
        return report

    def feeds(self, years=None):
        with closing(self.store.connect()) as db, db:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM official_warning_feeds')]
        return [r for r in rows if years is None or r['year'] in years]

    def _failure(self, provider, stream, year, error):
        row={'provider':provider,'stream':stream,'year':year,'fetched_at':local_now().isoformat(),
             'state':'unavailable','complete':False,'error':error}
        with closing(self.store.connect()) as db, db:db.execute('INSERT OR REPLACE INTO official_warning_feeds VALUES (?,?,?,?,?)',
            (provider,stream,year,row['fetched_at'],json.dumps(row,ensure_ascii=False)))
        return row

    def ensure_year(self, year, refresh=False):
        if not 2000<=int(year)<=local_now().year:raise ValueError('预警归档查询年份需介于2000年与当前年份之间。')
        with self.lock:
            prior={(r['provider'],r['stream']):r for r in self.feeds([year])}
            ttl=timedelta(minutes=10) if year==local_now().year else timedelta(days=1)
            jobs=[]
            for hazard,weather in STREAMS:
                stream=hazard+':'+weather;old=prior.get(('nmefc',stream))
                if not refresh and old and local_now()-stamp(old['fetched_at'])<ttl:continue
                jobs.append((stream,{'type':hazard,'warnType':weather,'year':str(year)}))
            def acquire(job):
                stream,params=job
                try:return self.ingest('nmefc',stream,year,self.fetcher('nmefc',params),parameters=params)
                except (ValueError,OSError,requests.RequestException) as exc:
                    # Never persist request exceptions containing credentials/URLs.
                    code=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[a-z_0-9]+',str(exc)) else type(exc).__name__
                    return self._failure('nmefc',stream,year,code)
            if jobs:list(ThreadPoolExecutor(max_workers=3).map(acquire,jobs))
            # Optional supported provincial credential; no harvesting browser tokens.
            if not os.getenv('ZJOCEAN_WARNING_TOKEN','').strip():
                for hazard in ('wave','storm','bore'):self._failure('zhejiang',hazard,year,'zhejiang_service_token_missing')
            else:
                for hazard,typ in [('wave',0),('storm',1),('bore',2)]:
                    old=prior.get(('zhejiang',hazard))
                    if not refresh and old and old.get('complete') and local_now()-stamp(old['fetched_at'])<ttl:continue
                    try:
                        rows=[];page=1;reached=False
                        while page<=80:
                            data=self.fetcher('zhejiang',{'page':page,'type':typ})
                            group=data.get('data')
                            if not isinstance(group,list):raise ValueError('official_list_schema_changed')
                            rows.extend(group)
                            dates=[stamp(r.get('PublishTime')) for r in group]
                            if not group or (dates and all(d and d.year<year for d in dates)) or page*15>=int(data.get('count',10**9)):
                                reached=True;break
                            page+=1
                        self.ingest('zhejiang',hazard,year,{'data':rows},parameters={'type':typ,'pages':page},complete=reached)
                    except (ValueError,OSError,requests.RequestException) as exc:
                        self._failure('zhejiang',hazard,year,type(exc).__name__)
            return self.feeds([year])

    def versions(self, notice_id=None):
        with closing(self.store.connect()) as db, db:
            rows=db.execute('SELECT payload FROM official_warning_versions'+(' WHERE notice_id=?' if notice_id else ''),
                            (notice_id,) if notice_id else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def status(self):
        with closing(self.store.connect()) as db, db:
            count,versions=db.execute('SELECT count(DISTINCT notice_id),count(*) FROM official_warning_versions').fetchone()
        return {'notice_count':count,'version_count':versions,'feeds':self.feeds(),
                'background_sync':self.store.state('official_warning_sync'),
                'provincial_access_configured':bool(os.getenv('ZJOCEAN_WARNING_TOKEN','').strip())}

    def query(self, question, refresh=False):
        coast_places=[(re.sub(r'(?:街道|镇|乡)$','',r.get('name','')),r.get('Prefecture','')) for r in self.store.entities('coast')]
        spec=query_spec(question,coast_places=coast_places);cutoff=stamp(spec['as_of']);first=stamp(spec['start'])
        years=list(range(first.year,cutoff.year+1))
        if spec['mode']=='as_of' and first.month==1 and first.day<=3 and first.year>2000:years.insert(0,first.year-1)
        for year in years:self.ensure_year(year,refresh=refresh)
        feeds=self.feeds(years)
        # Select a notice version available by the requested publication cutoff.
        unique={};later_versions=0
        for row in self.versions():
            if row['hazard'] not in spec['hazards'] or stamp(row['published_at'])>cutoff:continue
            if stamp(row['source_updated_at'])>cutoff:
                later_versions+=1;continue
            old=unique.get(row['id'])
            if old is None or (row['source_updated_at'],row['fetched_at'])>(old['source_updated_at'],old['fetched_at']):unique[row['id']]=row
        pool=list(unique.values())
        if spec['notice_code']:pool=[r for r in pool if r['code']==spec['notice_code']]
        if spec['publication_time_filter']:pool=[r for r in pool if r['published_at']==spec['publication_time_filter']]
        if spec['mode']=='published':pool=[r for r in pool if stamp(r['published_at'])>=first]
        else:
            latest={}
            for row in pool:
                # Crucially select newer scope-changing updates BEFORE region filtering.
                group=(row['provider'],row['stream'],row['event_key'] or 'unidentified_series')
                if group not in latest or row['published_at']>latest[group]['published_at']:latest[group]=row
            pool=list(latest.values())
        notices=[];conflicts=[]
        for row in pool:
            if spec['issuer_scope']=='national' and row['provider']!='nmefc':continue
            areas=match_region(row,spec['regions'])
            if spec['regions'] and not areas:continue
            if row['publication_period_conflict']:
                conflicts.append({'id':row['id'],'title':row['title'],'published_at':row['published_at'],'valid_period_text':row['valid_period_text']});continue
            if spec['mode']=='as_of' and row.get('valid_to_day') and row['valid_to_day']<cutoff.date().isoformat():continue
            if row['is_cancellation'] and not spec['include_cancellations']:continue
            if spec['mode']=='as_of' and row['is_cancellation'] and stamp(row['published_at'])<first:continue
            if row.get('is_information') and not spec['include_information']:continue
            if spec['mode']=='as_of' and not row['is_cancellation'] and not row.get('valid_to_day'):
                # An undated message from months ago is not an active warning today.
                if stamp(row['published_at']).date()!=cutoff.date():continue
            local_levels=sorted({a['level'] for a in areas if a.get('level')})
            notices.append({**row,'matched_areas':areas,'matched_levels':local_levels,
                            'query_status':'实际解除通报' if row['is_cancellation'] else '截至时刻的最新发布记录' if spec['mode']=='as_of' else '查询时段内发布记录'})
        notices.sort(key=lambda x:x['published_at'],reverse=True)
        zj_names={'浙江',*(c for c,p in CITIES.items() if p=='浙江'),*(name for name,city in coast_places)}
        for site in ACTIVITY_SITES.values():zj_names.update((*site['aliases'],re.sub(r'[县区]$','',site['county'])))
        zj_requested=any(r in zj_names for r in spec['regions'])
        selected_feeds=[f for f in feeds if f['stream'].split(':')[0] in spec['hazards']
                        and (f['provider']=='nmefc' or spec['issuer_scope']!='national' and (not spec['regions'] or zj_requested))]
        gaps=[]
        failed=[f for f in selected_feeds if not f.get('complete')]
        if spec['issuer_scope']!='national':
            if zj_requested and any(f['provider']=='zhejiang' for f in failed):
                gaps.append('浙江省级直接清单接口尚未配置服务端凭据或本次未完整取得；国家海洋预报台记录不能代表省、市全部发布记录。')
            if not spec['regions']:
                gaps.append('当前仅国家海洋预报台分类清单可完整读取，尚未完整接入各省、市的直接发布清单；不能当作全国各机构发布记录的全集。')
            elif any(r not in zj_names for r in spec['regions']):
                gaps.append('尚未接入查询地区全部省、市级直接发布清单；国家海洋预报台记录不能代表当地全部发布记录。')
        if any(f['provider']=='nmefc' for f in failed):gaps.append('部分国家海洋预报台分类或年份未完整取得，清单覆盖不完整。')
        if conflicts:gaps.append('部分官方记录的发布时间与正文预报月份冲突，已隔离，未改写日期或作为有效预警。')
        if later_versions and not notices:gaps.append('部分公告仅取得截至时刻之后修订的正文版本，未用于回填之前的发布状态。')
        if spec['mode']=='as_of' and any(r['valid_time_precision']=='not_specified' for r in notices if not r['is_cancellation']):gaps.append('部分公告未明确有效时段；只能确认发布记录，不能自行设定24小时有效期。')
        if any(r['matched_areas'] and not r['matched_levels'] and not r['is_cancellation'] for r in notices):gaps.append('部分区域正文未明确本地区颜色等级，不能直接套用整份公告最高等级。')
        result={'kind':'official_warnings','label':'官方预警发布记录','trial':False,'state':'partial' if gaps else 'ready',
                'as_of':local_now().isoformat(),'requested_as_of':spec['as_of'],'start':spec['start'],'end':spec['end'],
                'query':spec,'total':len(notices),'items':notices[:30],'truncated':len(notices)>30,
                'coverage_complete':not failed and not gaps,'coverage':selected_feeds,'date_conflicts':conflicts,
                'later_revision_versions_excluded':later_versions,'missing_information':gaps,
                'empty_meaning':'所接入且成功取得的发布源中未找到匹配记录；不等于当地没有预警。',
                'source_policy':'发布时间、原文有效时段、更新和解除分别保存；预计解除不作实际解除。主清单采用公告首个预报时段的地区等级；48/72小时展望分别归档，不能混为同一时段等级。'}
        if result['truncated']:result['missing_information'].append('匹配公告超过30条，正文只展示最新30条，请缩小日期、地区或灾种。');result['state']='partial'
        return result


def warning_table(result, citation='[资料1]'):
    rows=['| 发布机构 | 灾种/公告 | 发布时间（北京时间） | 本地区等级 | 原文预报时段／有效期 | 覆盖范围（原文） |',
          '|---|---|---|---|---|---|']
    for item in result['items']:
        level='解除' if item['is_cancellation'] else item['level']+'（非颜色警报）' if item.get('is_information') else '、'.join(item['matched_levels']) or '本地区未明确；公告总级别'+str(item['level'] or '未明确')
        scope='；'.join(dict.fromkeys(a['text'] for a in item['matched_areas'])) or '正文未明确覆盖范围'
        values=[item['publisher'],item['hazard_name']+' / '+item['title'],item['published_at'],level,item['valid_period_text'],scope]
        rows.append('| '+' | '.join(str(v).replace('|','／').replace('\n',' ') for v in values)+' '+citation+' |')
    return '\n'.join(rows) if result['items'] else ''


def warning_summary(result):
    spec=result['query'];regions='、'.join(spec['regions']) or '全部已接入地区'
    text=f"查询地区：{regions}；截至时刻：{spec['as_of']}，北京时间。\n查询方式：{'按发布时段列举' if spec['mode']=='published' else '按截至时刻选择最新公告，再核对地区和原文有效时段'}。\n"
    text+=f"已取得的匹配发布记录：{result['total']}条。国家与省级发布源分别核对，不把国家记录当成本省全部记录。\n"
    fetched=sorted(f['fetched_at'] for f in result['coverage'] if f.get('complete'))
    if fetched:text+='本次使用的已成功取得清单的采集时刻：'+fetched[0]+' 至 '+fetched[-1]+'；历史查询是对官方历史清单的回溯，不宣称已留存查询日所有网页修订。\n'
    if not result['items']:text+=result['empty_meaning']+'\n'
    text+=warning_table(result)+'\n'
    text+='\n'.join('缺项：'+x for x in result['missing_information'])
    text+='\n预报作用时段与公告失效时间分别判断；没有明确失效时间时不自行设置24小时有效期。时段保留官方“上午、下午、夜间”等原文，不擅自补成精确小时；预计解除与实际解除通报分别处理。\n'
    text+='主清单采用首个预报时段的地区等级；48和72小时展望是另一个时段，不合并为当前本地区等级。海啸信息、海浪消息与颜色警报分别列明。\n'
    for item in result['items']:
        text+='\n公告：'+item['title']+'；编号：'+(item['code'] or item['source_id'])+'；发布机构：'+item['publisher']+'；发布时间：'+item['published_at']+'\n'
        text+='首个预报时段原文：'+re.split(r'48\s*小时预报|72\s*小时预报',item['body'],maxsplit=1)[0]+'\n'
        if item['table']:text+='官方表格：'+item['table']+'\n'
        text+='原文链接：'+item['source_url']+'；版本SHA256：'+item['version_sha256']+'\n'
    return text


def answer_warnings(runtime, question, qa, meta,allow_web=True):
    result=runtime.warnings.query(question)
    text=warning_summary(result)
    evidence_id=hashlib.sha256(text.encode()).hexdigest()[:24]
    runtime.store.state('evidence:'+evidence_id,result)
    hit={'source_id':'forecast:'+evidence_id,'chunk_id':evidence_id,'title':'官方预警清单与正文查询',
         'filename':'官方预警发布记录','locator':result['start']+'至'+result['end'],
         'category':'官方预警公告','status':'indexed','text':text,'retrieval':'official_warning_archive',
         'metadata':{'source_type':'business_forecast','external_allowed':True,'trial':False,
                     'official_warning_records':True,'retrieved_at':result['as_of'],'unit':'公告原文单位'},
         'retrieval_details':{'direct_evidence':True}}
    sources=qa._public_sources(question,[hit])
    sources[0]['supporting_passages']=[{'id':1,'quote':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}]
    meta.update(route='official_warnings',business_query='official_warnings',business_trial=False,
                business_state=result['state'],business_evidence_id=evidence_id,official_warning_records=True,
                required_business_facts=[],tool_data_view=False,
                business_instruction='这是官方已经发布的预警公告查询，不是NC试算，不使用假设试算标题。先用简短中文回答查到了哪些灾种和等级，随后分别回答有效时段与覆盖地区。已查询清单中的字段按资料1解释；其他资料可补充定义、适用规则和符合所问日期地区的官方公告，分别说明来源，不能用历史公告冒充当前状态。国家海洋预报台记录不能称为省级或市级完整清单。只讨论用户查询地区，不引入无关省份。本地区等级与公告最高等级不同时使用本地区等级；没有本地区等级则说明未明确。主清单仅为首个预报时段，48/72小时展望不能混入同一时段等级。预计解除不是实际解除。无匹配记录不能说没有预警；覆盖缺项必须具体交代。公告月份冲突不得自行改写。不推导应急响应级别或实际停航。')
    organized=None
    hits=qa.joint_evidence(question,meta,allow_web,pinned=[hit],queries=meta.get('retrieval_queries')) if getattr(qa,'crag_enabled',False) else [hit]
    if qa.external_enabled and qa.model_caller:
        organized=qa._organized_answer(question,qa.model_name,hits,meta)
    if organized and organized.get('answer_meta',{}).get('used_mode')!='model_error':
        prose=organized['answer']
        output_meta={**meta,**organized['answer_meta'],'route':'official_warnings'}
    else:
        prose=(f"按所接入的官方发布记录，查询到{result['total']}条匹配公告。[资料1]" if result['items'] else result['empty_meaning']+'[资料1]')
        output_meta={**meta,'used_mode':'business_data','generation_used':bool(organized),
                     'validation_error':(organized or {}).get('answer_meta',{}).get('validation_error')}
    # The complete selected ledger always reaches the user even if prose omits a row.
    appendix=warning_table(result)
    answer='**官方预警发布记录**\n\n'+prose
    if appendix:answer+='\n\n**本次匹配公告清单**\n\n'+appendix
    if result['missing_information']:answer+='\n\n资料覆盖说明：'+'；'.join(result['missing_information'])+'[资料1]'
    fetched=sorted(f['fetched_at'] for f in result['coverage'] if f.get('complete'))
    if fetched:answer+='\n\n数据采集：'+fetched[0]+' 至 '+fetched[-1]+'，北京时间；历史日期按官方历史清单回溯。[资料1]'
    output_meta.update(answerability='partial' if result['state']!='ready' or not result['items'] else 'full',
                       missing_information=result['missing_information'],official_warning_count=result['total'],
                       local_evidence_count=meta.get('local_evidence_count',0),evidence_sent_count=len(hits),general_knowledge_used=False)
    rag=sources+([s for s in organized.get('rag_sources',[]) if s.get('source_id')!=sources[0]['source_id']] if organized else [])
    return {'answer':answer,'rag_sources':rag,'web_sources':organized.get('web_sources',[]) if organized else [],'answer_meta':output_meta}
