"""Deterministic minimum delivery, independent of a model's ``full`` label.

This is a publication floor, not a factual accuracy score.  It only consumes
already grounded atoms and never promotes an unsupported source into a fact.
"""
import hashlib
import json
import re
import unicodedata


SUBSTANTIVE_TYPES = frozenset({'direct_fact', 'synthesis', 'calculation'})
_DATE_ONLY = re.compile(r'^(?:该|本|此)?(?:方案|通知|文件|预案|办法)?.{0,12}自?\s*20\d{2}年\d{1,2}月\d{1,2}日.{0,14}(?:施行|实施|印发|发布)[。；\s]*$')
_ACTIONS = re.compile(r'检查|加固|清理|排查|固定|撤离|转移|清除|更换|消毒|申报|申请|提交|评议|审核|审批|确定|公示|发放|报送|上报|批准|下达|通知|理赔|赔付|核定|报案|查勘|查询|查看|关注|收听|登陆|登录|设立|观测|翻盆倒罐|倒掉|倒空|换水|换一次水|清扫|疏通|投放|穿(?:着)?|佩戴|涂抹|喷涂|安装|装(?:好|上)?(?:纱窗|纱门)|挂(?:上)?蚊帐|使用(?:蚊帐|驱蚊|防蚊|灭蚊|杀虫)|避开')
_IDENTITY = re.compile(r'《[^》]{2,100}》')
_META_ONLY = re.compile(r'^(?:文件|机关|文号|来源|发布单位|适用地区|版本|施行日期|发布日期)[:：]')
_TIME = re.compile(r'20\d{2}(?:[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?)?|\d{1,2}月\d{1,2}日|\d{1,2}[:：]\d{2}')
_ACTORS = re.compile(r'(?:由|报|向)[^。；，\n]{1,35}(?:部门|机构|政府|村委会|居委会|保险公司|财政部|应急管理部|指挥部|办公室)|(?:村委会|居委会|乡镇|乡级|县级|政府|财政部|应急管理部|指挥部|保险公司|办公室|主管部门).{0,12}(?:负责|审核|批准|确定|受理|赔付|下达|提出|发布|核定)')
_CONDITIONS = re.compile(r'符合|满足|须|需要|条件|若|如果|只有|当.{0,15}时|因灾|困难|不能仅|不能.{0,10}(?:判定|证明)|不等于|并不意味|不代表|根据|按照|依据')

# These checks only reject a confirmed object/intent conflict.  They do not
# establish evidence support or infer missing facts from a source title.
_MARINE = re.compile(r'海洋|海浪|巨浪|风暴潮|海啸|赤潮|海区|海域|海况|自然资源')
_WEATHER = re.compile(r'气象|天气|气象台|热带气旋')
_PERSONAL = re.compile(r'叮咬|长袖|长裤|长衣|纱窗|纱门|蚊帐|驱蚊|防蚊液|驱避|裸露|暴露皮肤|户外|外出|过敏|就医')
_BREEDING = re.compile(r'积水|孳生|滋生|花盆|花盘|水培|水缸|地漏|排水沟|轮胎|翻盆倒罐|瓶罐|水桶')
_INSURANCE = re.compile(r'保险|投保|承保|理赔|赔付|获赔|保单|报案|定损|索赔|保险合同')
_INFECTION_OUTCOME = re.compile(r'疾病|病原|病毒|感染|传染|登革热|基孔肯雅|乙脑|疟疾|寨卡|出血热|钩体病|疫情|暴发|爆发|发病|传播')
_RISK_OUTCOME = re.compile(r'风险|灾害|险情|淹没|' + _INFECTION_OUTCOME.pattern)


def _question(facet):
    return str(facet.get('question') or facet.get('text') or '')


def _risk_outcome(text, facet):
    question = _question(facet) + ' ' + ' '.join(facet.get('objects') or [])
    # Allergy is a different outcome from an infectious disease. Generic
    # "risk" in an allergy instruction must not answer infection questions.
    asked_infection = bool(re.search(r'传染|感染|疾病|疫情|病原|病毒|暴发|会得病|会传什么病', question))
    return bool((_INFECTION_OUTCOME if asked_infection else _RISK_OUTCOME).search(text))


