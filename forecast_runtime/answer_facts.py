"""Bounded typed facts from actual tool outputs, never inferred from row IDs."""
import math
import re
from datetime import datetime, timezone, timedelta

PRODUCTS={'WAV':('有效波高','米'),'SSW':('阵风','m/s'),'SWL':('SWL水位分量','米'),
          'OCU':('格点流速','m/s'),'SST':('水温','℃'),'MWD':('平均波向原始角度','°')}

def _finite(value):
    return type(value) in {int,float} and math.isfinite(value)


def typhoon_requested_fields(question):
    question=str(question or '')
    requested=set()
    if re.search(r'几个(?:正在)?(?:活动)?(?:的)?台风|多少(?:个)?(?:正在)?(?:活动)?(?:的)?台风|'
                 r'(?:活动台风|台风|活动列表|活动清单)(?:的)?(?:数量|总数|数目)|'
                 r'(?:活动台风|台风)(?:共有|一共|总共|共|有)?(?:多少个?|几个)(?:[？?。，,、]|分别|$)|'
                 r'有哪些(?:正在)?(?:活动)?(?:的)?台风|哪些台风|全部活动台风',question):requested.add('count')
    if re.search(r'(?:取得|获取|抓取|下载|快照).{0,8}(?:时间|时刻|几点)|(?:什么时候|何时|几点).{0,8}(?:取得|获取|抓取|快照)',question):requested.add('snapshot_time')
    if re.search(r'查询.{0,6}(?:时间|时刻|几点)|(?:时间|时刻).{0,6}(?:本次|此次)查询',question):requested.add('query_time')
    if re.search(r'(?:记录|位置)[^，,。；;？?、和与及]{0,8}(?:时间|时刻|几点|什么时候)',question):requested.add('record_time')
    timing_only=bool(requested & {'snapshot_time','record_time','query_time'})
    location_text=re.sub(r'(?:位置)?记录(?:是|的)?(?:时间|时刻|几点)|位置(?:记录|更新|对应的|对应|的|是)?(?:时间|时刻|几点)','',question) if timing_only else question
    if re.search(r'位置|经纬|中心在哪|在哪里|哪儿',location_text):requested.add('position')
    if re.search(r'强度|风速|风力|最大风|多强|几级',question):requested.add('intensity')
    if re.search(r'气压',question):requested.add('pressure')
    if re.search(r'方向|往哪|向哪',question):requested.add('direction')
    if re.search(r'移速|移动速度|速度多|速度是|移动.{0,4}(?:快|慢)|时速',question):requested.add('speed')
    if re.search(r'哪些|名称|名字|什么台风|哪个台风|叫什么|叫啥',question):requested.add('identity')
    if not requested and question:requested={'identity','position','intensity','direction'}
    if requested-{'count','snapshot_time','query_time'}:requested.add('identity')
    return requested


def _typhoon_requested(result):
    explicit=result.get('requested_fields')
    if isinstance(explicit,(list,tuple,set)):
        return set(explicit)&{'count','identity','position','intensity','pressure','direction','speed','snapshot_time','record_time','query_time'}
    return typhoon_requested_fields(result.get('request_question') or result.get('query_question') or result.get('question'))


def _record_age(time,as_of):
    try:
        before=datetime.fromisoformat(str(time));after=datetime.fromisoformat(str(as_of))
        zone=timezone(timedelta(hours=8))
        if before.tzinfo is None:before=before.replace(tzinfo=zone)
        if after.tzinfo is None:after=after.replace(tzinfo=zone)
        return max(0,(after-before).total_seconds()/3600)
    except (ValueError,TypeError):return None


