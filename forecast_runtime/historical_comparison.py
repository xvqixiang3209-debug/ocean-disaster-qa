"""Versioned event metrics and bounded forecast/historical comparisons.

Only explicit event sections of already-indexed bulletins supply numeric
records. A regional forecast peak is a benchmark against reported peaks,
never a local inundation/loss model or a claim of cyclone attribution.
"""
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import re
import sqlite3

from .storage import BEIJING, local_now

COMPARE=r'相比|对比|比较|对照|比.{0,60}(?:重|轻|高|低|强|弱|差|大|小)'
NUMBER=r'[+-]?\d+(?:[ \u00a0]\d{3})*(?:\.\d+)?'
ZHEJIANG_CITIES=('杭州','宁波','温州','嘉兴','湖州','绍兴','金华','衢州','舟山','台州','丽水')
AGGREGATE_METRICS={'direct_loss_yi','deaths','deaths_missing'}
EVENT_HEADING=re.compile(r'(?m)^[ \t]*(?:[●•]|\d+[.、．])?\s*(\d{4})\s*号?\s*[“"‚「]([\u4e00-\u9fff]{2,8})[”"‛」]\s*台风(?:风暴潮)?(?:灾害)?[ \t]*(?=\r?$)')
# An unrecognised numbered event, e.g. a compound cyclone or an extratropical
# surge, must terminate the preceding event just as a recognised one does.
SECTION_BOUNDARY=re.compile(r'(?m)^[ \t]*(?:[一二三四五六七八九十]+、[^\r\n]+|(?:[●•]|\d+[.、．])[ \t]*[^\r\n]{0,90}(?:台风|温带风暴潮)[^\r\n]{0,25})[ \t]*(?=\r?$)')


def comparison_question(question):
    # Retrieving an unnamed historical analogue is a selection task. A follow-up
    # asking that selected storm's loss must not turn it into a comparison of
    # two already-identified events. Keep explicit historical metric pairs here.
    from .routing import analogue_question
    select_unknown = re.search(r'哪(?:一)?(?:次|个|场).{0,8}台风|哪些.{0,8}台风|(?:找|寻找|检索|匹配).{0,24}(?:相似|类似|最像)|(?:最相似|最类似).{0,12}台风', question)
    explicit_events = _named_past_requests(question, 10000)
    if analogue_question(question) and select_unknown and len(explicit_events) < 2:
        return False
    return bool(re.search(COMPARE,question) and re.search(r'台风|风暴潮|增水|潮位|海浪',question)
                and re.search(r'历史|过去|(?:19|20)\d{2}年',question)
                and re.search(r'增水|潮位|风暴潮|海浪|损失|灾损|死亡',question)
                and not re.search(r'最严重|排名|排行',question)
                and not re.search(r'算法|相似度|怎么算|计算方法|研究方法',question))


def _number(value):
    return float(Decimal(re.sub(r'\s+','',value)))


def _compact(text):
    """Normalize extraction spacing for parsing, while retaining original proof."""
    value=re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])','',text)
    value=re.sub(r'(?<=\d)\s+(?=\d{3}(?:\D|$))','',value)
    return value


def _proof(row,text):
    return {'chunk_id':row['chunk_id'],'locator':row['locator'],'text':text,
            'sha256':hashlib.sha256(text.encode()).hexdigest()}


def _metric(event,row,text,key,value,unit,**extra):
    # The event total precedes sector breakdowns. Never replace the total with
    # a later sector value or a continuation from another event.
    if key in event['metrics']:return
    event['metrics'][key]={'value':value,'unit':unit,'source_id':event['source_id'],
        'chunk_id':row['chunk_id'],'locator':row['locator'],'quote':text,
        'source_title':event['source_title'],'source_sha256':event['source_sha256'],**extra}


