"""Bounded, reproducible 72-hour objective forecast reports.

The engine owns NC parsing, native-grid statistics, masks and map rendering.
This module only assembles those results into a dated data report. It neither
calls a language model nor derives warnings, impacts or activity permissions.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath


CN = timezone(timedelta(hours=8))
ZHEJIANG_BOUNDS = (119.5, 26.5, 123.5, 31.5)
REGIONS = (
    ('north', '北部参考统计框', (120.5, 29.5, 123.5, 31.5)),
    ('central', '中部参考统计框', (120.0, 28.0, 123.5, 29.5)),
    ('south', '南部参考统计框', (119.5, 26.5, 123.5, 28.0)),
)
FIELDS = {
    'wave': {'label': '有效波高', 'extreme': 'maximum', 'metric': '最大有效波高', 'colour': '#1677aa'},
    'wind': {'label': '海上10米风速', 'extreme': 'maximum', 'metric': '最大海上10米风速', 'colour': '#11806a'},
    'pressure': {'label': '海平面气压', 'extreme': 'minimum', 'metric': '最低海平面气压', 'colour': '#9a600a'},
}
FILES = frozenset({'report.json', 'report.html', 'series.csv',
                   'wave_peak.png', 'wind_peak.png', 'pressure_peak.png',
                   'wave_maximum.png', 'wind_maximum.png'})
REPORT_ALGORITHM_VERSION = 'objective-report-scope-process-cache-v3'
INTERPRETATION_VERSION = 'source-profile-explicit-temporary-v1'
_REPORT_LOCKS = {}
_REPORT_LOCKS_GUARD = threading.Lock()


def _bounds(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value)):
        raise ValueError('bounds 必须是有限数值组成的西、南、东、北四元组')
    west, south, east, north = map(float, value)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('bounds 的经纬度范围或顺序无效')
    return (west, south, east, north)


def _scope(scope, bounds):
    """One geographic contract owns all statistics, figures and product labels."""
    if scope is not None and not isinstance(scope, dict):
        raise ValueError('scope 必须是已解析的统计范围对象')
    source = dict(scope or {})
    chosen = _bounds(bounds if bounds is not None else source.get('bounds', ZHEJIANG_BOUNDS))
    if bounds is not None and source.get('bounds') is not None and _bounds(source['bounds']) != chosen:
        raise ValueError('scope 与 bounds 不一致，未将两处范围混用')
    default = not source and chosen == ZHEJIANG_BOUNDS
    regions = source.get('regions', [{'id': k, 'name': name, 'bounds': list(box)} for k, name, box in REGIONS] if default else [])
    if not isinstance(regions, (list, tuple)):
        raise ValueError('scope.regions 必须是列表')
    normalized = []
    identifiers = set()
    for item in regions:
        if not isinstance(item, dict) or not str(item.get('id') or '') or not str(item.get('name') or ''):
            raise ValueError('每个分区必须有 id、name 和 bounds')
        box = _bounds(item.get('bounds'))
        if box[0] < chosen[0] or box[1] < chosen[1] or box[2] > chosen[2] or box[3] > chosen[3]:
            raise ValueError('分区统计框不能超出所问地点的统计范围')
        if str(item['id']) in identifiers:
            raise ValueError('分区 id 不能重复')
        identifiers.add(str(item['id']))
        normalized.append({'id': str(item['id']), 'name': str(item['name']), 'bounds': list(box)})
    return {'id': str(source.get('id') or ('zhejiang' if default else 'custom')),
            'label': str(source.get('label') or ('浙江近海' if default else '指定海域')),
            'kind': str(source.get('kind') or ('reference_box' if default else 'map_bounds')),
            'version': str(source.get('version') or 'reference_box_v1'), 'bounds': list(chosen),
            'regions': normalized, 'limitations': list(source.get('limitations') or [])}


@contextmanager
def _product_lock(root, report_id):
    """Thread and process locks protect one immutable product, including rebuilds."""
    with _REPORT_LOCKS_GUARD:
        lock = _REPORT_LOCKS.setdefault((str(root), report_id), threading.Lock())
    with lock:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f'.{report_id}.lock'
        deadline = time.monotonic() + 300
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode('ascii'))
                os.close(fd)
                break
            except FileExistsError:
                # A hard-stopped process may leave a lock. Expiry exceeds the
                # bounded generation timeout; never remove a freshly held lock.
                try:
                    if time.time() - path.stat().st_mtime > 1800:
                        path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError('该报告仍在生成中，请稍后重试')
                time.sleep(.1)
        try:
            yield
        finally:
            path.unlink(missing_ok=True)


def _time(value, *, name='start'):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError(f'{name} 必须是有效的 ISO 日期或时刻，不能改用其他日期') from exc
    else:
        raise ValueError(f'{name} 必须是有效的 ISO 日期或时刻')
    return result.replace(tzinfo=CN) if result.tzinfo is None else result.astimezone(CN)


def _safe(value):
    """Convert engine scalar containers without serializing internal objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return _time(value).isoformat()
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if hasattr(value, 'item'):
        return _safe(value.item())
    if hasattr(value, 'tolist'):
        return _safe(value.tolist())
    raise TypeError(f'不能写入报告的统计类型：{type(value).__name__}')


def _source_files(values):
    result = []
    for value in values or []:
        if isinstance(value, str):
            result.append({'filename': PureWindowsPath(value).name})
            continue
        if not isinstance(value, dict):
            continue
        record = {key: _safe(value[key]) for key in (
            'file_id', 'source_file_id', 'sha256', 'size', 'bytes', 'format', 'unit', 'variable',
            'time_variable', 'valid_start', 'valid_end', 'available_start', 'available_end', 'batch', 'batch_utc') if key in value}
        name = value.get('filename') or value.get('name') or value.get('path')
        if name:
            record['filename'] = PureWindowsPath(str(name)).name
        result.append(record)
    return result


