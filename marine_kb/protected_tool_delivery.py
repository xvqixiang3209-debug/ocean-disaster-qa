"""Preserve exact typed tool records, with the same source and immutable facets.

This is a data handoff, not another model judgement. Only server-generated
forecast evidence whose identity, body hash and exact fact text match can
create a protected atom. A model cannot grant itself this provenance.
"""
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone


def _scope_datetime(value):
    try:
        instant=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        zone=timezone(timedelta(hours=8))
        return instant.replace(tzinfo=zone) if instant.tzinfo is None else instant.astimezone(zone)
    except (TypeError,ValueError):return None


def _storm_mentions(text):
    """Explicit names/IDs only; generic centre and activity nouns are not names."""
    text=str(text or '')
    ids=set(re.findall(r'(?:接口标识|typhoonID|台风编号|风暴编号|编号)\s*[:：]?\s*(\d{4,8})(?!\d|年)',text,re.I))
    ids.update(re.findall(r'(?:台风|热带气旋)\s*[#第]?\s*(\d{4,8})(?!\d|年)',text))
    ids.update(re.findall(r'(?<!\d)(\d{4,8})(?:号)?台风',text))
    names=set(re.findall(r'(?:台风|热带气旋)\s*[“「\"]([^”」\"\n]{1,24})[”」\"]',text))
    generic=r'数量|数目|总数|活动|最新|当前|最大|最小|最高|最低|最强|最弱|中心|位置|强度|风力|风速|气压|移动|名称|身份|预警|路径|是否|怎么|资料|记录|清单|信息|接口|有|在|的'
    pattern=r'(?:台风|热带气旋)\s*('+r'(?!(?:'+generic+r'))[\u4e00-\u9fffA-Za-z]{2,16}?)(?=的|最新|位置|中心|强度|风速|移动|记录|编号|20\d{2}年|[,，。；;？?、和与]|$)'
    names.update(re.findall(pattern,text))
    return {name.strip() for name in names if name.strip()},ids


def _identity_scope_matches(fact,text):
    names,ids=_storm_mentions(text)
    if ids and str(fact.get('storm_id') or '') not in ids:return False
    if names:
        name=str(fact.get('storm_name') or '').strip()
        if not name or not any(name.casefold()==asked.casefold() for asked in names):return False
    return True


def _explicit_time_scopes(text,anchor):
    """Resolve written dates/clocks against a fixed request scope, never now()."""
    text=str(text or '')
    spans=[];taken=[]
    clock_tail=r'(?:[T\s]*(\d{1,2})(?:[:：时点](\d{1,2}))?分?)?'
    patterns=[('full',re.compile(r'(?<!\d)(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})[日号]?'+clock_tail)),
              ('md',re.compile(r'(?<!\d)(\d{1,2})月(\d{1,2})[日号]'+clock_tail)),
              ('day',re.compile(r'(?<!\d)(\d{1,2})[日号]'+clock_tail))]
    for kind,pattern in patterns:
        for match in pattern.finditer(text):
            if any(lo<=match.start()<hi for lo,hi in taken):continue
            groups=match.groups()
            if kind=='full':year,month,day,hour,minute=groups
            elif anchor is None:return None
            elif kind=='md':month,day,hour,minute=groups;year=anchor.year
            else:day,hour,minute=groups;year=anchor.year;month=anchor.month
            try:value=datetime(int(year),int(month),int(day),int(hour or 0),int(minute or 0),tzinfo=timezone(timedelta(hours=8)))
            except (TypeError,ValueError):return None
            clock=hour is not None
            start=value.replace(second=0,microsecond=0) if clock else value.replace(hour=0,minute=0,second=0,microsecond=0)
            end=start+timedelta(minutes=1)-timedelta(microseconds=1) if clock else start+timedelta(days=1)-timedelta(microseconds=1)
            spans.append((start,end));taken.append(match.span())
    if spans:return spans
    relative=next(((token,offset) for token,offset in [('前天',-2),('昨天',-1),('今日',0),('今天',0),('明天',1),('后天',2)] if token in text),None)
    if relative:
        if anchor is None:return None
        start=(anchor+timedelta(days=relative[1])).replace(hour=0,minute=0,second=0,microsecond=0)
        spans=[(start,start+timedelta(days=1)-timedelta(microseconds=1))]
    clock=re.search(r'(?<!\d)(\d{1,2})(?:[:：](\d{2})|[时点](?:(\d{1,2})分)?)',text)
    if clock:
        if anchor is None:return None
        try:start=(spans[0][0] if spans else anchor).replace(hour=int(clock[1]),minute=int(clock[2] or clock[3] or 0),second=0,microsecond=0)
        except ValueError:return None
        return [(start,start+timedelta(minutes=1)-timedelta(microseconds=1))]
    return spans


