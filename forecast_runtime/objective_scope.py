"""Versioned place resolution for objective products, without silent scope changes.

Reference boxes are not administrative sea boundaries. An unboxed name needs
an explicit parent notice or clarification. No new coordinates are inferred.
"""
from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import re

SCOPE_VERSION = 'zhejiang_reference_boxes_v2'
LEGACY_SCOPE_VERSION = 'zhejiang_reference_boxes_v1'
ZHEJIANG_BOUNDS = (119.5, 26.5, 123.5, 31.5)
REGIONS = (
    ('north', '北部参考统计框', (120.5, 29.5, 123.5, 31.5)),
    ('central', '中部参考统计框', (120.0, 28.0, 123.5, 29.5)),
    ('south', '南部参考统计框', (119.5, 26.5, 123.5, 28.0)),
)
_DISCLAIMER = '本范围是版本化经纬度参考统计框，不是行政海域边界；网格结果不等同于岸边、浴场或钓点实况。'
_GENERIC = {'近海', '海域', '沿海', '附近', '一带', '当地', '这里', '那里', '本地', '目标',
            '地图', '这个', '那个', '所在', '对应', '指定', '全部', '所有', '范围', '区域',
            '统计', '分区', '参考', '海浪', '波浪', '风浪', '海面', '海上', '同一', '其他',
            '周边', '沿岸', '近岸', '小时', '分钟', '秒钟', '日', '时', '分', '秒', '时段', '期间',
            '这次', '本次', '此次', '今后', '接下来'}
_CORRECTION = re.compile(r'换成|改成|切换到|切换为|改为|改看|换到')
_BOUND_MARKER = re.compile(r'地图范围\s*[:：]\s*([-+\d.eE]+\s*,\s*[-+\d.eE]+\s*,\s*[-+\d.eE]+\s*,\s*[-+\d.eE]+)')


@lru_cache(maxsize=1)
def _registry():
    data = json.loads(Path(__file__).with_name('objective_places.json').read_text(encoding='utf-8'))
    if data.get('version') != SCOPE_VERSION:
        raise ValueError('地点注册表版本与客观预报范围版本不一致')
    rows = {row['id']: row for row in data['places']}
    aliases = {name: row['id'] for row in rows.values() for name in row['names']}
    # Existing PATHS containment is reused; it does not supply calculation boxes.
    from marine_kb.evidence_applicability import PATHS
    for name, path in PATHS.items():
        if name in aliases:
            continue
        parent = next((aliases[value] for value in reversed(path) if value in aliases), None)
        key = 'path:' + name
        rows[key] = {'id': key, 'names': [name], 'bounds': None, 'parent': parent,
                     'outside': name != '浙江' and '浙江' not in path,
                     'source': 'Existing PATHS containment; reference box not supplied'}
        aliases[name] = key
    for name in ('北京', '重庆', '四川', '云南', '贵州', '湖南', '湖北', '江西', '安徽',
                 '河南', '山西', '陕西', '吉林', '黑龙江', '内蒙古', '宁夏', '甘肃',
                 '青海', '新疆', '西藏', '台湾', '香港', '澳门', '日本', '韩国', '越南', '新加坡',
                 '太平洋', '大西洋', '印度洋', '北冰洋'):
        if name not in aliases:
            key = 'outside:' + name
            rows[key] = {'id': key, 'names': [name], 'bounds': None, 'outside': True}
            aliases[name] = key
    return rows, aliases


def _validate_bounds(value):
    from .objective import _bounds
    if isinstance(value, (list, tuple)) and any(isinstance(v, bool) for v in value):
        raise ValueError('bounds必须为有限经纬度数值，不能为布尔值')
    return _bounds(value)


def _canonical_name(identifier):
    return _registry()[0][identifier]['names'][0]


