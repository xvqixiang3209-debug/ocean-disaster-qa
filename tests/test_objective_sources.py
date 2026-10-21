"""Source selection/acquisition tests: no network, model, or real credentials."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from forecast_runtime.objective_sources import ObjectiveSources
import forecast_runtime.objective_sources as sources_module
from forecast_runtime.storage import BEIJING


START = datetime(2026, 10, 21, tzinfo=BEIJING)
END = START + timedelta(hours=72)
AS_OF = START - timedelta(hours=1)
BOUNDS = (119.5, 26.5, 123.5, 31.5)


def dataset(identifier, *, batch=None, level='L5', family='zjocean_l2_l5',
            start=START, end=END, bounds=BOUNDS, fields=('wave', 'wind')):
    return {'dataset_id': identifier, 'source_family': family, 'level': level,
            'batch': (batch or START-timedelta(hours=4)).isoformat(),
            'fields': {name: {'available_start': start.isoformat(), 'available_end': end.isoformat(),
                              'valid_times': [(start+timedelta(hours=h)).isoformat() for h in range(int((end-start).total_seconds()/3600)+1)],
                              'coverage': list(bounds)} for name in fields}}


class FakeStore:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.saved = {}

    def grids(self):
        return deepcopy(self.rows)

    def state(self, key, value=None):
        if value is not None:
            self.saved[key] = deepcopy(value)
        return deepcopy(self.saved.get(key))


class FakeEngine:
    def __init__(self, datasets=()):
        self.datasets = list(datasets)
        self.registered = []

    def catalogue(self):
        return {'datasets': deepcopy(self.datasets)}

    def register_directory(self, paths):
        self.registered.append([str(path) for path in paths])
        return self.catalogue()


class FakeAcquisition:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {'state': 'ready', 'directory_state': 'ready', 'files': [],
                                 'local_complete': 2, 'local_partial': 0, 'local_missing': 0}

    def ensure(self, start, end, **kwargs):
        self.calls.append({'start': start, 'end': end, **kwargs})
        return deepcopy(self.result)


def controller(*datasets, rows=(), acquisition=None):
    runtime = SimpleNamespace(store=FakeStore(rows), objective=FakeEngine(datasets),
                              acquisition=acquisition or FakeAcquisition())
    return ObjectiveSources(runtime), runtime


def select(source, **kwargs):
    return source.select(START, END, as_of=AS_OF, **kwargs)


def test_default_selects_full_l5_over_partial_l2_and_foreign_ecmwf():
    l2 = dataset('l2', level='L2', bounds=(120, 27, 125, 31))
    l5 = dataset('l5')
    ecmwf = dataset('ecmwf', family='ecmwf', level=None, fields=('wave', 'wind', 'pressure'))
    source, _ = controller(ecmwf, l2, l5)
    assert select(source)['dataset_id'] == 'l5'


def test_full_coverage_outranks_newer_but_partial_run():
    old = dataset('full-old', batch=START-timedelta(hours=16))
    new = dataset('partial-new', batch=START-timedelta(hours=4), end=END-timedelta(hours=12))
    source, _ = controller(new, old)
    assert select(source)['dataset_id'] == 'full-old'


def test_explicit_latest_can_expose_latest_partial_instead_of_changing_time():
    old = dataset('full-old', batch=START-timedelta(hours=16))
    new = dataset('partial-new', batch=START-timedelta(hours=4), end=END-timedelta(hours=12))
    source, _ = controller(old, new)
    assert select(source, latest=True)['dataset_id'] == 'partial-new'


def test_same_batch_full_l5_preferred_independent_of_catalogue_order():
    l2, l5 = dataset('l2', level='L2'), dataset('l5')
    for values in ((l2, l5), (l5, l2)):
        source, _ = controller(*values)
        assert select(source)['dataset_id'] == 'l5'


def test_explicit_level_never_switches_to_l5_when_l2_is_partial():
    source, _ = controller(dataset('l5'), dataset('l2', level='L2', bounds=(120, 27, 125, 31)))
    assert select(source, level='L2')['dataset_id'] == 'l2'
    assert select(source, level='L7') is None


def test_no_future_issue_asof_or_target_start_leaks_into_historical_selection():
    historical = dataset('historical', batch=START-timedelta(hours=16))
    after_cutoff = dataset('after-cutoff', batch=START-timedelta(minutes=30))
    after_start = dataset('after-start', batch=START+timedelta(hours=1))
    source, _ = controller(historical, after_cutoff, after_start)
    assert select(source)['dataset_id'] == 'historical'
    assert source.select(START, END, as_of=START+timedelta(days=10))['dataset_id'] == 'after-cutoff'


def test_requires_both_requested_fields_in_one_dataset_not_two_partial_datasets():
    combined = dataset('combined', batch=START-timedelta(hours=16))
    wave = dataset('wave-only', fields=('wave',))
    wind = dataset('wind-only', fields=('wind',))
    source, _ = controller(wave, wind, combined)
    assert select(source)['dataset_id'] == 'combined'


def test_pressure_gap_keeps_l2_l5_family_and_best_real_wind_wave_coverage():
    l5 = dataset('l5-full', batch=START-timedelta(hours=16))
    l2 = dataset('l2-partial-new', level='L2', bounds=(120, 27, 125, 31))
    ecmwf = dataset('ecmwf', family='ecmwf', level=None, fields=('wave', 'wind', 'pressure'))
    source, _ = controller(ecmwf, l2, l5)
    selected = select(source, fields=('wave', 'wind', 'pressure'))
    assert selected['source_family'] == 'zjocean_l2_l5'
    assert selected['dataset_id'] == 'l5-full'
    assert 'pressure' not in selected['fields']


def test_only_ecmwf_available_returns_none_instead_of_silent_fallback():
    source, _ = controller(dataset('ecmwf', family='ecmwf', level=None))
    assert select(source) is None


def test_partial_overlap_preferred_over_completely_uncovered_time():
    partial = dataset('overlap', batch=START-timedelta(hours=16), end=START+timedelta(hours=12))
    expired = dataset('expired', end=START-timedelta(hours=1), start=START-timedelta(hours=3))
    source, _ = controller(expired, partial)
    assert select(source)['dataset_id'] == 'overlap'


def grid_row(tmp_path, product, batch, level, suffix=''):
    filename = f'PZJ_BEN_OSM_ZJS_{batch}_{product}_00_{level}.nc'
    path = tmp_path / (suffix + filename)
    path.write_bytes(b'not-real-nc-for-registration-test')
    return {'id': filename, 'filename': filename, 'batch': batch, 'product': product,
            'level': level, 'path': str(path)}


def test_register_groups_only_wave_wind_with_matching_batch_and_level(tmp_path):
    rows = [grid_row(tmp_path, product, batch, level)
            for batch, level in [('202610202000', 'L2'), ('202610202000', 'L5'), ('202610200800', 'L5')]
            for product in ('WAV', 'SSW')]
    rows += [grid_row(tmp_path, 'SWL', '202610202000', 'L5'),
             grid_row(tmp_path, 'SST', '202610202000', 'L5')]
    source, runtime = controller(rows=rows)
    source.register_local()
    assert len(runtime.objective.registered) == 3
    for group in runtime.objective.registered:
        assert len(group) == 2
        names = [Path(path).name for path in group]
        assert any('_WAV_' in name for name in names) and any('_SSW_' in name for name in names)
        assert len({name.split('_')[4] for name in names}) == 1
        assert len({name[-5:-3] for name in names}) == 1


def test_register_local_infers_level_from_filename_and_skips_missing_files(tmp_path):
    row = grid_row(tmp_path, 'WAV', '202610202000', 'L5')
    row.pop('level')
    missing = {**row, 'path': str(tmp_path/'does-not-exist.nc')}
    unsupported = {**row, 'filename': 'arbitrary.nc', 'level': None}
    source, runtime = controller(rows=[row, missing, unsupported])
    source.register_local()
    assert runtime.objective.registered == [[row['path']]]


def test_unchanged_sources_not_rehashed_but_modified_pair_is_reregistered(tmp_path):
    rows = [grid_row(tmp_path, product, '202610202000', 'L5') for product in ('WAV', 'SSW')]
    source, runtime = controller(rows=rows)
    source.register_local()
    source.register_local()
    assert len(runtime.objective.registered) == 1
    Path(rows[0]['path']).write_bytes(b'changed-with-different-size')
    source.register_local()
    assert len(runtime.objective.registered) == 2


def test_ensure_acquires_only_wave_wind_with_exact_window_cutoff_and_refresh(tmp_path):
    rows = [grid_row(tmp_path, product, '202610202000', 'L5') for product in ('WAV', 'SSW')]
    source, runtime = controller(rows=rows)
    result = source.ensure(START, END, as_of=AS_OF, refresh=True)
    assert runtime.acquisition.calls == [{'start': START, 'end': END, 'products': ['WAV', 'SSW'],
                                          'as_of': AS_OF, 'refresh': True}]
    assert len(runtime.objective.registered) == 1
    assert result['state'] == 'ready'
    assert runtime.store.saved['objective_acquisition'] == result


def test_acquisition_status_does_not_publish_credentials_or_local_paths(monkeypatch):
    acquisition = FakeAcquisition({'state': 'partial', 'directory_state': 'ready',
        'token': 'DEMO_SECRET', 'local_path': 'E:\\private\\raw.nc',
        'files': [{'filename': 'wave.nc', 'state': 'ready', 'path': 'E:\\private\\raw.nc',
                   'token': 'DEMO_SECRET', 'url': 'https://example.invalid/nc?access_key=DEMO_SECRET'}]})
    source, runtime = controller(acquisition=acquisition)
    source.ensure(START, END, as_of=AS_OF)
    monkeypatch.setenv('MARINE_OBJECTIVE_AUTO_SYNC', 'true')
    status = source.status()
    text = json.dumps(status)
    assert 'DEMO_SECRET' not in text and 'private' not in text and 'access_key' not in text
    assert status['products'] == ['WAV', 'SSW'] and status['enabled'] is True
    assert status['last_check']['files'][0]['filename'] == 'wave.nc'


def test_recent_directory_cache_does_not_skip_explicit_historical_asof(monkeypatch):
    now = START + timedelta(hours=6)
    monkeypatch.setattr(sources_module, 'local_now', lambda: now)
    source, runtime = controller()
    runtime.store.state('objective_acquisition', {'checked_at': now.isoformat(), 'state': 'ready',
        'requested_start': START.isoformat(), 'requested_end': END.isoformat(), 'files': []})
    historical_start = START - timedelta(days=3)
    historical_end = historical_start + timedelta(hours=72)
    historical_asof = historical_start - timedelta(hours=1)
    source.ensure(historical_start, historical_end, as_of=historical_asof)
    assert runtime.acquisition.calls == [{'start': historical_start, 'end': historical_end,
        'products': ['WAV', 'SSW'], 'as_of': historical_asof, 'refresh': False}]


def test_cached_directory_status_cannot_select_future_batch_for_historical_target(monkeypatch):
    now = START + timedelta(hours=6)
    monkeypatch.setattr(sources_module, 'local_now', lambda: now)
    old = dataset('old', batch=START-timedelta(hours=16))
    new = dataset('future-relative-to-target', batch=START+timedelta(hours=1))
    source, runtime = controller(old, new)
    runtime.store.state('objective_acquisition', {'checked_at': now.isoformat(), 'state': 'ready', 'files': [],
        'requested_start':START.isoformat(),'requested_end':END.isoformat()})
    result = source.ensure(START, END)
    assert result['cache_hit'] is True and not runtime.acquisition.calls
    assert source.select(START, END)['dataset_id'] == 'old'


def test_cached_acquisition_window_cannot_be_relabelled_as_a_new_requested_period(monkeypatch):
    now=START+timedelta(hours=6)
    monkeypatch.setattr(sources_module,'local_now',lambda:now)
    source,runtime=controller()
    runtime.store.state('objective_acquisition',{'checked_at':now.isoformat(),'state':'ready','files':[],
        'requested_start':START.isoformat(),'requested_end':END.isoformat()})
    changed=START+timedelta(days=1)
    source.ensure(changed,changed+timedelta(hours=24))
    assert len(runtime.acquisition.calls)==1
    assert runtime.acquisition.calls[0]['start']==changed
    assert runtime.acquisition.calls[0]['end']==changed+timedelta(hours=24)


def test_refresh_bypasses_recent_directory_cache(monkeypatch):
    now = START + timedelta(hours=6)
    monkeypatch.setattr(sources_module, 'local_now', lambda: now)
    source, runtime = controller()
    runtime.store.state('objective_acquisition', {'checked_at': now.isoformat(), 'state': 'ready', 'files': []})
    source.ensure(START, END, refresh=True)
    assert len(runtime.acquisition.calls) == 1 and runtime.acquisition.calls[0]['refresh'] is True


@pytest.mark.parametrize('value,expected', [('10', 300), ('900', 900), ('bad', 900)])
def test_sync_interval_has_bounded_minimum_and_invalid_value_fallback(monkeypatch, value, expected):
    monkeypatch.setenv('MARINE_OBJECTIVE_SYNC_SECONDS', value)
    assert ObjectiveSources.interval() == expected