def _statistical_jurisdiction(text,position):
    """Bind loss/casualty totals to the scope of their own statistical sentence.

    Being printed in a provincial bulletin is not proof of a provincial total.
    Multiple cities remain one combined statistic and cannot be split or used
    as either city's total. Figure captions and earlier storm-track sentences
    are not administrative scope evidence for a later loss statement.
    """
    prefix=re.split(r'[。！？遥]',text[:position])[-1]
    prefix=re.sub(r'浙江省海洋灾害公报','',prefix)
    city_pattern='(?:'+'|'.join(ZHEJIANG_CITIES)+')市?'
    areas=[]
    for match in re.finditer(city_pattern+r'(?:[、和及]'+city_pattern+r')*',prefix):
        members=[m[0].removesuffix('市')+'市' for m in re.finditer(city_pattern,match[0])]
        areas.append((match.start(),{'kind':'city' if len(members)==1 else 'city_group',
                    'name':'、'.join(members),'members':members}))
    for match in re.finditer(r'全省|我省|浙江省(?!'+city_pattern+r')',prefix):
        areas.append((match.start(),{'kind':'province','name':'浙江','members':['浙江']}))
    if not areas:return {'kind':'unknown','name':'原文未明确地域范围','members':[]}
    return max(areas,key=lambda item:item[0])[1]


def _aggregate_scope_matches(metric,scope):
    jurisdiction=metric.get('jurisdiction') or {}
    if scope['kind']=='province':return jurisdiction.get('kind')=='province'
    return jurisdiction.get('kind')==scope['kind'] and jurisdiction.get('name')==scope['name']


def _table_rows(event,row,text):
    lines=text.splitlines();columns=None
    for line in lines:
        if '|' in line:
            cells=[v.strip() for v in line.strip().strip('|').split('|')]
            if any('站名' in c for c in cells):
                columns={}
                for i,cell in enumerate(cells):
                    field=re.sub(r'[\s/]+','',cell)
                    if '站名' in field:columns['station']=i
                    if re.search(r'最高潮位[（(]厘米[）)]',field):columns['water_peak_cm']=i
                    if re.search(r'最大(?:风暴)?增水[（(]厘米[）)]',field):columns['surge_peak_cm']=i
                    if '最高潮位出现时间' in field:columns['water_time']=i
                    if '最大增水出现时间' in field:columns['surge_time']=i
                    if '预警级别' in field:columns['warning']=i
                continue
            if not columns or 'station' not in columns:continue
            if any(index>=len(cells) for index in columns.values()):continue
            station=cells[columns['station']]
            if not re.fullmatch(r'[\u4e00-\u9fff]{1,15}',station):continue
            for key,timekey in [('water_peak_cm','water_time'),('surge_peak_cm','surge_time')]:
                index=columns.get(key)
                if index is None or not re.fullmatch(NUMBER,cells[index]):continue
                record={'station':station,'value':_number(cells[index]),'unit':'cm',
                    'observed_time_text':cells[columns[timekey]] if timekey in columns else None,
                    'warning_level':cells[columns['warning']] if 'warning' in columns else None,
                    'source_id':event['source_id'],'chunk_id':row['chunk_id'],'locator':row['locator'],'quote':text}
                event['water_levels' if key=='water_peak_cm' else 'surge_levels'].append(record)
        # PDF text tables retain one station, level, time and colour per line.
        # Require a matching centimetre table header in this same source span.
        elif re.search(r'站名\s+最高潮位\s*[（(渊]厘米[）)冤]',text):
            match=re.fullmatch(r'\s*([\u4e00-\u9fff]{1,15}站)\s+('+NUMBER+r')\s+((?:\d{1,2}\s*月\s*)?\d{1,2}\s*日\s*\d{1,2}:\d{2})\s+(红色|橙色|黄色|蓝色)(?:[（(].*[）)])?\s*',line)
            if match:event['water_levels'].append({'station':match[1],'value':_number(match[2]),'unit':'cm',
                'observed_time_text':match[3],'warning_level':match[4],'source_id':event['source_id'],
                'chunk_id':row['chunk_id'],'locator':row['locator'],'quote':text})


