"""Connect objective field products to the existing, server-owned QA session."""
from datetime import timedelta
import hashlib
import re


def _artifacts(result):
    values=result.get('artifacts') or []
    if isinstance(values,dict):values=list(values.values())
    output=[]
    allowed={'report.html','report.json','series.csv','wave_peak.png','wind_peak.png','pressure_peak.png',
             'wave_maximum.png','wind_maximum.png'}
    for value in values:
        if not isinstance(value,dict) or value.get('filename') not in allowed:continue
        url=str(value.get('url') or '')
        if not re.fullmatch(r'/api/objective/reports/[a-f0-9]{32}/'+re.escape(value['filename']),url):continue
        output.append(dict(value))
    return output


def _artifact_text(artifacts, *, markdown=False):
    names={'report.html':'完整HTML报告','report.json':'完整数据JSON','series.csv':'原生时次CSV',
           'wave_peak.png':'波高峰值时刻空间图','wind_peak.png':'风速峰值时刻空间图','pressure_peak.png':'海平面气压空间图',
           'wave_maximum.png':'所选时段逐格最大波高图','wind_maximum.png':'所选时段逐格最大风速图'}
    if not artifacts:return ''
    lines=['**图件与报告下载**' if markdown else '程序已生成的图件和报告下载文件（实际系统产物）：']
    for value in artifacts:
        label=names[value['filename']]
        lines.append(f'- [{label}]({value["url"]})' if markdown else
                     f'{label}；文件名：{value["filename"]}；下载链接：{value["url"]}；SHA256：{value.get("sha256") or "未登记"}。')
    return '\n'.join(lines)


def _requested_fields(question):
    q=str(question or '').split('本轮问题：')[-1]
    fields=[]
    if re.search(r'海浪|波高|浪高|涌浪|风浪|浪.{0,3}(?:最高|最大|更高|更大)',q):fields.append('wave')
    if re.search(r'风速|风场|海面风|海上风|10米风|风浪|换成风|改成风',q):fields.append('wind')
    if re.search(r'气压',q):fields.append('pressure')
    return fields or ['wave','wind','pressure']


def _maximum_query(question):
    return bool(re.search(r'逐格(?:最大(?:值)?)?(?:图|分布)|逐格最大(?:值)?|时段最大(?:值)?(?:图|分布)|(?:\d+\s*小时|[一二两三四五六七]+天).{0,10}最大值分布|最大值模式：逐格最大值图',str(question or '')))


def _field_query(question):
    if _maximum_query(question):return False
    raw=question.split('本轮问题：')[-1]
    if re.search(r'报告|专报|简报|分析|逐格|全过程|过程统计|分区|北部|中部|南部|未来\s*[\d一二两三四五六七八九十]+\s*(?:小时|天)',raw):return False
    return bool(re.search(r'地图查询|二维|分布|绘制|显示|风场|(?:浪|风|气压).{0,4}图',question) or
                re.search(r'\d{1,2}(?:[时点]|:\d{2})',raw) and re.search(r'波高|浪高|海浪|海面风|风速|气压',raw))


def _fields(question, dataset=None):
    marker=re.search(r'地图要素：(海浪|海面风|气压)',question)
    if marker:return [{'海浪':'wave','海面风':'wind','气压':'pressure'}[marker[1]]]
    fields=_requested_fields(question)
    if fields==['wave','wind','pressure'] and not re.search(r'气压',question):
        return list((dataset or {}).get('available_fields') or (dataset or {}).get('fields') or ['wave','wind'])
    return fields


def _map_action(dataset,field,valid,bounds,state='ready'):
    return {'type':'objective_field','dataset_id':dataset['dataset_id'],'field':field,
            'valid_time':valid,'bounds':list(bounds),'field_state':state,
            'source_family':dataset.get('source_family','ecmwf'),**({'level':dataset['level']} if dataset.get('level') else {}),
            'available_fields':list(dataset.get('fields') or {}),
            'title':{'wave':'海浪分布','wind':'海面风分布','pressure':'气压分布'}[field]}


