"""Ensure a generated tool answer actually delivers the available facts."""
import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from .quantities import normalize_grouped_numbers


def _typhoon_time_present(text,value):
    if not value:return True
    try:t=datetime.fromisoformat(str(value))
    except (ValueError,TypeError):return str(value) in text
    day=rf'(?:{t.year}[-/]0?{t.month}[-/]0?{t.day}|(?:{t.year}年)?0?{t.month}月0?{t.day}日)'
    clock=rf'(?:{t.hour:02d}:{t.minute:02d}|(?<!\d)0?{t.hour}(?:时|点)(?:0?{t.minute}分?)?)'
    return bool(re.search(day,text) and re.search(clock,text))


def _typhoon_lines(answer,fact):
    citation=f"[资料{fact.get('citation',1)}]"
    lines=[line for line in answer.splitlines() if citation in line]
    if fact['kind'] in {'typhoon_field','typhoon_field_gap','typhoon_identity'} or (fact['kind']=='typhoon_timing' and fact.get('storm_id')):
        identity=str(fact.get('storm_id') or fact.get('storm_name') or fact.get('location') or '')
        lines=[line for line in lines if identity and identity in line]
    return lines


def _typhoon_count_claims(answer):
    patterns=(r'(?:活动台风(?:数量|总数|数)?|(?:完整)?活动列表)(?:[^。；\n\d]{0,18})?(?P<value>\d+)\s*个',
              r'(?:共有|共|现在有|目前有|当前有|有)\s*(?P<value>\d+)\s*个(?:正在)?(?:活动(?:的)?台风|台风(?:在活动|正在活动))')
    for line in answer.splitlines():
        if re.search(r'未知|无法(?:确认|确定|统计)|不能.{0,14}(?:推定|当作|统计)|不等于|不是.{0,10}数量预测',line):
            # A historical/scoped assertion still has to report its actual count;
            # this exemption only rejects neither zero nor future count disclaimers.
            if not re.search(r'接口完整活动列表有\d+个台风',line):continue
        for pattern in patterns:
            for match in re.finditer(pattern,line):yield int(match['value']),line


