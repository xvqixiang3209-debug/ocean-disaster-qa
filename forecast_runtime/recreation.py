"""Query actual marine data before discussing shore and water activities."""
from datetime import datetime
import json
import re

from .storage import BEIJING,local_now
from .grids import distance_km
from .activity_rules import load_rules,numerical_screen,activity_gates,tide_reference,stamp

# These are explicit analysis sampling positions, not surveyed bathing sites.
# The exact requested and selected NC cells are always exposed to the caller.
LOCATIONS={
    'zhujiajian':{'name':'朱家尖东侧近岸参考海点','aliases':['朱家尖','南沙','东沙'],
                  'lon':122.4,'lat':29.9,'coast_id':'1182',
                  'coordinate_status':'configured_regional_sample_not_beach_survey',
                  'coordinate_note':'配置的区域采样点，不是南沙或东沙浴场实测定位；不得据此判断具体礁石或滩涂。'},
}
ACTIVITIES={'tidal_flat':r'赶海','swimming':r'游泳|下水|有用|海水浴场','shore_fishing':r'海钓|垂钓|岸钓|矶钓|礁钓','boat_fishing':r'船钓|出海钓'}
LABELS={'tidal_flat':'赶海','swimming':'游泳','shore_fishing':'岸边或礁石海钓','boat_fishing':'船钓'}
SITE_LABELS={'nansha':'南沙','dongsha':'东沙','wushitang':'乌石塘','baishan':'白山'}


def activity_question(question):
    physical=bool(re.search(r'赶海|游泳|海钓|垂钓|岸钓|船钓|矶钓|礁钓|下水|摩托艇|水上摩托',question))
    if re.search(r'退订|退房|订房|能退|退费|退款|酒店|民宿|机票|船票',question) and not physical:return False
    beach_visit=bool(re.search(r'海边|沙滩|滨海景区|南沙|东沙',question) and re.search(r'安全|开放|关闭|恢复|能去|可以去',question))
    if not beach_visit and not physical and '海水浴场' not in question:return False
    if re.search(r'什么是|定义|原理|怎么自救|如何自救|事故统计|历史.*事故',question):return False
    return bool(re.search(r'能|可以|适宜|安全|今天|明天|后天|当前|现在|预报|20\d{2}|\d+月\d+日',question))


def resolve_location(runtime,question):
    from .activity_locations import resolve_activity_location
    return resolve_activity_location(runtime,question,configured_locations=LOCATIONS)


def requested_activities(question):
    requested=[key for key,pattern in ACTIVITIES.items() if re.search(pattern,question)]
    # A boat-only question is not silently also assessed as a shore activity.
    if 'boat_fishing' in requested and not re.search(r'岸钓|岸边|矶钓|礁钓',question):
        requested=[key for key in requested if key!='shore_fishing']
    if not requested and not unsupported_activities(question):requested=['tidal_flat','swimming','shore_fishing']
    return requested


def unsupported_activities(question):
    return ['水上摩托艇'] if re.search(r'摩托艇|水上摩托',question) else []