def _maximum_action(dataset,field,start,hours,bounds,state='ready'):
    action=_map_action(dataset,field,None,bounds,state)
    action.pop('valid_time',None)
    action.update(type='objective_maximum',start=start.isoformat(),hours=hours,
                  title={'wave':'所选时段逐格最大波高分布','wind':'所选时段逐格最大风速分布',
                         'pressure':'所选时段逐格最大气压分布'}[field])
    return action


def _field_product(engine,dataset,question,start,now,bounds,scope=None):
    fields=_fields(question,dataset)
    explicit=bool(re.search(r'地图查询|20\d{2}[-年/]\d|\d{1,2}月\d{1,2}日|今天|今日|明天|明日|后天|\d{1,2}[时点]|\d{1,2}:\d{2}',question))
    stamps=sorted(set(t for name in fields for t in dataset.get('fields',{}).get(name,{}).get('valid_times',[])))
    if not stamps:stamps=sorted(set(t for value in dataset.get('fields',{}).values() for t in value.get('valid_times',[])))
    from .objective_sources import stamp
    valid=start.isoformat() if explicit else next((t for t in stamps if stamp(t)>=start),start.isoformat())
    frames=[]
    for name in fields:
        try:frame=engine.frame(dataset_id=dataset['dataset_id'],field=name,valid_time=valid,bounds=bounds,maxsize=80,
                              sea_mask='wave_valid' if name=='wind' and 'wave' in dataset['fields'] else None)
        except ValueError:
            frame={'state':'no_data','field':name,'code':'native_time_or_bounds_unavailable','stats':{},'valid_time':valid}
        frames.append(frame)
    scope=scope or {}
    lines=[f'**{scope.get("label","浙江近海")}二维预报图**\n\n数据源：{dataset.get("label",dataset["dataset_id"])}；起报：{dataset["batch"]}；所选有效时刻：{valid}（北京时间）[资料1]。',
           f'查询范围：东经{bounds[0]}—{bounds[2]}°，北纬{bounds[1]}—{bounds[3]}°；数值来自NC原生网格，非实况。']
    lines.extend(scope.get('limitations') or [])
    spatial_partial=False
    for frame in frames:
        name=frame['field'];label={'wave':'海浪波高','wind':'海面风速（暂按10米阵风解释）' if dataset.get('source_family')=='zjocean_l2_l5' else '10米风速','pressure':'海平面气压'}[name]
        if name not in dataset['fields']:
            lines.append(f'{label}：当前L2/L5数据源未提供该要素，未借用其他批次或ECMWF数据[资料1]。');continue
        coverage=dataset['fields'][name].get('coverage')
        if coverage and not (coverage[0]<=bounds[0] and coverage[1]<=bounds[1] and coverage[2]>=bounds[2] and coverage[3]>=bounds[3]):
            spatial_partial=True
            lines.append(f'{label}的源网格仅覆盖{coverage}（西、南、东、北）；本次仅统计交集，未代表所问范围全域[资料1]。')
        peak=frame.get('stats',{}).get('minimum' if name=='pressure' else 'maximum')
        if not peak:
            lines.append(f'{label}：该批次在所选时刻或范围没有可用原生数据，未插值、未替换时刻[资料1]。');continue
        unit=frame.get('unit','')
        lines.append(f'{label}在查询范围内的'+('最低' if name=='pressure' else '最大')+f'值为 **{peak["value"]} {unit}**，位于东经{peak["lon"]}°、北纬{peak["lat"]}°，发生于{peak["time"]}；有效海点{frame["stats"]["valid_count"]}个[资料1]。')
    if not explicit and valid!=start.isoformat():lines.append(f'本次选取查询时刻之后的首个原生预报时次{valid}，未将其标作当前实况。')
    if dataset.get('source_family')=='zjocean_l2_l5':lines.append('WAV暂作海浪波高；u10/v10合成风速暂作10米阵风。MWD浪向未核定，本次不绘制浪向；L2/L5均约0.05°网格，L5覆盖范围更大。')
    sources=[s for f in frames for s in f.get('source_files',[])]
    evidence='\n\n'.join(lines)+'\n\n'+'\n'.join('来源文件：'+s['filename']+'；SHA256：'+s['sha256'] for s in sources)
    ready=not spatial_partial and all(f.get('state')=='ready' for f in frames)
    first=frames[0];field=fields[0]
    field_state='not_provided' if field not in dataset['fields'] else 'partial' if spatial_partial and first.get('state')=='ready' else first.get('state','no_data')
    return {'kind':'objective_field','state':'ready' if ready else 'partial' if any(f.get('state')=='ready' for f in frames) else 'no_data',
            'dataset_id':dataset['dataset_id'],'source_family':dataset.get('source_family'),'level':dataset.get('level'),
            'start':valid,'end':(stamp(valid)+timedelta(hours=1)).isoformat(),'hours':1,'valid_time':valid,
            'view_kind':'field','field':field,'bounds':list(bounds),'scope':scope,'as_of':now.isoformat(),'batch':dataset['batch'],
            'available_fields':list(dataset['fields']),'spatial_partial':spatial_partial,'frames':[{k:v for k,v in f.items() if k not in {'values','u','v','lat','lon'}} for f in frames],
            'summary':'\n\n'.join(lines),'evidence':evidence,'artifacts':[],
            'map_action':_map_action(dataset,field,valid,bounds,field_state)}