def _validate_typhoon_fact(answer,fact):
    """Bind individual interface fields; other numbers cannot fill a missing one."""
    kind=fact['kind'];lines=_typhoon_lines(answer,fact)
    if kind=='typhoon_count':
        good=[]
        for line in lines:
            if not re.search(r'活动台风|活动列表',line):continue
            if not re.search(r'(?<!\d)'+str(fact['value'])+r'\s*个(?:台风)?',line):continue
            if not re.search(r'接口|列表|清单|快照',line):continue
            if not _typhoon_time_present(line,fact.get('as_of')):continue
            good.append(line)
        if not good:raise ValueError('business_delivery_missing_typhoon_count')
        for value,line in _typhoon_count_claims(answer):
            if value!=fact['value']:raise ValueError('business_delivery_typhoon_count_mismatch')
        if fact.get('stale') and not any(re.search(r'快照|缓存|滞后|仅代表|历史记录',line) for line in good):
            raise ValueError('business_delivery_typhoon_count_freshness_missing')
        return
    if kind=='typhoon_scope':
        if not any(re.search(r'(?:完整)?活动台风数量.{0,12}(?:未知|不明|无法)|(?:无法|不能).{0,15}(?:统计|确认).{0,8}(?:完整|全部).{0,8}数量',line) for line in lines):
            raise ValueError('business_delivery_unknown_typhoon_count_scope')
        for _,line in _typhoon_count_claims(answer):
            if not re.search(r'所选|部分|快照|历史|未知|无法|不能',line):
                raise ValueError('business_delivery_unverified_active_typhoon_count')
        return
    if kind=='typhoon_selected_count':
        if not any(re.search(r'所选|选中|指定',line) and re.search(r'(?<!\d)'+str(fact['value'])+r'\s*个',line) for line in lines):
            raise ValueError('business_delivery_missing_typhoon_selected_count')
        return
    if kind=='typhoon_identity':
        if not lines:raise ValueError('business_delivery_missing_typhoon_identity')
        return
    if kind=='typhoon_timing':
        if not any(fact['metric'] in line and
                   (_typhoon_time_present(line,fact.get('value')) if fact.get('value') else re.search(r'未提供|缺少|未知',line)) for line in lines):
            raise ValueError('business_delivery_missing_typhoon_timing:'+str(fact.get('field')))
        return
    if kind=='typhoon_availability':
        if fact.get('value')=='no_active_typhoons':
            valid=any(re.search(r'完整活动列表.{0,6}为空|活动台风数量.{0,4}0个',line) and
                      re.search(r'无|没有|不适用',line) and _typhoon_time_present(line,fact.get('as_of')) for line in lines)
        else:
            valid=any(re.search(r'未取得|未知|不可用|无法查询',line) and re.search(r'台风|位置|强度|方向',line) for line in lines)
        if not valid:raise ValueError('business_delivery_missing_typhoon_availability')
        return
    metric=str(fact.get('metric') or '')
    aliases={
        'position':r'中心位置|位置|经度|纬度|东经|西经|北纬|南纬',
        'maxSpeed':r'最大风速|中心风速|最大风', 'windScale':r'风力等级|风力|级',
        'intensity':r'强度|等级', 'pressure':r'气压',
        'moveDirection':r'移动方向|移向|方向', 'moveSpeed':r'移动速度|移速|速度'}
    source_field=fact.get('source_field') or fact.get('field')
    pattern=aliases.get(source_field,re.escape(metric))
    matching=[line for line in lines if re.search(pattern,line)]
    if kind=='typhoon_field_gap':
        if not any(re.search(r'未提供|缺失|缺少|未取得|未知|无法',line) for line in matching):
            raise ValueError('business_delivery_missing_typhoon_field_gap:'+str(source_field))
        return
    if kind!='typhoon_field':return
    accepted=[]
    for line in matching:
        if not _typhoon_time_present(line,fact.get('time')):continue
        if source_field=='position':
            value=fact['value'];lon=value['lon'];lat=value['lat']
            longitude=(r'东经' if lon>=0 else r'西经')+r'\s*'+_number(abs(lon))+r'\s*(?:度|°)?'
            latitude=(r'北纬' if lat>=0 else r'南纬')+r'\s*'+_number(abs(lat))+r'\s*(?:度|°)?'
            coordinate_pair=r'(?<![\d.])'+_number(lon)+r'\s*[,，/]\s*'+_number(lat)+r'(?![\d.])'
            if not ((re.search(longitude,line) and re.search(latitude,line)) or re.search(coordinate_pair,line)):continue
        elif isinstance(fact.get('value'),(int,float)):
            if not re.search(r'(?<![\d.])'+_number(fact['value'])+r'(?![\d.])',line):continue
            unit=fact.get('unit');units={'m/s':r'm/s|米/秒|米每秒','km/h':r'km/h|千米/小时|公里/小时|千米每小时','hPa':r'hPa|百帕','级':'级'}
            if unit and not re.search(units.get(unit,re.escape(unit)),line,re.I):continue
        elif str(fact.get('value')) not in line:continue
        if fact.get('unit_assumed') and not re.search(r'单位.{0,28}(?:假设|尚未核定|未核定|暂按)|(?:假设|暂按).{0,10}(?:单位|m/s|km/h|hPa)',line):continue
        if fact.get('stale') and not re.search(r'滞后|不是.{0,12}实时|不能.{0,16}(?:认定|代表).{0,16}(?:实时|此刻)|仅代表|历史记录',line):continue
        accepted.append(line)
    if not accepted:raise ValueError('business_delivery_missing_typhoon_field:'+str(source_field))