def _clean_candidate(value):
    value = re.sub(r'(?:附近|一带|沿海|近海|海域|沿岸|周边)$', '', value)
    value = re.sub(r'(?:(?:未来|今后|接下来|随后|最近|今起|近)?[一二两三四五六七八九十百零〇\d]+(?:个)?(?:小时|天|日|周)(?:的)?)', '', value)
    prefix = r'^(?:请帮我|麻烦帮我|请问|请|帮我|给我|绘制|画出|生成|制作|展示|显示|查看|看看|查询|对比|比较|同一时刻|同一时段|明天|今天|今日|明日|后天|未来|今后|接下来|开始|早晨|早上|上午|中午|下午|傍晚|晚上|凌晨|夜间|今晨|当前|现在|换成|改成|切换到|切换为|改为|改看|不是|而是|以及|还有|或者|还是|中国|和|与|及|在|从|到|看|将|把|的)+'
    previous = None
    while previous != value:
        previous = value
        value = re.sub(prefix, '', value)
    if re.fullmatch(r'[一二两三四五六七八九十百零〇\d]+(?:天|日|小时|时|分钟|分|周|个月)', value):
        return ''
    aliases = _registry()[1]
    # A numeric date/clock breaks the CJK run, leaving e.g. "时浙江近海".
    # Strip the clock unit only before an actual registered geographical name.
    names = '|'.join(re.escape(name) for name in sorted(aliases, key=len, reverse=True))
    value = re.sub(r'^(?:小时|分钟|秒钟|日|时|点|分|秒)+(?=' + names + ')', '', value)
    return value


def _residual_place_slots(text, aliases, *, allow_bare=False):
    """Conservatively detect unregistered names in explicit product slots.

    A geographic suffix is not required: 镇海/霞浦/鹤浦 can occupy the same
    place slot as 舟山. Only material before a requested element (after masking
    dates/clocks) or an explicit place correction is inspected. This is a
    clarification guard, not a geocoder and never supplies a parent or bounds.
    """
    fields = r'海面风|海上风|海浪|波浪|波高|浪高|风浪|风场|浪场|风速|气压|海况|阵风'
    explicit = bool(re.search(r'绘制|画出|显示|展示|生成|制作|查看|看看|查询|给我|地图|二维|报告|简报|图件|分布图|今天|今日|明天|明日|后天|未来|今后|\d[年月日时点]', text))
    correction = bool(_CORRECTION.search(text))
    if not (allow_bare or correction or explicit and re.search(fields, text)):
        return []
    # Keep offsets stable so these mentions can be ordered with registry hits.
    temporal = (r'(?:(?:未来|今后|接下来|随后|最近|今起|近)?[一二两三四五六七八九十百零〇\d]+(?:个)?(?:小时|天|日|周)(?:的)?)'
                r'|(?:今天|今日|明天|明日|后天|今晚|明早|今晨|早晨|早上|上午|中午|下午|傍晚|晚上|凌晨|夜间)'
                r'|\d+(?:年|月|日|号|时|点|分|秒)|\d{1,2}[:：]\d{2}|\d{4}[-/]\d{1,2}[-/]\d{1,2}')
    scrubbed = re.sub(temporal, lambda match: ' ' * len(match.group()), text)
    spans = []
    for match in re.finditer(fields, scrubbed):
        # A date can split the location from the field. Inspect all CJK runs in
        # that product clause's prefix, rather than only the last adjacent run.
        start = max(scrubbed.rfind(mark, 0, match.start()) for mark in '，,。；;\n') + 1
        for chunk in re.finditer(r'[\u4e00-\u9fff]{2,30}', scrubbed[start:match.start()]):
            spans.append((start + chunk.start(), chunk.group()))
    if allow_bare or correction:
        # A place correction occupies the first target clause. Later clauses
        # may change a date/dataset or say "其他不变"; they are not place slots.
        targets = list(_CORRECTION.finditer(scrubbed))
        start = targets[-1].end() if targets else 0
        first = re.split(r'[，,。；;\n]', scrubbed[start:], maxsplit=1)[0]
        if not re.search(r'数据集|dataset|批次|模式', first, re.I):
            for chunk in re.finditer(r'[\u4e00-\u9fff]{2,30}', first):
                spans.append((start + chunk.start(), chunk.group()))
    generic = (r'本轮问题|地图要素|最大值模式|数据集|有效时间|查询时刻|起报时间|北京时间|选择|使用|其他不变|其余不变|'
               r'请帮我|麻烦帮我|我想知道|我想看|我想|能不能|可不可以|请问|请|帮我|给我|一份|一张|一幅|'
               r'绘制|画出|生成|制作|展示|显示|查看|看看|查询|对比|比较|切换到|切换为|换成|改成|改为|改看|换到|'
               r'同一时刻|同一时次|同一时段|同一时间|同一批次|同批次|同一个|同一|最新批次|最新|当前|现在|实时|'
               r'最大值|最小值|最大|最小|最高|最低|平均值|平均|均值|单时刻|单时次|多时刻|逐格|逐时|时段|过程|'
               r'客观预报|客观|预报|参考统计框|参考框|参考|统计|分区|各区域|各地区|各区|区域|各地|北部|中部|南部|'
               r'海面风|海上风|海浪|波浪|波高|浪高|风浪|风场|浪场|风速|气压|海况|阵风|'
               r'二维|三维|地图|图件|图片|图像|分布图|分布|快照|报告|简报|产品|结果|图|'
               r'近海|沿海|海域|附近|沿岸|近岸|周边|一带|范围|地图范围|当前|未来|今后|开始|小时|分钟|'
               r'怎么样|怎样|如何|怎么|哪里|在哪|多少|哪个|这些|那个|这个|本地|当地|这里|那里|所在|对应|目标|'
               r'解读|分析|说明|解释|这次|此次|本次|全部|所有|还有|以及|或者|还是|同时|分别|都|一下|中国|的|和|与|及|在|从|到|看|将|把|用|各|起|并')
    found = []
    for start, raw in spans:
        value = _clean_candidate(raw)
        for alias in sorted(aliases, key=len, reverse=True):
            value = value.replace(alias, '')
        value = re.sub(generic, '', value)
        # Long residual instruction clauses need a parser, not a guessed place.
        if not 2 <= len(value) <= 10 or not re.fullmatch(r'[\u4e00-\u9fff]+', value):
            continue
        if value not in _GENERIC and value not in [item[1] for item in found]:
            found.append((start, value))
    return found