def _consume(event,row,text):
    text=text.strip()
    if not text:return
    event['evidence'].append(_proof(row,text));compact=_compact(text)
    if re.search(r'(?:基面|基准).{0,12}(?:85|1985)\s*(?:国家)?高程',compact):event['datum']='1985'
    surge=re.search(r'最大(?:风暴)?增水(?:为|达|达到|可达|约为)?\s*('+NUMBER+r')\s*(厘米|米)',compact)
    station=None
    if surge:
        name=re.search(r'发生在\s*([^，。；袁遥\s]{1,12}?)站',compact[surge.end():])
        station=name[1] if name else None
    else:
        surge=re.search(r'最大(?:风暴)?增水(?:出现|发生)在\s*([^，。；\s]{1,12}?)站\s*[（(]\s*('+NUMBER+r')\s*(厘米|米)',compact)
        if surge:
            station=surge[1]
            _metric(event,row,text,'surge_peak_cm',_number(surge[2])*(100 if surge[3]=='米' else 1),'cm',station=station,
                    scope='浙江沿岸所引事件段落的观测最大增水')
            surge=None
    if surge:_metric(event,row,text,'surge_peak_cm',_number(surge[1])*(100 if surge[2]=='米' else 1),'cm',station=station,
                     scope='浙江沿岸所引事件段落的观测最大增水')
    # Exclude sector-only continuation paragraphs. The first event-total
    # sentence and its original scope stay attached to the numeric value.
    if not re.match(r'其中|水产养殖|渔船|船只|码头|其他损失',compact):
        loss=re.search(r'直接经济损失(?:约为|达到|约|为|达)?\s*('+NUMBER+r')\s*(亿元|万元)',compact)
        if loss:
            jurisdiction=_statistical_jurisdiction(compact,loss.start())
            _metric(event,row,text,'direct_loss_yi',round(_number(loss[1])/(10000 if loss[2]=='万元' else 1),8),'亿元',
                jurisdiction=jurisdiction,region=jurisdiction['name'],
                scope=jurisdiction['name']+'；'+('风暴潮及近岸浪共同影响' if '近岸浪' in compact else '公报所引事件灾情口径，未包含台风其他灾种损失'))
    deaths=re.search(r'(?:未造成|无)人员死亡(?:[（(]含失踪[）)]|失踪)',compact)
    key='deaths_missing'
    if not deaths:
        deaths=re.search(r'(?:未造成|无)人员死亡|无人员伤亡',compact);key='deaths'
    if deaths:
        jurisdiction=_statistical_jurisdiction(compact,deaths.start())
        _metric(event,row,text,key,0,'人',jurisdiction=jurisdiction,region=jurisdiction['name'],
                scope=jurisdiction['name']+'；'+('死亡及失踪' if key=='deaths_missing' else '死亡人数；未据此补造失踪人数'))
    _table_rows(event,row,text)


def bulletin_events(database):
    """Extract explicit per-event prose and height tables; no annual totals."""
    if not Path(database).is_file():return []
    events=[]
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory=sqlite3.Row
        sources=connection.execute("SELECT * FROM sources WHERE filename LIKE '%公报%' AND status IN ('indexed','approved') ORDER BY filename,source_id").fetchall()
        for source in sources:
            year=re.search(r'(?:19|20)\d{2}',source['filename'])
            if not year:continue
            rows=connection.execute('SELECT chunk_id,locator,text,metadata FROM chunks WHERE source_id=? ORDER BY chunk_id',(source['source_id'],)).fetchall()
            # This adapter parses Zhejiang bulletins; a national or another
            # province's bulletin cannot silently become Zhejiang statistics.
            if re.search(r'中国|全国|广东|福建|山东|广西|海南|辽宁|江苏|河北|上海|天津',source['filename']):continue
            if '浙江' not in source['filename'] and not re.search(r'浙江省海洋灾害公报',
                    _compact('\n'.join(row['text'] for row in rows[:10]))):continue
            document_datum=next((_proof(row,row['text']) for row in rows if re.search(
                r'本公报.{0,12}所有潮位.{0,30}(?:85|1985)\s*(?:国家)?高程',_compact(row['text']))),None)
            current=None
            for row in rows:
                text=row['text'];headings={m.start():m for m in EVENT_HEADING.finditer(text)}
                boundaries={m.start():m for m in SECTION_BOUNDARY.finditer(text)
                            if not re.fullmatch(r'\s*二、风暴潮灾害\s*',m[0])};boundaries.update(headings)
                position=0
                for offset,heading in sorted(boundaries.items()):
                    if current:_consume(current,row,text[position:offset])
                    current=None
                    if offset in headings:
                        heading=headings[offset]
                        if int(heading[1][:2])==int(year[0])%100:
                            current={'storm_code':heading[1],'name':heading[2],'season':int(year[0]),'region':'浙江',
                                'source_id':source['source_id'],'source_title':source['filename'],'source_sha256':source['sha256'],
                                'metrics':{},'water_levels':[],'surge_levels':[],'evidence':[],
                                'datum':'1985' if document_datum else None,'datum_evidence':document_datum}
                            events.append(current)
                    position=offset
                if current:_consume(current,row,text[position:])
            # Do not infer event maxima from unrelated sections or annual rows.
    for event in events:
        if event['water_levels']:
            event['metrics']['water_peak_cm']=max(event['water_levels'],key=lambda r:r['value']) | {
                'datum':event['datum'],'scope':'公报该表所列测站中的最大值，不代表全省所有测站的绝对潮位最大值'}
    return [event for event in events if event['metrics']]