def _computed_present(answer,fact):
    unit=fact.get('unit','')
    aliases={'cm':r'厘米|cm','m':r'米|m(?![A-Za-z/])','米':r'米|m(?![A-Za-z/])',
             'm/s':r'm/s|米/秒|米每秒','℃':r'°C|℃|摄氏度|度','°':r'°|度|degree'}
    metric=fact.get('metric','')
    term=(r'增水' if '增水' in metric else r'潮位|水位' if re.search(r'潮位|水位',metric) else
          r'浪高|波高|海浪' if '波高' in metric else r'风速|阵风' if re.search(r'风速|阵风',metric) else
          r'波向|方向|角度' if '波向' in metric else r'流速' if '流速' in metric else r'水温|海温')
    location=str(fact.get('location') or '')
    local_name=re.sub(r'(?:站|镇|街道)$','',location.split('（')[0].split('(')[0])
    coordinate=bool(re.fullmatch(r'[\d.,]+',location))
    for line in answer.splitlines():
        if f"[资料{fact.get('citation',1)}]" not in line:continue
        if not re.search(term,line):continue
        if not re.search(r'(?<![\d.])'+_number(fact['value'])+r'(?![\d.]).{0,24}(?:'+aliases.get(unit,re.escape(unit))+r')',line,re.I):continue
        if local_name and not coordinate and local_name not in line:continue
        if coordinate and any(v not in answer for v in location.split(',')):continue
        # Bind the time to the same table record, not a separate unrelated row.
        if fact.get('time'):
            t=datetime.fromisoformat(fact['time']);compact=re.sub(r'\s+','',line)
            day=rf'(?:{t.month:02d}-{t.day:02d}|{t.month}月0?{t.day}日|(?<!\d)0?{t.day}日)'
            clock=rf'(?:{t.hour:02d}:{t.minute:02d}|(?<!\d)0?{t.hour}(?:时|点)(?:0?{t.minute}分?)?)'
            if not (re.search(day,compact) and re.search(clock,compact)):continue
        return True
    return False


def _number(value):
    token=format(float(value),'.2f').rstrip('0').rstrip('.')
    return re.escape(token)+(r'0*' if '.' in token else r'(?:\.0+)?')


def _comparison_number(value):
    # Historical loss totals retain more than two decimal places after the
    # exact 万元 -> 亿元 conversion; ordinary display rounding must not silently
    # turn 23.3759 into another published figure.
    token=format(float(value),'.8f').rstrip('0').rstrip('.')
    return re.escape(token)+(r'0*' if '.' in token else r'(?:\.0+)?')


def _historical_present(answer,fact):
    metric=unicodedata.normalize('NFKC',fact['metric']);station=str(fact.get('station') or '').removesuffix('站')
    answer=unicodedata.normalize('NFKC',answer)
    for line in answer.splitlines():
        if f"[资料{fact.get('citation',1)}]" not in line:continue
        if fact['event_name'] not in line or str(fact['season']) not in line:continue
        if metric not in line and not (metric=='死亡含失踪人数' and re.search(r'死亡.*失踪|失踪.*死亡',line)):continue
        if station and station not in line:continue
        jurisdiction=fact.get('jurisdiction') or {}
        if jurisdiction.get('kind') in {'city','city_group'}:
            members=jurisdiction.get('members') or [jurisdiction.get('name')]
            if any(not city or str(city).removesuffix('市') not in line for city in members):continue
        if jurisdiction.get('kind')=='unknown' and not re.search(r'(?:原文)?地域未明确|原文未明确地域范围|统计(?:地域|范围)(?:尚未|未)核定|原文统计地域未明确',line):continue
        raw_time=re.sub(r'\s+','',fact.get('time_text') or '')
        if raw_time and raw_time not in re.sub(r'\s+','',line):continue
        aliases=r'cm|厘米' if fact['unit']=='cm' else re.escape(fact['unit'])
        if re.search(r'(?<![\d.])'+_comparison_number(fact['value'])+r'(?![\d.])\s*(?:'+aliases+r')',line,re.I):return True
    return False


