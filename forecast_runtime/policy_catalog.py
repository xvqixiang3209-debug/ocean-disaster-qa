"""Official activity sources, selected without extending geographic/legal scope.

An applicable policy is evidence for a rule, never evidence that a site is open.
Unconfirmed, withdrawn and text-unavailable sources remain separate references.
"""
from copy import deepcopy
from datetime import date, datetime, time
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from .storage import BEIJING, local_now

CATALOG_PATH = Path(__file__).with_name('activity_policy_catalog.json')
ACTIVITIES = {'tidal_flat', 'swimming', 'shore_fishing', 'boat_fishing'}
ACTIVE_STATUSES = {'current', 'current_text_verified', 'current_method_confirmed', 'guidance_published'}
EVALUATORS = {'bathing_water_quality_temperature', 'gust_wind_risk'}


def _stamp(value, last=False):
    if isinstance(value, datetime):
        return value.replace(tzinfo=BEIJING) if value.tzinfo is None else value.astimezone(BEIJING)
    if isinstance(value, date):
        return datetime.combine(value, time.max if last else time.min, BEIJING)
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time.max if last else time.min, BEIJING)
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=BEIJING) if parsed.tzinfo is None else parsed.astimezone(BEIJING)
    except ValueError:
        return None


def _official_url(value):
    try:
        parsed = urlsplit(value or '')
        return (parsed.scheme in {'http', 'https'} and parsed.hostname
                and parsed.hostname.endswith('.gov.cn') and not parsed.username and not parsed.password)
    except ValueError:
        return False


def knowledge_cutoff(start=None, as_of=None):
    """Only use knowledge available now and at the requested reconstruction time."""
    values = [local_now()]
    for value in (start, as_of):
        parsed = _stamp(value)
        if parsed is not None:
            values.append(parsed)
    return min(values)


def load_catalog(override=None):
    catalog = deepcopy(override) if isinstance(override, dict) else json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    if catalog.get('schema') != 1 or not isinstance(catalog.get('version'), str) or not isinstance(catalog.get('policies'), list):
        raise ValueError('invalid_official_activity_catalog')
    seen = set()
    for policy in catalog['policies']:
        source = policy.get('source', {}) if isinstance(policy, dict) else {}
        identity = policy.get('id') if isinstance(policy, dict) else None
        if (not identity or identity in seen or policy.get('authority') != 'official'
                or not _official_url(source.get('url')) or not source.get('paragraph')
                or not _stamp(source.get('verification', {}).get('checked_at'))
                or not _stamp(policy.get('published_at')) or not _stamp(policy.get('effective_from'))
                or not isinstance(policy.get('scope'), dict)
                or not set(policy.get('activities', [])).issubset(ACTIVITIES)):
            raise ValueError('invalid_official_activity_policy')
        seen.add(identity)
        for rule in policy.get('numeric_rules', []):
            if rule.get('evaluator') not in EVALUATORS or not rule.get('quantity') or not rule.get('unit'):
                raise ValueError('invalid_official_numeric_rule')
            for key in ('suitable_min', 'suitable_max', 'maximum', 'major_above'):
                value = rule.get(key)
                if value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
                    raise ValueError('invalid_official_numeric_rule')
    return catalog


def normalize_location(location):
    """Use explicit administration/known place keys; never infer a boundary from NC cells."""
    result = deepcopy(location) if isinstance(location, dict) else {'key': location} if isinstance(location, str) else {}
    aliases = {'浙江': 'zhejiang', '浙江省': 'zhejiang', '33': 'zhejiang',
               '舟山': 'zhoushan', '舟山市': 'zhoushan', '3309': 'zhoushan',
               '普陀': 'putuo', '普陀区': 'putuo', '330903': 'putuo',
               '岱山': 'daishan', '岱山县': 'daishan', '330921': 'daishan',
               '嵊泗': 'shengsi', '嵊泗县': 'shengsi', '330922': 'shengsi',
               '定海': 'dinghai', '定海区': 'dinghai', '330902': 'dinghai'}
    for key in ('province', 'city', 'county'):
        if key in result:
            result[key] = aliases.get(str(result[key]), result[key])
    known = {'zhujiajian': ('zhejiang', 'zhoushan', 'putuo'),
             'putuo': ('zhejiang', 'zhoushan', 'putuo'),
             'daishan': ('zhejiang', 'zhoushan', 'daishan'),
             'shengsi': ('zhejiang', 'zhoushan', 'shengsi'),
             'dinghai': ('zhejiang', 'zhoushan', 'dinghai')}
    place = aliases.get(str(result.get('key')), result.get('key'))
    if place in known:
        for key, value in zip(('province', 'city', 'county'), known[place]):
            result.setdefault(key, value)
    if result.get('country') in {'CN', 'China', '中国'} or not result.get('country') and result.get('province') == 'zhejiang':
        result['country'] = 'CN'
    return result


