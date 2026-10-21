"""Versioned reference boxes for reproducible objective-field statistics.

These are configurable calculation windows, not administrative sea boundaries,
coastline polygons or bathing/fishing sites.  All products consume the same
resolved scope instead of independently assuming a province-wide rectangle.
"""
from copy import deepcopy
import re

SCOPE_VERSION = 'zhejiang_reference_boxes_v1'
ZHEJIANG_BOUNDS = (119.5, 26.5, 123.5, 31.5)
REGIONS = (
    ('north', '北部参考统计框', (120.5, 29.5, 123.5, 31.5)),
    ('central', '中部参考统计框', (120.0, 28.0, 123.5, 29.5)),
    ('south', '南部参考统计框', (119.5, 26.5, 123.5, 28.0)),
)
# Nearshore reference windows deliberately include offshore grid points. A
# named beach is not represented by an interpolated or nearest inland cell.
_BOXES = {
    '嘉兴': ('jiaxing', (120.75, 30.25, 121.65, 30.95)),
    '宁波': ('ningbo', (121.30, 28.85, 122.60, 30.40)),
    '舟山': ('zhoushan', (121.75, 29.45, 123.15, 31.10)),
    '台州': ('taizhou', (121.10, 28.05, 122.55, 29.25)),
    '温州': ('wenzhou', (120.35, 26.90, 122.10, 28.25)),
    '朱家尖': ('zhujiajian', (122.33, 29.82, 122.53, 30.02)),
}
_DISCLAIMER = '本范围是版本化经纬度参考统计框，不是行政海域边界；网格结果不等同于岸边、浴场或钓点实况。'


def available_scopes():
    """Public configuration for the same map/report scope selector."""
    return [resolve_scope(name) for name in ('浙江', '嘉兴', '宁波', '舟山', '台州', '温州', '朱家尖')]


def _validate_bounds(value):
    from .objective import _bounds
    if isinstance(value, (list, tuple)) and any(isinstance(v, bool) for v in value):
        raise ValueError('bounds必须为有限经纬度数值，不能为布尔值')
    return _bounds(value)


def resolve_scope(question, bounds=None):
    """Return ``id,label,bounds,regions,kind,version,limitations``.

    Explicit caller/map bounds win, but pass the normal coordinate validator.
    Specific place names win over their province/city background (e.g.
    浙江舟山朱家尖). Multiple peer cities produce a union window and separate
    city statistics, preserving each city's own calculation box.
    """
    q = str(question or '')
    marker = re.search(r'地图范围\s*[:：]\s*([^，。；\n]+)', q)
    if bounds is None and marker:
        bounds = marker[1].strip(' ,，')
    if bounds is not None:
        values = _validate_bounds(bounds)
        return {'id': 'map_bounds', 'label': '地图选定范围', 'bounds': values,
                'regions': [{'id': 'map_bounds', 'name': '地图选定参考统计框', 'bounds': list(values)}],
                'kind': 'map_bounds', 'version': SCOPE_VERSION,
                'limitations': [_DISCLAIMER]}
    # A correction refers to its target, not to the previously named object.
    correction = list(re.finditer(r'换成|改成|切换到|改为', q))
    if correction and any(name in q[correction[-1].end():] for name in _BOXES):
        q = q[correction[-1].end():]
    names = [name for name in _BOXES if name in q]
    if '朱家尖' in names:
        names = [name for name in names if name != '舟山']
    if not names:
        return {'id': 'zhejiang', 'label': '浙江近海参考统计框', 'bounds': list(ZHEJIANG_BOUNDS),
                'regions': [{'id': key, 'name': name, 'bounds': list(box)} for key, name, box in REGIONS],
                'kind': 'reference_box', 'version': SCOPE_VERSION, 'limitations': [_DISCLAIMER]}
    regions = [{'id': _BOXES[name][0], 'name': name + '近海参考统计框', 'bounds': list(_BOXES[name][1])}
               for name in names]
    values = [min(row['bounds'][0] for row in regions), min(row['bounds'][1] for row in regions),
              max(row['bounds'][2] for row in regions), max(row['bounds'][3] for row in regions)]
    limits = [_DISCLAIMER]
    if '朱家尖' in names:
        limits.append('朱家尖采用约0.2°的小范围参考框；原生有效海点不足或无海点时返回覆盖不足，不扩大到舟山或全省，也不以最近陆地点代替。')
    if len(regions) > 1:
        limits.append('各地参考框可能重叠；分区数值不能相加，联合图展示各框外接范围。')
    return deepcopy({'id': '+'.join(row['id'] for row in regions),
                     'label': '、'.join(names) + '近海参考统计框', 'bounds': values, 'regions': regions,
                     'kind': 'reference_box', 'version': SCOPE_VERSION, 'limitations': limits})