def _validate_historical_jurisdiction_claims(answer,fact):
    from .quantities import quantities
    jurisdiction=fact.get('jurisdiction') or {}
    if jurisdiction.get('kind') not in {'city','city_group','unknown'}:return
    members=jurisdiction.get('members') or [jurisdiction.get('name')]
    metric=unicodedata.normalize('NFKC',fact['metric'])
    answer=unicodedata.normalize('NFKC',answer)
    for line in answer.splitlines():
        if f"[资料{fact.get('citation',1)}]" not in line or fact['event_name'] not in line or str(fact['season']) not in line:continue
        if metric not in line and not (metric=='死亡含失踪人数' and re.search(r'死亡.*失踪|失踪.*死亡',line)):continue
        values=[row for row in quantities(line) if _comparison_close(row[0],row[1],fact['value'],fact['unit'])]
        if not values:continue
        if jurisdiction.get('kind') in {'city','city_group'} and all(city and str(city).removesuffix('市') in line for city in members):continue
        prefix=line[:values[0][2]]
        if re.search(r'全省|浙江(?:省)?|全国',prefix) and not re.search(r'不能|不代表|未核为|未确认|不推定|并非',prefix):
            raise ValueError('business_delivery_historical_jurisdiction_mismatch')


def _comparison_identity_pattern(label):
    match=re.fullmatch(r'(\d{4})年(.+)',label)
    return re.escape(match[1])+r'年.{0,6}'+re.escape(match[2]) if match else re.escape(label)


def _comparison_metric_pattern(fact):
    metric=fact['metric']
    return (r'增水' if '增水' in metric else r'潮位|水位' if re.search('潮位|水位',metric)
            else r'经济损失|灾损' if '损失' in metric else r'波高|浪高' if re.search('波高|海浪',metric)
            else r'死亡|失踪')


def _comparison_close(value,unit,expected,expected_unit):
    from .quantities import UNITS
    if unit not in UNITS or expected_unit not in UNITS:return False
    kind,factor=UNITS[unit];expected_kind,expected_factor=UNITS[expected_unit]
    return kind==expected_kind and abs(value*factor-Decimal(str(expected))*expected_factor)<=Decimal('0.00000001')*max(factor,expected_factor)


def _comparison_line_records(answer,fact):
    """Bind each operand and signed delta before any authoritative table is added.

    Numbers elsewhere in the source, another event's value, and a later correct
    table cannot license a contradictory comparison sentence.
    """
    from .quantities import quantities,UNITS
    expected_unit=fact.get('unit')
    if expected_unit not in UNITS:return
    dimension=UNITS[expected_unit][0]
    left_pattern=_comparison_identity_pattern(fact['left']);right_pattern=_comparison_identity_pattern(fact['right'])
    metric=_comparison_metric_pattern(fact)
    for line in answer.splitlines():
        if f"[资料{fact.get('citation',1)}]" not in line or not re.search(metric,line):continue
        if fact.get('station') and fact['station'] not in line:continue
        lefts=list(re.finditer(left_pattern,line));rights=list(re.finditer(right_pattern,line))
        if not lefts or not rights:continue
        left,right=lefts[0],rights[0]
        if re.search(r'不能(?:说|认为|认定)|错误(?:示例|说法)|并非',line[:min(left.start(),right.start())]):continue
        def operand(anchor,other):
            stop=other.start() if other.start()>anchor.start() else len(line)
            segment=line[anchor.end():stop]
            segment=re.split(r'前者|后者|二者|两者|差值|差额|相差|比',segment,maxsplit=1)[0]
            # In "A比B低12cm", the amount is a difference, not B's value.
            if re.match(r'\s*(?:偏高|偏低|高|低|多|少|大|小|相同|相等)',segment):return []
            return [(value,unit) for value,unit,_,_ in quantities(segment) if UNITS[unit][0]==dimension]
        left_values=operand(left,right);right_values=operand(right,left)
        wrong_operand=(any(not _comparison_close(v,u,fact['left_value'],expected_unit) for v,u in left_values)
                       or any(not _comparison_close(v,u,fact['right_value'],expected_unit) for v,u in right_values))
        complete_operands=bool(left_values and right_values) and not wrong_operand
        deltas=[]
        for marker in re.finditer(r'(偏高|偏低|高|低|多|少|大|小|相同|相等)(?:了|出)?\s*(?:[，,]\s*差值为\s*)?',line):
            following=line[marker.end():]
            found=[row for row in quantities(following) if row[2]<=2 and UNITS[row[1]][0]==dimension]
            if not found:continue
            value,unit,_,_=found[0]
            prefix=line[:marker.start()]
            pronouns=list(re.finditer(r'前者比后者|后者比前者',prefix))
            if pronouns:
                orientation=1 if left.start()<right.start() else -1
                if pronouns[-1][0]=='后者比前者':orientation*=-1
            else:
                preceding_left=list(re.finditer(left_pattern,prefix));preceding_right=list(re.finditer(right_pattern,prefix))
                if not preceding_left or not preceding_right:continue
                orientation=1 if preceding_left[-1].start()<preceding_right[-1].start() else -1
            sign=-1 if marker[1] in {'偏低','低','少','小'} else 0 if marker[1] in {'相同','相等'} else 1
            expected=Decimal(str(fact['difference']))*orientation
            actual=value*sign
            valid=(value>=0 and (sign!=0 or value==0) and _comparison_close(actual,unit,expected,expected_unit))
            deltas.append({'value':str(value),'unit':unit,'valid':valid})
        yield {'line':line,'complete_operands':complete_operands,'wrong_operand':wrong_operand,'deltas':deltas}