def semantic_match(atom, facet, registry=None):
    """False means a known foreign object/intent, not merely omitted words.

    The immutable registry is important even when only one facet is being
    repaired: a fact explicitly about another sibling cannot become local by
    being assigned that remaining fid. Heading text is never evidence.
    """
    facet = _facet(facet)
    text = clean_text(atom.get('text', ''))
    question = _question(facet)
    objects = [str(o) for o in facet.get('objects', []) if str(o)]
    registry = [_facet(f) for f in (registry or [])]
    if objects and registry:
        own_hit = any(o in text for o in objects)
        foreign_objects = {str(o) for f in registry if str(f.get('fid') or f.get('id')) != str(facet.get('fid') or facet.get('id'))
                           for o in f.get('objects', []) if str(o) and o not in objects}
        if not own_hit and any(o in text for o in foreign_objects):
            return False
    kind = facet.get('kind')
    if kind == 'channel':
        requested = question + ' ' + ' '.join(objects)
        marine = bool(_MARINE.search(requested))
        weather = bool(_WEATHER.search(requested) or ('台风预警' in requested and not marine))
        if weather and not marine and _MARINE.search(text) and not _WEATHER.search(text):
            return False
        if marine and not weather and _WEATHER.search(text) and not _MARINE.search(text):
            return False
    if kind in {'method_steps', 'procedure'}:
        # Specific personal-protection questions do not ask for habitat cleanup.
        if re.search(r'防(?:止)?叮咬|避免.{0,8}叮咬|预防.{0,8}叮咬', question):
            if _BREEDING.search(text) and not _PERSONAL.search(text):
                return False
        # Conversely, a cleanup facet cannot be delivered using clothing alone.
        if _BREEDING.search(question) and re.search(r'清理|清除|处理|清扫', question):
            if _PERSONAL.search(text) and not _BREEDING.search(text):
                return False
        if _INSURANCE.search(question) and not _INSURANCE.search(text):
            if re.search(r'网箱|池塘|锚链|锚泊|框架|堤埂|网衣|缆绳', text) and _ACTIONS.search(text):
                return False
    if kind == 'eligibility' and (_INSURANCE.search(question) or re.search(r'能赔|赔吗|获赔|赔偿', question)) and not _INSURANCE.search(text):
        if re.search(r'网箱|池塘|锚链|锚泊|框架|堤埂|网衣|缆绳', text) and _ACTIONS.search(text):
            return False
    if kind == 'risk_relation' and not _risk_outcome(text, facet):
        if _PERSONAL.search(text) or _BREEDING.search(text):
            return False
    return True


def _channel_parts(facet, facts):
    """A named matching service and an actual way in are separate outputs."""
    question = _question(facet) + ' ' + ' '.join(facet.get('objects') or [])
    marine = bool(_MARINE.search(question))
    weather = bool(_WEATHER.search(question) or ('台风预警' in question and not marine))
    texts = [clean_text(a.get('text', '')) for a in facts]
    named = re.compile(r'https?://[^\s]+|[\u4e00-\u9fffA-Za-z0-9]{2,30}(?:公众号|网站|官网|平台|热线|气象台|预报台|天气网|预报网|服务网)|“[^”]{2,35}”(?:栏目|公众号|平台|网站)?')
    def concrete_name(text):
        for match in named.finditer(text):
            value = match.group(0)
            if value.startswith(('http://', 'https://', '“')):
                return True
            value = re.sub(r'^(?:(?:可以|可|请|通过|到|在|从|进入|打开|登录|登陆|查询|查看|访问|关注|搜索|渠道|是|一个|相关|该|本|此|的|官方网站|官网|网站|平台|公众号|有关|服务|官方))+', '', value)
            if re.sub(r'公众号|网站|官网|平台|热线|气象台|预报台|天气网|预报网|服务网', '', value):
                return True
        return False
    compatible = [t for t in texts if (not marine and not weather) or
                  (marine and _MARINE.search(t)) or (weather and _WEATHER.search(t))]
    has_name = any(concrete_name(t) for t in compatible)
    # A URL, page/menu, platform search or concrete named portal gives a way in;
    # an isolated "网站/平台" or generic "应急管理" cannot fill both slots.
    access = any(re.search(r'https?://|栏目|入口|板块|菜单|公众号|热线|打开|进入|登录|登陆|搜索', t) or
                 (re.search(r'到|在|通过|访问|查询|查看|关注', t) and concrete_name(t)) for t in texts)
    return has_name, bool(has_name and access)


def _action_count(facts):
    # Repeated copies are not separate steps; different objects/actions are.
    seen = set()
    count = 0
    for atom in facts:
        text = re.sub(r'^\s*\d+(?:[.．](?!\d)|、)\s*', '', clean_text(atom.get('text', '')))
        key = re.sub(r'[\s。；;，,]', '', text)
        if key in seen:
            continue
        seen.add(key)
        count += len(_ACTIONS.findall(text))
    return count


def _has_refs(atom):
    return any(type(row.get('citation')) is int and row['citation'] > 0 and
               row.get('passage_id', 1) != 0 and row.get('quote')
               for row in atom.get('evidence_quotes', []))


def _place_present(text, facet):
    from .evidence_applicability import leaf_places
    wanted = facet.get('places') or []
    if wanted:
        return all(place in text for place in wanted)
    return bool(leaf_places(text) or re.search(r'中国|全国|我国|全球|(?<!该)(?<!本)[\u4e00-\u9fff]{2,12}(?:站|岸段|海域|码头)', text))


