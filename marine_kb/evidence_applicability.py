"""Question-relative scope and validity checks for public evidence.

No answers or question IDs live here. An official host proves provenance, not
geographical jurisdiction, object identity, effective dates or current status.
Unknown validity stays visible as reference material rather than being certified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import os

CN = timezone(timedelta(hours=8))
# Paths describe containment, not interchangeable beaches/ports. Extend this
# catalogue when a new region is onboarded; unmatched names remain explicit.
PATHS = {
    '浙江': (), '福建': (), '广东': (), '广西': (), '海南': (), '山东': (),
    '江苏': (), '上海': (), '辽宁': (), '河北': (), '天津': (),
    '舟山': ('浙江',), '宁波': ('浙江',), '温州': ('浙江',), '台州': ('浙江',),
    '杭州': ('浙江',), '嘉兴': ('浙江',), '绍兴': ('浙江',),
    '普陀': ('浙江', '舟山'), '定海': ('浙江', '舟山'),
    '岱山': ('浙江', '舟山'), '嵊泗': ('浙江', '舟山'),
    '朱家尖': ('浙江', '舟山', '普陀'), '沈家门': ('浙江', '舟山', '普陀'),
    '基湖': ('浙江', '舟山', '嵊泗'), '六横': ('浙江', '舟山', '普陀'),
    '鄞州': ('浙江', '宁波'), '北仑': ('浙江', '宁波'), '奉化': ('浙江', '宁波'),
    '象山': ('浙江', '宁波'), '宁海': ('浙江', '宁波'), '余姚': ('浙江', '宁波'),
    '咸祥': ('浙江', '宁波', '鄞州'), '松兰山': ('浙江', '宁波', '象山'),
    '温岭': ('浙江', '台州'), '石塘': ('浙江', '台州', '温岭'),
    '苍南': ('浙江', '温州'), '渔寮': ('浙江', '温州', '苍南'),
    '洞头': ('浙江', '温州'), '平阳': ('浙江', '温州'),
    '南麂': ('浙江', '温州', '平阳'),
}
OBJECTS = {
    'bathing': r'游泳|浴场|下海洗澡',
    'shore_fishing': r'岸钓|岸上.{0,3}钓|矶钓',
    'boat_fishing': r'船钓|船上.{0,3}钓|休闲渔船|海钓船',
    'tidal_flat': r'赶海|滩涂|拾贝',
    'passenger': r'客运|轮渡|客船|船票|航班|航次|客运航线',
    'fishing_vessel': r'(?<!休闲)渔船|捕捞船',
    'bridge': r'大桥|桥梁|高速公路',
    'scenic': r'景区|景点|公园|闭园',
}
OPERATION = r'开放|关闭|闭园|停航|复航|通航|停运|恢复运营|封闭|解封|开航'
METHOD = r'怎么查询|如何查询|怎样查询|哪里查|什么渠道|官方渠道|查询渠道|获取方式|怎么获取'
GENERAL = r'定义|什么是|是什么|区别|成因|原理|常识|方法|一般|通常|为什么|怎么|怎样|如何'
POLICY = r'预案|条例|办法|规定|标准|规范|工作指引|实施细则|操作规程'
SITES = ('南沙', '东沙', '基湖', '松兰山', '渔寮', '大沙岙', '大沙澳', '乌石塘',
         '蜈蚣峙', '半升洞', '普陀山', '白峰', '鸭蛋山', '三江码头')
DATE = re.compile(r'(?<!\d)(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?(?:[T\s]*(\d{1,2})(?:[:：时](\d{1,2}))?(?:分)?)?')
MD = re.compile(r'(?<!\d)(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2})(?:[:：时](\d{1,2}))?分?)?')


def parse_date(value, anchor=None, end=False):
    if isinstance(value, datetime):
        return value.replace(tzinfo=CN) if value.tzinfo is None else value.astimezone(CN)
    value = str(value or '').strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10 and end:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed.replace(tzinfo=CN) if parsed.tzinfo is None else parsed.astimezone(CN)
    except ValueError:
        pass
    match = DATE.search(value)
    try:
        if match:
            year, month, day, hour, minute = match.groups()
            return datetime(int(year), int(month), int(day), int(hour or (23 if end else 0)),
                            int(minute or (59 if end and not hour else 0)),
                            59 if end and not hour else 0, tzinfo=CN)
        if anchor and (match := MD.search(value)):
            month, day, hour, minute = match.groups()
            return datetime(anchor.year, int(month), int(day), int(hour or (23 if end else 0)),
                            int(minute or (59 if end and not hour else 0)), tzinfo=CN)
    except ValueError:
        return None
    return None


def places(text):
    result = [name for name in PATHS if name in text]
    for match in re.finditer(r'[\u4e00-\u9fff]{2,7}?(?:省|市|县|区(?!别|域|分)|镇|乡|街道)', text):
        name=re.sub(r'^(?:当前|现在|今天|明天|请问|位于|查询|截至|关于|前往|请到|在)+','',match.group())
        bare=re.sub(r'(?:街道|省|市|县|区|镇|乡)$','',name)
        if len(bare)>=2 and not any(value in name for value in ('保护区','景区','地区','区域','社区','城区','辖区','开发区')):
            if bare not in result and not any(known in bare for known in result):
                result.append(bare)
    return result


def leaf_places(text):
    names = places(text)
    return [name for name in names if not any(name in PATHS.get(other, ()) for other in names)]


def business_objects(text):
    found = [name for name, pattern in OBJECTS.items() if re.search(pattern, text)]
    if 'boat_fishing' in found and 'fishing_vessel' in found and '休闲渔船' in text:
        found.remove('fishing_vessel')
    return found


def named_objects(text):
    names = [name.replace('大沙澳', '大沙岙') for name in SITES if name in text]
    # Quoted and explicitly labelled route names remain exact business IDs.
    for match in re.finditer(r'[“「"]([^”」"\n]{2,30})[”」"]\s*(?:航线|码头|浴场|景区)', text):
        names.append(match[1])
    for match in re.finditer(r'([\u4e00-\u9fff]{2,6})[—–\-至]([\u4e00-\u9fff]{2,6})航线', text):
        ends=[next((site for site in SITES if part.endswith(site)),
                   re.sub(r'^(?:今天|当前|现在|日)','',part)) for part in match.groups()]
        names.append('—'.join(ends))
    ports='|'.join(map(re.escape,SITES))
    for match in re.finditer(r'('+ports+r')[—–\-至到]('+ports+r')',text):
        names.append('—'.join(match.groups()))
    for match in re.finditer(r'([\u4e00-\u9fff]{2,10}?)(?:海水浴场|海滨浴场|浴场|码头)',text):
        name=re.sub(r'^(?:当前|现在|今天|明天|请问|位于|前往|去|在)+','',match[1])
        # Known facility stems have a stable ID independent of optional parent
        # administrative prefixes. Unknown facilities keep their literal name.
        known=next((site for site in SITES if site in name),None)
        if known:
            names.append(known.replace('大沙澳','大沙岙'))
        elif len(name)>=2 and name not in {'该处','当地','对应','其他','各个','所有','这些'}:
            names.append(name)
    return sorted(set(names))


def question_context(question, dynamic=False, now=None):
    now = parse_date(now) or datetime.now(CN)
    dates = list(DATE.finditer(question))
    # An explicit cutoff controls availability; dates after it are forecast
    # targets, never an invitation to read future publications.
    cutoff_match = re.search(r'(?:截至|截止|以)(.{0,45}?)(?:为准|时的信息|的资料|，|,|。|$)', question)
    cutoff = parse_date(cutoff_match.group(1), now, end=True) if cutoff_match else None
    if cutoff_match and len(dates)>1:
        dates = [match for match in dates if match.start() >= cutoff_match.end()]
    target_start = parse_date(dates[0].group(), now) if dates else None
    target_end = parse_date(dates[-1].group(), now, end=True) if dates else None
    if len(dates) == 1:
        tail = question[dates[0].end():dates[0].end() + 28]
        span = re.search(r'(?:至|到|—|–|-)\s*(?:(\d{1,2})月)?(\d{1,2})日', tail)
        if span:
            target_end = parse_date(f'{span[1] or target_start.month}月{span[2]}日', target_start, end=True)
        elif target_start.hour or dates[0][4]:
            hours = re.match(r'\s*(?:至|到|—|–|-)\s*(\d{1,2})(?:[:：时](\d{1,2}))?', tail)
            if hours and int(hours[1])<24:
                target_end=target_start.replace(hour=int(hours[1]),minute=int(hours[2] or 0))
    if not dates:
        relative = 1 if '明天' in question else -1 if '昨天' in question else 0
        if dynamic or re.search(r'今天|今日|明天|昨天|当前|现在', question):
            target_start = (now + timedelta(days=relative)).replace(hour=0, minute=0, second=0, microsecond=0)
            target_end = target_start + timedelta(days=1) - timedelta(seconds=1)
        if re.search(r'当前|现在|此刻|最新',question) and not re.search(r'今天|明天|未来|小时',question):
            target_start=target_end=now
    # A date-only past request is evaluated using information available by its
    # end; scientific historical summaries may be published afterwards.
    availability = cutoff or (min(now, target_end) if target_end else now)
    status = bool((re.search(OPERATION, question) or
                  dynamic and re.search(r'响应|预警|警报', question) and re.search(r'是否|已经|当前|现在', question) or
                  dynamic and business_objects(question) and re.search(r'能去|能否|可以|适不适合',question))
                  and not re.search(METHOD, question))
    return {'question': question, 'places': leaf_places(question), 'objects': business_objects(question),
            'dynamic': dynamic, 'status_request': status,
            'named_objects': named_objects(question),
            'policy_request': bool(re.search(POLICY + r'|由谁|审批|启动条件|终止条件|降级条件', question)),
            'general_request': bool(re.search(GENERAL + '|' + METHOD, question)) and not status,
            'as_of': availability, 'target_start': target_start, 'target_end': target_end}


def scope_gate_enabled():
    return os.getenv('MARINE_SCOPE_GATE', '').strip().casefold() in {'true', '1', 'yes', 'on'}


def document_kind(title, body, generic=False, primary_scope=None):
    if scope_gate_enabled():
        # A dated forecast is positive evidence of product type. The isolated
        # conjunction 与 never converts an operational product into education.
        dated_product = bool(re.search(r'预报|预测|警报|预警', title) and
                             re.search(r'\d{1,2}月\d{1,2}日|20\d{2}[-/]\d{1,2}[-/]\d{1,2}|第\d+期|未来\d+小时', title))
        educational = bool(re.search(r'科普|知识|是什么|怎么理解|预报.{0,4}防御|预报.{0,4}预警.{0,6}(?:区别|关系)|认识.{0,6}预警', title))
        if primary_scope in {'national_guidance', 'service_directory'} and not dated_product:
            return 'knowledge'
        if educational and not dated_product and not re.search(POLICY, title):
            return 'knowledge'
    if re.search(POLICY+r'|中华人民共和国.{1,20}法', title):
        return 'policy'
    if re.search(OPERATION, title) and re.search(r'公告|通知|通告|消息|安排|开放|停航|复航', title):
        return 'operation'
    if re.search(r'响应', title) and re.search(r'启动|调整|终止|解除|降级', title) and re.search(r'通知|通告|公告',title):
        return 'operation'
    if re.search(r'预警|警报', title):
        return 'warning'
    if re.search(r'预报|预测|潮汐表', title) and not re.search(r'方法|系统|规范|介绍|如何', title):
        return 'forecast'
    if re.search(r'公报|年鉴|灾情总结|历史|实录', title):
        return 'historical'
    if not generic and re.search(r'即日起|恢复开放|暂停开放|全线停航|恢复通航', body[:800]):
        return 'operation'
    return 'knowledge'


def _scope(context, title, body, kind):
    requested = context['places']
    # Navigation/footer mentions do not establish an article's jurisdiction.
    scope_text = title + '\n' + '\n'.join(re.findall(r'[^。\n]*(?:适用于|适用范围|全省|全市|本市|全县|全国)[^。\n]*', body)[:4])
    if kind=='policy' and (re.search(r'本预案为国家层面部门应急预案|本(?:预案|办法|规定)适用于全国|适用于.{0,40}我国管辖海域|本预案适用于我国境内',scope_text)
            or re.search(r'国务院办公厅|应急管理部.{0,8}财政部', title)):
        # Use the operative scope, not the province hosting a republication
        # or a department name ending in 海区 parsed as a geographic name.
        return 'national_framework', []
    actual = leaf_places(scope_text) or leaf_places(body[:650])
    if not requested:
        return 'unspecified', []
    if not actual:
        national = re.search(r'全国|中华人民共和国|自然资源部|国务院|国家.{0,6}(?:标准|预案)', scope_text)
        if national and kind == 'policy':
            return 'national_framework', []
        return ('general' if kind == 'knowledge' else 'unverified'), ['来源未明确覆盖所问地点']
    exact = all(any(a == r for a in actual) for r in requested)
    if exact:
        return 'exact', []
    contains = all(any(a in PATHS.get(r, ()) for a in actual) for r in requested)
    if contains:
        return ('regional_framework' if kind == 'policy' else 'broader_region'), ['上级地区资料不能证明具体场所状态'] if kind == 'operation' else []
    below = any(r in PATHS.get(a, ()) for a in actual for r in requested)
    return ('local_example' if below else 'other_region'), ['来源地区与提问范围不一致，限作案例或通用参考']


def _valid_interval(page, body, published, kind=None):
    metadata = page.get('metadata') or {}
    start = parse_date(page.get('valid_from') or metadata.get('valid_from'), published)
    end = parse_date(page.get('valid_to') or metadata.get('valid_to'), published, end=True)
    # Keep extraction tied to operative labels, not arbitrary dates in a story.
    for line in re.split(r'[。\n]', body):
        if kind=='policy' and not re.search(r'有效期(?:限)?|有效时间|施行日期|实施日期|(?:本预案|本办法|本规定|本细则|本通知).{0,30}(?:施行|实施|执行)|自.{0,30}(?:施行|实施)',line):
            # A plan's example "未来24小时" trigger is not its expiry date.
            continue
        if not re.search(r'有效|预报|预计|开放时间|执行时间|实施时间|施行|实施|自|起|至|到|期间', line):
            continue
        found = list(DATE.finditer(line)) or list(MD.finditer(line))
        if len(found) >= 2 and re.search(r'至|到|—|–|~|～', line):
            start = start or parse_date(found[0].group(), published)
            end = end or parse_date(found[-1].group(), published, end=True)
        elif found and re.search(r'自|于|起|施行|实施', line):
            start = start or parse_date(found[0].group(), published)
        if found and len(found)==1 and re.search(r'至|到|—|–',line[found[0].end():]):
            tail=line[found[0].end():]
            shortened=re.search(r'(?:至|到|—|–)\s*(?:(\d{1,2})月)?(\d{1,2})日',tail)
            first=parse_date(found[0].group(),published)
            if first and shortened:
                start=start or first
                end=end or parse_date(f'{shortened[1] or first.month}月{shortened[2]}日',first,end=True)
            elif first:
                hour=re.search(r'(?:至|到|—|–)\s*(\d{1,2})时',tail)
                if hour and int(hour[1])<24:
                    start=start or first
                    end=end or first.replace(hour=int(hour[1]),minute=0,second=0)
        if published and not end and kind!='policy':
            hours=re.search(r'未来\s*(\d{1,3})\s*小时',line)
            if hours:
                start=start or published
                end=published+timedelta(hours=int(hours[1]))
        if published and re.search(r'即日|即时|立即', line):
            start = start or published
    return start, end


def assess_page(question, page, dynamic=False, now=None, subquestion=None):
    context = question_context(question, dynamic, now)
    if subquestion:
        # Keep parent geography/date, but bind objects and requested action to
        # this particular subquestion rather than all its siblings.
        context.update(objects=business_objects(subquestion) or context['objects'],
                       places=leaf_places(subquestion) or context['places'],
                       named_objects=named_objects(subquestion) or context['named_objects'],
                       status_request=bool(re.search(OPERATION, subquestion) and not re.search(METHOD, subquestion)))
    title = str(page.get('page_title') or page.get('title') or '')
    body = str(page.get('content') or page.get('text') or '')
    metadata = page.get('metadata') or {}
    kind = document_kind(title, body, bool(page.get('_generic_scope')),
                         page.get('_primary_scope') or metadata.get('primary_scope'))
    if page.get('_primary_scope') == 'service_directory' or metadata.get('primary_scope') == 'service_directory':
        # A verified service directory identifies a channel, not its current
        # alert list. status_request below still prevents live-state use.
        kind = 'knowledge'
    published = parse_date(page.get('published_at') or metadata.get('publication_date'))
    start, end = _valid_interval(page, body, published, kind)
    geography, reasons = _scope(context, title, body, kind)
    actual_objects = business_objects(title) or business_objects(body[:1000])
    requested = context['objects']
    object_match = 'matched' if not requested or set(requested) & set(actual_objects) else 'mismatch' if actual_objects else 'unverified'
    if object_match == 'mismatch':
        reasons.append('业务对象不同，不能由客运/渔船或其他活动推定所问对象状态')
    named = named_objects(title) or named_objects(body[:1000])
    named_match = ('matched' if not context['named_objects'] or set(context['named_objects']) <= set(named)
                   else 'mismatch' if named else 'unverified')
    if named_match != 'matched':
        reasons.append('未匹配所问的具体浴场、码头或航线，不能用同地区其他设施替代')
    temporal = 'not_time_sensitive'
    if kind in {'forecast', 'warning', 'operation'}:
        if published and published > context['as_of']:
            temporal = 'after_cutoff'
        elif not published:
            temporal = 'publication_unknown'
        elif end and context['target_start'] and end < context['target_start']:
            temporal = 'expired'
        elif (kind=='operation' and re.search(r'响应',title) and context['target_start']
              and published.year != context['target_start'].year and not end):
            temporal = 'event_not_matched'
        elif start and context['target_end'] and start > context['target_end']:
            temporal = 'not_yet_effective'
        elif start and end and context['target_start']:
            temporal = 'covers_target' if start <= context['target_start'] and end >= context['target_end'] else 'partly_covers_target'
        elif kind == 'operation' and start:
            temporal = 'subsequent_notice_unverified'
        else:
            temporal = 'validity_unknown'
    elif kind == 'policy':
        if published and published > context['as_of']:
            temporal = 'after_cutoff'
        elif re.search(r'有效性\s*[:：]\s*(?:废止|失效|已废止)',body[:1500]):
            temporal = 'expired'
        elif end and end < context['as_of']:
            temporal = 'expired'
        elif start and start > context['as_of']:
            temporal = 'not_yet_effective'
        elif (metadata.get('effectiveness') == 'current_verified' or
              re.search(r'有效性\s*[:：]\s*(?:有效|现行有效)', body[:1500])) and published:
            temporal = 'current_verified'
        else:
            temporal = 'version_unverified'
    elif published and published > context['as_of'] and re.search(r'截至|截止|当时|以.{0,35}为准',question):
        # Historical literature reviews also have an availability cutoff.
        # Forecast valid times can be later; publication itself cannot.
        temporal = 'after_cutoff'
    if temporal not in {'not_time_sensitive', 'covers_target', 'current_verified'}:
        reasons.append({'after_cutoff':'发布晚于查询截至时刻', 'expired':'有效时段已过',
                        'not_yet_effective':'尚未生效', 'publication_unknown':'未核实发布时间',
                        'event_not_matched':'未匹配查询所指的灾害事件，旧响应通知不能证明当前状态',
                        'partly_covers_target':'有效时段仅覆盖部分查询时段',
                        'subsequent_notice_unverified':'尚未核实是否存在后续解除或恢复通知',
                        'version_unverified':'尚未核实文件生效及修订废止关系',
                        'validity_unknown':'未核实有效时段'}.get(temporal, temporal))
    # Exclude definite contradictions. Uncertain or broader evidence remains
    # usable only with an explicit boundary; no fabricated blanket refusal.
    use = 'direct'
    if temporal in {'after_cutoff', 'expired', 'not_yet_effective', 'event_not_matched'}:
        use = 'excluded'
    elif object_match == 'mismatch' and kind in {'operation', 'forecast', 'warning'}:
        use = 'excluded'
    elif named_match == 'mismatch' and kind == 'operation':
        use = 'excluded'
    elif geography in {'other_region', 'local_example'} or object_match == 'mismatch':
        use = 'reference'
    elif context['status_request'] and (kind != 'operation' or geography not in {'exact', 'unspecified'} or
                                       temporal not in {'covers_target'} or object_match != 'matched' or named_match != 'matched'):
        use = 'reference'
    elif kind in {'forecast', 'warning'} and temporal != 'covers_target':
        use = 'reference'
    elif kind == 'policy' and temporal != 'current_verified':
        use = 'reference'
    # General safety/definitions can travel across regions; local regulations
    # and runtime status cannot. Preserve that distinction in model metadata.
    if kind == 'knowledge' and context['general_request'] and temporal!='after_cutoff':
        use = 'direct'
    if (kind=='warning' and re.search(r'预警|警报',subquestion or question)
            and not re.search(OPERATION,subquestion or question) and not context['objects']
            and geography in {'exact','unspecified'} and temporal=='covers_target'):
        use='direct'
    scope_note = None
    typed_facet = None
    if scope_gate_enabled():
        # Import locally to avoid a module cycle: the contract uses the
        # geographic/date extractors above, never assess_page itself.
        from .facet_contract import build_contract
        typed_facet = build_contract(question, [{'id': 1, 'question': subquestion or question}])[0]
        if (typed_facet['place_role'] == 'context' and
                typed_facet['kind'] in {'method_steps', 'definition', 'distinction', 'risk_relation', 'explanation'} and
                kind == 'knowledge'):
            reasons = [reason for reason in reasons if reason != '来源未明确覆盖所问地点']
            # A national method can answer a local-background question, while
            # its jurisdiction remains explicit in the card and diagnostics.
            if temporal != 'after_cutoff' and object_match != 'mismatch':
                use = 'direct'
        current_legal = bool(re.search(r'现行|当前有效|是否有效|仍然有效|法定|必须依据|目前适用', subquestion or question))
        if (kind == 'policy' and temporal == 'version_unverified' and
                typed_facet['kind'] in {'basis_document', 'procedure'} and not current_legal and
                geography not in {'other_region', 'local_example'} and object_match != 'mismatch'):
            # This supports an explanation of the cited edition only. It is
            # deliberately not current_verified or a permission to execute.
            use = 'direct'
            scope_note = 'version_unverified'
    result = {'kind': kind, 'use': use, 'geography': geography, 'object_match': object_match,
            'named_object_match':named_match, 'source_named_objects':named,
            'requested_objects': requested, 'source_objects': actual_objects,
            'temporal': temporal, 'published_at': published.isoformat() if published else None,
            'valid_from': start.isoformat() if start else None, 'valid_to': end.isoformat() if end else None,
            'query_as_of': context['as_of'].isoformat(), 'reasons': reasons}
    if typed_facet:
        result.update(facet_kind=typed_facet['kind'], place_role=typed_facet['place_role'])
    if scope_note:
        result.update(scope_note=scope_note, support_scope='cited_edition_explanation_only')
    return result


def reconcile_notices(hits):
    """Prefer later operative notices only for the same resolved object.

    No common-city shortcut: a reopening for another beach/route must never
    cancel an earlier closure. Unknown object identity leaves both references.
    """
    for old in hits:
        meta = old.get('metadata') or {}
        assessment = meta.get('applicability') or {}
        if assessment.get('kind') != 'operation' or not assessment.get('published_at'):
            continue
        names = assessment.get('source_named_objects')
        if not names:
            continue
        stamp = parse_date(assessment['published_at'])
        cutoff = parse_date(assessment['query_as_of'])
        for new in hits:
            other = (new.get('metadata') or {}).get('applicability') or {}
            if (new is old or other.get('kind') != 'operation' or
                    other.get('source_named_objects') != names or
                    other.get('source_objects') != assessment.get('source_objects')):
                continue
            newer = parse_date(other.get('published_at'))
            effective = parse_date(other.get('valid_from'))
            if newer and stamp < newer <= cutoff and effective and effective <= cutoff:
                # Different cities can have facilities with identical names.
                if leaf_places(old.get('title','')) != leaf_places(new.get('title','')):
                    continue
                assessment['use'] = 'excluded'
                assessment['temporal'] = 'superseded_in_retrieved_evidence'
                assessment['reasons'].append('同一对象已有查询截至时刻前生效的后续公告')
                assessment['superseded_by'] = new.get('url')
                break
    return hits


def facet_support(question, part, hit):
    """A lexical hit is not proof that the requested action is in the body."""
    text = str(hit.get('text') or '')
    from .public_service_intents import supports_subject
    if not supports_subject(part, text):
        return False
    crag=(hit.get('metadata') or {}).get('crag_facets')
    if crag and not any(p.get('question')==part for p in crag):return False
    assessment = (hit.get('metadata') or {}).get('applicability')
    if assessment:
        assessment = next((item for item in assessment.get('facets', []) if item['question']==part), assessment)
    if assessment and assessment['use'] == 'excluded':
        return False
    requirements = [
        (r'由谁|谁来|谁决定|责任主体|决定主体|权限', r'由.{1,50}(?:决定|批准|签发|发布)|负责|职责|报.{1,30}批准'),
        (r'程序|流程|手续|审批', r'会商|报请|报送|批准|签发|程序|流程'),
        (r'怎么公告|如何公告|发布方式|怎么公布', r'通过.{1,40}(?:发布|公告|通告)|向社会|网站|广播|短信'),
        (r'降级|降低响应', r'降级|降低|调整.{0,15}(?:级别|等级|响应)'),
        (r'终止|结束响应|解除响应', r'终止|结束响应|解除响应'),
    ]
    for requested, evidence in requirements:
        if re.search(requested, part) and not re.search(evidence, text):
            return False
    if re.search(OPERATION, part) and not re.search(METHOD, part) and assessment:
        if assessment['use'] != 'direct' or assessment['kind'] != 'operation':
            return False
    wanted = business_objects(part)
    if wanted and not set(wanted) & set(business_objects(text)):
        return False
    return True


def coverage_matrix(question, parts, hits):
    result = []
    for index, item in enumerate(parts or [question], 1):
        part = item.get('question', '') if isinstance(item, dict) else item
        supported, reference = [], []
        for citation, hit in enumerate(hits, 1):
            if not facet_support(question, part, hit):
                continue
            assessment = (hit.get('metadata') or {}).get('applicability') or {}
            assessment = next((item for item in assessment.get('facets', []) if item['question']==part), assessment)
            crag=[p for p in (hit.get('metadata') or {}).get('crag_facets',[]) if p.get('question')==part]
            (reference if assessment.get('use') == 'reference' or crag and all(p['grade']=='ambiguous' for p in crag) else supported).append(citation)
        result.append({'id': item.get('id', index) if isinstance(item, dict) else index,
                       'question': part, 'candidate_citations': supported,
                       'reference_citations': reference,
                       'state': 'candidate_evidence' if supported else 'reference_only' if reference else 'not_found'})
    return result
