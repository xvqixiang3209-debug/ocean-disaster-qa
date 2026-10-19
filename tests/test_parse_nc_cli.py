"""Reusable NC command contracts; synthetic local files, no network or service."""
from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np

from tools.parse_nc import main


class ParseNcCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.serial = 0

    def grid(self, product='SSW', batch='202610170800', level='L2',
             missing=False, unsupported=False, time_units=None):
        path = self.inputs / f'PZJ_BEN_OSM_ZJS_{batch}_{product}_00_{level}.nc'
        components = {'u10': 3, 'v10': 4} if product == 'SSW' else {product: 1}
        if unsupported:
            components = {'UNSUPPORTED': 7}
        with h5py.File(path, 'w') as nc:
            nc.create_dataset('lon', data=[122.30, 122.35, 122.40])
            nc.create_dataset('lat', data=[29.90, 29.95, 30.00])
            time = nc.create_dataset('time', data=[0, 1, 2])
            origin = datetime.strptime(batch, '%Y%m%d%H%M').isoformat()
            time.attrs['units'] = time_units or f'hours since {origin}+08:00'
            for name, value in components.items():
                values = np.full((3, 3, 3), -9999 if missing else value,
                                 dtype=np.float32)
                variable = nc.create_dataset(name, data=values)
                variable.attrs['unit'] = 'm/s' if product == 'SSW' else 'm'
                variable.attrs['_FillValue'] = -9999
        return path

    def run_cli(self, command, input_path, *extra, output=None):
        self.serial += 1
        output = output or self.root / f'result-{self.serial}.json'
        arguments = [command, '--input', str(input_path), '--output', str(output)]
        if command == 'point':
            arguments.extend(['--cache-dir', str(self.root / 'parsed-cache')])
        arguments.extend(extra)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(arguments)
        payload = (json.loads(output.read_text(encoding='utf-8-sig'))
                   if output.suffix == '.json' and output.exists() else None)
        return result, payload, output, stdout.getvalue() + stderr.getvalue()

    @staticmethod
    def point_options(start='2026-10-17T08:00:00+08:00',
                      end='2026-10-17T10:00:00+08:00'):
        return ['--lon', '122.35', '--lat', '29.95',
                '--start', start, '--end', end]

    def test_vector_query_normalizes_equivalent_timezones_and_exports_csv(self):
        path = self.grid()
        beijing = self.run_cli('point', path, *self.point_options())
        utc = self.run_cli('point', path, *self.point_options(
            '2026-10-17T00:00:00Z', '2026-10-17T02:00:00Z'))
        for result, payload, output, message in (beijing, utc):
            self.assertEqual(result, 0, message)
            self.assertEqual(payload['query']['start'],
                             '2026-10-17T08:00:00+08:00')
            series = payload['files'][0]['point']['series']
            self.assertEqual([row['value'] for row in series], [5, 5, 5])
            self.assertEqual(payload['files'][0]['point']['maximum']['value'], 5)
            self.assertEqual(payload['files'][0]['point']['minimum']['value'], 5)
            with output.with_suffix('.csv').open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
        self.assertEqual(beijing[1]['files'][0]['point']['series'],
                         utc[1]['files'][0]['point']['series'])

    def test_naive_time_is_explicitly_beijing_and_cache_survives_new_call(self):
        path = self.grid()
        options = self.point_options('2026-10-17T08:00:00',
                                     '2026-10-17T10:00:00')
        options.extend(['--cache-dir', str(self.root / 'parsed-cache')])
        first = self.run_cli('point', path, *options)
        second = self.run_cli('point', path, *options)
        self.assertEqual(first[0], 0, first[3])
        self.assertEqual(second[0], 0, second[3])
        self.assertEqual(first[1]['query']['start'],
                         '2026-10-17T08:00:00+08:00')
        self.assertFalse(first[1]['files'][0]['point']['parsed_cache_hit'])
        self.assertTrue(second[1]['files'][0]['point']['parsed_cache_hit'])
        self.assertTrue((self.root / 'parsed-cache' / 'forecast.sqlite3').exists())

    def test_directory_requires_batch_and_level_to_avoid_splicing_products(self):
        self.grid(batch='202610170800', level='L2')
        self.grid(batch='202610170800', level='L5')
        self.grid(batch='202610162000', level='L2')
        no_batch = self.run_cli('point', self.inputs, *self.point_options())
        self.assertEqual(no_batch[0], 2, no_batch[3])
        no_level = self.run_cli('point', self.inputs, *self.point_options(),
                                '--batch', '202610170800')
        self.assertEqual(no_level[0], 2, no_level[3])
        selected = self.run_cli('point', self.inputs, *self.point_options(),
                                '--batch', '202610170800', '--level', 'L2')
        self.assertEqual(selected[0], 0, selected[3])
        self.assertEqual(len(selected[1]['files']), 1)
        self.assertEqual(selected[1]['files'][0]['metadata']['level'], 'L2')
        self.assertEqual(selected[1]['files'][0]['metadata']['batch'],
                         '2026-10-17T08:00:00+08:00')

    def test_missing_or_unsupported_product_never_becomes_zero_or_an_extreme(self):
        for unsupported, expected_exit in ((False, 3), (True, 2)):
            with self.subTest(unsupported=unsupported):
                path = self.grid(product='WAV', missing=not unsupported,
                                 unsupported=unsupported)
                result, payload, _, message = self.run_cli(
                    'point', path, *self.point_options())
                self.assertEqual(result, expected_exit, message)
                if payload:
                    for item in payload['files']:
                        point = item.get('point') or {}
                        self.assertFalse(point.get('series'))
                        self.assertIsNone(point.get('minimum'))
                        self.assertIsNone(point.get('maximum'))

    def test_swl_inspection_keeps_semantics_and_time_fallback_unconfirmed(self):
        path = self.grid(product='SWL', time_units='hours since initial time')
        result, payload, _, message = self.run_cli('inspect', path)
        self.assertEqual(result, 0, message)
        item = payload['files'][0]
        self.assertEqual(item['metadata']['time_origin'], 'filename_assumed')
        self.assertTrue(item['metadata']['time_timezone_assumed'])
        warnings = '\n'.join(item['warnings'])
        self.assertIn('纯增水', warnings)
        self.assertIn('总潮位', warnings)
        self.assertRegex(warnings, '核定|确认')
        self.assertNotIn('point', item)

    def test_output_cannot_overwrite_the_source_nc(self):
        path = self.grid()
        original = path.read_bytes()
        result, _, _, message = self.run_cli('inspect', path, output=path)
        self.assertEqual(result, 2, message)
        self.assertEqual(path.read_bytes(), original)

    def test_identical_bytes_with_different_filename_context_keep_correct_series_and_source(self):
        # These products sometimes omit their issue time from the NC itself.
        # Identical bytes with a different filename can therefore mean different
        # valid times, even though the content SHA-256 is unchanged.
        for batch, level, expected in (
                ('202610170900', 'L2', [1, 2]),
                ('202610170800', 'L5', [2, 3])):
            with self.subTest(batch=batch, level=level):
                first_path = self.grid(product='WAV',
                                       time_units='hours since initial time')
                with h5py.File(first_path, 'r+') as nc:
                    nc['WAV'][:] = np.stack([np.full((3, 3), value)
                                            for value in (1, 2, 3)])
                second_path = self.inputs / (
                    f'PZJ_BEN_OSM_ZJS_{batch}_WAV_00_{level}.nc')
                second_path.write_bytes(first_path.read_bytes())
                cache = self.root / f'context-cache-{batch}-{level}'
                options = self.point_options('2026-10-17T09:00:00+08:00',
                                             '2026-10-17T10:00:00+08:00')
                options.extend(['--cache-dir', str(cache)])
                first = self.run_cli('point', first_path, *options)
                second = self.run_cli('point', second_path, *options)
                self.assertEqual(first[0], 0, first[3])
                self.assertEqual(second[0], 0, second[3])
                item = second[1]['files'][0]
                point = item['point']
                self.assertEqual([row['value'] for row in point['series']], expected)
                self.assertEqual(point['filename'], item['metadata']['filename'])
                self.assertEqual(point['batch'], item['metadata']['batch'])

    def test_unreadable_cache_database_returns_an_actionable_error(self):
        path = self.grid()
        original = path.read_bytes()
        cache = self.root / 'invalid-cache'
        cache.mkdir()
        (cache / 'forecast.sqlite3').write_text('not a SQLite database',
                                              encoding='utf-8')
        result, _, _, message = self.run_cli(
            'point', path, *self.point_options(), '--cache-dir', str(cache))
        self.assertEqual(result, 2, message)
        self.assertIn('error', message)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