def _region_delivery(result,question):
    """Keep each requested object's real regional value/time/position tuple."""
    from .objective_report import FIELDS,_display,_extreme,_point
    regions=result.get('regions') or []
    if not regions:return ''
    wanted=_fields(question,result)
    definitions=result.get('field_info') or FIELDS
    lines=['**分区具体数据**','以下为本次报告的临时经纬度统计框，非行政海域；时刻均为北京时间。',
           '| 分区 | 指标 | 极值及单位 | 发生时刻 | 极值网格位置 |','|---|---|---|---|---|']
    by_field={name:[] for name in wanted}
    for region in regions:
        for name in wanted:
            field=(region.get('fields') or {}).get(name) or {}
            info=definitions.get(name) or FIELDS[name]
            peak=_extreme(field,info['extreme'])
            value=peak.get('value')
            stamp=peak.get('time') or peak.get('valid_time')
            if value is None:
                lines.append(f'| {region["name"]} | {info["metric"]} | 未取得，未按0处理 | — | — |')
                continue
            display_stamp=str(stamp or '未取得').replace('T',' ').replace('+08:00','')
            lines.append(f'| {region["name"]} | {info["metric"]} | {value} {field.get("unit", "单位未核定")} | {display_stamp} | {_point(peak)} |')
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                by_field[name].append((value,region['name'],field.get('unit','单位未核定'),stamp,_point(peak)))
    for name,values in by_field.items():
        if not values:continue
        info=definitions.get(name) or FIELDS[name]
        extreme=(min if info['extreme']=='minimum' else max)(v[0] for v in values)
        winners=[v for v in values if v[0]==extreme]
        choice='最低' if info['extreme']=='minimum' else '最高'
        detail='；'.join(f'{v[1]}为{v[0]} {v[2]}，发生于{v[3]}，{v[4]}' for v in winners)
        lines.append(f'已取得的{len(values)}个分区中，{info["metric"]}{choice}的是：{detail}。')
    lines.append('以上是同一批次、同一查询窗口的空间极值比较；不按峰值直接推定当地灾损或活动安全。')
    return '\n\n'.join(lines[:2])+ '\n\n'+'\n'.join(lines[2:])


def _objective_context(result):
    from marine_kb.objective_context import product_context
    if result.get('state') not in {'ready','partial'} and result.get('view_kind')!='field':return None
    return product_context({key:result.get(key) for key in ('dataset_id','start','end','hours','view_kind','field','valid_time','bounds','scope') if result.get(key) is not None})


