"""Native cadence, missing-data persistence and per-cell maxima regressions."""
from datetime import datetime, timedelta

import h5py
import numpy as np
import pytest

from forecast_runtime.objective import ObjectiveForecast
from forecast_runtime.objective_time import temporal_coverage


BATCH = '2026-10-21T08:00:00+08:00'
BOX = [122., 29., 123., 30.]


def stamps(offsets):
    first = datetime.fromisoformat(BATCH)
    return [(first+timedelta(hours=n)).isoformat() for n in offsets]


def make_engine(tmp_path, offsets=(0, 3, 9, 12), waves=None, winds=None):
    raw = tmp_path/'raw'; raw.mkdir()
    shape = (len(offsets), 2, 2) if waves is None else waves.shape
    for product in ('WAV', 'SSW'):
        path = raw/f'PZJ_BEN_OSM_ZJS_202610210800_{product}_00_L2.nc'
        with h5py.File(path, 'w') as nc:
            nc['lat'] = np.linspace(29., 30., shape[-2]); nc['lon'] = np.linspace(122., 123., shape[-1])
            nc['time'] = offsets; nc['time'].attrs['units'] = 'hours since initial time'
            for variable, values in ({'WAV': np.full(shape, 2.) if waves is None else waves} if product == 'WAV'
                    else {'u10': np.full(shape, 3.) if winds is None else winds, 'v10': np.full(shape, 4.)}).items():
                nc[variable] = values
                nc[variable].attrs['unit'] = 'm' if product == 'WAV' else 'm/s'
                nc[variable].attrs['_FillValue'] = -999.
    engine = ObjectiveForecast(tmp_path/'index'); engine.register_directory(raw)
    return engine


def test_three_hour_product_finds_middle_hole_and_keeps_off_grid_boundaries_native():
    times = stamps([0, 3, 9, 12])
    coverage = temporal_coverage(times, stamps([0])[0], stamps([12])[0])
    assert coverage['native_step_seconds'] == 10800
    assert coverage['missing_times'] == stamps([6])
    assert coverage['expected_count'] == 5 and coverage['available_count'] == 4
    assert coverage['ratio'] == .8 and not coverage['complete']
    subset = temporal_coverage(stamps([0, 3, 6, 9, 12]), stamps([.5])[0], stamps([5.5])[0])
    assert subset['complete'] and subset['expected_times'] == stamps([3])


def test_repeated_resolution_change_is_not_falsely_hourly():
    times = stamps([0, 1, 2, 3, 6, 9, 12])
    coverage = temporal_coverage(times, times[0], times[-1])
    assert coverage['native_steps_seconds'] == [3600, 10800]
    assert coverage['expected_count'] == 7 and coverage['complete']
    assert coverage['step_method'] == 'coordinate_repeated_piecewise_cadence'


def test_gap_in_later_three_hour_segment_does_not_expect_hourly_samples():
    times = stamps([0, 1, 2, 3, 6, 9, 15, 18, 21])
    coverage = temporal_coverage(times, times[0], times[-1])
    assert coverage['missing_times'] == stamps([12])
    assert coverage['expected_count'] == 10 and coverage['available_count'] == 9


def test_window_outside_source_and_single_time_remain_partial():
    coverage = temporal_coverage(stamps([0, 3, 6]), stamps([-3])[0], stamps([9])[0])
    assert not coverage['complete'] and coverage['missing_times'] == stamps([-3, 9])
    one = temporal_coverage(stamps([0]), stamps([0])[0], stamps([1])[0])
    assert one['partial'] and one['native_step_seconds'] is None and one['ratio'] is None


def test_process_threshold_durations_never_bridge_missing_three_hour_sample(tmp_path):
    engine = make_engine(tmp_path)
    stats = engine.stats72(start=BATCH, hours=12, bounds=BOX,
                           thresholds={'wave': {'value': 2, 'unit': 'm', 'metadata': {'source': 'test user'}}})
    wave = stats['fields']['wave']
    assert wave['partial'] and wave['temporal_coverage']['missing_times'] == stamps([6])
    process = wave['process']['threshold_exceedances'][0]
    assert process['duration_hours'] == 6
    assert [(item['start'], item['end']) for item in process['intervals']] == [(stamps([0])[0], stamps([3])[0]), (stamps([9])[0], stamps([12])[0])]
    assert process['maximum_fraction'] == 1 and process['exceeding_sample_count'] == 4
    assert not process['official_warning'] and process['metadata']['source'] == 'test user'
    assert 'same-cell' in process['duration_interpretation']
    assert engine.stats72(start=BATCH, hours=12, bounds=BOX)['fields']['wave']['process']['threshold_exceedances'] == []