def _report_events(runtime,question):
    service=getattr(runtime,'analogue_service',None)
    repository=getattr(service,'report_repository',None)
    path=getattr(repository,'database_path',None)
    if repository is None or not path or not Path(path).is_file():return []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory=sqlite3.Row
        candidates=connection.execute("SELECT DISTINCT sid,storm_name_cn,storm_name_en,season_resolved,storm_code FROM typhoon_report_cases WHERE qc_status!='fail'").fetchall()
    result=[]
    for row in candidates:
        names=[str(row[key] or '') for key in ('storm_name_cn','storm_name_en','storm_code')]
        if not any(name and (name.casefold() in question.casefold()) for name in names):continue
        years=_event_years(question,row['storm_name_cn'] or '\0')
        if years and str(row['season_resolved']) not in years:continue
        record=repository.lookup({'sid':row['sid'],'name':row['storm_name_en'],'season':row['season_resolved']})
        if not record:continue
        metrics={}
        for field,key in [('max_surge_m','surge_peak_cm'),('max_water_level_m','water_peak_cm')]:
            if record.get(field) is not None:
                metrics[key]={'value':round(record[field]*100,3),'unit':'cm','station':record.get(field.replace('_m','_station')),
                              'source_title':record.get('source_file'),'source_sha256':record.get('source_sha256'),
                              'source_kind':'structured_historical_report','case_id':record.get('case_id'),'raw_field':field,
                              'observed_time_text':record.get(field.replace('_m','_time')),
                              'coverage':'parsed_report_station_subset',
                              'scope':'旧历史报告已解析测站子集的记录；尚未核为全事件或浙江区域最大值，不进入区域峰值比较'}
        result.append({'name':row['storm_name_cn'],'season':row['season_resolved'],'region':'浙江',
                       'source_title':record.get('source_file'),'source_sha256':record.get('source_sha256'),
                       'event_start':record.get('event_start'),'event_end':record.get('event_end'),
                       'coverage':'parsed_report_station_subset',
                       'metrics':metrics,'evidence':[{'kind':'structured_report_record','record':record}],
                       'water_levels':[],'surge_levels':[],'alternate_source':True})
    return result


def _forecast_window(runtime,question,start,end):
    cutoff=getattr(runtime._local,'request_cutoff',None) or local_now()
    # Explicit valid intervals remain authoritative. An as-of date alone is
    # not a forecast-valid interval; use one issued batch's available horizon.
    explicit=bool(re.search(r'(?:日|\d时|\d:\d\d)\s*(?:至|到|~|～|—)\s*(?:20\d{2}|\d{1,2}月)',question)
                  or re.search(r'未来\s*\d+\s*(?:小时|天)',question))
    issued=[r for r in runtime.store.grids() if r['product']=='SWL' and datetime.fromisoformat(r['batch'])<=cutoff]
    if not issued:return None
    latest=max(r['batch'] for r in issued);batch=[r for r in issued if r['batch']==latest]
    if not explicit:
        start=max(cutoff.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1),min(datetime.fromisoformat(r['valid_from']) for r in batch))
        end=min(min(datetime.fromisoformat(r['valid_to']) for r in batch),start+timedelta(hours=167))
    if start>end:return None
    return start,end,latest


def _event_years(question,name):
    escaped=re.escape(name)
    before=re.findall(r'((?:19|20)\d{2})年\s*(?:第\d+号)?\s*(?:台风)?\s*[“"「]?'+escaped,question)
    after=re.findall(escaped+r'[”"」]?\s*(?:台风)?\s*[（(]?((?:19|20)\d{2})年',question)
    return set(before+after)


def _named_past_requests(question,year):
    pattern=r'((?:19|20)\d{2})年\s*(?:台风)?\s*(?:[“"「]([\u4e00-\u9fff]{2,8})[”"」]|([\u4e00-\u9fff]{2,8}?)(?=台风|风暴潮|与|和|的|相比|[，,；;。]))'
    return {(int(match[1]),match[2] or match[3]) for match in re.finditer(pattern,question) if int(match[1])<year}