def typhoon_delivery_facts(result):
    """Typed fields from an activity response, never inferred from NC or prose.

    The original activity-list count is independent of a named-storm filter.
    A verified empty list is zero; unavailable/partial lists remain unknown.
    Each emitted field has enough scope for deterministic delivery and checks.
    """
    requested=_typhoon_requested(result)
    as_of=result.get('requested_as_of') or result.get('as_of')
    common={'citation':1,'required':True,'as_of':as_of,'requested_as_of':as_of,
            'observed_at':None,'record_time':None,'status':'ready','retrieved_at':result.get('retrieved_at'),
            'historical_snapshot':bool(result.get('historical_snapshot')),
            'query_time_mode':result.get('query_time_mode'),'cache_stale':bool(result.get('cache_stale')),
            'selection_scope':result.get('selection_scope','all_active')}
    rows=result.get('list')
    rows=rows if isinstance(rows,list) else []
    healthy=result.get('state') in {'ready','no_active_typhoons'} and result.get('query_time_mode')!='future'
    declared=result.get('activity_list_count')
    verified=(healthy and result.get('activity_list_count_verified') is True and
              result.get('snapshot_scope')=='complete_api_response' and
              type(declared) is int and declared>=len(rows))
    if not verified and healthy and result.get('selection_scope','all_active')=='all_active':
        verified=(result.get('activity_count_verified') is True and
                  result.get('snapshot_scope')=='complete_api_response' and isinstance(result.get('list'),list))
        if verified:declared=len(rows)
    facts=[]
    for timing,label,value in [('snapshot_time','接口取得时刻',common['retrieved_at']),('query_time','本次查询截至时刻',as_of)]:
        if timing in requested:
            facts.append({**common,'kind':'typhoon_timing','field':timing,'metric':label,'value':value,
                          'status':'ready' if value else 'missing'})
    if 'count' in requested:
        if verified:
            stale_count=common['cache_stale'] or (common['historical_snapshot'] and common['query_time_mode']!='historical')
            facts.append({**common,'kind':'typhoon_count','field':'count','metric':'接口完整活动列表数量',
                          'value':declared,'unit':'个','count_scope':'complete_api_response',
                          'stale':stale_count,'status':'stale' if stale_count else 'ready'})
        else:
            facts.append({**common,'kind':'typhoon_scope','field':'count','metric':'完整活动台风数量',
                          'known_records':len(rows),'count_scope':'unverified','status':'missing',
                          'reason':'未取得目标时刻完整的活动列表，不能把缺失数据或所选台风记录推定为全部数量'})
    if not rows and requested-{'count','snapshot_time','query_time'}:
        empty_all=verified and declared==0
        facts.append({**common,'kind':'typhoon_availability','field':'availability','metric':'台风记录可用状态',
                      'value':'no_active_typhoons' if empty_all else 'unknown',
                      'count':0 if empty_all else None,
                      'status':'ready' if empty_all else 'missing',
                      'count_scope':'complete_api_response' if empty_all else 'unverified'})
    if common['selection_scope']=='named_subset' and requested-{'count','snapshot_time','query_time'}:
        facts.append({**common,'kind':'typhoon_selected_count','field':'identity','metric':'所选台风记录数',
                      'value':len(rows),'unit':'个','count_scope':'named_subset'})
    field_specs=(('position','position','中心位置',None),('intensity','maxSpeed','中心最大风速','m/s'),
                 ('intensity','windScale','风力等级','级'),('intensity','intensity','接口强度标记',None),
                 ('pressure','pressure','中心最低气压','hPa'),('direction','moveDirection','移动方向',None),
                 ('speed','moveSpeed','移动速度','km/h'))
    for storm in rows if requested-{'count','snapshot_time','query_time'} else []:
        points=[p for p in storm.get('points') or [] if isinstance(p,dict) and p.get('time')]
        latest=max(points,key=lambda p:str(p['time'])) if points else {}
        name=str(storm.get('nameCN') or storm.get('nameEN') or '名称未提供')
        identity=str(storm.get('typhoonID') or '')
        label=name+('（接口标识'+identity+'）' if identity else '')
        age=_record_age(latest.get('time'),as_of)
        base={**common,'storm_id':identity,'storm_name':name,'location':label,'time':latest.get('time'),
              'observed_at':latest.get('time'),'record_time':latest.get('time'),
              'age_hours':round(age,1) if age is not None else None,
              'stale':common['cache_stale'] or bool(age is not None and age>6)}
        base['status']='stale' if base['stale'] else 'ready'
        facts.append({**base,'kind':'typhoon_identity','field':'identity','metric':'台风身份','value':label})
        if not latest:
            for field in sorted(requested-{'count','identity','snapshot_time','query_time'}):
                facts.append({**base,'kind':'typhoon_field_gap','field':field,'metric':{
                    'position':'中心位置','intensity':'中心强度','pressure':'中心最低气压',
                    'direction':'移动方向','speed':'移动速度','record_time':'位置记录时刻',
                    'snapshot_time':'接口取得时刻','query_time':'本次查询截至时刻'}[field],'status':'missing','reason':'接口未提供带时刻的位置记录'})
            continue
        if 'record_time' in requested:
            facts.append({**base,'kind':'typhoon_timing','field':'record_time','metric':'位置记录时刻','value':latest.get('time')})
        for request_field,key,metric,unit in field_specs:
            if request_field not in requested and not (key=='pressure' and 'intensity' in requested):continue
            output_field=request_field if request_field in requested else 'intensity'
            if key=='position':
                value={'lon':latest.get('lon'),'lat':latest.get('lat')}
                present=(all(_finite(value.get(k)) for k in ('lon','lat')) and
                         -180<=value['lon']<=180 and -90<=value['lat']<=90)
            else:
                value=latest.get(key)
                present=_finite(value) if key in {'maxSpeed','pressure','moveSpeed','windScale'} else bool(str(value or '').strip())
            # Optional secondary strength labels cannot turn an absent wind into zero.
            if not present and (key in {'windScale','intensity'} or (key=='pressure' and 'pressure' not in requested)):continue
            if not present:
                facts.append({**base,'kind':'typhoon_field_gap','field':output_field,'source_field':key,'metric':metric,'status':'missing',
                              'reason':'该条接口记录未提供此字段'})
                continue
            facts.append({**base,'kind':'typhoon_field','field':output_field,'source_field':key,'metric':metric,'value':value,'unit':unit,
                          'unit_assumed':key in {'maxSpeed','pressure','moveSpeed'} and not result.get(key+'_unit_verified',False)})
    return facts


