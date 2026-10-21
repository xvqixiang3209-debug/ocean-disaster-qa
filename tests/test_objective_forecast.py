"""Native NC subsets, provenance, masks and time contracts; no model calls."""
from pathlib import Path
import json
import warnings

from flask import Flask
import h5py
import numpy as np
import pytest
from scipy.io import netcdf_file

from forecast_runtime.objective import ObjectiveForecast, _source_meta
from forecast_runtime.objective_api import create_blueprint


BATCH = '2026-10-19T20:00:00+08:00'
NEXT = '2026-10-20T02:00:00+08:00'
BOX = [120, 29, 123, 32]


def hdf(path, variables, lon=None, lat=None, times=None, unit_overrides=None):
    lat = [32., 31., 30., 29.] if lat is None else lat
    lon = [120., 121., 122., 123.] if lon is None else lon
    times = [0, 6, 12] if times is None else times
    with h5py.File(path, 'w') as nc:
        nc['latitude'] = lat; nc['longitude'] = lon
        nc['time'] = times; nc['time'].attrs['units'] = 'hours since 2026-10-19 12:00:00'
        for key, values in variables.items():
            nc[key] = values
            nc[key].attrs['units'] = (unit_overrides or {}).get(key, 'Pa' if key == 'msl' else 'm s**-1')
            nc[key].attrs['_FillValue'] = -999.


def wave(path, values, lon=None, lat=None):
    lat = [32., 31., 30., 29.] if lat is None else lat
    lon = [120., 121., 122., 123.] if lon is None else lon
    with netcdf_file(path, 'w') as nc:
        for key, size in [('time', 3), ('record', 1), ('latitude', len(lat)), ('longitude', len(lon))]: nc.createDimension(key, size)
        nc.createVariable('latitude', 'd', ('latitude',))[:] = lat
        nc.createVariable('longitude', 'd', ('longitude',))[:] = lon
        t = nc.createVariable('time', 'i', ('time',)); t[:] = [0, 6, 12]
        t.units = 'hours since 2026-10-19 12:00:00'; t.calendar = 'gregorian'
        data = nc.createVariable('swh', 'd', ('time', 'record', 'latitude', 'longitude'))
        data[:] = values[:, None, :, :]; data.units = 'm'; data._FillValue = -32767.


@pytest.fixture
def engine(tmp_path):
    raw = tmp_path/'raw'; raw.mkdir()
    values = np.ones((3, 4, 4))
    waves = values.copy(); waves[:, 0, 0] = -32767; waves[:, 1, 1] = [2, 3, 4]
    u = values * 3; v = values * 4; u[:, 1, 1] = 12; v[:, 1, 1] = 5
    u[:, 3, 3] = -999
    hdf(raw/'atm_2026101912.nc', {'10u': u, '10v': v})
    hdf(raw/'msl_2026101912.nc', {'msl': values * 99900})
    wave(raw/'wave_2026101912.nc', waves)
    result = ObjectiveForecast(tmp_path/'index')
    with warnings.catch_warnings():
        warnings.simplefilter('error', RuntimeWarning)
        result.register_directory([raw])
    return result


def test_registration_references_sources_and_never_copies_raw(engine):
    cat = engine.catalogue(); d = cat['datasets'][0]
    assert set(d['fields']) == {'wave', 'wind', 'pressure'}
    assert d['batch'] == BATCH and d['batch_utc'] == '2026-10-19T12:00:00+00:00'
    assert not list(engine.root.glob('*.nc'))
    assert 'path' not in json.dumps(cat)
    for f in d['fields'].values(): assert len(f['source_file_id']) == 64
    assert engine.register_directory(engine.root.parent/'raw')['datasets'][0]['dataset_id'] == d['dataset_id']


@pytest.mark.parametrize(('field', 'value', 'unit'), [('wind', 13., 'm/s'), ('pressure', 999., 'hPa'), ('wave', 2., 'm')])
def test_frame_native_orientation_units_and_missing(field, value, unit, engine):
    frame = engine.frame(field=field, valid_time='2026-10-19T12:00:00Z', bounds=BOX)
    assert frame['lat'] == [32, 31, 30, 29] and frame['lon'] == [120, 121, 122, 123]
    assert frame['valid_time'] == BATCH and frame['unit'] == unit
    assert frame['values'][1][1] == value
    if field == 'wave': assert frame['values'][0][0] is None
    if field == 'wind':
        assert frame['values'][3][3] is None
        assert frame['u'][1][1] == 12 and frame['v'][1][1] == 5
    assert len(frame['color_scale']['colors']) == 32


def test_statistics_are_native_and_do_not_miss_a_peak_after_display_sampling(engine):
    frame = engine.frame(field='wind', bounds=BOX, maxsize=2)
    assert frame['shape'] == [2, 2] and frame['native_shape'] == [4, 4]
    assert frame['stats']['maximum']['value'] == 13
    assert max(v for row in frame['values'] for v in row if v is not None) == 5


def test_wave_sea_mask_excludes_land_from_native_wind_statistics(engine):
    stats = engine.stats72(start=BATCH, hours=6, bounds=BOX, sea_mask='wave_valid')
    wind = stats['fields']['wind']; pressure = stats['fields']['pressure']
    assert wind['native_count'] == 16 and wind['sea_count'] == 15
    assert wind['valid_count'] == 28 and wind['missing_count'] == 2
    assert pressure['minimum']['value'] == 999
    assert wind['sea_mask']['source']['sha256']
    assert stats['fields']['wave']['maximum']['value'] == 3


