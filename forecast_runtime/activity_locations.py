"""Resolve Zhejiang activity locations without fabricating surveyed beach points.

Coordinates come from the existing coast registry (or the user), except the
previously configured Zhujiajian regional marine sample. Administrative places
and water-quality station names are never interchangeable with beach polygons.
"""
from __future__ import annotations

import math
import re

from .grids import distance_km


PROVINCE = '浙江省'
CITY_NAMES = ('嘉兴市', '杭州市', '宁波市', '舟山市', '台州市', '温州市')
# These names identify a monitoring station, not a geodetically surveyed point.
# coast_id selects an existing business reference; no beach coordinates are made up.
ACTIVITY_SITES = {
    'zhujiajian': {'name': '朱家尖', 'aliases': ('朱家尖',),
        'city': '舟山市', 'county': '普陀区', 'coast_id': '1182',
        'quality_site': '舟山朱家尖海水浴场'},
    'jihu': {'name': '嵊泗基湖沙滩', 'aliases': ('嵊泗基湖沙滩', '基湖沙滩', '基湖浴场', '基湖'),
        'city': '舟山市', 'county': '嵊泗县', 'coast_id': '1195',
        'quality_site': '舟山嵊泗基湖沙滩海水浴场'},
    'songlanshan': {'name': '象山松兰山', 'aliases': ('象山松兰山', '松兰山'),
        'city': '宁波市', 'county': '象山县', 'coast_id': '1018',
        'quality_site': '宁波象山松兰山海滨浴场'},
    'wenling_jinshatan': {'name': '温岭金沙滩', 'aliases': ('温岭金沙滩', '石塘金沙滩'),
        'city': '台州市', 'county': '温岭市', 'coast_id': '1091',
        'quality_site': '温岭金沙滩浴场'},
    'yuliao': {'name': '苍南渔寮大沙滩', 'aliases': ('苍南渔寮大沙滩', '渔寮大沙滩', '渔寮'),
        'city': '温州市', 'county': '苍南县', 'coast_id': '1138',
        'quality_site': '温州渔寮大沙滩海水浴场'},
    'dongtou_dashaao': {'name': '洞头大沙岙', 'aliases': ('洞头大沙岙', '洞头大沙澳'),
        'city': '温州市', 'county': '洞头区', 'coast_id': '1160',
        'quality_site': '温州洞头大沙岙海水浴场'},
    'nanji_dashaao': {'name': '南麂大沙岙', 'aliases': ('南麂大沙岙', '南麂大沙澳', '南麂岛', '南麂'),
        'city': '温州市', 'county': '平阳县', 'coast_id': None,
        'quality_site': '温州南麂大沙岙海水浴场',
        'protection_area': '南麂列岛国家级海洋自然保护区'},
}


def _coordinate_match(question):
    return re.search(r'(?:东经|经度|lon)\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*(?:度|°|E)?[^\d]{0,15}'
                     r'(?:北纬|纬度|lat)\s*[:：]?\s*(-?\d+(?:\.\d+)?)', question, re.I)


def _city_mentions(question):
    return {city for city in CITY_NAMES if city in question or city[:-1] in question}


def _failure(reason, **fields):
    return {'key': 'unresolved_zhejiang_activity', 'province': PROVINCE,
            'coordinate_conflict': True, 'needs_location': True,
            'coordinate_note': reason, **fields}


