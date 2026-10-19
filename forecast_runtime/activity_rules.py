"""Traceable official activity conditions, independent of site permission.

The default catalog has no invented wind/wave/current safety limits. Schema 1
remains supported only as an explicit legacy override. No DEM height is combined
with sea level here and no missing rule is interpreted as a failed condition.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from .storage import BEIJING
from .policy_catalog import load_catalog, select_policies, policy_requirements, knowledge_cutoff

RULE_PATH = Path(__file__).with_name('activity_rules.json')
LABELS = {'tidal_flat': '赶海', 'swimming': '游泳',
          'shore_fishing': '岸边或礁石海钓', 'boat_fishing': '船钓'}
GATE_LABELS = {
    'activity_permission': '该场地该活动的有效开放信息',
    'site_representativeness': '活动场地与采样网格的代表性核验',
    'forecast_semantics': '参与规则比较的变量含义与时区核定',
    'site_hazard_check': '破碎浪、离岸流、迎浪方向等现场风险核查',
    'lifeguard': '该活动时段的现场救生保障',
    'tidal_return_plan': '实测滩高、通行路线及返程临界水位的核定方案',
    'fishing_site': '岸钓或礁钓位置及通行、禁入范围核验',
    'boat_eligibility': '船型、适航证书、抗风限制、航区及开航安排',
}


def load_rules(override=None, location=None, activity=None, start=None, end=None, as_of=None):
    rules = deepcopy(override) if isinstance(override, dict) else json.loads(RULE_PATH.read_text(encoding='utf-8'))
    rules.pop('fingerprint', None)
    if not isinstance(rules.get('version'), str):
        raise ValueError('invalid_activity_rules')
    if rules.get('schema') == 2:
        if rules.get('mode') != 'official_catalog' or rules.get('limits', {}) != {}:
            raise ValueError('invalid_official_activity_rules')
        previous = rules.pop('resolved_for', {})
        catalog = load_catalog(rules.pop('_catalog', None) or rules.pop('catalog', None))
        for key in ('policy_selection', 'numeric_rules'):
            rules.pop(key, None)
        # Reusing the same resolved request preserves its knowledge snapshot.
        # An explicitly changed query period starts a new snapshot unless the
        # caller also supplies an earlier reconstruction cutoff.
        same_period = ((start is None or stamp(start) == stamp(previous.get('start')))
                       and (end is None or stamp(end, end=True) == stamp(previous.get('end'), end=True)))
        selected_as_of = as_of if as_of is not None else previous.get('as_of') if same_period else None
        selection = select_policies(catalog,
            location if location is not None else previous.get('location'),
            activity if activity is not None else previous.get('activity'),
            start if start is not None else previous.get('start'),
            end if end is not None else previous.get('end'),
            selected_as_of)
        rules.update(limits={}, low_tide_reference_minutes=None, _catalog=catalog,
                     policy_selection=selection, resolved_for=selection['request'],
                     numeric_rules=[{**rule, 'policy_id': policy['id'], 'source': policy['source']}
                        for policy in selection['eligible_policies'] for rule in policy.get('numeric_rules', [])])
        rules['fingerprint'] = hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
        return rules
    if rules.get('schema') != 1 or not isinstance(rules.get('limits'), dict):
        raise ValueError('invalid_activity_rules')
    for product, limit in rules['limits'].items():
        number = limit.get('maximum') if isinstance(limit, dict) else None
        if product not in {'WAV', 'SSW', 'OCU'} or type(number) not in {int, float} or not math.isfinite(number) or number <= 0:
            raise ValueError('invalid_activity_screening_limit')
    rules['fingerprint'] = hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return rules


def stamp(value, end=False):
    if isinstance(value, datetime):
        return value.replace(tzinfo=BEIJING) if value.tzinfo is None else value.astimezone(BEIJING)
    if not isinstance(value, str):
        return None
    try:
        date_only = len(value) == 10
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            result = result.replace(tzinfo=BEIJING)
        if end and date_only:
            result += timedelta(days=1, microseconds=-1)
        return result.astimezone(BEIJING)
    except ValueError:
        return None


def _url(value):
    try:
        parts = urlsplit(value or '')
        return parts.scheme in {'http', 'https'} and bool(parts.hostname) and not parts.username and not parts.password
    except ValueError:
        return False


def valid_period(record, start, end, as_of=None):
    first, last = stamp(record.get('valid_from')), stamp(record.get('valid_to'), end=True)
    published = stamp(record.get('published_at') or record.get('observed_at'))
    observed = stamp(record.get('observed_at'))
    cutoff = knowledge_cutoff(start, as_of)
    return bool(first and last and start and end and first <= start <= end <= last
                and (published is None or published <= cutoff) and (observed is None or observed <= cutoff))


def _series(product):
    return {stamp(row.get('time')): row.get('value') for row in product.get('series', [])
            if stamp(row.get('time')) is not None and type(row.get('value')) in {int, float}
            and math.isfinite(row['value']) and row['value'] >= 0}


def _ranges(rows, status='passed'):
    windows = []
    for row in rows:
        if row['state'] != status:
            continue
        when = stamp(row['time'])
        if windows and when - stamp(windows[-1]['last_sample']) == timedelta(hours=1):
            windows[-1]['last_sample'] = row['time']
            windows[-1]['sample_count'] += 1
        else:
            windows.append({'first_sample': row['time'], 'last_sample': row['time'], 'sample_count': 1})
    for row in windows:
        row.update(scope='sampled_forecast_times_only', not_a_safe_window=True,
                   note='仅表示这些逐时采样点通过工程数值筛查，不外推两次采样间或现场安全。')
    return windows


def numerical_screen(products, rules, start=None, end=None, location=None, activity=None, context=None):
    if rules.get('schema') == 2:
        return _official_numerical_screen(products, rules, start, end, location, activity, context)
    variables = {item['product']: item for item in products}
    series = {key: _series(variables.get(key, {})) for key in ('WAV', 'SSW', 'OCU')}
    times = sorted(set().union(*(set(rows) for rows in series.values())))
    if start and end:
        # Explicitly include absent hours: an intermittent series is not complete.
        times = []
        cursor = start
        while cursor <= end and len(times) < 24 * 32:
            times.append(cursor)
            cursor += timedelta(hours=1)
    rows = []
    for when in times:
        values, failed, missing = {}, [], []
        for key in ('WAV', 'SSW', 'OCU'):
            value, limit, product = series[key].get(when), rules['limits'].get(key), variables.get(key, {})
            if value is None or not limit or product.get('state') != 'ready' or product.get('unit') != limit.get('unit'):
                missing.append(key)
                continue
            values[key] = {'value': value, 'unit': product['unit']}
            if value > limit['maximum']:
                failed.append(key)
        state = 'failed' if failed else 'insufficient' if missing else 'passed'
        rows.append({'time': when.isoformat(), 'state': state, 'values': values,
                     'exceeded_products': failed, 'missing_products': missing})
    failed = any(row['state'] == 'failed' for row in rows)
    incomplete = (not rows or any(row['state'] == 'insufficient' for row in rows)
                  or any(variables.get(key, {}).get('partial') for key in ('WAV', 'SSW', 'OCU')))
    return {'state': 'failed' if failed else 'insufficient' if incomplete else 'passed',
            'rule_version': rules.get('version'), 'rule_fingerprint': rules['fingerprint'],
            'rule_status': rules.get('status'), 'limits': rules['limits'],
            'thresholds_are_official': rules.get('status') == 'validated' and rules.get('authority') == 'official',
            'assumption': rules.get('assumption'), 'purpose': rules.get('purpose'),
            'hourly': rows, 'candidate_windows': _ranges(rows),
            'not_an_activity_permission': True}


def _official_numerical_screen(products, rules, start, end, location, activity, context):
    context = context or {}
    rules = load_rules(rules, location, activity, start, end,
                       as_of=context.get('as_of') or context.get('forecast_issue_cutoff'))
    selection = rules['policy_selection']
    cutoff = selection['request']['as_of']
    requested_location = selection['request']['location']
    variables = {item['product']: item for item in products if isinstance(item, dict) and item.get('product')}
    series = {key: _series(value) for key, value in variables.items()}
    times = sorted(set().union(*(set(value) for value in series.values()))) if series else []
    if start and end:
        times, cursor = [], start
        while cursor <= end and len(times) < 24 * 32:
            times.append(cursor)
            cursor += timedelta(hours=1)
    rows = []
    for when in times:
        values = {key: {'value': value[when], 'unit': variables[key].get('unit'),
                        'semantics': variables[key].get('semantics', 'not_verified')}
                  for key, value in series.items() if when in value and variables[key].get('state') == 'ready'}
        rows.append({'time': when.isoformat(), 'state': 'not_evaluated', 'values': values,
                     'exceeded_products': [], 'missing_products': [key for key in ('WAV', 'SSW', 'OCU') if key not in values]})
    evaluations, risk_classes = [], []
    for rule in rules['numeric_rules']:
        common = {'rule_id': rule['id'], 'policy_id': rule['policy_id'], 'source': rule['source'],
                  'effect': rule['effect'], 'quantity': rule['quantity'], 'unit': rule['unit'],
                  'not_an_activity_permission': True}
        if rule['evaluator'] == 'bathing_water_quality_temperature':
            quality = context.get('water_quality') or {}
            site = requested_location.get('site_id') or context.get('site_id')
            observed = stamp(quality.get('observed_at'))
            usable = (site and quality.get('site_id') == site and quality.get('state') == 'current'
                      and quality.get('published_by_query_date') is not False
                      and _url(quality.get('source_url')) and stamp(quality.get('published_at'))
                      and valid_period(quality, start, end, cutoff) and observed and start and end
                      and start.date() == end.date() == observed.date())
            if not usable:
                evaluations.append({**common, 'state': 'insufficient', 'reason_code': 'matched_current_bathing_observation_required',
                    'required_data': rule['requires'],
                    'reason': '缺同一浴场、同一监测日期且已发布的实测水温和水质；模式SST与历史监测不替代。'})
                continue
            temperature, grade = quality.get('water_temperature_c'), quality.get('water_quality_grade')
            temperature_ok = type(temperature) in {int, float} and math.isfinite(temperature)
            unsuitable = (grade == '差' or temperature_ok and
                          (temperature < rule['suitable_min'] or temperature > rule['maximum']))
            complete = temperature_ok and grade in {'优', '良', '差'}
            suitability = ('不适宜' if unsuitable else
                           '较适宜' if complete and (grade == '良' or temperature > rule['suitable_max']) else
                           '适宜' if complete else None)
            evaluations.append({**common, 'state': 'failed' if unsuitable else 'passed' if complete else 'insufficient',
                'classification': suitability, 'reason_code': 'official_bathing_environment_criteria' if suitability else 'water_observation_incomplete',
                'observed_at': quality['observed_at'], 'water_temperature_c': temperature,
                'water_quality_grade': grade, 'observation_source_url': quality['source_url'],
                'reason': '仅评价所列监测日的浴场环境；现场风浪、救生和开放许可需另核。'})
        elif rule['evaluator'] == 'gust_wind_risk':
            product = variables.get(rule['product'], {})
            semantics = product.get('semantics') or {}
            confirmed = (isinstance(semantics, dict) and semantics.get('quantity') == rule['quantity']
                         and semantics.get('verification') == 'verified' and _url(semantics.get('source_url'))
                         and not semantics.get('assumed') and product.get('unit') == rule['unit'])
            if not confirmed:
                risk_classes.append({**common, 'state': 'unsupported_semantics',
                    'reason_code': 'verified_three_second_gust_required',
                    'reason': '本条比较预报3秒阵风最大值；SSW的u10/v10矢量模或假设阵风不满足该定义。'})
                continue
            if context.get('recreational_fishery_operation') is not True:
                risk_classes.append({**common, 'state': 'insufficient', 'reason_code': 'operation_type_not_verified'})
                continue
            samples = series.get(rule['product'], {})
            complete = product.get('state') == 'ready' and not product.get('partial') and rows and all(stamp(row['time']) in samples for row in rows)
            if not complete:
                risk_classes.append({**common, 'state': 'insufficient', 'reason_code': 'gust_forecast_coverage_incomplete'})
                continue
            classified = [{'time': row['time'], 'wind_impact': 'major' if samples[stamp(row['time'])] > rule['major_above'] else 'lesser',
                           'value': samples[stamp(row['time'])]} for row in rows]
            risk_classes.append({**common, 'state': 'classified', 'hourly': classified,
                'wind_impact': 'major' if any(row['wind_impact'] == 'major' for row in classified) else 'lesser',
                'composite_risk': 'not_evaluated', 'reason': '仅风力分项；综合等级另需能见度与对流预报。风险等级不等于禁航或开放许可。'})
    # Risk classes never become admission failures or candidate safe windows.
    state = ('failed' if any(row['state'] == 'failed' for row in evaluations) else
             'insufficient' if evaluations and any(row['state'] != 'passed' for row in evaluations) else
             'passed' if evaluations else
             'unsupported_semantics' if any(row['state'] == 'unsupported_semantics' for row in risk_classes) else
             'insufficient' if risk_classes and any(row['state'] == 'insufficient' for row in risk_classes) else 'not_evaluated')
    if evaluations and state in {'passed', 'failed'}:
        for row in rows:
            row.update(state=state, evaluation_scope='matched_bathing_observation_only')
    reason_code = ('official_environment_rule_evaluated' if state in {'passed', 'failed'} else
                   'official_numeric_rule_inputs_missing' if state == 'insufficient' else
                   'unsupported_forecast_semantics' if state == 'unsupported_semantics' else
                   'risk_classification_only' if risk_classes else 'no_applicable_official_numeric_rule')
    available = [{'product': key, 'state': item.get('state'), 'unit': item.get('unit'),
                  'sample_count': len(series[key]), 'partial': item.get('partial', False),
                  'semantics': item.get('semantics', 'not_verified')}
                 for key, item in variables.items()]
    return {'state': state, 'reason_code': reason_code, 'mode': 'official_catalog',
            'reason': {'not_evaluated': '未以风浪流假定阈值作活动判断；下列已取得海况仍可查看。',
                       'unsupported_semantics': '已找到规则，但现有变量含义不能直接用于该条比较。',
                       'insufficient': '已找到适用数值条款，尚缺同场地、时段和量纲一致的输入。',
                       'passed': '所列官方环境评价条件通过；这不授予活动许可。',
                       'failed': '匹配的官方环境评价条件显示不适宜；这不是全场地行政禁令。'}[state],
            'rule_version': rules['version'], 'rule_fingerprint': rules['fingerprint'], 'rule_status': rules['status'],
            'limits': {}, 'thresholds_are_official': bool(rules['numeric_rules']),
            'numeric_rules': rules['numeric_rules'], 'numeric_evaluations': evaluations, 'risk_classes': risk_classes,
            'policy_selection': selection, 'available_data': available,
            'missing_rules': ['匹配该地域、活动、场地及指标含义的官方风浪流准入条款'] if not evaluations else [],
            'assumption': rules.get('assumption'), 'purpose': rules.get('purpose'), 'hourly': rows,
            'candidate_windows': [], 'not_an_activity_permission': True}


def _matching_evidence(context, activity, location, start, end, as_of=None):
    requested_site = location.get('site_id') or context.get('site_id')
    matches = []
    for row in context.get('activity_evidence', []):
        if not isinstance(row, dict) or row.get('activity') != activity:
            continue
        if row.get('location_key') != location.get('key') or not row.get('site_id'):
            continue
        if requested_site and row['site_id'] != requested_site:
            continue
        if (not _url(row.get('source_url')) or stamp(row.get('published_at')) is None
                or not valid_period(row, start, end, as_of)):
            continue
        matches.append(row)
    return matches


def _permission_timeline(context, activity, location, start, end, cutoff):
    """Resolve latest dated notices per site and overlap before exposing closures.

    Open raw notices may cancel an older closure, but only records already
    resolved into activity_evidence can form an affirmative permission gate.
    """
    if start is None or end is None or start > end:
        return [], []
    requested_site = location.get('site_id') or context.get('site_id')
    fields = ('kind', 'activity', 'location_key', 'site_id', 'state',
              'valid_from', 'valid_to', 'source_url', 'published_at')
    candidates = {}
    for collection, resolved in (('activity_evidence', True), ('notice_evidence', False)):
        for row in context.get(collection) or []:
            if not isinstance(row, dict) or row.get('kind') != 'activity_permission':
                continue
            if (row.get('state') not in {'verified_closed', 'verified_open'} or row.get('activity') != activity
                    or row.get('location_key') != location.get('key') or not row.get('site_id')
                    or requested_site and row['site_id'] != requested_site or not _url(row.get('source_url'))):
                continue
            a, b, published = stamp(row.get('valid_from')), stamp(row.get('valid_to'), end=True), stamp(row.get('published_at'))
            if not (a and b and published and a <= b and a <= end and b >= start and published <= cutoff):
                continue
            identity = json.dumps({key: row.get(key) for key in fields}, sort_keys=True, ensure_ascii=False)
            if identity in candidates:
                candidates[identity]['resolved'] |= resolved
            else:
                candidates[identity] = {'row': row, 'start': max(a, start), 'end': min(b, end),
                                        'published': published, 'resolved': resolved, 'identity': identity}
    grouped = {}
    for item in candidates.values():
        grouped.setdefault(item['row']['site_id'], []).append(item)
    closures, opened = [], []
    epsilon = timedelta(microseconds=1)
    for site, items in grouped.items():
        boundaries = sorted({point for item in items for point in (item['start'], item['end'] + epsilon)})
        segments = []
        for left, right in zip(boundaries, boundaries[1:]):
            active = [item for item in items if item['start'] <= left <= item['end']]
            if not active:
                continue
            latest = max(item['published'] for item in active)
            winners = [item for item in active if item['published'] == latest]
            states = {item['row']['state'] for item in winners}
            state = next(iter(states)) if len(states) == 1 else 'conflicting'
            # Prefer a resolved duplicate when dates agree. A tie with opposite
            # states is uncertainty, never a verified ban or affirmative permit.
            winner = max(winners, key=lambda item: item['resolved'])
            segment = {'start': left, 'end': right - epsilon, 'state': state, 'winner': winner}
            if (segments and segments[-1]['end'] + epsilon == left and segments[-1]['state'] == state
                    and segments[-1]['winner']['identity'] == winner['identity']):
                segments[-1]['end'] = segment['end']
            else:
                segments.append(segment)
        for segment in segments:
            if segment['state'] != 'verified_closed':
                continue
            item, row = segment['winner'], deepcopy(segment['winner']['row'])
            if (segment['start'], segment['end']) != (item['start'], item['end']):
                row.update(source_valid_from=row['valid_from'], source_valid_to=row['valid_to'],
                           valid_from=segment['start'].isoformat(), valid_to=segment['end'].isoformat(),
                           supersession_applied=True)
            row.update(effective_closure_interval={'start': segment['start'].isoformat(), 'end': segment['end'].isoformat()},
                       knowledge_cutoff=cutoff.isoformat())
            closures.append(row)
        if (segments and segments[0]['start'] == start and segments[-1]['end'] == end
                and all(row['state'] == 'verified_open' and row['winner']['resolved'] for row in segments)
                and all(a['end'] + epsilon == b['start'] for a, b in zip(segments, segments[1:]))):
            seen = set()
            for segment in segments:
                item = segment['winner']
                if item['identity'] not in seen:
                    seen.add(item['identity'])
                    opened.append(item['row'])
    return closures, opened


def activity_gates(activity, context, location, start, end, rules):
    requested_site = location.get('site_id') or context.get('site_id')
    cutoff = knowledge_cutoff(start, context.get('as_of') or context.get('forecast_issue_cutoff'))
    prior_request = rules.get('resolved_for', {})
    prior_cutoff = stamp(prior_request.get('as_of'))
    same_period = stamp(prior_request.get('start')) == stamp(start) and stamp(prior_request.get('end')) == stamp(end)
    if prior_cutoff and same_period:
        cutoff = min(cutoff, prior_cutoff)
    official_rules = None
    if rules.get('schema') == 2:
        policy_location = {**location}
        if requested_site:
            policy_location['site_id'] = requested_site
        official_rules = load_rules(rules, policy_location, activity, start, end, as_of=cutoff)
    records = [row for row in _matching_evidence(context, activity, location, start, end, cutoff)
               if row.get('kind') != 'activity_permission']
    closures, opened = _permission_timeline(context, activity, location, start, end, cutoff)
    records += opened
    site_ids = {row['site_id'] for row in records if row.get('kind') == 'site_representativeness'
                and row.get('state') == 'verified' and row.get('value') in {True, 'passed', 'confirmed'}}
    # Every affirmative gate must refer to the same actual activity site.
    site_id = requested_site if requested_site and requested_site in site_ids else None
    records = [row for row in records if row['site_id'] == site_id] if site_id else []
    required = ['activity_permission', 'site_representativeness', 'forecast_semantics', 'site_hazard_check']
    required += {'swimming': ['lifeguard'], 'tidal_flat': ['tidal_return_plan'],
                 'shore_fishing': ['fishing_site'], 'boat_fishing': ['boat_eligibility']}[activity]
    additional = policy_requirements(official_rules['policy_selection'], activity) if official_rules else []
    required += [row['kind'] for row in additional if row['kind'] not in required]
    additional_labels = {row['kind']: row['label'] for row in additional}
    gates = []
    for kind in required:
        allowed = {'verified_open'} if kind == 'activity_permission' else {'verified'}
        successful = [row for row in records if row.get('kind') == kind and row.get('state') in allowed
                      and (kind == 'activity_permission' or row.get('value') in {True, 'passed', 'confirmed', 'available', 'approved'})]
        gates.append({'kind': kind, 'label': GATE_LABELS.get(kind, additional_labels.get(kind, kind)), 'state': 'passed' if successful else 'missing',
                      'sources': successful})
    if activity == 'swimming':
        quality = context.get('water_quality', {})
        matched_quality = (quality.get('state') == 'current' and quality.get('published_by_query_date') is not False
                and quality.get('site_id') == site_id and site_id is not None
                and _url(quality.get('source_url')) and valid_period(quality, start, end, cutoff))
        good = matched_quality and quality.get('reported_swimming_suitability') in {'适宜', '较适宜'}
        unsuitable = matched_quality and quality.get('reported_swimming_suitability') == '不适宜'
        gates.append({'kind': 'water_quality', 'label': '匹配浴场和活动时段的有效水质与适游评价',
                      'state': 'passed' if good else 'failed' if unsuitable else 'missing',
                      'sources': [quality] if good or unsuitable else []})
    for gate in gates:
        negative = [row for row in records if row.get('kind') == gate['kind'] and row.get('state') == 'verified'
                    and row.get('value') in {False, 'failed', 'unsafe', 'not_eligible'}]
        if negative:
            gate.update(state='failed', sources=negative)
    if official_rules:
        eligible = official_rules['policy_selection']['eligible_policies']
        gates.append({'kind': 'validated_activity_rule', 'label': '地域、活动、版本和时段匹配的已核官方依据',
            'state': 'passed' if eligible else 'missing',
            'sources': [{'source_url': row['source']['url'], 'title': row['title'],
                         'paragraph': row['source']['paragraph'], 'policy_id': row['id'],
                         'rule_kind': row['kind']} for row in eligible],
            'evidence_scope': 'official_rule_only', 'not_an_activity_permission': True,
            'coverage_notice': official_rules['policy_selection'].get('coverage_notice')})
    else:
        rule_valid = (rules.get('status') == 'validated' and rules.get('validated_by')
                      and rules.get('location_key') == location.get('key') and rules.get('site_id') == site_id and site_id
                      and activity in rules.get('applicable_activities', [])
                      and _url(rules.get('source_url')) and valid_period(rules, start, end, cutoff))
        gates.append({'kind': 'validated_activity_rule', 'label': '经场地业务核定、在有效期内的活动规则',
                      'state': 'passed' if rule_valid else 'missing',
                      'sources': [rules['source_url']] if rule_valid else []})
    for gate in gates:
        gate['knowledge_cutoff'] = cutoff.isoformat()
    return gates, closures, site_id


def tide_reference(tide, rules, start=None, end=None):
    if not tide or tide.get('state') != 'ready':
        return {'state': 'insufficient', 'process': [], 'low_tide_reference_windows': []}
    series = sorted([(stamp(row.get('time')), row.get('value_cm')) for row in tide.get('series', [])
                     if stamp(row.get('time')) and type(row.get('value_cm')) in {int, float}])
    # Hourly sample differences describe a net change over an interval. They
    # cannot establish the instantaneous phase at its starting hour: a 14:04
    # high tide can be hidden inside the net-decreasing 14:00--15:00 interval.
    extrema = sorted([
        {'time': stamp(row.get('time')), 'kind': kind, 'value_cm': row.get('value_cm')}
        for collection, kind in (('high_tides', 'high'), ('low_tides', 'low'))
        for row in tide.get(collection, []) if stamp(row.get('time'))
    ], key=lambda row: row['time'])
    def public_extremum(row):
        return {'time': row['time'].isoformat(), 'kind': row['kind'],
                'value_cm': row['value_cm']} if row else None

    process = []
    for index, (when, value) in enumerate(series):
        previous = series[index - 1] if index else None
        next_row = series[index + 1] if index + 1 < len(series) else None
        other = next_row or previous
        contiguous = bool(other and abs((other[0] - when).total_seconds()) == 3600)
        delta = (other[1] - value if next_row else value - other[1]) if contiguous else None
        direction = 'unknown' if delta is None else 'rising' if delta > 0 else 'falling' if delta < 0 else 'flat'
        exact = [row for row in extrema if row['time'] == when]
        previous_extremum = next((row for row in reversed(extrema) if row['time'] < when), None)
        next_extremum = next((row for row in extrema if row['time'] > when), None)
        # Display reference only: do not carry one isolated peak/trough across
        # a long missing-data span or infer measured current direction.
        if previous_extremum and when - previous_extremum['time'] > timedelta(hours=8):
            previous_extremum = None
        if next_extremum and next_extremum['time'] - when > timedelta(hours=8):
            next_extremum = None
        phase, label = 'unknown', '缺精确高低潮时刻，无法判别该时刻潮相'
        if exact and len({row['kind'] for row in exact}) == 1:
            phase = 'high_tide' if exact[0]['kind'] == 'high' else 'low_tide'
            label = '高潮时刻' if phase == 'high_tide' else '低潮时刻'
        elif not exact:
            reference = previous_extremum or next_extremum
            # Two same-kind adjacent extrema indicate a missing turn; do not
            # interpolate one continuous phase between them.
            consistent = not (previous_extremum and next_extremum
                              and previous_extremum['kind'] == next_extremum['kind'])
            if reference and consistent:
                if previous_extremum:
                    phase = 'rising' if previous_extremum['kind'] == 'low' else 'falling'
                    label = ('低潮后参考涨潮段' if phase == 'rising' else '高潮后参考落潮段')
                else:
                    phase = 'rising' if next_extremum['kind'] == 'high' else 'falling'
                    label = ('高潮前参考涨潮段' if phase == 'rising' else '低潮前参考落潮段')
        interval_start = min(when, other[0]) if contiguous else None
        interval_end = max(when, other[0]) if contiguous else None
        turns = [public_extremum(row) for row in extrema
                 if contiguous and interval_start < row['time'] < interval_end]
        trend_label = {'unknown': '相邻样本不连续，无法比较', 'rising': '采样区间净上升',
                       'falling': '采样区间净下降', 'flat': '采样区间两端样本持平'}[direction]
        if turns:
            trend_label += '（区间内含高低潮转折，不代表整段同向涨落）'
        process.append({'time': when.isoformat(), 'value_cm': value, 'phase': phase,
                        'phase_label': label, 'method': 'reported_tide_extrema_reference' if phase != 'unknown' else 'unavailable',
                        'phase_basis': {'previous_extremum': public_extremum(previous_extremum),
                                        'next_extremum': public_extremum(next_extremum),
                                        'exact_extrema': [public_extremum(row) for row in exact],
                                        'max_single_extremum_offset_hours': 8,
                                        'not_instant_measurement': True},
                        'interval_trend': {'start': interval_start.isoformat() if interval_start else None,
                                           'end': interval_end.isoformat() if interval_end else None,
                                           'direction': direction, 'delta_cm': delta, 'label': trend_label,
                                           'comparison': 'following_sample' if next_row else 'preceding_sample',
                                           'method': 'adjacent_hourly_samples', 'contained_extrema': turns,
                                           'not_instant_phase': True},
                        'not_measured_shore_waterline': True})
    legacy_window = rules.get('schema') == 1
    minutes = max(0, min(120, int(rules.get('low_tide_reference_minutes', 60)))) if legacy_window else 0
    windows = []
    for low in tide.get('low_tides', []) if legacy_window else []:
        when = stamp(low.get('time'))
        if not when:
            continue
        first, last = when - timedelta(minutes=minutes), when + timedelta(minutes=minutes)
        if start: first = max(first, start)
        if end: last = min(last, end)
        if first > last: continue
        windows.append({'low_tide': low, 'start': first.isoformat(), 'end': last.isoformat(),
                        'reference_minutes_each_side': minutes, 'not_a_safe_access_window': True,
                        'reason': '低潮前后仅供查看潮过程；未用实测滩高和返程临界水位推导，不能当作赶海进入或返回安全线。'})
    complete = (len(series) >= 2 and not tide.get('partial')
                and all(b[0] - a[0] == timedelta(hours=1) for a,b in zip(series, series[1:]))
                and (start is None or series[0][0] <= start)
                and (end is None or series[-1][0] >= end))
    return {'state': 'ready' if complete else 'partial', 'process': process,
            'low_tide_reference_windows': windows, 'datum': tide.get('datum'),
            'low_tide_reference_events': [row for row in tide.get('low_tides', []) if stamp(row.get('time'))
                and (start is None or stamp(row['time']) >= start) and (end is None or stamp(row['time']) <= end)],
            'return_window_rule_state': 'legacy_explicit_override' if legacy_window else 'no_official_fixed_offset_rule',
            'phase_notice': '逐时潮位为整点样本；潮相按原资料精确高潮、低潮时刻作参考判别。相邻整点差只表示所标采样区间的净升降，不能把区间趋势当成整点瞬时涨落潮；高潮前后、低潮前后需按精确转折时刻区分。这不是实测水流方向或滩涂出露判断。',
            'not_a_safe_access_window': True}