def _language_problem(organized,artifacts):
    meta=organized.get('answer_meta') or {}
    if meta.get('used_mode')=='model_error' or not str(organized.get('answer') or '').strip():return 'model_unavailable'
    text=str(organized.get('answer') or '')
    if artifacts and re.search(r'(?:未(?:提供|给出|生成|取得)|无法(?:提供|下载)|没有|缺少|尚缺).{0,26}(?:图件|图像|图片|报告|下载链接|下载方式|文件名)|'
                               r'(?:图件|报告|下载链接|文件名).{0,20}(?:未给出|未提供|缺失|未取得)|'
                               r'(?:图件|报告|下载链接|文件名)[^。；\n]{0,80}未在.{0,12}(?:提供|给出)',text):
        return 'contradicts_generated_artifacts'
    phase=meta.get('phase2') or {}
    if meta.get('missing_information') or meta.get('answerability') in {'partial','insufficient','missing'} or phase.get('render_mode')=='with_gaps':
        return 'incomplete_interpretation_not_appended'
    if phase.get('unresolved_final_review') or (phase.get('judge_consistency') or {}).get('unresolved'):
        return 'unresolved_interpretation_review'
    if any(d.get('drop_rule')=='typed_grounding_unknown_facet' and d.get('status')=='supported' for d in phase.get('decisions') or []):
        return 'supported_scientific_content_lost_during_binding'
    return None


def objective_question(question):
    q=str(question or '')
    fields=bool(re.search(r'海浪|波高|浪高|海面风|海上风|风速|风场|气压|风浪|海况',q))
    product=bool(re.search(r'报告|专报|简报|分析|图件|分布图|二维|逐格|过程统计',q))
    dated=bool(re.search(r'今天|今日|明天|明日|后天|未来|今后|接下来|随后|20\d{2}[-年/]\d|\d{1,2}月\d{1,2}日|\d{1,2}[时点]',q))
    explicit=bool(re.search(r'客观预报|ECMWF|L[25].{0,10}(?:风|浪|图)|二维.{0,8}(?:风|浪|气压)|(?:风场|海面风|海上风|风速|海浪|波高|浪高|气压|风浪).{0,4}(?:图|产品|分布)|影响(?:研判)?(?:报告|专报)',q,re.I))
    explicit |= fields and product
    explicit |= fields and dated and bool(re.search(r'多大|多高|多少|最大|最低|范围|变化|预报|怎么样|如何|趋势|过程|超过',q))
    # A concept, activity decision or warning inventory remains its own
    # business route. Explicit product + other intent is handled as mixed by
    # product_question_plan rather than silently answering only the NC half.
    if re.search(r'什么是|是什么意思|定义|原理|区别|怎么计算|如何计算|如何制作',q) and not re.search(r'(?:并|另外|同时|再).{0,12}(?:生成|绘制|给我).{0,18}(?:报告|图|简报)',q):return False
    if re.search(r'赶海|游泳|海钓|岸钓|船钓|停航|预警|警报|应急响应',q) and not product:return False
    return bool(explicit and re.search(r'浙江|舟山|朱家尖|宁波|台州|温州|嘉兴|近海|海域|地图范围|客观预报|ECMWF|二维',q,re.I))


def _time_text(question):
    """Normalise bounded Chinese durations and relative dates, not numbers in data."""
    names={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7}
    return re.sub(r'([一二两三四五六七])\s*(天|日|小时)',lambda m:str(names[m[1]])+('天' if m[2]=='日' else m[2]),str(question or ''))


def _duration_hours(question,default=72):
    q=_time_text(question)
    duration=re.search(r'(\d{1,3})\s*(小时|天)',q)
    hours=int(duration[1])*(24 if duration[2]=='天' else 1) if duration else default
    if not 1<=hours<=168:raise ValueError('客观预报报告支持1至168小时')
    return hours


def _relative_dates(question,now):
    offsets={'今天':0,'今日':0,'明天':1,'明日':1,'后天':2,'昨天':-1,'昨日':-1}
    return re.sub('|'.join(offsets),lambda m:(now+timedelta(days=offsets[m[0]])).strftime('%Y年%m月%d日'),question)