def _time_scope_matches(fact,text,facet,*,facet_text=False):
    scope=facet.get('time') or {}
    instant=_scope_datetime(fact.get('as_of') or fact.get('requested_as_of'))
    anchor=_scope_datetime(scope.get('as_of')) or instant
    start=_scope_datetime(scope.get('start'));end=_scope_datetime(scope.get('end'))
    if instant is None:
        return not (scope or re.search(r'20\d{2}|\d{1,2}月\d{1,2}日|昨天|今天|明天|前天|后天|\d+[时点]',str(text)))
    # Relative dates in the original facet have already been resolved once.
    relative_tokens=('前天','昨天','今天','今日','明天','后天')
    original=str(facet.get('question') or facet.get('text') or '')
    shared_relative=next((token for token in relative_tokens if token in str(text) and token in original),None)
    if shared_relative and start and end:
        offsets={'前天':-2,'昨天':-1,'今天':0,'今日':0,'明天':1,'后天':2}
        anchor=start-timedelta(days=offsets[shared_relative])
    spans=_explicit_time_scopes(text,anchor)
    if spans is None:return False
    if len(spans)>1:
        # A comparison over distinct instants requires separate tool tasks;
        # one record cannot silently satisfy both sides of an unsplit facet.
        return False
    if spans and not any(lo<=instant<=hi for lo,hi in spans):return False
    if facet_text and start and end:
        # "现在/最新" contracts can carry a microsecond instant. Requiring the
        # later planner clock to equal the earlier acquisition clock is wrong.
        if not start.date()<=instant.date()<=end.date():return False
    return True


def _fact_scope_matches(fact,text,facet,*,facet_text=False):
    return _identity_scope_matches(fact,text) and _time_scope_matches(fact,text,facet,facet_text=facet_text)


