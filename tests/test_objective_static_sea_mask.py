"""P2: one full-source sea domain, independent per-time validity; no API calls."""
from datetime import datetime, timedelta
import json

import h5py
import numpy as np
import pytest
from scipy.io import netcdf_file

from forecast_runtime.objective import ObjectiveForecast, STATIC_SEA_MASK_METHOD
from forecast_runtime.objective_report import ObjectiveReport


BATCH = '2026-10-21T08:00:00+08:00'
BOX = [122., 29., 123., 30.]


def stamp(hour):
    return (datetime.fromisoformat(BATCH) + timedelta(hours=hour)).isoformat()


def make_engine(tmp_path, *, classic=False, waves=None):
    raw = tmp_path / 'raw'; raw.mkdir()
    if waves is None:
        waves = np.ones((13, 5, 5))
        waves[:, 1, 1] = -999.  # never valid (not a sea missing sample)
        waves[0, 2, 2] = -999.  # sea point missing only at the first timestamp
        waves[4, 2, 2] = 9.
        waves[6, 4, 4] = 4.
    shape = waves.shape
    wind_u = np.full(shape, 3.); wind_v = np.full(shape, 4.)
    wind_u[:, 1, 1] = wind_v[:, 1, 1] = -999.
    wind_u[9, 3, 3] = 12.; wind_v[9, 3, 3] = 16.
    for product, variables in [('WAV', {'WAV': waves}), ('SSW', {'u10': wind_u, 'v10': wind_v})]:
        path = raw / f'PZJ_BEN_OSM_ZJS_202610210800_{product}_00_L2.nc'
        lat, lon = np.linspace(29., 30., shape[1]), np.linspace(122., 123., shape[2])
        if classic:
            with netcdf_file(str(path), 'w') as nc:
                for name, size in [('time', shape[0]), ('lat', shape[1]), ('lon', shape[2])]:
                    nc.createDimension(name, size)
                for name, data in [('lat', lat), ('lon', lon), ('time', np.arange(shape[0]))]:
                    var = nc.createVariable(name, 'f8', (name,)); var[:] = data
                    if name == 'time': var.units = b'hours since initial time'
                for name, data in variables.items():
                    var = nc.createVariable(name, 'f8', ('time', 'lat', 'lon')); var[:] = data
                    var.unit = b'm' if product == 'WAV' else b'm/s'; var._FillValue = -999.
        else:
            with h5py.File(path, 'w') as nc:
                nc['lat'] = lat; nc['lon'] = lon; nc['time'] = np.arange(shape[0])
                nc['time'].attrs['units'] = 'hours since initial time'
                for name, data in variables.items():
                    nc[name] = data
                    nc[name].attrs['unit'] = 'm' if product == 'WAV' else 'm/s'
                    nc[name].attrs['_FillValue'] = -999.
    engine = ObjectiveForecast(tmp_path / 'index'); engine.register_directory(raw)
    return engine


@pytest.mark.parametrize('classic', [False, True])
def test_recovered_cell_peaks_and_missing_samples_agree_across_outputs(tmp_path, classic):
    engine = make_engine(tmp_path, classic=classic)
    summary = engine.stats72(start=BATCH, hours=12, bounds=BOX,
        thresholds={'wave': [{'value': 5, 'comparison': '>'}, {'value': 9, 'comparison': '>='},
                             {'value': 9, 'comparison': '>'}]})
    wave, wind = summary['fields']['wave'], summary['fields']['wind']
    assert wave['maximum'] == {'value': 9., 'lon': 122.5, 'lat': 29.5, 'time': stamp(4)}
    assert wave['sea_count'] == 24 and wave['never_valid_count'] == 1
    assert wave['missing_count'] == 1 and wave['series'][0]['missing_count'] == 1
    assert wave['valid_count'] == 24 * 13 - 1
    assert wave['intermittent_cell_count'] == 1 and wave['data_partial']
    assert [v['exceeding_sample_count'] for v in wave['process']['threshold_exceedances']] == [1, 1, 0]
    assert wave['sea_mask']['method'] == STATIC_SEA_MASK_METHOD
    assert len(wave['sea_mask']['source_sha256']) == 64
    assert wind['maximum']['value'] == 20. and wind['maximum']['time'] == stamp(9)
    assert wind['missing_count'] == wind['intermittent_cell_count'] == 0
    for maxsize in (2, 80, 600):
        maximum = engine.temporal_maximum(start=BATCH, hours=12, bounds=BOX, maxsize=maxsize)
        assert maximum['stats']['maximum'] == wave['maximum']
        assert maximum['stats']['sea_count'] == wave['sea_count']
        assert maximum['never_valid_count'] == 1 and maximum['intermittent_cell_count'] == 1
        assert maximum['sea_mask']['method'] == STATIC_SEA_MASK_METHOD
        frame = engine.frame(field='wave', valid_time=stamp(4), bounds=BOX, maxsize=maxsize, sea_mask='wave_valid')
        assert frame['stats']['maximum'] == wave['maximum']
    full = engine.temporal_maximum(start=BATCH, hours=12, bounds=BOX)
    assert full['values'][3][1] is None and full['valid_sample_counts'][3][1] == 0
    assert full['values'][2][2] == 9. and full['valid_sample_counts'][2][2] == 12
    # Wind remains independently valid at t0 even though wave is missing there.
    for hour in (0, 1, 4):
        frame = engine.frame(field='wind', valid_time=stamp(hour), bounds=BOX, sea_mask='wave_valid')
        assert frame['values'][2][2] == 5. and frame['values'][3][1] is None


