from __future__ import annotations
from datetime import datetime,timedelta,timezone
from pathlib import Path
from threading import Thread,Event,local
from functools import wraps
import hashlib
import json
import math
import re

from .storage import BEIJING,ForecastStore,local_now
from .grids import GridReader,distance_km
from .sync import SourceSync
from .parsed import ParsedGridStore
from .acquisition import GridAcquisition
from .activity_sources import ActivitySources
from .recreation import activity_question,query_activity,activity_summary,resolve_location
from .spatial_queries import spatial_question,query_spatial,spatial_summary
from .impact_inputs import impact_inputs_question,standalone_impact_inputs_question,query_impact_inputs
from .historical_comparison import comparison_question,query_comparison,comparison_summary
from .official_warnings import OfficialWarnings,warning_question,warning_summary,answer_warnings
from .answer_facts import delivery_facts,fact_text,delivery_table
from marine_kb.evidence_selection import select_evidence
from .activity_policy_queries import policy_question,query_policies,policy_summary
from .routing import administrative_question,analogue_question,coastal_request as wants_coastal,grid_request as wants_grids,tide_request as wants_tide,numeric_request

COLORS={'blue':'蓝色','yellow':'黄色','orange':'橙色','red':'红色'}


def contains_result(result,kind):
    return result.get('kind')==kind or any(contains_result(row,kind) for row in result.get('items',[]))


def live_typhoon_question(question):
    if re.search(r'系统|智能体|你能|你的|数据来源|更新频率|多久更新',question):return False
    dated=bool(re.search(r'截至|截止|当前|现在|目前|今天|今日|昨天|昨日|明天|明日|本次|这次|未来|20\d{2}年\d{1,2}月',question))
    if dated and re.search(r'(?:几个|多少个).{0,8}台风|台风.{0,8}(?:活动|几个|多少个)',question):return True
    if dated and '台风' in question and re.search(r'加强|增强|最强|峰值|各机构|影响|登陆|有依据|最坏|位置|强度|移动|路径',question):return True
    return bool(re.search(r'活动台风|台风.*(?:位置|移动方向|移动速度|几个|最新路径|预报路径|强度|中心|登陆|实况|动态)|(?:当前|现在|实时).*台风',question))


def snapshot_query(function):
    @wraps(function)
    def run(self,*args,**kwargs):
        clock_owner=function.__name__=='query' and not hasattr(self._local,'request_cutoff')
        config_owner=False
        try:
            if clock_owner:
                # One clock belongs to the whole request, including recursive
                # bundles. Cutoff parsing itself can fail; clean that context
                # too, so a pooled worker cannot retain a previous request.
                self._local.request_now=local_now()
                self._local.request_time=query_time_context(
                    args[0] if args else kwargs.get('question',''),now=self._local.request_now)
                self._local.request_cutoff=self._local.request_time['cutoff']
            config_owner=getattr(self._local,'config',None) is None
            if config_owner:self._local.config=self.config()
            return function(self,*args,**kwargs)
        finally:
            if config_owner:self._local.config=None
            if clock_owner:
                for name in ('request_cutoff','request_time','request_now'):
                    if hasattr(self._local,name):delattr(self._local,name)
    return run


def affine(expression,value):
    if not expression:return value
    match=re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))x(?:([+-](?:\d+(?:\.\d*)?|\.\d+)))?',expression.strip())
    if not match:raise ValueError('不支持的岸段转换公式')
    return float(match[1])*value+float(match[2] or 0)


def _clock_hour(hour,minute,part=''):
    hour,minute=int(hour),int(minute or 0)
    if part in {'下午','晚上','晚间'} and 1<=hour<12:hour+=12
    if part=='中午' and hour<11:hour+=12
    if part in {'凌晨','早上','上午'} and hour==12:hour=0
    if not (0<=hour<=23 and 0<=minute<=59):raise ValueError('时刻需要在00:00至23:59之间')
    return hour,minute


def _same_day_clock_range(question,start):
    clock=r'(?:(上午|早上|下午|晚上|晚间|中午|凌晨)\s*)?(\d{1,2})(?:[:：](\d{1,2})|[时点](?:(\d{1,2})分?)?)'
    match=re.search(r'(?<![\d.])'+clock+r'\s*(?:至|到|~|～|—|-)\s*(次日|第二天)?\s*'+clock,question)
    if not match:return None
    p1,h1,m1,n1,next_day,p2,h2,m2,n2=match.groups()
    first=start.replace(hour=_clock_hour(h1,m1 or n1,p1)[0],minute=int(m1 or n1 or 0))
    last=start.replace(hour=_clock_hour(h2,m2 or n2,p2)[0],minute=int(m2 or n2 or 0))
    if next_day:last+=timedelta(days=1)
    if last<first:raise ValueError('结束时刻早于开始时刻，跨夜查询请注明次日或完整日期')
    return first,last


def period(question='',date=None,hours=24,now=None):
    now=(now or local_now()).astimezone(BEIJING)
    start=now.replace(minute=0,second=0,microsecond=0)
    dates=list(re.finditer(r'(?:(20\d{2})[-年/])?(\d{1,2})[-月/](\d{1,2})日?(?:[T\s]*(\d{1,2})(?:[:时点](\d{1,2})?分?))?',question))
    def stamp(match,year):
        y,m,d,h,minute=match.groups()
        return datetime(int(y or year),int(m),int(d),int(h or 0),int(minute or 0),tzinfo=BEIJING)
    if not date:
        # A meeting's "截至" time and model issue time are metadata, not the
        # requested valid interval. Find an actual adjacent date-range pair.
        for left,right in zip(dates,dates[1:]):
            between=question[left.end():right.start()]
            if not re.fullmatch(r'\s*(?:至|到|~|～|—|–|-)\s*',between):continue
            first=stamp(left,now.year);last=stamp(right,first.year)
            if not 0<=(last-first).total_seconds()<=168*3600:raise ValueError('查询区间需要按时间先后填写，且不超过168小时')
            return first,last
    explicit=re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?',question)
    monthday=re.search(r'(\d{1,2})月(\d{1,2})日',question)
    if date:
        start=datetime.fromisoformat(date)
        start=start.astimezone(BEIJING) if start.tzinfo else start.replace(tzinfo=BEIJING)
    elif explicit:
        start=datetime(*map(int,explicit.groups()),tzinfo=BEIJING)
    elif monthday:
        start=datetime(now.year,int(monthday[1]),int(monthday[2]),tzinfo=BEIJING)
    elif '后天' in question:
        start=(now+timedelta(days=2)).replace(hour=0,minute=0,second=0,microsecond=0)
    elif '明天' in question or '明日' in question:
        start=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    elif '今天' in question or '今日' in question:
        start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if not date and dates:start=stamp(dates[0],now.year)
    clock_range=_same_day_clock_range(question,start)
    if not date and clock_range:return clock_range
    duration=re.search(r'(\d{1,3})\s*(小时|天)',question)
    if duration:hours=int(duration[1])*(24 if duration[2]=='天' else 1)
    elif re.search(r'上午|下午|晚上|夜间',question):
        if '上午' in question:start=start.replace(hour=0);hours=12
        elif '下午' in question:start=start.replace(hour=12);hours=6
        else:start=start.replace(hour=18);hours=6
    if not 1<=int(hours)<=168:raise ValueError('查询时段支持1至168小时')
    return start,start+timedelta(hours=int(hours)-1)


def _cutoff_clock(day,tail):
    """Parse an explicit clock without dropping unsupported precision/zones."""
    error='截至时刻格式不支持，请使用年月日时分秒或ISO时间；时区可写北京时间、Z、UTC或±HH:MM。'
    remaining=tail.lstrip()
    iso_marker=remaining.startswith(('T','t'))
    if iso_marker:remaining=remaining[1:].lstrip()
    token=re.match(r'(?P<h>\d{1,2})(?:(?:[:：](?P<m>\d{1,2})(?:[:：](?P<s>\d{1,2})(?:\.(?P<f>\d{1,6}))?)?)|'
                   r'(?:[时点](?:(?P<cm>\d{1,2})(?:分|(?![\d秒])))?(?:(?P<cs>\d{1,2})(?:\.(?P<cf>\d{1,6}))?秒)?))?',remaining)
    if not token:
        if iso_marker or re.match(r'[:：时点分秒]|上午|下午|凌晨|UTC|GMT|Z\b|[+-]\d',remaining,re.I):raise ValueError(error)
        return day,False
    values=token.groupdict()
    remaining=remaining[token.end():].lstrip()
    zone=BEIJING
    named_local=re.match(r'北京时间',remaining)
    if named_local:
        remaining=remaining[named_local.end():].lstrip()
    else:
        explicit_zone=re.match(r'(Z|(?:UTC|GMT)(?:[+-]\d{1,2}(?::?\d{2})?)?|[+-]\d{2}:?\d{2})(?![A-Za-z0-9])',remaining,re.I)
        if explicit_zone:
            raw=explicit_zone[1].upper();remaining=remaining[explicit_zone.end():].lstrip()
            if raw in {'Z','UTC','GMT'}:zone=timezone.utc
            else:
                raw=re.sub(r'^(?:UTC|GMT)','',raw)
                sign=-1 if raw.startswith('-') else 1
                digits=raw[1:].replace(':','')
                hours=int(digits) if len(digits)<=2 else int(digits[:-2])
                minutes=0 if len(digits)<=2 else int(digits[-2:])
                if minutes>59 or hours>23:raise ValueError(error)
                zone=timezone(sign*timedelta(hours=hours,minutes=minutes))
    # A partially consumed clock is an error, never a date-only live query.
    if re.match(r'[\d:：.时点分秒+\-]|(?:UTC|GMT)|Z\b|[A-Za-z]{1,10}\b',remaining,re.I):raise ValueError(error)
    try:
        fraction=values['f'] or values['cf'] or ''
        value=day.replace(hour=int(values['h']),minute=int(values['m'] or values['cm'] or 0),
                          second=int(values['s'] or values['cs'] or 0),
                          microsecond=int(fraction.ljust(6,'0') or 0),tzinfo=zone)
    except ValueError as exc:
        raise ValueError(error) from exc
    return value.astimezone(BEIJING),True