def _site_matches(question):
    matches = [(len(alias), key, site) for key, site in ACTIVITY_SITES.items()
               for alias in site['aliases'] if alias in question]
    # Bare 南沙、东沙、金沙滩、大沙岙 occur in several places.
    if ('朱家尖' in question or '普陀' in question) and re.search(r'南沙|东沙|乌石塘|白山', question):
        site = ACTIVITY_SITES['zhujiajian']
        matches.append((20, 'zhujiajian', site))
    if '舟山' in question and '南沙' in question and '岱山' not in question:
        matches.append((10, 'zhujiajian', ACTIVITY_SITES['zhujiajian']))
    if ('温岭' in question or '石塘' in question) and '金沙滩' in question:
        matches.append((20, 'wenling_jinshatan', ACTIVITY_SITES['wenling_jinshatan']))
    if '洞头' in question and re.search(r'大沙[岙澳]', question):
        matches.append((20, 'dongtou_dashaao', ACTIVITY_SITES['dongtou_dashaao']))
    if '南麂' in question and re.search(r'大沙[岙澳]', question):
        matches.append((20, 'nanji_dashaao', ACTIVITY_SITES['nanji_dashaao']))
    unique = {key: site for _, key, site in sorted(matches)}
    return unique


def _coast_aliases(row):
    name = str(row.get('name') or '')
    aliases = [name] if name else []
    short = re.sub(r'(?:街道|镇|乡)$', '', name)
    if len(short) >= 2 and short not in {'东沙', '南沙', '白山'}:
        aliases.append(short)
    return aliases


def _coast_match(coasts, question):
    cities = _city_mentions(question)
    candidates = []
    for row in coasts:
        city = row.get('Prefecture')
        if cities and city not in cities:
            continue
        for alias in _coast_aliases(row):
            if alias in question:
                candidates.append((len(alias), row))
    if not candidates:
        return None
    longest = max(size for size, _ in candidates)
    finalists = {str(row['Id']): row for size, row in candidates if size == longest}
    if len(finalists) > 1:
        return _failure('该地名对应多个岸段，请补充市县、完整岸段名或坐标。',
                        candidate_coasts=[{'coast_id': key, 'name': row['name'],
                            'city': row.get('Prefecture'), 'county': row.get('County')}
                            for key, row in finalists.items()])
    row = next(iter(finalists.values()))
    if row.get('lon') is None or row.get('lat') is None:
        return _failure('所选岸段缺少可查询坐标，请补充东经和北纬。', coast_id=str(row['Id']))
    return {'key': 'coast:' + str(row['Id']), 'name': row['name'] + '岸段参考点',
        'province': PROVINCE, 'city': row.get('Prefecture'), 'county': row.get('County'),
        'coast_id': str(row['Id']), 'lon': float(row['lon']), 'lat': float(row['lat']),
        'coordinate_status': 'coast_reference_not_activity_site',
        'coordinate_note': '采用既有业务岸段参考坐标，可能为参考潮站位置；不是浴场、礁石或滩涂实测点。'}