def _station_key(name):
    value=str(name or '').removesuffix('站')
    for city in ('嘉兴','宁波','舟山','台州','温州'):
        if value.startswith(city) and len(value)>len(city):return value[len(city):]
    return value


def _scope(runtime,question,historical):
    coasts=runtime.coasts() if hasattr(runtime,'coasts') else []
    cities={str(c.get('Prefecture') or '') for c in coasts}|{city+'市' for city in ZHEJIANG_CITIES}
    counties={str(c.get('County') or '') for c in coasts}
    candidates=[(name,'county') for name in counties if len(name)>1 and (name in question or name.removesuffix('市').removesuffix('县').removesuffix('区') in question)]
    candidates += [(name,'city') for name in cities if len(name)>1 and (name in question or name.removesuffix('市') in question)]
    stations={str(row.get('station') or '') for event in historical for key in ('water_levels','surge_levels') for row in event.get(key,[])}
    stations.update(str(metric.get('station') or '') for event in historical for metric in event['metrics'].values())
    explicit=[s for s in stations if s and (_station_key(s)+'站') in question]
    if explicit:return {'kind':'station','name':max(explicit,key=len),'label':_station_key(max(explicit,key=len))+'站'}
    if candidates:
        # A named county outranks its prefecture. Multiple separate counties
        # require a task-specific scope instead of merging their peaks.
        level='county' if any(kind=='county' for _,kind in candidates) else 'city'
        chosen=sorted({name for name,kind in candidates if kind==level})
        if len(chosen)>1:return {'kind':'ambiguous','name':'、'.join(chosen),'label':'、'.join(chosen)}
        return {'kind':level,'name':chosen[0],'label':chosen[0]}
    foreign=re.search(r'全国|广东|福建|山东|广西|海南|辽宁|江苏|河北|上海|天津',question)
    if foreign:return {'kind':'unsupported','name':foreign[0],'label':foreign[0]}
    return {'kind':'province','name':'浙江','label':'浙江'}


def _scoped_event(event,scope):
    value=deepcopy(event)
    if scope['kind']=='province':return value
    value['original_region']=event['region'];value['region']=scope['label']
    value['metrics']={};value['unavailable_metrics']=list(event['metrics'])
    for key,metric in event['metrics'].items():
        if key in AGGREGATE_METRICS and _aggregate_scope_matches(metric,scope):value['metrics'][key]=metric
    def match(record):
        station=record.get('station') or ''
        if scope['kind']=='station':return _station_key(station)==_station_key(scope['name'])
        # Only an explicit city prefix proves the table station's city here.
        # A nearby tide-reference or current coast mapping is not a historical
        # administrative/observational membership proof.
        return scope['kind']=='city' and station.startswith(scope['name'].removesuffix('市'))
    for key,rows in [('water_peak_cm','water_levels'),('surge_peak_cm','surge_levels')]:
        value[rows]=[row for row in event.get(rows,[]) if match(row)]
        if value[rows]:value['metrics'][key]=max(value[rows],key=lambda r:r['value']) | {
            'datum':event.get('datum') if key=='water_peak_cm' else None,
            'scope':scope['label']+'，该公报明确列出的测站峰值，不代表全域完整测站统计'}
        elif scope['kind']=='station' and key in event['metrics'] and match(event['metrics'][key]):value['metrics'][key]=event['metrics'][key]
    value['unavailable_metrics']=[key for key in value['unavailable_metrics'] if key not in value['metrics']]
    return value


def _event_label(event):return str(event['season'])+'年'+event['name']


def _requested_metrics(question):
    result=[]
    for pattern,key in [(r'增水','surge_peak_cm'),(r'潮位|水位','water_peak_cm'),(r'损失|灾损','direct_loss_yi'),(r'死亡|失踪','deaths_missing'),(r'海浪|波高','wave_peak_m')]:
        if re.search(pattern,question):result.append(key)
    return result or ['surge_peak_cm','water_peak_cm','direct_loss_yi']