def query_time_context(question,now=None):
    """Separate a current request from a genuinely historical/future cutoff.

    A date without a time means end-of-day for an archive query. Today's
    date, however, means the request instant; it must refresh the live source.
    Keep the *requested* future instant separately from the effective cutoff
    used to choose already-issued forecast products.
    """
    reference=now or local_now()
    reference=reference.astimezone(BEIJING) if reference.tzinfo else reference.replace(tzinfo=BEIJING)
    date_pattern=r'((?:20\d{2})[-年/]\d{1,2}[-月/]\d{1,2}日?)'
    match=re.search(r'(?:截至|截止)\s*'+date_pattern,question)
    if not match:
        match=re.search(r'(?:截至|截止)\s*(\d{1,2}月\d{1,2}日)',question)
    if not match and re.search(r'(?:截至|截止)\s*\d',question):
        raise ValueError('截至日期格式不支持，请使用YYYY年MM月DD日、YYYY-MM-DD或MM月DD日。')
    # A standalone dated activity/centre observation can omit "as of".
    # Do not steal the valid date of a forecast or another marine subtask.
    implicit_observation=(live_typhoon_question(question)
            and not re.search(r'预报|预计|未来|登陆|增强|加强|影响|最坏|有依据',question)
            and not any((wants_coastal(question),wants_grids(question),wants_tide(question))))
    if not match and implicit_observation:
        match=re.search(date_pattern,question)
    relative=None
    if match:
        parts=list(map(int,re.findall(r'\d+',match[1])))
        if len(parts)==2:parts.insert(0,reference.year)
        day=datetime(*parts,tzinfo=BEIJING)
        tail=question[match.end():]
    else:
        relative=re.search(r'(?:截至|截止)\s*(今天|今日|昨天|昨日|明天|明日)',question)
        if not relative and implicit_observation:
            relative=re.search(r'(今天|今日|昨天|昨日|明天|明日)',question)
        if not relative:
            return {'mode':'current','cutoff':None,'requested_at':None,'now':reference}
        offset={'今天':0,'今日':0,'昨天':-1,'昨日':-1,'明天':1,'明日':1}[relative[1]]
        day=(reference+timedelta(days=offset)).replace(hour=0,minute=0,second=0,microsecond=0)
        tail=question[relative.end():]
    value,explicit_clock=_cutoff_clock(day,tail)
    if not explicit_clock:
        value=(reference if value.date()==reference.date()
               else value.replace(hour=23,minute=59,second=59))
    mode='future' if value>reference else 'historical' if value<reference else 'current'
    return {'mode':mode,'cutoff':min(value,reference),'requested_at':value,'now':reference}


def as_of_cutoff(question,now=None):
    return query_time_context(question,now=now)['cutoff']


