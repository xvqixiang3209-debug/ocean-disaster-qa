from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
import hashlib
import json
import os
import re
import time
import requests
from uuid import uuid4

from .grids import inspect_grid
from .storage import BEIJING, local_now

DIRECTORY='https://zjocean.org.cn/3rdpartyservice/index.php/index/getRulerFile'
TYPHOON='https://zjocean.org.cn/mapi_web/index.php/typhoon/'
USER_AGENT='Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.106 Safari/537.36'


class SourceSync:
    def __init__(self, store, config):
        self.store,self.config=store,config
        self.lock=Lock()
        self.typhoon_lock=Lock()

    def typhoons(self, refresh=False):
        cached=self.store.state('typhoons')
        age=(local_now()-datetime.fromisoformat(cached['retrieved_at'])).total_seconds() if cached else float('inf')
        if cached and age<600 and not refresh:
            self.store.record_typhoon_snapshot(cached,complete=True)
            return {**cached,'cache_age_seconds':round(age),'cache_stale':False}
        with self.typhoon_lock:
            try:
                response=requests.post(TYPHOON+'index/activetyphoons',data={},headers={'User-Agent':USER_AGENT,'Referer':'https://zjocean.org.cn/typhoon/'},timeout=(5,10))
                response.raise_for_status()
                data=response.json()
                if not isinstance(data,dict) or data.get('status')!=200 or not isinstance(data.get('list'),list):
                    raise ValueError('invalid_typhoon_response')
                value={'retrieved_at':local_now().isoformat(),'list':data['list'],'source':'浙江海洋预报网活动台风接口','source_url':TYPHOON+'index/activetyphoons'}
                self.store.record_typhoon_snapshot(value,complete=True)
                self.store.state('typhoons',value)
                return {**value,'cache_age_seconds':0,'cache_stale':False}
            except Exception as exc:
                if cached:
                    return {**cached,'cache_age_seconds':round(age),'cache_stale':True,'refresh_error':type(exc).__name__}
                return {'list':[],'state':'unavailable','refresh_error':type(exc).__name__,'cache_stale':True}

    def download(self, prefix, url):
        # Signed URLs remain server-side; no URLs/credentials in logs or database.
        parsed=urlsplit(url)
        government_oss='hzzf-oss.oss403c-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn'
        allowed=(parsed.scheme in {'http','https'} and parsed.hostname==government_oss) or (
            parsed.scheme=='https' and (parsed.hostname or '').endswith('.aliyuncs.com'))
        if not allowed:
            raise ValueError('unexpected_download_host')
        name=Path(parsed.path).name
        if not re.fullmatch(r'[A-Za-z0-9_]+\.nc',name):
            raise ValueError('invalid_grid_filename')
        target=self.store.root/'grids'/prefix.replace('/','_').strip('_')/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            try:
                record=inspect_grid(target);self.store.register_grid(record)
                return {'filename':name,'state':'existing'}
            except (ValueError,OSError):
                pass
        # Background sync and query-driven acquisition can request the same
        # file concurrently. Each writer owns its staging file; publication
        # remains atomic and no writer can unlink another writer's data.
        part=target.with_name(target.name+'.'+uuid4().hex+'.part')
        try:
            with requests.get(url,stream=True,headers={'Referer':'https://zjocean.org.cn'},timeout=(5,20),allow_redirects=False) as response:
                response.raise_for_status();size=0
                if response.status_code!=200:raise ValueError('unexpected_download_redirect')
                with part.open('wb') as stream:
                    for chunk in response.iter_content(65536):
                        size+=len(chunk)
                        if size>self.config()['max_file_bytes']:
                            raise ValueError('grid_download_too_large')
                        stream.write(chunk)
            # Validate before publishing the completed filename.
            with __import__('h5py').File(part,'r') as nc:
                if not all(key in nc for key in ('time','lat','lon')):
                    raise ValueError('invalid_netcdf_download')
            # Serialize publication across processes too. On Windows an open
            # HDF5 reader prevents replacement; if another writer published
            # the same valid target, reuse it rather than replacing it again.
            state='downloaded'
            with closing(self.store.connect()) as db,db:
                db.execute('BEGIN IMMEDIATE')
                existing=None
                if target.exists():
                    try:existing=inspect_grid(target)
                    except (ValueError,OSError):pass
                if existing:
                    record=existing;state='existing'
                else:
                    part.replace(target);record=inspect_grid(target)
                db.execute('INSERT OR REPLACE INTO grids VALUES (?,?)',
                           (record['id'],json.dumps(record,ensure_ascii=False)))
            return {'filename':name,'state':state,'bytes':size}
        finally:
            if part.exists():
                part.unlink()

    def run(self):
        if not self.lock.acquire(blocking=False):
            return {'state':'running'}
        try:
            self.store.state('sync',{'state':'running','started_at':local_now().isoformat()})
            current=self.typhoons(refresh=True)
            credentials=[os.getenv(k,'') for k in ('ZJOCEAN_GRID_ID','ZJOCEAN_GRID_CODE','ZJOCEAN_GRID_SALT')]
            if not all(credentials):
                result={'state':'credentials_missing','typhoon_count':len(current['list']),'finished_at':local_now().isoformat()}
                self.store.state('sync',result);return result
            identity,code,salt=credentials
            pwd=hashlib.md5((code+salt+local_now().strftime('%Y%m%d')).encode()).hexdigest()
            jobs=[]
            for prefix in ('MGrid/PZJ_BEN_L2/','MGrid/PZJ_BEN_L5/'):
                response=requests.get(DIRECTORY,params={'id':identity,'pwd':pwd,'prefix':prefix},headers={'User-Agent':USER_AGENT},timeout=(5,12))
                response.raise_for_status();files=response.json().get('files')
                if not isinstance(files,list):
                    raise ValueError('invalid_file_directory')
                groups={}
                for url in files:
                    if not isinstance(url,str):continue
                    match=re.search(r'_(\d{12})_(?:\d+_)?([A-Z]+)_\d+_L[25]\.nc$',urlsplit(url).path)
                    if match and match[2] in self.config()['download_products']:
                        if match[2] not in groups or match[1]>groups[match[2]][0]:
                            groups[match[2]]=(match[1],url)
                jobs.extend((prefix,value[1]) for value in groups.values())
            results=[]
            with ThreadPoolExecutor(max_workers=2,thread_name_prefix='forecast-download') as executor:
                futures=[executor.submit(self.download,prefix,url) for prefix,url in jobs]
                for future in futures:
                    try:results.append(future.result())
                    except Exception as exc:results.append({'state':'error','error_type':type(exc).__name__})
            result={'state':'ready' if results and all(r['state']!='error' for r in results) else 'partial',
                    'files':results,'typhoon_count':len(current['list']),'finished_at':local_now().isoformat()}
        except Exception as exc:
            result={'state':'error','error_type':type(exc).__name__,'finished_at':local_now().isoformat()}
        finally:
            self.lock.release()
        self.store.state('sync',result)
        return result