def _comparison_present(answer,fact):
    return any(row['complete_operands'] and any(delta['valid'] for delta in row['deltas'])
               for row in _comparison_line_records(answer,fact))


def _validate_comparison_claims(answer,fact):
    for row in _comparison_line_records(answer,fact):
        if row['wrong_operand']:raise ValueError('business_delivery_comparison_operand_mismatch')
        if any(not delta['valid'] for delta in row['deltas']):raise ValueError('business_delivery_comparison_delta_mismatch')


def validate_object_claims(answer,facts):
    """Reject measurements borrowed from another queried object's row.

This checks optional model prose before the authoritative data table is added;
the presence of that table must never hide a contradictory generated row.
"""
    from decimal import Decimal
    from .quantities import quantities,UNITS
    scoped=[f for f in facts if f.get('object_required') and f['kind']=='marine_product']
    terms={'WAV':r'浪高|波高|海浪','SSW':r'风速|阵风|风力','OCU':r'流速','SST':r'水温|海温','SWL':r'增水|SWL'}
    for line in re.split(r'[\n。；;]+',answer):
        # Rule thresholds and dated observations are not point forecasts.
        # Their numbers are still checked against the selected citation.
        if re.search(r'规则|条款|判据|评价口径|适用|历史|监测日期',line):continue
        for f in scoped:
            name=f['location']
            aliases=[name]+([name.replace('朱家尖','')] if name.startswith('朱家尖') else [])
            if not any(alias in line for alias in aliases):continue
            marker=re.search(terms[f['product']],line)
            if not marker:continue
            segment=line[marker.end():]
            next_metric=re.search('|'.join('(?:'+p+')' for key,p in terms.items() if key!=f['product'])+
                r'|潮位|高潮|低潮|高程|海拔|潮汐|[。；;]|适宜|判据|规则|条件|阈值',segment)
            if next_metric:segment=segment[:next_metric.start()]
            unit=f.get('unit');dimension,factor=UNITS.get(unit,(None,None))
            if dimension is None:continue
            expected=[Decimal(str(v))*factor for v in f['values']]
            for value,u,_,_ in quantities(segment):
                if UNITS[u][0]!=dimension:continue
                if not any(abs(value*UNITS[u][1]-v)<=Decimal('.005')*max(factor,UNITS[u][1]) for v in expected):
                    error=ValueError('business_delivery_object_claim_mismatch')
                    error.details={'object':name,'product':f['product'],'value':str(value),'unit':u,
                                   'expected':f['values'],'line':line[:700]}
                    raise error


