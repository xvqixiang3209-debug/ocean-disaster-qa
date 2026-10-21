"""P1 place-resolution contracts: no service, no network, no model calls."""
from copy import deepcopy

import pytest

from forecast_runtime.objective_scope import (
    LEGACY_SCOPE_VERSION, SCOPE_VERSION, available_scopes, place_mentions,
    resolve_scope, resolve_scope_request, validated_scope,
)


@pytest.mark.parametrize('question,scope_id', [
    ('绘制朱家尖明天08时海浪二维图', 'zhujiajian'),
    ('浙江省舟山市朱家尖明天8时海浪图', 'zhujiajian'),
    ('舟山明天海面风分布', 'zhoushan'),
    ('宁波和舟山未来72小时海浪图', 'ningbo+zhoushan'),
    ('不是嵊泗，是舟山，明天08时海浪图', 'zhoushan'),
    ('不要朱家尖，而是温州风浪报告', 'wenzhou'),
    ('朱家尖改成宁波海浪图', 'ningbo'),
    ('朱家尖同一时刻换成海面风', 'zhujiajian'),
    ('不是嵊泗而是舟山海浪图', 'zhoushan'),
    ('宁波海浪图，不是舟山', 'ningbo'),
    ('显示2026年10月21日08时浙江近海海浪分布图', 'zhejiang'),
    ('08时30分浙江近海海浪分布图', 'zhejiang'),
    ('浙江近海气压分布图，数据集ID unknown_20261021', 'zhejiang'),
    ('中国浙江近海海浪图', 'zhejiang'),
    ('明天早上浙江近海海浪图', 'zhejiang'),
    ('朱家尖未来七十二小时近海风浪报告', 'zhujiajian'),
])
def test_registered_and_corrected_places(question, scope_id):
    result = resolve_scope_request(question)
    assert result['state'] == 'resolved'
    assert result['scope']['id'] == scope_id
    assert result['notice'] == ''
    assert result['scope']['version'] == SCOPE_VERSION


@pytest.mark.parametrize('question,place,parent,parent_id', [
    ('绘制嵊泗明天8时海浪二维图', '嵊泗', '舟山', 'zhoushan'),
    ('嵊泗县明天海浪怎么样', '嵊泗', '舟山', 'zhoushan'),
    ('嵊泗列岛未来三天海况', '嵊泗', '舟山', 'zhoushan'),
    ('舟山普陀明天8时海浪图', '普陀', '舟山', 'zhoushan'),
    ('同一时刻换成普陀附近', '普陀', '舟山', 'zhoushan'),
    ('普陀山岛明天海浪图', '普陀山', '舟山', 'zhoushan'),
    ('象山明天8时海浪怎么样', '象山', '宁波', 'ningbo'),
    ('石塘未来72小时海浪图', '石塘', '台州', 'taizhou'),
    ('基湖海浪图', '基湖', '舟山', 'zhoushan'),
    ('南麂风浪图', '南麂', '温州', 'wenzhou'),
    ('绘制浙江舟山定海区附近海浪图', '定海', '舟山', 'zhoushan'),
])
def test_known_unboxed_place_has_explicit_parent_notice(question, place, parent, parent_id, monkeypatch):
    monkeypatch.delenv('OBJECTIVE_UNBOXED_POLICY', raising=False)
    result = resolve_scope_request(question)
    assert result['state'] == 'parent_fallback'
    assert result['requested_places'] == [place]
    assert result['parents'] == {place: parent}
    assert result['scope']['id'] == parent_id
    assert place in result['notice'] and parent in result['notice']
    assert '不代表' in result['notice']
    assert result['notice'] in resolve_scope(question)['limitations']


def test_parent_policy_can_require_scope_confirmation(monkeypatch):
    monkeypatch.setenv('OBJECTIVE_UNBOXED_POLICY', 'clarify')
    result = resolve_scope_request('嵊泗未来两天风浪怎么样')
    assert result['state'] == 'needs_scope' and result['scope'] is None
    assert result['reason'] == 'parent_confirmation_required'
    assert '舟山' in result['notice']
    with pytest.raises(ValueError):
        resolve_scope('嵊泗海浪图')


