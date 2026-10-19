"""Bounded query-time acquisition tests: synthetic catalogs, no live network."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from threading import Lock
import time
import unittest
from unittest.mock import Mock, patch

from forecast_runtime.acquisition import GridAcquisition, PREFIXES
from forecast_runtime.storage import BEIJING


NOW = datetime(2026, 10, 17, 15, tzinfo=BEIJING)
START = NOW.replace(hour=0)
END = NOW.replace(hour=23)
CREDENTIALS = {'ZJOCEAN_GRID_ID': 'test-identity', 'ZJOCEAN_GRID_CODE': 'test-secret-code',
               'ZJOCEAN_GRID_SALT': 'test-secret-salt'}


def filename(batch, product='WAV', level='L2'):
    return f'PZJ_BEN_OSM_ZJS_{batch}_{product}_00_{level}.nc'


def url(batch, product='WAV', level='L2'):
    return 'https://test.aliyuncs.com/' + filename(batch, product, level) + '?Signature=never-expose-me'


def grid(batch='202610162000', product='WAV', level='L2', **changes):
    issue = datetime.strptime(batch, '%Y%m%d%H%M').replace(tzinfo=BEIJING)
    times = [(issue + timedelta(hours=index)).isoformat() for index in range(1, 169)]
    record = {'id': filename(batch, product, level), 'filename': filename(batch, product, level),
              'product': product, 'level': level, 'batch': issue.isoformat(),
              'valid_from': times[0], 'valid_to': times[-1], 'times': times,
              'variables': [{'name': product, 'unit': 'm'}]}
    record.update(changes)
    return record


class MemoryStore:
    def __init__(self, records=()):
        self.records = list(records)
        self.states = {}

    def grids(self):
        return deepcopy(self.records)

    def state(self, key, value=None):
        if value is not None:
            self.states[key] = deepcopy(value)
        return deepcopy(self.states.get(key))


class FakeSync:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.error = False
        self.missing_variable = False
        self.active = 0
        self.max_active = 0
        self.lock = Lock()

    def download(self, prefix, signed_url):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(.002)
            name = signed_url.split('?')[0].rsplit('/', 1)[-1]
            self.calls.append((prefix, name))
            if self.error:
                raise RuntimeError('secret signed URL: ' + signed_url)
            parts = name[:-3].split('_')
            record = grid(parts[4], parts[5], parts[-1])
            if self.missing_variable:
                record['variables'] = []
            self.store.records.append(record)
            return {'filename': name, 'state': 'downloaded', 'bytes': 123,
                    'url': signed_url, 'private': 'must-not-be-copied'}
        finally:
            with self.lock:
                self.active -= 1


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.sync = FakeSync(self.store)
        self.acquisition = GridAcquisition(self.store, self.sync, lambda: {'download_products': ['WAV']})
        self.env = patch.dict('os.environ', CREDENTIALS)
        self.env.start(); self.addCleanup(self.env.stop)
        self.clock = patch('forecast_runtime.acquisition.local_now', return_value=NOW)
        self.clock.start(); self.addCleanup(self.clock.stop)

    @staticmethod
    def catalog(products=('WAV',), batches=('202610162000', '202610170800', '202610172000')):
        def response(*args, **kwargs):
            level = kwargs['params']['prefix'][-3:-1]
            result = Mock()
            result.json.return_value = {'files': [url(batch, product, level)
                                                 for product in products for batch in batches]}
            return result
        return response

    def test_selects_issued_latest_and_before_start_not_future_latest(self):
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog()) as get:
            result = self.acquisition.ensure(START, END)
        self.assertEqual(get.call_count, 2)
        names = [name for _, name in self.sync.calls]
        self.assertEqual(len(names), 4)
        self.assertTrue(any('202610162000' in name for name in names))
        self.assertTrue(any('202610170800' in name for name in names))
        self.assertFalse(any('202610172000' in name for name in names))
        self.assertEqual(result['local_complete'], 1)
        self.assertEqual(result['state'], 'ready')
        self.assertFalse(result['point_values_verified'])

    def test_downloads_are_bounded_to_twenty_files_and_two_workers(self):
        products = ['SWL', 'WAV', 'SSW', 'OCU', 'SST']
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog(products)):
            result = self.acquisition.ensure(START, END, products=products)
        self.assertEqual(len(result['files']), 20)
        self.assertEqual(result['local_complete'], 5)
        self.assertLessEqual(self.sync.max_active, 2)
        for prefix in PREFIXES:
            self.assertEqual(sum(item['prefix'] == prefix for item in result['files']), 10)

    def test_explicit_cutoff_never_uses_later_nominal_issue(self):
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog()):
            result = self.acquisition.ensure(START, END, as_of=NOW.replace(hour=7))
        self.assertEqual(len(result['files']), 2)
        self.assertTrue(all('202610162000' in item['filename'] for item in result['files']))
        self.store.records.append(grid('202610170800'))
        coverage = self.acquisition._coverage(START.replace(hour=9), END, ['WAV'], NOW.replace(hour=7))
        self.assertTrue(all(item['batch'] <= '2026-10-17T07:00:00+08:00'
                            for item in coverage['coverage']['WAV']['files']))

    def test_local_data_remains_available_without_credentials(self):
        self.store.records.append(grid())
        with patch.dict('os.environ', {key: '' for key in CREDENTIALS}):
            with patch('forecast_runtime.acquisition.requests.get') as get:
                result = self.acquisition.ensure(START, END)
        get.assert_not_called()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['directory_state'], 'credentials_missing')
        self.assertEqual(result['local_complete'], 1)

    def test_no_directory_archive_does_not_discard_complete_local_run(self):
        self.store.records.append(grid())
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog(batches=('202610170800',))):
            result = self.acquisition.ensure(START, END)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(len(result['directory_missing']), 2)
        self.assertTrue(all(item['reason'] == 'no_batch_at_or_before_start' for item in result['directory_missing']))

    def test_cached_catalog_rechecks_live_local_coverage_and_refresh_bypasses(self):
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog()) as get:
            first = self.acquisition.ensure(START, END)
            self.store.records.clear()
            cached = self.acquisition.ensure(START, END)
            refreshed = self.acquisition.ensure(START, END, refresh=True)
        self.assertFalse(first['cache_hit'])
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(cached['local_missing'], 1)
        self.assertFalse(refreshed['cache_hit'])
        self.assertEqual(refreshed['local_complete'], 1)
        self.assertEqual(get.call_count, 4)

    def test_invalid_directory_and_request_errors_do_not_expose_secrets(self):
        invalid = Mock(); invalid.json.return_value = {'files': [{'url': 'private'}]}
        with patch('forecast_runtime.acquisition.requests.get', side_effect=[invalid, RuntimeError('never-expose-me test-secret-code')]):
            result = self.acquisition.ensure(START, END)
        self.assertEqual(result['directory_state'], 'unavailable')
        self.assertEqual(len(result['directory_errors']), 2)
        serialized = json.dumps([result, self.store.states])
        for private in ('never-expose-me', 'test-secret-code', 'test-secret-salt', 'Signature=', 'https://'):
            self.assertNotIn(private, serialized)

    def test_download_errors_expose_only_type_and_public_filename(self):
        self.sync.error = True
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog()):
            result = self.acquisition.ensure(START, END)
        self.assertEqual(result['state'], 'unavailable')
        self.assertTrue(all(item['error_type'] == 'RuntimeError' for item in result['files']))
        self.assertNotIn('Signature=', json.dumps([result, self.store.states]))
        self.assertNotIn('never-expose-me', json.dumps([result, self.store.states]))

    def test_successful_download_is_not_coverage_when_variable_is_missing(self):
        self.sync.missing_variable = True
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog()):
            result = self.acquisition.ensure(START, END)
        self.assertTrue(all(item['state'] == 'downloaded' for item in result['files']))
        self.assertEqual(result['local_complete'], 0)
        self.assertEqual(result['missing_products'], ['WAV'])

    def test_time_axis_gap_and_separate_half_runs_are_not_complete(self):
        record = grid()
        record['times'].remove('2026-10-17T12:00:00+08:00')
        self.store.records = [record]
        gap = self.acquisition._coverage(START, END, ['WAV'], NOW)
        self.assertEqual(gap['local_partial'], 1)
        self.assertEqual(gap['local_complete'], 0)
        record = grid()
        left = dict(record, times=[value for value in record['times'] if value <= '2026-10-17T11:00:00+08:00'],
                    valid_to='2026-10-17T11:00:00+08:00')
        right = dict(grid('202610170800'), times=[value for value in record['times'] if value >= '2026-10-17T12:00:00+08:00'],
                     valid_from='2026-10-17T12:00:00+08:00')
        self.store.records = [left, right]
        halves = self.acquisition._coverage(START, END, ['WAV'], NOW)
        self.assertEqual(halves['local_partial'], 1)
        self.assertEqual(halves['local_complete'], 0)

    def test_return_and_cache_never_copy_sync_private_fields(self):
        with patch('forecast_runtime.acquisition.requests.get', side_effect=self.catalog()):
            result = self.acquisition.ensure(START, END)
        serialized = json.dumps([result, self.store.states])
        self.assertNotIn('https://', serialized)
        self.assertNotIn('must-not-be-copied', serialized)
        self.assertNotIn('Signature', serialized)


if __name__ == '__main__':
    unittest.main()