def evaluate_activities(question,products,tide,context,location,start=None,end=None,rules=None):
    """Apply independent evidence gates after exposing actual hourly sea state."""
    requested=requested_activities(question)
    rules=load_rules(rules)
    if start is None or end is None:
        times=[stamp(row.get('time')) for product in products for row in product.get('series',[])]
        times=[value for value in times if value is not None]
        if times:start,end=min(times),max(times)
    variables={row['product']:row for row in products}
    tidal=tide_reference(tide,rules,start,end)
    assessments=[]
    for activity in requested:
        scoped_rules=load_rules(rules,location=location,activity=activity,start=start,end=end)
        screening=numerical_screen(products,scoped_rules,start,end,location=location,activity=activity,context=context)
        gates,closures,site_id=activity_gates(activity,context,location,start,end,scoped_rules)
        official=scoped_rules.get('schema')==2
        gaps=[row['label'] for row in gates if row['state']!='passed']
        if screening['state']=='insufficient':
            gaps.append(screening.get('reason','缺同场地、时段和指标含义一致的官方评价输入') if official else '风、浪、流逐时预报缺数、单位不支持或时段覆盖不全')
        if official and screening['state'] in {'not_evaluated','unsupported_semantics'}:
            gaps.append(screening.get('reason') or '当前没有同时满足适用范围、指标含义和有效数据要求的官方数值判据')
        if activity=='tidal_flat' and tidal['state']!='ready':gaps.append('缺完整匹配时段的潮汐过程')
        requested_site=location.get('site_id') or context.get('site_id')
        regional_closures=bool(closures and not requested_site)
        if closures and requested_site:
            state='official_closed'
            recommendation='所查场地'+SITE_LABELS.get(requested_site,str(requested_site))+'在查询时段内有该活动的有效关闭或禁止通知；所列关闭时段不开展该活动。'
        elif any(gate['state']=='failed' for gate in gates):
            state='not_recommended'
            recommendation='匹配场地和时段的资料显示至少一项活动条件未通过，暂不推荐开展；具体未通过条件见证据门槛。'
        elif screening['state']=='failed':
            state='not_recommended'
            all_failed=bool(screening['hourly']) and all(row['state']=='failed' for row in screening['hourly'])
            recommendation=('本次适用的官方指标评价存在未通过项，按该项评价不建议开展；具体依据见评价结果，这不是行政禁令。' if official else ('所列采样时刻均超过' if all_failed else '部分采样时刻超过')+'当前工程筛查配置，暂不推荐该时段自行开展；这是数值筛查结论，不是官方禁令。')
        elif not gaps and screening['state']=='passed':
            state='conditional_recommendation'
            recommendation='所查时段的数值筛查和该场地活动证据门槛均通过，可按已核定规则及现场管理要求有条件考虑开展；不是安全保证，现场变化或新通知优先。'
        else:
            state='insufficient_evidence'
            recommendation=(('已完成适用官方判据的数值评价；仍需核实本次场地开放及现场条件，未把评价结果当作活动许可。'
                             if screening['state']=='passed' else
                             '可提供实际海况和适用规则；现有依据不足以给出可开展或必须停止的确定结论。未找到适用数值判据的要素不作超限判定。')
                            if official else
                            ('逐时风浪流通过工程数值筛查，可查看候选时段；仍未取得全部场地与活动依据，不能给出可开展的肯定建议。'
                             if screening['state']=='passed' else '现有逐时海况或场地证据不足，暂不能给出可开展的肯定建议；下列已获得数值和潮时仍可参考。'))
        if regional_closures:
            recommendation='查询区域中部分场地有活动关闭信息（'+ '、'.join(SITE_LABELS.get(site,site) for site in sorted({row['site_id'] for row in closures}))+'），仅适用于通知所列场地与时段，不能推定全岛或其他场地均关闭。'+recommendation
        basis=[]
        for token in ('WAV','SSW','OCU','SST'):
            value=variables.get(token,{})
            if value.get('state')=='ready':basis.append({'product':token,'unit':value.get('unit'),
                'minimum':value.get('minimum'),'maximum':value.get('maximum')})
        if activity=='tidal_flat' and tide:
            # Compare tide times with the actual published admission hours,
            # without interpreting either as a safe tidal-flat access window.
            for low in tide.get('low_tides',[]):
                low_stamp=datetime.fromisoformat(low['time']);time=low_stamp.strftime('%H:%M');day=low_stamp.date().isoformat()
                available=context.get('query_date')==day
                opening=[row for row in context.get('opening_hours',[]) if available
                         and row.get('valid_from',day)<=day<=row.get('valid_to',day)]
                sites=[row['site'] for row in opening if row['start_time']<=time<row['end_time']]
                basis.append({'reference_low_tide':low,'admission_sites_at_that_time':sites,
                              'admission_context_available':bool(opening),
                              'not_a_safe_access_window':True})
        assessments.append({'activity':activity,'label':LABELS[activity],
            'state':state,'recommendation':recommendation,'site_id':site_id,
            'missing_conditions':gaps,'numeric_basis':basis,'evidence_gates':gates,
            'official_closures':closures,'closure_scope':'regional_specific_sites_only' if regional_closures else 'requested_site',
            'numerical_screening':screening,
            'official_policy_selection':scoped_rules.get('policy_selection'),
            'candidate_windows':screening['candidate_windows'],
            'tide_reference':tidal if activity=='tidal_flat' else None,
            'permission_state':'partial_restrictions' if regional_closures else 'verified_closed' if closures else 'verified_open' if any(
                row['kind']=='activity_permission' and row['state']=='passed' for row in gates) else 'not_established',
            'reason':'数值筛查、活动开放许可和具体场地风险分别判断；低风浪不能单独证明安全。'})
    return assessments


