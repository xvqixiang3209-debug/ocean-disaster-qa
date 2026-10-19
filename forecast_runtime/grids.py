from __future__ import annotations
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
import hashlib
import json
import math
import re

import h5py
import numpy as np
from .storage import BEIJING, local_now


def distance_km(lon1, lat1, lon2, lat2):
    a,b=math.radians(lat1),math.radians(lat2)
    h=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(h)))


def attribute(value):
    return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else str(value)


def inspect_grid(path):
    path=Path(path).resolve()
    with h5py.File(path,'r') as nc:
        if any(key not in nc for key in ('time','lat','lon')):
            raise ValueError('网格缺少时间或坐标')
        lon,lat=nc['lon'][:],nc['lat'][:]
        if lon.ndim!=1 or lat.ndim!=1 or len(lon)<2 or len(lat)<2:
            raise ValueError('当前支持一维经纬度规则网格')
        if not (np.all(np.isfinite(lon)) and np.all(np.isfinite(lat))):
            raise ValueError('网格坐标含缺测')
        unit=attribute(nc['time'].attrs.get('units',''))
        match=re.fullmatch(r'(hours|minutes|seconds) since (.+)',unit)
        if not match:
            raise ValueError('网格时间单位无法识别')
        time_origin='netcdf_attribute'
        try:
            base=datetime.fromisoformat(match[2].replace('Z','+00:00'))
        except ValueError:
            stamp=re.search(r'_(\d{12})_',path.name)
            if match[2]!='initial time' or not stamp:raise
            base=datetime.strptime(stamp[1],'%Y%m%d%H%M')
            time_origin='filename_assumed'
        if base.tzinfo is None:
            base=base.replace(tzinfo=BEIJING)
        base=base.astimezone(BEIJING)
        multiplier={'hours':3600,'minutes':60,'seconds':1}[match[1]]
        times=[(base+timedelta(seconds=float(v)*multiplier)).isoformat() for v in nc['time'][:]]
        if times!=sorted(set(times)) or not times:
            raise ValueError('网格时间不是唯一递增序列')
        variables=[]
        for name,obj in nc.items():
            if isinstance(obj,h5py.Dataset) and obj.shape==(len(times),len(lat),len(lon)):
                variables.append({'name':name,'unit':attribute(obj.attrs.get('units',obj.attrs.get('unit','unknown'))),
                                  'description':attribute(obj.attrs.get('long_name',obj.attrs.get('descript',''))),
                                  'shape':list(obj.shape)})
        if not variables:
            raise ValueError('缺少可读取的时间经纬度变量')
        # Filename distinguishes product and resolution; coordinates/time come from data.
        product=re.search(r'_([A-Z]+)_\d+_L([25])\.nc$',path.name)
        if not product:
            raise ValueError('网格产品名无法识别')
        components={'SSW':('u10','v10'),'OCU':('u','v')}.get(product[1])
        if components and all(any(v['name']==name and v['unit']=='m/s' for v in variables) for name in components):
            variables.append({'name':product[1],'unit':'m/s','description':'程序计算矢量模hypot('+','.join(components)+')',
                              'shape':[len(times),len(lat),len(lon)],'derived_components':list(components)})
        return {'id':hashlib.sha256(path.read_bytes()).hexdigest(),'filename':path.name,'path':str(path),
                'product':product[1],'level':'L'+product[2],'batch':base.isoformat(),'times':times,
                'valid_from':times[0],'valid_to':times[-1],'variables':variables,'bytes':path.stat().st_size,
                'bounds':[float(np.min(lon)),float(np.min(lat)),float(np.max(lon)),float(np.max(lat))],
                'registered_at':local_now().isoformat(),'time_origin':time_origin,'time_timezone_assumed':not bool(re.search(r'Z|[+-]\d\d:\d\d$',match[2]))}


class GridReader:
    def __init__(self):
        self.lock=RLock()
        self.cache=OrderedDict()
        self.max_cache_bytes=192*1024*1024

    def data(self, record, variable):
        key=(record['id'],variable)
        with self.lock:
            if key not in self.cache:
                with h5py.File(record['path'],'r') as nc:
                    def decode(name):
                        ds=nc[name];raw=ds[:];data=raw.astype(np.float32);missing=~np.isfinite(data)
                        for attr in ('_FillValue','missing_value'):
                            if attr in ds.attrs:
                                for marker in np.asarray(ds.attrs[attr]).reshape(-1):missing |= raw==marker
                        missing |= (data<=-999) | (data>=9999)
                        data=data*float(ds.attrs.get('scale_factor',1))+float(ds.attrs.get('add_offset',0));data[missing]=np.nan
                        return data
                    components={'SSW':('u10','v10'),'OCU':('u','v')}.get(variable)
                    data=np.hypot(decode(components[0]),decode(components[1])) if components else decode(variable)
                    if variable in {'WAV','SSW','OCU'}:data[data<0]=np.nan
                    value=(data,nc['lon'][:],nc['lat'][:])
                if data.nbytes>self.max_cache_bytes:
                    raise ValueError('网格太大，超过当前缓存上限')
                while self.cache and sum(v[0].nbytes for v in self.cache.values())+data.nbytes>self.max_cache_bytes:
                    self.cache.popitem(last=False)
                self.cache[key]=value
            self.cache.move_to_end(key)
            return self.cache[key]

    def point(self, record, variable, lon, lat, start, end, max_distance=20):
        x0,y0,x1,y1=record['bounds']
        if not (x0<=lon<=x1 and y0<=lat<=y1):
            return {'state':'out_of_bounds','series':[]}
        data,xs,ys=self.data(record,variable)
        i,j=int(np.argmin(abs(xs-lon))),int(np.argmin(abs(ys-lat)))
        indices=[k for k,t in enumerate(record['times']) if start<=t<=end]
        if not indices:
            return {'state':'outside_valid_time','series':[]}
        candidates=[]
        for dj in range(-2,3):
            for di in range(-2,3):
                yy,xx=j+dj,i+di
                if 0<=xx<len(xs) and 0<=yy<len(ys):
                    distance=distance_km(lon,lat,float(xs[xx]),float(ys[yy]))
                    if distance<=max_distance and np.any(np.isfinite(data[indices,yy,xx])):
                        candidates.append((distance,yy,xx))
        if not candidates:
            return {'state':'missing_grid','series':[]}
        distance,j,i=min(candidates)
        series=[{'time':record['times'][k],'value':round(float(data[k,j,i]),4)} for k in indices if np.isfinite(data[k,j,i])]
        return {'state':'ready' if series else 'missing_grid','series':series,'grid_lon':float(xs[i]),'grid_lat':float(ys[j]),
                'distance_km':round(distance,2),'method':'nearest_valid_grid','batch':record['batch'],
                'filename':record['filename'],'unit':next(v['unit'] for v in record['variables'] if v['name']==variable),
                'variable':variable,'source_id':record['id'],'available_from':record['valid_from'],'available_to':record['valid_to'],
                'time_origin':record.get('time_origin','netcdf_attribute'),
                'partial':len(series)<len(indices) or start<record['valid_from'] or end>record['valid_to']}