def _explicit_interval(question,now):
    from .service import period,_same_day_clock_range
    q=_relative_dates(_time_text(question),now)
    pattern=r'(?:(20\d{2})[-年/])?(\d{1,2})[-月/](\d{1,2})日?(?:[T\s]*(\d{1,2})(?:[:时点](\d{1,2})?分?))?'
    dates=list(re.finditer(pattern,q))
    paired=any(re.fullmatch(r'\s*(?:至|到|~|～|—|–|-)\s*',q[a.end():b.start()]) for a,b in zip(dates,dates[1:]))
    first,last=period(q,now=now,hours=_duration_hours(q))
    if not paired:
        same_day=_same_day_clock_range(q,first)
        if not same_day:return None
        first,last=same_day
    delta=(last-first).total_seconds()/3600
    if not delta.is_integer() or not 1<=delta<=168:raise ValueError('报告起止区间需为1至168个完整小时；请明确起止时刻。')
    return first,int(delta)


def requested_window(question,now):
    from .service import period
    # period() uses inclusive hourly samples for the older point service. The
    # product contract is a continuous interval, exactly start + N hours.
    question=_time_text(question)
    interval=_explicit_interval(question,now)
    if interval:return interval
    hours=_duration_hours(question)
    parsed=_relative_dates(question,now)
    start,_=period(parsed,hours=hours,now=now)
    local_clock=re.search(r'(?:今天|今日|明天|明日|后天|从)\s*(\d{1,2})(?:[:：](\d{2})|[时点](?:(\d{1,2})分)?)',question)
    if local_clock:start=start.replace(hour=int(local_clock[1]),minute=int(local_clock[2] or local_clock[3] or 0),second=0,microsecond=0)
    if re.search(r'未来|今后|接下来|随后',question) and not re.search(r'20\d{2}[-年/]\d|\d{1,2}月\d{1,2}日|今天|今日|明天|明日|后天|\d{1,2}[时点]',question):
        start=now.replace(second=0,microsecond=0)
    return start,hours


def _thresholds(question):
    """Only explicit numeric thresholds; no implied warning/safety standard."""
    result={}
    for name,pattern in (
        ('wave',r'(?:浪高|波高|海浪)(?:\s*)?(?:超过|高于|大于|≥|>=|>)\s*(\d+(?:\.\d+)?)\s*(?:米|m)(?!/|每秒|秒)'),
        ('wind',r'(?:风速|海面风速|海面风)(?:\s*)?(?:超过|高于|大于|≥|>=|>)\s*(\d+(?:\.\d+)?)\s*(?:米每秒|米/秒|m/s)')):
        match=re.search(pattern,str(question or ''),re.I)
        if match:
            comparison='>=' if re.search(r'≥|>=',match[0]) else '>'
            result[name]={'value':float(match[1]),'comparison':comparison,'label':'用户明确筛查阈值'}
    return result or None