def _typhoon_choice_error(message,choices=None,code='CURRENT_TYPHOON_SELECTION_REQUIRED'):
    from typhoon.schema import TyphoonInputError
    error=TyphoonInputError(message)
    error.code=code;error.choices=choices or []
    return error


def select_typhoon_payload(data, *, storm_id=None, agency=None, track_kind=None, cutoff=None):
    """Pure selection shared by map and QA; no network and no implicit last storm.

    Forecast input contains one explicit agency and the observation anchor.
    Input identity, issued time and path type remain visible in result metadata.
    """
    import math
    from typhoon.schema import normalize_current_typhoon
    kind=str(track_kind or ('forecast' if agency else 'observed')).strip().lower()
    if kind not in {'observed','forecast'}:
        raise _typhoon_choice_error('track_kind仅支持observed（已有路径）或forecast（单机构预报路径）。',code='INVALID_TRACK_KIND')
    if kind=='observed' and agency:
        raise _typhoon_choice_error('已有路径不选择预报机构；请改用track_kind=forecast或清空agency。',code='INVALID_TRACK_SELECTION')
    storms=[s for s in data.get('list') or [] if isinstance(s,dict)]
    choices=[{'storm_id':str(s.get('typhoonID') or ''),'name':s.get('nameCN') or s.get('nameEN') or ''} for s in storms]
    if storm_id is not None:
        storms=[s for s in storms if str(s.get('typhoonID'))==str(storm_id).strip()]
        if not storms:raise _typhoon_choice_error('未取得所选台风记录，不改用另一场台风。',choices,'CURRENT_TYPHOON_NOT_FOUND')
    if not storms:raise FileNotFoundError('当前接口没有可用活动台风；可选择已有历史案例。')
    if len(storms)!=1:raise _typhoon_choice_error('有多个活动台风，请明确storm_id后检索；系统不会默认选择最后一个。',choices)
    storm=storms[0]
    def instant(value):
        t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return t.astimezone(BEIJING) if t.tzinfo else t.replace(tzinfo=BEIJING)
    limit=instant(cutoff) if cutoff is not None else None
    points=[]
    for point in storm.get('points') or []:
        if not isinstance(point,dict) or not point.get('time'):continue
        try:
            when=instant(point['time']);lat=float(point['lat']);lon=float(point['lon'])
        except (ValueError,TypeError,KeyError):continue
        if not all(math.isfinite(v) for v in (lat,lon)) or not (-90<=lat<=90 and -180<=lon<=180):continue
        if limit and when>limit:continue
        points.append((when,point))
    points.sort(key=lambda pair:pair[0])
    if not points:raise _typhoon_choice_error('所选台风没有截至查询时刻可用的路径点。',code='CURRENT_TYPHOON_TRACK_MISSING')
    issued,latest=points[-1];forecasts=latest.get('forecast') or {}
    agencies=sorted(k for k,v in forecasts.items() if isinstance(v,list) and v)
    selected_agency=None;observed_count=len(points);forecast_count=0
    if kind=='forecast':
        aliases={'CMA':'中国','CHINA':'中国','中国气象局':'中国','JMA':'日本','JAPAN':'日本','日本气象厅':'日本',
                 'JTWC':'美国','USA':'美国','HKO':'香港','中国香港':'香港'}
        requested=str(agency or '').strip()
        alias=aliases.get(requested.upper(),aliases.get(requested,requested))
        selected_agency=next((k for k in agencies if k.casefold()==alias.casefold() or k.casefold()==requested.casefold()),None)
        agency_choices=[{'agency':a,'forecast_points':len(forecasts[a])} for a in agencies]
        if not requested and len(agencies)==1:selected_agency=agencies[0]
        if not selected_agency:
            raise _typhoon_choice_error('请选择该台风可用的预报机构，不拼接不同机构路径。',agency_choices,'FORECAST_AGENCY_REQUIRED' if not requested else 'FORECAST_AGENCY_NOT_FOUND')
        points=[(issued,latest)]
        for point in forecasts[selected_agency]:
            try:
                when=instant(point['time']);lat=float(point['lat']);lon=float(point['lon'])
            except (ValueError,TypeError,KeyError):continue
            if when<=issued or not all(math.isfinite(v) for v in (lat,lon)) or not (-90<=lat<=90 and -180<=lon<=180):continue
            points.append((when,point))
        points.sort(key=lambda pair:pair[0]);observed_count=1;forecast_count=len(points)-1
        if not forecast_count:raise _typhoon_choice_error('所选机构没有起报之后的有效预报路径点，不改用已有路径代替。',agency_choices,'FORECAST_TRACK_MISSING')
    def number(value):
        try:result=float(value)
        except (ValueError,TypeError):return None
        return result if math.isfinite(result) else None
    track=[]
    for when,point in points:
        wind=number(point.get('maxSpeed'))
        track.append({'time':when.isoformat(),'lat':float(point['lat']),'lon':float(point['lon']),
                      'wind_kt':wind/.514444 if wind is not None else None,'pressure_hpa':number(point.get('pressure'))})
    payload={'sid':str(storm['typhoonID']),'name':storm.get('nameCN') or storm.get('nameEN') or str(storm['typhoonID']),
             'source':'ZJOCEAN'+(':'+selected_agency if selected_agency else ':OBSERVED'),
             'issued_at':issued.isoformat(),'track':track}
    result=normalize_current_typhoon(payload)
    result['track_context']={'track_kind':kind,'agency':selected_agency,'issued_at':issued.isoformat(),
        'track_start':track[0]['time'],'track_end':track[-1]['time'],
        'observed_point_count':observed_count,'forecast_point_count':forecast_count}
    result['data_status']={'retrieved_at':data.get('retrieved_at'),'cache_stale':bool(data.get('cache_stale')),
        'historical_snapshot':bool(data.get('historical_snapshot')),'provisional_units':True,
        'timezone_assumed':'Asia/Shanghai','wind_input_assumed':'m/s','available_forecast_agencies':agencies}
    return result


class LiveTyphoonProvider:
    """Adapt live m/s points to the legacy matcher that expects knots."""
    def __init__(self, sync):self.sync=sync

    @property
    def available(self):return bool((self.sync.store.state('typhoons') or {}).get('list'))

    def get_current_typhoon(self, *, storm_id=None, agency=None, track_kind=None):
        return select_typhoon_payload(self.sync.typhoons(),storm_id=storm_id,agency=agency,track_kind=track_kind)