def _typhoon_time_text(fact):
    if fact.get('field')=='count' or fact['kind']=='typhoon_selected_count':
        text='查询截至时刻：'+str(fact.get('as_of') or '未提供')
    else:text='记录时刻：'+str(fact.get('time') or '未提供')+'；查询截至时刻：'+str(fact.get('as_of') or '未提供')
    text+='；接口取得时间：'+str(fact.get('retrieved_at') or '未提供')
    if fact.get('stale'):
        text+='；记录或缓存可能滞后，仅代表上述记录或快照，不能认定为查询时刻的实时状态'
    elif fact.get('historical_snapshot'):
        text+='；历史原始快照，只代表当时已保存的接口记录'
    return text


def typhoon_fact_text(fact):
    """One self-contained exact source line for field-level preservation."""
    kind= fact['kind'];scope=_typhoon_time_text(fact)
    if kind=='typhoon_count':
        return f"接口完整活动列表有{fact['value']}个台风（活动台风数量）；{scope}；这是接口列表数量，不是未来台风数量预测。"
    if kind=='typhoon_selected_count':
        return f"所选台风记录数为{fact['value']}个；{scope}；所选对象记录数不等于全部活动台风数量。"
    if kind=='typhoon_scope':
        return f"完整活动台风数量未知；已取得{fact.get('known_records',0)}个台风记录；{scope}；仅为部分或所选记录，不能当作全部数量，也不能推定为0个。"
    if kind=='typhoon_timing':
        prefix=str(fact.get('location') or '台风接口')
        return prefix+'，'+fact['metric']+'：'+str(fact.get('value') or '未提供')+'；'+scope+'。'
    if kind=='typhoon_availability':
        if fact['value']=='no_active_typhoons':
            return '接口完整活动列表为空，活动台风数量为0个，因此没有可列示的活动台风位置、强度或移动方向；'+scope+'；该结论仅代表接口列表。'
        return '未取得所问台风的可用接口记录，位置、强度和移动方向未知；'+scope+'；不能据此认定没有活动台风。'
    identity=str(fact.get('location') or fact.get('storm_name') or '所选台风')
    if kind=='typhoon_identity':return identity+'；台风身份来自接口记录；'+scope+'。'
    if kind=='typhoon_field_gap':return identity+'，'+fact['metric']+'未提供；'+scope+'；'+fact['reason']+'，不按0处理。'
    value=fact['value']
    if fact['field']=='position':
        lon=value['lon'];lat=value['lat']
        content=('东经' if lon>=0 else '西经')+str(abs(lon))+'度，'+('北纬' if lat>=0 else '南纬')+str(abs(lat))+'度'
    else:
        directions={'N':'正北','NNE':'北偏东北','NE':'东北','ENE':'东偏东北','E':'正东','ESE':'东偏东南',
                    'SE':'东南','SSE':'南偏东南','S':'正南','SSW':'南偏西南','SW':'西南','WSW':'西偏西南',
                    'W':'正西','WNW':'西偏西北','NW':'西北','NNW':'北偏西北'}
        content=str(value)+(('（'+directions[value]+'）') if fact['field']=='direction' and value in directions else '')
        if fact.get('unit'):content+=' '+fact['unit']
        if fact.get('unit_assumed'):content+='（单位按现有接口解释，尚未核定）'
    return identity+'，'+fact['metric']+'：'+content+'；'+scope+'。'

