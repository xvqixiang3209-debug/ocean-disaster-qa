"""Acquire only wind/wave products; keep issue, level and source family separate."""
from datetime import datetime, timedelta
from threading import RLock, Thread, Event
from pathlib import Path
import os
from .storage import BEIJING, local_now


def stamp(value):
    if not isinstance(value, datetime): value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return value.astimezone(BEIJING) if value.tzinfo else value.replace(tzinfo=BEIJING)


class ObjectiveSources:
    def __init__(self, runtime):
        self.runtime, self.engine = runtime, runtime.objective
        self.lock, self.stop_event = RLock(), Event()
        self.thread = None
        self.signatures = set()

    def register_local(self):
        with self.lock:
            groups = {}
            for row in self.runtime.store.grids():
                if row.get('product') not in {'WAV', 'SSW'}: continue
                level = row.get('level')
                if level not in {'L2', 'L5'}:
                    import re
                    match = re.search(r'_(L[25])\.nc$', row.get('filename', ''))
                    level = match[1] if match else None
                if not level: continue
                path = row.get('path')
                if not path or not Path(path).is_file(): continue
                groups.setdefault((row['batch'], level), {})[row['product']] = row
            # All locally retained runs remain queryable; an unchanged pair is
            # not rehashed on each chat turn. Sources themselves are immutable.
            for key, values in sorted(groups.items(), reverse=True):
                paths = sorted(str(v['path']) for v in values.values())
                signature = tuple((p, Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in paths)
                if signature in self.signatures: continue
                self.engine.register_directory(paths)
                self.signatures.add(signature)
            return self.engine.catalogue()

    def ensure(self, start, end, as_of=None, refresh=False):
        previous=self.runtime.store.state('objective_acquisition')
        if not refresh and as_of is None and previous and previous.get('checked_at'):
            same_window=(previous.get('requested_start')==stamp(start).isoformat()
                         and previous.get('requested_end')==stamp(end).isoformat())
            if same_window and 0 <= (local_now()-stamp(previous['checked_at'])).total_seconds() < 600:
                self.register_local()
                return {**previous,'cache_hit':True}
        outcome = self.runtime.acquisition.ensure(start, end, products=['WAV', 'SSW'], as_of=as_of, refresh=refresh)
        self.register_local()
        safe = {k: outcome.get(k) for k in ('checked_at', 'directory_state', 'state', 'cache_hit', 'requested_start',
                'requested_end', 'requested_products', 'local_complete', 'local_partial', 'local_missing')}
        safe['files']=[{k:v for k,v in row.items() if k in {'filename','prefix','state','bytes','reason','error_type'}}
                       for row in outcome.get('files',[]) if isinstance(row,dict)]
        self.runtime.store.state('objective_acquisition', safe)
        return safe

    def select(self, start, end, fields=('wave', 'wind'), bounds=(119.5,26.5,123.5,31.5), as_of=None, level=None, latest=False):
        from .objective_time import temporal_coverage
        start, end = stamp(start), stamp(end)
        cutoff = min(stamp(as_of) if as_of else local_now(), start)
        candidates = []
        for item in self.engine.catalogue().get('datasets', []):
            if item.get('source_family') != 'zjocean_l2_l5' or level and item.get('level') != level: continue
            if stamp(item['batch']) > cutoff: continue
            covered, overlap = True, 0
            coverage_fields=[name for name in fields if name!='pressure'] or ['wave','wind']
            for name in coverage_fields:
                data = item.get('fields', {}).get(name)
                if not data: covered = False; continue
                first, last = stamp(data['available_start']), stamp(data['available_end'])
                if first <= end and last >= start: overlap += 1
                spatial = data['coverage']
                time_complete=temporal_coverage(data.get('valid_times',[]),start,end)['complete']
                covered &= (time_complete and first <= start and last >= end and spatial[0] <= bounds[0] and spatial[1] <= bounds[1]
                            and spatial[2] >= bounds[2] and spatial[3] >= bounds[3])
            # Missing pressure is an explicit gap, never a reason to use ECMWF.
            candidates.append((item, bool(covered), overlap))
        if not candidates: return None
        def rank(row):
            item, complete, overlap = row
            issue = stamp(item['batch'])
            return (issue, complete, overlap, item.get('level') == 'L5') if latest else (complete, overlap > 0, issue, item.get('level') == 'L5')
        return max(candidates, key=rank)[0]

    def status(self):
        return {'enabled': os.getenv('MARINE_OBJECTIVE_AUTO_SYNC', 'false').lower() == 'true',
                'running': bool(self.thread and self.thread.is_alive()),
                'products': ['WAV', 'SSW'], 'interval_seconds': self.interval(),
                'last_check': self.runtime.store.state('objective_acquisition')}

    @staticmethod
    def interval():
        try: return max(300, int(os.getenv('MARINE_OBJECTIVE_SYNC_SECONDS', '900')))
        except ValueError: return 900

    def start(self):
        if self.thread and self.thread.is_alive(): return
        def worker():
            if self.stop_event.wait(3): return
            while not self.stop_event.is_set():
                now = local_now().replace(minute=0, second=0, microsecond=0)
                try: self.ensure(now, now + timedelta(hours=72))
                except Exception as exc:
                    self.runtime.store.state('objective_acquisition', {'checked_at': local_now().isoformat(),
                        'state': 'error', 'error_type': type(exc).__name__})
                if self.stop_event.wait(self.interval()): return
        self.thread = Thread(target=worker, name='objective-wind-wave-sync', daemon=True)
        self.thread.start()
