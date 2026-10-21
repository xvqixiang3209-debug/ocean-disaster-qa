"""Native forecast time-axis coverage; never assume an hourly product."""
from datetime import datetime, timedelta, timezone
import math


def _datetime(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return value.replace(tzinfo=timezone(timedelta(hours=8))) if value.tzinfo is None else value


def temporal_coverage(times, start, end):
    """Compare the requested inclusive window with the full source-native axis.

    Repeated longer steps form a legitimate coarser native segment. An isolated
    longer multiple of the shortest cadence is a missing timestamp, not a new
    cadence. This is an inference from coordinates, not a provider contract;
    genuinely irregular intervals remain explicitly uncertain.
    """
    first, last = _datetime(start), _datetime(end)
    if last < first:
        raise ValueError('预报结束时刻不能早于开始时刻')
    axis = sorted({_datetime(t).astimezone(first.tzinfo) for t in times})
    result = {'requested_start': first.isoformat(), 'requested_end': last.isoformat(),
              'source_start': axis[0].isoformat() if axis else None,
              'source_end': axis[-1].isoformat() if axis else None,
              'expected_count': 0, 'available_count': 0, 'missing_times': [],
              'expected_times': [], 'available_times': [], 'native_step_seconds': None,
              'native_steps_seconds': [], 'ratio': None, 'complete': False, 'partial': True,
              'step_method': 'not_enough_native_times', 'irregular_intervals': [],
              'boundary_covered': bool(axis and axis[0] <= first and axis[-1] >= last),
              'note': 'Timestamp coverage only; finite grid values are checked separately. No interpolation or batch substitution.'}
    available = [t for t in axis if first <= t <= last]
    result['available_times'] = [t.isoformat() for t in available]
    result['available_count'] = len(available)
    if len(axis) < 2:
        result['expected_times'] = result['available_times']
        result['expected_count'] = len(available)
        return result
    deltas = [int(round((b-a).total_seconds())) for a, b in zip(axis, axis[1:])]
    minimum = min(deltas)
    # A change in resolution must repeat, unlike a lone missing-frame gap.
    stable = {minimum}
    for i, step in enumerate(deltas):
        if (i and step == deltas[i-1]) or (i+1 < len(deltas) and step == deltas[i+1]):
            stable.add(step)
    result['native_step_seconds'] = minimum
    result['native_steps_seconds'] = sorted(stable)
    result['step_method'] = 'coordinate_repeated_piecewise_cadence' if len(stable) > 1 else 'coordinate_minimum_native_cadence'
    result['cadence_inferred'] = True
    expected = set(axis)
    irregular = []
    for index, (a, b, step) in enumerate(zip(axis, axis[1:], deltas)):
        if step in stable:
            continue
        before = next((v for v in reversed(deltas[:index]) if v in stable), None)
        after = next((v for v in deltas[index+1:] if v in stable), None)
        local_step = before if before == after and before is not None else min(v for v in (before, after, minimum) if v is not None)
        if step % local_step:
            if a <= last and b >= first:
                irregular.append({'start': a.isoformat(), 'end': b.isoformat(), 'seconds': step})
            continue
        count = step // local_step
        if count > 100000:
            raise ValueError('原生时间轴间隔过大，无法可靠核对缺失时次')
        expected.update(a + timedelta(seconds=local_step*k) for k in range(1, count))
    # Outside the file period the nearest native cadence defines missing slots.
    before_step = deltas[0] if deltas[0] in stable else minimum
    after_step = deltas[-1] if deltas[-1] in stable else minimum
    for anchor, edge, step, sign in ((axis[0], first, before_step, -1), (axis[-1], last, after_step, 1)):
        seconds = (anchor-edge).total_seconds() if sign == -1 else (edge-anchor).total_seconds()
        count = max(0, math.floor(seconds / step))
        if count > 100000:
            raise ValueError('请求时间窗口过大，无法核对原生时次')
        expected.update(anchor + timedelta(seconds=sign*step*k) for k in range(1, count+1))
    expected = sorted(t for t in expected if first <= t <= last)
    missing = sorted(set(expected)-set(available))
    result.update(expected_times=[t.isoformat() for t in expected], expected_count=len(expected),
                  missing_times=[t.isoformat() for t in missing], irregular_intervals=irregular,
                  ratio=round(len(available)/len(expected), 6) if expected else None)
    result['complete'] = bool(result['boundary_covered'] and not missing and not irregular and expected)
    result['partial'] = not result['complete']
    return result


def adjacent_native_interval(first, last, coverage):
    """Whether two sampled timestamps can contribute a persistence interval."""
    a, b = _datetime(first), _datetime(last)
    step = int(round((b-a).total_seconds()))
    if step <= 0 or step not in coverage.get('native_steps_seconds', []):
        return False
    if any(a < _datetime(t) < b for t in coverage.get('missing_times', [])):
        return False
    return not any(a < _datetime(t['end']) and b > _datetime(t['start'])
                   for t in coverage.get('irregular_intervals', []))
