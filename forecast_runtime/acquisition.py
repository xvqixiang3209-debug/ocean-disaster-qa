"""Bounded, auditable acquisition of issued grids for a requested interval.

Signed directory URLs exist only in local variables during a request.  Cached
results contain public filenames and coverage metadata, never credentials or
download URLs.  Catalog coverage is not a claim that a particular sea point has
valid numerical cells; the point reader still checks that separately.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import urlsplit
import hashlib
import json
import os
import re

import requests

from .storage import BEIJING, local_now
from .sync import DIRECTORY, USER_AGENT


PRODUCTS = frozenset({'SWL', 'WAV', 'SSW', 'OCU', 'SST'})
PREFIXES = ('MGrid/PZJ_BEN_L2/', 'MGrid/PZJ_BEN_L5/')
FILE_PATTERN = re.compile(r'^[A-Za-z0-9_]+_(\d{12})_(?:\d+_)?([A-Z]+)_\d+_L([25])\.nc$')


def _time(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return value.astimezone(BEIJING) if value.tzinfo else value.replace(tzinfo=BEIJING)


class GridAcquisition:
    """Fetch at most two issued runs per product/resolution, then recheck local grids."""

    def __init__(self, store, sync, config):
        self.store, self.sync, self.config = store, sync, config
        self.lock = Lock()
        self.cache_seconds = 600

    def _coverage(self, start, end, products, as_of):
        # Inspect registered variable and time axes, not filename promises.
        available = {product: [] for product in products}
        invalid_records = 0
        try:
            records = self.store.grids()
        except Exception as exc:
            return {'local_complete': 0, 'local_partial': 0, 'local_missing': len(products),
                    'missing_products': list(products), 'coverage': {},
                    'coverage_scope': 'registered_variable_and_time_axis',
                    'coverage_error_type': type(exc).__name__}
        expected = []
        cursor = start
        while cursor <= end:
            expected.append(cursor)
            cursor += timedelta(hours=1)
        for record in records:
            product = record.get('product')
            if product not in available:
                continue
            try:
                batch = _time(record['batch'])
                first, last = _time(record['valid_from']), _time(record['valid_to'])
                if batch > as_of or first > end or last < start:
                    continue
                variables = {item['name'] for item in record.get('variables', [])}
                if product not in variables:
                    continue
                times = {_time(item) for item in record.get('times', [])}
                included = sorted(item for item in times if start <= item <= end)
                if not included:
                    continue
                complete = first <= start and last >= end and all(item in times for item in expected)
                available[product].append({'filename': record['filename'], 'batch': batch.isoformat(),
                                           'valid_from': first.isoformat(), 'valid_to': last.isoformat(),
                                           'hours_in_interval': len(included), 'complete': complete})
            except (ValueError, KeyError, TypeError):
                invalid_records += 1
        coverage = {}
        for product, rows in available.items():
            rows.sort(key=lambda row: (row['complete'], row['batch']), reverse=True)
            complete = any(row['complete'] for row in rows)
            coverage[product] = {'state': 'complete' if complete else 'partial' if rows else 'missing',
                                 'single_batch_required': True, 'files': rows[:6]}
        counts = {state: sum(value['state'] == state for value in coverage.values())
                  for state in ('complete', 'partial', 'missing')}
        return {'local_complete': counts['complete'], 'local_partial': counts['partial'],
                'local_missing': counts['missing'],
                'missing_products': [key for key, value in coverage.items() if value['state'] == 'missing'],
                'coverage': coverage, 'invalid_local_records': invalid_records,
                'coverage_scope': 'registered_variable_and_time_axis',
                'point_values_verified': False}

    def _directory(self, start, products, as_of, now):
        credentials = [os.getenv(key, '') for key in ('ZJOCEAN_GRID_ID', 'ZJOCEAN_GRID_CODE', 'ZJOCEAN_GRID_SALT')]
        result = {'checked_at': now.isoformat(), 'as_of': as_of.isoformat(),
                  'directory_state': 'unavailable', 'directory_missing': [],
                  'directory_errors': [], 'files': []}
        if not all(credentials):
            result['directory_state'] = 'credentials_missing'
            result['directory_errors'].append({'reason': 'credentials_missing'})
            return result
        identity, code, salt = credentials
        password = hashlib.md5((code + salt + now.strftime('%Y%m%d')).encode()).hexdigest()
        jobs = []
        successful = 0
        for prefix in PREFIXES:
            level = prefix[-3:-1]
            try:
                response = requests.get(DIRECTORY,
                                        params={'id': identity, 'pwd': password, 'prefix': prefix},
                                        headers={'User-Agent': USER_AGENT}, timeout=(5, 12))
                response.raise_for_status()
                document = response.json()
                files = document.get('files') if isinstance(document, dict) else None
                if not isinstance(files, list) or len(files) > 20000 or any(not isinstance(url, str) for url in files):
                    raise ValueError('invalid_file_directory')
                groups = {product: {} for product in products}
                for url in files:
                    name = urlsplit(url).path.rsplit('/', 1)[-1]
                    match = FILE_PATTERN.fullmatch(name)
                    if not match or match[2] not in groups or 'L' + match[3] != level:
                        continue
                    try:
                        batch = datetime.strptime(match[1], '%Y%m%d%H%M').replace(tzinfo=BEIJING)
                    except ValueError:
                        continue
                    if batch <= as_of:
                        # Deterministic selection if a directory repeats a batch.
                        groups[match[2]].setdefault(batch, (name, url))
                for product, candidates in groups.items():
                    if not candidates:
                        result['directory_missing'].append({'prefix': prefix, 'product': product,
                                                            'reason': 'no_issued_batch'})
                        continue
                    latest = max(candidates)
                    before_start = [batch for batch in candidates if batch <= start]
                    chosen = {latest}
                    if before_start:
                        chosen.add(max(before_start))
                    else:
                        result['directory_missing'].append({'prefix': prefix, 'product': product,
                                                            'reason': 'no_batch_at_or_before_start'})
                    for batch in sorted(chosen, reverse=True):
                        name, url = candidates[batch]
                        jobs.append((prefix, name, url))
                successful += 1
            except Exception as exc:
                # Exceptions may contain a signed request URL: expose types only.
                result['directory_errors'].append({'prefix': prefix, 'reason': 'directory_request_failed',
                                                    'error_type': type(exc).__name__})
        # There are at most 2 levels * 5 products * 2 batches = 20 jobs.
        if jobs:
            def download(job):
                prefix, name, url = job
                try:
                    record = self.sync.download(prefix, url)
                    state = record.get('state') if isinstance(record, dict) else None
                    public = {'filename': name, 'prefix': prefix,
                              'state': state if state in {'downloaded', 'existing', 'error'} else 'unknown'}
                    if isinstance(record, dict) and isinstance(record.get('bytes'), int):
                        public['bytes'] = record['bytes']
                    return public
                except Exception as exc:
                    return {'filename': name, 'prefix': prefix, 'state': 'error',
                            'reason': 'download_failed', 'error_type': type(exc).__name__}
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='question-grid-download') as executor:
                result['files'] = list(executor.map(download, jobs[:20]))
        result['directory_state'] = 'ready' if successful == len(PREFIXES) else 'partial' if successful else 'unavailable'
        if any(record['state'] in {'error', 'unknown'} for record in result['files']):
            result['directory_state'] = 'partial' if successful else 'unavailable'
        return result

    def ensure(self, start, end, products=None, as_of=None, refresh=False):
        start, end = _time(start), _time(end)
        if end < start:
            raise ValueError('查询起止时间需要按先后填写')
        now = local_now()
        explicit_as_of = as_of is not None
        cutoff = min(_time(as_of), now) if explicit_as_of else now
        config = self.config() if callable(self.config) else self.config
        requested = products if products is not None else config.get('download_products', sorted(PRODUCTS))
        requested = [requested] if isinstance(requested, str) else requested
        selected = sorted(set(str(item).upper() for item in requested if str(item).upper() in PRODUCTS))
        if not selected:
            raise ValueError('需要指定SWL、WAV、SSW、OCU或SST产品')
        # Default calls share a 10-minute bucket. Explicit historical cutoffs use
        # their exact upper bound so a later run cannot leak into an earlier one.
        cache_cutoff = cutoff if explicit_as_of else cutoff.replace(minute=cutoff.minute // 10 * 10, second=0, microsecond=0)
        cache_material = {'start': start.isoformat(), 'end': end.isoformat(),
                          'products': selected, 'as_of_upper': cache_cutoff.isoformat()}
        from marine_kb.phase2 import current_turn_key
        if current_turn_key():cache_material['turn_state_fingerprint']=current_turn_key()
        cache_key = 'grid_acquisition:' + hashlib.sha256(json.dumps(cache_material, sort_keys=True).encode()).hexdigest()[:24]
        with self.lock:
            cached = None
            if not refresh:
                try:
                    candidate = self.store.state(cache_key)
                    age = (now - _time(candidate['checked_at'])).total_seconds() if candidate else -1
                    if candidate and 0 <= age < self.cache_seconds and _time(candidate['as_of']) <= cutoff:
                        cached = candidate
                except (ValueError, KeyError, TypeError):
                    pass
            result = dict(cached) if cached else self._directory(start, selected, cutoff, now)
            if not cached:
                self.store.state(cache_key, result)
            result.update(cache_hit=bool(cached), requested_start=start.isoformat(), requested_end=end.isoformat(),
                          requested_products=selected, requested_as_of=cutoff.isoformat())
            result.update(self._coverage(start, end, selected, cutoff))
            result['state'] = ('ready' if result['local_complete'] == len(selected) else
                               'partial' if result['local_complete'] + result['local_partial'] else 'unavailable')
            return result
