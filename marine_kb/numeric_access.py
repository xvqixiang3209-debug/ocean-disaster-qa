"""Read-only access to original workbook cells and coast-version comparisons."""
from pathlib import Path
import re
from urllib.parse import urlencode


FILE = re.compile(r'(?:Surge|Waterlevel)_(?:detem|ensem)_TY\d+_\d{10}\.xlsx', re.I)
TIME = re.compile(r'(?<!\d)(\d{1,2}/\d{1,2}:\d{2})(?!\d)')


def archive_path(store):
    return Path(store.database).parent / 'numeric_archive.sqlite3'


def open_archive(store):
    from .numeric_archive import NumericArchive
    path = archive_path(store)
    if not path.is_file():
        raise FileNotFoundError('数值归档尚未建立')
    return NumericArchive(path, read_only=True)


def matches(question):
    # An exact historical workbook is intentionally distinct from the latest
    # NC or a broad future-forecast query. Do not steal either route.
    return bool(FILE.search(str(question)))


def _unique(values):
    return list(dict.fromkeys(values))


def _mentions(text, labels):
    """Retain separate short labels, suppressing only overlapping longer names."""
    matches=[]
    for label in labels:
        if not label:continue
        matches.extend((m.start(),m.end(),label) for m in re.finditer(re.escape(label),text))
    selected=[]
    for item in sorted(matches,key=lambda v:(-(v[1]-v[0]),v[0])):
        if not any(item[0]<other[1] and other[0]<item[1] for other in selected):selected.append(item)
    return sorted(selected)


def _pairs(text, labels):
    places=_mentions(text,labels);times=list(TIME.finditer(text))
    unique_places=_unique(p[2] for p in places);unique_times=_unique(t[1] for t in times)
    if not unique_places or not unique_times:return [],'missing'
    if len(unique_places)==1:return [(unique_places[0],token) for token in unique_times],None
    if len(unique_times)==1:return [(place,unique_times[0]) for place in unique_places],None
    # Multiple clocks and places need an explicit local binding. Each place
    # followed by one clock before the next place is a bounded statement.
    pairs=[];used=set()
    for index,place in enumerate(places):
        end=places[index+1][0] if index+1<len(places) else len(text)
        selected=[t for t in times if place[1]<=t.start()<end]
        if len(selected)!=1:return [],'ambiguous_pairing'
        pairs.append((place[2],selected[0][1]));used.add(selected[0].start())
    return (_unique(pairs),None) if len(used)==len(times) else ([], 'ambiguous_pairing')


def _unprocessed(question):
    output=[]
    for part in re.split(r'[？?；;\n]|(?:另外|同时|还请|以及|并且|并请)',FILE.sub('【历史模式表】',question)):
        part=part.strip(' ，,。')
        if not part:continue
        if re.search(r'\bNC\b|网格|二维|海浪图|风场图|风浪图|气压图',part,re.I):
            output.append('NC或地图子问尚未处理：'+part[:200])
        elif re.search(r'(?:实时|当前|今天|今日|最新|发布|清单|有效).{0,18}预警|预警.{0,18}(?:清单|覆盖|有效|发布)',part):
            output.append('实时预警子问尚未处理：'+part[:200])
    return _unique(output)


def _cell(value):
    return str(value).replace('|','\\|').replace('\n',' ').replace('\r',' ')