def _mentions_raw(text, *, allow_bare=False):
    """Ordered leaf names, including unsupported geographical suffixes."""
    rows, aliases = _registry()
    candidates, known_spans = [], []
    pattern = '|'.join(re.escape(name) for name in sorted(aliases, key=len, reverse=True))
    for match in re.finditer(pattern, text):
        key = aliases[match.group()]
        known_spans.append((match.start(), match.end(), key))
        candidates.append((match.start(), _canonical_name(key), key))
    suffix = r'(?:街道|列岛|群岛|半岛|码头|海峡|海岸|省|市|县|区(?!别|域|分)|镇|乡|岛|港|湾|湖|沟|海域|附近|一带|沿海|近海)'
    for match in re.finditer(r'[\u4e00-\u9fff]{2,18}?' + suffix, text):
        name = _clean_candidate(match.group())
        if len(name) < 2 or name in _GENERIC:
            continue
        if re.search(r'参考|统计|分区|覆盖|预报|实况|预测|地图|二维|资料|知识|报告|当前|同一|本次|这些|那个|哪个|什么|怎么|如何|此处|这片|那片|片区|数据|时间|有效|全部|所有|选定', name):
            continue
        bare = re.sub(r'(?:街道|省|市|县|区|镇|乡)$', '', name)
        if name in aliases or bare in aliases:
            continue
        rest = bare
        for alias in sorted(aliases, key=len, reverse=True):
            rest = rest.replace(alias, '')
        if not rest:
            continue
        start = match.end() - len(name)
        if any(left <= start and right >= match.end() for left, right, _ in known_spans):
            continue
        # A distinct unsupported bay/island is not the containing county box.
        candidates = [row for row in candidates if not (start <= row[0] < match.end())]
        candidates.append((start, bare, None))
    for start, name in _residual_place_slots(text, aliases, allow_bare=allow_bare):
        candidates.append((start, name, None))
    ordered = []
    for _, name, key in sorted(candidates, key=lambda row: row[0]):
        if name not in [row[0] for row in ordered]:
            ordered.append((name, key))
    def ancestor(parent, child):
        seen = set()
        while child in rows and child not in seen:
            seen.add(child)
            child = rows[child].get('parent')
            if child == parent:
                return True
        return False
    return [name for name, key in ordered if not key or not any(
        other and other != key and ancestor(key, other) for _, other in ordered)]