def query_activity(runtime,question,start,end):
    location=resolve_location(runtime,question)
    base={**runtime.base_result(start,end),'kind':'activity','requested_activities':
          [LABELS[key] for key in requested_activities(question)]}
    if not location:return {**base,'state':'needs_location','reason':'请给具体海滩、岸段名称或东经北纬，以便提取相应NC和潮汐数据。'}
    if location.get('coordinate_conflict'):
        return {**base,'state':'needs_location','location':location,'reason':location['coordinate_note']}
    now=local_now()
    # A dated full-day answer uses a run issued by the beginning of that day.
    # It is a forecast reconstruction, not a statement that those values were
    # measured. Do not silently use a later run for earlier hours.
    cutoff=min(now,start)
    request_cutoff=getattr(getattr(runtime,'_local',None),'request_cutoff',None)
    if isinstance(request_cutoff,datetime):cutoff=min(cutoff,request_cutoff)
    acquisition=runtime.acquisition.ensure(start,end,products=['WAV','SSW','OCU','SST','SWL'],as_of=cutoff)
    products=[runtime.grid(product,location['lon'],location['lat'],start,end,as_of=cutoff,
                           max_distance=8) for product in ('wave','wind','current','temperature','surge')]
    coast=next((c for c in runtime.coasts() if str(c['Id'])==location.get('coast_id')),None)
    tide=None
    if coast:
        profile,method=runtime.reference(coast)
        if profile:
            tide=runtime.tide(profile['code'],start,end)
            tide['reference_for']=coast['name'];tide['reference_method']=method
    site_name=next((name for name in ('南沙','东沙','乌石塘','白山') if name in question),None)
    adapter=runtime.activity_sources
    if callable(getattr(type(adapter),'fetch_for_location',None)):
        context=adapter.fetch_for_location(location,start.date(),as_of=cutoff)
    else:context=adapter.fetch(location['key'],start.date(),site_name=site_name)
    rules=load_rules(getattr(runtime,'activity_rules',None),location=location,start=start,end=end)
    assessments=evaluate_activities(question,products,tide,context,location,start,end,rules)
    spatial_context={'state':'unavailable','reason':'spatial_module_unavailable'}
    spatial=getattr(runtime,'spatial',None)
    if spatial is not None and callable(getattr(spatial,'point',None)):
        try:
            spatial_context=spatial.point(location['lon'],location['lat'])
            if not isinstance(spatial_context,dict):
                spatial_context={'state':'unavailable','reason':'invalid_spatial_response'}
        except (ValueError,KeyError,OSError,RuntimeError):
            spatial_context={'state':'unavailable','reason':'spatial_point_query_failed'}
    spatial_context={**spatial_context,'activity_scope':'区域参考点的地形和人口背景，不是实测滩高、返程安全线或受影响人数。',
                     'not_an_inundation_calculation':True}
    ready=[p for p in products if p['state']=='ready']
    result={**base,'state':'ready' if ready else 'no_data','location':location,
            'forecast_issue_cutoff':cutoff.isoformat(),'forecast_not_observation':True,
            'products':products,'tide':tide,'official_context':context,'acquisition':acquisition,
            'unsupported_requested_activities':unsupported_activities(question),
            'activity_assessments':assessments,'permission_state':
                'verified_closed' if any(row['permission_state']=='verified_closed' for row in assessments) else
                'partial_restrictions' if any(row['permission_state']=='partial_restrictions' for row in assessments) else
                'verified_open' if assessments and not unsupported_activities(question) and all(row['permission_state']=='verified_open' for row in assessments) else 'not_established',
            'activity_rule_version':rules.get('version'),'activity_rule_fingerprint':rules['fingerprint'],
            'activity_rule_mode':'official_catalog' if rules.get('schema')==2 else 'legacy_explicit_override',
            'spatial_context':spatial_context,
            'limitations':['区域网格不解析具体海滩的破碎浪与离岸流。',
                           'SST原单位写作Celsis D，按摄氏度解释仍属试算假设；模式海表温度不替代浴场实测水温。',
                           '天文潮不包含全部现场水位变化，不能直接当滩涂出露与返程安全线。',
                           '景区营业公告不代表允许游泳、赶海或海钓。']}
    # Immutable inputs are also in parsed NC series rows; this indexed report
    # links data, source files and the derived activity assessment together.
    key='activity_result:'+__import__('hashlib').sha256(json.dumps(
        {'question':question,'activities':requested_activities(question),'location':location,
         'start':start.isoformat(),'end':end.isoformat(),'rule_fingerprint':rules['fingerprint'],
         'official_context':context,'forecast_sources':[row.get('source_id') for row in products]},
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    result['request_key']=key
    runtime.store.state(key,result)
    return result


def activity_summary(result,compact=False):
    from .activity_policy_queries import policy_digest
    if not result.get('products'):return json.dumps(result,ensure_ascii=False)
    rows=[]
    for value in result['products']:
        meta={'要素':value['product'],'单位':value.get('unit'),'状态':value['state'],
              '变量描述':value.get('variable_description'),
              '起报时间':value.get('batch'),'文件':value.get('filename'),
              '格点经纬度':[value.get('grid_lon'),value.get('grid_lat')],'距请求点公里':value.get('distance_km'),
              '时段覆盖不全':value.get('partial'),'文件指纹':value.get('source_id')}
        for label,key in [('最小','minimum'),('最大','maximum')]:
            if value.get(key):meta[label]={'time':value[key]['time'],'value':value[key]['value'],'展示值':round(value[key]['value'],2)}
        rows.append(meta)
    tide=result.get('tide')
    context=result.get('official_context') or {}
    assessments=result['activity_assessments']
    screen=assessments[0]['numerical_screening'] if assessments else {}
    tidal=next((row['tide_reference'] for row in assessments if row['activity']=='tidal_flat'),None)
    hourly=screen.get('hourly',[])
    tidal_rows=tidal.get('process',[]) if tidal else []
    def summarized(values):return values if len(values)<=40 else values[:20]+values[-20:]
    # Put conclusions and conditional windows before large hourly arrays. The
    # generator's evidence budget must not silently drop the decision gates.
    payload={'活动结论与候选时段':[{
        '活动':row['label'],'结论':row['recommendation'],'结论类别':row['state'],
        '许可状态':row['permission_state'],'数值筛查':row['numerical_screening']['state'],
        '适用官方条款':[policy_digest(policy) for policy in (row.get('official_policy_selection') or {}).get('eligible_policies',[])],
        '数值评价说明':row['numerical_screening'].get('reason'),
        '官方指标评价':row['numerical_screening'].get('numeric_evaluations',[]),
        '条件式说明':row['numerical_screening'].get('conditional_findings',[]),
        '缺少条件':row['missing_conditions'],'未通过条件':[gate['label'] for gate in row['evidence_gates'] if gate['state']=='failed'],
        '有效关闭通知':row['official_closures'],
        '已通过门槛':[{'条件':gate['label'],'来源':[
            (source.get('source_url') or source.get('url') or (source.get('source') or {}).get('url')) if isinstance(source,dict) else source for source in gate['sources']]}
            for gate in row['evidence_gates'] if gate['state']=='passed']}
        for row in assessments],
        '规则模式':result.get('activity_rule_mode'),
        '各活动规则版本及评价':[{'活动':row['label'],
            '版本':row['numerical_screening'].get('rule_version'),
            '状态':row['numerical_screening'].get('state'),
            '数值指标':row['numerical_screening'].get('limits',{}),
            '评价说明':row['numerical_screening'].get('reason')}
            for row in assessments],
        '数值评价候选采样时段':screen.get('candidate_windows',[]),
        '筛查规则':{key:screen.get(key) for key in ('rule_version','rule_status','limits','thresholds_are_official','assumption','purpose')},
        '赶海低潮参考窗口':tidal.get('low_tide_reference_windows',[]) if tidal else [],
        '潮相解释':tidal.get('phase_notice') if tidal else None,
        '潮汐转折说明':[{'时刻':row['time'],'精确潮相参考':row['phase_label'],
            '高低潮依据':row.get('phase_basis'),'相邻采样区间':row.get('interval_trend')}
            for row in tidal_rows if row.get('interval_trend',{}).get('contained_extrema')],
        '查询时段':[result['start'],result['end']],'查询时间':result['as_of'],
        '定位':result['location'],'预报起报截止':result['forecast_issue_cutoff'],'数值性质':'NC模式预报，不是实况',
        '海况':rows,'风速语义':'SSW为u10/v10矢量模，按10米阵风假设解释，不是平均风',
        '潮汐':{'参考站':tide['site']['code'],'名称':tide['site']['name'],'文件':tide['site']['filename'],
            '关联':tide.get('reference_method'),'高潮':tide['high_tides'],'低潮':tide['low_tides'],
            '潮位单位':'cm','潮位基准':'原值按1985基准假设使用，尚未核定'} if tide else None,
        '区域地形与人口背景':result.get('spatial_context'),
        '营业时间':context.get('opening_hours',[]),'水质':context.get('water_quality'),
        '活动许可':context.get('activity_permission'),'官方来源':[
            {**{key:row.get(key) for key in ('title','url','published_at','observed_at','valid_from','valid_to','status','scope')},
             'text_excerpt':str(row.get('text_excerpt') or '')[:650]} for row in context.get('sources',[])[:3]],
        '赶海潮时与营业时间对照':[item for row in result['activity_assessments'] if row['activity']=='tidal_flat'
            for item in row['numeric_basis'] if 'reference_low_tide' in item],
        '限制':result['limitations'],
        '假设':result['assumptions'][:5],
        '逐时表展示说明':{'海况总行数':len(hourly),'潮过程总行数':len(tidal_rows),
            '海况省略行数':max(0,len(hourly)-40),'潮过程省略行数':max(0,len(tidal_rows)-40),
            '说明':'超过40行的逐时表仅展示首尾各20行；范围统计、数值候选时段及活动判断采用完整序列。完整逐时数据保存在本次业务数据原始结果中。'},
        '逐时海况列':['时刻','浪高m','风速m/s','流速m/s','筛查状态','超配置要素','缺数要素'],
        '逐时海况':[[row['time'],row['values'].get('WAV',{}).get('value'),row['values'].get('SSW',{}).get('value'),
            row['values'].get('OCU',{}).get('value'),row['state'],row['exceeded_products'],row['missing_products']]
            for row in summarized(hourly)],
        '逐时潮过程列':['整点时刻','天文潮cm','按精确高低潮时刻判别的参考潮相','相邻采样区间起止','采样区间净升降（不是整点瞬时潮相）'],
        '逐时潮过程':[[row['time'],row['value_cm'],row['phase_label'],
            [row.get('interval_trend',{}).get('start'),row.get('interval_trend',{}).get('end')],
            row.get('interval_trend',{}).get('label')] for row in summarized(tidal_rows)]}
    # Keep the actual sea state, tide and dated observations before detailed
    # rules/hourly arrays, so a bounded model context cannot erase these facts.
    payload['未接入规则的所问活动']=result.get('unsupported_requested_activities',[])
    leading=('查询时段','查询时间','定位','预报起报截止','数值性质','海况','潮汐','未接入规则的所问活动',
             '营业时间','水质','活动结论与候选时段','规则模式','各活动规则版本及评价','限制','假设')
    ordered={key:payload[key] for key in leading}
    ordered.update({key:value for key,value in payload.items() if key not in ordered})
    if compact:
        for key in ('逐时海况','逐时海况列','逐时潮过程','逐时潮过程列','潮汐转折说明','各活动规则版本及评价','筛查规则','逐时表展示说明','赶海潮时与营业时间对照'):
            ordered.pop(key,None)
        for row in ordered['活动结论与候选时段']:
            row['适用官方条款']=row['适用官方条款'][:2]
    return json.dumps(ordered,ensure_ascii=False,separators=(',',':'))
