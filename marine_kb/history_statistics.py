"""Reproducible monthly archive counts; not landfall or forecast probabilities."""
from collections import Counter
from contextlib import closing
from datetime import datetime
import hashlib,json,re,sqlite3
from pathlib import Path

def matches(question):
    return bool(re.search(r'台风|热带气旋',question) and re.search(r'几月|月份|月度|季节|频次|频数|多发',question)
                and not re.search(r'这次|本次|预计|预报|当前|明天|登陆',question))

def monthly(database,start=1981,end=2020):
    if type(start) is not int or type(end) is not int or not 1800<=start<=end<=datetime.now().year:
        raise ValueError('invalid_statistics_years')
    path=Path(database).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        first=Counter({int(month):count for month,count in db.execute(
            "SELECT substr(start_time,6,2),count(*) FROM storms WHERE start_time>=? AND start_time<? GROUP BY substr(start_time,6,2)",
            (f'{start}-01-01',f'{end+1}-01-01'))})
        active=Counter({int(month):count for month,count in db.execute(
            "SELECT substr(time,6,2),count(DISTINCT sid) FROM track_points WHERE time>=? AND time<? GROUP BY substr(time,6,2)",
            (f'{start}-01-01',f'{end+1}-01-01'))})
        bounds=db.execute('SELECT min(start_time),max(end_time),count(*) FROM storms').fetchone()
        metadata=dict(db.execute('SELECT key,value FROM metadata WHERE key IN (?,?,?)',('source_member','built_at','basin')))
    if not bounds[0] or start<int(bounds[0][:4]) or end>int(bounds[1][:4]):
        raise ValueError('statistics_outside_archive')
    rows=[{'month':month,'first_record_count':first[month],'active_unique_storms':active[month]} for month in range(1,13)]
    return {'kind':'historical_statistics','state':'ready','trial':False,'start_year':start,'end_year':end,'time_basis':'原始档案UTC月份',
            'sample_count':sum(first.values()),'monthly':rows,'archive_start':bounds[0],'archive_end':bounds[1],
            'archive_storm_count':bounds[2],'source_metadata':metadata,
            'definition':'首记计数按本库每个SID的start_time月份计1次；活动计数按当月存在轨迹点的SID去重，同一台风可在不同月份分别出现。',
            'limitations':['样本为西北太平洋热带气旋档案，包含不同强度阶段；未套用各国台风强度分类。',
                           '不是我国或浙江登陆台风频次，不是预报概率，也不能用月度样本数判断某天安全。',
                           '首条轨迹记录时间不等于业务认定的生成时刻；未声称源档案逐年无漏记。']}

def source_hit(qa,question):
    path=getattr(qa,'historical_database',None);runtime=getattr(qa,'forecast_data',None)
    if not path or runtime is None or not matches(question):return None
    years=re.search(r'((?:18|19|20)\d{2})\s*年?\s*(?:至|到|—|－|-)\s*((?:18|19|20)\d{2})',question)
    start,end=(map(int,years.groups()) if years else (1981,2020))
    try:result=monthly(path,start,end)
    except (ValueError,OSError,sqlite3.Error):return None
    text=f"西北太平洋热带气旋档案{start}至{end}年月度样本统计，时间基准为UTC，不是我国或浙江登陆台风统计。\n"
    text+=result['definition']+'\n| 月份 | 首次记录样本数（个） | 当月活动样本数（个，按SID去重） |\n|---|---|---|\n'
    text+='\n'.join(f"| {r['month']}月 | {r['first_record_count']}个 | {r['active_unique_storms']}个 |" for r in result['monthly'])
    text+='\n样本首记合计：'+str(result['sample_count'])+'个。\n'+'\n'.join(result['limitations'])
    identity=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
    runtime.store.state('evidence:'+identity,result)
    return {'source_id':'forecast:'+identity,'chunk_id':identity,'title':'西北太平洋热带气旋档案月度样本统计',
            'filename':'历史台风数据库月度统计','locator':f'{start}年至{end}年 UTC月份，按SID去重','text':text,'status':'indexed',
            'retrieval_details':{'direct_evidence':True},'metadata':{'source_type':'business_forecast','external_allowed':True,
            'trial':False,'historical_statistics_records':True,'evidence_scope':'archive_monthly_sample_statistics'}}