@pytest.mark.parametrize('place', [
    '东极岛', '洋山港', '杭州湾', '东海', '象山港',
    '珍珠岛', '清溪港', '碧水湾', '龙潭县', '未知镇', '九寨沟',
])
def test_unknown_or_unscoped_places_do_not_become_zhejiang(place):
    result = resolve_scope_request('绘制' + place + '明天08时海浪二维图')
    assert result['state'] == 'needs_scope'
    assert result['scope'] is None
    assert result['unknown'] and result['requested_places']
    with pytest.raises(ValueError):
        resolve_scope(place + '明天海浪图')


@pytest.mark.parametrize('place', ['厦门', '福建', '上海', '广东', '深圳', '青岛', '海南省', '日本', '太平洋'])
def test_outside_zhejiang_is_rejected_before_calculation(place):
    result = resolve_scope_request('绘制' + place + '明天海浪二维图')
    assert result['state'] == 'out_of_region'
    assert result['scope'] is None
    assert result['outside']
    assert '未用浙江结果代答' in result['notice']


@pytest.mark.parametrize('question', [
    '明天08时海浪二维图', '未来72小时近海风浪报告', '海域风浪怎么样',
    '绘制当地海域海浪图', '比较北部和中部参考统计分区海浪过程',
    '同一时刻风浪都显示', '换成风场和浪场', '选择地图范围看海浪',
    '未来三天近海风浪图', '未来两天近海海浪报告',
    '三天的近海浪高图', '今后三天近海风浪报告', '这次近海海浪图',
])
def test_generic_field_time_and_area_words_do_not_become_unknown(question):
    assert place_mentions(question) == []
    result = resolve_scope_request(question)
    assert result['state'] == 'resolved' and result['reason'] == 'default'
    assert result['scope']['id'] == 'zhejiang'
    assert '未指定地点' in result['notice']
    assert result['notice'] in resolve_scope(question)['limitations']


def test_validated_explicit_bounds_win_over_any_named_place():
    result = resolve_scope_request('厦门，地图范围：122.1,29.6,122.6,30.1 明天海浪图')
    assert result['state'] == 'resolved' and result['reason'] == 'explicit_bounds'
    assert result['scope']['id'] == 'map_bounds'
    assert result['scope']['bounds'] == [122.1, 29.6, 122.6, 30.1]


@pytest.mark.parametrize('question,bounds', [
    ('地图范围：122,30,121,31，海浪图', None),
    ('地图范围：122,30,123，海浪图', None),
    ('舟山', [True, 29, 123, 30]),
    ('舟山', [121, 30, float('nan'), 31]),
])
def test_invalid_explicit_bounds_do_not_default(question, bounds):
    with pytest.raises(ValueError):
        resolve_scope_request(question, bounds=bounds)


def test_previous_scope_is_inherited_only_without_new_place():
    old = resolve_scope('朱家尖')
    kept = resolve_scope_request('同一时刻换成海面风', previous=old)
    assert kept['scope'] == old and kept['reason'] == 'inherited'
    assert kept['notice'] and '朱家尖' in kept['notice']
    changed = resolve_scope_request('同一时刻换成嵊泗', previous=old)
    assert changed['state'] == 'parent_fallback'
    assert changed['scope']['id'] == 'zhoushan'
    assert changed['requested_places'] == ['嵊泗']
    rejected = resolve_scope_request('同一时刻换成洋山港', previous=old)
    assert rejected['scope'] is None and rejected['state'] == 'needs_scope'


def test_current_question_overrides_canonical_inherited_scope_prefix():
    result = resolve_scope_request('朱家尖近海参考统计框客观预报，从2026年10月22日8时起未来1小时。本轮问题：同一时刻换成嵊泗')
    assert result['scope']['id'] == 'zhoushan'
    assert result['requested_places'] == ['嵊泗']
    inherited = resolve_scope_request('朱家尖近海参考统计框客观预报，本轮问题：同一时刻换成海面风')
    assert inherited['scope']['id'] == 'zhujiajian'