def test_report_uses_correct_peak_snapshot_and_records_intermittent_gap(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    real_image = engine.image_png; calls = []
    def image(**kwargs):
        calls.append(kwargs.copy())
        return real_image(**kwargs)
    monkeypatch.setattr(engine, 'image_png', image)
    reports = ObjectiveReport(engine, tmp_path / 'reports', now=lambda: datetime.fromisoformat(stamp(1)))
    output = reports.report(start=BATCH, hours=12, bounds=BOX, requested_fields=['wave', 'wind'])
    result = output['objective_result']
    assert result['state'] == 'partial' and result['fields']['wave']['maximum']['value'] == 9.
    assert result['fields']['wave']['intermittent_cell_count'] == 1
    assert result['fields']['wave']['never_valid_count'] == 1
    assert result['algorithm_version'].endswith('-v4')
    assert any('1个海上格点-时次样本缺测（含时有时无的格点1个）' in text for text in result['missing_information'])
    assert {item['field']: item['valid_time'] for item in calls} == {'wave': stamp(4), 'wind': stamp(9)}
    assert all(item['sea_mask'] == 'wave_valid' for item in calls)
    assert output['artifacts']['wave_png']['filename'] == 'wave_peak.png'
    assert reports.report(start=BATCH, hours=12, bounds=BOX, requested_fields=['wave', 'wind'])['cache_hit']
    # The rollback algorithm must never reuse a static-mask product.
    monkeypatch.setenv('OBJECTIVE_SEA_MASK', 'first_frame')
    rollback = reports.report(start=BATCH, hours=12, bounds=BOX, requested_fields=['wave', 'wind'])
    assert not rollback['cache_hit'] and rollback['report_id'] != output['report_id']
    assert rollback['objective_result']['fields']['wave']['maximum']['value'] == 4.


def test_sea_point_valid_only_outside_window_counts_as_missing_not_land(tmp_path):
    waves = np.ones((13, 5, 5)); waves[:7, 2, 2] = -999.
    engine = make_engine(tmp_path, waves=waves)
    wave = engine.stats72(start=BATCH, hours=6, bounds=BOX)['fields']['wave']
    assert wave['sea_count'] == 25 and wave['never_valid_count'] == 0
    assert wave['missing_count'] == 7 and wave['intermittent_cell_count'] == 1
    maximum = engine.temporal_maximum(start=BATCH, hours=6, bounds=BOX)
    assert maximum['stats']['sea_count'] == 25 and maximum['stats']['missing_count'] == 1
    assert maximum['values'][2][2] is None and maximum['valid_sample_counts'][2][2] == 0


def test_all_window_missing_does_not_publish_zero_or_a_false_peak(tmp_path):
    waves = np.ones((13, 5, 5)); waves[:7] = -999.; waves[:, 1, 1] = -999.
    engine = make_engine(tmp_path, waves=waves)
    wave = engine.stats72(start=BATCH, hours=6, bounds=BOX, thresholds={'wave': 0})['fields']['wave']
    assert wave['state'] == 'no_data' and wave['maximum'] is None
    assert wave['missing_count'] == 24 * 7 and wave['never_valid_count'] == 1
    assert wave['intermittent_cell_count'] == 24
    maximum = engine.temporal_maximum(start=BATCH, hours=6, bounds=BOX)
    assert maximum['state'] == 'no_data' and maximum['stats']['maximum'] is None
    assert all(value is None for row in maximum['values'] for value in row)
    assert all(value == 0 for row in maximum['valid_sample_counts'] for value in row)


def test_cached_mask_is_source_and_axis_bound_reused_and_rebuilt_if_corrupt(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    first = engine.frame(valid_time=stamp(4), bounds=BOX, sea_mask='wave_valid')
    files = list((engine.root / 'index' / 'masks').glob('*.npy'))
    assert len(files) == 1
    metadata = json.loads(files[0].with_suffix('.json').read_text('utf-8'))
    assert metadata['identity']['axis_sha256'] == first['sea_mask']['axis_sha256']
    reopened = ObjectiveForecast(engine.root)
    original = h5py.Dataset.__getitem__; reads = []
    def read(dataset, selection):
        if dataset.name.endswith('/WAV'): reads.append(selection)
        return original(dataset, selection)
    monkeypatch.setattr(h5py.Dataset, '__getitem__', read)
    assert reopened.frame(valid_time=stamp(4), bounds=BOX, sea_mask='wave_valid')['stats']['maximum'] == first['stats']['maximum']
    assert len(reads) == 1  # requested frame only, no rescan of the source cube
    files[0].write_bytes(b'corrupt cache')
    repaired = ObjectiveForecast(engine.root)
    assert repaired.frame(valid_time=stamp(4), bounds=BOX, sea_mask='wave_valid')['stats']['maximum'] == first['stats']['maximum']
    assert np.load(files[0], allow_pickle=False).dtype == np.bool_


def test_mask_builder_streams_rows_and_uses_all_native_source_timestamps(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    original = h5py.Dataset.__getitem__; selections = []
    def read(dataset, key):
        if dataset.name.endswith('/WAV'): selections.append(key)
        return original(dataset, key)
    monkeypatch.setattr(h5py.Dataset, '__getitem__', read)
    mask = engine._static_sea_mask(engine._dataset(None))
    assert mask['values'].sum() == 24
    assert len(selections) == 13
    assert all(isinstance(key, tuple) and isinstance(key[0], int) and key[1].stop-key[1].start <= 256 for key in selections)


def test_static_wave_domain_maps_to_other_field_grid_and_clips_outside_coverage(tmp_path):
    engine = make_engine(tmp_path)
    native = {'values': np.ones((4, 4)), 'lat': np.array([30.25, 29.5, 29.25, 28.75]),
              'lon': np.array([121.75, 122.25, 122.5, 123.25])}
    mask, meta = engine._mask(engine._dataset(None), native, 'wind', [121.75, 28.75, 123.25, 30.25], 'wave_valid')
    assert mask[1, 2]  # recovered wave sea point, not excluded by first frame
    assert not mask[2, 1]  # permanently missing point
    assert not mask[0].any() and not mask[-1].any()
    assert not mask[:, 0].any() and not mask[:, -1].any()
    assert meta['never_valid_count'] == 13


def test_static_mask_applies_same_scale_fill_and_nonnegative_contract_as_native_reader(tmp_path):
    engine = make_engine(tmp_path)
    source = engine._source(engine._dataset(None), 'wave')
    with h5py.File(source['path'], 'r+') as nc:
        nc['WAV'].attrs['scale_factor'] = .5
        nc['WAV'].attrs['add_offset'] = -1.
    engine = ObjectiveForecast(tmp_path / 'scaled_index')
    engine.register_directory(tmp_path / 'raw')
    dataset = engine._dataset(None)
    # Raw 1 scales to -.5 and is invalid; raw 4 and 9 provide two sea cells.
    mask = engine._static_sea_mask(dataset)['values']
    assert mask.sum() == 2
    native_union = np.zeros(mask.shape, dtype=bool)
    for hour in range(13):
        native_union |= np.isfinite(engine._native(dataset, 'wave', stamp(hour), BOX)['values'])
    np.testing.assert_array_equal(mask, native_union)