def _field(value, catalogue_field):
    """Keep engine statistics and provenance, omitting absolute local paths."""
    value = value if isinstance(value, dict) else {}
    allowed = ('state', 'unit', 'series', 'maximum', 'minimum', 'native_count',
               'sea_count', 'valid_count', 'missing_count', 'sample_count',
               'sample_count_unit', 'frame_count', 'coverage_start', 'coverage_end',
               'available_start', 'available_end', 'series_start', 'series_end',
               'requested_start', 'requested_end', 'partial', 'profile',
               'mask', 'sea_mask', 'sea_mask_source', 'sea_mask_time',
               'native_step_hours', 'reason', 'limitations', 'provisional',
               'actual_bounds', 'coverage_bounds', 'requested_bounds', 'spatial_partial',
               'coverage', 'source_family', 'level', 'grid_spacing_degrees',
               'interpretation', 'wind_kind', 'descriptions', 'temporal_coverage',
               'process', 'temporal_maximum', 'period_start', 'period_end')
    output = {key: _safe(value[key]) for key in allowed if key in value}
    output['unit'] = output.get('unit') or catalogue_field.get('unit') or '单位未核定'
    output['series'] = output.get('series') or []
    output['source_files'] = _source_files(value.get('source_files') or catalogue_field.get('source_files'))
    if catalogue_field.get('source_file_id'):
        output['source_file_id'] = catalogue_field['source_file_id']
    return output


def _field_info(dataset):
    """Use the selected product's interpretation, never another batch's labels."""
    result = {name: dict(info) for name, info in FIELDS.items()}
    family = str(dataset.get('source_family') or '')
    level = str(dataset.get('level') or '')
    if family == 'zjocean_l2_l5' or level in {'L2', 'L5'}:
        result['wind'].update(label='海面风速（暂按阵风解释）', metric='最大海面风速（暂按阵风解释）')
    return result


def _info(result, name):
    return result.get('field_info', {}).get(name) or FIELDS[name]


def _available(result):
    return result.get('available_fields', list(FIELDS))


def _not_provided(name):
    return {'state': 'not_provided', 'unit': {'wave': 'm', 'wind': 'm/s', 'pressure': 'hPa'}[name],
            'series': [], 'source_files': [], 'reason': '所选数据集未提供该要素；未使用其他批次或产品补齐'}


def _extreme(field, key):
    value = field.get(key)
    if isinstance(value, dict):
        return value
    return {'value': value} if isinstance(value, (int, float)) else {}


def _display(value, digits=2):
    if value is None:
        return '未取得'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(value, f'.{digits}f').rstrip('0').rstrip('.')
    return str(value)


def _point(value):
    lon = value.get('lon', value.get('longitude'))
    lat = value.get('lat', value.get('latitude'))
    if lon is None or lat is None:
        return '位置未取得'
    return f'东经 {_display(lon, 4)}°、北纬 {_display(lat, 4)}°'


def _series_value(row, extreme):
    entry = row.get(extreme)
    if isinstance(entry, dict):
        return entry.get('value')
    if isinstance(entry, (int, float)):
        return entry
    # Engine may name per-frame statistics max/min, while period extrema keep
    # their full maximum/minimum dictionaries. These are real spatial extrema.
    short = 'max' if extreme == 'maximum' else 'min'
    entry = row.get(short)
    return entry.get('value') if isinstance(entry, dict) else entry


def _plot_svg(field, info):
    groups, current = [], []
    temporal = field.get('temporal_coverage') or {}
    native_seconds = temporal.get('native_step_seconds')
    native_steps = temporal.get('native_steps_seconds') or ([native_seconds] if native_seconds else [])
    missing_instants = [_time(value).timestamp() for value in temporal.get('missing_times') or []]
    previous = None
    for row in field.get('series', []):
        value = _series_value(row, info['extreme'])
        stamp = row.get('time') or row.get('valid_time')
        if not isinstance(value, (int, float)) or not stamp or not math.isfinite(value):
            if current:
                groups.append(current)
                current = []
            continue
        instant = _time(stamp).timestamp()
        disconnected = previous is not None and (
            (native_steps and round(instant-previous) not in native_steps)
            or any(previous < missing < instant for missing in missing_instants))
        if disconnected:
            if current:
                groups.append(current)
                current = []
        current.append((instant, value))
        previous = instant
    if current:
        groups.append(current)
    points = [point for group in groups for point in group]
    if not points:
        return '<p class="muted">该要素在所选时段没有可绘制的原生时次。</p>'
    width, height, pad = 760, 150, 24
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    def coordinates(group):
        return [(pad + (x-x0) / max(x1-x0, 1) * (width-2*pad),
                 height-pad-(y-y0) / max(y1-y0, 1e-9) * (height-2*pad)) for x, y in group]
    paths = [' '.join(f'{x:.2f},{y:.2f}' for x, y in coordinates(group)) for group in groups]
    coords = coordinates(points)
    circles = ''.join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.8"/>' for x, y in coords)
    lines = ''.join(f'<polyline points="{path}" fill="none" stroke="{info["colour"]}" stroke-width="2"/>' for path in paths)
    first = datetime.fromtimestamp(x0, CN).strftime('%m-%d %H:%M')
    last = datetime.fromtimestamp(x1, CN).strftime('%m-%d %H:%M')
    return (f'<svg role="img" aria-label="{html.escape(info["metric"])}原生时次曲线" viewBox="0 0 {width} {height}">'
            f'<path d="M {pad} {pad} V {height-pad} H {width-pad}" fill="none" stroke="#cbd5df"/>'
            f'{lines}'
            f'<g fill="{info["colour"]}">{circles}</g>'
            f'<text x="{pad+4}" y="18" fill="#52616f" font-size="12">{_display(y1)} / {_display(y0)} {html.escape(field.get("unit", ""))}</text>'
            f'<text x="{pad}" y="147" fill="#52616f" font-size="11">{first}</text>'
            f'<text x="{width-pad}" y="147" text-anchor="end" fill="#52616f" font-size="11">{last}</text>'
            '</svg>')