def query_objective(runtime,question):
    engine=getattr(runtime,'objective',None)
    reports=getattr(runtime,'objective_reports',None)
    if engine is None or reports is None:
        return {'kind':'objective_report','state':'no_data','summary':'客观预报数据尚未登记，请先导入NC文件。','artifacts':[]}
    from .storage import local_now
    now=getattr(runtime._local,'request_now',None) or local_now()
    start,hours=requested_window(question,now)
    match=re.search(r'(?:数据集|dataset)(?:ID|编号)?\s*[:：=]?\s*([A-Za-z0-9_-]{8,96})',question,re.I)
    identifier=match[1] if match else None
    sources=getattr(runtime,'objective_sources',None)
    acquisition=None
    is_field=_field_query(question)
    from .objective_scope import resolve_scope
    scope=resolve_scope(question)
    bounds=scope['bounds']
    datasets=engine.catalogue().get('datasets',[]) if hasattr(engine,'catalogue') else []
    dataset=next((d for d in datasets if d['dataset_id']==identifier),None) if identifier else None
    if sources and not identifier:
        try:acquisition=sources.ensure(start,start+timedelta(hours=1 if is_field else hours))
        except Exception as exc:acquisition={'state':'unavailable','error_type':type(exc).__name__}
        level_match=re.search(r'\bL([25])\b',question,re.I)
        dataset=sources.select(start,start+timedelta(hours=0 if is_field else hours),fields=_fields(question),bounds=bounds,
            as_of=now,level='L'+level_match[1] if level_match else None,latest=bool(re.search('最新批次',question)))
        if not dataset:return {'kind':'objective_report','state':'no_data','summary':f'尚未取得符合{scope["label"]}、查询日期、批次和产品的L2/L5风浪数据；未借用ECMWF或未来起报批次。','artifacts':[],'data_acquisition':acquisition,'scope':scope,'bounds':bounds,'start':start.isoformat(),'end':(start+timedelta(hours=hours)).isoformat()}
        identifier=dataset['dataset_id']
    if is_field and dataset:
        result=_field_product(engine,dataset,question,start,now,bounds,scope)
        result['data_acquisition']=acquisition
        return result
    package=reports.report(question=question,start=start.isoformat(),dataset_id=identifier,hours=hours,
                           requested_fields=_fields(question,dataset),bounds=bounds,scope=scope,thresholds=_thresholds(question))
    result=dict(package.get('objective_result') or {})
    result.update(kind='objective_report',summary=package.get('summary') or result.get('summary',''),
                  evidence=package.get('evidence') or result.get('evidence',''),
                  report_id=package.get('report_id'),artifacts=package.get('artifacts') or [],
                  state=package.get('state') or result.get('state','no_data'))
    result.setdefault('start',start.isoformat());result.setdefault('end',(start+timedelta(hours=hours)).isoformat())
    result.setdefault('as_of',now.isoformat())
    result.setdefault('scope',scope);result.setdefault('bounds',bounds)
    result['requested_question']=question
    result['data_acquisition']=acquisition
    if dataset:
        field=_fields(question,dataset)[0]
        peak=(result.get('fields',{}).get(field) or {}).get('minimum' if field=='pressure' else 'maximum') or {}
        valid=peak.get('time') or peak.get('valid_time')
        if not valid:
            valid=next((t for f in dataset['fields'].values() for t in f['valid_times'] if t>=start.isoformat()),start.isoformat())
        field_state='not_provided' if field not in dataset['fields'] else 'ready' if peak else 'no_data'
        if _maximum_query(question):
            result.update(view_kind='maximum',field=field)
            result['map_action']=_maximum_action(dataset,field,start,hours,bounds,field_state)
        else:
            result['map_action']=_map_action(dataset,field,valid,bounds,field_state)
    return result