def delivery_facts(result):
    kind=result.get('kind')
    if kind=='bundle':
        return ([{'kind':'impact_scope','citation':1,'required':True}] if result.get('purpose')=='impact_boundary_inputs' else [])+[
            fact for row in result.get('items',[]) for fact in delivery_facts(row)]
    facts=[]
    if kind=='historical_comparison':
        from .historical_comparison import comparison_facts
        return comparison_facts(result)
    typed_typhoon=kind=='typhoons' and bool(result.get('request_question') or result.get('requested_fields'))
    if typed_typhoon:
        facts=typhoon_delivery_facts(result)
        # Institution forecasts retain the old independently bound rows below.
        if not result.get('requested_forecast'):return facts
    if kind=='typhoons' and result.get('activity_scope_warning_required') and not facts:
        facts.append({'kind':'typhoon_scope','citation':1,'required':True})
    if kind=='typhoons' and result.get('state')=='ready':
        for storm in result.get('list',[]) if typed_typhoon else result.get('list',[])[:3]:
            points=storm.get('points') or []
            if not points:continue
            latest=max(points,key=lambda p:p['time'])
            name=str(storm.get('nameCN') or storm.get('nameEN') or '')+'（接口标识'+str(storm.get('typhoonID'))+'）'
            if _finite(latest.get('maxSpeed')) and not typed_typhoon:
                facts.append({'kind':'computed','metric':'接口中心最大风速（单位按现有假设）','value':latest['maxSpeed'],
                    'unit':'m/s','location':name,'time':latest['time'],'citation':1,
                    'scope':'台风中心接口记录，不代表浙江局地风速','batch':latest['time']})
            if result.get('requested_forecast'):
                for agency,forecast in (latest.get('forecast') or {}).items():
                    valid=[p for p in forecast if _finite(p.get('maxSpeed'))] if isinstance(forecast,list) else []
                    if not valid:continue
                    peak=max(valid,key=lambda p:p['maxSpeed'])
                    facts.append({'kind':'computed','metric':str(agency)+'机构预报中心最大风速峰值（单位按现有假设）',
                        'value':peak['maxSpeed'],'unit':'m/s','location':name,'time':peak['time'],'citation':1,
                        'scope':'该机构预报时段中的最大值，不代表确定加强、登陆或局地灾损','batch':latest['time']})
    if kind in {'activity','grids'}:
        for product in result.get('products',[]):
            token=product.get('product')
            if product.get('state')!='ready' or token not in PRODUCTS:continue
            if token=='MWD':
                location=result.get('location') or {}
                name=location.get('name') or f"{location.get('lon',product.get('lon'))},{location.get('lat',product.get('lat'))}"
                samples=([product['at_wave_peak']] if product.get('at_wave_peak') else [])+product.get('direction_samples',[])
                seen=set()
                for row in samples:
                    if not _finite(row.get('value')) or row['time'] in seen:continue
                    seen.add(row['time'])
                    facts.append({'kind':'computed','metric':row['label']+'平均波向原始角度','value':round(row['value'],2),'unit':'°',
                                  'location':name,'time':row['time'],'batch':product.get('batch'),'citation':1,
                                  'scope':product.get('direction_convention'),'partial':product.get('partial',False)})
                continue
            if kind=='activity':
                values=[product[key]['value'] for key in ('minimum','maximum')
                        if isinstance(product.get(key),dict) and _finite(product[key].get('value'))]
                if values:facts.append({'kind':'marine_product','product':token,'values':[round(value,2) for value in values],
                                       'unit':'℃' if token=='SST' and product.get('unit')=='Celsis D' else product.get('unit'),
                                       'source_unit':product.get('unit'),'unit_assumed':token=='SST' and product.get('unit')=='Celsis D',
                                       'required':token!='SWL','swl_kind':result.get('swl_kind'),
                                       'location':result.get('task_object'),'object_required':bool(result.get('task_object')),
                                       'start':result.get('start'),'end':result.get('end'),'batch':product.get('batch'),'citation':1})
            else:
                location=result.get('location') or {}
                name=location.get('name') or f"{location.get('lon',product.get('lon'))},{location.get('lat',product.get('lat'))}"
                for edge,label in (('minimum','最小'),('maximum','最大')):
                    row=product.get(edge) or {}
                    if _finite(row.get('value')):
                        metric=('风暴增水' if token=='SWL' and result.get('swl_kind')=='surge' else PRODUCTS[token][0])+label
                        facts.append({'kind':'computed','metric':metric,'value':round(row['value'],2),
                                      'unit':'℃' if token=='SST' else product.get('unit') or PRODUCTS[token][1],'location':name,'time':row.get('time'),
                                      'batch':product.get('batch'),'citation':1,'partial':product.get('partial',False)})
    if kind=='coastal':
        facts.append({'kind':'coverage',**{k:result.get(k,0) for k in ('requested_count','usable_count','missing_count','partial_count')},'citation':1})
        rows=[r for r in result.get('results',[]) if r.get('state')=='ready' and _finite((r.get('maximum') or {}).get('total_cm'))]
        if rows:
            highest=max(r['maximum']['total_cm'] for r in rows)
            for r in [r for r in rows if r['maximum']['total_cm']==highest][:5]:
                facts.append({'kind':'computed','metric':'最高总潮位','value':r['maximum']['total_cm'],'unit':'cm',
                              'location':r['name'],'time':r['maximum']['time'],'batch':r.get('batch'),'citation':1,
                              'scope':'岸段代表点试算，非验潮站实测','partial':r.get('partial',False),
                              'required':result.get('requested_metrics',{}).get('total',True)})
        peaks=[(v,r) for r in result.get('results',[]) for v in (r.get('swl_series') or r.get('series',[])) if _finite(v.get('swl_cm'))]
        if peaks:
            highest=max(v['swl_cm'] for v,r in peaks)
            seen=set()
            for v,r in peaks:
                key=(r['name'],v.get('time'))
                if v['swl_cm']!=highest or key in seen:continue
                seen.add(key)
                facts.append({'kind':'computed','metric':'最大风暴增水' if result.get('swl_kind')=='surge' else '最大SWL总水位',
                              'value':v['swl_cm'],'unit':'cm','location':r['name'],'time':v['time'],
                              'batch':r.get('batch'),'citation':1,'scope':'已取得岸段中的最大记录',
                              'required':result.get('requested_metrics',{}).get('surge',True)})
                if len(seen)>=5:break
            if len(seen)>1:
                for f in facts:
                    if f.get('metric') in {'最大风暴增水','最大SWL总水位'}:f['scope']='已取得岸段中的并列最大记录'
    if kind=='analogue' and result.get('state')=='ready' and result.get('analogues'):
        row=result['analogues'][0]
        facts.append({'kind':'analogue','name':row['name'],'season':row['season'],'rank':row.get('rank',1),'citation':1})
    if kind=='tide' and result.get('state')=='ready':
        for edge,label in (('maximum','逐时最高潮位'),('minimum','逐时最低潮位')):
            row=result.get(edge) or {}
            if _finite(row.get('value_cm')):
                facts.append({'kind':'computed','metric':label,'value':row['value_cm'],'unit':'cm',
                              'location':(result.get('site') or {}).get('name',''),'time':row.get('time'),
                              'citation':1,'scope':'天文潮预报，基准按配置假设'})
    if kind=='spatial':
        terrain=result.get('terrain',{})
        if terrain.get('state')=='ready' and terrain.get('elevation_m') is not None:
            facts.append({'kind':'elevation','value':terrain['elevation_m'],'unit':'m','datum':terrain['vertical_datum']})
        population=result.get('population',{})
        if population.get('state')=='ready' and population.get('estimated_population') is not None:
            facts.append({'kind':'population','value':population['estimated_population'],
                          'year':population['year'],'has_nodata':population.get('has_nodata',False)})
    return facts