@pytest.mark.parametrize('version', [LEGACY_SCOPE_VERSION, SCOPE_VERSION])
def test_previous_version_is_validated_and_exact_window_replayed(version):
    old = resolve_scope('朱家尖')
    old['version'] = version
    result = resolve_scope_request('同一时刻海面风', previous={'scope': old})
    assert result['reason'] == 'inherited'
    assert result['scope']['bounds'] == old['bounds']
    assert result['scope']['version'] == version
    assert validated_scope(old) == old


def test_forged_or_unknown_previous_scope_does_not_silently_default():
    old = resolve_scope('朱家尖')
    for changes in ({'bounds': [119, 25, 126, 33]}, {'version': 'future-version'}, {'id': 'unregistered'}):
        forged = {**deepcopy(old), **changes}
        assert validated_scope(forged) is None
        result = resolve_scope_request('同一时刻海面风', previous=forged)
        assert result['state'] == 'needs_scope' and result['scope'] is None
    legacy = {**old, 'version': LEGACY_SCOPE_VERSION}
    assert validated_scope(legacy, allow_legacy=False) is None


def test_selector_only_offers_boxes_with_trusted_coordinates():
    scopes = available_scopes()
    assert len(scopes) == 7
    assert {row['id'] for row in scopes} == {
        'zhejiang', 'jiaxing', 'ningbo', 'zhoushan', 'taizhou', 'wenzhou', 'zhujiajian'}
    assert all(row['version'] == SCOPE_VERSION for row in scopes)


@pytest.mark.parametrize('question,name', [
    ('绘制镇海明天08时海浪二维图', '镇海'),
    ('绘制霞浦明天08时海浪二维图', '霞浦'),
    ('显示明天08时鹤浦海面风分布图', '鹤浦'),
    ('给我一张海门明天08时海浪图', '海门'),
    ('生成佛渡未来三天风浪报告', '佛渡'),
    ('绘制2026年10月22日08时楚门海浪二维图', '楚门'),
    ('绘制2026年10月22日08时沥港风场和浪场二维图', '沥港'),
    ('换成镇海', '镇海'),
    ('同一时刻换成楚门', '楚门'),
    ('同一时刻换成佛渡', '佛渡'),
])
def test_unregistered_suffixless_names_in_explicit_place_slots_require_scope(question, name):
    assert name in place_mentions(question)
    result = resolve_scope_request(question, previous=resolve_scope('朱家尖'))
    assert result['state'] == 'needs_scope'
    assert result['scope'] is None
    assert name in result['unknown']


def test_singapore_explicit_product_query_is_outside_not_default():
    result = resolve_scope_request('绘制新加坡明天08时海浪二维图')
    assert result['state'] == 'out_of_region'
    assert result['scope'] is None
    assert result['outside'] == ['新加坡']


@pytest.mark.parametrize('question', [
    '同一时刻风浪都显示', '各区域未来三天海浪报告',
    '同批次改成明天，其他不变',
    '同一批次改成明天8时起未来24小时，使用数据集ID zjocean_L5_fixture',
    '同一批次改成明天8时起未来24小时，使用数据集ID zjocean_L5_fixture，其他不变',
    '生成并解读未来三天风浪报告',
    '选择地图范围看海浪', '显示一张单时刻海浪二维图',
])
def test_non_place_correction_and_instruction_residue_never_becomes_unknown(question):
    assert place_mentions(question) == []
    result = resolve_scope_request(question, previous=resolve_scope('朱家尖'))
    assert result['state'] == 'resolved'
    assert result['scope']['id'] == 'zhujiajian'


def test_report_instruction_and_datetime_before_registered_place_are_not_residual_names():
    question = '从2026年10月22日08时起生成并解读宁波未来12小时风浪报告'
    assert place_mentions(question) == ['宁波']
    assert resolve_scope_request(question)['scope']['id'] == 'ningbo'