def answer_objective(runtime,question,qa,meta,allow_web):
    original=meta.get('current_turn_question') or question
    # The canonical follow-up carries server-owned dataset/window input slots;
    # a short current-turn wording must never erase them before re-reading NC.
    canonical='本轮问题：' in question or meta.get('normalizer')=='objective_input_slots'
    query=question if canonical else original if objective_question(original) else question
    try:result=query_objective(runtime,query)
    except (ValueError,KeyError,OSError) as exc:
        result={'kind':'objective_report','state':'no_data','summary':str(exc)[:300],'artifacts':[]}
    body=result.get('evidence') or result.get('summary') or '本次未取得客观预报数据。'
    artifacts=_artifacts(result)
    artifact_evidence=_artifact_text(artifacts)
    if artifact_evidence:body+='\n\n'+artifact_evidence
    identity=hashlib.sha256(body.encode()).hexdigest()[:24]
    runtime.store.state('evidence:'+identity,result)
    hit={'source_id':'forecast:'+identity,'chunk_id':identity,'title':'浙江客观预报区域统计与产品',
         'filename':'客观预报数据产品','category':'客观预报','status':'provisional','text':body,
         'locator':str(result.get('start',''))+'至'+str(result.get('end','')),
         'metadata':{'source_type':'business_forecast','external_allowed':True,'trial':True,
                     'assumption_id':runtime.config_id(),'retrieved_at':result.get('as_of'),
                     'objective_forecast':True},'retrieval':'objective_nc_calculation'}
    current={**meta,'route':'objective_forecast','business_query':'objective_report',
             'business_state':result['state'],'business_trial':True,'business_evidence_id':identity,
             'objective_artifacts':artifacts,'objective_dataset_id':result.get('dataset_id'),
             'objective_report_id':result.get('report_id'),'tool_data_view':True,
             'map_action':result.get('map_action'),
             'objective_context':_objective_context(result),'as_of':result.get('as_of') or meta.get('as_of'),
             'answerability':'full' if result['state']=='ready' else 'partial',
             'business_instruction':('资料1是本次NC原生网格的程序计算和产品。数值图表已由程序展示，'
                 '语言部分集中解释空间分布、时间变化和预报影响，不复述整张数值表。'
                 '按资料1的产品定义解释：ECMWF的10u/10v是10米风分量；L2/L5的u10/v10暂按10米阵风合成，WAV暂作波高；L2/L5没有气压。'
                 '只以资料1的实际来源、批次、层级、时段、分区与有效海点解释，不混用ECMWF、L2/L5、SWL或天文潮口径。'
                 '临时分区是明确经纬度统计框，非行政海域界线。预报数值不称实况，'
                 '路径相似度不用于推定灾损。规则和预警知识可使用另外检索取得的正文，'
                 '官方已发布预警与这份客观预报报告分开。资料不足时只保留对应缺口。'
                 '资料1末尾列出程序实际生成的文件名和下载链接，这些文件已存在；不要将它们判为未提供。'
                 '程序已完整展示数值和分区表；本次补充只解释这些结果与限制，不再次索取已有数据或报告。')}
    summary=result.get('summary') or body
    table=_region_delivery(result,original)
    downloads=_artifact_text(artifacts,markdown=True)
    program_answer='\n\n'.join(part for part in (summary,table,downloads) if part)
    sources=qa._public_sources(question,[hit])
    response={'answer':program_answer,'rag_sources':sources,'web_sources':[],
              'answer_meta':{**current,'used_mode':'business_data','generation_used':False}}
    if qa.external_enabled and qa.model_caller and result['state'] in {'ready','partial'} and result.get('kind')!='objective_field':
        # Data and download tasks are already served by the program above.
        # One bounded interpretation facet avoids relabelling each numerical
        # product as a definition or requesting a download procedure again.
        fields='、'.join({'wave':'有效波高','wind':'10米风速','pressure':'海平面气压'}[name] for name in _requested_fields(original))
        interpretation=f'依据本次客观预报程序结果，解释{fields}在参考统计框和所选时段内的变化、对照及适用限制。'
        language_meta=dict(current)
        for key in ('facets','facet_contract','crag_evidence_plan','evidence_coverage','web_query_subquestions'):
            language_meta.pop(key,None)
        language_meta.update(normalized_question=interpretation,
                             subquestions=[{'id':1,'question':interpretation,'kind':'explanation'}],
                             objective_program_question=query)
        try:
            hits=qa.joint_evidence(question,current,allow_web,pinned=[hit]) if getattr(qa,'crag_enabled',False) else [hit]
            organized=qa._organized_answer(interpretation,qa.model_name,hits,language_meta)
            if not isinstance(organized,dict):raise TypeError('objective_interpretation_response_type')
        except Exception as exc:
            organized={'answer':'','answer_meta':{'used_mode':'model_error','objective_language_error_type':type(exc).__name__}}
        problem=_language_problem(organized,artifacts)
        diagnostics=organized.get('answer_meta') or {}
        if problem is None:
            explanation=organized.get('answer','')
            response=organized
            response['answer']=program_answer+('\n\n**资料解读**\n\n'+explanation if explanation else '')
            response['answer_meta']={**diagnostics,**current,'generation_used':True,
                                     'used_mode':diagnostics.get('used_mode','llm'),
                                     'objective_language_state':'appended_grounded_interpretation'}
        else:
            response['answer_meta'].update({key:value for key,value in diagnostics.items()
                                           if key not in current and key not in {'answerability','missing_information','used_mode','generation_used'}})
            response['answer_meta'].update(objective_language_state=problem,language_state='program_delivery_preserved',
                                          objective_language_missing=diagnostics.get('missing_information') or [],
                                          objective_language_answerability=diagnostics.get('answerability'),
                                          objective_language_attempted=True)
    response['answer_meta']['objective_delivery_verified']=True
    return response
