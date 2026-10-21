"""Local objective-forecast slices: immutable sources, native statistics, no LLM."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from threading import RLock
import hashlib
import json
import math
import re

import h5py
import numpy as np

from .storage import BEIJING
from .objective_time import temporal_coverage
from .objective_process import threshold_specs, threshold_sample, process_summary


PROFILE = {
    'id': 'ecmwf-objective-provisional-v1', 'provisional': True,
    'time': 'Unzoned NC timestamps and filename issue times provisionally interpreted as UTC; displayed in Asia/Shanghai.',
    'wave': 'swh: significant height of combined wind waves and swell, metres.',
    'wind': '10u/10v: 10 metre horizontal wind components; speed=hypot(u,v), m/s. Not a gust product.',
    'pressure': 'msl: mean sea-level pressure; Pa divided by 100 to hPa.',
    'scope': 'Objective model forecast, not observations, a warning, or an activity permit.',
}
L2_L5_PROFILE = {
    'id': 'zjocean-l2-l5-provisional-v1', 'provisional': True,
    'time': 'L2/L5 unzoned NC and filename issue times provisionally interpreted as Asia/Shanghai, matching the existing grid connector.',
    'wave': 'WAV: significant wave height in metres; L5 metadata says maximum significant wave height. Product averaging/maximisation interval remains unconfirmed.',
    'wind': 'SSW u10/v10: speed=hypot(u10,v10), m/s. Source descript is "Wind guest above 10m ground"; provisionally a 10 metre gust product, not ECMWF mean 10 metre wind.',
    'pressure': 'No pressure grid is supplied by these L2/L5 WAV/SSW products. It is not estimated from wind or imported from another dataset.',
    'wave_direction': 'MWD is not rendered: L5 metadata describes wave height and metres, so the direction convention and units remain unconfirmed.',
    'scope': 'Objective model forecast, not observations, a warning, or an activity permit. L2/L5 identify product domains; actual grid spacing comes from the coordinate axes.',
}
UNITS = {'wave': 'm', 'wind': 'm/s', 'pressure': 'hPa'}
ZHEJIANG_BOUNDS = [118., 26., 126., 33.]
SYNOPTIC_BOUNDS = [115., 15., 145., 36.]
_PLOT_LOCK = RLock()
COLOR_LIMITS = {'wave': (0, 8), 'wind': (0, 40), 'pressure': (960, 1040)}
COLORMAPS = {'wave': 'viridis', 'wind': 'turbo', 'pressure': 'coolwarm'}


@lru_cache(maxsize=3)
def _color_scale(field):
    from matplotlib import colormaps
    colors = (colormaps[COLORMAPS[field]](np.linspace(0, 1, 32))[:, :3] * 255).round().astype(int).tolist()
    return {'minimum': COLOR_LIMITS[field][0], 'maximum': COLOR_LIMITS[field][1],
            'min': COLOR_LIMITS[field][0], 'max': COLOR_LIMITS[field][1], 'colors': colors}


def _text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)


def _attrs(variable):
    return dict(variable.attrs) if hasattr(variable, 'attrs') else dict(variable._attributes)


def _stamp(value):
    if isinstance(value, datetime): result = value
    else:
        try: result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (ValueError, TypeError) as exc: raise ValueError('时间请使用ISO日期时刻') from exc
    return (result.replace(tzinfo=BEIJING) if result.tzinfo is None else result).astimezone(BEIJING)


def _bounds(value):
    if isinstance(value, str):
        try: value = [float(v) for v in value.split(',')]
        except ValueError as exc: raise ValueError('bounds请使用west,south,east,north') from exc
    if value is None: return list(ZHEJIANG_BOUNDS)
    if not isinstance(value, (list, tuple)) or len(value) != 4: raise ValueError('bounds需要4个经纬度值')
    try: west, south, east, north = map(float, value)
    except (ValueError, TypeError) as exc: raise ValueError('bounds必须为有限数值') from exc
    if not all(math.isfinite(v) for v in (west, south, east, north)): raise ValueError('bounds必须为有限数值')
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('bounds需满足-180≤west<east≤180且-90≤south<north≤90；跨日期线请分区查询')
    return [west, south, east, north]


@contextmanager
def _open(path, kind):
    if kind == 'hdf5':
        with h5py.File(path, 'r') as nc: yield nc
    else:
        from scipy.io import netcdf_file
        # mmap makes a single frame/rectangle read independent of cube size.
        nc = netcdf_file(str(path), 'r', mmap=True)
        try: yield nc.variables
        finally:
            # All our reads copy their requested slices. Detach the variable
            # objects too: a local ``ds`` reference must not keep mmap alive.
            for variable in nc.variables.values(): variable.data = None
            nc.variables.clear()
            nc.close()


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def _longitude(values):
    # A regional axis ending at 180E must not look like a near-global domain.
    return values.copy() if np.max(values) <= 180 else (values + 180) % 360 - 180


def _source_meta(path):
    path = Path(path).resolve()
    kind = 'hdf5' if h5py.is_hdf5(path) else 'classic'
    legacy = re.search(r'_(20\d{10})_(?:\d+_)?([A-Z]+)_\d+_(L[25])\.nc$', path.name, re.I)
    match = re.search(r'(20\d{8}(?:\d{2})?)\.nc$', path.name, re.I)
    if not legacy and not match: raise ValueError('客观预报文件名需包含YYYYMMDDHH或YYYYMMDDHHMM起报批次')
    stamp = legacy[1] if legacy else match[1]
    issue = datetime.strptime(stamp, '%Y%m%d%H' if len(stamp) == 10 else '%Y%m%d%H%M').replace(tzinfo=BEIJING if legacy else timezone.utc)
    family, level = ('zjocean_l2_l5', legacy[3].upper()) if legacy else ('ecmwf', None)
    profile = L2_L5_PROFILE if legacy else PROFILE
    with _open(path, kind) as nc:
        lat_key = next((k for k in ('latitude', 'lat') if k in nc), None)
        lon_key = next((k for k in ('longitude', 'lon') if k in nc), None)
        if not lat_key or not lon_key or 'time' not in nc: raise ValueError('客观预报缺少time/latitude/longitude')
        lat = np.array(nc[lat_key][:], dtype=float, copy=True)
        lon = np.array(nc[lon_key][:], dtype=float, copy=True)
        if (lat.ndim != 1 or lon.ndim != 1 or len(lat) < 2 or len(lon) < 2
                or not np.isfinite(lat).all() or not np.isfinite(lon).all()):
            raise ValueError('客观预报仅支持有限的一维经纬度轴')
        if not (np.all(np.diff(lat) > 0) or np.all(np.diff(lat) < 0)): raise ValueError('纬度轴必须严格单调')
        if not (np.all(np.diff(lon) > 0) or np.all(np.diff(lon) < 0)): raise ValueError('经度轴必须严格单调')
        if np.max(np.abs(lat)) > 90 or np.min(lon) < -180 or np.max(lon) > 360: raise ValueError('经纬度坐标范围无效')
        time_attrs = _attrs(nc['time'])
        time_unit = _text(time_attrs.get('units', ''))
        origin = re.fullmatch(r'(hours|minutes|seconds|days) since (.+)', time_unit)
        if not origin: raise ValueError('客观预报时间单位无法解析')
        calendar = _text(time_attrs.get('calendar', 'standard'))
        if calendar not in {'standard', 'gregorian', 'proleptic_gregorian'}: raise ValueError('客观预报日历暂不支持')
        time_origin = 'netcdf_attribute'
        try: base = datetime.fromisoformat(origin[2].replace('Z', '+00:00'))
        except ValueError:
            if not legacy or origin[2] != 'initial time': raise ValueError('客观预报时间起点无法解析')
            base, time_origin = issue, 'filename_assumed'
        timezone_assumed = base.tzinfo is None or time_origin == 'filename_assumed'
        if base.tzinfo is None: base = base.replace(tzinfo=BEIJING if legacy else timezone.utc)
        factor = {'seconds': 1, 'minutes': 60, 'hours': 3600, 'days': 86400}[origin[1]]
        offsets = np.array(nc['time'][:], dtype=float, copy=True)
        if offsets.ndim != 1 or not np.isfinite(offsets).all(): raise ValueError('时间坐标无效')
        times = [(base + timedelta(seconds=float(t) * factor)).astimezone(BEIJING).isoformat() for t in offsets]
        if not times or times != sorted(set(times)): raise ValueError('预报时间必须唯一递增')
        fields = {}
        specs = [('wave', ['WAV']), ('wind', ['u10', 'v10'])] if legacy else [('wave', ['swh']), ('wind', ['10u', '10v']), ('pressure', ['msl'])]
        for field, variables in specs:
            if not all(v in nc for v in variables): continue
            for name in variables:
                shape = nc[name].shape
                if not (len(shape) in {3, 4} and shape[0] == len(times) and shape[-2:] == (len(lat), len(lon))
                        and (len(shape) == 3 or shape[1] == 1)):
                    raise ValueError('变量维度需为time,lat,lon或time,1,lat,lon')
            attributes = [_attrs(nc[v]) for v in variables]
            units = [_text(a.get('units', a.get('unit', ''))) for a in attributes]
            accepted = {'wave': {'m', 'metre', 'meter'}, 'wind': {'m/s', 'm s**-1', 'm s-1'}, 'pressure': {'Pa', 'hPa'}}
            if any(u not in accepted[field] for u in units): raise ValueError('客观预报变量单位不支持：' + field)
            fields[field] = {'variables': variables, 'source_units': units, 'unit': UNITS[field],
                             'scale': .01 if field == 'pressure' and units[0] == 'Pa' else 1.,
                             'descriptions': [_text(a.get('long_name', a.get('descript', ''))) for a in attributes],
                             'interpretation': profile[field],
                             'wind_kind': ('provisional_gust_10m' if legacy else 'mean_10m') if field == 'wind' else None}
        if not fields: raise ValueError('文件未包含swh、10u/10v、msl或L2/L5 WAV、u10/v10可用预报变量')
        coverage = [float(_longitude(lon).min()), float(lat.min()), float(_longitude(lon).max()), float(lat.max())]
    return {'path': str(path), 'filename': path.name, 'sha256': _sha(path), 'bytes': path.stat().st_size,
            'mtime_ns': path.stat().st_mtime_ns,
            'format': kind, 'latitude_name': lat_key, 'longitude_name': lon_key,
            'batch': issue.astimezone(BEIJING).isoformat(), 'batch_utc': issue.astimezone(timezone.utc).isoformat(),
            'source_family': family, 'level': level, 'product': legacy[2].upper() if legacy else None,
            'grid_spacing_degrees': {'longitude': round(float((lon.max()-lon.min())/(len(lon)-1)), 6),
                                     'latitude': round(float((lat.max()-lat.min())/(len(lat)-1)), 6)},
            'time_origin': time_origin, 'time_timezone_assumed': timezone_assumed, 'profile': profile,
            'valid_times': times, 'available_start': times[0], 'available_end': times[-1],
            'coverage': coverage, 'shape': [len(times), len(lat), len(lon)], 'fields': fields}


def _public_source(source):
    return {k: source[k] for k in ('filename', 'sha256', 'bytes', 'format', 'batch', 'batch_utc', 'available_start', 'available_end',
                                  'source_family', 'level', 'product', 'grid_spacing_degrees', 'time_origin', 'time_timezone_assumed') if k in source}


class ObjectiveForecast:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / 'index.json'
        self.lock = RLock()

    def _index(self):
        if not self.index_path.exists(): return {'datasets': [], 'profile': PROFILE}
        return json.loads(self.index_path.read_text(encoding='utf-8'))

    def register_directory(self, paths):
        if isinstance(paths, (str, Path)): paths = [paths]
        files = []
        for item in paths:
            path = Path(item).resolve()
            files.extend(sorted(path.rglob('*.nc')) if path.is_dir() else [path])
        files = list(dict.fromkeys(files))
        # A downloaded product folder also contains SST/SWL/OCU. They are
        # handled by the existing business connector, not silently reinterpreted
        # as wave, wind or pressure by this renderer.
        files = [path for path in files if not re.search(r'_(?:SST|SWL|OCU)_\d+_L[25]\.nc$', path.name, re.I)]
        if not files: raise ValueError('没有找到NC文件')
        sources = [_source_meta(path) for path in files]
        groups = {}
        for source in sources: groups.setdefault((source.get('source_family', 'ecmwf'), source.get('level'), source['batch']), []).append(source)
        with self.lock:
            existing = self._index()
            for (family, level, batch), members in groups.items():
                fields = {}
                for source in members:
                    for field in source['fields']:
                        if field in fields: raise ValueError('同批次同要素出现多个来源，请显式选择文件')
                        fields[field] = source['sha256']
                digest = hashlib.sha256(json.dumps(sorted(s['sha256'] for s in members)).encode()).hexdigest()[:12]
                prefix = 'zjocean_' + level.lower() if family == 'zjocean_l2_l5' else 'ecmwf'
                dataset_id = prefix + '_' + _stamp(batch).strftime('%Y%m%d%H%M') + '_' + digest
                value = {'dataset_id': dataset_id, 'batch': batch, 'batch_utc': members[0]['batch_utc'],
                         'sources': members, 'fields': fields, 'profile': members[0]['profile'], 'provisional': True,
                         'source_family': family, 'level': level,
                         'label': ('浙江海洋网格 ' + level if level else 'ECMWF')}
                existing['datasets'] = [d for d in existing['datasets'] if d['dataset_id'] != dataset_id] + [value]
            part = self.index_path.with_suffix('.json.part')
            part.write_text(json.dumps(existing, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            part.replace(self.index_path)
        return self.catalogue()

    def _dataset(self, dataset_id):
        datasets = self._index()['datasets']
        if dataset_id is None and datasets: return max(datasets, key=lambda d: d['batch'])
        for dataset in datasets:
            if dataset['dataset_id'] == dataset_id: return dataset
        raise ValueError('未登记该客观预报数据集')

    def _source(self, dataset, field):
        if field not in UNITS: raise ValueError('field仅支持wave、wind、pressure')
        key = dataset['fields'].get(field)
        if not key: raise ValueError('数据集没有该预报要素')
        source = next(s for s in dataset['sources'] if s['sha256'] == key)
        if not Path(source['path']).is_file(): raise ValueError('登记的原始预报文件不存在')
        if Path(source['path']).stat().st_size != source['bytes']: raise ValueError('原始预报文件大小变化，请重新登记')
        if source.get('mtime_ns') is not None and Path(source['path']).stat().st_mtime_ns != source['mtime_ns']:
            raise ValueError('原始预报文件修改时间变化，请重新登记并核验哈希')
        return source

    def catalogue(self):
        items = []
        for dataset in sorted(self._index()['datasets'], key=lambda d: d['batch'], reverse=True):
            fields = {}
            for field in dataset['fields']:
                source = self._source(dataset, field)
                fields[field] = {'unit': UNITS[field], 'valid_times': source['valid_times'], 'coverage': source['coverage'],
                                 'available_start': source['available_start'], 'available_end': source['available_end'],
                                 'source_file_id': source['sha256'], 'source_files': [_public_source(source)],
                                 'grid_spacing_degrees': source.get('grid_spacing_degrees'),
                                 **{k: source['fields'][field][k] for k in ('interpretation', 'wind_kind', 'descriptions') if k in source['fields'][field]}}
            items.append({'dataset_id': dataset['dataset_id'], 'batch': dataset['batch'], 'batch_utc': dataset['batch_utc'],
                          'fields': fields, 'profile': dataset['profile'], 'provisional': True,
                          'source_family': dataset.get('source_family', 'ecmwf'), 'level': dataset.get('level'),
                          'label': dataset.get('label', 'ECMWF'), 'available_fields': list(fields),
                          'unavailable_fields': [f for f in UNITS if f not in fields]})
        return {'datasets': items, 'profile': PROFILE, 'provisional': True,
                'default_bounds': ZHEJIANG_BOUNDS, 'synoptic_bounds': SYNOPTIC_BOUNDS}

    def _native(self, dataset, field, valid_time, bounds):
        source = self._source(dataset, field)
        valid = _stamp(valid_time or source['valid_times'][0]).isoformat()
        if valid not in source['valid_times']: raise ValueError('该要素没有所选原生预报时刻，未插值或借用其他时刻')
        k = source['valid_times'].index(valid)
        west, south, east, north = _bounds(bounds)
        with _open(source['path'], source['format']) as nc:
            lat = np.array(nc[source['latitude_name']][:], dtype=float, copy=True)
            raw_lon = np.array(nc[source['longitude_name']][:], dtype=float, copy=True)
            lon = _longitude(raw_lon)
            iy = np.where((lat >= south - 1e-5) & (lat <= north + 1e-5))[0]
            ix = np.where((lon >= west - 1e-5) & (lon <= east + 1e-5))[0]
            if not len(iy) or not len(ix): raise ValueError('所选范围不包含该产品的原生网格中心')
            iy = iy[np.argsort(-lat[iy])]; ix = ix[np.argsort(lon[ix])]
            y0, y1, x0, x1 = int(iy.min()), int(iy.max()) + 1, int(ix.min()), int(ix.max()) + 1
            arrays = []
            for variable in source['fields'][field]['variables']:
                ds = nc[variable]
                selection = (k, slice(y0, y1), slice(x0, x1)) if len(ds.shape) == 3 else (k, 0, slice(y0, y1), slice(x0, x1))
                raw = np.array(ds[selection], copy=True)
                values = raw.astype(float)
                attrs = _attrs(ds)
                missing = ~np.isfinite(values)
                for name in ('_FillValue', 'missing_value'):
                    if name in attrs:
                        for marker in np.asarray(attrs[name]).reshape(-1): missing |= raw == marker
                if source.get('source_family') == 'zjocean_l2_l5':
                    # Several legacy WAV/SSW files omit missing-value attrs.
                    # Retain the existing connector's sentinel contract, only
                    # for this product family (Pa pressure is never masked).
                    missing |= (values <= -999) | (values >= 9999)
                values = values * float(attrs.get('scale_factor', 1)) + float(attrs.get('add_offset', 0))
                values[missing] = np.nan
                arrays.append(values[np.ix_(iy - y0, ix - x0)])
            values = np.hypot(*arrays) if field == 'wind' else arrays[0] * source['fields'][field]['scale']
            if field == 'wave': values[values < 0] = np.nan
            axes_lat, axes_lon = lat[iy], lon[ix]
        return {'values': values, 'u': arrays[0] if field == 'wind' else None,
                'v': arrays[1] if field == 'wind' else None, 'lat': axes_lat, 'lon': axes_lon,
                'source': source, 'valid_time': valid, 'requested_bounds': [west, south, east, north]}

    def _mask(self, dataset, native, field, bounds, sea_mask):
        if sea_mask not in {None, '', 'none', 'wave_valid'}: raise ValueError('sea_mask仅支持none或wave_valid')
        if sea_mask != 'wave_valid': return np.ones_like(native['values'], dtype=bool), None
        if 'wave' not in dataset['fields']: raise ValueError('该数据集没有海浪有效海点掩膜')
        source = self._source(dataset, 'wave')
        # A static model sea mask, not a shoreline survey or an inundation map.
        margin = [max(-180, bounds[0] - 1), max(-90, bounds[1] - 1), min(180, bounds[2] + 1), min(90, bounds[3] + 1)]
        wave = self._native(dataset, 'wave', source['valid_times'][0], margin)
        yi = np.abs(wave['lat'][:, None] - native['lat'][None, :]).argmin(axis=0)
        xi = np.abs(wave['lon'][:, None] - native['lon'][None, :]).argmin(axis=0)
        mask = np.isfinite(wave['values'][np.ix_(yi, xi)])
        mask &= ((native['lat'][:, None] >= wave['lat'].min()) & (native['lat'][:, None] <= wave['lat'].max())
                 & (native['lon'][None, :] >= wave['lon'].min()) & (native['lon'][None, :] <= wave['lon'].max()))
        return mask, {'method': 'nearest_wave_native_valid_cell_mask', 'source': _public_source(source),
                      'valid_time': wave['valid_time'], 'note': 'Model sea-grid mask; not a precise coastline.'}

    @staticmethod
    def _stats(native, mask=None):
        values = native['values']
        mask = np.ones(values.shape, dtype=bool) if mask is None else mask
        good = np.isfinite(values) & mask
        result = {'native_count': int(values.size), 'sea_count': int(mask.sum()), 'valid_count': int(good.sum()),
                  'missing_count': int((mask & ~np.isfinite(values)).sum()), 'minimum': None, 'maximum': None, 'mean': None}
        if not good.any(): return result
        def edge(maximum):
            flat = np.nanargmax(np.where(good, values, np.nan)) if maximum else np.nanargmin(np.where(good, values, np.nan))
            y, x = np.unravel_index(flat, values.shape)
            return {'value': round(float(values[y, x]), 6), 'lon': float(native['lon'][x]), 'lat': float(native['lat'][y]), 'time': native['valid_time']}
        result.update(minimum=edge(False), maximum=edge(True), mean=round(float(values[good].mean()), 6))
        return result

    def frame(self, dataset_id=None, field='wave', valid_time=None, bounds=None, maxsize=180, sea_mask=None):
        if type(maxsize) is not int or not 2 <= maxsize <= 1200: raise ValueError('maxsize需为2至1200的整数')
        dataset = self._dataset(dataset_id); bounds = _bounds(bounds)
        if field not in UNITS: raise ValueError('field仅支持wave、wind、pressure')
        if field not in dataset['fields']:
            return {**self._unavailable(dataset, field), 'requested_bounds': bounds,
                    'valid_time': _stamp(valid_time).isoformat() if valid_time else None,
                    'values': [], 'lat': [], 'lon': [], 'shape': [0, 0], 'native_shape': [0, 0],
                    'bounds': None, 'stats': {'native_count': 0, 'sea_count': 0, 'valid_count': 0,
                    'missing_count': 0, 'minimum': None, 'maximum': None, 'mean': None}}
        native = self._native(dataset, field, valid_time, bounds)
        mask, mask_meta = self._mask(dataset, native, field, bounds, sea_mask)
        stats = self._stats(native, mask)
        ys = np.unique(np.linspace(0, len(native['lat']) - 1, min(maxsize, len(native['lat']))).astype(int))
        xs = np.unique(np.linspace(0, len(native['lon']) - 1, min(maxsize, len(native['lon']))).astype(int))
        def matrix(value):
            selected = value[np.ix_(ys, xs)]
            eligible = mask[np.ix_(ys, xs)]
            return [[round(float(v), 6) if bool(ok) and np.isfinite(v) else None for v, ok in zip(row, m)] for row, m in zip(selected, eligible)]
        lat, lon = native['lat'][ys], native['lon'][xs]
        result = {'state': 'ready' if stats['valid_count'] else 'no_data', 'dataset_id': dataset['dataset_id'], 'field': field,
                  'unit': UNITS[field], 'values': matrix(native['values']), 'lat': lat.tolist(), 'lon': lon.tolist(),
                  'shape': [len(lat), len(lon)], 'native_shape': list(native['values'].shape),
                  'axis_order': 'north_to_south_west_to_east', 'bounds': [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())],
                  'requested_bounds': bounds, 'batch': dataset['batch'], 'valid_time': native['valid_time'],
                  'source_files': [_public_source(native['source'])], 'provisional': True, 'profile': dataset['profile'],
                  'stats': stats, 'stats_resolution': 'native_grid_before_display_sampling', 'sea_mask': mask_meta,
                  'color_scale': _color_scale(field), 'colormap': COLORMAPS[field]}
        result.update(source_family=dataset.get('source_family', 'ecmwf'), level=dataset.get('level'),
                      grid_spacing_degrees=native['source'].get('grid_spacing_degrees'),
                      interpretation=native['source']['fields'][field].get('interpretation'),
                      wind_kind=native['source']['fields'][field].get('wind_kind'))
        if field == 'wind': result.update(u=matrix(native['u']), v=matrix(native['v']))
        return result

    def point(self, dataset_id=None, field='wave', valid_time=None, lon=None, lat=None):
        try: lon, lat = float(lon), float(lat)
        except (TypeError, ValueError) as exc: raise ValueError('point需要有效lon/lat') from exc
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90): raise ValueError('点经纬度超界')
        dataset = self._dataset(dataset_id)
        if field not in UNITS: raise ValueError('field仅支持wave、wind、pressure')
        if field not in dataset['fields']:
            return {**self._unavailable(dataset, field), 'value': None, 'lon': lon, 'lat': lat,
                    'valid_time': _stamp(valid_time).isoformat() if valid_time else None}
        coverage = self._source(dataset, field)['coverage']
        if not (coverage[0] <= lon <= coverage[2] and coverage[1] <= lat <= coverage[3]):
            raise ValueError('点位超出该预报产品坐标覆盖，不作边界外推')
        native = self._native(dataset, field, valid_time, [max(-180, lon - 1), max(-90, lat - 1), min(180, lon + 1), min(90, lat + 1)])
        y = int(np.abs(native['lat'] - lat).argmin()); x = int(np.abs(native['lon'] - lon).argmin())
        value = native['values'][y, x]
        result = {'state': 'ready' if np.isfinite(value) else 'missing_grid', 'dataset_id': dataset['dataset_id'], 'field': field,
                  'value': round(float(value), 6) if np.isfinite(value) else None, 'unit': UNITS[field], 'lon': lon, 'lat': lat,
                  'grid_lon': float(native['lon'][x]), 'grid_lat': float(native['lat'][y]), 'method': 'nearest_native_cell_no_fill',
                  'batch': dataset['batch'], 'valid_time': native['valid_time'], 'source_files': [_public_source(native['source'])],
                  'provisional': True, 'profile': dataset['profile']}
        result.update(source_family=dataset.get('source_family', 'ecmwf'), level=dataset.get('level'),
                      interpretation=native['source']['fields'][field].get('interpretation'),
                      wind_kind=native['source']['fields'][field].get('wind_kind'))
        if field == 'wind': result.update(u=float(native['u'][y, x]) if np.isfinite(native['u'][y, x]) else None,
                                          v=float(native['v'][y, x]) if np.isfinite(native['v'][y, x]) else None)
        return result

    @staticmethod
    def _unavailable(dataset, field):
        names = {'wave': '海浪', 'wind': '海面风', 'pressure': '气压'}
        return {'state': 'no_data', 'code': 'field_not_provided', 'field': field, 'unit': UNITS[field],
                'reason': '当前数据集未提供' + names[field] + '网格，不从其他批次或产品补造。',
                'dataset_id': dataset['dataset_id'], 'batch': dataset['batch'], 'source_files': [],
                'provisional': True, 'profile': dataset['profile'],
                'source_family': dataset.get('source_family', 'ecmwf'), 'level': dataset.get('level')}

    def stats72(self, dataset_id=None, start=None, hours=72, bounds=None, sea_mask='wave_valid', thresholds=None):
        if type(hours) is not int or not 1 <= hours <= 360: raise ValueError('hours需为1至360整数')
        if thresholds is not None and (not isinstance(thresholds, dict) or any(k not in {'wave', 'wind'} for k in thresholds)):
            raise ValueError('thresholds仅支持wave、wind的用户筛查阈值')
        dataset = self._dataset(dataset_id); bounds = _bounds(bounds)
        first = _stamp(start or dataset['batch']); last = first + timedelta(hours=hours)
        fields = {}
        for field in dataset['fields']:
            source = self._source(dataset, field)
            times = [t for t in source['valid_times'] if first <= _stamp(t) <= last]
            coverage = temporal_coverage(source['valid_times'], first, last)
            specs = threshold_specs(thresholds, field, UNITS[field])
            series = []; mask = mask_meta = None
            for valid in times:
                native = self._native(dataset, field, valid, bounds)
                if mask is None: mask, mask_meta = self._mask(dataset, native, field, bounds, sea_mask)
                item = {'time': valid, **self._stats(native, mask)}
                if specs:
                    item['threshold_samples'] = [threshold_sample(native['values'], mask, spec) for spec in specs]
                series.append(item)
            maxima = [s['maximum'] for s in series if s['maximum']]
            minima = [s['minimum'] for s in series if s['minimum']]
            partial = coverage['partial']
            coverage['nonempty_frame_count'] = len(maxima)
            process = process_summary(series, coverage, specs)
            fields[field] = {'state': 'ready' if maxima else 'no_data', 'unit': UNITS[field], 'series': series,
                             'minimum': min(minima, key=lambda r: r['value']) if minima else None,
                             'maximum': max(maxima, key=lambda r: r['value']) if maxima else None,
                             'native_count': max((s['native_count'] for s in series), default=0),
                             'sea_count': max((s['sea_count'] for s in series), default=0),
                             'valid_count': sum(s['valid_count'] for s in series), 'missing_count': sum(s['missing_count'] for s in series),
                             'sample_count_unit': 'grid_cell_time_samples', 'frame_count': len(series),
                             'coverage_start': source['available_start'], 'coverage_end': source['available_end'],
                             'available_start': source['available_start'], 'available_end': source['available_end'],
                             'series_start': times[0] if times else None, 'series_end': times[-1] if times else None,
                             'requested_start': first.isoformat(), 'requested_end': last.isoformat(), 'partial': partial,
                             'temporal_coverage': coverage, 'process': process,
                             'data_partial': any(not s['valid_count'] for s in series) or not series,
                             'sea_mask': mask_meta, 'source_files': [_public_source(source)],
                             'coverage_bounds': source['coverage'], 'requested_bounds': bounds,
                             'spatial_partial': not (source['coverage'][0] <= bounds[0] and source['coverage'][1] <= bounds[1]
                                                     and source['coverage'][2] >= bounds[2] and source['coverage'][3] >= bounds[3]),
                             'grid_spacing_degrees': source.get('grid_spacing_degrees'),
                             **{k: source['fields'][field][k] for k in ('interpretation', 'wind_kind', 'descriptions') if k in source['fields'][field]}}
        for field in UNITS:
            if field not in fields:
                fields[field] = {**self._unavailable(dataset, field), 'series': [], 'minimum': None, 'maximum': None,
                                 'frame_count': 0, 'valid_count': 0, 'missing_count': 0, 'partial': True,
                                 'requested_start': first.isoformat(), 'requested_end': last.isoformat()}
        return {'state': 'ready' if any(f['state'] == 'ready' for f in fields.values()) else 'no_data',
                'dataset_id': dataset['dataset_id'], 'batch': dataset['batch'], 'fields': fields, 'bounds': bounds,
                'requested_start': first.isoformat(), 'requested_end': last.isoformat(), 'hours': hours,
                'provisional': True, 'profile': dataset['profile'], 'statistics_resolution': 'native_grid_no_display_sampling',
                'source_family': dataset.get('source_family', 'ecmwf'), 'level': dataset.get('level'),
                'available_fields': list(dataset['fields']), 'unavailable_fields': [f for f in UNITS if f not in dataset['fields']]}

    def temporal_maximum(self, dataset_id=None, field='wave', start=None, hours=72, bounds=None, maxsize=180, sea_mask='wave_valid'):
        """Native per-cell period maximum, incrementally reduced before sampling.

        No wind vectors are returned: different cells can peak at different
        timestamps. Keeping one frame plus accumulators bounds cube memory.
        """
        if type(hours) is not int or not 1 <= hours <= 360: raise ValueError('hours需为1至360整数')
        if type(maxsize) is not int or not 2 <= maxsize <= 1200: raise ValueError('maxsize需为2至1200的整数')
        if field not in UNITS: raise ValueError('field仅支持wave、wind、pressure')
        dataset = self._dataset(dataset_id); bounds = _bounds(bounds)
        first = _stamp(start or dataset['batch']); last = first + timedelta(hours=hours)
        base = {'product_type': 'temporal_maximum', 'period_start': first.isoformat(), 'period_end': last.isoformat(),
                'requested_start': first.isoformat(), 'requested_end': last.isoformat(), 'hours': hours,
                'requested_bounds': bounds, 'dataset_id': dataset['dataset_id'], 'batch': dataset['batch'],
                'field': field, 'unit': UNITS[field], 'provisional': True, 'profile': dataset['profile'],
                'values': [], 'valid_sample_counts': [], 'valid_sample_ratios': [], 'lat': [], 'lon': [], 'shape': [0, 0], 'native_shape': [0, 0],
                'source_family': dataset.get('source_family', 'ecmwf'), 'level': dataset.get('level'),
                'valid_time': None, 'bounds': None,
                'stats': {'native_count': 0, 'sea_count': 0, 'valid_count': 0, 'missing_count': 0,
                          'minimum': None, 'maximum': None, 'mean': None},
                'statistics_resolution': 'native_grid_temporal_reduction_before_display_sampling',
                'vectors_omitted': field == 'wind'}
        if field not in dataset['fields']:
            return {**base, **self._unavailable(dataset, field)}
        source = self._source(dataset, field)
        coverage = temporal_coverage(source['valid_times'], first, last)
        times = coverage['available_times']
        base.update(temporal_coverage=coverage, partial=coverage['partial'], frame_count=len(times),
                    expected_sample_count=coverage['expected_count'],
                    source_files=[_public_source(source)],
                    grid_spacing_degrees=source.get('grid_spacing_degrees'),
                    interpretation=source['fields'][field].get('interpretation'),
                    wind_kind=source['fields'][field].get('wind_kind'),
                    color_scale=_color_scale(field), colormap=COLORMAPS[field],
                    coverage_bounds=source['coverage'],
                    spatial_partial=not (source['coverage'][0] <= bounds[0] and source['coverage'][1] <= bounds[1]
                                         and source['coverage'][2] >= bounds[2] and source['coverage'][3] >= bounds[3]))
        if not times:
            return {**base, 'state': 'no_data', 'code': 'no_native_times_in_window'}
        maximum = counts = peak_indices = mask = mask_meta = None
        for i, valid in enumerate(times):
            native = self._native(dataset, field, valid, bounds)
            if maximum is None:
                maximum = np.full(native['values'].shape, np.nan)
                counts = np.zeros(native['values'].shape, dtype=np.int32)
                peak_indices = np.full(native['values'].shape, -1, dtype=np.int32)
                mask, mask_meta = self._mask(dataset, native, field, bounds, sea_mask)
            # Wave values already carry their native dynamic validity. A hole in
            # the first wave frame must not suppress a later valid wave peak.
            active_mask = np.ones_like(mask) if field == 'wave' and sea_mask == 'wave_valid' else mask
            good = np.isfinite(native['values']) & active_mask
            replace = good & (~np.isfinite(maximum) | (native['values'] > maximum))
            peak_indices[replace] = i
            counts += good.astype(np.int32)
            np.fmax(maximum, np.where(good, native['values'], np.nan), out=maximum)
        display_mask = counts > 0 if field == 'wave' and sea_mask == 'wave_valid' else mask
        aggregate = {**native, 'values': maximum, 'valid_time': None}
        stats = self._stats(aggregate, display_mask)
        for name in ('minimum', 'maximum'):
            edge = stats[name]
            if edge:
                y = int(np.where(native['lat'] == edge['lat'])[0][0])
                x = int(np.where(native['lon'] == edge['lon'])[0][0])
                edge['time'] = times[int(peak_indices[y, x])]
        ys = np.unique(np.linspace(0, len(native['lat'])-1, min(maxsize, len(native['lat']))).astype(int))
        xs = np.unique(np.linspace(0, len(native['lon'])-1, min(maxsize, len(native['lon']))).astype(int))
        selected = maximum[np.ix_(ys, xs)]
        result_values = [[round(float(v), 6) if np.isfinite(v) else None for v in row] for row in selected]
        lat, lon = native['lat'][ys], native['lon'][xs]
        expected_count = coverage['expected_count']
        selected_counts = counts[np.ix_(ys, xs)]
        base.update(state='ready' if stats['valid_count'] else 'no_data', values=result_values,
                    lat=lat.tolist(), lon=lon.tolist(), shape=[len(lat), len(lon)], native_shape=list(maximum.shape),
                    axis_order='north_to_south_west_to_east',
                    bounds=[float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())],
                    stats=stats, valid_sample_counts=selected_counts.tolist(),
                    valid_sample_ratios=[[round(int(v)/expected_count, 6) if expected_count else None for v in row] for row in selected_counts],
                    valid_sample_count_unit='finite source-native timestamps per displayed grid cell',
                    valid_sample_ratio_denominator='expected native timestamps in requested window; missing values and missing timestamps both reduce the ratio',
                    maximum_sample_count=int(counts.max()), minimum_sample_count=int(counts[display_mask].min()) if display_mask.any() else 0,
                    sea_mask=mask_meta, stats_resolution='native_grid_before_display_sampling',
                    summary_note='Each cell is its maximum over available native times, which may differ between cells. This is not a snapshot at one shared peak time.')
        if field == 'wave' and sea_mask == 'wave_valid':
            base['sea_mask'] = {'method': 'any_valid_wave_native_sample_in_requested_window', 'source': _public_source(source),
                                'note': 'Native wave-valid cells over this period; not a precise coastline.'}
        return base

    def image_temporal_maximum_png(self, dataset_id=None, field='wave', start=None, hours=72, bounds=None, maxsize=600, sea_mask='wave_valid'):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import MaxNLocator
        result = self.temporal_maximum(dataset_id, field, start, hours, bounds, maxsize, sea_mask)
        with _PLOT_LOCK:
            figure = Figure(figsize=(9.6, 6), dpi=120); FigureCanvasAgg(figure)
            ax = figure.add_subplot(111)
            if result['values']:
                values = np.array(result['values'], dtype=float)
                mesh = ax.pcolormesh(result['lon'], result['lat'], np.ma.masked_invalid(values), shading='nearest',
                                     cmap=COLORMAPS[field], vmin=COLOR_LIMITS[field][0], vmax=COLOR_LIMITS[field][1])
                figure.colorbar(mesh, ax=ax, label=field + ' [' + result['unit'] + ']')
                ax.set_aspect(1 / max(.2, math.cos(math.radians(np.mean(result['lat'])))))
                ax.set(xlabel='Longitude (degrees east)', ylabel='Latitude (degrees north)')
                ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
                ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
                ax.grid(alpha=.2)
            else:
                ax.axis('off')
                ax.text(.5, .5, 'No available native data for this field and window.', ha='center', transform=ax.transAxes)
            ax.set_title('PER-GRID PERIOD MAXIMUM | ' + field + '\n' + result['period_start'] + ' to ' + result['period_end']
                         + '\nIssue ' + result['batch'] + (' | PARTIAL TIME COVERAGE' if result.get('partial') else ''))
            figure.text(.02, .015, 'Different cells may peak at different times; no wind vectors. Provisional model interpretation, not an official warning.', fontsize=8)
            figure.tight_layout(rect=(0, .035, 1, 1))
            output = BytesIO(); figure.savefig(output, format='png'); return output.getvalue()

    def image_png(self, dataset_id=None, field='wave', valid_time=None, bounds=None, maxsize=600, sea_mask=None):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        result = self.frame(dataset_id, field, valid_time, bounds, maxsize, sea_mask)
        if result.get('code') == 'field_not_provided':
            with _PLOT_LOCK:
                figure = Figure(figsize=(9.6, 6), dpi=120); FigureCanvasAgg(figure)
                ax = figure.add_subplot(111); ax.axis('off')
                ax.text(.5, .55, field + ' grid not provided by this dataset.', ha='center', transform=ax.transAxes)
                ax.text(.5, .45, 'No estimate or data from another product has been substituted.', ha='center', transform=ax.transAxes)
                output = BytesIO(); figure.savefig(output, format='png'); return output.getvalue()
        values = np.array(result['values'], dtype=float)
        with _PLOT_LOCK:
            figure = Figure(figsize=(9.6, 6), dpi=120); FigureCanvasAgg(figure)
            ax = figure.add_subplot(111)
            mesh = ax.pcolormesh(result['lon'], result['lat'], np.ma.masked_invalid(values), shading='nearest', cmap=COLORMAPS[field],
                                 vmin=COLOR_LIMITS[field][0], vmax=COLOR_LIMITS[field][1])
            figure.colorbar(mesh, ax=ax, label=field + ' [' + result['unit'] + ']')
            if field == 'wind':
                stride = max(1, math.ceil(max(values.shape) / 22))
                u, v = np.array(result['u'], dtype=float), np.array(result['v'], dtype=float)
                x, y = np.meshgrid(result['lon'][::stride], result['lat'][::stride])
                ax.quiver(x, y, u[::stride, ::stride], v[::stride, ::stride], color='white', alpha=.85, width=.0025)
            if field == 'pressure' and np.isfinite(values).sum() > 3 and min(values.shape) > 1 and np.nanmax(values) > np.nanmin(values):
                contour = ax.contour(result['lon'], result['lat'], np.ma.masked_invalid(values), colors='black', linewidths=.6)
                ax.clabel(contour, fontsize=7, fmt='%g')
            ax.set(xlabel='Longitude (degrees east)', ylabel='Latitude (degrees north)',
                   title=('ZJOCEAN ' + str(result.get('level')) if result.get('source_family') == 'zjocean_l2_l5' else 'ECMWF')
                   + ' objective forecast | ' + ('provisional 10m gust speed' if result.get('wind_kind') == 'provisional_gust_10m' else field)
                   + '\nIssue ' + result['batch'] + ' | Valid ' + result['valid_time'])
            ax.set_aspect(1 / max(.2, math.cos(math.radians(np.mean(result['lat'])))))
            ax.grid(alpha=.2); figure.text(.02, .015, 'Provisional interpretation; native-source model forecast, not an official warning.', fontsize=8)
            figure.tight_layout(rect=(0, .035, 1, 1))
            output = BytesIO(); figure.savefig(output, format='png'); return output.getvalue()

    render_png = image_png