def place_mentions(text):
    """Ordered geographical leaves, shared by routing and follow-up replacement."""
    q = _BOUND_MARKER.sub('', str(text or ''))
    if '本轮问题：' in q:
        current = q.rsplit('本轮问题：', 1)[1]
        if _mentions_raw(current):
            q = current
    q = re.sub(r'(?:不是|不看|不要).{0,80}?(?:[，,；;]\s*(?:而)?是|而是)', '', q)
    q = re.sub(r'(?:不是|不看|不要)[^，,；;。]*', '', q)
    corrections = list(_CORRECTION.finditer(q))
    if corrections:
        target = q[corrections[-1].end():]
        if _mentions_raw(target, allow_bare=True):
            q = target
    return _mentions_raw(q, allow_bare=bool(corrections))


def _scope(keys):
    rows, _ = _registry()
    keys = list(dict.fromkeys(keys))
    if keys == ['zhejiang']:
        return {'id': 'zhejiang', 'label': '浙江近海参考统计框', 'bounds': list(ZHEJIANG_BOUNDS),
                'regions': [{'id': key, 'name': name, 'bounds': list(box)} for key, name, box in REGIONS],
                'kind': 'reference_box', 'version': SCOPE_VERSION, 'limitations': [_DISCLAIMER]}
    regions = [{'id': key, 'name': rows[key]['names'][0] + '近海参考统计框',
                'bounds': list(rows[key]['bounds'])} for key in keys]
    values = [min(row['bounds'][0] for row in regions), min(row['bounds'][1] for row in regions),
              max(row['bounds'][2] for row in regions), max(row['bounds'][3] for row in regions)]
    limits = [_DISCLAIMER]
    if 'zhujiajian' in keys:
        limits.append('朱家尖采用约0.2°的小范围参考框；原生有效海点不足或无海点时返回覆盖不足，不扩大到舟山或全省，也不以最近陆地点代替。')
    if len(regions) > 1:
        limits.append('各地参考框可能重叠；分区数值不能相加，联合图展示各框外接范围。')
    return {'id': '+'.join(keys), 'label': '、'.join(rows[key]['names'][0] for key in keys) + '近海参考统计框',
            'bounds': values, 'regions': regions, 'kind': 'reference_box', 'version': SCOPE_VERSION,
            'limitations': limits}


def _map_scope(bounds, *, version=SCOPE_VERSION):
    values = _validate_bounds(bounds)
    return {'id': 'map_bounds', 'label': '地图选定范围', 'bounds': values,
            'regions': [{'id': 'map_bounds', 'name': '地图选定参考统计框', 'bounds': list(values)}],
            'kind': 'map_bounds', 'version': version, 'limitations': [_DISCLAIMER]}


def validated_scope(value, *, allow_legacy=True):
    """Validate v1/v2 scope and retain the original window/version for replay."""
    if not isinstance(value, dict):
        return None
    version = value.get('version')
    if version not in ((SCOPE_VERSION, LEGACY_SCOPE_VERSION) if allow_legacy else (SCOPE_VERSION,)):
        return None
    try:
        if value.get('kind') == 'map_bounds':
            if value.get('bounds') is None:
                return None
            expected = _map_scope(value['bounds'], version=version)
        else:
            keys = str(value.get('id', '')).split('+')
            rows, _ = _registry()
            if any(key not in rows or not rows[key].get('bounds') for key in keys):
                return None
            expected = _scope(keys)
            expected['version'] = version
        if any(value.get(key) != expected[key] for key in ('id', 'bounds', 'kind', 'version')):
            return None
        return expected
    except (TypeError, ValueError, KeyError):
        return None


