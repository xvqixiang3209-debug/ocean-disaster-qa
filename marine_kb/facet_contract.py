"""Deterministic per-question scope contracts shared by retrieval and review.

These records describe what was asked, never what an answer contains.  ``id``
remains the legacy integer; ``fid`` and ``key`` are stable machine identifiers.
No question numbers, answer templates or external calls belong in this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import os
from datetime import datetime, timedelta

from .evidence_applicability import CN, business_objects, leaf_places, parse_date, question_context

FACET_KINDS = ('definition', 'distinction', 'basis_document', 'procedure',
               'eligibility', 'method_steps', 'channel', 'risk_relation',
               'numeric_value', 'status_at_time', 'historical_fact',
               'calculation', 'explanation')
GAP_TYPES = ('evidence_not_found', 'jurisdiction_rule_not_found',
             'live_status_not_available', 'individual_determination_required',
             'business_data_missing', 'calculation_input_missing')

_OBJECTS = (
    '过渡期生活救助', '冬春救助', '巨灾保险赔付', '公共巨灾保险', '巨灾保险',
    '倒损住房恢复重建救助', '倒损住房救助', '应急生活救助', '政府救助',
    '重力式网箱', '深水网箱', '网箱', '池塘', '缆绳', '框架', '网衣',
    '堤埂', '进排水设施', '备用设备', '锚泊系统', '台风路径', '台风预警',
    '海浪警报', '风暴潮警报', '海洋预警', '蚊虫', '蚊子', '鼠类', '老鼠',
    '登革热', '基孔肯雅热', '乙脑', '出血热', '钩体病', '增水', '潮位',
    '浪高', '风速', '水质', '淹没范围', '转移人数',
)
_OUTPUTS = {
    'definition': ['definition'], 'distinction': ['verdict', 'comparison'],
    'basis_document': ['document_identity', 'basis_content'],
    'procedure': ['actors', 'steps'], 'eligibility': ['conditions', 'application_path'],
    'method_steps': ['steps'], 'channel': ['channel_name', 'access_path'],
    'risk_relation': ['relation', 'conditions'],
    'numeric_value': ['value', 'unit', 'place', 'time', 'provenance'],
    'status_at_time': ['status', 'place', 'valid_time', 'provenance'],
    'historical_fact': ['fact', 'event_time', 'provenance'],
    'calculation': ['formula', 'inputs', 'result_or_input_gap'],
    'explanation': ['answer', 'support'],
}
_JURISDICTION = re.compile(
    r'当地规定|地方.{0,8}(?:规定|标准|预案|要求)|本地预案|按.{0,12}预案|'
    r'现行|签发|由谁|谁(?:来|决定|批准)|金额|补助标准|赔偿标准|申请窗口|'
    r'开放|停运|停航|复航|回港|名单|准入|法定|强制')
_LIVE = re.compile(r'当前|现在|今天|今日|明天|实时|截至|截止|20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}')
_COUNT = re.compile(r'几个|几条|几艘|几家|几处|几座|几人|多少(?:个|条|艘|家|处|座|人|台风|预警|记录|船舶|渔船|航次)|数量|总数|人数')


def _requested_time_fields(text):
    """Return requested clocks and mask them before detecting object fields.

    A position-record *time* is metadata, not a request for its coordinates.
    Keeping these tokens distinct also prevents retrieval time from filling a
    requested observation timestamp or the user's query cutoff.
    """
    patterns = (
        ('snapshot_time', r'(?:快照|接口|数据)(?:的)?(?:取得|获取|抓取|下载)(?:的)?(?:时间|时刻)|快照(?:时间|时刻)'),
        ('record_time', r'(?:(?:最新)?(?:的)?(?:位置|观测|中心|业务数据|台风))?(?:的)?(?:记录|观测|更新)(?:的)?(?:时间|时刻)'),
        ('query_time', r'(?:查询|统计)(?:的)?(?:截至|截止)?(?:时间|时刻)|(?:截至|截止)(?:的)?(?:时间|时刻)'),
    )
    fields, masked = [], text
    for field, pattern in patterns:
        if re.search(pattern, masked):
            fields.append(field)
            masked = re.sub(pattern, ' ', masked)
    return fields, masked


def delivery_shape(text, parent_text=''):
    """Declare requested fields, not the values returned by a tool.

    List counts have a statistical scope and an as-of time; metres and a
    municipal jurisdiction are not their units. Storm motion instead has an
    object identity and coordinates. Physical forecasts keep the strict
    place/time/unit contract below.
    """
    if re.search(r'定义|含义|是什么意思|什么是|怎么计算|如何计算|计算公式|怎么算', text):
        return {}
    time_fields, field_text = _requested_time_fields(text)
    contextual_storm = bool(re.search(r'台风|热带气旋', text + ' ' + parent_text))
    count = bool(_COUNT.search(field_text))
    if time_fields and not re.search(r'几个|几条|几艘|几家|几处|几座|几人|多少', field_text):
        # "活动数量的统计截止时刻" asks for the clock, not a second
        # count. Explicit "数量是多少，查询时间是什么" still asks both.
        count = bool(re.search(r'(?:数量|总数|人数).{0,5}(?:是多少|几|多少)|(?:查询|给出|列出).{0,8}(?:数量|总数|人数)', field_text))
    # "台风预警有几条" counts warnings, not tropical cyclones. The same
    # distinction applies to storm-track points, stations and documents.
    storm_count = count and not re.search(r'预警|警报|通报|预报产品|文件|台账|路径点|测站|站点|岸段|船|航次|'
                                         r'(?:路径|轨迹|位置|观测)(?:点|记录)|(?:资料|文件|台风)记录(?:数|量)', field_text)
    identity = bool(re.search(r'叫什么|分别叫|名称|名字|哪些台风|什么台风|哪个台风', field_text))
    dynamic = contextual_storm and (bool(time_fields) or identity or bool(re.search(r'活动|实时|当前|现在|最新|位置|强度|移动|中心|风速|气压|几个|数量|总数', field_text)))
    if not dynamic:
        if time_fields:
            return {'domain': 'record_metadata', 'quantity_type': 'metadata',
                    'required_outputs': [*time_fields, 'provenance']}
        return {'quantity_type': 'count'} if count else {}
    fields = []
    if storm_count:
        fields += ['count', 'statistical_scope', 'as_of', 'provenance']
    requested = []
    if re.search(r'位置|在哪里|经纬度|中心(?:位于|在哪)', field_text):
        requested.append('position')
    if re.search(r'强度|几级|级别', field_text):
        requested.append('intensity')
    if re.search(r'最大风速|中心风速', field_text):
        requested.append('max_wind_speed')
    if re.search(r'风力', field_text):
        requested.append('wind_scale')
    if re.search(r'气压', field_text):
        requested.append('pressure')
    if re.search(r'移动方向|移动.{0,5}方向|移向|往哪|向哪里', field_text):
        requested.append('motion_direction')
    if re.search(r'移动速度|移速', field_text):
        requested.append('motion_speed')
    if requested:
        fields += ['object_identity', *requested, 'valid_time', 'provenance']
    elif identity:
        fields += ['object_identity', 'provenance']
    fields += [*time_fields, 'provenance'] if time_fields else []
    return {'domain': 'typhoon_dynamics', 'quantity_type': 'count' if storm_count else 'dynamic' if requested or identity else 'metadata',
            'required_outputs': list(dict.fromkeys(fields))} if fields else ({'quantity_type': 'count'} if count else {})


def facet_contract_enabled():
    return os.getenv('MARINE_FACET_CONTRACT', '').strip().casefold() in {'true', '1', 'yes', 'on'}


def object_names(text):
    """Retain explicit technical objects, dropping redundant shorter aliases."""
    names = [name for name in _OBJECTS if name in text]
    names = [name for name in names if not any(name != other and name in other for other in names)]
    return names + [name for name in business_objects(text) if name not in names]


def infer_kind(text, suggested=None):
    """Rules win over a planner suggestion, particularly for status/amounts."""
    if re.search(r'是不是一回事|是否一回事|区别|差别|不同|不一样|能否替代|能替代|能.{0,4}替代|分别.{0,4}含义|偏重还是偏轻|比较像', text):
        return 'distinction'
    if re.search(r'依据什么|依据是(?:什么|哪些)|依据哪些|按什么(?:文件|规定)|依据文件|政策依据|依据.*办理|以何为据|现行.{0,25}(?:标准|规范).{0,8}哪些', text):
        return 'basis_document'
    if re.search(r'(?:会不会|能不能).{0,6}(?:传染|传播|得病)|是否.{0,6}(?:说明|意味着)|风险关系|是否必然|会传什么病|会得', text):
        return 'risk_relation'
    if re.search(r'哪里.{0,5}查|在哪.{0,6}查|在哪里.{0,8}(?:收到|获得|获取)|渠道|查询入口|查询.{0,10}方式|如何查询|怎么查询|怎样查询|如何获取|怎么收到预警', text):
        return 'channel'
    if re.search(r'程序|流程|手续|办理步骤|怎么申请|如何申请|由谁|谁来|谁决定|谁批准|向谁申请|找谁|启动条件|终止条件|降级条件|什么时候.{0,8}响应|何时.{0,8}响应', text):
        return 'procedure'
    if (re.search(r'什么是|是什么|定义|含义|是什么意思|怎么划分|怎么分[？?]?$|分别指哪里', text) and
            not re.search(r'是多少|有多少|值是多少|最新|当前|目前|时间进度|哪一次|什么情况|统计口径|计损口径|判据', text)):
        return 'definition'
    if re.search(r'多少|多大|最高|最大|最低|最小|几个|几条|几人|几厘米|几米|各站|分站|数值(?!模式)|总量|面积|提前量', text):
        return 'numeric_value'
    if re.search(r'怎么计算|如何计算|计算公式|怎么算|计算方法', text):
        return 'calculation'
    # A question about an official procedure remains a procedure even when
    # asking for its current edition; a date does not turn every facet live.
    if re.search(r'申请什么|申请哪些|能申请|可以申请|能赔|会赔|赔吗|资格|是否符合|有没有资格|能领取|能拿|能获得', text):
        return 'eligibility'
    if re.search(r'当前|是否已经|已经.{0,10}了吗|发布了哪些|是否开放|开放吗|开放了|停航了吗|复航了吗|能去|还能去|能否出海|可以出海|已暴发|最新.{0,12}(?:位置|强度|方向|时刻)', text):
        return 'status_at_time'
    if (_LIVE.search(text) and business_objects(text) and
            re.search(r'能否|可以|能不能|可不可以|适合|能.{0,8}吗', text)):
        return 'status_at_time'
    if re.search(r'检查|加固|防叮咬|防蚊|清理|怎么防|如何防|怎么做|怎么.{0,12}(?:防|清理|准备)|步骤|如何.{0,12}(?:防|清理|准备)', text):
        return 'method_steps'
    if re.search(r'历史|曾经|当时|(?:19|20)\d{2}年.{0,20}(?:损失|受灾|造成|登陆)', text):
        return 'historical_fact'
    if re.search(r'怎么|如何|怎样|怎么办|要准备|要注意|重点巡查|需要哪些输入|该准备', text):
        return 'method_steps'
    return suggested if suggested in FACET_KINDS else 'explanation'


def _local_place_role(text, kind, places):
    if not places:
        return 'none'
    if kind in {'eligibility', 'status_at_time', 'numeric_value', 'channel'} or _JURISDICTION.search(text):
        return 'jurisdiction_required'
    # "标准怎么定义" alone is not a request for an adopted municipal norm.
    if re.search(r'(?:地方|当地|本地|现行|适用).{0,6}(?:标准|规则|规范)', text):
        return 'jurisdiction_required'
    return 'context'


def allowed_gaps(kind, place_role, text=''):
    result = {'evidence_not_found'}
    if place_role == 'jurisdiction_required' and kind in {'basis_document', 'procedure', 'eligibility', 'explanation'}:
        result.add('jurisdiction_rule_not_found')
    if kind == 'eligibility':
        result.add('individual_determination_required')
    if kind == 'status_at_time':
        result.add('live_status_not_available')
    if kind in {'numeric_value', 'historical_fact'}:
        result.add('business_data_missing')
    if kind == 'calculation':
        result.add('calculation_input_missing')
        result.add('business_data_missing')
    # Exact locally adopted requirements are not optional merely because the
    # main action uses the words "how" or "steps".
    if place_role == 'jurisdiction_required' and _JURISDICTION.search(text) and kind == 'method_steps':
        result.add('jurisdiction_rule_not_found')
    return sorted(result)


def resolve_time_scope(text, previous_time=None, now=None):
    """Resolve a short date using the prior explicit year without moving now."""
    clock = parse_date(now) or datetime.now(CN)
    prior = parse_date((previous_time or {}).get('start'))
    year = prior.year if prior else clock.year
    expanded = text
    if not re.search(r'20\d{2}[-年/]', expanded):
        expanded = re.sub(r'(?<!\d)(\d{1,2}月\d{1,2}日)', lambda match: f'{year}年{match[1]}', expanded, count=1)
    if '后天' in expanded:
        expanded = expanded.replace('后天', (clock + timedelta(days=2)).strftime('%Y年%m月%d日'))
    context = question_context(expanded, now=clock)
    return {key: context.get(name).isoformat() if context.get(name) else None
            for key, name in [('start', 'target_start'), ('end', 'target_end'), ('as_of', 'as_of')]}


def _parts(question, supplied):
    if supplied:
        return [dict(item) if isinstance(item, dict) else {'question': str(item)} for item in supplied]
    parts = [part.strip(' ，,；;') for part in re.split(r'[？?。\n]+', question) if part.strip(' ，,；;')]
    return [{'question': part} for part in (parts or [question])]


def build_contract(question, planner_parts=None, history_state=None):
    """Build JSON-friendly records; callers preserve these through every stage.

    Existing planner IDs are retained. Parent places fill omitted child places;
    explicit child places replace parent places, preventing sibling leakage.
    ``history_state`` only carries already resolved entities, not old answers.
    """
    state = history_state.as_dict() if hasattr(history_state, 'as_dict') else history_state or {}
    parent_places = leaf_places(question) or list(state.get('places') or [])
    parent_objects = object_names(question) or list(state.get('objects') or [])
    parent_time = state.get('time')
    if _LIVE.search(question) or re.search(r'\d{1,2}月\d{1,2}日|昨天|后天', question):
        parent_time = resolve_time_scope(question, parent_time)
    rows = []
    used_ids = set()
    for ordinal, part in enumerate(_parts(question, planner_parts), 1):
        text = str(part.get('question') or part.get('text') or '').strip()
        if not text:
            continue
        identity = part.get('id', ordinal)
        if not isinstance(identity, int) or isinstance(identity, bool) or identity < 1 or identity in used_ids:
            identity = ordinal
            while identity in used_ids:
                identity += 1
        used_ids.add(identity)
        kind = infer_kind(text, part.get('kind'))
        child_places = leaf_places(text) or parent_places
        role = _local_place_role(text, kind, child_places)
        explicit_time = bool(_LIVE.search(text) or re.search(r'\d{1,2}月\d{1,2}日|昨天|后天', text))
        time_scope = parent_time
        if explicit_time:
            time_scope = resolve_time_scope(text, state.get('time'))
        shape = delivery_shape(text, question)
        if shape.get('quantity_type') == 'count' and kind not in {'channel', 'procedure', 'distinction', 'definition', 'method_steps', 'calculation'}:
            kind = 'numeric_value'
        elif shape.get('domain') in {'typhoon_dynamics', 'record_metadata'} and shape.get('quantity_type') in {'dynamic', 'metadata'}:
            kind = 'status_at_time'
        outputs = shape.pop('required_outputs', None)
        if shape.get('quantity_type') == 'count' and outputs is None and kind == 'numeric_value':
            outputs = ['count', 'statistical_scope', 'as_of', 'provenance']
        if shape.get('domain') == 'typhoon_dynamics':
            role = 'statistical_scope' if shape.get('quantity_type') == 'count' else 'object_coordinates'
        key_material = {'text': re.sub(r'[\s？?。；;，,]', '', text), 'kind': kind,
                        'objects': object_names(text) or parent_objects, 'places': child_places}
        key = hashlib.sha1(json.dumps(key_material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        rows.append({'id': identity, 'fid': f'f{identity}', 'key': key, 'question': text, 'text': text,
                     'kind': kind, 'objects': key_material['objects'], 'places': list(child_places),
                     'place_role': role, 'time': time_scope, 'required_outputs': outputs or list(_OUTPUTS[kind]),
                     'allowed_gaps': allowed_gaps(kind, role, text), 'parent': part.get('parent'), **shape})
    return rows


def by_fid(contract):
    return {str(row['fid']): row for row in contract}