def test_all_missing_data_has_no_zero_peak_or_exceedance(tmp_path):
    engine = make_engine(tmp_path, offsets=[0, 3, 6], waves=np.full((3, 2, 2), -999.))
    stats = engine.stats72(start=BATCH, hours=6, bounds=BOX, sea_mask=None, thresholds={'wave': 2})
    wave = stats['fields']['wave']
    assert wave['state'] == 'no_data' and wave['maximum'] is None
    assert wave['process']['nonempty_sample_count'] == 0
    exceed = wave['process']['threshold_exceedances'][0]
    assert exceed['maximum_fraction'] is None and exceed['first'] is None
    assert all(item['any_exceedance'] is None for item in exceed['samples'])
    maximum = engine.temporal_maximum(field='wave', start=BATCH, hours=6, bounds=BOX, sea_mask=None)
    assert maximum['state'] == 'no_data' and maximum['values'] == [[None, None], [None, None]]
    assert maximum['valid_sample_counts'] == [[0, 0], [0, 0]]


def test_period_maximum_reduces_each_native_cell_not_one_peak_snapshot(tmp_path):
    waves = np.array([[[9., 1.], [2., -999.]], [[3., 8.], [4., -999.]], [[1., 2.], [7., 5.]]])
    engine = make_engine(tmp_path, offsets=[0, 3, 6], waves=waves)
    maximum = engine.temporal_maximum(field='wave', start=BATCH, hours=6, bounds=BOX)
    expected = np.array([[7., 5.], [9., 8.]])  # north-to-south orientation
    np.testing.assert_allclose(maximum['values'], expected)
    assert maximum['valid_sample_counts'] == [[3, 1], [3, 3]]
    assert maximum['expected_sample_count'] == 3 and maximum['valid_sample_ratios'] == [[1., .333333], [1., 1.]]
    assert maximum['product_type'] == 'temporal_maximum' and maximum['valid_time'] is None
    assert maximum['stats']['maximum']['value'] == 9
    assert maximum['stats']['maximum']['time'] == BATCH
    assert maximum['stats']['minimum']['value'] == 5
    assert maximum['stats']['minimum']['time'] == stamps([6])[0]
    wind = engine.temporal_maximum(field='wind', start=BATCH, hours=6, bounds=BOX)
    assert wind['vectors_omitted'] and 'u' not in wind and 'v' not in wind
    pressure = engine.temporal_maximum(field='pressure', start=BATCH, hours=6, bounds=BOX)
    assert pressure['code'] == 'field_not_provided' and pressure['values'] == []
    assert engine.image_temporal_maximum_png(field='wave', start=BATCH, hours=6, bounds=BOX).startswith(b'\x89PNG')


def test_maximum_reads_one_time_rectangle_per_iteration(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, offsets=[0, 3, 6])
    original = h5py.Dataset.__getitem__
    selections = []
    def read(dataset, key):
        if dataset.name.endswith('/WAV'):
            selections.append(key)
        return original(dataset, key)
    monkeypatch.setattr(h5py.Dataset, '__getitem__', read)
    maximum = engine.temporal_maximum(field='wave', start=BATCH, hours=6, bounds=BOX, sea_mask=None)
    assert maximum['frame_count'] == 3
    assert len(selections) == 3
    assert all(isinstance(key, tuple) and isinstance(key[0], int) for key in selections)


@pytest.mark.parametrize('threshold', [-1, float('nan'), True, {'value': 2, 'unit': 'cm'}, {'value': 2, 'comparison': '<'}])
def test_threshold_contract_rejects_ambiguous_or_bad_units(tmp_path, threshold):
    engine = make_engine(tmp_path)
    with pytest.raises(ValueError):
        engine.stats72(start=BATCH, hours=12, bounds=BOX, thresholds={'wave': threshold})


def test_per_cell_maximum_precedes_display_decimation(tmp_path):
    waves = np.ones((3, 5, 5)); waves[1, 2, 2] = 19.; waves[2, 1, 1] = 11.
    engine = make_engine(tmp_path, offsets=[0, 3, 6], waves=waves)
    dataset = engine._dataset(None)
    cells = np.stack([engine._native(dataset, 'wave', t, BOX)['values'] for t in stamps([0, 3, 6])])
    result = engine.temporal_maximum(field='wave', start=BATCH, hours=6, bounds=BOX, sea_mask=None, maxsize=2)
    np.testing.assert_allclose(result['values'], np.max(cells, axis=0)[np.ix_([0, 4], [0, 4])])
    assert result['stats']['maximum']['value'] == 19.
    assert max(v for row in result['values'] for v in row) == 1.
    assert result['native_shape'] == [5, 5] and result['shape'] == [2, 2]
    assert result['statistics_resolution'] == 'native_grid_temporal_reduction_before_display_sampling'


def test_missing_frame_interrupts_threshold_duration_without_becoming_calm(tmp_path):
    waves = np.full((5, 2, 2), 3.); waves[2] = -999.
    engine = make_engine(tmp_path, offsets=[0, 3, 6, 9, 12], waves=waves)
    report = engine.stats72(start=BATCH, hours=12, bounds=BOX, sea_mask=None, thresholds={'wave': 2})
    wave = report['fields']['wave']
    assert wave['temporal_coverage']['complete']  # coordinates, independently of finite data
    assert wave['data_partial'] and wave['process']['nonempty_sample_count'] == 4
    sample = wave['process']['threshold_exceedances'][0]
    assert sample['duration_hours'] == 6 and len(sample['intervals']) == 2
    assert sample['samples'][2]['any_exceedance'] is None
    assert sample['samples'][2]['fraction'] is None