def fact_hash(fact):
    return hashlib.sha256(json.dumps(fact, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _owners(fact, facets):
    from forecast_runtime.answer_facts import typhoon_requested_fields
    field = fact.get('field')
    owners = []
    for f in facets:
        if f.get('domain') != 'typhoon_dynamics':
            continue
        question = str(f.get('question') or f.get('text') or '')
        wanted = typhoon_requested_fields(question)
        # A present centre observation cannot answer a future peak/causal claim.
        if re.search(r'最强|峰值|增强|加强|会不会|未来|预报|预计|登陆', question) and not re.search(r'现在|当前|最新|目前', question):
            continue
        if not _fact_scope_matches(fact,question,f,facet_text=True):continue
        if field in wanted or field=='availability' or (field=='identity' and wanted-{'count','snapshot_time','query_time'}):
            owners.append(str(f.get('fid') or f.get('id')))
    return owners


def verified_tool_atoms(meta, evidence, facets):
    """Return proof-bound atoms and an extended exact-original passage view."""
    from forecast_runtime.answer_facts import fact_text
    from .evidence_units import digest, doc_card_sha256
    identity = str(meta.get('business_evidence_id') or '')
    report = {'source_id': 'forecast:' + identity, 'accepted': [], 'rejected': []}
    view = copy.deepcopy(evidence)
    if not re.fullmatch(r'[a-f0-9]{24}', identity):
        return [], view, report
    selected = [e for e in view if (e.get('doc_card') or {}).get('source_id') == report['source_id']]
    if len(selected) != 1:
        return [], view, report
    source = selected[0]
    body = source.get('text')
    citation = source.get('citation')
    if (source.get('evidence_view_version') != 2 or not isinstance(body, str)
            or type(citation) is not int or citation < 1
            or source.get('metadata', {}).get('source_type') != 'business_forecast'
            or digest(body) != source.get('body_sha256')
            or source.get('doc_card', {}).get('body_sha256') != digest(body)):
        report['rejected'].append({'rule': 'source_identity_or_body_hash_invalid'})
        return [], view, report
    atoms = []
    for fact in meta.get('required_business_facts') or []:
        if not str(fact.get('kind', '')).startswith('typhoon_') or 'field' not in fact:
            continue
        owners = _owners(fact, facets)
        if not owners:
            continue
        line = fact_text([fact])
        start = body.find(line) if line else -1
        if start < 0:
            report['rejected'].append({'field': fact.get('field'), 'rule': 'exact_tool_fact_not_in_source'})
            continue
        end = start + len(line)
        matches = [p for p in source.get('passages', []) if p.get('id', 0) > 0
                   and p.get('start') is not None and p['start'] <= start and p.get('end', -1) >= end
                   and digest(p.get('text', '')) == p.get('sha256')
                   and body[p['start']:p['end']] == p.get('text')]
        if not matches:
            # A retrieval selector may not erase a tool field. Append its exact
            # original span; all pre-existing citation and passage IDs stay put.
            pid = max((p.get('id', 0) for p in source['passages']), default=0) + 1
            passage = {'id': pid, 'passage_id': pid, 'citation': citation, 'text': line,
                       'start': start, 'end': end, 'sha256': digest(line),
                       'section_path': [], 'kind': 'original_tool_record'}
            source['passages'].append(passage)
        else:
            passage = matches[0]
        proof = {'citation': citation, 'passage_id': passage['id'], 'quote': line}
        value = copy.deepcopy(fact)
        key = fact_hash(value)
        is_gap = fact.get('status') == 'missing'
        atom = {'aid': 'tool_' + key[:20], 'text': line + f'[资料{citation}]',
                'display_text': line + f'[资料{citation}]', 'type': 'gap' if is_gap else 'calculation',
                'origin': 'verified_tool_record', 'tool_fact': value, 'tool_fact_hash': key,
                'facet_ids': owners, 'evidence_quotes': [proof],
                'gap_type': 'business_data_missing' if is_gap else None,
                'context_subject': [str(fact.get('location') or '接口活动台风列表')]}
        atoms.append(atom)
        report['accepted'].append({'aid': atom['aid'], 'field': fact['field'],
                                   'status': fact.get('status'), 'facet_ids': owners,
                                   'source_passage_id': passage['id'], 'fact_sha256': key})
    source['view_sha256'] = digest(doc_card_sha256(source['doc_card']) + '\n' + '\n'.join(
        str(p['id']) + ':' + p['sha256'] for p in source['passages']))
    return atoms, view, report


def merge_tool_atoms(result, tool_atoms, facets):
    """Freeze tool output and replace model data prose for those same facets.

    Model explanations and other facets survive. Numeric dynamic clauses are
    rendered from the exact tool records, preventing two contradictory counts
    or positions from coexisting after a model independently rewrites them.
    """
    from .facet_delivery import evaluate_delivery, publishable, render_with_gaps
    if not tool_atoms:
        return result
    targets = {fid for a in tool_atoms if a.get('type') != 'gap' for fid in a['facet_ids']}
    fields_by_fid = {fid: {a['tool_fact']['field'] for a in tool_atoms
                          if fid in a['facet_ids'] and a.get('type') != 'gap'} for fid in targets}
    field_markers = {'count': r'(?:\d+\s*个.{0,8}台风|台风.{0,12}\d+\s*个|活动.{0,8}(?:列表|数量|总数)|该数量|上述数量)',
                     'position': r'中心位置|经度|纬度|东经|西经|北纬|南纬|经纬度',
                     'intensity': r'强度|风力|风速|m/s|米/秒', 'pressure': r'气压|hPa|百帕',
                     'direction': r'移动方向|向.{0,5}移动|WNW|NNW|ENE|WSW|西北偏西|西偏西北',
                     'speed': r'移动速度|移速|km/h|公里/小时'}
    accepted, replaced = [], []
    for original in result.get('accepted_atoms', []):
        if original.get('origin') == 'verified_tool_record':
            continue  # Rebuild only from the proof-checked server atom set.
        atom = copy.deepcopy(original)
        own = targets.intersection(atom.get('facet_ids') or [])
        text = str(atom.get('text') or '')
        mentioned = {field for field, pattern in field_markers.items() if re.search(pattern, text, re.I)}
        superseded = {fid for fid in own if mentioned & fields_by_fid[fid]}
        if superseded and atom.get('type') != 'gap':
            atom['facet_ids'] = [fid for fid in atom['facet_ids'] if fid not in superseded]
            replaced.append({'aid': atom['aid'], 'facet_ids': sorted(superseded), 'rule': 'render_from_exact_tool_fields'})
            if not atom['facet_ids']:
                continue
        accepted.append(atom)
    accepted = list(tool_atoms) + accepted
    ids = {a['aid'] for a in accepted}
    # A removed prose premise cannot remain behind as an unsupported inference.
    while True:
        filtered = [a for a in accepted if not a.get('premises') or set(a['premises']) <= ids]
        new_ids = {a['aid'] for a in filtered}
        if new_ids == ids:
            break
        accepted, ids = filtered, new_ids
    rows = evaluate_delivery(facets, accepted)
    return {**result, 'accepted_atoms': accepted, 'delivery': rows,
            'publishable': publishable(facets, rows),
            'answer': render_with_gaps(facets, accepted, rows=rows),
            'tool_field_replacements': list(result.get('tool_field_replacements') or [])+replaced}


def resolve_tool_gaps(missing, facets, atoms):
    """Clear only contradicted availability gaps for fully delivered fields.

    Unit definitions, permission, forecasts and other objects' gaps stay. This
    cannot turn a partial/stale source into a complete live observation.
    """
    from .facet_delivery import evaluate_delivery
    protected = re.compile(r'单位.{0,12}(?:核定|定义|含义)|高程|基准|SWL|WAV|MWD|NC|许可|开放|保险|资格|保单|疫情|全球|全世界|因果|预报|预测|未来', re.I)
    markers = {'count': r'几个|数量|总数|完整.{0,8}列表|全量.{0,8}列表',
               'identity': r'名称|名字|身份|哪些台风', 'position': r'位置|经纬|中心在哪',
               'intensity': r'强度|风速|风力', 'pressure': r'气压',
               'direction': r'方向', 'speed': r'移动速度|移速',
               'snapshot_time':r'(?:取得|获取|快照).{0,8}(?:时间|时刻|几点)',
               'record_time':r'(?:记录|位置).{0,8}(?:时间|时刻|几点)',
               'query_time':r'查询.{0,6}(?:时间|时刻|几点)'}
    tool = [a for a in atoms if a.get('origin') == 'verified_tool_record' and a.get('type') == 'calculation'
            and a.get('tool_fact', {}).get('status') == 'ready']
    complete = {r['fid'] for r in evaluate_delivery(facets, tool) if r['status'] == 'full'}
    kept, resolved = [], []
    for item in missing:
        text = str(item.get('text', '')) if isinstance(item, dict) else str(item)
        fields = {field for field, pattern in markers.items() if re.search(pattern, text)}
        if (protected.search(text) or not fields
                or not re.search(r'缺|未提供|未取得|未获取|没有|无法查询|尚未取得', text)):
            kept.append(item); continue
        owners = []
        declared=[]
        if isinstance(item,dict):
            declared=list(item.get('facet_ids') or [])
            one=item.get('fid') or item.get('facet_id')
            if one is not None:declared.append(str(one))
        for f in facets:
            fid = str(f.get('fid') or f.get('id'))
            if declared and fid not in {str(value) for value in declared}:continue
            own = [a for a in tool if fid in a.get('facet_ids', [])
                   and _fact_scope_matches(a['tool_fact'],text,f)]
            available = {a['tool_fact']['field'] for a in own}
            if fid in complete and fields <= available:
                owners.append(fid)
        if owners:
            resolved.append({'text': text, 'facet_ids': owners, 'rule': 'exact_ready_tool_fields_already_delivered'})
        else:
            kept.append(item)
    return {'kept': kept, 'resolved': resolved}


def tool_display_atoms(atoms, facets):
    """Remove exact duplicate tool rows only at the final display boundary.

    A model row can have inherited an unrelated parent's object on several
    facets. The verified card already prints that same source/value/time once.
    Do not let its incorrect extra bindings repeat it under other questions.
    This does not change grounding, recovery prompts or completeness decisions.
    """
    from .business_validation import _validate_typhoon_fact
    protected=[a for a in atoms if a.get('origin')=='verified_tool_record']
    facts=[a for a in protected if a.get('tool_fact',{}).get('kind')=='typhoon_field']
    domains={str(f.get('fid') or f.get('id')):f.get('domain') for f in facets}
    kept=[];audit=[]
    for original in atoms:
        if original.get('origin')=='verified_tool_record':continue
        atom=copy.deepcopy(original)
        text=str(atom.get('text') or '')
        cells=[c.strip() for c in text.strip().strip('|').split('|')]
        matched=[]
        if atom.get('table_header') and text.strip().startswith('|') and len(cells)==4:
            for tool in facts:
                fact=tool['tool_fact'];ref=tool['evidence_quotes'][0]['citation']
                if cells[0]!=fact.get('metric') or f'[资料{ref}]' not in text:continue
                same_source=[a for a in facts if a['evidence_quotes'][0]['citation']==ref]
                names,ids=_storm_mentions(text)
                if not names and not ids and len({a['tool_fact'].get('storm_id') for a in same_source})!=1:continue
                if not _identity_scope_matches(fact,text):continue
                # A name-free row is unambiguous only in a one-storm source.
                # The validator also binds value, unit, record time and caveats.
                checked=text+' '+str(fact.get('storm_id') or fact.get('location') or '')
                try:_validate_typhoon_fact(checked,{**fact,'citation':ref})
                except ValueError:continue
                matched.append(tool['aid'])
        if matched:
            removed=[fid for fid in atom.get('facet_ids',[]) if domains.get(fid)=='typhoon_dynamics']
            atom['facet_ids']=[fid for fid in atom.get('facet_ids',[]) if fid not in removed]
            audit.append({'aid':atom['aid'],'facet_ids':removed,'replacement_aids':matched,
                          'rule':'exact_tool_row_already_in_data_card'})
            if not atom['facet_ids']:continue
        # One explanation with several legitimate owners needs one display;
        # its full ownership remains unchanged in the grounding/delivery audit.
        if len(atom.get('facet_ids',[]))>1 and all(domains.get(fid)=='typhoon_dynamics' for fid in atom['facet_ids']):
            audit.append({'aid':atom['aid'],'facet_ids':atom['facet_ids'][1:],
                          'rule':'shared_explanation_display_once'})
            atom['facet_ids']=atom['facet_ids'][:1]
        kept.append(atom)
    return kept,audit


def tool_data_card(atoms):
    """Compact display of the exact proof-bound records, independent of prose.

    Time/scope are printed once per response snapshot. Values, units and all
    missing/stale qualifications survive; no new numerical operation is done.
    """
    from datetime import datetime,timezone,timedelta
    def stamp(value):
        try:
            dt=datetime.fromisoformat(str(value))
            if dt.tzinfo:dt=dt.astimezone(timezone(timedelta(hours=8)))
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError,TypeError):return str(value or '未提供')
    def cell(value):return str(value).replace('|','／').replace('\n',' ')
    records=[];seen=set()
    for a in atoms:
        if a.get('origin')!='verified_tool_record' or a['aid'] in seen:continue
        seen.add(a['aid']);records.append((a,a['tool_fact']))
    if not records:return ''
    groups={}
    for atom,fact in records:
        key=(fact.get('as_of'),fact.get('retrieved_at'),bool(fact.get('historical_snapshot')))
        groups.setdefault(key,[]).append((atom,fact))
    cards=[]
    for (as_of,retrieved,historical),rows in groups.items():
        lines=['**接口数据**',f'查询截至：{stamp(as_of)}；接口快照取得：{stamp(retrieved)}（北京时间）。']
        for atom,f in rows:
            ref=atom['evidence_quotes'][0]['citation']
            if f['kind']=='typhoon_count':
                label='历史快照的接口完整活动列表' if historical else '接口完整活动列表'
                lines.append(f"**截至{stamp(as_of)}，{label}有{f['value']}个台风。**[资料{ref}]")
            elif f['kind']=='typhoon_selected_count':
                lines.append(f"本次选中{f['value']}个台风记录；该子集数量不代表全部活动数量。[资料{ref}]")
            elif f['kind']=='typhoon_scope':
                lines.append(f"完整活动台风数量未知；已取得{f.get('known_records',0)}个记录，不能据此推定全部数量或0个。[资料{ref}]")
            elif f['kind']=='typhoon_availability':
                text=(f'截至{stamp(as_of)}，活动台风数量0个，完整活动列表为空，没有中心位置、强度或移动方向可列。'
                      if f.get('value')=='no_active_typhoons' else f.get('reason')or'尚未取得可用台风动态。')
                lines.append(text+f'[资料{ref}]')
        data=[];gaps=[];assumed=set();stale=False
        field_records={f.get('storm_id') for _,f in rows if f['kind'] in {'typhoon_field','typhoon_field_gap'}}
        for atom,f in rows:
            ref=atom['evidence_quotes'][0]['citation'];kind=f['kind']
            if kind=='typhoon_field':
                value=f['value']
                if isinstance(value,dict):
                    value=('东经' if value['lon']>=0 else '西经')+str(abs(value['lon']))+'°，'+('北纬' if value['lat']>=0 else '南纬')+str(abs(value['lat']))+'°'
                elif f.get('unit'):value=str(value)+' '+f['unit']
                if f.get('unit_assumed'):
                    assumed.add(f['metric']);value=str(value)+'（单位尚未核定）'
                if f.get('stale') or f.get('cache_stale'):value=str(value)+'（记录滞后，仅代表历史记录）'
                data.append([f.get('location')or'接口记录',f['metric'],str(value)+f' [资料{ref}]',stamp(f.get('record_time'))])
                stale=stale or bool(f.get('stale')or f.get('cache_stale'))
            elif kind=='typhoon_identity' and f.get('storm_id') not in field_records:
                data.append([f.get('location')or f.get('value'),'台风身份',str(f.get('value'))+f' [资料{ref}]',stamp(f.get('record_time'))])
            elif kind=='typhoon_timing':
                data.append([f.get('location')or'本次接口查询',f['metric'],stamp(f.get('value'))+f' [资料{ref}]',stamp(f.get('record_time')) if f.get('record_time') else '—'])
            elif kind=='typhoon_field_gap':
                gaps.append(str(f.get('location')or'接口记录')+'：'+str(f.get('metric'))+'未提供，不按0处理'+f'[资料{ref}]。')
        if data:
            lines+=['| 台风或查询对象 | 字段 | 接口记录值 | 记录时刻 |','|---|---|---|---|']
            lines+=['| '+' | '.join(cell(v) for v in row)+' |' for row in data]
        if assumed:lines.append('单位说明：'+ '、'.join(sorted(assumed))+'按现有接口解释展示，单位尚待业务方核定。')
        if historical:lines.append('这是截至所问时刻之前已保存的历史快照；没有用今天的接口数据回填。')
        if stale:lines.append('部分记录或缓存滞后；表中保留原记录时刻，不能据此认定为查询时点的实时状态。')
        lines+=gaps
        lines.append('数量仅指上述接口列表；中心记录与海况NC格网分别查询。')
        cards.append('\n\n'.join(lines[:2])+'\n\n'+'\n'.join(lines[2:]))
    return '\n\n'.join(cards)
