from types import SimpleNamespace

import pytest

from forecast_runtime.activity_locations import resolve_activity_location


def runtime():
    rows = [
        ('1182', '朱家尖街道', '舟山市', '普陀区', 122.31656, 29.94341),
        ('1190', '东沙镇', '舟山市', '岱山县', 122.198, 30.23728),
        ('1195', '菜园镇', '舟山市', '嵊泗县', 122.50695, 30.69905),
        ('1018', '爵溪街道', '宁波市', '象山县', 121.984, 29.455),
        ('1091', '石塘镇', '台州市', '温岭市', 121.63433, 28.30117),
        ('1138', '马站镇', '温州市', '苍南县', 120.53517, 27.2465),
        ('1160', '北岙街道', '温州市', '洞头区', 121.146, 27.85983),
        ('1010', '乍浦镇', '嘉兴市', '平湖市', 121.08333, 30.6),
        ('1003', '盐官镇', '嘉兴市', '海宁市', 120.55, 30.4),
    ]
    return SimpleNamespace(coasts=lambda: [dict(Id=id, name=name, Prefecture=city, County=county,
        lon=lon, lat=lat) for id, name, city, county, lon, lat in rows])


@pytest.mark.parametrize('question', ['浙江省明天可以游泳吗', '舟山市明天能海钓吗',
    '宁波明天赶海', '洞头区能游泳吗', '大沙岙能游泳吗', '东沙能海钓吗', '金沙滩能游泳吗',
    '杭州湾明天能游泳吗'])
def test_broad_or_ambiguous_location_never_falls_back_to_zhujiajian(question):
    assert resolve_activity_location(runtime(), question) is None


@pytest.mark.parametrize('question,key,coast_id', [
    ('朱家尖南沙明天能游泳吗', 'zhujiajian', '1182'),
    ('舟山东沙镇明天能赶海吗', 'coast:1190', '1190'),
    ('象山松兰山明天能游泳吗', 'songlanshan', '1018'),
    ('温岭石塘金沙滩能游泳吗', 'wenling_jinshatan', '1091'),
    ('苍南渔寮大沙滩能游泳吗', 'yuliao', '1138'),
    ('洞头大沙岙明天能游泳吗', 'dongtou_dashaao', '1160'),
    ('嵊泗基湖沙滩能游泳吗', 'jihu', '1195'),
    ('嘉兴乍浦明天能岸钓吗', 'coast:1010', '1010'),
    ('海宁盐官镇能赶海吗', 'coast:1003', '1003'),
])
def test_existing_coast_registry_coordinates_and_long_names(question, key, coast_id):
    result = resolve_activity_location(runtime(), question)
    assert result['key'] == key
    assert result['coast_id'] == coast_id
    assert result['province'] == '浙江省'
    assert 'lon' in result and 'lat' in result
    assert result['coordinate_status'] != 'surveyed_beach'
    if key != 'zhujiajian':
        coast = next(row for row in runtime().coasts() if str(row['Id']) == coast_id)
        assert (result['lon'], result['lat']) == (coast['lon'], coast['lat'])


def test_different_dashaao_stations_and_two_locations_require_split():
    result = resolve_activity_location(runtime(), '南麂大沙岙能游泳吗')
    assert result['needs_location']
    assert result['requested_site']['quality_site'] == '温州南麂大沙岙海水浴场'
    assert resolve_activity_location(runtime(), '南麂大沙岙和洞头大沙岙能游泳吗')['needs_location']


@pytest.mark.parametrize('question', [
    '朱家尖东经120.5北纬27.5能游泳吗', '洞头大沙岙东经122.4北纬29.9能游泳吗',
    '朱家尖东经110北纬20能游泳吗', '台州朱家尖能游泳吗', '岱山朱家尖能游泳吗',
])
def test_coordinate_and_admin_conflicts_prevent_source_attachment(question):
    result = resolve_activity_location(runtime(), question)
    assert result['coordinate_conflict']
    assert result['needs_location']


def test_explicit_point_is_user_supplied_not_surveyed_or_province_verified():
    result = resolve_activity_location(runtime(), '东经121.6北纬28.3能海钓吗')
    assert result['key'] == 'user_point'
    assert result['coordinate_status'] == 'user_supplied'
    assert result.get('site_id') is None
    assert result.get('coast_id') is None
    assert result['province'] is None
    assert result['administration_unverified']
    assert result['country'] == 'CN'


def test_service_envelope_does_not_assert_zhejiang_administration():
    # This coordinate is in the large service envelope and must not activate
    # Zhejiang-only policy scopes merely because grid data can be requested.
    result = resolve_activity_location(runtime(), '东经119.7北纬26.5能船钓吗')
    assert result['province'] is None
    assert result['administration_basis'] == 'service_envelope_only'
    assert result['administration_unverified']
    declared = resolve_activity_location(runtime(), '浙江省东经121.6北纬28.3能海钓吗')
    assert declared['province'] == '浙江省'
    assert declared['administration_basis'] == 'user_declared_province_unverified'
    assert declared['administration_unverified']


def test_named_site_without_reference_does_not_invent_one():
    result = resolve_activity_location(runtime(), '南麂大沙岙东经121.1北纬27.45游泳')
    assert result['coordinate_site_unverified']
    assert result['coast_id'] is None
    assert result['site_id'] == 'nanji_dashaao'