def query_comparison(runtime,question,start,end):
    database=runtime.store.root.parent/'marine_kb/knowledge.sqlite3'
    catalog=bulletin_events(database)
    selected=[event for event in catalog if event['name'] in question or re.search(r'(?<!\d)'+re.escape(event['storm_code'])+r'(?!\d)',question)]
    # Repeated names in different years require an explicit year, not a guess.
    for name in {e['name'] for e in selected}:
        years=_event_years(question,name)
        if years:selected=[e for e in selected if e['name']!=name or str(e['season']) in years]
    reports=_report_events(runtime,question)
    unique={}
    for event in sorted(selected,key=lambda e:len(e['metrics']),reverse=True):
        key=(event['season'],event['name'])
        if key in unique:unique[key].setdefault('alternative_reports',[]).append(event)
        else:unique[key]=event
    for event in reports:
        key=(event['season'],event['name'])
        if key in unique:unique[key].setdefault('alternative_reports',[]).append(event)
        else:unique[key]=event
    historical=sorted(unique.values(),key=lambda event:(question.find(event['name']),event['season']))
    scope=_scope(runtime,question,historical)
    requested=_requested_metrics(question)
    cutoff=getattr(runtime._local,'request_cutoff',None)
    base={**runtime.base_result(start,end),'kind':'historical_comparison','historical':historical,
          'comparison_mode':'historical_events','comparisons':[],
          'state':'ready' if historical else 'no_data','overall_disaster_severity_computed':False,
          'requested_as_of':cutoff.isoformat() if cutoff else None,'requested_scope':scope,
          'requested_metrics':requested,'missing_information':[],
          'historical_periods':[{'event':_event_label(event),'season':event['season'],
                                 'start':event.get('event_start'),'end':event.get('event_end')} for event in historical],
          'limitations':['仅按相同指标分项对照；不同站点的峰值不是本站水位差，不能据此推出淹没范围或经济损失。']}
    if not historical:
        return {**base,'reason':'没有唯一匹配到所问历史台风的已核对事件资料，请明确名称及年份。'}
    if len(historical)>2 or any(sum(e['name']==event['name'] for e in historical)>1 and not _event_years(question,event['name']) for event in historical):
        return {**base,'state':'needs_clarification','historical':[],'reason':'匹配到同名不同年或多于两个历史事件，请明确两次事件的名称及年份。'}
    if scope['kind'] in {'unsupported','ambiguous'}:
        return {**base,'state':'needs_clarification','historical':[],
                'reason':'当前结构化比较只覆盖浙江事件；所问范围为'+scope['label']+'，尚未取得该范围的对应指标。'}
    historical=[_scoped_event(event,scope) for event in historical];base['historical']=historical
    if scope['kind']!='province':base['limitations'].append('只使用能核定归属到'+scope['label']+'的历史测站指标和地域统计；全省事件损失和其他测站峰值未替代本地数据。')
    for event in historical:
        if event.get('coverage')=='parsed_report_station_subset':
            base['missing_information'].append(_event_label(event)+'的旧报告字段仅覆盖已解析测站子集，尚未核为该区域全事件峰值，未用其计算区域强弱差值。')
        for metric in requested:
            if metric not in event['metrics']:
                base['missing_information'].append(_event_label(event)+'在'+scope['label']+'范围缺少'+METRIC_LABELS.get(metric,metric)+'的明确记录。')
            elif metric in AGGREGATE_METRICS and not _aggregate_scope_matches(event['metrics'][metric],scope):
                local=event['metrics'][metric].get('region','地域范围未核定')
                base['missing_information'].append(_event_label(event)+'的'+METRIC_LABELS[metric]+'原文统计范围为'+local+'，未取得'+scope['label']+'完整同口径统计，保留该局部记录但不作所问范围差值。')
    if len(historical)==2:
        left,right=historical
        for key,a in left['metrics'].items():
            if key=='water_peak_cm':continue
            b=right['metrics'].get(key)
            if not b or a['unit']!=b['unit']:continue
            if key in AGGREGATE_METRICS and not (_aggregate_scope_matches(a,scope) and _aggregate_scope_matches(b,scope)):continue
            if a.get('coverage')=='parsed_report_station_subset' or b.get('coverage')=='parsed_report_station_subset':continue
            base['comparisons'].append({'metric':key,'left':_event_label(left),'left_value':a['value'],
                'right':_event_label(right),'right_value':b['value'],'difference':round(a['value']-b['value'],8),'unit':a['unit'],
                'kind':'historical_same_metric','statistical_scope':'两份所列事件资料在'+scope['label']+'的分项统计，保留原统计范围；'+
                    ('两次事件所列测站峰值对照，不代表同站验证。' if key=='surge_peak_cm' else '名义金额未作价格平减。' if key=='direct_loss_yi' else '')+'非全国全历史排名'})
        if left.get('datum')==right.get('datum')=='1985':
            right_stations={_station_key(row['station']):row for row in right.get('water_levels',[])}
            for a in left.get('water_levels',[]):
                station=_station_key(a['station']);b=right_stations.get(station)
                if not b:continue
                base['comparisons'].append({'metric':'water_peak_cm','station':station,
                    'left':_event_label(left),'left_value':a['value'],'right':_event_label(right),'right_value':b['value'],
                    'difference':round(a['value']-b['value'],3),'unit':'cm','kind':'historical_same_station',
                    'statistical_scope':'同名站、同一公报明确1985基准的两次最高潮位，未推定其他站或损失'})
        compared={row['metric'] for row in base['comparisons']}
        for key in requested:
            if key not in compared:base['missing_information'].append(METRIC_LABELS.get(key,key)+'未取得可作同口径差值的两侧记录；潮位差还需同站及基准一致。')
        if base['missing_information'] or not base['comparisons']:base['state']='partial'
        return base
    missing_events=_named_past_requests(question,(cutoff or local_now()).year)-{(event['season'],event['name']) for event in historical}
    if missing_events or not re.search(r'本次|这次|此次|当前|预报|未来|截至',question):
        return {**base,'state':'partial','reason':'只匹配到一次历史事件，另一次历史事件缺少同范围结构化数据，未以当前预报替代。',
                'missing_information':base['missing_information']+([str(year)+'年'+name+'的同范围历史事件指标。' for year,name in sorted(missing_events)] or ['另一历史事件的明确名称、年份和对应指标。'])}
    window=_forecast_window(runtime,question,start,end)
    if not window:return {**base,'state':'partial','reason':'已取得历史事件指标，但没有截至查询时刻已起报的水位预报场。'}
    start,end,expected_batch=window
    if scope['kind']=='station':
        return {**base,'state':'partial','reason':'已取得历史测站数据，但没有该历史测站与当前模式岸段的核定同站映射，未使用全省岸段峰值替代。'}
    coastal=runtime.coastal(start,end,'' if scope['kind']=='province' else scope['name'])
    if coastal.get('selected_batch')!=expected_batch or (cutoff and datetime.fromisoformat(coastal['selected_batch'])>cutoff):
        return {**base,'state':'partial','reason':'可用预报批次与截至时刻选定批次不一致，未拼接或使用未来起报数据。'}
    usable=[r for r in coastal['results'] if r.get('state')=='ready']
    base.update(start=start.isoformat(),end=end.isoformat(),comparison_mode='forecast_regional_peak_benchmark',
                forecast={'start':start.isoformat(),'end':end.isoformat(),'batch':coastal.get('selected_batch'),'region':scope['label'],
                          **{k:coastal[k] for k in ('requested_count','usable_count','missing_count','partial_count')},'metrics':{}},
                cyclone_attribution_verified=False)
    base['limitations'] += ['当前NC未提供可核定的台风场景标识；只能对照已起报海况预报场，不能确认整个场均由所问台风造成。',
                           '预报为岸段代表点，历史为验潮站统计；采样点与时段不同，峰值对照不构成同站实况验证。']
    for key,field in [('surge_peak_cm','swl_cm'),('water_peak_cm','total_cm')]:
        if key=='surge_peak_cm' and runtime.config()['swl_kind']!='surge':continue
        candidates=[(v[field],r,v) for r in usable for v in r.get('series',[]) if v.get(field) is not None]
        if not candidates:continue
        value,coast,point=max(candidates,key=lambda pair:pair[0])
        metric={'value':value,'unit':'cm','location':coast['name'],'time':point['time'],'batch':coastal.get('selected_batch'),
                'scope':'已取得岸段代表点的预报峰值，非验潮站实测；假设口径'}
        base['forecast']['metrics'][key]=metric
        other=historical[0]['metrics'].get(key)
        # Unknown historical water datum cannot support a total-height delta.
        if other and key=='surge_peak_cm' and other.get('coverage')!='parsed_report_station_subset':
            base['comparisons'].append({'metric':key,'left':scope['label']+'已取得岸段预报','left_value':value,
                'right':_event_label(historical[0]),'right_value':other['value'],'difference':round(value-other['value'],3),
                'unit':'cm','kind':'regional_peak_benchmark','statistical_scope':'预报岸段峰值与历史测站峰值，仅作指标对照，非同站差值'})
    base['noncomparable_metrics']=[{'metric':'water_peak_cm','reason':'现有预报岸段代表点不是历史测站，未计算跨站绝对潮位差；即使基准相同，也不据不同站的最高值判断灾害轻重。'}]
    if 'water_peak_cm' in requested:base['missing_information'].append(base['noncomparable_metrics'][0]['reason'])
    if re.search(r'偏重|偏轻|总体|整体|影响(?:是|如何|怎么样)|灾损',question):
        base['missing_information'].append('未核定NC与所问台风的场景归属，且未计算总体灾损；现有峰值仅能逐指标对照。')
    for metric in requested:
        if metric not in base['forecast']['metrics']:
            base['missing_information'].append('当前海况预报没有'+METRIC_LABELS.get(metric,metric)+'计算结果。')
    if base['missing_information'] or coastal['missing_count'] or coastal['partial_count']:base['state']='partial'
    if not base['forecast']['metrics']:base.update(state='partial',reason='已取得历史事件指标，所选时段未取得可计算岸段海况。')
    return base


