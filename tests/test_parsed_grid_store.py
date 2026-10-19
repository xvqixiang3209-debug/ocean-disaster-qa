"""Persistent point-series cache contracts, exercised with real synthetic NCs."""
from contextlib import closing
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

import h5py
import numpy as np

from forecast_runtime.grids import GridReader, inspect_grid
from forecast_runtime.parsed import PARSER_VERSION, ParsedGridStore
from forecast_runtime.storage import ForecastStore


class ParsedGridStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ForecastStore(self.root)
        self.path = self.root / 'PZJ_BEN_OSM_ZJS_202610170800_WAV_00_L2.nc'
        self.record = self.write_grid()
        self.start = self.record['times'][0]
        self.end = self.record['times'][-1]
        self.reader = GridReader()
        self.cache = ParsedGridStore(self.store, self.reader)

    def write_grid(self, values=(0.5, 1.0, 1.5), missing=False):
        with h5py.File(self.path, 'w') as nc:
            nc.create_dataset('lon', data=[122.30, 122.35, 122.40])
            nc.create_dataset('lat', data=[29.90, 29.95, 30.00])
            time = nc.create_dataset('time', data=[0, 1, 2])
            time.attrs['units'] = 'hours since 2026-10-17T08:00:00+08:00'
            data = np.stack([np.full((3, 3), value, dtype=np.float32)
                             for value in values])
            if missing:
                data[:] = -9999
            for name, multiplier in [('WAV', 1), ('MWD', 10)]:
                dataset = nc.create_dataset(name, data=data * multiplier)
                dataset.attrs['unit'] = 'm' if name == 'WAV' else 'degree'
                dataset.attrs['_FillValue'] = -9999
        return inspect_grid(self.path)

    def point(self, cache=None, record=None, **changes):
        options = dict(variable='WAV', lon=122.35, lat=29.95,
                       start=self.start, end=self.end, max_distance=20)
        options.update(changes)
        return (cache or self.cache).point(record or self.record, **options)

    def count(self):
        with closing(self.store.connect()) as db:
            return db.execute('SELECT COUNT(*) FROM grid_point_series').fetchone()[0]

    def test_persistent_reuse_preserves_real_values_and_source_metadata(self):
        first = self.point()
        self.assertFalse(first['parsed_cache_hit'])
        self.assertEqual([item['value'] for item in first['series']], [0.5, 1, 1.5])
        self.assertEqual(first['unit'], 'm')
        self.assertEqual(first['source_id'], self.record['id'])
        # A separate store/reader models a restarted application. No NC read is
        # possible here, so this proves persistence rather than in-memory reuse.
        unavailable_reader = Mock()
        unavailable_reader.point.side_effect = AssertionError('NC must not be reread')
        reopened = ParsedGridStore(ForecastStore(self.root), unavailable_reader)
        second = self.point(cache=reopened)
        self.assertTrue(second.pop('parsed_cache_hit'))
        first.pop('parsed_cache_hit')
        self.assertEqual(second, first)
        unavailable_reader.point.assert_not_called()
        with closing(self.store.connect()) as db:
            row = dict(db.execute('SELECT * FROM grid_point_series').fetchone())
        self.assertEqual(row['source_file_id'], self.record['id'])
        self.assertEqual(row['parser_version'], PARSER_VERSION)
        self.assertTrue(row['created_at'])
        self.assertEqual(json.loads(row['payload_json']), first)

    def test_reinspected_file_hash_invalidates_prior_values(self):
        self.point()
        changed = self.write_grid(values=(2, 3, 4))
        self.assertNotEqual(changed['id'], self.record['id'])
        result = self.point(record=changed)
        self.assertFalse(result['parsed_cache_hit'])
        self.assertEqual([item['value'] for item in result['series']], [2, 3, 4])
        self.assertEqual(result['source_id'], changed['id'])
        self.assertEqual(self.count(), 2)

    def test_coordinates_variable_interval_and_distance_do_not_share_keys(self):
        original = self.point()
        variants = [dict(lon=122.40), dict(lat=30.00), dict(variable='MWD'),
                    dict(start=self.record['times'][1]),
                    dict(end=self.record['times'][1]), dict(max_distance=1)]
        for options in variants:
            with self.subTest(options=options):
                result = self.point(**options)
                self.assertEqual(result['state'], 'ready')
                self.assertFalse(result['parsed_cache_hit'])
                self.assertTrue(self.point(**options)['parsed_cache_hit'])
        self.assertEqual(self.count(), 1 + len(variants))
        short = self.point(start=self.record['times'][1])
        self.assertEqual([row['value'] for row in short['series']], [1, 1.5])
        self.assertEqual(original['grid_lon'], 122.35)
        self.assertEqual(self.point(lon=122.40)['grid_lon'], 122.40)
        self.assertEqual(self.point(variable='MWD')['unit'], 'degree')

    def test_missing_out_of_bounds_and_outside_time_are_not_cached(self):
        missing = self.write_grid(missing=True)
        for _ in range(2):
            result = self.point(record=missing)
            self.assertEqual(result['state'], 'missing_grid')
            self.assertEqual(result['series'], [])
            self.assertFalse(result['parsed_cache_hit'])
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.point(lon=125)['state'], 'out_of_bounds')
        self.assertEqual(self.point(start='2030-01-01', end='2030-01-02')['state'],
                         'outside_valid_time')
        self.assertEqual(self.count(), 0)

    def test_reader_exception_never_persists_a_result(self):
        with self.assertRaises(KeyError):
            self.point(variable='DOES_NOT_EXIST')
        self.assertEqual(self.count(), 0)

    def test_parser_version_changes_invalidate_the_cache(self):
        from unittest.mock import patch
        self.point()
        with patch('forecast_runtime.parsed.PARSER_VERSION', 'next-parser'):
            result = self.point()
        self.assertFalse(result['parsed_cache_hit'])
        self.assertEqual(self.count(), 2)

    def test_retention_keeps_the_most_recent_entries(self):
        self.cache.max_records = 2
        self.point()
        self.point(end=self.record['times'][1])
        self.point(start=self.record['times'][1])
        self.assertEqual(self.count(), 2)
        self.assertTrue(self.point(start=self.record['times'][1])['parsed_cache_hit'])
        self.assertFalse(self.point()['parsed_cache_hit'])
        self.assertEqual(self.count(), 2)


if __name__ == '__main__':
    unittest.main()