def resolve_activity_location(runtime, question, configured_locations=None):
    """Return one resolved point, a conflict record, or None for a broad request.

    A city/province or county alone deliberately returns None. Existing regional
    reference positions remain labelled as such and cannot pass site-representativeness
    gates. Explicit points outside the Zhejiang marine envelope are rejected.
    """
    coasts = runtime.coasts()
    named = _site_matches(question)
    if len(named) > 1:
        return _failure('问题涉及多个活动场地，请逐一给地点或分别查询。',
                        candidate_sites=[row['name'] for row in named.values()])
    known = None
    if named:
        key, site = next(iter(named.items()))
        # A familiar beach alias must not overrule an explicitly different
        # province or city. Unknown municipalities remain unresolved too.
        provinces=re.findall(r'([\u4e00-\u9fff]{2,6}?)省',question)
        if any(not name.endswith('浙江') for name in provinces):
            return _failure('所述省份与已登记活动场地不一致，请确认地点；不套用浙江参考点数据。')
        explicit_cities=re.findall(r'([\u4e00-\u9fff]{2,9}?)市',question)
        if any(not name.endswith(site['city'][:-1]) for name in explicit_cities):
            return _failure('所述城市与已登记活动场地不一致，请确认地点；不套用该场地数据。')
        cities = _city_mentions(question)
        if cities and site['city'] not in cities:
            return _failure('所述市名与活动场地不一致，请确认地点。')
        counties = {row.get('County') for row in coasts if row.get('County') and
                    (row['County'] in question or re.sub(r'[县市区]$', '', row['County']) in question)}
        if counties and site['county'] not in counties:
            return _failure('所述县区与活动场地不一致，请确认地点。')
        known = {'key': key, 'name': site['name'] + '区域参考点', 'province': PROVINCE,
                 'city': site['city'], 'county': site['county'],
                 'quality_site': site['quality_site'], 'coast_id': site['coast_id']}
        if site.get('protection_area'):
            known['protection_area'] = site['protection_area']
        if key == 'zhujiajian':
            # Preserve the pre-existing explicitly declared marine sample.
            known.update(lon=122.4, lat=29.9,
                coordinate_status='configured_regional_sample_not_beach_survey',
                coordinate_note='朱家尖东侧既有区域采样点，不是南沙或东沙浴场实测定位。')
            names = {'nansha': '南沙', 'dongsha': '东沙', 'wushitang': '乌石塘', 'baishan': '白山'}
            specific = [sid for sid, name in names.items() if name in question]
            if len(specific) > 1:
                return _failure('问题涉及朱家尖多个场地，请分别查询。')
            if specific:
                known['site_id'] = specific[0]
        else:
            known['site_id'] = key
            coast = next((row for row in coasts if str(row['Id']) == site['coast_id']), None)
            if coast and coast.get('lon') is not None and coast.get('lat') is not None:
                known.update(lon=float(coast['lon']), lat=float(coast['lat']),
                    coordinate_status='coast_reference_not_activity_site',
                    coordinate_source={'dataset': 'existing_business_coast_registry', 'coast_id': site['coast_id'],
                                       'coast_name': coast['name']},
                    coordinate_note='采用对应行政区域的既有业务岸段参考坐标，可能为参考潮站位置；不是该浴场实测点。')
        configured = (configured_locations or {}).get(key)
        if configured:
            known.update(configured)
            known['key'] = key
    else:
        known = _coast_match(coasts, question)
        if known and known.get('coordinate_conflict'):
            return known
    coordinates = _coordinate_match(question)
    if coordinates:
        lon, lat = map(float, coordinates.groups())
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError('经纬度超出范围')
        # A bounding envelope is a service scope check, not a province polygon.
        if not (118 <= lon <= 125 and 26 <= lat <= 32):
            return _failure('显式坐标不在浙江沿海服务范围内，请确认；不关联浙江场地公告。',
                            lon=lon, lat=lat, coordinate_status='user_supplied')
        if known and 'lon' in known and distance_km(lon, lat, known['lon'], known['lat']) > 25:
            return _failure('显式坐标与所述地点的参考区域不一致，请确认；不关联该地潮站或公告。',
                            lon=lon, lat=lat, coordinate_status='user_supplied')
        result = {**(known or {}), 'key': known['key'] if known else 'user_point',
            'name': '用户指定坐标', 'country': 'CN',
            'province': known.get('province') if known else PROVINCE if '浙江' in question else None,
            'administration_unverified': not bool(known),
            'administration_basis': 'named_existing_zhejiang_reference' if known else
                'user_declared_province_unverified' if '浙江' in question else 'service_envelope_only',
            'lon': lon, 'lat': lat,
            'coordinate_status': 'user_supplied',
            'coordinate_note': '按用户坐标查询；坐标位于服务范围包络内，不等于已经核实行政边界或具体浴场代表关系。'}
        if known and 'lon' not in known:
            result['coordinate_site_unverified'] = True
            result['coordinate_note'] += '当前场地缺可靠定位，所述场地与该坐标的关系未核实。'
        # Coordinate coincidence does not establish monitoring-site identity.
        return result
    if known and 'lon' not in known:
        return _failure('已识别活动场地，但既有岸段库没有可靠的对应参考点，请补充东经和北纬。',
                        requested_site=known)
    return known