METRIC_LABELS={'surge_peak_cm':'最大风暴增水','water_peak_cm':'最高潮位','direct_loss_yi':'直接经济损失','deaths_missing':'死亡含失踪人数','deaths':'死亡人数','wave_peak_m':'最大有效波高'}


def comparison_facts(result):
    facts=[{'kind':'comparison_scope','citation':1,'forecast_benchmark':result.get('comparison_mode')=='forecast_regional_peak_benchmark',
            'scope':(result.get('requested_scope') or {}).get('label','浙江'),
            'missing_information':result.get('missing_information',[])}]
    for event in result.get('historical',[]):
        for key,metric in event['metrics'].items():
            if key not in METRIC_LABELS:continue
            label=METRIC_LABELS[key]
            if metric.get('coverage')=='parsed_report_station_subset':label='已解析测站记录的'+label+'（非区域全事件最大值）'
            facts.append({'kind':'historical_metric','metric':label,'value':metric['value'],'unit':metric['unit'],
                          'location':_event_label(event),'event_name':event['name'],'season':event['season'],
                          'station':metric.get('station'),'region':metric.get('region',event['region']),
                          'jurisdiction':metric.get('jurisdiction'),
                          'time_text':metric.get('observed_time_text'),'datum':metric.get('datum',event.get('datum')),
                          'source_title':metric.get('source_title',event['source_title']),'locator':metric.get('locator'),
                          'scope':metric.get('scope','所引事件公报口径'),'citation':1})
    forecast=result.get('forecast') or {}
    for key,metric in forecast.get('metrics',{}).items():
        facts.append({'kind':'computed','metric':'预报'+METRIC_LABELS[key],'value':metric['value'],'unit':metric['unit'],
                      'location':metric['location'],'time':metric['time'],'batch':metric['batch'],'scope':metric['scope'],'citation':1})
    for comparison in result.get('comparisons',[]):
        if comparison['metric'] not in METRIC_LABELS:continue
        facts.append({'kind':'comparison','metric':str(comparison.get('station') or '')+METRIC_LABELS[comparison['metric']],
            **{key:comparison[key] for key in ('left','right','left_value','right_value','difference','unit')},
            'station':comparison.get('station'),'scope':comparison['statistical_scope'],'citation':1})
    if forecast:
        facts.append({'kind':'coverage',**{key:forecast.get(key,0) for key in ('requested_count','usable_count','missing_count','partial_count')},'citation':1})
    return facts


def comparison_summary(result):
    view=deepcopy(result)
    # Legacy event maxima were populated from only partially parsed reports.
    # Keep those records in the audited tool result, but do not present them
    # to the language model as competing province-wide peak measurements.
    for event in view.get('historical',[]):
        event['alternative_reports']=[{'source_title':report.get('source_title'),
            'source_sha256':report.get('source_sha256'),'excluded_from_comparison':True,
            'reason':'旧报告字段尚未核为区域全事件峰值；本次主指标仅取明确年度公报事件段落。'}
            for report in event.get('alternative_reports',[])]
    return ('历史灾害指标对照的程序结果。历史指标保留原文、出处和统计范围；预报不是实测，区域峰值对照不是同站差值。'
            '不能从中心风速替代浙江风暴潮影响，也不计算总体灾损轻重。\n'+json.dumps(view,ensure_ascii=False,separators=(',',':')))