def _scope_reason(policy, location):
    scope = policy['scope']
    for key in ('country', 'province', 'city', 'county'):
        if scope.get(key) and scope[key] != location.get(key):
            return 'geography_unknown' if not location.get(key) else 'outside_' + key
    if scope.get('location_keys') and location.get('key') not in scope['location_keys']:
        return 'outside_location'
    if scope.get('site_ids') and location.get('site_id') not in scope['site_ids']:
        return 'site_not_matched'
    if scope.get('protected_area_ids') and location.get('protected_area_id') not in scope['protected_area_ids']:
        return 'protected_area_not_verified'
    # A declared incompatible type excludes the rule. Unknown site type stays
    # an explicit application condition; its observation/permission gate must
    # separately prove the actual bathing site.
    if scope.get('site_types') and location.get('site_type') and location['site_type'] not in scope['site_types']:
        return 'incompatible_site_type'
    return None


def select_policies(catalog, location=None, activity=None, start=None, end=None, as_of=None):
    if activity is not None and activity not in ACTIVITIES:
        raise ValueError('unknown_activity')
    location = normalize_location(location)
    first, last = _stamp(start), _stamp(end, last=True)
    cutoff = knowledge_cutoff(first, as_of)
    eligible, references, excluded = [], [], []
    for original in catalog['policies']:
        policy = deepcopy(original)
        reason = None
        if activity is not None and activity not in policy['activities']:
            reason = 'different_activity'
        reason = reason or _scope_reason(policy, location)
        effective, expires = _stamp(policy['effective_from']), _stamp(policy.get('effective_to'), last=True)
        published = _stamp(policy['published_at'])
        if not reason and published > cutoff:
            reason = 'not_published_by_knowledge_cutoff'
        if not reason and ((first and first < effective) or (first and published > first)
                           or (last and last < effective)):
            reason = 'not_yet_effective_or_published'
        if not reason and expires and (not last or last > expires):
            reason = 'expired_or_withdrawn'
        if not reason and first and last and first > last:
            reason = 'invalid_query_period'
        if reason:
            excluded.append({'id': policy['id'], 'title': policy['title'], 'status': policy['status'],
                             'reason': reason, 'source': policy['source'], 'scope': policy['scope']})
        elif policy.get('execution') != 'evaluate' or policy.get('status') not in ACTIVE_STATUSES:
            policy['selection_reason'] = 'withdrawn' if policy.get('status') == 'withdrawn' else 'reference_only_not_executable'
            references.append(policy)
        else:
            eligible.append(policy)
    return {'state': 'matched' if eligible else 'no_matching_rule',
            'catalog_version': catalog['version'], 'catalog_checked_at': catalog.get('checked_at'),
            'request': {'location': location, 'activity': activity,
                        'start': first.isoformat() if first else None, 'end': last.isoformat() if last else None,
                        'as_of': cutoff.isoformat()},
            'eligible_policies': eligible, 'reference_policies': references, 'excluded_policies': excluded,
            'coverage_notice': catalog.get('coverage_notice'), 'not_an_activity_permission': True}


def policy_requirements(selection, activity):
    result = {}
    for policy in selection['eligible_policies']:
        for item in policy.get('requirements', []):
            if item.get('activities') and activity not in item['activities']:
                continue
            result.setdefault(item['kind'], {**item, 'policy_ids': [], 'policy_sources': []})
            result[item['kind']]['policy_ids'].append(policy['id'])
            result[item['kind']]['policy_sources'].append(policy['source'])
    return list(result.values())
