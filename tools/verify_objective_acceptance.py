"""Verify saved live answers against tool tuples, source bytes and HTTP products.

No model invocation. This is a delivery check and explicit concept review,
not a claim that every business question or physical forecast is correct.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

import h5py
import numpy as np
import requests
from scipy.io import netcdf_file

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('--url',default='http://127.0.0.1:8773')
    args=p.parse_args()
    records={cid:read(args.run/(cid+'.result.json')) for cid in ('KNOWLEDGE','ANALOGUE','REPORT','FOLLOWUP')}
    assert all(r['http_status']==200 for r in records.values())
    stable=read(args.run/'run_completed.json')['source_stability']
    assert stable['stable']
    checks={}
    knowledge=records['KNOWLEDGE']['reply']
    assert all(phrase in knowledge['answer'] for phrase in ('有效波高','高潮位','组织力量、调集资源','不能只凭颜色'))
    assert len(knowledge['sources'])>=3 and all('.gov.cn/' in row['url'] for row in knowledge['sources'])
    assert not knowledge['missing_information']
    checks['KNOWLEDGE']={'status':'通过','conclusion':'人工核对：海浪、风暴潮、预警颜色、组织响应各自归属清楚；未追加阈值缺项。官方卡正文由程序保留，DeepSeek安排结构。',
        'official_source_count':len(knowledge['sources']),
        'joint_search_executed':knowledge['evidence_diagnostics'].get('web_search_executed'),
        'scope':'基础概念，非当日清单或具体条文'}
    analogue=records['ANALOGUE']; tool=analogue['tool_result']; text=analogue['reply']['answer']
    rows=tool['analogues']; assert len(rows)==3
    for row in rows:
        assert str(row['name']) in text and str(row['season']) in text
        assert format(row['similarity_score'],'.15g') in text
    assert tool['track_context']['agency']=='中国' and tool['track_context']['track_kind']=='forecast'
    checks['ANALOGUE']={'status':'通过','conclusion':'三条名称、年份、真实路径分数及距离完整进入回答；中国机构和起报时刻明确，纯排名未索要历史灾损。',
        'rank_rows':[{key:row[key] for key in ('rank','name','season','similarity_score','track_dtw_km')} for row in rows],
        'track_context':tool['track_context'],
        'comparison_note':'本轮实时路径批次已更新，分数与旧录制不必相同；改进比较针对交付完整性，不把数值变化归因为代码。'}
    report=records['REPORT']; data=report['tool_result']; a=report['reply']['answer']
    assert data['dataset_id']=='ecmwf_202610192000_247997aeca80'
    assert data['start']=='2026-10-20T20:00:00+08:00' and data['end']=='2026-10-23T20:00:00+08:00'
    assert all(value in a for value in ('6.74','23.48','1003.07'))
    assert '图件和报告的具体下载链接、文件名或获取步骤未在资料中给出' not in a
    artifact_checks=[]
    for item in report['reply']['objective_artifacts']:
        response=requests.get(args.url+item['url'],timeout=30);response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest()==item['sha256']
        if item['filename'].endswith('.png'):assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
        artifact_checks.append({'filename':item['filename'],'http_status':response.status_code,'sha_verified':True,'bytes':len(response.content)})
    assert len(artifact_checks)==6
    # Independent nearest-cell reads using the file libraries, without calling
    # the product reader or reusing its decoded value arrays.
    index=read(ROOT/'data/forecast_runtime/objective/index.json')
    dataset=next(d for d in index['datasets'] if d['dataset_id']==data['dataset_id'])
    source_checks=[]
    for field in ('wave','wind','pressure'):
        stat=data['fields'][field]
        extreme=stat['minimum'] if field=='pressure' else stat['maximum']
        source=next(s for s in dataset['sources'] if field in s['fields'])
        source_path=Path(source['path'])
        assert hashlib.sha256(source_path.read_bytes()).hexdigest()==source['sha256']
        if field=='wave':
            with netcdf_file(source_path,'r',mmap=False) as nc:
                lat=np.asarray(nc.variables['latitude'][:]);lon=np.asarray(nc.variables['longitude'][:])
                offsets=np.asarray(nc.variables['time'][:]);origin=datetime(1900,1,1,tzinfo=timezone.utc)
                wanted=(datetime.fromisoformat(extreme['time']).astimezone(timezone.utc)-origin).total_seconds()/3600
                ti=int(np.argmin(np.abs(offsets-wanted)));yi=int(np.argmin(np.abs(lat-extreme['lat'])));xi=int(np.argmin(np.abs(lon-extreme['lon'])))
                assert abs(float(offsets[ti])-wanted)<1e-5
                raw=float(nc.variables['swh'][ti,0,yi,xi])
        else:
            with h5py.File(source_path,'r') as nc:
                offsets=nc['time'][:];lat=nc['latitude'][:];lon=nc['longitude'][:]
                wanted=(datetime.fromisoformat(extreme['time']).astimezone(timezone.utc)-datetime(2026,10,19,12,tzinfo=timezone.utc)).total_seconds()/3600
                ti=int(np.argmin(abs(offsets-wanted)));yi=int(np.argmin(abs(lat-extreme['lat'])));xi=int(np.argmin(abs(lon-extreme['lon'])))
                assert abs(float(offsets[ti])-wanted)<1e-5
                raw=float(np.hypot(float(nc['10u'][ti,yi,xi]),float(nc['10v'][ti,yi,xi]))) if field=='wind' else float(nc['msl'][ti,yi,xi])/100
        assert abs(raw-extreme['value'])<1e-4,(field,raw,extreme)
        source_checks.append({'field':field,'native_value':raw,'product_value':extreme['value'],'unit':stat['unit'],
            'time':extreme['time'],'lon':extreme['lon'],'lat':extreme['lat'],'source_sha_verified':True})
    checks['REPORT']={'status':'通过','conclusion':'三个要素原生极值、分区、发生时刻、重点时段及6个真实产物完整交付；独立读取源文件极值格点一致。',
        'artifact_checks':artifact_checks,'source_checks':source_checks,
        'language_delivery':report['reply']['mode'],
        'limitation':'额外模型解读未通过时采用已核对的程序报告；临时统计框不代表浙江行政海域。'}
    follow=records['FOLLOWUP']; f=follow['tool_result']; reply=follow['reply']
    assert reply['context_used'] and f['dataset_id']==data['dataset_id'] and f['start']==data['start'] and f['end']==data['end']
    assert '需要明确地点' not in reply['answer'] and not reply['missing_information']
    for region in f['regions']:
        peak=region['fields']['wave']['maximum']
        assert region['name'] in reply['answer'] and str(round(peak['value'],6)) in reply['answer']
    assert '最高的是：南部参考统计框' in reply['answer']
    checks['FOLLOWUP']={'status':'通过','conclusion':'沿用同一实际数据集及72小时连续窗口，三分区浪高、时间、位置全部交付，并明确南部参考框最高。',
        'same_dataset':True,'same_start':True,'same_end':True,
        'language_delivery':reply['mode'],
        'remaining_quality_note':'回答仍带完整报告摘要，后续可进一步缩短追问中的重复信息。'}
    checks['_validation']={'transaction_count':4,'manual_review_scope':'上述四个明确请求的事实与交付；不外推为全160题已完善',
        'model_calls':sum(r.get('cassette_summary',{}).get('model_calls',0) for r in records.values()),
        'raw_recordings_exported':False,
        'node_contract_groups':4,
        'ui_after_recording':'仅整理主检索按钮、折叠手工JSON入口和去除无实测时的代理估算标签；未变更后端或数值。四组前端测试重跑通过。'}
    path=ROOT/'tmp/objective_acceptance_checks.json';path.write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':str(path),'cases':{k:v['status'] for k,v in checks.items() if not k.startswith('_')},'model_calls':checks['_validation']['model_calls']},ensure_ascii=False))


if __name__=='__main__':main()