class ForecastRuntime:
    def __init__(self,root,assumptions_path=None,*,coastal_acquisition=False):
        self.store=ForecastStore(root)
        self.config_path=Path(assumptions_path or self.store.root/'assumptions.json')
        if not self.config_path.exists():
            self.config_path.write_bytes((Path(__file__).parent/'assumptions.json').read_bytes())
        self.reader=GridReader()
        self._local=local()
        self.sync=SourceSync(self.store,self.config)
        self.parsed=ParsedGridStore(self.store,self.reader)
        self.acquisition=GridAcquisition(self.store,self.sync,self.config)
        self.coastal_acquisition=coastal_acquisition
        self.warnings=OfficialWarnings(self.store)
        self.activity_sources=ActivitySources(self.store)
        from spatial_runtime import SpatialRuntime
        self.spatial=SpatialRuntime(self.store.root.parent/'spatial_runtime')
        self.stop=Event()
        self.worker=None
        self.warning_worker=None

    def config(self):
        if getattr(self._local,'config',None) is not None:return self._local.config
        config=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        if config['swl_kind'] not in {'surge','total_water_level'}:
            raise ValueError('SWL口径仅支持surge或total_water_level')
        if config['tide_datum'] not in {'raw_already_1985','subtract_base85','add_base85'}:
            raise ValueError('天文潮订正配置无效')
        if config['threshold_unit']!='cm' or config['timezone']!='+08:00':
            raise ValueError('当前适配层仅支持厘米阈值及北京时间')
        if config['formula_applies_to']!='astronomical_tide':
            raise ValueError('当前公式适配仅支持天文潮转换')
        if config['swl_datum']!='1985' or config['threshold_datum']!='1985':
            raise ValueError('当前试算仅支持1985统一基准；其他基准需要明确转换参数')
        tide_mode={'raw_already_1985':'原值按1985基准使用，不再次订正','subtract_base85':'原值减站点偏移量','add_base85':'原值加站点偏移量'}[config['tide_datum']]
        config['notes']=[
            '试算口径：天文潮'+tide_mode+'，逐站单独配置'+str(len(config.get('station_tide_overrides',{})))+'项；高程基准尚未核定。',
            '试算口径：SWL='+('纯增水，与同时刻天文潮相加' if config['swl_kind']=='surge' else '总潮位，直接使用，不再次叠加天文潮')+'；单位米。',
            '试算口径：本次SQL阈值按厘米、1985基准使用，尚未完成业务版本核定。',
            '试算口径：所有无时区时间采用北京时间。',
            '试算口径：NC仅写initial time时，以文件名批次作为时间基点；SSW由u10/v10矢量模计算，按10米阵风解释，不推定平均风。',
            '岸段公式作用于天文潮；缺少直接关联时'+('允许80公里内最近可用站近似（站点坐标精度有限）' if config['nearest_reference_enabled'] else '保留缺数，不替代站点')+'。',
            'MWD描述冲突，未解读为波向；缺测和无效阈值不补造；试算不构成正式预警。']
        return config

    def request_now(self):
        return getattr(self._local,'request_now',None) or local_now()

    def config_id(self):
        return hashlib.sha256(json.dumps(self.config(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]

    def start_worker(self):
        if self.worker or not self.config().get('sync_enabled'):return
        def run():
            while not self.stop.wait(3):
                if self.config().get('sync_enabled') and self.config()['enabled']:
                    self.sync.run()
                if self.stop.wait(max(300,self.config()['sync_interval_seconds'])):break
        self.worker=Thread(target=run,name='marine-forecast-sync',daemon=True)
        self.worker.start()

    def start_warning_worker(self):
        """Keep warning publication snapshots independently of large NC downloads."""
        if self.warning_worker:return
        def run():
            while not self.stop.wait(3):
                try:
                    self.warnings.ensure_year(local_now().year)
                    self.store.state('official_warning_sync',{'state':'completed','at':local_now().isoformat()})
                except Exception as exc:
                    self.store.state('official_warning_sync',{'state':'failed','at':local_now().isoformat(),'error_type':type(exc).__name__})
                if self.stop.wait(900):break
        self.warning_worker=Thread(target=run,name='official-warning-archive',daemon=True)
        self.warning_worker.start()

    @snapshot_query
    def status(self):
        records=self.store.grids()
        products={r['product'] for r in records}
        return {'enabled':self.config()['enabled'],'trial':True,'assumption_id':self.config_id(),'assumptions':self.config(),
                'official_warnings':self.warnings.status(),
                'spatial':self.spatial.status(),
                'import':self.store.state('import'),'sync':self.store.state('sync'),'grid_files':len(records),
                'products':sorted(products),'typhoon_cache_at':(self.store.state('typhoons') or {}).get('retrieved_at'),
                'batches':[self.public_grid(r) for r in sorted(records,key=lambda r:r['batch'],reverse=True)[:20]]}

    @staticmethod
    def public_grid(record):
        return {k:record[k] for k in ('id','filename','product','level','batch','valid_from','valid_to','bounds','variables')}

    def sites(self):
        return self.store.tide_files()

    def coasts(self):
        return self.store.entities('coast')

    def base_result(self,start,end):
        return {'trial':True,'label':'试算结果（假设口径）','assumption_id':self.config_id(),
                'swl_kind':self.config()['swl_kind'],
                'assumptions':self.config()['notes'],'as_of':self.request_now().isoformat(),
                'start':start.isoformat(),'end':end.isoformat(),'timezone':'Asia/Shanghai'}

    def corrected(self,profile,value):
        config=self.config()
        mode=config.get('station_tide_overrides',{}).get(profile['code'],config['tide_datum'])
        if mode=='raw_already_1985':return value
        offset=profile.get('base85_cm')
        if offset is None:raise ValueError('该站缺少基准订正偏移量')
        if mode=='subtract_base85':return value-float(offset)
        if mode=='add_base85':return value+float(offset)
        raise ValueError('逐站订正模式无效')

    def find_site(self,name):
        files=self.sites()
        exact=[f for f in files if name.casefold() in {f['code'].casefold(),str(f.get('station_id') or '').casefold()}]
        if exact:return exact[0]
        matches=[f for f in files if name in (f.get('station_name') or '') or name in f['name']]
        if len(matches)==1:return matches[0]
        if len(matches)>1:raise ValueError('存在同名站，请选择：'+'、'.join(f['code']+' '+f['name'] for f in matches))
        raise ValueError('没有找到该潮位站')

    @snapshot_query
    def tide(self,name,start,end):
        profile=self.find_site(name)
        values=self.store.hours(profile['code'],start.isoformat(),end.isoformat())
        extremes=self.store.extrema(profile['code'],start.isoformat(),end.isoformat())
        rows=[{**r,'raw_value_cm':r['value_cm'],'value_cm':round(self.corrected(profile,r['value_cm']),2)} for r in values]
        highs=[{**r,'raw_value_cm':r['value_cm'],'value_cm':round(self.corrected(profile,r['value_cm']),2)} for r in extremes if r['kind']=='high']
        lows=[{**r,'raw_value_cm':r['value_cm'],'value_cm':round(self.corrected(profile,r['value_cm']),2)} for r in extremes if r['kind']=='low']
        return {**self.base_result(start,end),'kind':'tide','site':profile,'unit':'cm','datum':'1985（假设）',
                'state':'ready' if rows else 'no_data','series':rows,'high_tides':highs,'low_tides':lows,
                'partial':len(rows)<int((end-start).total_seconds()/3600)+1,
                'maximum':max(rows,key=lambda r:r['value_cm']) if rows else None,
                'minimum':min(rows,key=lambda r:r['value_cm']) if rows else None}

    @snapshot_query
    def grid(self,product,lon,lat,start,end,batch=None,as_of=None,max_distance=None,freshness_at=None):
        token={'wave':'WAV','wave_direction':'MWD','surge':'SWL','wind':'SSW','temperature':'SST','current':'OCU'}.get(product,product)
        family='WAV' if token=='MWD' else token
        records=[r for r in self.store.grids() if r['product']==family and any(v['name']==token for v in r['variables']) and (batch is None or r['batch']==batch)]
        rejected_units=[]
        if token=='MWD':
            accepted=[]
            for r in records:
                unit=next(v['unit'] for v in r['variables'] if v['name']==token)
                if unit.strip().lower() in {'degree','degrees','deg','°'}:accepted.append(r)
                else:rejected_units.append({'filename':r['filename'],'declared_unit':unit})
            records=accepted
        # Select one batch for the whole requested interval; do not splice runs.
        # Files can arrive before their named issue time. Use the latest issued
        # run, never let a future filename hide an already usable forecast.
        cutoffs=[self.request_now()]+[t for t in (as_of,getattr(self._local,'request_cutoff',None)) if t is not None]
        cutoff=min(cutoffs)
        issued=[r for r in records if datetime.fromisoformat(r['batch'])<=cutoff]
        records=sorted(issued,key=lambda r:(r['batch'],r['level']=='L5'),reverse=True)
        candidates=[r for r in records if r['valid_from']<=end.isoformat() and r['valid_to']>=start.isoformat()
                    and r['bounds'][0]<=lon<=r['bounds'][2] and r['bounds'][1]<=lat<=r['bounds'][3]]
        base={**self.base_result(start,end),'kind':'grid','product':token,'lon':lon,'lat':lat}
        if not candidates:return {**base,'state':'no_data','series':[],'rejected_variable_units':rejected_units,
                                  'reason':'MWD变量的角度单位未获支持，未将长度单位当成角度' if rejected_units and token=='MWD' else '没有覆盖该位置和时段的产品批次'}
        complete=[r for r in candidates if r['valid_from']<=start.isoformat() and r['valid_to']>=end.isoformat()]
        # A land/fill cell in the preferred resolution must not hide valid
        # cells in another resolution of the same selected issue.
        point_candidates=complete or candidates
        selected_issue=point_candidates[0]['batch']
        for record in [r for r in point_candidates if r['batch']==selected_issue]:
            value=self.parsed.point(record,token,lon,lat,start.isoformat(),end.isoformat(),
                                    max_distance if max_distance is not None else self.config()['max_grid_distance_km'])
            if value.get('state')=='ready':break
        issued_at=datetime.fromisoformat(record['batch'])
        reference=max(issued_at,min(cutoff,freshness_at)) if freshness_at is not None else cutoff
        age=(reference-issued_at).total_seconds()/3600
        series=value.get('series') or []
        expected=sum(start.isoformat()<=t<=end.isoformat() for t in record['times'])
        coverage={'available_samples':len(series),'expected_samples':expected,'complete':not value.get('partial',True) and bool(series),
                  'first_time':series[0]['time'] if series else None,'last_time':series[-1]['time'] if series else None}
        result={**base,**value,'stale':age>self.config()['max_batch_age_hours'],'batch_age_hours':round(age,2),
                'freshness_reference_at':reference.isoformat(),'batch_age_at_query_hours':round((cutoff-issued_at).total_seconds()/3600,2),
                'variable_description':next(v.get('description','') for v in record['variables'] if v['name']==token),
                'series_coverage':coverage,
                'rejected_variable_units':rejected_units,
                'future_batch':age<0,'maximum':max(value['series'],key=lambda r:r['value']) if value['series'] else None,
                'minimum':min(value['series'],key=lambda r:r['value']) if value['series'] else None}
        if token=='MWD':
            # Cyclic directions are neither a linear severity range nor an
            # arithmetic mean across the discontinuity at north.
            result.update(maximum=None,minimum=None,direction_samples=([dict(series[0],label='首个有效时刻'),dict(series[-1],label='末个有效时刻')] if series else []),
                          direction_convention='NC仅声明Mean Wave Direction与degree；零点、旋转方向及来向/去向尚未核定，不转换成东南西北。')
        return result

    def reference(self,coast):
        profiles=[f for f in self.sites() if f['state']=='ready']
        sites=self.store.entities('site')
        for field in ('NumStation','ObsOriStid','FcstStation'):
            ref=str(coast.get(field) or '')
            if not ref:continue
            candidates=[s for s in sites if ref in {str(s.get(k) or '') for k in ('StationID','RelativeStation','NumStation','DataFile')}]
            for site in candidates:
                profile=next((f for f in profiles if f.get('station_id')==site['StationID']),None)
                if profile:return profile,field+'关联（'+profile['mapping']+'）'
        if self.config()['nearest_reference_enabled'] and coast['lon'] is not None:
            candidates=[(distance_km(coast['lon'],coast['lat'],f['lon'],f['lat']),f) for f in profiles if f['lon'] is not None]
            if candidates:
                distance,profile=min(candidates,key=lambda r:(r[0],r[1]['code']))
                if distance<=self.config()['max_reference_distance_km']:
                    return profile,'近似参考站，粗略坐标距离'+str(round(distance,1))+'公里'
        return None,'没有可用参考站关联'

    @snapshot_query
    def coastal(self,start,end,region='',coast_id=None):
        province=region.strip() in {'浙江','浙江省','浙江沿海','全省'}
        zj_cities={'杭州市','嘉兴市','宁波市','舟山市','台州市','温州市'}
        selected=[c for c in self.coasts() if (not region or province and c.get('Prefecture') in zj_cities or any(region in str(c.get(k) or '') for k in ('name','Prefecture','County')))
                  and (coast_id is None or str(c['Id'])==str(coast_id))]
        now=self.request_now();cutoff=min(now,getattr(self._local,'request_cutoff',None) or now)
        acquisition=None
        if self.coastal_acquisition and self.config().get('sync_enabled'):
            acquisition=self.acquisition.ensure(start,end,products=['SWL'],as_of=getattr(self._local,'request_cutoff',None))
        available=[r for r in self.store.grids() if r['product']=='SWL' and r['valid_from']<=end.isoformat() and r['valid_to']>=start.isoformat()
                   and datetime.fromisoformat(r['batch'])<=cutoff]
        complete=[r for r in available if r['valid_from']<=start.isoformat() and r['valid_to']>=end.isoformat()]
        selected_batch=max((r['batch'] for r in (complete or available)),default=None)
        results=[]
        for coast in selected:
            row={'id':coast['Id'],'name':coast['name'],'city':coast['Prefecture'],'county':coast['County'],
                 'lon':coast['lon'],'lat':coast['lat'],'thresholds_cm':coast['thresholds_cm'],'geometry':coast['geometry'],
                 'state':'no_data','level':None,'maximum':None}
            if coast['lon'] is None:
                row['reason']='缺少岸段位置';results.append(row);continue
            grid=self.grid('surge',coast['lon'],coast['lat'],start,end,batch=selected_batch,freshness_at=min(start,cutoff))
            if grid.get('unit') not in {'m','meter','metre','meters','metres'} or grid['state']!='ready':
                row['reason']=('限定邻近格点搜索内没有有效SWL数值，未用陆地或缺测值补齐' if grid.get('state')=='missing_grid' else
                               '没有覆盖所查位置及有效时段的SWL文件' if grid.get('state')=='no_data' else '水位网格不可用或单位不支持')
                row['grid_state']=grid.get('state');results.append(row);continue
            if grid.get('stale') or grid.get('future_batch'):
                row['reason']='在所查预报参考时刻产品已超过更新时限或尚未起报';results.append(row);continue
            swl_series=[{'time':v['time'],'swl_cm':round(v['value']*100,2)} for v in grid['series'] if abs(v['value'])<=30]
            row.update(batch=grid['batch'],filename=grid['filename'],source_id=grid.get('source_id'),
                       grid_lon=grid['grid_lon'],grid_lat=grid['grid_lat'],grid_distance_km=grid['distance_km'],
                       freshness_reference_at=grid['freshness_reference_at'],batch_age_hours=grid['batch_age_hours'],
                       batch_age_at_query_hours=grid['batch_age_at_query_hours'],
                       swl_series=swl_series,swl_maximum=max(swl_series,key=lambda v:v['swl_cm']) if swl_series else None,
                       swl_partial=len(swl_series)<int((end-start).total_seconds()/3600)+1)
            profile,method=self.reference(coast)
            tidal={r['time']:self.corrected(profile,r['value_cm']) for r in self.store.hours(profile['code'],start.isoformat(),end.isoformat())} if profile else {}
            values=[]
            for item in grid['series']:
                if abs(item['value'])>30:continue
                if self.config()['swl_kind']=='total_water_level':
                    total=item['value']*100;tide_value=None
                elif item['time'] in tidal:
                    tide_value=affine(coast.get('Formula'),tidal[item['time']]);total=tide_value+item['value']*100
                else:continue
                values.append({'time':item['time'],'total_cm':round(total,2),'tide_cm':round(tide_value,2) if tide_value is not None else None,
                               'swl_cm':round(item['value']*100,2)})
            if not values:
                row['reason']='未取得同时刻天文潮与水位；'+method;results.append(row);continue
            maximum=max(values,key=lambda v:v['total_cm'])
            level=next((color for color in ('red','orange','yellow','blue') if row['thresholds_cm'][color] is not None and maximum['total_cm']>=row['thresholds_cm'][color]),'below')
            threshold=row['thresholds_cm'].get(level)
            row.update(state='ready',level=level,maximum=maximum,exceedance_cm=round(maximum['total_cm']-threshold,2) if threshold is not None else None,
                reference_code=profile['code'] if profile else None,reference_method=method,formula=coast.get('Formula'),batch=grid['batch'],filename=grid['filename'],
                grid_lon=grid['grid_lon'],grid_lat=grid['grid_lat'],grid_distance_km=grid['distance_km'],
                partial=len(values)<int((end-start).total_seconds()/3600)+1,valid_hours=len(values),series=values)
            results.append(row)
        counts={color:sum(r['level']==color for r in results) for color in ('red','orange','yellow','blue','below')}
        ready=[r for r in results if r['state']=='ready']
        return {**self.base_result(start,end),'kind':'coastal','state':'ready' if ready else 'partial' if any(r.get('swl_series') for r in results) else 'no_data','unit':'cm',
                'swl_kind':self.config()['swl_kind'],'counts':counts,'requested_count':len(results),'usable_count':len(ready),
                'selected_batch':selected_batch,
                'freshness_reference_at':min(start,cutoff).isoformat(),
                'forecast_interpretation':'单一起报批次在所查有效时段的预报，不是实况；历史时段不因查询日变晚自动失效。',
                'data_acquisition':acquisition,
                'swl_usable_count':sum(bool(r.get('swl_series')) for r in results),
                'swl_missing_count':sum(not r.get('swl_series') for r in results),
                'missing_count':len(results)-len(ready),'results':results,
                'partial_count':sum(r['partial'] for r in ready),
                'maximum':max(ready,key=lambda r:r['maximum']['total_cm'])['id'] if ready else None}

    def matches(self,question,sid=None):
        if not self.config()['enabled']:return False
        if warning_question(question):return True
        if sid:return False
        if re.search(r'系统|智能体|你能|你的|数据来源|多久更新|更新频率',question):return False
        if administrative_question(question):return False
        if re.search(r'风力.{0,8}对应.{0,8}浪|船只.{0,8}等级.{0,8}出海',question) and not numeric_request(question):return False
        if policy_question(question):return True
        if activity_question(question):return True
        if spatial_question(question):return True
        if impact_inputs_question(question):return True
        if comparison_question(question):return True
        named_years=re.findall(r'((?:19|20)\d{2})年',question)
        if named_years and all(int(y)<local_now().year for y in named_years):return False
        if analogue_question(question):return True
        if numeric_request(question):return True
        if live_typhoon_question(question) and re.search(r'截至|当前|现在|目前|本次|这次|20\d{2}年\d{1,2}月',question):return True
        if re.search(r'什么是|定义|是什么意思|什么不同|有何不同|区别|差别|一回事|原理|如何计算|怎么算|计算方法|如何划分|怎么划分|强度等级有哪些|季节|成因|规范|标准|应急|响应|五停|预警.{0,8}含义|'
                     r'重现期|年一遇|淹没|易淹|倒灌|灾损|损失|误差|准确率|同化|设计|爬高|情景|海平面上升|地面沉降|'
                     r'是否安全|可以出海|能出海|出去看看|风险区|隐患|高风险|危险|避险|防御|避护|停航|降级|终止|预警解除|数据可用|有没有缺测|哪些潮位站|哪些.*浮标|雷达|卫星',question):return False
        years=re.findall(r'(?:19|20)\d{2}',question)
        if years and all(int(y)<local_now().year for y in years):return False
        if live_typhoon_question(question):return True
        if re.search(r'天文潮|高潮|低潮|潮汐|退潮|涨潮|潮时|岸段|潮位|增水|浪高|波高|风力|风速|风浪|海温|流速',question):
            return bool(re.search(r'未来|明天|后天|今天|当前|(?<!出)现在|本次|这次|预报|查询|多少|几点|哪个|哪些|最大|\d{1,2}月\d{1,2}日|20\d{2}[-/]',question))
        return False

    @snapshot_query
    def query(self,question,_only=None,_point=None):
        if _only=='warnings' or (_only is None and warning_question(question)):
            return self.warnings.query(question)
        start,end=period(question,now=getattr(self,'request_now',local_now)())
        if _only is None and re.search(r'影响研判报告|影响研判专报|台风影响报告',question) and re.search(r'浙江|舟山|宁波|台州|温州|嘉兴',question):
            cutoff=getattr(self._local,'request_cutoff',None)
            if cutoff and not re.search(r'未来|(?<!截)至|到|小时|日[0-9零一二三四五六七八九十]*时',question):
                start=cutoff.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                end=start+timedelta(hours=72)
            window=start.strftime('%Y年%m月%d日%H时')+'至'+end.strftime('%Y年%m月%d日%H时')
            region=next((r for r in ('舟山','宁波','台州','温州','嘉兴') if r in question),None)
            regions=[region] if region else ['舟山','宁波','台州','温州','嘉兴']
            items=[self.query(question,_only='typhoons'),self.query(window+' '+(region or '浙江')+'沿海最大增水最高潮位',_only='coastal')]
            items.extend(self.query(window+' '+r+'海域风速浪高',_only='grids') for r in regions)
            return {**self.base_result(start,end),'kind':'bundle','items':items,'state':'ready' if any(v['state']=='ready' for v in items) else 'no_data',
                    'scope_note':'海况为指定地区代表格点和岸段预报，不是全省面积极值或浙江影响实况；NC未核定台风场景归属，不能据此认定均由该台风造成。'}
        if _only in {None,'comparison'} and comparison_question(question):
            comparison=query_comparison(self,question,start,end)
            items=[comparison]
            if _only is None:
                if activity_question(question):items.append(self.query(question,_only='activity',_point=_point))
                if spatial_question(question):items.append(self.query(question,_only='spatial',_point=_point))
                if policy_question(question):items.append(self.query(question,_only='policy'))
            if len(items)==1:return comparison
            return {**self.base_result(start,end),'kind':'bundle','items':items,
                    'state':'ready' if any(item['state']=='ready' for item in items) else 'no_data'}
        if _only is None and impact_inputs_question(question):
            impact=query_impact_inputs(self,question,start,end)
            if standalone_impact_inputs_question(question):return impact
            items=[]
            active=activity_question(question)
            if active:items.append(self.query(question,_only='activity',_point=_point))
            if spatial_question(question):items.append(self.query(question,_only='spatial',_point=_point))
            if wants_grids(question) and not active:items.append(self.query(question,_only='grids',_point=_point))
            if wants_tide(question) and not wants_coastal(question) and not active:items.append(self.query(question,_only='tide',_point=_point))
            if live_typhoon_question(question):items.append(self.query(question,_only='typhoons'))
            if analogue_question(question):items.append(self.query(question,_only='analogue'))
            if policy_question(question):items.append(self.query(question,_only='policy'))
            if not items:return impact
            items.append(impact)
            return {**self.base_result(start,end),'kind':'bundle','purpose':'impact_boundary_inputs',
                    'impact_assessment':impact['impact_assessment'],'items':items,
                    'state':'ready' if any(row['state']=='ready' for row in items) else 'no_data'}
        if _only in {None,'analogue'} and analogue_question(question):
            from .analogue import query_analogue
            analogue=query_analogue(self,question,start,end)
            items=[analogue]
            if _only is None:
                if activity_question(question):
                    activity_clauses=[part for part in re.split(r'[？?！!；;。]+',question) if activity_question(part)]
                    # An explicitly dated activity is independent of the track
                    # query's as-of date; its request cutoff remains on _local.
                    explicit_date=r'20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}|\d{1,2}月\d{1,2}日'
                    activity_request='；'.join(activity_clauses) if any(re.search(explicit_date,part) for part in activity_clauses) else question
                    items.append(self.query(activity_request,_only='activity',_point=_point))
                if spatial_question(question):items.append(self.query(question,_only='spatial',_point=_point))
                if policy_question(question):items.append(self.query(question,_only='policy'))
            if len(items)==1:return analogue
            return {**self.base_result(start,end),'kind':'bundle','items':items,
                    'state':'ready' if any(item['state']=='ready' for item in items) else 'no_data'}
        if _only in {None,'policy'} and policy_question(question):return query_policies(self,question,start,end)
        if _only in {None,'activity'} and activity_question(question):
            from .task_plan import activity_tasks, single_target_question
            plan=activity_tasks(question,coasts=self.coasts())
            items=[]
            for task in plan['tasks']:
                if task['state']=='not_executed':
                    items.append({**self.base_result(start,end),'kind':'activity','state':'not_executed',
                                  'task_object':task['object'],'reason':task['reason']})
                    continue
                try:
                    task_start,task_end=period(task['question'])
                    child=query_activity(self,task['question'],task_start,task_end)
                except (ValueError,KeyError,OSError) as exc:
                    child={**self.base_result(start,end),'kind':'activity','state':'unavailable',
                           'reason':'该地点查询失败：'+type(exc).__name__}
                child['task_object']=task['object']
                child['task_id']=task['id']
                task['state']=child['state']
                items.append(child)
            if not items:
                items=[query_activity(self,single_target_question(question),start,end)]
            if _only is None and spatial_question(question):items.append(query_spatial(self,question,start,end))
            if _only is None and re.search(r'超警|哪些岸段|哪个岸段|最高潮位|最大增水',question):
                items.append(self.query(question,_only='coastal'))
            if _only is None and live_typhoon_question(question):items.append(self.query(question,_only='typhoons'))
            if len(items)>1:
                return {**self.base_result(start,end),'kind':'bundle','items':items,
                        'task_plan':plan,
                        'state':'ready' if any(item['state']=='ready' for item in items) else 'no_data'}
            return items[0]
        if _only=='spatial' or (_only is None and spatial_question(question)):
            return query_spatial(self,question,start,end)
        live=live_typhoon_question(question)
        coastal_request=wants_coastal(question)
        grid_request=wants_grids(question)
        tidal_request=wants_tide(question) and not coastal_request
        if _only is None:
            facets=[]
            if live:facets.append('typhoons')
            if coastal_request or (grid_request and re.search(r'潮位|增水',question)):facets.append('coastal')
            elif tidal_request:facets.append('tide')
            if grid_request:facets.append('grids')
            if len(facets)>1:
                items=[self.query(question,_only=facet,_point=_point) for facet in facets]
                return {**self.base_result(start,end),'kind':'bundle','items':items,
                        'state':'ready' if any(item['state']=='ready' for item in items) else 'no_data'}
        if (_only=='typhoons') or (_only is None and live):
            identities=re.findall(r'(?:接口标识|typhoonID|台风编号)\s*[:：]?\s*(\d{4,6})',question,re.I)
            cutoff=getattr(self._local,'request_cutoff',None)
            instant=self.request_now()
            timing=getattr(self._local,'request_time',None) or query_time_context(question,now=instant)
            mode=timing['mode']
            if mode=='future':
                return {**self.base_result(instant,instant),'trial':False,'label':'台风接口查询结果','kind':'typhoons','list':[],
                        'state':'future_observation_unavailable','source':'浙江海洋预报网活动台风接口',
                        'source_url':'https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons',
                        'snapshot_scope':'missing','historical_snapshot':False,'cache_stale':False,
                        'activity_count_verified':False,'activity_scope_warning_required':True,
                        'activity_list_count':None,'activity_list_count_verified':False,
                        'selection_scope':'named_subset' if identities else 'all_active',
                        'request_question':question,'query_time_mode':mode,
                        'requested_as_of':timing['requested_at'].isoformat(),
                        'effective_as_of':instant.isoformat(),'requested_forecast':False,
                        'missing_inputs':['所问时刻尚未到达，当前活动列表不能证明未来的台风数量、中心实况或移动状态；预报路径须另行查询并标明预报属性。']}
            past_cutoff=mode=='historical'
            data=self.store.typhoon_snapshot(cutoff,identities,question,allow_partial=True) if past_cutoff else None
            if data is None and past_cutoff:
                data={'list':[],'state':'historical_snapshot_unavailable','cache_stale':False,
                    'source':'浙江海洋预报网','historical_snapshot':False,'snapshot_scope':'missing',
                    'source_url':'https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons',
                    'missing_inputs':['没有保存截至查询时刻之前的活动台风接口原始快照，不能用之后取得的列表回填当时活动数量。']}
            elif data is None:
                data=self.sync.typhoons()
                data={**data,'snapshot_scope':'complete_api_response' if data.get('state')!='unavailable' else 'missing'}
            activity_list_count=len(data.get('list',[]))
            named=[storm for storm in data.get('list',[]) if str(storm.get('nameCN') or '\0') in question
                   or str(storm.get('nameEN') or '\0').lower() in question.lower()]
            if identities:data={**data,'list':[storm for storm in data.get('list',[]) if str(storm.get('typhoonID')) in identities]}
            elif named:data={**data,'list':named}
            incomplete=data.get('snapshot_scope')!='complete_api_response'
            data={**data,'activity_count_verified':not incomplete,
                  'activity_scope_warning_required':incomplete and not identities and not named,
                  'activity_list_count':activity_list_count if not incomplete else None,
                  'activity_list_count_verified':not incomplete,
                  'selection_scope':'named_subset' if identities or named else 'all_active',
                  'request_question':question,'query_time_mode':mode}
            point_cutoff=cutoff or instant
            if point_cutoff:
                selected=[]
                for storm in data.get('list',[]):
                    points=[]
                    for p in storm.get('points') or []:
                        t=datetime.fromisoformat(p['time']);t=t.astimezone(BEIJING) if t.tzinfo else t.replace(tzinfo=BEIJING)
                        if t<=point_cutoff:points.append(p)
                    # Membership of the API's activity list is independent of
                    # whether a storm has a usable centre observation.
                    selected.append({**storm,'points':points})
                data={**data,'list':selected,'requested_as_of':point_cutoff.isoformat(),
                      'effective_as_of':point_cutoff.isoformat()}
            state=data.get('state') or ('ready' if data['list'] else 'no_active_typhoons')
            return {**self.base_result(instant,instant),'trial':False,'label':'台风接口查询结果',
                    'kind':'typhoons',**data,'state':state,'requested_forecast':bool(re.search(r'预报|路径|未来|预计|登陆|增强|加强|影响|最坏|有依据',question))}
        coast=next((c for c in sorted(self.coasts(),key=lambda c:len(c['name']),reverse=True) if c['name'] in question),None)
        named_location=resolve_location(self,question)
        if coast is None and named_location and named_location.get('coast_id'):
            coast=next((c for c in self.coasts() if str(c['Id'])==named_location['coast_id']),None)
        region=next((x for x in ('嘉兴','宁波','台州','温州','舟山') if x in question),'')
        if _only=='coastal' or (_only is None and coastal_request):
            # A point's city in another subquestion must not narrow an explicit
            # province-wide coast ranking (e.g. 舟山风浪 + 浙江岸段潮位).
            province_wide=bool(re.search(r'浙江(?:省)?(?:沿海|各|全省)?岸段|浙江(?:省)?沿海|全省.{0,8}岸段',question))
            coastal_region='' if province_wide else region
            result=self.coastal(start,end,coastal_region,coast['Id'] if coast and not province_wide else None)
            result['cyclone_attribution_verified']=False
            result['attribution_note']='NC的特定台风场景归属尚未核定；这限制台风因果归属，不影响报告已取得的网格预报数值。'
            result['requested_metrics']={'surge':bool(re.search(r'增水|SWL',question,re.I)),
                                         'total':bool(re.search(r'潮位|水位|超警|叠加',question))}
            return result
        profiles=self.sites()
        profile=next((p for p in sorted(profiles,key=lambda p:len(p['code']),reverse=True) if p['code'].casefold() in question.casefold()),None)
        if profile is None:
            named=[p for p in profiles if p['name'] in question]
            if named:profile=max(named,key=lambda p:len(p['name']))
        if profile is None:
            named=[p for p in profiles if p.get('station_name') and p['station_name'] in question]
            if named:
                longest=max(len(p['station_name']) for p in named)
                named=[p for p in named if len(p['station_name'])==longest]
                if len(named)>1 and coast is None:
                    return {**self.base_result(start,end),'kind':'clarification','state':'needs_location',
                            'reason':'有同名站，请明确：'+'；'.join(p['name']+'（'+p['code']+'）' for p in named)}
                profile=named[0]
        if _only=='tide' or (_only is None and tidal_request):
            if coast:
                profile,method=self.reference(coast)
                if profile:
                    result=self.tide(profile['code'],start,end);result['reference_for']=coast['name'];result['reference_method']=method
                    if coast.get('Formula'):
                        for row in result['series']+result['high_tides']+result['low_tides']:
                            row['value_cm']=round(affine(coast['Formula'],row['value_cm']),2)
                        result['maximum']=max(result['series'],key=lambda r:r['value_cm']) if result['series'] else None
                        result['minimum']=min(result['series'],key=lambda r:r['value_cm']) if result['series'] else None
                        result['formula']=coast['Formula']
                    return result
            if profile:return self.tide(profile['code'],start,end)
            return {**self.base_result(start,end),'kind':'clarification','state':'needs_location','reason':'请指定潮位站，例如“坎门站明天几点高潮”，或选择站点代码。'}
        points=re.findall(r'(?:东经|经度|lon)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:度|°|E)?[^\d]{0,15}(?:北纬|纬度|lat)\s*[:：]?\s*(\d+(?:\.\d+)?)',question,re.I)
        if _point is None and len(points)>1:
            items=[self.query(question,_only='grids',_point=tuple(map(float,p))) for p in dict.fromkeys(points)]
            return {**self.base_result(start,end),'kind':'bundle','items':items,
                    'state':'ready' if any(x['state']=='ready' for x in items) else 'no_data'}
        regional={'舟山':(122.3,30.3),'宁波':(122.0,29.8),'台州':(121.8,28.5),'温州':(121.4,27.8),'嘉兴':(121.3,30.6)}
        approximate=False
        if _point is not None:lon,lat=_point
        elif points:lon,lat=map(float,points[0])
        elif named_location and named_location.get('key') in {'zhujiajian'}:
            lon,lat=named_location['lon'],named_location['lat'];approximate=True
        elif coast:lon,lat=coast['lon'],coast['lat']
        elif region and '海域' in question:lon,lat=regional[region];approximate=True
        elif profile and profile['lon'] is not None:lon,lat=profile['lon'],profile['lat'];approximate=True
        elif region:lon,lat=regional[region];approximate=True
        else:return {**self.base_result(start,end),'kind':'clarification','state':'needs_location','reason':'请给出岸段、海域或经纬度，例如“舟山海域未来72小时浪高多少”。'}
        products=[]
        if re.search(r'浪|波高',question):products.append('wave')
        if re.search(r'波向',question):products.append('wave_direction')
        if re.search(r'风力|风速|风浪',question):products.append('wind')
        if re.search(r'增水|水位|潮位',question) and not (_only=='grids' and coastal_request):products.append('surge')
        if re.search(r'海温|水温',question):products.append('temperature')
        if '流速' in question:products.append('current')
        if not products:products=['wave']
        values=[self.grid(product,lon,lat,start,end) for product in products]
        waves=next((p for p in values if p.get('product')=='WAV' and p.get('state')=='ready'),None)
        direction=next((p for p in values if p.get('product')=='MWD' and p.get('state')=='ready'),None)
        if waves and direction and waves.get('maximum'):
            same_point=all(waves.get(k)==direction.get(k) for k in ('batch','grid_lon','grid_lat'))
            sample=next((p for p in direction['series'] if p['time']==waves['maximum']['time']),None)
            if same_point and sample:direction['at_wave_peak']=dict(sample,label='波高峰值同刻')
            else:direction['alignment_warning']='波高与波向未同时匹配批次、格点和时刻，未构造同点同刻记录。'
        unsupported=[]
        for term in ('谱峰周期','平均风','流向'):
            if term in question:unsupported.append(term+'尚未配置可核定变量；不能用现有要素替代')
        return {**self.base_result(start,end),'kind':'grids','state':'ready' if any(v['state']=='ready' for v in values) else 'no_data',
                'location':{'lon':lon,'lat':lat,'regional_representative_assumed':approximate},'products':values,'missing_requested_elements':unsupported}

    def summary(self,result):
        if result.get('kind')=='official_warnings':return warning_summary(result)
        header='试算数据，所有下列数值只在当前假设配置成立时适用，不是正式预警或响应决定。\n'
        header+='配置编号：'+result['assumption_id']+'；北京时间；查询时段：'+result['start']+'至'+result['end']+'。\n'
        kind=result['kind']
        if kind=='historical_comparison':return comparison_summary(result)
        if kind=='analogue':
            return '路径相似检索的程序实际结果，不表示灾害损失会相同。风速输入按现有授权假设解释。\n'+json.dumps(result,ensure_ascii=False,separators=(',',':'))
        if kind=='activity_rules':return policy_summary(result)
        if kind=='spatial':return spatial_summary(result)
        if kind=='activity':
            if result.get('activity_rule_mode')=='official_catalog':
                header='海况为已起报NC的程序解析结果；活动依据按官方来源、地点和时段匹配。变量含义与潮位基准的未核口径见假设说明。\n'
            return header+activity_summary(result)
        if kind=='bundle':
            impact=('本次仅提取边界输入，未计算淹没。'+json.dumps(result['impact_assessment'],ensure_ascii=False)+'\n' if result.get('impact_assessment') else '')
            return header+impact+str(result.get('reason') or '')+'\n复合问题按地点与时段分别查询，每项保留自己的有效时段和批次；某一项缺数不拒绝其他项。\n'+'\n\n'.join(
                ('查询对象：'+item['task_object']+'\n' if item.get('task_object') else '')+
                (activity_summary(item,compact=True) if item.get('kind')=='activity' else self.summary(item)) for item in result['items'])
        if kind=='typhoons':
            header='台风活动列表的接口查询结果；数量对应接口活动列表，不是未来24小时台风数量预测。未核定的字段单位分别注明。\n'
            if result.get('historical_snapshot'):
                header='截至查询时刻之前已保存的原始台风接口快照；仅代表快照中最新的记录，不能证明该时刻没有后续更新。'+header
            rows=[]
            for storm in result['list']:
                points=storm.get('points') or []
                if not points:continue
                latest=max(points,key=lambda p:p['time'])
                latest_time=datetime.fromisoformat(latest['time'])
                latest_time=latest_time.astimezone(BEIJING) if latest_time.tzinfo else latest_time.replace(tzinfo=BEIJING)
                cutoff=result.get('requested_as_of')
                reference=datetime.fromisoformat(cutoff or result['as_of'])
                if not reference.tzinfo:reference=reference.replace(tzinfo=BEIJING)
                age=max(0,(reference-latest_time).total_seconds()/3600)
                rows.append({'name':storm.get('nameCN'),'id':storm.get('typhoonID'),'记录距查询时刻小时':round(age,1),'记录可能滞后':age>6,
                             **{k:latest.get(k) for k in ['time','lon','lat','maxSpeed','pressure','moveDirection','moveSpeed','isActive','windScale','intensity']}})
                if result.get('requested_forecast'):
                    rows[-1]['预报路径按机构分别展示']={name:[{k:p.get(k) for k in ['time','lon','lat','maxSpeed','pressure']} for p in points[:8]]
                        for name,points in (latest.get('forecast') or {}).items() if isinstance(points,list)}
            count_verified=result.get('activity_list_count_verified',result.get('activity_count_verified',True))
            count=result.get('activity_list_count',len(result['list'])) if count_verified else None
            data={'活动台风数':count,
                  '已选对象数':len(result['list']),'查询对象范围':result.get('selection_scope','all_active'),
                  '已取得记录的台风数':len(rows),'活动列表完整性':'完整活动台风数量未知；仅保存部分历史记录或未保存历史列表' if result.get('activity_scope_warning_required') else '仅限请求对象' if result.get('activity_count_verified') is False else '接口活动列表',
                  '台风':rows,'风速假设单位':'m/s','移动速度假设单位':'km/h',
                  '取得时间':result.get('retrieved_at'),'历史原始接口快照':result.get('historical_snapshot',False),
                  '查询截至时刻':result.get('requested_as_of'),'快照范围':result.get('snapshot_scope'),
                  '查询时间模式':result.get('query_time_mode'),
                  '缓存过期':result['cache_stale'],'状态':result.get('state','ready'),
                  '缺项说明':result.get('missing_inputs',[])}
        elif kind=='tide':
            datum_mode=self.config().get('station_tide_overrides',{}).get(result['site']['code'],self.config()['tide_datum'])
            datum_text={'raw_already_1985':'原值按1985基准使用，尚未核定','subtract_base85':'原值减站点偏移量，转换方向为假设','add_base85':'原值加站点偏移量，转换方向为假设'}[datum_mode]
            data={'站点':result['site']['name'],'文件':result['site']['filename'],'潮位单位':'厘米','潮位基准':datum_text,
                  '逐时最大':result['maximum'],'逐时最小':result['minimum'],'精确高潮':result['high_tides'],'精确低潮':result['low_tides'],
                  '参考岸段':result.get('reference_for'),'关联方法':result.get('reference_method'),'状态':result['state'],'时段不完整':result['partial']}
            data['岸段转换公式']=result.get('formula')
        elif kind=='coastal':
            sorted_rows=sorted([r for r in result['results'] if r['state']=='ready'],key=lambda r:r['maximum']['total_cm'],reverse=True)
            # Summaries are computed over all requested coasts, not the model's top rows.
            surge_rows=[(max(r.get('swl_series') or r.get('series',[]),key=lambda v:v['swl_cm']),r)
                        for r in result['results'] if r.get('swl_series') or r.get('series')]
            max_surge=max(surge_rows,key=lambda pair:pair[0]['swl_cm']) if surge_rows else None
            data={'假设':self.config()['notes'][:5],
                  '起报批次':result.get('selected_batch'),'预报时效参考时刻':result.get('freshness_reference_at'),
                  '时效说明':result.get('forecast_interpretation'),
                  '台风归属边界':result.get('attribution_note'),
                  '可提取SWL岸段数':result.get('swl_usable_count'),
                  '已列潮位最高岸段数':len(sorted_rows[:5]),
                  '请求岸段数':result['requested_count'],'可计算岸段数':result['usable_count'],'缺数岸段数':result['missing_count'],
                  '时段覆盖不完整岸段数':result['partial_count'],
                  '各试算等级数量':result['counts'],('最大增水记录' if self.config()['swl_kind']=='surge' else '最大SWL总潮位记录'):{'岸段':max_surge[1]['name'],**max_surge[0]} if max_surge else None,
                  '潮位最高岸段':[{'岸段':r['name'],'地区':r['city'],'最大总潮位cm':r['maximum']['total_cm'],'发生时间':r['maximum']['time'],
                                    '试算等级':COLORS.get(r['level'],'未达已知阈值'),'超过对应阈值cm':r['exceedance_cm'],
                                    '参考站':r['reference_code'],'关联':r['reference_method'],'批次':r['batch'],'时段不完整':r['partial']} for r in sorted_rows[:5]],
                  '缺数示例':[{'岸段':r['name'],'原因':r.get('reason')} for r in result['results'] if r['state']!='ready'][:3]}
        elif kind=='grids':
            data={'位置':result['location'],'SWL口径':self.config()['swl_kind'],'SSW口径':'u10/v10矢量模，按10米阵风试算；不是平均风',
                  '未覆盖子项':result.get('missing_requested_elements',[]),
                  '产品':[{'要素':p['product'],'单位':p.get('unit'),'最大值':p.get('maximum'),
                    '逐时序列覆盖':p.get('series_coverage'),'波向原始角度样本':p.get('direction_samples'),
                    '使用网格坐标':[p.get('grid_lon'),p.get('grid_lat')],'距查询点公里':p.get('distance_km'),
                    '波高峰值同刻波向':p.get('at_wave_peak'),'方向口径':p.get('direction_convention'),'方向对齐限制':p.get('alignment_warning'),
                    '最小值':p.get('minimum'),'批次':p.get('batch'),'过期':p.get('stale'),'尚未起报':p.get('future_batch'),
                    '时段不完整':p.get('partial'),'状态':p['state'],'原因':p.get('reason')} for p in result['products']]}
        else:data={'状态':result['state'],'提示':result.get('reason')}
        return header+json.dumps(data,ensure_ascii=False,separators=(',',':'))

    def evidence_allowed(self,source):
        if (source.get('metadata') or {}).get('official_typhoon_records') is True:
            key=str(source.get('source_id') or '').removeprefix('forecast:')
            record=self.store.state('evidence:'+key) if re.fullmatch('[a-f0-9]{24}',key) else None
            return bool(record and record.get('kind')=='typhoons' and record.get('trial') is False)
        if (source.get('metadata') or {}).get('historical_statistics_records') is True:
            key=str(source.get('source_id') or '').removeprefix('forecast:')
            record=self.store.state('evidence:'+key) if re.fullmatch('[a-f0-9]{24}',key) else None
            return bool(record and record.get('kind')=='historical_statistics' and record.get('trial') is False)
        if (source.get('metadata') or {}).get('official_warning_records') is True:
            key=str(source.get('source_id') or '').removeprefix('forecast:')
            record=self.store.state('evidence:'+key) if re.fullmatch('[a-f0-9]{24}',key) else None
            return bool(record and record.get('kind')=='official_warnings' and record.get('trial') is False)
        return (source.get('metadata') or {}).get('assumption_id')==self.config_id()

    @snapshot_query
    def answer(self,question,qa,meta,allow_web):
        if warning_question(question):
            try:return answer_warnings(self,question,qa,meta,allow_web=allow_web)
            except ValueError as exc:
                return {'answer':str(exc)[:300],'rag_sources':[],'web_sources':[],
                        'answer_meta':{**meta,'route':'official_warnings','used_mode':'needs_clarification',
                                       'business_trial':False,'answerability':'needs_clarification'}}
        try:result=self.query(question)
        except (ValueError,KeyError,OSError) as exc:
            reason='数据文件暂不可读取，请查看数据同步状态。' if isinstance(exc,OSError) else '业务变量缺项，请查看数据状态。' if isinstance(exc,KeyError) else str(exc)[:300]
            result={**self.base_result(*period()),'kind':'clarification','state':'unavailable','reason':reason}
        typhoon_only=result.get('kind')=='typhoons'
        text=self.summary(result)
        historical_only=result.get('kind')=='historical_comparison' and result.get('comparison_mode')=='historical_events'
        if result.get('start') and result.get('end') and not historical_only and not typhoon_only:
            span=(datetime.fromisoformat(result['end'])-datetime.fromisoformat(result['start'])).total_seconds()/3600
            text+='\n查询起止时刻相差'+str(round(span,2))+'小时；若按逐时含首尾采样，目标为'+str(int(span)+1)+'个时刻；不据此声称缺测已补齐。'
        facts=delivery_facts(result)
        coverage=[]
        def series_coverage(value):
            if value.get('kind')=='grid':coverage.append({'product':value.get('product'),'state':value.get('state'),**(value.get('series_coverage') or {})})
            for key in ('items','products'):
                for child in value.get(key,[]) or []:series_coverage(child)
        series_coverage(result)
        meta['tool_series_coverage']=coverage
        if fact_text(facts):text='程序计算值明细（数值、单位、地点、时刻须保持对应）：\n'+fact_text(facts)+'\n\n'+text
        meta['business_task_plan']=result.get('task_plan')
        meta['tool_data_view']=True
        if result.get('task_plan'):
            meta['business_object_instruction']='逐地点、逐日期回答。每一海况数据行必须写地点、日期、指标、完整最小至最大范围、单位和引用；不能合并不同场地的范围或把另一地点的数据作为本地点结果。任务失败项保留具体原因，其他任务照常交付。仅作比较依据的对象不当作已请求计算的地点。'
        evidence_id=hashlib.sha256((text+self.config_id()).encode()).hexdigest()[:24]
        self.store.state('evidence:'+evidence_id,result)
        static_only=result.get('kind')=='spatial'
        policy_only=result.get('kind')=='activity_rules'
        activity=contains_result(result,'activity')
        hit={'source_id':'forecast:'+evidence_id,'chunk_id':evidence_id,'title':'台风接口查询结果' if typhoon_only else '历史海洋灾害分项指标比较' if historical_only else '涉海活动官方规则查询' if policy_only else '本地空间栅格查询结果' if static_only else '海洋活动预报与官方规则分析' if activity else '业务数据查询与假设试算结果',
             'filename':'空间栅格查询结果' if static_only else '业务数据查询结果',
             'locator':'；'.join(row['event'] for row in result.get('historical_periods',[])) if historical_only else result['start']+'至'+result['end'],
              'category':'台风接口记录' if typhoon_only else '历史事件数据' if historical_only else '活动规则' if policy_only else '空间栅格数据' if static_only else '业务预报数据','status':'indexed' if static_only or policy_only or historical_only or typhoon_only else 'provisional',
              'text':text,'metadata':{'source_type':'business_forecast','external_allowed':True,'trial':not(static_only or policy_only or historical_only or typhoon_only),
                                    'official_typhoon_records':typhoon_only,
                                   'policy_basis':'official_catalog' if activity or policy_only else None,
                                   'assumption_id':self.config_id(),'retrieved_at':result['as_of'],
                                   'unit':'m/估计常住人口人数（按查询要素）' if static_only else 'cm/m（按要素）'},
             'retrieval':'structured_forecast_query','retrieval_details':{'direct_evidence':True}}
        hits=[hit]
        spatial=contains_result(result,'spatial')
        meta.update(route='business_forecast',business_trial=True,business_query=result['kind'],
             business_state=result['state'],assumption_id=self.config_id(),business_evidence_id=evidence_id,
             business_instruction='业务数值只引用资料1的程序查询结果，不能从网页或模型记忆补数。直接给出数值、地点和时间，再解释；假设口径集中在末尾说明一次，避免配置编号和程序字段名，不说“本轮只回答”或“可确认的核心结论”等过程语。不得称为正式预警。缺数及不完整覆盖必须说明。只解释本轮查询范围，不能把SWL总潮位称为增水。岸段代表点试算不是验潮站实测或独立站点排名，参考站代码仅说明数据关联。活动台风数量是接口活动列表数量，不是未来24小时的预测；isActive=1表示接口标记活动。必须写最新位置记录时刻；记录滞后不代表此刻位置。不同机构路径分别解释，不混成一条。SSW按10米阵风试算，不转成平均风。岸段列表只给最高前5个时，说明完整结果可在本次数据链接和地图查看。网页仅补充相关解释，不逐份说明无关资料缺什么。')
        if typhoon_only:
            meta.update(business_trial=False,official_typhoon_records=True)
            meta['business_instruction']='本题为台风接口记录查询。先明确回答已核对的活动数量和所选对象，再逐项给出用户所问的位置、强度和移动方向，保留各条记录时刻。数量是接口活动列表数量，不是未来数量预测；仅选某台风的记录数不能冒充全量活动数量。过期缓存、历史快照、部分名单与未到达的未来时刻分别说明，不把旧位置称为此刻位置。经纬度、气压、方向等原始字段按原值交付；风速和移动速度的未核单位只在相应字段旁注明，不把整份接口记录称为假设试算。没有请求的NC、SWL、天文潮及高程基准不作为本题缺项。只引用资料1中实际取得的记录和数量，网页可以补充解释，不能编造新记录。'
        if result.get('scope_note'):
            meta['marine_brief']=True
            meta['business_instruction']+='本题影响报告分为台风中心记录、海况预报和缺项。NC网格和岸段计算一律放入预报部分，不能放入实况部分；查询得到的数值不等于实测。'+result['scope_note']
        if result.get('purpose')=='impact_boundary_inputs':
            meta['business_instruction']+='本次工具只提取淹没判断的边界参考输入，未运行淹没模型。开头明确未计算淹没，再交付实际取得的潮位、增水、时间与岸段覆盖；不能据此判定会或不会被淹，更不能给淹没面积、深度或转移人数。'
        if activity:
            meta.update(activity_data_driven=True,official_activity_rules=True,route='marine_activity',activity_permission_state=result.get('permission_state'))
            meta['business_instruction']+='本题为具体涉海活动数据分析。先列请求日期/采样坐标、起报批次、风浪流温实际范围和高潮低潮时刻，再分别回答赶海、游泳、海钓。预报与程序计算数值、已执行规则的活动判断取自资料1；其他来源可以补充各自适用的规则、手续及正式公告，不能伪称已通过程序核验。标明区域参考海点，不称精确浴场；预报不称实况；格点流速不称离岸流。必须遵守资料中的活动判断，不把未达到可推荐条件改写成已适宜或允许活动，不把“暂不建议自行开展”写成正式禁令。回答只用易懂中文，不展示内部字段、英文状态码或“程序判为not_established”等实现细节。景区营业只说明入园时间，历史水质不用于当日适游结论。不要只返回通用科普或“缺资料无法答”；先清楚交付已解析的实际海况、潮汐和公告事实，再解释哪些条件阻止给出肯定建议。'
            meta['business_instruction']+='有分时筛查与候选时段时，先分别说三类活动的结论，再用精简数据表列依据，给出参考时段和不满足的具体条件。工程筛查通过不改称安全或正式许可；低潮参考窗口不称安全赶海窗口。只问某一种活动时不展开其他活动。'
            meta['business_instruction']+='不能只凭高潮或低潮时刻声称滩涂已出露或不能出露；没有实测滩高时仅说明涨落潮过程。明确SSW按阵风、SST按摄氏度解释仍属假设。'
            meta['business_instruction']+='当前活动规则已改为按地域、活动和有效期匹配官方依据。只使用资料1中各活动的适用官方条款和数值评价，不能套用旧的统一演示阈值，也不能从风浪较小推断已获许可。没有适用数值判据时明确未作超限判定，不把它写成海况超标或一律不建议；照常给出实际数值和可核对的官方条件。岱山等地方风险分级不当作浙江全省停航线，保护区办法只适用于指定保护区；未核全文或废止文件不作当前依据。已取得官方条款时按条文概括关键条件，并说明当前已满足、未满足及未核实部分，避免只罗列缺项。'
            meta['business_instruction']+='用户指定的经纬度称用户指定坐标，海况来自邻近模式海格点，不把有地表高程的陆地点称为海点或已核实的游泳水域。开放和现场管理状态未知，不等于法律要求所有赶海或岸钓都申请行政许可；只有适用条款明确许可时才讲法定手续。活动日期分析不必展开完整温度阈值表；如需引用，较适宜温度分支是大于28℃且不超过33℃，不能写成含28℃的28—33区间，负面条件优先。'
            if result.get('state')=='needs_location':
                meta['business_instruction']+='本次尚未提取NC，原因仅是地点不明确或没有可靠参考坐标。直接请求用户给海滩、岸段或坐标，不能自行判断日期太远或没有预报数据，也不能引入通用解释掩盖定位缺项。当前查询时间为'+result['as_of']+'，所问时段从'+result['start']+'开始。'
        if policy_only:
            meta.update(activity_data_driven=True,official_activity_rules=True,business_trial=False,route='marine_activity_policy')
            meta['business_instruction']='本题查询规则而非当天海况。按资料1中eligible_policies列出的适用官方条款，用简明表格或步骤说明每种活动的依据和条件；排除条目仅说明失效或不适用，不引用其数字作现行阈值。国家通用方法、浙江规则、县市规则、保护区规则分别保留范围；没有官方数值规则就说明未核定，不能恢复演示参数。规则存在不能证明今天场地开放、救生到位、水质达标或船舶适航。只回答用户问到的活动，开头标明适用地区。'
            meta['business_instruction']+='所有大小边界精确保留：较适宜温度分支为大于28℃且不超过33℃，不包含28℃；水质差、水温低于20℃或高于33℃的负面条件优先。问到条件与手续时，交付资料已有的受理部门、申请条件、材料和办理期限，不把程序已提供的信息说成缺少。'
        if activity or policy_only:
            meta['business_instruction']+='条文“另按休闲渔业管理规定管理”只说明管理关系，不推出个人免证或不适用个人海钓许可程序；原文未明确豁免时，不自行免除其他条文的许可要求，具体手续另核。'
        if spatial:
            meta.update(spatial_data_driven=True)
            meta['business_instruction']+='空间数值仅使用资料1的栅格查询结果。明确DEM为地表高程参考、人口为2020年100米格网估算；海点的0值不解释为浴场实测海拔；未知高程基准不与1985潮位直接比较；矩形人口不称行政区总人口、实时游客或应转移人数。人口有缺测时，人数必须称有效格网的估计合计，注明缺测格网未按零处理，不称整个范围的完整准确人数；超出覆盖包络的裁切结果明确仅为覆盖部分。未指定人口范围时请求用户明确矩形范围，不自行扩大统计。'
        if static_only:
            meta['business_trial']=False
            meta['business_instruction']+='本题仅为空间栅格查询，不加入无关潮位或增水假设。ASTER产品声明为EGM96；未完成的是与1985潮位基准统一，不把这两件事说成相互矛盾。'
        meta['required_business_facts']=facts
        if contains_result(result,'coastal'):
            meta['business_instruction']+='岸段预报先交付已取得的增水、总潮位、发生时刻和起报批次，再回答缺项。历史有效时段的预报不因今天距起报超过24小时就称没有数据。时效以资料中的参考时刻核对。NC缺少特定台风场景标识只限制因果归属，不得把已经计算出的网格增水和潮位说成无法取得；也不得把整个网格场的数值归因于所问台风。SWL可提取而天文潮缺测时，仅限制总潮位，不能丢弃独立SWL峰值。'
        if contains_result(result,'historical_comparison'):
            meta['business_instruction']+='本题为历史灾害指标比较。按最大风暴增水、最高潮位、直接经济损失、死亡/失踪人数分别交付已取得值；每个历史数值行写年份、事件名称、测站或事件范围、单位和引用。差值只使用comparisons里已计算的记录，每行明确左事件、右事件和前者高低差，不自行相减其他不同站潮位。历史年度公报与工作总结有差异时注明各自出处，不混为一个值。预报岸段峰值与历史测站峰值只能作区域指标对照，不称同站差值；不同测站绝对潮位的最大值不能代表整体灾害程度。未核定NC台风场景归属时不得把当前整个海况场的数值归因于所问台风。不能将中心风速、路径相似或单一峰值直接写成浙江总体灾损偏重或偏轻。历史公报已提供的损失、死亡数和数值不能说成全部缺数据。不同年份名义经济损失未作价格平减，不作为同等资产暴露下强度对比；“无死亡”不扩写为“无死亡失踪”。用户指定城市、县、站时，工具未取得的本地指标不得改用全省数值。只比较历史事件时不用查询处理时间冒充事件发生时间。'
            if historical_only:meta['business_trial']=False
        if getattr(qa,'crag_enabled',False):
            hits=qa.joint_evidence(question,meta,allow_web,pinned=[hit],queries=meta.get('retrieval_queries'))
        else:
            local=qa._collect_local_hits(question,question,[],meta.get('subquestions',[]),meta,meta.get('dynamic_question',False),None)
            if re.search(r'为什么|怎么|意义|如何|依据|注意|区别|影响',question):
                hits.extend([row for row in local if qa._can_send(row)][:1])
            web,web_meta=qa._fetch_public(question,meta.get('subquestions',[]),meta.get('dynamic_question',False),allow_web)
            hits.extend(select_evidence(question,web,meta.get('subquestions',[]),limit=4));meta.update(web_meta)
        heading='**台风接口查询结果**' if typhoon_only else '**历史灾害指标比较**' if historical_only else '**官方规则查询**' if policy_only else '**空间数据查询**' if static_only else '**海洋活动分析**' if activity else '**试算结果（假设口径）**'
        if not qa.external_enabled or not qa.model_caller:
            return {'answer':heading+'\n'+text,'rag_sources':qa._public_sources(question,[hit]),'web_sources':[],
                    'answer_meta':{**meta,'used_mode':'business_data'}}
        answer=qa._organized_answer(question,qa.model_name,hits,meta)
        if answer['answer_meta'].get('used_mode')=='model_error' and (typhoon_only or any(f['kind'] in {'computed','analogue','marine_product','historical_metric','comparison'} or f['kind'].startswith('typhoon_') for f in facts)):
            return {'answer':delivery_table(result,facts,question=question),'rag_sources':qa._public_sources(question,[hit]),'web_sources':[],
                    'answer_meta':{**meta,'used_mode':'business_data','answerability':'partial','computed_table_fallback':True,
                                   'validation_error':answer['answer_meta'].get('validation_error'),
                                   'validation_diagnostics':answer['answer_meta'].get('validation_diagnostics'),
                                   'missing_information':list(dict.fromkeys(['本次模型语言说明未通过核对；表格保留程序实际数值，其他子问待补充。']+result.get('missing_information',[])))}}
        if answer['answer_meta'].get('used_mode')!='model_error':
            if result.get('kind')=='historical_comparison' and result.get('state')!='ready':
                answer['answer_meta']['answerability']='partial'
                answer['answer_meta']['missing_information']=list(dict.fromkeys(
                    answer['answer_meta'].get('missing_information',[])+result.get('missing_information',[])+
                    ([result['reason']] if result.get('reason') else [])))
            if result.get('purpose')=='impact_boundary_inputs':
                answer['answer_meta']['answerability']='partial'
                answer['answer_meta']['missing_information']=list(dict.fromkeys(
                    answer['answer_meta'].get('missing_information',[])+result['impact_assessment']['missing_inputs']))
            answer['answer']=heading+'\n\n'+answer['answer']
        return answer