def answer(store, question):
    question=str(question)
    matches=list(FILE.finditer(question))
    if not matches:return None
    filenames=_unique(m[0] for m in matches)
    unprocessed=_unprocessed(question)
    meta={'route':'numeric_archive','used_mode':'structured_local','numeric_archive_verified':True,
          'answerability':'partial','numeric_archive_files_requested':filenames,
          'unprocessed_subquestions':unprocessed}
    try:archive=open_archive(store)
    except FileNotFoundError:
        text='这些历史模式表尚未进入数值归档：'+'、'.join(filenames)+'。请先完成数值恢复。'
        if unprocessed:text+='\n以下额外子问尚未处理：\n'+'\n'.join('- '+item for item in unprocessed)
        return {'answer':text,
                'rag_sources':[],'web_sources':[],'answer_meta':{**meta,'answerability':'insufficient',
                    'numeric_archive_rows':0,'missing_information':['历史模式表数值归档尚未建立',*unprocessed]}}
    records={};all_labels=[]
    with archive.connect() as db:
        for filename in filenames:
            files=db.execute('SELECT filename,file_id FROM numeric_files WHERE lower(filename)=lower(?)',(filename,)).fetchall()
            if not files:
                records[filename]={'exists':False,'labels':[]};continue
            labels=[r[0] for r in db.execute('''SELECT DISTINCT c.label FROM numeric_columns c
                JOIN numeric_sheets s USING(sheet_id) JOIN numeric_files f USING(file_id)
                WHERE lower(f.filename)=lower(?)''',(filename,))]
            records[filename]={'exists':True,'file':files[0]['filename'],'labels':labels}
            all_labels.extend(labels)
    all_labels=_unique(all_labels)
    common_pairs,common_error=_pairs(question,all_labels)
    mentions=_mentions(question,all_labels)
    common_places=_unique(p[2] for p in mentions)
    common_times=_unique(m[1] for m in TIME.finditer(question))
    shared_all=bool(re.search(r'(?:每(?:一)?份|两份|两个|各(?:份|表)|所有(?:文件|表)).{0,10}(?:均|都|同样)',question))
    collective_ambiguous=(len(filenames)>1 and len(common_times)>1 and bool(mentions)
                          and mentions[0][0]>=matches[-1].end() and not shared_all)
    queries=[];clarifications=[];gaps=[]
    for index,match in enumerate(matches):
        requested=match[0];record=records[requested]
        if not record['exists']:
            gaps.append('未登记文件：'+requested);continue
        if collective_ambiguous:
            clarifications.append(requested+'：多份文件与多个时间的对应关系不明确，请按“文件名—地点列—原始时间标签”逐项写明。')
            continue
        local=question[match.end():matches[index+1].start() if index+1<len(matches) else len(question)]
        pairs,error=_pairs(local,all_labels)
        if error=='missing':
            # Sharing one clock or one place is unambiguous. Multiple files
            # with several clocks and places must have their own local pairs.
            if shared_all or len(common_places)==1 and len(common_times)==1 or len(matches)==1:
                pairs,error=common_pairs,common_error
            elif len(common_times)==1:
                pairs,error=common_pairs,common_error
        if error:
            clarifications.append(requested+'：'+('多个地点与时间的对应关系不明确，请按“文件名—地点列—原始时间标签”逐项写明。' if error=='ambiguous_pairing' or len(common_places)>1 and len(common_times)>1 else '请指定表内地点列和原始时间标签，例如“大戢山，05/15:00”。'))
            continue
        for place,token in pairs:
            key=(record['file'],place,token)
            if key not in queries:queries.append(key)
    rows=[];file_rows={};request_results=[]
    for filename,place,token in queries:
        found=archive.query(file=filename,station=place,time_token=token,limit=100)
        request_results.append({'file':filename,'station':place,'time_token':token,'matched':found['total']})
        if not found['items']:
            gaps.append(f'{filename}：没有匹配“{place}、{token}”的单元格记录，不能按0回答。')
        if found['total']>len(found['items']):gaps.append(f'{filename}：匹配{found["total"]}个单元格，本次仅列前{len(found["items"])}个，请缩小范围。')
        for row in found['items']:
            key=(row['file_id'],row['sheet'],row['cell'])
            if not any((r['file_id'],r['sheet'],r['cell'])==key for r in rows):
                rows.append(row);file_rows.setdefault(filename,[]).append(row)
    sources=[];citations={}
    for filename,group in file_rows.items():
        first=group[0];citation=len(sources)+1;citations[filename]=citation
        links=[{'station':r['station'],'time_token':r['time_raw'],'sheet':r['sheet'],'cell':r['cell']}for r in group]
        sources.append({'citation_id':citation,'citation':f'[资料{citation}]','source_id':first.get('source_id'),
            'title':filename,'locator':'；'.join(_unique(f"{r['sheet']}!{r['cell']}" for r in group)),
            'url':'/api/knowledge/numeric/query?'+urlencode({'file':filename,'station':first['station'],'time_token':first['time_raw']}),
            'source_type':'local_numeric_archive','review_label':'原始单元格已恢复，业务口径待核',
            'metadata':{'source_sha256':first['sha256'],'unit_status':first['unit_status'],
                        'time_status':first['time_status'],'vertical_datum':first['vertical_datum'],'supporting_cells':links}})
    text='**历史模式表原值查询**\n\n'
    if rows:
        text+='| 文件 | 地点列 | 原始时间标签 | 单元格 | 原值 | 单位 | 来源 |\n|---|---|---|---|---:|---|---|\n'
        for row in rows:
            value=(str(row['value'])+('（公式缓存值）' if row['quality']=='formula_cached' else '')
                   if row['quality'] in {'valid','formula_cached'} else '缺测/无效（'+row['quality']+'）')
            cells=[row['filename'],row['station'],row['time_raw'],row['sheet']+'!'+row['cell'],value,row.get('unit') or 'unknown',f'[资料{citations[row["filename"]]}]']
            text+='| '+' | '.join(_cell(v) for v in cells)+' |\n'
        text+='\n以上逐项来自各自原工作簿；单位、高程基准、完整年月和时区尚未核定，不能把文件名当有效日期、不能直接改称厘米或总潮位，也未进行超警判定。'
        if re.search(r'比较|相比|相差|差值|差多少|哪个.{0,8}(?:大|高)|变化',question):
            text+='不同表先并列原值；未核定单位、基准及真实有效时刻，未计算带业务含义的差值或轻重结论。'
        gaps.append('单位、高程基准及完整有效时间尚未核定')
    elif not clarifications:
        text+='未取得匹配的原始单元格数值；没有记录或缺测不等于0。'
    if clarifications:
        text+='\n\n需明确的查询项：\n'+'\n'.join('- '+item for item in _unique(clarifications))
        text+='\n原始时间缺少年月和时区时，不能凭文件名自动补齐。'
    if gaps:
        text+='\n\n尚缺或未取得：\n'+'\n'.join('- '+item for item in _unique(gaps))
    if unprocessed:
        text+='\n\n本轮仅处理上述历史模式表原值，以下子问尚未处理，不能视为整题已完整交付：\n'+'\n'.join('- '+item for item in unprocessed)
    meta.update(numeric_archive_rows=len(rows),numeric_archive_requests=request_results,
                missing_information=_unique([*gaps,*clarifications,*unprocessed]),
                used_mode='needs_clarification' if clarifications and not rows else 'structured_local',
                answerability='partial' if rows or clarifications else 'insufficient',
                business_state='partial' if rows else 'needs_clarification' if clarifications else 'no_data')
    return {'answer':text,'rag_sources':sources,'web_sources':[],'answer_meta':meta}