def fact_text(facts):
    """Preserve value-unit-place-time bindings in the retained source text."""
    lines=[]
    for fact in facts:
        if fact['kind'].startswith('typhoon_') and 'field' in fact:
            lines.append(typhoon_fact_text(fact))
        elif fact['kind']=='comparison_scope':
            lines.append('分项指标对照不代表总体灾损轻重；预报岸段峰值不是历史测站的同站差值，NC台风场景归属尚未核定。' if fact.get('forecast_benchmark') else '仅按相同指标分别比较历史事件，不按一个峰值推定总体灾害轻重或全国全历史排名。')
        elif fact['kind']=='comparison':
            direction='高' if fact['difference']>0 else '低' if fact['difference']<0 else '相同，差值为'
            lines.append(f"{fact['metric']}：{fact['left']}数值{fact['left_value']} {fact['unit']}；{fact['right']}数值{fact['right_value']} {fact['unit']}；前者比后者{direction}{abs(fact['difference'])} {fact['unit']}；{fact.get('scope','所列事件分项对照')}。")
        elif fact['kind']=='historical_metric':
            lines.append(f"{fact['location']}，{fact['region']}，{fact['station'] or '公报所列事件范围'}，{fact['metric']}：{fact['value']} {fact['unit']}；时间：{fact.get('time_text') or str(fact['season'])+'年该事件期间'}；{fact['scope']}；来源：{fact['source_title']} {fact.get('locator') or '已登记历史报告'}。")
        elif fact['kind']=='typhoon_scope':
            lines.append('完整活动台风数量未知：仅保存部分历史台风记录或未保存截至时刻前的完整列表，不能把已取得记录的台风数当作当时全部活动数量。')
        elif fact['kind']=='computed':
            lines.append(f"{fact['location']}，{fact['metric']}：{fact['value']} {fact['unit']}；发生时间：{fact.get('time') or '未提供'}；批次：{fact.get('batch') or '天文潮文件'}；{fact.get('scope','程序查询结果')}。")
        elif fact['kind']=='coverage':
            lines.append(f"岸段覆盖：请求{fact['requested_count']}个，可计算{fact['usable_count']}个，缺数{fact['missing_count']}个，时段不完整{fact['partial_count']}个；缺测不按零处理。")
        elif fact['kind']=='marine_product':
            label,unit=PRODUCTS[fact['product']]
            if fact['product']=='SWL':label='风暴增水（SWL，按当前假设解释）' if fact.get('swl_kind')=='surge' else 'SWL总水位（按当前假设解释）'
            scope=(str(fact['location'])+'，查询日期时段：'+str(fact.get('start'))+'至'+str(fact.get('end'))+'；') if fact.get('object_required') else ''
            lines.append(scope+label+'范围：'+' 至 '.join(str(v)+' '+unit for v in fact['values'])+'；批次：'+str(fact.get('batch') or '未提供')+'；模式预报，变量含义按现有假设解释。')
        elif fact['kind']=='analogue':
            lines.append(f"路径相似排名第{fact['rank']}：{fact['season']}年{fact['name']}；不代表本次灾害损失相同。")
    return '\n'.join(lines)