def _typed_table_values(atom, facet):
    """Read a grounded row's numbers only under its own explicit unit column.

    ``table_header`` travels with a validated atom, not a unit found elsewhere
    on a page. An inline Markdown table may also carry its own header. Bare
    values in another column, prose or subsequent table never borrow a unit.
    """
    from .quantities import UNITS, declared_column_unit
    from decimal import Decimal
    unit_pattern = '|'.join(re.escape(unit) for unit in sorted(UNITS, key=len, reverse=True))
    number = r'[+-]?\d+(?:\.\d+)?'
    header = str(atom.get('table_header') or '')
    result = []
    question = _question(facet)
    for raw in str(atom.get('text') or '').splitlines():
        line = unicodedata.normalize('NFKC', raw).strip()
        if not line.startswith('|'):
            header = ''
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if all(re.fullmatch(r'[:\s-]+', cell or '-') for cell in cells):
            continue
        def column_unit(label):
            label = unicodedata.normalize('NFKC', label)
            literal = re.search(r'[/／]\s*(' + unit_pattern + r')\s*$', label)
            return declared_column_unit(label) or (literal[1] if literal else None)
        if any(column_unit(cell) for cell in cells) and not any(re.fullmatch(number, cell) for cell in cells):
            header = line
            continue
        columns = [cell.strip() for cell in unicodedata.normalize('NFKC', header).strip('|').split('|')]
        if not header or len(columns) != len(cells):
            continue
        for label, cell in zip(columns, cells):
            unit = column_unit(label)
            if not unit:
                continue
            if facet.get('quantity_type') == 'normative_angle':
                if UNITS[unit][0] != 'angle':
                    continue
                if '中心角' in question and '角度范围' not in question and '中心角' not in label:
                    continue
                if '角度范围' in question and '中心角' not in question and '角度范围' not in label:
                    continue
            cell = unicodedata.normalize('NFKC', re.sub(r'\[资料\d+\]', '', cell)).replace('**', '').strip()
            if cell.endswith(unit):
                cell = cell[:-len(unit)].strip()
            if re.fullmatch(number, cell):
                result.append((Decimal(cell), unit, 0, 0))
                continue
            match = re.fullmatch(r'[\[(]\s*(' + number + r')\s*[,，]\s*(' + number + r')\s*[\])]', cell)
            if match:
                result.extend((Decimal(value), unit, 0, 0) for value in match.groups())
    return result


def _numeric_rows(facts, facet):
    """A value is not a complete tuple without its own grounded scope.

    Never assemble place from one unrelated atom and time/value from another.
    Tables remain one validated atom, preserving column positions.
    """
    from .quantities import quantities, UNITS
    rows = []
    for atom in facts:
        text = clean_text(atom.get('text', ''))
        values = quantities(text) + _typed_table_values(atom, facet)
        counts = re.findall(r'(?<!\d)\d+(?:\.\d+)?\s*(?:个|条|艘|家|处|座)(?!月)', text)
        duration_text = re.sub(r'(?:19|20)\d{2}(?:年\d{1,2}月\d{1,2}日?|[-/]\d{1,2}[-/]\d{1,2})(?:\s*\d{1,2}[:：]\d{2})?', ' ', text)
        written_duration = (facet.get('quantity_type') == 'normative_duration' and
            re.search(r'(?<!\d)(?!(?:19|20)\d{2}年)(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百两]+)(?:个)?(?:工作日|小时|个月|年|天|日|分钟|h|min)', duration_text, re.I))
        quantity_type = facet.get('quantity_type')
        if quantity_type == 'normative_frequency':
            # A horizon such as 24h is not the number of issues per day.
            # Both the counter and a period must occur in the same grounded
            # atom; neither is guessed from the document year or the question.
            number = r'(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百两]+)'
            counter = re.search(r'(?<![\d.])' + number + r'\s*次', text)
            period = re.search(r'每日|每天|一日|一天|每\s*' + number + r'\s*(?:h|小时|天|日)|'
                               r'次\s*[/／]\s*(?:d|天|日)|日(?:发布|最低|最少)', text, re.I)
            values, counts, written_duration = [], bool(counter and period), False
        elif quantity_type in {'normative_duration', 'normative_angle'}:
            wanted = {'duration', 'workdays'} if quantity_type == 'normative_duration' else {'angle'}
            values = [value for value in values if UNITS[value[1]][0] in wanted]
            counts = []
        if values or counts or written_duration:
            rows.append({'value': True, 'unit': True, 'place': _place_present(text, facet),
                         'time': bool(_TIME.search(text)), 'provenance': _has_refs(atom)})
    return rows


def _count_rows(facts, facet):
    """A disclaimer about a count, speed or dated storm ID is not a count."""
    rows = []
    for atom in facts:
        text = clean_text(atom.get('text', ''))
        tool = _verified_tool_fact(atom)
        if tool:
            if (tool.get('kind') == 'typhoon_count' and tool.get('field') == 'count'
                    and type(tool.get('value')) is int and tool['value'] >= 0
                    and tool.get('count_scope') == 'complete_api_response'):
                rows.append({'count': True, 'statistical_scope': not facet.get('places'),
                             'as_of': bool(tool.get('as_of')), 'provenance': True})
            # A scoped partial snapshot cannot masquerade as a complete list
            # even though its sentence contains a small integer and a name.
            continue
        if atom.get('origin') == 'verified_tool_record':
            continue
        if re.search(r'完整.{0,12}(?:数量|名单|列表).{0,10}(?:未知|未取得|不全)|'
                     r'(?:仅有|仅为|仅取得|仅保存|所选|部分).{0,12}(?:记录|列表|数量)|'
                     r'不能.{0,10}(?:全部|总数|完整|当作)|数量.{0,8}(?:未知|缺测|未提供)', text):
            continue
        # An explicit counter and its list/object are required in this same
        # grounded atom. Neither a scope_note nor '该数量...' can fill value.
        match = re.search(r'(?<![\d.])(?:\d+|零|一|二|两|三|四|五|六|七|八|九|十|百)\s*(?:个|条|艘|家|处|座|人)(?!月)', text)
        if not match or re.search(r'假如|假设有|例如有|举例', text):
            continue
        if facet.get('domain') == 'typhoon_dynamics':
            obj = bool(re.search(r'台风|热带气旋|活动列表', text))
            scope = bool(re.search(r'活动|列表|统计|范围|全球|西北太平洋|接口', text))
        else:
            objects = facet.get('objects') or []
            obj = any(str(value) in text for value in objects) if objects else bool(re.search(
                r'名单|清单|数量|统计|总数|记录|条目|预警|航次|船舶|渔船|人员|居民|人口|站点|岸段', text))
            scope = obj
        region = not facet.get('places') or _place_present(text, facet)
        if obj:
            rows.append({'count': True, 'statistical_scope': scope and region,
                         'as_of': bool(_TIME.search(text)), 'provenance': _has_refs(atom)})
    return rows


