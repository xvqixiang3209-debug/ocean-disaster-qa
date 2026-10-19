"""Bounded typed facts from actual tool outputs, never inferred from row IDs."""
import math

PRODUCTS={'WAV':('有效波高','米'),'SSW':('阵风','m/s'),'SWL':('SWL水位分量','米'),
          'OCU':('格点流速','m/s'),'SST':('水温','℃'),'MWD':('平均波向原始角度','°')}

def _finite(value):
    return type(value) in {int,float} and math.isfinite(value)

def delivery_facts(result):
    kind=result.get('kind')
    if kind=='bundle':
        return ([{'kind':'impact_scope','citation':1,'required':True}] if result.get('purpose')=='impact_boundary_inputs' else [])+[
            fact for row in result.get('items',[]) for fact in delivery_facts(row)]
    facts=[]
    if kind=='historical_comparison':
        from .historical_comparison import comparison_facts
        return comparison_facts(result)
    if kind=='typhoons' and result.get('activity_scope_warning_required'):
        facts.append({'kind':'typhoon_scope','citation':1,'required':True})
    if kind=='typhoons' and result.get('state')=='ready':
        for storm in result.get('list',[])[:3]:
            points=storm.get('points') or []
            if not points:continue
            latest=max(points,key=lambda p:p['time'])
            name=str(storm.get('nameCN') or storm.get('nameEN') or '')+'（接口标识'+str(storm.get('typhoonID'))+'）'
            if _finite(latest.get('maxSpeed')):
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
        if fact['kind']=='comparison_scope':
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
        if f['kind']=='impact_scope':
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