def fact_table(facts):
    """Deterministic tool data view, independent of the model's prose choices."""
    rows=['| 地点 | 日期或发生时刻 | 指标 | 查询结果 |','|---|---|---|---|']
    extra=[]
    for f in facts:
        if f['kind'].startswith('typhoon_') and 'field' in f:
            extra.append(typhoon_fact_text(f)+'[资料1]')
        elif f['kind']=='impact_scope':
            extra.append('本次仅取得边界参考输入，未计算淹没范围、深度或转移人数；岸段代表点潮位不能代替本地淹没模型结果[资料1]。')
        elif f['kind']=='typhoon_scope':
            extra.append('完整活动台风数量未知：仅有部分历史记录或未保存截至时刻前的完整列表，不能据此统计全部活动数量[资料1]。')
        elif f['kind']=='marine_product':
            metric,unit=PRODUCTS[f['product']]
            if f.get('unit_assumed'):metric+='（单位按现有假设解释）'
            if f['product']=='SWL':metric='风暴增水（假设）' if f.get('swl_kind')=='surge' else 'SWL水位分量（假设）'
            rows.append('| '+str(f.get('location') or '本次查询点')+' | '+str(f.get('start') or '')[:10]+' | '+metric+' | '+
                        ' 至 '.join(str(v)+' '+unit for v in f['values'])+' [资料1] |')
        elif f['kind']=='computed':
            rows.append(f"| {f['location']} | {f.get('time') or '未提供'} | {f['metric']} | {f['value']} {f['unit']} [资料1] |")
        elif f['kind']=='historical_metric':
            rows.append(f"| {f['location']}，{f['region']}，{f.get('station') or '事件统计'} | {f.get('time_text') or str(f['season'])+'年该事件期间'} | {f['metric']} | {f['value']} {f['unit']} [资料1] |")
        elif f['kind']=='comparison':
            direction='高' if f['difference']>0 else '低' if f['difference']<0 else '相同，差值为'
            extra.append(f"{f['metric']}：{f['left']}{f['left_value']} {f['unit']}，{f['right']}{f['right_value']} {f['unit']}；前者比后者{direction}{abs(f['difference'])} {f['unit']}[资料1]。")
        elif f['kind']=='comparison_scope':
            extra.append(('分项指标对照不代表总体灾损轻重；预报岸段峰值不是历史测站同站差值，NC台风场景归属尚未核定。' if f.get('forecast_benchmark') else '仅按相同指标分别比较历史事件，不按一个峰值推定总体灾害轻重或全国全历史排名。')+'[资料1]')
        elif f['kind']=='coverage':
            extra.append(f"岸段覆盖：请求{f['requested_count']}个，可计算{f['usable_count']}个，缺数{f['missing_count']}个，时段不完整{f['partial_count']}个；缺测不按零处理[资料1]。")
        elif f['kind']=='analogue':
            extra.append(f"路径相似排名第{f['rank']}：{f['season']}年{f['name']}，不代表损失相同[资料1]。")
        elif f['kind']=='elevation':extra.append(f"地表高程参考：{f['value']} m；基准{f.get('datum') or '未核定'}[资料1]。")
        elif f['kind']=='population':extra.append(f"{f['year']}年人口格网{'有效格网的估计合计，含缺测格网未按零处理' if f.get('has_nodata') else '估计合计'}：{f['value']}人[资料1]。")
    return '\n'.join((rows if len(rows)>2 else [])+(['']+extra if extra else []))