def resolve_scope_request(question, bounds=None, previous=None):
    """Four states: resolved, parent_fallback, needs_scope, out_of_region.

    No-place default/inheritance are resolved with visible reason and notice.
    Caller/map bounds keep API semantics and win over named-place text.
    """
    q = str(question or '')
    marker = _BOUND_MARKER.search(q)
    if bounds is None and marker:
        bounds = marker[1]
    if bounds is None and re.search(r'地图范围\s*[:：]', q):
        raise ValueError('地图范围请使用west,south,east,north四个有限经纬度数值')
    result = {'state': 'resolved', 'reason': 'explicit', 'requested_places': [], 'matched': [],
              'parents': {}, 'unknown': [], 'scope': None, 'notice': '', 'registry_version': SCOPE_VERSION}
    if bounds is not None:
        result.update(scope=_map_scope(bounds), reason='explicit_bounds')
        return result
    names = place_mentions(q)
    result['requested_places'] = names
    if not names:
        supplied = previous.get('scope') if isinstance(previous, dict) and 'scope' in previous else previous
        if previous is not None:
            scope = validated_scope(supplied)
            if scope is None:
                return {**result, 'state': 'needs_scope', 'reason': 'invalid_previous',
                        'notice': '上一轮地点范围无法核对，请重新选择地点或在地图上框选范围。'}
            result.update(scope=scope, reason='inherited', notice='未指定新地点，沿用上一轮' + scope['label'] + '。')
        else:
            result.update(scope=_scope(['zhejiang']), reason='default', notice='未指定地点，按浙江近海参考统计框计算。')
        return result
    rows, aliases = _registry()
    resolved, outside = [], []
    for name in names:
        key = aliases.get(name)
        if not key:
            result['unknown'].append(name)
            continue
        row = rows[key]
        if row.get('outside'):
            outside.append(name)
        elif row.get('bounds'):
            resolved.append(key)
            result['matched'].append(name)
        else:
            seen, parent = {key}, row.get('parent')
            while parent and parent not in seen and parent in rows and not rows[parent].get('bounds'):
                seen.add(parent)
                parent = rows[parent].get('parent')
            if parent in rows and rows[parent].get('bounds'):
                resolved.append(parent)
                result['parents'][name] = _canonical_name(parent)
            else:
                result['unknown'].append(name)
    if outside:
        result.update(state='out_of_region', reason='outside_supported_region', outside=outside,
                      notice='、'.join(outside) + '不在当前浙江近海客观预报服务范围内，未用浙江结果代答。请提供适用数据和明确范围，或选择浙江已登记地点。')
        return result
    if result['unknown']:
        result.update(state='needs_scope', reason='unregistered_place',
                      notice='、'.join(result['unknown']) + '尚无可核对的计算范围，未替换成全省或沿用旧地点。请在地图上框选范围，或选择已登记地点：浙江、嘉兴、宁波、舟山、台州、温州、朱家尖。')
        return result
    if result['parents'] and os.environ.get('OBJECTIVE_UNBOXED_POLICY', 'parent_with_notice') == 'clarify':
        result.update(state='needs_scope', reason='parent_confirmation_required',
                      notice='、'.join(result['parents']) + '未单独登记参考框。可明确选择父级参考框（' + '、'.join(dict.fromkeys(result['parents'].values())) + '），或在地图上框选范围；结果不代表未登记地点当地。')
        return result
    if result['parents']:
        details = '；'.join(name + '未单独登记参考框，按' + parent + '近海参考框计算，不代表' + name + '当地' for name, parent in result['parents'].items()) + '。'
        result.update(state='parent_fallback', reason='known_parent_reference', notice=details)
    result['scope'] = _scope(resolved)
    return deepcopy(result)


def resolve_scope(question, bounds=None):
    """Compatibility wrapper; unsupported requests now fail instead of defaulting."""
    result = resolve_scope_request(question, bounds=bounds)
    if result['scope'] is None:
        raise ValueError(result['notice'])
    scope = result['scope']
    if result['notice']:
        scope['limitations'].append(result['notice'])
    return scope


def available_scopes():
    """Public selector lists only configured calculation windows."""
    return [resolve_scope(name) for name in ('浙江', '嘉兴', '宁波', '舟山', '台州', '温州', '朱家尖')]