def test_requested_interval_is_not_confused_with_native_sample_start(engine):
    stats = engine.stats72(start='2026-10-19T20:31:00+08:00', hours=6, bounds=BOX)
    for f in stats['fields'].values():
        assert not f['partial']
        assert f['available_start'] == BATCH
        assert f['series_start'] == NEXT
        assert f['requested_start'] == '2026-10-19T20:31:00+08:00'
        assert f['frame_count'] == 1


def test_window_outside_available_period_is_partial_and_never_backfilled(engine):
    stats = engine.stats72(start='2026-10-21T00:00:00+08:00', hours=72, bounds=BOX)
    assert stats['state'] == 'no_data'
    assert all(f['partial'] and f['maximum'] is None for f in stats['fields'].values())


def test_no_borrowing_another_valid_time(engine):
    with pytest.raises(ValueError, match='原生预报时刻'):
        engine.frame(field='wave', valid_time='2026-10-19T21:00:00+08:00', bounds=BOX)


@pytest.mark.parametrize('bounds', [[1, 2, 3], [124, 28, 120, 30], [120, 32, 124, 29], [float('nan'), 29, 123, 32], [-181, 29, 123, 32], '1,2,no,4'])
def test_invalid_bounds_are_rejected(engine, bounds):
    with pytest.raises(ValueError): engine.frame(bounds=bounds)


def test_missing_variables_and_bad_units_do_not_become_zero(tmp_path):
    path = tmp_path/'empty_2026101912.nc'; hdf(path, {'temperature': np.ones((3, 4, 4))})
    with pytest.raises(ValueError, match='未包含'): _source_meta(path)
    hdf(path, {'10u': np.ones((3, 4, 4)), '10v': np.ones((3, 4, 4))}, unit_overrides={'10u': 'knots'})
    with pytest.raises(ValueError, match='单位不支持'): _source_meta(path)


def test_longitude_0_360_is_sorted_to_minus180_plus180(tmp_path):
    path = tmp_path/'wind_2026101912.nc'
    u = np.tile(np.array([0., 90., 180., 270.]), (3, 4, 1))
    hdf(path, {'10u': u, '10v': np.zeros_like(u)}, lon=[0, 90, 180, 270])
    e = ObjectiveForecast(tmp_path/'idx'); e.register_directory([path])
    frame = e.frame(field='wind', bounds=[-180, 29, 90, 32])
    assert frame['lon'] == [-180, -90, 0, 90]
    assert frame['u'][0] == [180, 270, 0, 90]


def test_regional_axis_ending_180_is_not_advertised_as_global(tmp_path):
    path = tmp_path/'wind_2026101912.nc'
    hdf(path, {'10u': np.ones((3, 4, 4)), '10v': np.zeros((3, 4, 4))}, lon=[30, 80, 130, 180])
    assert _source_meta(path)['coverage'] == [30, 29, 180, 32]


def test_point_nearest_is_source_bound_and_does_not_move_off_missing_cell(engine):
    good = engine.point(field='wind', valid_time=BATCH, lon=121.1, lat=30.9)
    assert good['value'] == 13 and good['grid_lon'] == 121 and good['grid_lat'] == 31
    missing = engine.point(field='wave', lon=120, lat=32)
    assert missing['state'] == 'missing_grid' and missing['value'] is None
    with pytest.raises(ValueError, match='超出'): engine.point(field='wave', lon=119.5, lat=31)


def test_png_and_api_use_same_engine_without_exposing_source_paths(engine):
    app = Flask(__name__); app.register_blueprint(create_blueprint(engine)); client = app.test_client()
    dataset = engine.catalogue()['datasets'][0]['dataset_id']
    query = {'dataset_id': dataset, 'field': 'wind', 'bounds': ','.join(map(str, BOX))}
    frame = client.get('/api/objective/frame', query_string=query)
    assert frame.status_code == 200 and frame.json['stats']['maximum']['value'] == 13
    image = client.get('/api/objective/image.png', query_string=query)
    assert image.status_code == 200 and image.data.startswith(b'\x89PNG\r\n\x1a\n')
    assert 'path' not in frame.get_data(as_text=True)
    assert client.get('/api/objective/frame?field=evil&bounds=120,29,123,32').status_code == 400
    assert client.get('/api/objective/point?field=wave&lon=120&lat=32').json['value'] is None
    assert client.get('/api/objective/frame?dataset_id=../../secret&bounds=120,29,123,32').status_code == 400


def test_source_size_change_is_detected(engine):
    source = Path(engine._index()['datasets'][0]['sources'][0]['path'])
    with source.open('ab') as stream: stream.write(b'x')
    with pytest.raises(ValueError, match='大小变化'): engine.catalogue()


def test_same_size_source_change_does_not_keep_using_an_old_hash(engine):
    import os
    source = Path(engine._index()['datasets'][0]['sources'][0]['path'])
    before = source.stat().st_mtime_ns
    os.utime(source, ns=(source.stat().st_atime_ns, before + 1000000000))
    with pytest.raises(ValueError, match='修改时间变化'): engine.catalogue()