def _validate_official_temperature_ranges(answer):
    """Preserve the open lower boundary of the cited bathing-water rule."""
    text=unicodedata.normalize('NFKC',answer)
    interval=r'(?<![\d.])28(?:\.0+)?\s*(?:°\s*C|摄氏度|度)?\s*(?:—|–|-|~|～|至|到)\s*33(?:\.0+)?\s*(?:°\s*C|摄氏度|度)'
    for match in re.finditer(interval,text,re.I):
        first=max(text.rfind('。',0,match.start()),text.rfind('；',0,match.start()),text.rfind(';',0,match.start()),text.rfind('\n',0,match.start()))+1
        stops=[index for token in ('。','；',';','\n') if (index:=text.find(token,match.end()))>=0]
        last=min(stops) if stops else len(text)
        clause=text[max(first,match.start()-80):min(last,match.end()+80)]
        if '较适宜' not in clause:
            continue
        explicit_open=(re.search(r'(?:不含|不包括|不包含|不取)\s*28(?:\.0+)?',clause)
                       or re.search(r'不含下限|左开右闭',clause)
                       or re.search(r'28(?:\.0+)?\s*(?:°\s*C)?\s*<\s*(?:T|水温|温度)',clause,re.I)
                       or re.search(r'(?:大于|超过|高于|>)\s*$',text[max(first,match.start()-8):match.start()]))
        if not explicit_open:
            raise ValueError('official_rule_temperature_interval_boundary')


def _validate_recreational_fishing_exemption(answer):
    """Separate leisure-fishery management never establishes a permit waiver."""
    text=unicodedata.normalize('NFKC',answer)
    if '保护区' not in text or not re.search(r'休闲渔船|休闲渔业',text):
        return
    patterns=(r'(?:免证|免办|免领|免于办理|豁免|无需|不必|不需要|不需).{0,18}(?:海钓许可|个人.{0,5}许可|许可证|许可手续|许可程序)',
              r'免证',r'不适用.{0,18}(?:个人.{0,5}海钓|海钓|个人).{0,8}(?:许可|许可证)',
              r'(?:个人.{0,5}海钓|保护区海钓|个人).{0,8}(?:许可|许可证).{0,8}不适用于?')
    # Keep question delimiters: a heading repeating "可以免办...吗？" is a
    # question, not an assertion that the waiver exists.
    for sentence in re.findall(r'[^。！？!?\n]+[。！？!?\n]?',text):
        for pattern in patterns:
            for match in re.finditer(pattern,sentence):
                # Negated inferences such as "不推定免证" explicitly preserve
                # the legal boundary. Do not reject that correct explanation.
                prefix=sentence[:match.start()]
                local=re.split(r'[，,；;：:]',prefix)[-1]
                local=re.sub(r'[\s“”‘’"\'：:]','',local)
                suffix=re.sub(r'[\s“”‘’"\']','',sentence[match.end():])
                question=(re.search(r'(?:能否|是否|可否)(?:可以|可|直接|依法)?$',local)
                          or re.match(r'^(?:证|程序|手续)?(?:(?:吗|么|吧)[？?：:]?|[？?])',suffix))
                if question:
                    continue
                negated=(re.search(r'(?:不能|不得|不应|不可|不宜|无法|尚不能|不是|并非|不)(?:据此|因此|由此|直接|当然|自动|就|一概|进而|进一步|将|理解为|认定为?|认为|推定为?|推断为?|推出|等同于|视为|解释为|表明|表示|说明|认定|判断|得出|意味着|代表|等于|当作|即为|其|该活动|参与者|可以|可){0,8}$',local)
                         or re.search(r'(?:没有|未|并未|不曾)(?:规定|说明|明确|写明|授予|建立|设定).{0,15}$',local))
                qualified=re.search(r'(?:不能|不得|不应|不可|不宜)(.{0,35})(?:理解为|理解成|认定为|推定为?|推断为?|解释为|等同于|视为)(?:其|个人|参与者|休闲渔船|该活动|可以)?$',local)
                if qualified and not re.search(r'但|却|而|另外|此外|同时',qualified[1]):
                    negated=True
                # Real PR02 drafts used "不能确认[乘船者]可以免办" and
                # "不能仅凭[另按管理]推定免办". Their entire proposition is
                # explicitly denied, even with a subject between verb and item.
                cognitive=re.search(r'(?:不能|不得|不应|不可|不宜|无法|尚不能|未能)(.{0,40})(?:确认|认定|推定|推断|断言|认为|理解为|理解成|视为|等同于|解释为|证明)(.{0,40})$',local)
                if cognitive and not re.search(r'但|却|而|然而|不过|另外|此外|同时',cognitive[1]+cognitive[2]) and not re.search(r'因此|所以|故|可见',cognitive[2]):
                    negated=True
                if re.search(r'(?:不支持|不足以支持|没有依据支持|无依据支持|缺乏依据支持).{0,25}$',local) or re.search(r'(?:没有|并未|未|并非|不是)$',local):
                    negated=True
                if not negated:
                    raise ValueError('official_rule_recreational_fishing_exemption')


