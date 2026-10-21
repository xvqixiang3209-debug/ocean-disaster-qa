"""Legacy download products reuse the objective map without mixing domains."""
import json

from flask import Flask
import h5py
import numpy as np
import pytest

from forecast_runtime.objective import ObjectiveForecast
from forecast_runtime.objective_api import create_blueprint


BATCH = '2026-10-20T20:00:00+08:00'
VALID = '2026-10-20T21:00:00+08:00'
BOX = [120, 29, 120.1, 29.1]


def legacy(path, *, wind=False, offsets=(1, 2, 3), initial=True, fill=True):
    with h5py.File(path, 'w') as nc:
        nc['lon'] = [120, 120.05, 120.1]
        nc['lat'] = [29, 29.05, 29.1]
        nc['time'] = offsets
        nc['time'].attrs['units'] = ('hours since initial time' if initial else 'hours since 2026-10-20 20:00:00')
        shape = (len(offsets), 3, 3)
        for key, value in ({'u10': 3., 'v10': 4.} if wind else {'WAV': 2., 'MWD': 270.}).items():
            data = np.full(shape, value)
            data[:, 0, 0] = -999
            data[-1, 1, 1] = 12 if wind and key == 'u10' else value
            nc[key] = data
            nc[key].attrs['unit'] = 'm/s' if wind else 'm'
            nc[key].attrs['descript'] = 'Wind guest above 10m ground' if wind else 'Maximum significant wave height'
            if fill: nc[key].attrs['_FillValue'] = -999.


def paths(root, level='L2'):
    postfix = '00' if level == 'L2' else '20'
    prefix = 'PZJ_BEN_OSM_ZJS_202610202000_' + ('' if level == 'L2' else '144001_')
    return [root / f'{prefix}{product}_{postfix}_{level}.nc' for product in ('WAV', 'SSW')]


@pytest.fixture
def engine(tmp_path):
    raw = tmp_path / 'raw'; raw.mkdir()
    for level in ('L2', 'L5'):
        wave, wind = paths(raw, level)
        legacy(wave, initial=level == 'L2', fill=level == 'L2')
        legacy(wind, wind=True, initial=False, fill=False)
    result = ObjectiveForecast(tmp_path / 'objective')
    result.register_directory(raw)
    return result


def test_levels_with_same_batch_remain_independent_datasets(engine):
    cat = engine.catalogue()
    assert len(cat['datasets']) == 2
    assert {d['level'] for d in cat['datasets']} == {'L2', 'L5'}
    for d in cat['datasets']:
        assert d['dataset_id'].startswith('zjocean_' + d['level'].lower())
        assert d['source_family'] == 'zjocean_l2_l5'
        assert set(d['fields']) == {'wave', 'wind'}
        assert d['unavailable_fields'] == ['pressure']
        assert d['batch'] == BATCH and d['batch_utc'] == '2026-10-20T12:00:00+00:00'
        assert d['fields']['wave']['valid_times'][0] == VALID
        assert d['fields']['wave']['grid_spacing_degrees'] == {'longitude': .05, 'latitude': .05}
        assert d['fields']['wind']['wind_kind'] == 'provisional_gust_10m'
        assert 'not ECMWF mean' in d['profile']['wind']
        assert 'path' not in json.dumps(d)


def test_first_forecast_hour_is_not_misread_as_batch_or_utc(engine):
    d = engine.catalogue()['datasets'][0]
    frame = engine.frame(d['dataset_id'], 'wave', VALID, BOX)
    assert frame['valid_time'] == VALID and frame['batch'] == BATCH
    assert frame['lat'] == [29.1, 29.05, 29.]
    assert frame['values'][2][0] is None
    assert frame['stats']['maximum']['value'] == 2
    with pytest.raises(ValueError, match='原生预报时刻'):
        engine.frame(d['dataset_id'], 'wave', BATCH, BOX)


