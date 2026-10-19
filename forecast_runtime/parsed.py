"""Persist parsed, source-linked NC point series without changing grid semantics."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math

from .storage import local_now


PARSER_VERSION = 'grid-point-series-v2'


class ParsedGridStore:
    """Cache successful GridReader.point results in the forecast SQLite database.

    Grid records use their content SHA-256 as ``id``. Filename-based time origins
    and product labels also affect interpretation, even when bytes are identical.
    Empty or failed reads are deliberately not persisted.
    """

    max_records = 5000

    def __init__(self, store, reader):
        self.store = store
        self.reader = reader
        with closing(store.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS grid_point_series (
                    cache_key TEXT PRIMARY KEY,
                    parser_version TEXT NOT NULL,
                    source_file_id TEXT NOT NULL,
                    variable TEXT NOT NULL,
                    query_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS grid_point_series_source
                    ON grid_point_series(source_file_id);
                CREATE INDEX IF NOT EXISTS grid_point_series_created
                    ON grid_point_series(created_at DESC);
            ''')

    @staticmethod
    def _number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Grid query coordinates and distance must be finite')
        return result if result else 0.0

    def point(self, record, variable, lon, lat, start, end, max_distance=20):
        query = {
            'parser_version': PARSER_VERSION,
            'source_file_id': record['id'],
            'source_context': {name: record.get(name) for name in (
                'filename', 'batch', 'product', 'level', 'times',
                'time_origin', 'time_timezone_assumed')},
            'variable': variable,
            'lon': self._number(lon),
            'lat': self._number(lat),
            'start': start,
            'end': end,
            'max_distance': self._number(max_distance),
        }
        serialized = json.dumps(query, sort_keys=True, separators=(',', ':'),
                                ensure_ascii=False, allow_nan=False)
        key = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        with closing(self.store.connect()) as db:
            cached = db.execute(
                'SELECT payload_json FROM grid_point_series WHERE cache_key=?',
                (key,),
            ).fetchone()
        if cached:
            result = json.loads(cached[0])
            return dict(result, parsed_cache_hit=True)

        # Reader remains responsible for units, fill values, spatial selection,
        # time alignment and vector components. An exception never writes data.
        result = self.reader.point(record, variable, lon, lat, start, end,
                                   max_distance=max_distance)
        if result.get('state') == 'ready' and result.get('series'):
            payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
            with closing(self.store.connect()) as db, db:
                db.execute('''
                    INSERT OR REPLACE INTO grid_point_series
                    (cache_key, parser_version, source_file_id, variable,
                     query_json, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (key, PARSER_VERSION, record['id'], variable, serialized,
                      payload, local_now().isoformat()))
                db.execute('''
                    DELETE FROM grid_point_series WHERE rowid NOT IN (
                        SELECT rowid FROM grid_point_series
                        ORDER BY created_at DESC, rowid DESC LIMIT ?
                    )
                ''', (self.max_records,))
        return dict(result, parsed_cache_hit=False)
