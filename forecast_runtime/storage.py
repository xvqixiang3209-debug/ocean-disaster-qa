from __future__ import annotations
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import collections
import hashlib
import json
import re
import shutil
import sqlite3
import zipfile

BEIJING = timezone(timedelta(hours=8))
INVALID = {-999, -9999, -99999, 9999}


def local_now():
    return datetime.now(BEIJING)


def read_sql(path, table):
    """Parse a Navicat literal dump as data; never execute its SQL."""
    text = Path(path).read_text(encoding='utf-8-sig')
    block = re.search(r'CREATE TABLE `' + re.escape(table) + r'`\s*\((.*?)\) ENGINE', text, re.S)
    if not block:
        raise ValueError('找不到指定表结构')
    columns = re.findall(r'^\s*`([^`]+)`', block.group(1), re.M)
    rows = []
    for match in re.finditer(r'^INSERT INTO `' + re.escape(table) + r'` VALUES \((.*)\);\s*$', text, re.M):
        raw, parts, start, quoted, i = match.group(1), [], 0, False, 0
        while i < len(raw):
            if quoted and raw[i] == '\\':
                i += 2
                continue
            if raw[i] == "'":
                if quoted and i + 1 < len(raw) and raw[i + 1] == "'":
                    i += 2
                    continue
                quoted = not quoted
            if raw[i] == ',' and not quoted:
                parts.append(raw[start:i].strip())
                start = i + 1
            i += 1
        if quoted:
            raise ValueError('SQL字符串未闭合')
        parts.append(raw[start:].strip())
        values = []
        for token in parts:
            if token.upper() == 'NULL':
                values.append(None)
            elif token.startswith("'") and token.endswith("'"):
                value = token[1:-1].replace("''", "'")
                values.append(re.sub(r'\\(.)', lambda m: {'n':'\n','r':'\r','t':'\t','0':'\0'}.get(m[1], m[1]), value))
            elif re.fullmatch(r'-?\d+(?:\.\d+)?', token):
                values.append(float(token) if '.' in token else int(token))
            elif re.fullmatch("b'[01]+'", token):
                values.append(int(token[2:-1], 2))
            else:
                raise ValueError('不支持的SQL值类型')
        if len(values) != len(columns):
            raise ValueError('SQL列数不匹配')
        rows.append(dict(zip(columns, values)))
    if not rows or len(rows) != len(re.findall(r'^INSERT INTO `' + re.escape(table) + '`', text, re.M)):
        raise ValueError('SQL记录不完整')
    return rows


def coordinates(row):
    try:
        lon, lat = float(row['Lon']), float(row['Lat'])
    except (TypeError, ValueError, KeyError):
        return None, None, 'missing'
    if 24 <= lon <= 33 and 115 <= lat <= 125:
        lon, lat = lat, lon
        state = 'swapped_fields'
    else:
        state = 'source_coordinates'
    if not (115 <= lon <= 125 and 24 <= lat <= 33):
        return None, None, 'out_of_region'
    return lon, lat, state


class ForecastStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / 'forecast.sqlite3'
        with closing(self.connect()) as db, db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS entities(kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS tide_hours(code TEXT,time TEXT,value_cm INTEGER,PRIMARY KEY(code,time));
                CREATE TABLE IF NOT EXISTS tide_extrema(code TEXT,day TEXT,time TEXT,kind TEXT,value_cm INTEGER,PRIMARY KEY(code,day,time,kind));
                CREATE TABLE IF NOT EXISTS tide_files(code TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS grids(id TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS typhoon_snapshots(id TEXT PRIMARY KEY,retrieved_at TEXT NOT NULL,payload TEXT NOT NULL,origin TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS typhoon_snapshot_time ON typhoon_snapshots(retrieved_at);
            ''')

    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def entities(self, kind):
        with closing(self.connect()) as db:
            return [json.loads(r['payload']) for r in db.execute('SELECT payload FROM entities WHERE kind=? ORDER BY id', (kind,))]

    def state(self, key, value=None):
        with closing(self.connect()) as db, db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
                if key.startswith('evidence:'):
                    db.execute("DELETE FROM state WHERE key LIKE 'evidence:%' AND rowid NOT IN (SELECT rowid FROM state WHERE key LIKE 'evidence:%' ORDER BY rowid DESC LIMIT 100)")
                return value
            row = db.execute('SELECT payload FROM state WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def record_typhoon_snapshot(self,data,origin=None,complete=False):
        expected='https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons'
        if data.get('source_url')!=expected or not isinstance(data.get('list'),list) or not data.get('retrieved_at'):
            raise ValueError('invalid_typhoon_snapshot_source')
        stamp=datetime.fromisoformat(data['retrieved_at'])
        stamp=(stamp.astimezone(BEIJING) if stamp.tzinfo else stamp.replace(tzinfo=BEIJING)).isoformat()
        value={k:data.get(k) for k in ('source','source_url','list')}
        value.update(retrieved_at=stamp,snapshot_scope='complete_api_response' if complete else 'captured_tool_subset')
        # Identical source bodies share one snapshot; retain the earliest
        # proven retrieval, not the latest refresh time.
        digest=hashlib.sha256(json.dumps({'source_url':expected,'list':value['list'],'complete':complete},
            ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with closing(self.connect()) as db,db:
            db.execute('INSERT INTO typhoon_snapshots VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                'retrieved_at=excluded.retrieved_at,payload=excluded.payload,origin=excluded.origin '
                'WHERE excluded.retrieved_at<typhoon_snapshots.retrieved_at',
                (digest,stamp,json.dumps(value,ensure_ascii=False),json.dumps(origin or {'kind':'direct_api'},ensure_ascii=False)))
        return digest

    def typhoon_snapshot(self,as_of,storm_ids=(),question='',allow_partial=False):
        as_of=(as_of.astimezone(BEIJING) if as_of.tzinfo else as_of.replace(tzinfo=BEIJING)).isoformat()
        with closing(self.connect()) as db:
            rows=db.execute('SELECT id,payload FROM typhoon_snapshots WHERE retrieved_at<=? ORDER BY retrieved_at DESC',(as_of,)).fetchall()
        for row in rows:
            value=json.loads(row['payload']);storms=value['list']
            named=[s for s in storms if str(s.get('nameCN') or '\0') in question or str(s.get('nameEN') or '\0').lower() in question.lower()]
            if storm_ids and not any(str(s.get('typhoonID')) in storm_ids for s in storms):continue
            if not storm_ids and not named and value['snapshot_scope']!='complete_api_response' and not allow_partial:continue
            return {**value,'snapshot_id':row['id'],'historical_snapshot':True,'cache_stale':False,'cache_age_seconds':0}
        return None

    def tide_files(self):
        with closing(self.connect()) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM tide_files ORDER BY code')]

    def hours(self, code, start, end):
        with closing(self.connect()) as db:
            return [dict(r) for r in db.execute('SELECT time,value_cm FROM tide_hours WHERE code=? AND time>=? AND time<=? ORDER BY time', (code, start, end))]

    def extrema(self, code, start, end):
        with closing(self.connect()) as db:
            return [dict(r) for r in db.execute('SELECT time,kind,value_cm FROM tide_extrema WHERE code=? AND time>=? AND time<=? ORDER BY time', (code, start, end))]

    def grids(self):
        with closing(self.connect()) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM grids')]

    def register_grid(self, record):
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO grids VALUES (?,?)', (record['id'], json.dumps(record, ensure_ascii=False)))

    def import_sources(self, site_sql, coast_sql, tide_zip, names_txt, geometry=None, provisional_aliases=None):
        sites, coasts = read_sql(site_sql, 'm_fcst_site'), read_sql(coast_sql, 'm_warn_tide_info_town')
        source_dir = self.root / 'sources'
        source_dir.mkdir(exist_ok=True)
        manifests = []
        for path in [site_sql, coast_sql, tide_zip, names_txt]:
            path = Path(path)
            content = path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            target = source_dir / (checksum[:12] + '_' + path.name)
            if not target.exists():
                shutil.copyfile(path, target)
            manifests.append({'name':path.name,'sha256':checksum})
        shapes = {}
        if geometry:
            document = json.loads(Path(geometry).read_text(encoding='utf-8-sig'))
            shapes = {str(f['properties']['cid']):f['geometry'] for f in document['features']}
        names = {}
        for line in Path(names_txt).read_text(encoding='utf-8-sig').splitlines():
            if line.strip():
                code, name = line.split(None, 1)
                names[code] = name.strip()
        site_files = {s['DataFile'].casefold():s for s in sites if s.get('DataFile')}
        for site in sites:
            site['lon'], site['lat'], site['coordinate_state'] = coordinates(site)
            site['coordinate_quality'] = 'rounded_integer' if site['lon'] is not None else 'missing'
        for coast in coasts:
            coast['lon'], coast['lat'], coast['coordinate_state'] = coordinates(coast)
            coast['geometry'] = shapes.get(str(coast['Id']))
            coast['thresholds_cm'] = {color:None if coast.get(key) in INVALID or coast.get(key) is None else float(coast[key])
                                      for color,key in [('blue','BlueLevel'),('yellow','YellowLevel'),('orange','OrangeLevel'),('red','RedLevel')]}
        hour_count, extreme_count, errors, files = 0, 0, [], []
        with closing(self.connect()) as db, db:
            for kind, rows, key in [('site',sites,'StationID'),('coast',coasts,'Id')]:
                db.execute('DELETE FROM entities WHERE kind=?',(kind,))
                db.executemany('INSERT INTO entities VALUES (?,?,?)', [(kind,str(r[key]),json.dumps(r,ensure_ascii=False)) for r in rows])
            # Whole-year import is one transaction: repeated import cannot duplicate rows.
            with zipfile.ZipFile(tide_zip) as archive:
                for entry in archive.infolist():
                    match = re.fullmatch(r'([A-Za-z0-9_]+)((?:19|20)\d{2})', entry.filename)
                    if entry.is_dir() or not match:
                        continue
                    code, year = match[1], int(match[2])
                    site = site_files.get(code.casefold())
                    if site is None:
                        # Two water-authority stations have unique documented names, but lack DataFile.
                        alias_ids = provisional_aliases or {}
                        site = next((s for s in sites if s['StationID']==alias_ids.get(code)),None)
                        match_state = 'provisional_alias' if site else 'unmapped'
                    else:
                        match_state = 'datafile_exact'
                    lines = [s for s in archive.read(entry).decode('utf-8-sig').splitlines() if s.strip()]
                    record = {'code':code,'year':year,'name':names.get(code,names.get('QIANGJIANG') if code=='QINGJIANG' else code),
                              'station_id':site['StationID'] if site else None,'station_name':site['StationName'] if site else None,
                              'base85_cm':site.get('Base85') if site else None,'lon':site.get('lon') if site else None,'lat':site.get('lat') if site else None,
                              'mapping':match_state,'rows':len(lines),'filename':entry.filename,'state':'ready' if lines else 'empty'}
                    db.execute('DELETE FROM tide_hours WHERE code=?',(code,))
                    db.execute('DELETE FROM tide_extrema WHERE code=?',(code,))
                    hours, extrema = [], []
                    first = datetime(year,1,1,tzinfo=BEIJING)
                    for day_index, line in enumerate(lines):
                        try:
                            values = [int(s) for s in line.split()]
                            if len(values)!=36 or (first+timedelta(days=day_index)).year!=year:
                                raise ValueError('invalid_day')
                        except ValueError:
                            errors.append({'code':code,'day_index':day_index,'reason':'invalid_row'})
                            continue
                        day = first + timedelta(days=day_index)
                        for hour, value in enumerate(values[:24]):
                            if value not in INVALID and value <= 7000:
                                hours.append((code,(day+timedelta(hours=hour)).isoformat(),value))
                        for offset in range(24,36,2):
                            hhmm, value = values[offset:offset+2]
                            if hhmm>2501 or value>7000 or value in INVALID or not (0<=hhmm//100<24 and 0<=hhmm%100<60):
                                continue
                            time = day+timedelta(hours=hhmm//100,minutes=hhmm%100)
                            extrema.append((code,day.date().isoformat(),time.isoformat(),'high' if offset<30 else 'low',value))
                    db.executemany('INSERT INTO tide_hours VALUES (?,?,?)',hours)
                    db.executemany('INSERT INTO tide_extrema VALUES (?,?,?,?,?)',extrema)
                    db.execute('INSERT OR REPLACE INTO tide_files VALUES (?,?)',(code,json.dumps(record,ensure_ascii=False)))
                    files.append(record);hour_count+=len(hours);extreme_count+=len(extrema)
        result = {'imported_at':local_now().isoformat(),'sites':len(sites),'coasts':len(coasts),'tide_files':len(files),
                  'tide_hours':hour_count,'tide_extrema':extreme_count,'empty_files':[r['code'] for r in files if r['state']=='empty'],
                  'mapping_counts':dict(collections.Counter(r['mapping'] for r in files)), 'row_errors':errors[:100], 'sources':manifests}
        self.state('import', result)
        return result
