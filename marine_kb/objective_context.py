"""Carry product input slots across turns, never previous model conclusions.

Only server-produced dataset/window metadata is trusted. An unrelated next
question drops this context. The resolved request is re-read from the NC files.
"""
from datetime import datetime
import math
import re

_DATE = re.compile(r'(?:(20\d{2})[-年/])?(\d{1,2})[-月/](\d{1,2})日?(?:[T\s]*(\d{1,2})(?:[:时点](\d{1,2})?分?))?')
_CLOCK = re.compile(r'(?<!\d)(\d{1,2})(?:[:：](\d{2})|[时点](?:(\d{1,2})分)?)')
_FIELD_LABELS = {'wave': '海浪', 'wind': '海面风', 'pressure': '气压'}


def _map_bounds(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4 or any(isinstance(v, bool) for v in value):
        return None
    try: west, south, east, north = map(float, value)
    except (ValueError, TypeError): return None
    if not all(math.isfinite(v) for v in (west, south, east, north)):
        return None
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    return [west, south, east, north]


def _explicit_map_fields(question):
    """The object after a correction wins over the object being corrected."""
    corrections = list(re.finditer(r'换成|改成|切换到|切换为', question))
    if corrections:
        question = question[corrections[-1].end():]
    fields = []
    if re.search(r'海浪|波高|浪高|涌浪|浪场', question): fields.append('wave')
    if re.search(r'风场|风速|海面风|海上风|10米风|(?:换成|改成|看看|看一下)风(?:[，,。？?]|$)', question): fields.append('wind')
    if '气压' in question: fields.append('pressure')
    return fields


def _window(question, context, now):
    from forecast_runtime.service import period
    from forecast_runtime.storage import BEIJING
    from forecast_runtime.objective_bridge import _duration_hours, _explicit_interval, _relative_dates, _time_text
    start = datetime.fromisoformat(context['start']).astimezone(BEIJING)
    text = _time_text(re.sub(r'不是.{0,80}?[，,]\s*(?:而)?是', '', question))
    interval = _explicit_interval(text, now)
    if interval:
        return interval
    dates = list(_DATE.finditer(text))
    hours = _duration_hours(text, context['hours'])
    if dates or re.search(r'今天|今日|明天|明日|后天', text):
        day, _ = period(_relative_dates(text, now), now=now, hours=hours)
        clock = _CLOCK.search(text)
        start = day if clock else day.replace(hour=start.hour, minute=start.minute)
    clock = _CLOCK.search(text)
    if clock:
        start = start.replace(hour=int(clock[1]), minute=int(clock[2] or clock[3] or 0), second=0, microsecond=0)
    if not 1 <= hours <= 168:
        raise ValueError('客观预报报告支持1至168小时')
    return start, hours


def product_context(value):
    if not isinstance(value, dict):
        return None
    identifier = value.get('dataset_id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,96}', identifier):
        return None
    try:
        start, end = (datetime.fromisoformat(str(value[key])) for key in ('start', 'end'))
        if start.tzinfo is None or end.tzinfo is None:
            return None
        duration = (end-start).total_seconds()/3600
        if not duration.is_integer() or not 1 <= duration <= 168:
            return None
    except (ValueError, KeyError, TypeError):
        return None
    result = {'dataset_id': identifier, 'start': start.isoformat(),
              'end': end.isoformat(), 'hours': int(duration)}
    if value.get('scope') is not None:
        from forecast_runtime.objective_scope import resolve_scope
        supplied = value['scope']
        if not isinstance(supplied, dict):
            return None
        try:
            scope = resolve_scope('' if supplied.get('kind') == 'map_bounds' else supplied.get('label', ''),
                                  bounds=supplied.get('bounds') if supplied.get('kind') == 'map_bounds' else None)
        except ValueError:
            return None
        if any(supplied.get(key) != scope[key] for key in ('id', 'bounds', 'kind', 'version')):
            return None
        result.update(scope=scope, bounds=scope['bounds'])
    if value.get('view_kind') == 'field':
        field = value.get('field')
        bounds = _map_bounds(value.get('bounds'))
        try: valid_time = datetime.fromisoformat(str(value.get('valid_time')))
        except (ValueError, TypeError): return None
        # A map is a single original NC time, not a one-hour aggregation.
        # start/end remain the established conversation window contract.
        if field not in _FIELD_LABELS or bounds is None or valid_time.tzinfo is None or valid_time != start or duration != 1:
            return None
        if result.get('scope') and bounds != result['scope']['bounds']:
            return None
        result.update(view_kind='field', field=field, valid_time=valid_time.isoformat(), bounds=bounds)
    elif value.get('view_kind') == 'maximum':
        field = value.get('field')
        bounds = _map_bounds(value.get('bounds'))
        if field not in _FIELD_LABELS or bounds is None or result.get('scope') and bounds != result['scope']['bounds']:
            return None
        result.update(view_kind='maximum',field=field,bounds=bounds)
    elif value.get('view_kind') not in (None, '', 'report'):
        return None
    return result


def product_question_plan(question, history=None, *, now=None):
    from forecast_runtime.objective_bridge import objective_question
    from forecast_runtime.storage import local_now
    from .evidence_applicability import PATHS, leaf_places
    from .conversation import followup_question, resolve_turn
    from .facet_contract import build_contract, facet_contract_enabled
    q = str(question or '').strip()
    previous = (history or [])[-1] if history else {}
    previous = previous if isinstance(previous, dict) else {}
    context = product_context(previous.get('objective_context'))
    elliptical = (len(q) <= 90 and
                  bool(re.match(r'^(?:那|那么|请问|请)?(?:最大|最高|最低|北部|中部|南部|浪高|波高|风速|气压|图件|报告|哪个|何时|几点)',q)) and
                  bool(re.search(r'浪高|波高|风速|气压|北部|中部|南部|图件|报告', q)) and
                  bool(re.search(r'最大|最高|最低|多少|几点|何时|哪个|对比|比较|下载', q)))
    field_context = bool(context and context.get('view_kind') == 'field')
    maximum_context = bool(context and context.get('view_kind') == 'maximum')
    report_request = bool(re.search(r'(?:生成|制作|输出|再做|改为|给我).{0,16}(?:报告|专报|简报|分析)|(?:[\d一二两三四五六七]+\s*(?:小时|天)).{0,12}(?:报告|专报|简报)|海况简报|风浪分析', q))
    reference = followup_question(q) or elliptical or bool(re.search(r'同一批次和时段|同批次|同一时段|同一时刻|改成|换成|更正|最新批次', q)) or bool(field_context and report_request)
    # A different business question is never made a grid product merely by
    # adding a previous product's name. Mixed products are explicitly scoped.
    unrelated = bool(re.search(r'什么是|是什么意思|定义|原理|台风|预警|警报|响应|赶海|游泳|海钓|停航|救助|人口|地形', q))
    has_product = bool(re.search(r'图件|报告|海浪图|风场图|气压图|客观预报',q))
    mixed = bool(context and reference and unrelated and has_product and not re.search(r'什么是|是什么意思|定义|原理',q))
    geo_text = re.sub(r'(?:参考|统计)?分区|参考(?:统计)?框|北部|中部|南部', '', q)
    explicit_places = leaf_places(geo_text)
    outside = [name for name in explicit_places if name != '浙江' and '浙江' not in PATHS.get(name,())]
    foreign = bool(re.search(r'[\u4e00-\u9fff]{1,10}(?:海岸|海峡|海湾|国)|太平洋|大西洋|印度洋|北冰洋',geo_text)) and not any(name in geo_text for name in ('浙江','舟山','宁波','台州','温州','嘉兴'))
    unsupported = bool(outside or foreign)
    inherit = bool(context and reference and not unrelated and not unsupported)
    if unsupported and (objective_question(q) or context and reference):
        return {'normalized_question':q, 'normalizer':'objective_scope_boundary', 'retrieval_queries':[],
                'subquestions':[], 'sid':None, 'context_used':False, 'history_messages_used':0,
                'clarification':'当前客观报告计算范围是浙江近海参考框；这次所问地区不在该报告范围内，未改用浙江结果代答。请在地图选择大范围图查看，或明确要计算的浙江参考范围。'}
    if mixed:
        return {'normalized_question':q, 'normalizer':'objective_mixed_scope', 'retrieval_queries':[],
                'subquestions':[], 'sid':None, 'context_used':False, 'history_messages_used':0,
                'clarification':'这条追问同时包含客观产品和另一项业务查询。报告、图件可从上一条的下载入口取得；请将预警或台风动态部分单独提问，系统会查询对应来源，不用NC场代替。'}
    if unrelated:
        # Return a standalone plan so the generic history normalizer cannot
        # reattach the previous NC report to this new business question.
        return {'normalized_question':q, 'normalizer':'objective_context_new_task',
                'retrieval_queries':[q], 'subquestions':[q], 'sid':None,
                'context_used':False, 'history_messages_used':0} if context else None
    if not objective_question(q) and not inherit:
        return None
    normalized = q
    if inherit:
        identifier = re.search(r'(?:数据集|dataset)(?:ID|编号)?\s*[:：=]?\s*([A-Za-z0-9_-]{8,96})', q, re.I)
        latest = bool(re.search(r'(?:改成|换成|使用|采用).{0,5}最新(?:的)?(?:数据|批次)|最新批次',q))
        dataset = identifier[1] if identifier else None if latest else context['dataset_id']
        start, hours = _window(q, context, now or local_now())
        from forecast_runtime.objective_bridge import _time_text, _maximum_query
        explicit_duration = re.search(r'(\d{1,3})\s*(?:小时|天)', _time_text(q))
        # Switching to a report is an explicit change of product. An old
        # one-hour map window must never become an accidental one-hour report.
        explicit_fields = _explicit_map_fields(q)
        # A request for multiple fields becomes a data product for the same
        # window; it cannot silently keep only the old map's field.
        explicit_snapshot = bool(re.search(r'单时刻|单时次|快照|(?:\d{1,2}[时点]|\d{1,2}:\d{2}).{0,12}(?:图|分布)',q)) and not _maximum_query(q)
        maximum_mode = bool((maximum_context and not report_request and not explicit_snapshot) or _maximum_query(q))
        field_mode = (field_context and hours == 1 or maximum_context and explicit_snapshot) and not report_request and not maximum_mode and len(explicit_fields) <= 1
        if field_mode:hours=1
        if field_context and report_request and not explicit_duration:
            hours = 72
        field = (explicit_fields[0] if explicit_fields else None) or context.get('field')
        from forecast_runtime.objective_scope import resolve_scope
        named_scope = bool(re.search(r'浙江|舟山|朱家尖|宁波|台州|温州|嘉兴|地图范围', q))
        scope = resolve_scope(q) if named_scope else context.get('scope')
        # Historical field contexts only carried a rectangle. Keep that exact
        # rectangle until the user supplies a new named scope or map bounds.
        bounds = scope['bounds'] if scope else context.get('bounds') if field_mode or maximum_mode else None
        map_scope = bool(scope and scope['kind'] == 'map_bounds' or not scope and (field_mode or maximum_mode))
        place = scope['label'] if scope and scope['kind'] != 'map_bounds' else '浙江'
        scope_marker = ('地图范围：' + ','.join(format(v, '.12g') for v in bounds) + '，') if map_scope and bounds else ''
        # Chinese explicit clock keeps the legacy date parser's inclusive
        # sampling convention out of this continuous product interval.
        normalized = ((place + '客观预报地图查询，' if field_mode else place + '客观预报，') +
                      ('数据集ID ' + dataset + '，' if dataset else '') +
                      ('地图要素：' + _FIELD_LABELS[field] + '，' if field_mode or maximum_mode and field else '') +
                      ('最大值模式：逐格最大值图，' if maximum_mode else '') + scope_marker + '从' +
                      start.strftime('%Y年%m月%d日%H时%M分') +
                      f'起未来{hours}小时。本轮问题：' + q)
    state = resolve_turn(normalized, now=now)
    parts = [q]
    result = {'normalized_question': normalized, 'normalizer': 'objective_input_slots',
              'retrieval_queries': [q], 'subquestions': parts, 'sid': None,
              'context_used': inherit, 'history_messages_used': 1 if inherit else 0}
    if inherit and field_mode:
        result['objective_view_kind'] = 'field'
        result['objective_field'] = field
        result['objective_bounds'] = bounds
    if facet_contract_enabled():
        facets = build_contract(normalized, parts, state)
        result.update(subquestions=facets, facets=facets, facet_contract=facets,
                      turn_state=state.as_dict(), inherited_fields=(['objective_dataset', 'objective_window'] +
                          (['objective_field', 'objective_bounds'] if field_mode else [])) if inherit else [],
                      turn_state_fingerprint=state.fingerprint())
    return result