def delivery_table(result,facts,question=''):
    """A transparent last-resort data view if model prose fails validation."""
    if result.get('kind')=='bundle':
        prefix='本次仅取得边界参考输入，未计算淹没范围、深度或转移人数[资料1]。\n\n' if result.get('purpose')=='impact_boundary_inputs' else ''
        return prefix+'\n\n'.join(('**'+str(row.get('task_object') or '分项查询')+'**\n\n')+
                            delivery_table(row,delivery_facts(row),question) for row in result.get('items',[]))
    if result.get('kind')=='typhoons' and any(f['kind'].startswith('typhoon_') and 'field' in f for f in facts):
        return '\n\n'.join(['**台风接口查询结果**',fact_table(facts),'以上为台风接口列表与中心记录，不是NC海况格网，也不据此签发正式预警。'])
    if result.get('state') in {'needs_location','not_executed','unavailable','no_data'} and not facts:
        return str(result.get('reason') or '该项尚未取得有效数据。')+'[资料1]'
    if result.get('kind')=='historical_comparison':
        lines=['以下按同一指标列出已取得的历史记录和计算差值[资料1]。']
        if result.get('requested_as_of'):lines.append('预报资料截至时刻：'+result['requested_as_of']+'，北京时间[资料1]。')
        forecast=result.get('forecast') or {}
        if forecast:lines.append('预报有效时段：'+forecast['start']+' 至 '+forecast['end']+'；起报批次：'+str(forecast.get('batch'))+'，北京时间[资料1]。')
        lines+=['',fact_table(facts)]
        for f in facts:
            if f['kind']=='historical_metric':lines.append(f"{f['location']}的{f['metric']}出处：{f['source_title']}，{f.get('locator') or '已登记历史报告记录'}；{f['scope']}[资料1]。")
        for note in dict.fromkeys([result.get('reason')]+result.get('limitations',[])+result.get('missing_information',[])):
            if note:lines+=['',note+'[资料1]。']
        if forecast:lines+=['','预报按当前SWL、天文潮和高程基准假设计算，岸段代表点结果为模式预报，未构成实际灾损、同站检验或活动安全判定[资料1]。']
        return '\n'.join(lines)
    lines=['已取得以下程序查询结果；本次语言说明未通过核对，先保留可追溯的计算值。',
           (f"资料截至时刻：{result.get('requested_as_of') or result.get('as_of')}，北京时间；接口快照取得时间：{result.get('retrieved_at') or '未提供'}[资料1]。" if result.get('kind')=='typhoons' else
            f"查询时段：{result['start']} 至 {result['end']}，北京时间[资料1]。")]
    if result.get('kind')=='historical_comparison':
        lines+=['','本次为分项指标对照，不代表总体灾损轻重；预报岸段峰值不是历史测站同站差值，NC的台风场景归属尚未核定[资料1]。']
        for f in facts:
            if f['kind']=='comparison':
                direction='高' if f['difference']>0 else '低' if f['difference']<0 else '相同，差值为'
                lines.append(f"{f['metric']}：{f['left']}{f['left_value']} {f['unit']}，{f['right']}{f['right_value']} {f['unit']}；前者比后者{direction}{abs(f['difference'])} {f['unit']}[资料1]。")
    computed=[f for f in facts if f['kind']=='computed']
    if computed:
        lines+=['','| 地点 | 指标 | 数值 | 发生时间 |','|---|---|---|---|']
        for f in computed:
            lines.append(f"| {f['location']} | {f['metric']} | {f['value']} {f['unit']} | {f.get('time') or '未提供'} [资料1] |")
    products=[f for f in facts if f['kind']=='marine_product']
    if products:
        scoped=any(f.get('object_required') for f in products)
        lines+=['','| 地点 | 日期 | 指标 | 查询时段内范围 |' if scoped else '| 指标 | 查询时段内范围 |',
                '|---|---|---|---|' if scoped else '|---|---|']
        for f in products:
            name,unit=PRODUCTS[f['product']]
            if f['product']=='SWL':name='SWL风暴增水（假设）' if f.get('swl_kind')=='surge' else 'SWL总水位（假设）'
            prefix=('| '+str(f.get('location'))+' | '+str(f.get('start',''))[:10]+' ') if scoped else ''
            lines.append(prefix+'| '+name+' | '+' 至 '.join(str(v)+' '+unit for v in f['values'])+' [资料1] |')
        for row in result.get('activity_assessments',[]):
            lines+=['',row.get('label',row.get('activity','活动'))+'：'+row.get('recommendation','现有证据尚不足以给出完整活动建议。')+'[资料1]。']
        tide=result.get('tide') or {}
        if tide:
            site=(tide.get('site') or {}).get('name') or (tide.get('site') or {}).get('code','参考站')
            lines+=['',f'潮时参考站：{site}；天文潮预报，非实测，基准按现有假设[资料1]。']
            for key,label in (('high_tides','高潮'),('low_tides','低潮')):
                extrema=tide.get(key) or []
                if not extrema:lines.append(f'查询时段内未取得精确{label}记录[资料1]。')
                for row in extrema:lines.append(f"{label}：{row['time']}，{row['value_cm']} cm[资料1]。")
        for label in result.get('unsupported_requested_activities',[]):
            lines+=['',label+'：尚未接入该活动的适用规则和现场条件，无法判断可否开展[资料1]。']
        if any(term in question for term in ('开放','关闭','恢复')):
            lines+=['','场地开放、关闭和恢复：已列活动结论按取得的证据给出；营业时间不等于活动许可。本次语言说明未通过核验，未形成可确认的正式公告及复开时刻答复，不据数值表推定开放。']
    for f in facts:
        if f['kind']=='typhoon_scope':
            lines+=['','完整活动台风数量未知：未取得截至时刻前的完整活动列表，已取得的个别台风记录不能当作全部数量[资料1]。']
        elif f['kind']=='coverage':
            lines+=['',f"请求{f['requested_count']}个岸段，可计算{f['usable_count']}个，缺数{f['missing_count']}个，时段不完整{f['partial_count']}个；缺测未按零处理[资料1]。"]
        elif f['kind']=='analogue':lines+=['',f"路径相似排名第{f['rank']}为{f['season']}年{f['name']}；历史损失需按对应报告单独核实，路径相似不表示损失相同[资料1]。"]
    lines+=['','以上为预报或假设口径计算；岸段代表点不等于验潮站实测，天文潮与SWL含义及高程基准限制保留，不据此签发预警或确认活动安全[资料1]。']
    return '\n'.join(lines)