class ObjectiveReport:
    def __init__(self, engine, output_root, *, now=None, links=None):
        self.engine = engine
        self.output_root = Path(output_root).resolve()
        self.now = now or (lambda: datetime.now(CN))
        self.links = {'typhoon': '/', 'analogue': '/', 'warnings': '/', **(links or {})}

    def _dataset(self, requested):
        catalogue = self.engine.catalogue()
        datasets = catalogue.get('datasets', []) if isinstance(catalogue, dict) else catalogue
        if not isinstance(datasets, list) or not datasets:
            raise ValueError('尚未登记可用的客观预报数据批次')
        if requested:
            selected = next((d for d in datasets if d.get('dataset_id') == requested), None)
            if selected is None:
                raise ValueError('指定的客观预报批次不存在，未改用其他批次')
            return selected
        default = catalogue.get('default_dataset_id') if isinstance(catalogue, dict) else None
        return next((d for d in datasets if d.get('dataset_id') == default), None) or max(
            datasets, key=lambda d: str(d.get('batch') or ''))

    def artifact_path(self, report_id, filename):
        if not re.fullmatch(r'[a-f0-9]{32}', str(report_id)) or filename not in FILES:
            raise ValueError('无效的报告文件')
        path = (self.output_root / report_id / filename).resolve()
        if path.parent.parent != self.output_root or not path.is_file():
            raise FileNotFoundError('报告文件不存在')
        return path

    @staticmethod
    def _artifact(report_id, path):
        content = path.read_bytes()
        return {'filename': path.name, 'url': f'/api/objective/reports/{report_id}/{path.name}',
                'relative_path': f'{report_id}/{path.name}', 'size': len(content),
                'sha256': hashlib.sha256(content).hexdigest()}

    def report(self, question='', start=None, dataset_id=None, hours=72, requested_fields=None,
               bounds=None, scope=None, thresholds=None):
        if type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError('hours 必须是 1 至 168 的整数')
        created = _time(self.now(), name='now')
        begin = created if start is None else _time(start)
        end = begin + timedelta(hours=hours)
        dataset = self._dataset(dataset_id)
        available = [name for name in FIELDS if name in dataset.get('fields', {})]
        if requested_fields is None:
            requested = list(available)
        elif (not isinstance(requested_fields, (list, tuple)) or not requested_fields
              or any(not isinstance(name, str) or name not in FIELDS for name in requested_fields)):
            raise ValueError('requested_fields 必须是 wave、wind、pressure 组成的非空列表')
        else:
            requested = list(dict.fromkeys(requested_fields))
        info = _field_info(dataset)
        selected_id = dataset['dataset_id']
        dataset_batch = _time(dataset.get('batch'), name='dataset.batch')
        selected_scope = _scope(scope, bounds)
        if thresholds is not None:
            if not isinstance(thresholds, dict) or any(key not in FIELDS for key in thresholds):
                raise ValueError('thresholds 必须以 wave、wind 或 pressure 为要素键')
            from .objective_process import threshold_specs
            for name in thresholds:
                threshold_specs(thresholds, name, dataset.get('fields', {}).get(name, {}).get('unit')
                                or _not_provided(name)['unit'])
        # The key uses the registered immutable file identity, selected field
        # definitions and interpretation profile. Questions never enter shared
        # product HTML, so another user's wording cannot leak through reuse.
        identity = {'algorithm': REPORT_ALGORITHM_VERSION, 'interpretation': INTERPRETATION_VERSION,
                    'dataset_id': selected_id, 'batch': dataset.get('batch'),
                    'sources': _source_files(dataset.get('source_files') or dataset.get('sources')),
                    'fields': {name: _safe(dataset.get('fields', {}).get(name, {})) for name in available},
                    'profile': _safe(dataset.get('profile') or {}), 'source_family': dataset.get('source_family'),
                    'level': dataset.get('level'), 'scope': selected_scope, 'start': begin.isoformat(),
                    'hours': hours, 'requested_fields': sorted(requested), 'thresholds': _safe(thresholds or {}),
                    'mask': 'wave_valid', 'issued_as_of_creation': dataset_batch <= created}
        cache_key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()
        report_id = cache_key[:32]
        with _product_lock(self.output_root, report_id):
            cached = self._load_product(report_id, cache_key)
            if cached is not None:
                return self._response(cached, cache_hit=True)
            staging = Path(tempfile.mkdtemp(prefix=f'.{report_id}-', dir=self.output_root))
            try:
                result = self._generate(dataset, selected_id, created, begin, end, hours, available,
                                        requested, info, selected_scope, thresholds, staging, report_id, cache_key)
                manifest = {path.name: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}
                            for path in staging.iterdir() if path.is_file() and path.name in FILES}
                (staging / '.complete.json').write_text(json.dumps({'cache_key': cache_key, 'files': manifest},
                                                                   sort_keys=True), encoding='utf8')
                target = self.output_root / report_id
                invalid = None
                if target.exists():
                    invalid = self.output_root / f'.invalid-{report_id}-{uuid.uuid4().hex}'
                    os.replace(target, invalid)
                try:
                    os.replace(staging, target)
                except BaseException:
                    if invalid is not None and not target.exists():
                        os.replace(invalid, target)
                    raise
                if invalid is not None:
                    shutil.rmtree(invalid)
                return self._response(result, cache_hit=False)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def _load_product(self, report_id, cache_key):
        folder = self.output_root / report_id
        try:
            manifest = json.loads((folder / '.complete.json').read_text('utf8'))
            if manifest.get('cache_key') != cache_key:
                return None
            files = manifest.get('files') or {}
            if not {'report.html', 'report.json', 'series.csv'} <= files.keys():
                return None
            for name, item in files.items():
                if name not in FILES:
                    return None
                path = folder / name
                if path.stat().st_size != item['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                    return None
            result = json.loads((folder / 'report.json').read_text('utf8'))
            if result.get('report_id') != report_id or result.get('cache_key') != cache_key or result.get('artifact_errors'):
                return None
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _response(self, result, *, cache_hit):
        # Recompute public artifact references only after atomic publication.
        artifacts = dict(result.get('artifacts') or {})
        artifacts['json'] = self._artifact(result['report_id'], self.output_root / result['report_id'] / 'report.json')
        result = {**result, 'artifacts': artifacts, 'cache_hit': cache_hit}
        return {'state': result['state'], 'report_id': result['report_id'], 'objective_result': result,
                'summary': result['summary'], 'evidence': self._evidence(result), 'artifacts': artifacts,
                'cache_hit': cache_hit}

    def _generate(self, dataset, selected_id, created, begin, end, hours, available, requested,
                  info, selected_scope, thresholds, folder, report_id, cache_key):
        batch = _time(dataset.get('batch'), name='dataset.batch')
        late_batch = batch > begin or batch > created
        def statistics(bounds):
            if not late_batch:
                kwargs = {'dataset_id': selected_id, 'start': begin.isoformat(), 'hours': hours,
                          'bounds': bounds, 'sea_mask': 'wave_valid'}
                if thresholds is not None:
                    kwargs['thresholds'] = thresholds
                return self.engine.stats72(**kwargs)
            return {'state': 'no_data', 'batch': dataset['batch'], 'provisional': dataset.get('provisional', False),
                    'fields': {name: {'state': 'no_data', 'series': [], 'partial': True,
                                      'unit': dataset.get('fields', {}).get(name, {}).get('unit'),
                                      'source_files': dataset.get('fields', {}).get(name, {}).get('source_files', []),
                                      'reason': '所选批次起报晚于目标开始或查询时刻，未使用未来起报资料回填'} for name in available}}
        def normalize_fields(section):
            return {name: (_field(section.get('fields', {}).get(name), dataset.get('fields', {}).get(name, {}))
                           if name in available else _not_provided(name)) for name in FIELDS}
        selected_bounds = tuple(selected_scope['bounds'])
        stats = statistics(selected_bounds)
        zones = []
        for region in selected_scope['regions']:
            section = statistics(tuple(region['bounds']))
            zones.append({'id': region['id'], 'name': region['name'], 'bounds': list(region['bounds']),
                          'fields': normalize_fields(section)})
        fields = normalize_fields(stats)
        limitations = [
            f'统计范围为{selected_scope["label"]}参考经纬度框，不代表法定或行政海域边界。',
            '海陆掩膜采用同批次波浪产品有效海点，并映射到本数据集其他要素原生网格；它不是精细岸线或淹没模型。',
            '已提供要素的极值均在海上原生网格统计，不以显示下采样或P95代替极值。',
            '分要素保留原生时间步长，不把不同起报批次或文件外时段补成实况；折线连接仅用于查看，不修改原始值。',
            '各分区独立统计，公共边界格点可能重复计入，不将分区格点数相加作为全区面积。',
            '这是客观模式预报，不能从台风中心强度推导当地严重影响，也不生成正式预警、灾损或活动许可。',
        ]
        limitations.extend(selected_scope.get('limitations') or [])
        provisional = bool(stats.get('provisional', dataset.get('provisional', False)))
        if provisional:
            if dataset.get('source_family') == 'zjocean_l2_l5' or dataset.get('level') in {'L2', 'L5'}:
                limitations.append('当前L2/L5使用临时解析配置：WAV暂按有效波高，u10/v10合成风速暂按阵风解释；时区、变量含义和单位仍需业务方核定，未称作已确认的平均10米风。')
            else:
                limitations.append('当前客观场使用临时解析配置。NC及文件名未标注的时区暂按UTC解释后转北京时间，变量含义、时区和单位转换仍需业务方核定。')
        unavailable = [name for name in FIELDS if name not in available]
        notes = [f'{info[name]["label"]}：所选数据集未提供，不与其他产品或起报批次混用补齐。' for name in unavailable]
        gaps = ['所选批次起报晚于目标开始或查询时刻，未使用未来起报资料回填；请明确选择查询时刻已起报、目标开始前已起报且有效期覆盖所需时段的批次。'] if late_batch else []
        for key in requested:
            value = fields[key]
            if key not in available:
                gaps.append(f'{info[key]["label"]}：所选数据集未提供该要素，未使用其他批次或产品补齐。')
                continue
            extreme = _extreme(value, FIELDS[key]['extreme'])
            if not value.get('series') or extreme.get('value') is None:
                gaps.append(f'{info[key]["label"]}：所选时段未取得可计算的海上原生样本。')
            elif value.get('partial') or value.get('state') in {'partial', 'no_data', 'unavailable'}:
                gaps.append(f'{info[key]["label"]}：文件未完整覆盖所选时空范围或存在不可用样本，已保留实际覆盖。')
            if value.get('spatial_partial'):
                gaps.append(f'{info[key]["label"]}：源网格未完整覆盖请求统计框，仅统计覆盖范围{value.get("coverage_bounds") or "见源文件"}与请求框的交集。')
            if isinstance(value.get('missing_count'), (int, float)) and value['missing_count'] > 0:
                gaps.append(f'{info[key]["label"]}：有{value["missing_count"]}个海上格点-时间样本缺测，未按0补齐。')
            coverage = value.get('temporal_coverage') or {}
            if coverage.get('complete') is False:
                missing = coverage.get('missing_times') or []
                gaps.append(f'{info[key]["label"]}：原生时次覆盖不完整，预计{coverage.get("expected_count", "未定")}个，'
                            f'取得{coverage.get("available_count", "未定")}个；缺少{len(missing)}个时次，未插值补齐。')
        for zone in zones:
            for key in requested:
                value = zone['fields'][key]
                if key in available and (value.get('partial') or value.get('spatial_partial') or not value.get('series')):
                    gaps.append(f'{zone["name"]}的{info[key]["label"]}仅能按已取得的实际覆盖统计。')
        artifacts, errors = {}, []
        for name in available:
            field_info = info[name]
            peak = _extreme(fields[name], field_info['extreme'])
            stamp = peak.get('time') or peak.get('valid_time')
            if peak.get('value') is None or not stamp:
                continue
            try:
                image = self.engine.image_png(dataset_id=selected_id, field=name, valid_time=stamp,
                                               bounds=selected_bounds, maxsize=600, sea_mask='wave_valid')
                if not isinstance(image, bytes) or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('图像输出不是有效PNG数据')
                path = folder / f'{name}_peak.png'
                path.write_bytes(image)
                artifacts[name + '_png'] = self._artifact(report_id, path)
            except Exception as exc:
                errors.append({'field': name, 'stage': 'image', 'error_type': type(exc).__name__})
                if name in requested:
                    gaps.append(f'{field_info["label"]}极值时刻空间图生成失败，数值结果继续保留。')
            if name in ('wave', 'wind') and callable(getattr(self.engine, 'image_temporal_maximum_png', None)):
                try:
                    image = self.engine.image_temporal_maximum_png(
                        dataset_id=selected_id, field=name, start=begin.isoformat(), hours=hours,
                        bounds=selected_bounds, maxsize=600, sea_mask='wave_valid')
                    if not isinstance(image, bytes) or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('时段最大值图不是有效PNG数据')
                    path = folder / f'{name}_maximum.png'
                    path.write_bytes(image)
                    artifacts[name + '_maximum_png'] = self._artifact(report_id, path)
                except Exception as exc:
                    errors.append({'field': name, 'stage': 'temporal_maximum_image', 'error_type': type(exc).__name__})
                    if name in requested:
                        gaps.append(f'{field_info["label"]}逐格时段最大值图生成失败，数值结果继续保留。')
        no_data = not any(_extreme(fields[name], FIELDS[name]['extreme']).get('value') is not None for name in requested)
        result = {'kind': 'objective_report', 'state': 'no_data' if no_data else 'partial' if gaps else 'ready',
                  'report_id': report_id, 'dataset_id': selected_id, 'batch': dataset.get('batch') or stats.get('batch'),
                  'created_at': created.isoformat(), 'start': begin.isoformat(), 'end': end.isoformat(),
                  'hours': hours, 'timezone': 'Asia/Shanghai', 'bounds': list(selected_bounds),
                  'scope': selected_scope, 'scope_label': selected_scope['label'],
                  'region_definition': '临时参考统计框，非行政海域边界', 'sea_mask': 'wave_valid',
                  'thresholds': _safe(thresholds or {}), 'algorithm_version': REPORT_ALGORITHM_VERSION,
                  'interpretation_version': INTERPRETATION_VERSION, 'cache_key': cache_key,
                  'source_family': dataset.get('source_family'), 'level': dataset.get('level'),
                  'available_fields': available, 'requested_fields': requested, 'unavailable_fields': unavailable,
                  'field_info': info, 'product_notes': notes,
                  'fields': fields, 'regions': zones, 'missing_information': list(dict.fromkeys(gaps)),
                  'limitations': limitations, 'artifact_errors': errors,
                  'source_files': _source_files(stats.get('source_files')), 'provisional': provisional,
                  'profile': _safe(stats.get('profile') or dataset.get('profile') or {})}
        summary = self._summary(result)
        result['summary'] = summary
        csv_path = folder / 'series.csv'
        self._write_csv(result, csv_path)
        artifacts['csv'] = self._artifact(report_id, csv_path)
        html_path = folder / 'report.html'
        html_path.write_text(self._html(result, '', artifacts), encoding='utf8')
        artifacts['html'] = self._artifact(report_id, html_path)
        result['artifacts'] = artifacts
        json_path = folder / 'report.json'
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
        return result

    @staticmethod
    def _process_lines(field, info):
        process = field.get('process') or {}
        lines = []
        for item in process.get('threshold_exceedances') or []:
            if not isinstance(item, dict):
                continue
            threshold = item.get('threshold')
            unit = item.get('unit') or field.get('unit', '')
            comparison = '>' if item.get('comparison') == '>' else '≥'
            label = f'{info["label"]}筛查条件{comparison}{_display(threshold)} {unit}'
            first, last = item.get('first'), item.get('last')
            if first is None:
                lines.append(f'{label}：已取得时次内未出现超阈样本。')
                continue
            duration = item.get('duration_hours')
            lines.append(f'{label}：首次{first}，最后{last}；原生离散时次的超阈持续时长{_display(duration)}小时，'
                         '按相邻原生时次两端均超阈的间隔累计；超阈格点可随时刻移动，缺时次不作连续超阈推断。')
            if item.get('maximum_fraction') is not None:
                fraction = item['maximum_fraction']
                if isinstance(fraction, (int, float)):
                    lines.append(f'超阈海点最大比例：{_display(fraction*100)}%；比例按有效海上格点计，不是海域面积百分比。')
        if process.get('threshold_exceedances'):
            lines.append('上述阈值仅为用户指定的数据筛查条件，不表示官方预警或活动准入标准。')
        return lines

    @staticmethod
    def _insights(result):
        """Compare actual regional extremes and their native occurrence times."""
        output = []
        for name in ('wave', 'wind'):
            if name not in _available(result):
                continue
            info = _info(result, name)
            choices = []
            for region in result['regions']:
                field = region['fields'][name]
                peak = _extreme(field, FIELDS[name]['extreme'])
                value = peak.get('value')
                if isinstance(value, (int, float)) and math.isfinite(value):
                    choices.append((region['name'], value, field['unit']))
            if len(choices) < 2:
                continue
            high, low = max(row[1] for row in choices), min(row[1] for row in choices)
            if high == low:
                output.append(f'已取得分区的{info["metric"]}相同，均为{_display(high)} {choices[0][2]}。')
                continue
            high_names = '、'.join(row[0] for row in choices if row[1] == high)
            low_names = '、'.join(row[0] for row in choices if row[1] == low)
            output.append(f'已取得分区对照：{high_names}的{info["metric"]}较高（{_display(high)} {choices[0][2]}），'
                          f'{low_names}较低（{_display(low)} {choices[0][2]}）；这是同指标的框内极值比较。')
        begin, end = _time(result['start']), _time(result['end'])
        groups = {}
        for name, field in result['fields'].items():
            peak = _extreme(field, FIELDS[name]['extreme'])
            stamp = peak.get('time') or peak.get('valid_time')
            if not stamp or peak.get('value') is None:
                continue
            instant = _time(stamp)
            if not begin <= instant <= end:
                continue
            index = min(max(int((instant-begin).total_seconds()//86400), 0), max(0, (result['hours']-1)//24))
            groups.setdefault(index, []).append(_info(result, name)['metric'])
        if groups:
            spans = []
            for index, names in sorted(groups.items()):
                first, last = begin+timedelta(hours=index*24), min(end, begin+timedelta(hours=(index+1)*24))
                spans.append('、'.join(names) + f'出现在{first.isoformat()}至{last.isoformat()}这一时段')
            output.append('极值时段：' + '；'.join(spans) + '。时段按真实发生时刻归类，不代表当地灾害等级。')
        return output

    @classmethod
    def _evidence(cls, result):
        lines = [result['summary'], '\n分区程序统计（各框独立；已提供要素按指标取极值；地点为极值网格中心）：']
        for region in result['regions']:
            for name in _available(result):
                field = region['fields'][name]
                info = _info(result, name)
                peak = _extreme(field, FIELDS[name]['extreme'])
                if peak.get('value') is None:
                    lines.append(region['name'] + '的' + info['label'] + '：未取得可计算数值。')
                    continue
                lines.append(f'{region["name"]}；统计框W,S,E,N={region["bounds"]}；{info["metric"]}：{peak["value"]} {field["unit"]}；'
                             f'{_point(peak)}；发生时刻：{peak.get("time") or peak.get("valid_time")}；'
                             f'原生样本：{field.get("series_start") or "未取得"}至{field.get("series_end") or "未取得"}；海点数{field.get("sea_count", "未取得")}。')
                lines.extend(region['name'] + '；' + line for line in cls._process_lines(field, info))
        by_field, selected = {}, set()
        for name in _available(result):
            field = result['fields'][name]
            rows = {str(row.get('time') or row.get('valid_time')): row for row in field['series'] if row.get('time') or row.get('valid_time')}
            by_field[name] = rows
            previous = None
            for stamp in sorted(rows, key=_time):
                instant = _time(stamp)
                if previous is None or instant-previous >= timedelta(hours=6):
                    selected.add(stamp)
                    previous = instant
            if rows:
                selected.add(max(rows, key=_time))
            peak = _extreme(field, FIELDS[name]['extreme'])
            if peak.get('time') or peak.get('valid_time'):
                selected.add(str(peak.get('time') or peak['valid_time']))
        if selected:
            headings = []
            for name in _available(result):
                metric = _info(result, name)['metric']
                # Retain the established ECMWF evidence wording while the L2/L5
                # label explicitly carries its different temporary wind meaning.
                if name == 'wind' and metric == FIELDS['wind']['metric']:
                    metric = '最大10米风速'
                headings.append('空间' + metric + ' (' + result['fields'][name]['unit'] + ')')
            lines += ['\n全框原生时序摘录：各要素每约6小时选取其原生时次，同时保留末时次与全时段真实极值时刻；未对缺少该时次的要素插值。完整各原生时次在CSV中。',
                      '| 北京时间 | ' + ' | '.join(headings) + ' |',
                      '|' + '---|' * (len(headings) + 1)]
            for stamp in sorted(selected, key=_time):
                cells = []
                for name in _available(result):
                    info = _info(result, name)
                    row = by_field[name].get(stamp)
                    value = _series_value(row, info['extreme']) if row else None
                    cells.append(str(value) if value is not None else '无该原生时次/缺测，未插值')
                lines.append('| ' + stamp + ' | ' + ' | '.join(cells) + ' |')
        lines += ['\n来源与解析口径：']
        for name, field in result['fields'].items():
            for source in field['source_files']:
                lines.append(f'{_info(result, name)["label"]}；源文件：{source.get("filename") or source.get("source_file_id") or source.get("file_id") or "已登记文件"}；SHA256：{source.get("sha256") or "未取得"}。')
        lines.extend(result['limitations'])
        return '\n'.join(lines)

    @staticmethod
    def _summary(result):
        west, south, east, north = result['bounds']
        lines = [f'{result.get("scope_label", "浙江近海")}{result["hours"]}小时客观海况参考报告。',
                 f'查询时段（北京时间）：{result["start"]} 至 {result["end"]}；起报批次：{result.get("batch") or "未取得"}。',
                 f'统计范围：东经{_display(west, 5)}°至{_display(east, 5)}°、北纬{_display(south, 5)}°至{_display(north, 5)}°内的有效海点，非行政海域边界。']
        if result.get('provisional'):
            lines.append('数值来自已登记NC文件；当前时区和变量解析配置待业务方核定，未将这些模式值称作正式预警或实测。')
        for name in _available(result):
            info = _info(result, name)
            value = result['fields'][name]
            peak = _extreme(value, info['extreme'])
            if peak.get('value') is not None:
                lines.append(f'{info["metric"]}：{_display(peak["value"])} {value["unit"]}；{_point(peak)}；时刻：{peak.get("time") or peak.get("valid_time") or "未取得"}。')
            else:
                lines.append(f'{info["label"]}：所选时段没有可用数值。')
            if value.get('series'):
                lines.append(f'{info["label"]}所选窗口原生样本时段：{value.get("series_start") or "未取得"} 至 {value.get("series_end") or "未取得"}；'
                             f'海上格点数：{value.get("sea_count", "未取得")}；有效格点-时间样本数：{value.get("valid_count", "未取得")}；缺测样本数：{value.get("missing_count", "未取得")}。')
            else:
                lines.append(f'{info["label"]}：所选窗口无可用原生时次，未将源文件其他日期的覆盖当作本窗口样本。')
            if value.get('available_start') or value.get('available_end'):
                lines.append(f'{info["label"]}源文件可用时段（区别于所选查询窗口）：{value.get("available_start") or "未取得"} 至 {value.get("available_end") or "未取得"}。')
            temporal = value.get('temporal_coverage') or {}
            if temporal:
                lines.append(f'{info["label"]}原生时次覆盖：{temporal.get("available_count", "未取得")}/{temporal.get("expected_count", "未定")}，'
                             f'完整性：{"完整" if temporal.get("complete") else "不完整"}。')
            lines.extend(ObjectiveReport._process_lines(value, info))
        lines.extend(result.get('product_notes', []))
        if result['missing_information']:
            lines.append('覆盖或交付缺项：' + '；'.join(result['missing_information']))
        lines.extend(ObjectiveReport._insights(result))
        lines.append('完整分区、逐时原生统计、空间图、源文件哈希见本次报告与CSV；结果不是正式预警。')
        return '\n'.join(lines)

    @staticmethod
    def _write_csv(result, path):
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['scope_label', 'region_id', 'region_name', 'bounds_W_S_E_N', 'dataset_id', 'batch',
                             'field', 'unit', 'valid_time', 'maximum', 'minimum', 'valid_cells',
                             'missing_cells', 'sea_cells', 'mask', 'source_sha256', 'expected_times', 'available_times', 'missing_times'])
            sections = [{'id': 'all', 'name': result.get('scope_label', '浙江近海') + '参考统计框', 'bounds': result['bounds'], 'fields': result['fields']}, *result['regions']]
            for region in sections:
                for name, field in region['fields'].items():
                    hashes = ';'.join(str(source.get('sha256', '')) for source in field['source_files'])
                    for row in field['series']:
                        temporal = field.get('temporal_coverage') or {}
                        writer.writerow([result.get('scope_label', '浙江近海'), region['id'], region['name'], ','.join(map(str, region['bounds'])),
                                         result['dataset_id'], result['batch'], name, field['unit'],
                                         row.get('time') or row.get('valid_time'), _series_value(row, 'maximum'),
                                         _series_value(row, 'minimum'), row.get('valid_count'), row.get('missing_count'),
                                         row.get('sea_count', field.get('sea_count')), 'wave_valid', hashes,
                                         temporal.get('expected_count'), temporal.get('available_count'), ';'.join(temporal.get('missing_times') or [])])

    def _html(self, result, question, artifacts):
        esc = lambda value: html.escape(str(value), quote=True)
        scope_label = result.get('scope_label', '浙江近海')
        cards, figures = [], []
        for name in _available(result):
            info = _info(result, name)
            field = result['fields'][name]
            peak = _extreme(field, info['extreme'])
            cards.append(f'<article><span>{esc(info["metric"])}</span><strong>{esc(_display(peak.get("value")))} <small>{esc(field["unit"])}</small></strong>'
                         f'<p>{esc(_point(peak))}<br>{esc(peak.get("time") or peak.get("valid_time") or "时刻未取得")}</p></article>')
            image = artifacts.get(name + '_png')
            figure = f'<img loading="lazy" src="{esc(image["filename"])}" alt="{esc(info["metric"])}发生时刻的空间分布">' if image else '<p>该要素空间图未生成，数值与缺项见表。</p>'
            maximum = artifacts.get(name + '_maximum_png')
            maximum_figure = (f'<h3>{result["hours"]}小时逐格最大值分布</h3><img loading="lazy" src="{esc(maximum["filename"])}" '
                              f'alt="{esc(scope_label)}{result["hours"]}小时{esc(info["label"])}逐格最大值图">'
                              '<p class="muted">每个格点分别取所选时段的最大值，各格点峰值可能发生在不同时刻；这不是同一时刻的场。'
                              '缺失时次不补齐，实际覆盖见表；风速逐格最大值图不拼接不同发生时刻的风向箭头。</p>') if maximum else ''
            process_lines = self._process_lines(field, info)
            process_html = '<ul>' + ''.join(f'<li>{esc(line)}</li>' for line in process_lines) + '</ul>' if process_lines else ''
            figures.append(f'<section><h2>{esc(info["label"])}：原生时次空间极值</h2>{_plot_svg(field, info)}'
                           '<p class="muted">每个点代表该原生时刻统计框内的空间极值；极值位置可以随时刻变化，曲线不是固定站点序列。</p>'
                           f'{process_html}{figure}<p class="muted">上图对应极值发生时刻；绘图缩放不参与上方极值统计。</p>{maximum_figure}</section>')
        rows = []
        region_process = []
        for region in result['regions']:
            for name in _available(result):
                info = _info(result, name)
                field = region['fields'][name]
                peak = _extreme(field, info['extreme'])
                rows.append('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in (
                    region['name'], ', '.join(map(str, region['bounds'])), info['metric'],
                    _display(peak.get('value')) + ' ' + field['unit'], _point(peak),
                    peak.get('time') or peak.get('valid_time') or '未取得',
                    field.get('series_start') or field.get('coverage_start') or '未取得',
                    field.get('series_end') or field.get('coverage_end') or '未取得', field.get('sea_count', '未取得'))) + '</tr>')
                if name in ('wave', 'wind'):
                    process_lines = self._process_lines(field, info)
                    coverage = field.get('temporal_coverage') or {}
                    coverage_text = (f'<p class="muted">原生时次：{esc(coverage.get("available_count", "未取得"))}/'
                                     f'{esc(coverage.get("expected_count", "未定"))}；缺少时次：'
                                     f'{esc("；".join(coverage.get("missing_times") or []) or "无已识别缺时次")}。</p>') if coverage else ''
                    region_process.append(f'<h3>{esc(region["name"])} · {esc(info["label"])}</h3>{_plot_svg(field, info)}'
                                          + coverage_text + '<ul>' + ''.join(f'<li>{esc(line)}</li>' for line in process_lines) + '</ul>')
        gap = '<section class="gap"><h2>实际覆盖与缺项</h2><ul>' + ''.join(f'<li>{esc(text)}</li>' for text in result['missing_information']) + '</ul></section>' if result['missing_information'] else ''
        provenance = []
        for name, field in result['fields'].items():
            for source in field['source_files']:
                provenance.append('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in (
                    _info(result, name)['label'], source.get('filename') or source.get('source_file_id') or source.get('file_id') or '已登记源文件',
                    source.get('variable', ''), source.get('sha256', '未取得'))) + '</tr>')
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{esc(scope_label)}{result["hours"]}小时客观海况参考报告</title><style>'
                'body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#edf3f7;color:#193346;margin:0;line-height:1.6}'
                'main{max-width:1120px;margin:28px auto;padding:0 20px}h1{font-size:28px}h2{font-size:20px}section,header{background:white;padding:24px;margin:18px 0;border-radius:12px}'
                '.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.cards article{background:white;border-top:4px solid #168a93;padding:20px;border-radius:8px}'
                '.cards strong{display:block;font-size:32px;margin-top:8px}.cards small{font-size:16px}.muted{color:#5c6d7a;font-size:13px}.gap{border-left:4px solid #c98620}'
                'table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:9px;text-align:left;border-bottom:1px solid #dbe5ed;overflow-wrap:anywhere}th{background:#eff5f8}'
                '.scroll{overflow:auto}img,svg{width:100%;height:auto}a{color:#087a88;margin-right:18px}pre{white-space:pre-wrap;font:inherit}'
                '@media(max-width:700px){.cards{grid-template-columns:1fr}main{padding:0 10px}section,header{padding:16px}}</style><main><header>'
                f'<h1>{esc(scope_label)}{result["hours"]}小时客观海况参考报告</h1><p>{esc(result["start"])} 至 {esc(result["end"])}（北京时间）</p>'
                f'<p>起报批次：{esc(result.get("batch") or "未取得")}；数据集：{esc(result["dataset_id"])}；报告状态：{esc(result["state"])}</p>'
                + (f'<p>数据产品：{esc(result.get("source_family") or "已登记客观预报")} {esc(result.get("level") or "")}</p>' if result.get('source_family') else '')
                + ('<p class="muted">数值来自登记NC；临时时区与变量解析配置待业务核定。</p>' if result.get('provisional') else '') +
                f'<p class="muted">统计框：{esc(result["bounds"])}（西、南、东、北）；临时参考范围，非行政海域边界。</p>'
                + ''.join(f'<p class="muted">{esc(note)}</p>' for note in result.get('product_notes', [])) +
                f'<p>{esc(question)}</p><a href="series.csv" download>下载原生时次CSV</a><a href="report.json" download>下载完整JSON</a></header>'
                '<div class="cards">' + ''.join(cards) + '</div><section><h2>变化重点</h2><ul>' +
                ''.join(f'<li>{esc(text)}</li>' for text in self._insights(result)) + '</ul><p class="muted">以海上网格统计比较分区与发生时段，不根据台风中心强度推断当地严重影响。</p></section>' + gap + ''.join(figures) +
                '<section><h2>分区极值与实际覆盖</h2><div class="scroll"><table><tr><th>分区</th><th>统计框W,S,E,N</th><th>指标</th><th>极值</th><th>位置</th><th>发生时刻</th><th>原生样本起</th><th>原生样本止</th><th>海点数</th></tr>' + ''.join(rows) + '</table></div></section>'
                + ('<section><h2>分区风浪过程</h2><p class="muted">各分区分别计算原生时次的空间极值，缺时次处断开；曲线不是固定站点过程。</p>' + ''.join(region_process) + '</section>' if region_process else '') +
                '<section><h2>来源追溯</h2><p>显示数字保留小数位；JSON和CSV保留程序统计数值。有效样本/缺测样本按格点-时间计数，海点数按空间格点计数。</p><div class="scroll"><table><tr><th>要素</th><th>源文件</th><th>变量</th><th>SHA256</th></tr>' + ''.join(provenance) + '</table></div></section>'
                '<section><h2>台风背景、相似台风和预警知识</h2>'
                f'<a href="{esc(self.links["typhoon"])}">打开主界面查询台风背景</a><a href="{esc(self.links["analogue"])}">历史相似台风入口</a><a href="{esc(self.links["warnings"])}">官方预警查询与知识问答入口</a>'
                '<p>这些入口不代表本报告已执行台风归因、发布预警或计算灾损。海况格网和台风中心记录分别核对。</p></section>'
                '<section><h2>统计口径</h2><ul>' + ''.join(f'<li>{esc(text)}</li>' for text in result['limitations']) + '</ul></section></main></html>')
