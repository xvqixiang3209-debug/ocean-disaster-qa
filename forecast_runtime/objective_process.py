"""Sample-based process and optional user-threshold summaries."""
import math

import numpy as np

from .objective_time import _datetime, adjacent_native_interval


def threshold_specs(thresholds, field, unit):
    if thresholds is None or field not in thresholds:
        return []
    values = thresholds[field]
    values = values if isinstance(values, (list, tuple)) else [values]
    result = []
    for value in values:
        item = dict(value) if isinstance(value, dict) else {'value': value}
        raw = item.get('value', item.get('threshold'))
        if isinstance(raw, bool):
            raise ValueError('筛查阈值必须为有限数值')
        try:
            raw = float(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError('筛查阈值必须为有限数值') from exc
        if not math.isfinite(raw) or raw < 0:
            raise ValueError('筛查阈值必须为非负有限数值')
        if item.get('unit', unit) != unit:
            raise ValueError('筛查阈值单位必须与预报要素单位一致')
        comparison = item.get('comparison', '>=')
        if comparison not in {'>=', '>'}:
            raise ValueError('筛查阈值比较仅支持>=或>')
        result.append({'threshold': raw, 'unit': unit, 'comparison': comparison,
                       'origin': str(item.get('origin', 'user_supplied_screening')),
                       'label': str(item.get('label', '用户指定数值筛查')),
                       'metadata': dict(item.get('metadata') or {}),
                       'official_warning': False})
    return result


def threshold_sample(values, mask, spec):
    good = np.isfinite(values) & mask
    exceeding = good & ((values >= spec['threshold']) if spec['comparison'] == '>=' else (values > spec['threshold']))
    count, valid = int(exceeding.sum()), int(good.sum())
    return {'exceed_count': count, 'valid_count': valid,
            'fraction': round(count/valid, 6) if valid else None,
            'any_exceedance': bool(count) if valid else None}


def process_summary(series, coverage, specs):
    maxima = [item['maximum'] for item in series if item.get('maximum')]
    peak = max((item['value'] for item in maxima), default=None)
    result = {'sample_count': len(series), 'nonempty_sample_count': len(maxima),
              'peak_value': peak, 'peak_times': [item['time'] for item in maxima if item['value'] == peak],
              'first_nonempty_time': maxima[0]['time'] if maxima else None,
              'last_nonempty_time': maxima[-1]['time'] if maxima else None,
              'sample_statistic': 'native grid cells at each source-native timestamp',
              'threshold_exceedances': [],
              'note': 'Process values describe native samples, not interpolated continuous conditions.'}
    for i, spec in enumerate(specs):
        samples = [{'time': item['time'], **item['threshold_samples'][i]} for item in series]
        hits = [s for s in samples if s['any_exceedance'] is True]
        windows = []
        current = None
        previous = None
        for sample in samples:
            if sample['any_exceedance'] is not True:
                if current:
                    windows.append(current)
                    current = None
                previous = sample
                continue
            contiguous = previous and previous['any_exceedance'] is True and adjacent_native_interval(previous['time'], sample['time'], coverage)
            if not current or not contiguous:
                if current:
                    windows.append(current)
                current = {'start': sample['time'], 'end': sample['time'], 'duration_hours': 0., 'sample_count': 1}
            else:
                current['end'] = sample['time']
                current['duration_hours'] += (_datetime(sample['time'])-_datetime(previous['time'])).total_seconds()/3600
                current['sample_count'] += 1
            previous = sample
        if current:
            windows.append(current)
        for window in windows:
            window['duration_hours'] = round(window['duration_hours'], 6)
        fractions = [s['fraction'] for s in samples if s['fraction'] is not None]
        result['threshold_exceedances'].append({**spec, 'samples': samples,
            'first': hits[0]['time'] if hits else None, 'last': hits[-1]['time'] if hits else None,
            'first_exceedance_sample': hits[0]['time'] if hits else None,
            'last_exceedance_sample': hits[-1]['time'] if hits else None,
            'exceeding_sample_count': len(hits), 'intervals': windows,
            'duration_hours': round(sum(w['duration_hours'] for w in windows), 6),
            'maximum_fraction': max(fractions) if fractions else None,
            'fraction_denominator': 'finite native grid cells in requested model sea mask; grid-cell share, not geographic area',
            'duration_method': 'sum adjacent native intervals whose endpoints both have at least one exceedance; gaps and missing data break intervals',
            'duration_interpretation': 'sampled regional exceedance persistence; exceedances may move between cells; not a continuous or same-cell duration guarantee'})
    return result