def _verified_tool_fact(atom, *, allow_stale=False):
    """Recognize only caller-injected, hash-bound tool facts, never model tags.

    answer_pipeline strips model-origin trust fields before adding these
    records from verified tool evidence. The digest detects later mutation;
    it is not a replacement for that caller trust boundary.
    """
    fact = atom.get('tool_fact')
    if (atom.get('origin') != 'verified_tool_record' or atom.get('type') != 'calculation'
            or not _has_refs(atom) or not isinstance(fact, dict)):
        return None
    digest = hashlib.sha256(json.dumps(fact, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf8')).hexdigest()
    if atom.get('tool_fact_hash') != digest or fact.get('status') not in ({'ready', 'stale'} if allow_stale else {'ready'}):
        return None
    return fact


def _record_clock_fields(facts):
    """Keep retrieval, query cutoff and observation timestamps independent."""
    from .evidence_applicability import parse_date
    fields = set()
    for atom in facts:
        tool = _verified_tool_fact(atom, allow_stale=True)
        if tool:
            for name, keys in (('snapshot_time', ('retrieved_at',)),
                               ('query_time', ('requested_as_of', 'as_of')),
                               ('record_time', ('record_time', 'observed_at', 'time'))):
                if any(parse_date(tool.get(key)) is not None for key in keys):
                    fields.add(name)
            continue
        if atom.get('origin') == 'verified_tool_record':
            continue
        text = clean_text(atom.get('text', ''))
        # Match the time in the same named-clock clause; a different labelled
        # clock elsewhere in the paragraph cannot satisfy this field.
        for name, expression in (
                ('snapshot_time', r'(?:快照|接口|数据)(?:的)?(?:取得|获取|抓取|下载)(?:的)?(?:时间|时刻)|快照(?:时间|时刻)'),
                ('query_time', r'(?:查询|统计)(?:的)?(?:截至|截止)?(?:时间|时刻)|(?:截至|截止)(?:的)?(?:时间|时刻)'),
                ('record_time', r'(?:记录|观测|更新)(?:的)?(?:时间|时刻)')):
            for match in re.finditer(expression, text):
                clause = re.split(r'[；;。\n]', text[match.end():], maxsplit=1)[0]
                # Do not let the next named clock donate its value.
                clause = re.split(r'(?:快照|接口)(?:取得|获取)|查询(?:截至|截止)?|(?:位置)?记录(?:时间|时刻)', clause, maxsplit=1)[0]
                if parse_date(clause) is not None and _has_refs(atom):
                    fields.add(name)
    return fields


def _storm_fields(facts, facet):
    """Storm coordinates identify an object; an administrative city cannot.

    Only substantive, grounded body atoms enter this floor. The caller binds
    verified tool records to the same immutable facet before this stage.
    Stale observations remain useful history but never certify today's state.
    """
    fields = set()
    for atom in facts:
        text = clean_text(atom.get('text', ''))
        tool = _verified_tool_fact(atom, allow_stale=True)
        if tool:
            if (tool.get('kind') == 'typhoon_availability' and tool.get('field') == 'availability'
                    and tool.get('value') == 'no_active_typhoons' and tool.get('count') == 0
                    and tool.get('count_scope') == 'complete_api_response' and tool.get('as_of')
                    and tool.get('status') == 'ready' and not tool.get('stale') and not tool.get('cache_stale')):
                # An explicitly verified empty activity list has no storm
                # centre or intensity to report. This is a complete negative
                # result, unlike an unavailable list or a partial archive.
                fields.update({'object_identity', 'position', 'intensity', 'max_wind_speed',
                               'wind_scale', 'pressure', 'motion_direction', 'motion_speed',
                               'valid_time', 'provenance'})
                continue
            # Each populated field is independently required; a pressure
            # alone does not fill maxSpeed, windScale or general intensity.
            if tool.get('kind') == 'typhoon_identity' and tool.get('field') == 'identity':
                if tool.get('storm_id') or tool.get('storm_name'):
                    fields.add('object_identity')
            if tool.get('kind') == 'typhoon_field':
                field = tool.get('source_field') or tool.get('field')
                value = tool.get('value')
                if field == 'position' and isinstance(value, dict) and all(
                        isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool)
                        for key in ('lon', 'lat')):
                    if -180 <= value['lon'] <= 180 and -90 <= value['lat'] <= 90:
                        fields.add('position')
                if field in {'maxSpeed', 'windScale', 'intensity'} and value is not None:
                    fields.add('intensity')
                if field == 'maxSpeed' and isinstance(value, (int, float)) and not isinstance(value, bool):
                    fields.add('max_wind_speed')
                if field == 'windScale' and isinstance(value, (int, float)) and not isinstance(value, bool):
                    fields.add('wind_scale')
                if field == 'pressure' and isinstance(value, (int, float)) and not isinstance(value, bool):
                    fields.add('pressure')
                if field in {'moveDirection', 'direction'} and value not in (None, ''):
                    fields.add('motion_direction')
                if field in {'moveSpeed', 'speed'} and value is not None:
                    fields.add('motion_speed')
            if tool.get('storm_id') or tool.get('storm_name'):
                fields.add('object_identity')
            current = bool(re.search(r'当前|现在|今天|今日|实时|最新|截至', _question(facet)))
            recorded = bool(re.search(r'快照(?:中|内|里|所)|已保存.{0,6}(?:记录|快照)|记录的(?:位置|风速|强度)|当时记录', _question(facet)))
            fresh = tool.get('status') == 'ready' and not tool.get('stale') and not tool.get('cache_stale')
            if (tool.get('record_time') or tool.get('time')) and (fresh or recorded or not current):
                fields.add('valid_time')
            fields.add('provenance')
            continue
        # An injected non-ready tool record may be displayed as an older
        # observation but cannot satisfy a requested current-status slot.
        if atom.get('origin') == 'verified_tool_record':
            continue
        identity = bool(re.search(r'(?:台风|热带气旋)\s*[“「"][\u4e00-\u9fffA-Za-z]{2,16}[”」"]|'
                                  r'(?:台风|接口|风暴|编号|标识).{0,12}(?<!\d)\d{4,6}(?!\d)|'
                                  r'[“「"][^”」"]{2,20}[”」"]', text))
        if identity:
            fields.add('object_identity')
        stale = bool(re.search(r'滞后|陈旧|不能.{0,15}(?:认定|确认|代表).{0,15}(?:当前|此刻|实况|实际位置)', text))
        current = bool(re.search(r'当前|现在|今天|今日|实时|最新', _question(facet)))
        # Historical queries may ask what the archived position was. A stale
        # current query must expose its gap rather than marking current full.
        if re.search(r'(?:东经|西经|经度).{0,12}[-+]?\d+(?:\.\d+)?', text) and re.search(
                r'(?:北纬|南纬|纬度).{0,12}[-+]?\d+(?:\.\d+)?', text):
            fields.add('position')
        if re.search(r'(?:最大风速|中心风速|最大风力|风力).{0,15}\d+(?:\.\d+)?\s*(?:m/s|米/秒|级|kt|节)|'
                     r'(?:强度|等级|intensity).{0,12}(?:热带低压|热带风暴|强热带风暴|超强台风|强台风|台风|SuperTY|TY|STS|TS|TD)', text, re.I):
            fields.add('intensity')
        if re.search(r'(?:最大风速|中心风速).{0,15}\d+(?:\.\d+)?\s*(?:m/s|米/秒|kt|节)', text, re.I):
            fields.add('max_wind_speed')
        if re.search(r'(?:最大风力|风力).{0,15}\d+(?:\.\d+)?\s*级', text):
            fields.add('wind_scale')
        if re.search(r'(?:中心)?气压.{0,12}\d+(?:\.\d+)?\s*(?:hPa|百帕)', text, re.I):
            fields.add('pressure')
        if re.search(r'(?:移动方向|移向|向|往).{0,10}(?:[东西南北]{1,4}(?:偏[东西南北])?|[NEWS]{1,3})', text, re.I):
            fields.add('motion_direction')
        if re.search(r'(?:移动速度|移速|速度).{0,12}\d+(?:\.\d+)?\s*(?:km/h|公里/小时|千米/小时|m/s|米/秒)', text, re.I):
            fields.add('motion_speed')
        if _TIME.search(text) and not (current and stale):
            fields.add('valid_time')
        if _has_refs(atom):
            fields.add('provenance')
    return fields


def _facet(row):
    if hasattr(row, 'to_dict'):
        return row.to_dict()
    if hasattr(row, '__dict__'):
        return dict(vars(row))
    return row


def clean_text(text):
    return re.sub(r'\[资料\d+\]', '', str(text)).strip(' \t\n#*-。')


def _own_atoms(facet, accepted, registry):
    fid = str(facet.get('fid') or facet.get('id'))
    own = [a for a in accepted if fid in a.get('facet_ids', [])]
    if own or not re.search(r'出处|原文来源|第几条|哪一条', _question(facet)):
        return own
    # A correctly grounded source statement sometimes stays with the answer
    # it supports. Reuse only that explicit statement for its provenance-only
    # sibling; never reassign substantive business conclusions across siblings.
    titles = set(re.findall(r'《([^》]+)》', _question(facet)))
    if not titles:
        titles = {title for row in registry for title in re.findall(r'《([^》]+)》', _question(row))}
    if len(titles) != 1:
        return own
    title = next(iter(titles))
    for atom in accepted:
        text = clean_text(atom.get('text', ''))
        if (atom.get('type') == 'direct_fact' and _has_refs(atom) and title in text
                and re.search(r'出自|出处|来源|第[一二三四五六七八九十百\d]+条', text)):
            return [{**atom, 'facet_ids': [fid], 'provenance_owner_reused': True}]
    return own


def substantive(atom):
    """A date, citation, title, or version disclaimer alone is not an answer."""
    if atom.get('type') not in SUBSTANTIVE_TYPES:
        return False
    text = clean_text(atom.get('text', ''))
    if len(text) < 2 or _DATE_ONLY.fullmatch(text) or _META_ONLY.match(text):
        return False
    if re.fullmatch(r'(?:该|本|此)?(?:方案|通知|文件|预案|办法)?.{0,10}(?:现行有效性|版本|适用性).{0,10}(?:未核实|待核实|尚未核实)', text):
        return False
    return True


def covered_outputs(facet, own, *, registry=None):
    """Kind-specific delivery floors; model role labels are never enough."""
    facet = _facet(facet)
    facts = [a for a in own if substantive(a) and semantic_match(a, facet, registry)]
    text = '\n'.join(clean_text(a.get('text', '')) for a in facts)
    kind = facet.get('kind', 'explanation')
    required = list(facet.get('required_outputs') or ['explanation'])
    output = set()
    identities = any(a.get('type') == 'document_identity' for a in own)
    body_identity = bool(_IDENTITY.search(text))
    steps = _action_count(facts)
    numeric = _numeric_rows(facts, facet)
    counts = _count_rows(facts, facet)
    storm_fields = _storm_fields(facts, facet) if facet.get('domain') == 'typhoon_dynamics' else set()
    clock_fields = _record_clock_fields(facts)
    refs = any(_has_refs(atom) for atom in facts)
    channel_name, access_path = _channel_parts(facet, facts)
    for name in required:
        if name in {'verdict', 'distinction', 'comparison'}:
            ok = any(a.get('type') == 'synthesis' for a in facts) or bool(re.search(
                r'不是一回事|不是同一|并非同一|不能等同|不可等同|不同之处|区别在(?:于)?|二者不同|三者不同|分别是|'
                r'不能(?:把|将).{1,50}(?:当作|理解为|等同于)|'
                r'(?:性质|机制|成因|对象|口径|含义|计算方式|适用范围|指标|统计方式).{0,8}不同', text))
        elif name in {'document_identity', 'document', 'document_name'}:
            ok = identities or body_identity
        elif name in {'per_object_basis', 'basis', 'basis_document'}:
            ok = ((identities or body_identity) and bool(facts) and
                  bool(_ACTIONS.search(text) or re.search(r'用于|属于|适用于|规定|条件|救助对象|保险责任', text)))
        elif name == 'basis_content':
            ok = bool(facts) and bool(_ACTIONS.search(text) or re.search(r'用于|属于|适用于|规定|条件|救助对象|保险责任', text))
        elif name == 'definition':
            ok = bool(facts) and len(text) >= 8 and bool(re.search(r'是|指|表示|称为|定义为|属于', text))
        elif name in {'actors', 'actor', 'responsible_actor'}:
            ok = bool(_ACTORS.search(text))
        elif name in {'conditions', 'condition'}:
            if kind == 'risk_relation':
                ok = bool(_risk_outcome(text, facet) and re.search(
                    r'不能.{0,18}(?:判定|证明|认定)|不等于|并不意味|不代表|并非|不一定|取决于|'
                    r'携带.{0,12}(?:病原|病毒)|感染.{0,12}蚊|病媒|带毒|若|如果|只有|当.{0,15}时|'
                    r'叮咬.{0,15}(?:传播|感染)|(?:传播|感染).{0,15}叮咬', text))
            else:
                ok = bool(_CONDITIONS.search(text))
        elif name in {'steps', 'method_steps', 'procedure'}:
            ok = steps >= 2
        elif name in {'count', 'statistical_scope', 'as_of'}:
            row = max(counts, key=lambda item: sum(item.values()), default={})
            ok = bool(row.get(name))
        elif name in {'object_identity', 'position', 'intensity', 'max_wind_speed', 'wind_scale', 'pressure', 'motion_direction', 'motion_speed'}:
            ok = name in storm_fields
        elif name in {'snapshot_time', 'record_time', 'query_time'}:
            ok = name in clock_fields
        elif name in {'value', 'numeric_value', 'calculation', 'number'}:
            ok = bool(numeric)
        elif name in {'frequency', 'duration'}:
            # A combined provision question needs both predicates. A valid
            # duration cannot satisfy frequency, or the other way around.
            ok = bool(_numeric_rows(facts, {**facet, 'quantity_type': 'normative_' + name}))
        elif name in {'unit', 'place', 'time', 'valid_time', 'provenance'}:
            if kind in {'numeric_value', 'calculation'} and numeric:
                # Select one value tuple; never borrow time or place from a
                # different station's value. Keep the cells that are present
                # when just one required cell (e.g. place) is missing.
                row = max(numeric, key=lambda item: sum(item.values()))
                ok = bool(row.get('time' if name == 'valid_time' else name))
            elif name == 'valid_time' and facet.get('domain') == 'typhoon_dynamics':
                ok = name in storm_fields
            elif name == 'provenance' and facet.get('quantity_type') == 'count':
                row = max(counts, key=lambda item: sum(item.values()), default={})
                ok = bool(row.get(name))
            elif name == 'unit':
                ok = False
            elif name == 'place':
                ok = _place_present(text, facet)
            elif name in {'time', 'valid_time'}:
                ok = bool(_TIME.search(text))
            else:
                ok = refs
        elif name == 'application_path':
            ok = bool(re.search(r'(?:向|找|到|联系).{1,35}(?:村委会|居委会|乡镇|政府|部门|机构|保险公司|承保|窗口)', text)) and bool(re.search(r'申请|提交|受理|报案|申报|理赔|核定|咨询', text))
        elif name in {'channel_name', 'channel', 'access_path'}:
            ok = access_path if name == 'access_path' else channel_name
        elif name in {'relation', 'risk_relation'}:
            # "可能进入住宅" is not a disease relation. Disease/risk must be
            # the actual outcome and be connected by a relational predicate.
            ok = bool(_risk_outcome(text, facet) and re.search(
                r'传播|传染|媒介|感染|病媒|不能.{0,18}(?:判定|证明|认定)|不等于|并不意味|不代表|'
                r'(?:增加|升高|提高|降低|存在|有).{0,12}(?:风险|概率)|'
                r'(?:风险|概率).{0,12}(?:增加|升高|提高|降低)|由.{1,20}引起', text))
        elif name in {'status', 'status_at_time', 'current_status'}:
            ok = bool(re.search(r'已发布|生效|解除|开放|关闭|停运|复航|启动|终止|正在|有效|未发布', text)) and bool(_TIME.search(text))
        elif name == 'formula':
            ok = bool(re.search(r'=|＝|\+|相加|相减|相乘|乘以|除以|之和|之差|公式', text))
        elif name == 'inputs':
            ok = bool(re.search(r'输入|参数|变量|需要|依赖|采用|单位|基准', text))
        elif name == 'result_or_input_gap':
            ok = bool(numeric)
        elif name in {'fact', 'answer', 'explanation'}:
            ok = bool(facts)
        elif name == 'event_time':
            ok = bool(_TIME.search(text))
        elif name == 'support':
            ok = refs
        else:
            ok = False
        if ok:
            output.add(name)
    if not facts:
        output.clear()
    return output


def evaluate_delivery(facets, accepted):
    rows = []
    for original in facets:
        f = _facet(original)
        fid = str(f.get('fid') or f.get('id'))
        own = _own_atoms(f, accepted, [_facet(row) for row in facets])
        facts = [a for a in own if substantive(a) and semantic_match(a, f, facets)]
        gaps = [a for a in own if a.get('type') == 'gap' and a.get('gap_type') in f.get('allowed_gaps', [])]
        covered = covered_outputs(f, own, registry=facets)
        required = set(f.get('required_outputs') or ['explanation'])
        state = 'full' if facts and required <= covered and not gaps else 'partial' if facts or gaps else 'missing'
        rows.append({'fid': fid, 'id': f.get('id'), 'kind': f.get('kind'), 'status': state,
                     'covered_outputs': sorted(covered), 'missing_outputs': sorted(required - covered),
                     'atom_ids': [a['aid'] for a in own], 'substantive_atom_ids': [a['aid'] for a in facts],
                     'object_conflict_atom_ids': [a['aid'] for a in own if substantive(a) and not semantic_match(a, f, facets)],
                     'allowed_gap_atom_ids': [a['aid'] for a in gaps]})
    return rows


def publishable(facets, rows):
    """No date-only fragments, and no silent omission of the core question."""
    normalized = [_facet(f) for f in facets]
    if not normalized:
        return False
    by_fid = {r['fid']: r for r in rows}
    core = [f for f in normalized if f.get('kind') in {'distinction', 'numeric_value', 'status_at_time', 'calculation'}] or normalized[:1]
    has_content = lambda f: bool(by_fid.get(str(f.get('fid') or f.get('id')), {}).get('substantive_atom_ids'))
    for f in core:
        row = by_fid.get(str(f.get('fid') or f.get('id')), {})
        if f.get('kind') in {'numeric_value', 'status_at_time', 'calculation'} and row.get('status') != 'full':
            return False
    return all(has_content(f) for f in core) and sum(has_content(f) for f in normalized) * 2 >= len(normalized)


_PATHS = {
    'business_data_missing': '接入对应业务单位的原始数据、台账或正式通报。',
    'calculation_input_missing': '补齐原始格网、站点、单位、基准和目标时段后重新计算。',
    'live_status_not_available': '查询目标日期的主管部门、现场管理单位正式公告或归档记录。',
    'individual_determination_required': '向当地主管部门或承保机构提交个人材料、核定记录和合同。',
    'jurisdiction_rule_not_found': '补充该地区主管部门的适用文件及版本依据。',
    'evidence_not_found': '补查对应主管部门正文、附件或带出处的原始记录。',
}


def render_answer(facets, accepted):
    """Render by immutable facet ownership, never by surviving heading position."""
    blocks = []
    for f in map(_facet, facets):
        fid = str(f.get('fid') or f.get('id'))
        own = [a for a in _own_atoms(f, accepted, [_facet(row) for row in facets]) if semantic_match(a, f, facets)]
        if not own:
            continue
        title = str(f.get('question') or f.get('text') or fid)
        lines = []
        seen = set()
        for atom in own:
            text = str(atom.get('display_text') or atom.get('text', '')).strip()
            key_text = re.sub(r'^\s*\d+(?:[.．](?!\d)|、)\s*', '', clean_text(atom.get('text') or text))
            key = (re.sub(r'\s+', '', key_text).strip('。；;'), atom.get('type'),
                   tuple(sorted(atom.get('context_subject') or [])))
            if text and key not in seen:
                seen.add(key)
                header = atom.get('table_header')
                if header:
                    text = header + '\n|' + '---|' * len(header.strip('|').split('|')) + '\n' + text
                lines.append(text)
        blocks.append('**' + title.rstrip('？?。') + '**\n\n' + '\n\n'.join(lines))
    return '\n\n'.join(blocks)


_OUTPUT_LABELS = {
    'verdict': '对核心问题的明确结论', 'distinction': '各对象的区别',
    'comparison': '按相同维度逐项比较的依据', 'document_identity': '依据文件及身份信息',
    'document': '依据文件', 'document_name': '依据文件名称', 'per_object_basis': '各对象对应的文件与条款',
    'basis': '适用依据', 'basis_document': '适用文件', 'basis_content': '相关正文条款',
    'definition': '概念定义', 'actors': '提出、受理或批准的责任主体', 'actor': '责任主体',
    'responsible_actor': '责任主体', 'conditions': '适用条件或判断边界', 'condition': '适用条件',
    'steps': '针对本对象的具体操作步骤', 'method_steps': '具体操作步骤', 'procedure': '办理程序',
    'value': '具体数值', 'numeric_value': '具体数值', 'calculation': '计算结果', 'number': '数量',
    'count': '明确的数量', 'statistical_scope': '统计对象与范围', 'as_of': '数量的统计截止时刻',
    'object_identity': '台风名称或编号', 'position': '台风中心经纬度', 'intensity': '台风强度或最大风速',
    'max_wind_speed': '中心最大风速及单位', 'wind_scale': '中心最大风力级别', 'pressure': '中心气压及单位',
    'snapshot_time': '接口快照取得时间', 'record_time': '位置或业务记录时间', 'query_time': '查询截止时间',
    'motion_direction': '移动方向', 'motion_speed': '移动速度',
    'unit': '计量单位', 'place': '适用地点或海域', 'time': '对应日期和时刻', 'valid_time': '有效时段',
    'provenance': '原始出处', 'application_path': '申请或办理渠道', 'channel_name': '对应业务的具体渠道名称',
    'channel': '具体查询渠道', 'access_path': '可操作的访问入口', 'relation': '对象与风险之间的关系',
    'risk_relation': '风险关系', 'status': '目标时点的实际状态', 'status_at_time': '目标时点的实际状态',
    'current_status': '当前状态', 'formula': '计算公式', 'inputs': '计算输入数据',
    'result_or_input_gap': '计算结果或明确的输入缺口', 'fact': '所问事实', 'answer': '本子问的实质回答',
    'explanation': '具体解释', 'event_time': '事件发生时间', 'support': '支持结论的原文',
}


def output_label(name):
    return _OUTPUT_LABELS.get(name, '本子问所需的实质依据')


def output_labels(names):
    return [output_label(name) for name in names]


def format_time_scope(value):
    if not isinstance(value, dict):
        return str(value or '')
    start, end, as_of = (value.get(k) for k in ('start', 'end', 'as_of'))
    if start and end:
        return str(start) if start == end else f'{start}至{end}'
    return ('截至' + str(as_of)) if as_of else str(start or end or '')


def render_with_gaps(facets, accepted, evidence=None, *, rows=None, gap_reasons=None):
    """Explicit incomplete delivery with acquired/needed/acquisition-path fields.

    Document cards prove only what was acquired. They do not prove the missing
    fact or license a current-policy, calculation, or personal-eligibility claim.
    """
    rows = rows or evaluate_delivery(facets, accepted)
    by_fid = {r['fid']: r for r in rows}
    text = render_answer(facets, accepted)
    card_lines = []
    for e in evidence or []:
        card = e.get('doc_card') or {}
        if hasattr(card, 'to_dict'):
            card = card.to_dict()
        n = e.get('citation')
        title = str(card.get('title') or e.get('title') or '').replace('\n', ' ')[:160]
        if title:
            card_lines.append(title + (f'[资料{n}]' if type(n) is int and n > 0 else ''))
    acquired = '；'.join(card_lines[:6]) or '本次尚未取得可核对的正文或业务数据。'
    gaps = []
    for f in map(_facet, facets):
        fid = str(f.get('fid') or f.get('id'))
        row = by_fid.get(fid, {})
        if row.get('status') == 'full':
            continue
        allowed = list(f.get('allowed_gaps') or ['evidence_not_found'])
        reason = (gap_reasons or {}).get(fid, {})
        gap_type = reason.get('gap_type')
        if gap_type not in allowed:
            gap_type = 'evidence_not_found' if 'evidence_not_found' in allowed else allowed[0]
        objects = '、'.join(f.get('objects') or [])
        places = '、'.join(f.get('places') or [])
        scope = '，'.join(x for x in (objects, places, format_time_scope(f.get('time'))) if x)
        title = str(f.get('question') or f.get('text') or fid).rstrip('？?。')
        needed = '、'.join(output_label(name) for name in row.get('missing_outputs', [])) or '本子问的实质依据'
        gaps.append(f'**待补齐：{title}**\n\n已取得：{acquired}\n\n还缺：{scope + "的" if scope else ""}{needed}，尚未形成可核对的完整交付。\n\n补齐路径：{_PATHS.get(gap_type, _PATHS["evidence_not_found"])}')
    return '\n\n'.join(x for x in (text, *gaps) if x)