def validate_business_delivery(answer,meta,citations):
    coverage=meta.get('tool_series_coverage') or []
    if coverage and all(row.get('state')=='ready' and row.get('complete') is True for row in coverage):
        if re.search(r'未提供(?:逐时完整|完整逐时|逐时)序列|缺少(?:逐时完整|完整逐时|逐时)序列|没有(?:逐时完整|完整逐时|逐时)序列',answer):
            raise ValueError('business_delivery_series_claim_contradiction')
    if meta.get('official_activity_rules'):
        _validate_official_temperature_ranges(answer)
        _validate_recreational_fishing_exemption(answer)
    facts=meta.get('required_business_facts') or []
    if not facts:return
    if not citations:raise ValueError('business_delivery_missing_citations')
    # Preserve unit/token boundaries. Removing both the space and the comma
    # from "76 m，来源..." produced "76m来源": Python treats Chinese letters
    # as word characters, so an ASCII m\b incorrectly rejected the real fact.
    # Thousands separators inside numbers can be removed without joining prose.
    text=normalize_grouped_numbers(unicodedata.normalize('NFKC',answer).replace('−','-'))
    line_text=text
    text=re.sub(r'\s+',' ',text)
    for fact in facts:
        if fact.get('required') is False:continue
        kind=fact['kind']
        if kind.startswith('typhoon_') and 'field' in fact:
            _validate_typhoon_fact(line_text,fact)
            continue
        if kind=='comparison_scope':
            if fact.get('forecast_benchmark'):
                if not re.search(r'非同站|不是.{0,20}同站|不(?:能|可).{0,20}同站|不能.{0,20}(?:直接|等同).{0,20}(?:实测|测站)|采样.{0,20}不同|(?:岸段|代表点).{0,40}(?:测站|验潮站).{0,30}(?:不同|对照|比较)|(?:测站|验潮站).{0,40}(?:岸段|代表点).{0,30}(?:不同|对照|比较)',text):
                    raise ValueError('business_delivery_missing_comparison_sampling_scope')
                if not re.search(r'(?:场景|归属|归因).{0,28}(?:未|无法|不能|不明|不确定)|(?:未|无法|不能|尚未).{0,28}(?:场景|归属|归因)',text):
                    raise ValueError('business_delivery_missing_cyclone_attribution_scope')
            for sentence in re.split(r'[。；;\n]',text):
                if re.search(r'(?:总体|整体|灾损|经济损失).{0,18}(?:偏轻|偏重|较轻|较重|更轻|更重|更严重)',sentence) and not re.search(r'不能|无法|不代表|不等于|未计算|不推定|不判断|并非|不作',sentence):
                    raise ValueError('business_delivery_uncomputed_overall_disaster_comparison')
        if kind=='comparison':
            _validate_comparison_claims(line_text,fact)
            if not _comparison_present(line_text,fact):raise ValueError('business_delivery_missing_comparison_delta')
        if kind=='historical_metric':
            _validate_historical_jurisdiction_claims(line_text,fact)
            if not _historical_present(line_text,fact):
                raise ValueError('business_delivery_missing_historical_metric: '+fact['location']+' '+fact['metric'])
        if kind=='impact_scope':
            if not re.search(r'(?:未|尚未|没有)(?:进行|完成|运行|计算|开展).{0,12}淹没|不能.{0,15}(?:判断|推断|确定).{0,12}(?:淹没|被淹)',text):
                raise ValueError('business_delivery_missing_inundation_scope')
            if re.search(r'(?:预计|计算|预测|推算|将).{0,8}淹没(?:深度|面积|人数).{0,6}(?:为|是|[:：])?\s*\d',text):
                raise ValueError('business_delivery_uncomputed_inundation_value')
        if kind=='typhoon_scope':
            unknown=r'(?:数量|总数|几个).{0,24}(?:未知|不明|不确定|无法|未能)|(?:无法|不能|未能|不足以|尚不能).{0,40}(?:数量|总数|几个)|完整.{0,12}列表.{0,12}(?:未|无法|缺|不)'
            if not re.search(unknown,text):raise ValueError('business_delivery_unknown_typhoon_count_scope')
            for sentence in re.split(r'[。；;\n]',answer):
                if re.search(r'(?:有|共|共有|活动台风数(?:量|目)?(?:为|是|[:：]))\s*\d+\s*个?.{0,14}(?:活动|台风)|活动台风.{0,8}(?:为|是|[:：])\s*\d+',sentence):
                    if not re.search(r'无法|不能|未能|仅|部分|记录|快照|未知|不确定',sentence):
                        raise ValueError('business_delivery_unverified_active_typhoon_count')
        if kind=='computed':
            if not _computed_present(line_text,fact):
                raise ValueError('business_delivery_missing_computed_fact: '+str({key:fact.get(key) for key in ('metric','value','unit','location','time')}))
        elif kind=='analogue':
            if fact['name'] not in text or str(fact['season']) not in text or fact.get('citation',1) not in citations:
                raise ValueError('business_delivery_missing_analogue')
        elif kind=='coverage':
            if not all(re.search(r'(?<!\d)'+str(fact[key])+r'(?!\d)',text) for key in ('requested_count','usable_count','missing_count')):
                raise ValueError('business_delivery_missing_coverage')
            if fact.get('missing_count') and not re.search(r'缺数|缺测|未取得|未覆盖|不可计算|无法计算',text):
                raise ValueError('business_delivery_missing_coverage_scope')
        elif kind=='elevation':
            if not re.search(r'(?:海拔|高程).{0,50}(?<!\d)'+_number(fact['value'])+r'\s*(?:米(?!/|每秒)|m(?![A-Za-z0-9/²³]))',text,re.I):
                raise ValueError('business_delivery_missing_elevation')
        elif kind=='population':
            if not re.search(r'(?<!\d)'+_number(fact['value'])+r'(?!\d)',text) or str(fact['year']) not in text:
                raise ValueError('business_delivery_missing_population')
            if fact.get('has_nodata') and not (re.search(r'缺测|无数据',text) and re.search(r'有效.{0,30}合计|有值.{0,30}合计|合计.{0,30}有效|合计.{0,30}有值',text)):
                raise ValueError('business_delivery_population_requires_valid_cells_scope')
        elif kind=='marine_product':
            term={'WAV':r'浪高|波高|海浪','SSW':r'风速|阵风|风力','OCU':r'流速','SST':r'水温|海温','SWL':r'增水|水位|SWL'}[fact['product']]
            if not all(re.search(r'(?:'+term+r').{0,110}(?<!\d)'+_number(value)+r'(?!\d)',text,re.I) for value in fact['values']):
                raise ValueError('business_delivery_missing_'+fact['product'])
            if fact.get('object_required'):
                metric={'WAV':'有效波高','SSW':'阵风','OCU':'流速','SST':'水温','SWL':'水位'}[fact['product']]
                for value in fact['values']:
                    if not _computed_present(line_text,{**fact,'kind':'computed','metric':metric,'value':value,'time':None}):
                        raise ValueError('business_delivery_object_range_mismatch')
                day=datetime.fromisoformat(fact['start']) if fact.get('start') else None
                if day:
                    dated=rf'(?:{day.year}-0?{day.month}-0?{day.day}|0?{day.month}月0?{day.day}日)'
                    if not any(fact['location'] in line and re.search(term,line) and re.search(dated,line)
                               and f"[资料{fact.get('citation',1)}]" in line for line in line_text.splitlines()):
                        raise ValueError('business_delivery_object_date_mismatch')