def test_sentinel_values_without_attributes_are_missing_not_1400ms_winds(engine):
    d = engine.catalogue()['datasets'][0]
    frame = engine.frame(d['dataset_id'], 'wind', VALID, BOX)
    assert frame['values'][2][0] is None
    assert frame['stats']['maximum']['value'] == 5
    assert frame['wind_kind'] == 'provisional_gust_10m'
    assert frame['u'][0][0] == 3 and frame['v'][0][0] == 4


def test_missing_pressure_is_explicit_and_never_borrowed(engine):
    d = engine.catalogue()['datasets'][0]['dataset_id']
    f = engine.frame(d, 'pressure', VALID, BOX)
    p = engine.point(d, 'pressure', VALID, lon=120.05, lat=29.05)
    for item in (f, p):
        assert item['state'] == 'no_data'
        assert item['code'] == 'field_not_provided'
        assert item['source_files'] == []
        assert '不从其他批次或产品补造' in item['reason']
    assert f['values'] == [] and p['value'] is None
    stats = engine.stats72(d, start=VALID, hours=2, bounds=BOX)
    assert stats['state'] == 'ready'
    assert stats['fields']['pressure']['state'] == 'no_data'
    assert stats['fields']['pressure']['maximum'] is None
    assert stats['fields']['wind']['maximum']['value'] == pytest.approx(12.649111)


def test_spatial_and_temporal_coverage_are_separate(engine):
    stats = engine.stats72(start=VALID, hours=2, bounds=[119.5, 28.5, 120.1, 29.1])
    for field in ('wave', 'wind'):
        value = stats['fields'][field]
        assert not value['partial']  # all requested times exist
        assert value['spatial_partial']
        assert value['coverage_bounds'] == BOX
        assert value['requested_bounds'] == [119.5, 28.5, 120.1, 29.1]


def test_legacy_product_folder_skips_other_elements_without_reinterpreting(tmp_path):
    wave, wind = paths(tmp_path)
    legacy(wave); legacy(wind, wind=True)
    unsupported = tmp_path / 'PZJ_BEN_OSM_ZJS_202610202000_SWL_00_L2.nc'
    unsupported.write_bytes(b'not read by objective adapter')
    e = ObjectiveForecast(tmp_path / 'index')
    cat = e.register_directory(tmp_path)
    assert len(cat['datasets']) == 1 and set(cat['datasets'][0]['fields']) == {'wave', 'wind'}


def test_native_read_uses_single_time_rectangle_not_full_data_cube(engine, monkeypatch):
    original = h5py.Dataset.__getitem__
    selections = []
    def read(dataset, key):
        if dataset.name.rsplit('/', 1)[-1] in {'WAV', 'u10', 'v10', 'MWD'}:
            selections.append((dataset.name, key))
        return original(dataset, key)
    monkeypatch.setattr(h5py.Dataset, '__getitem__', read)
    engine.frame(field='wind', valid_time=VALID, bounds=BOX)
    assert len(selections) == 2
    assert all(isinstance(key, tuple) and isinstance(key[0], int) for _, key in selections)
    assert all('MWD' not in name for name, _ in selections)


def test_missing_field_api_is_200_no_data_and_png_is_labeled(engine):
    app = Flask(__name__); app.register_blueprint(create_blueprint(engine)); client = app.test_client()
    query = {'dataset_id': engine.catalogue()['datasets'][0]['dataset_id'], 'field': 'pressure'}
    response = client.get('/api/objective/frame', query_string=query)
    assert response.status_code == 200 and response.json['code'] == 'field_not_provided'
    image = client.get('/api/objective/image.png', query_string=query)
    assert image.status_code == 200 and image.data.startswith(b'\x89PNG\r\n\x1a\n')
    actual = engine.image_png(field='wind', valid_time=VALID, bounds=BOX)
    assert actual.startswith(b'\x89PNG\r\n\x1a\n')

