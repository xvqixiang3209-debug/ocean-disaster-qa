"""Recover/query local numeric review staging; never modify knowledge reviews."""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from marine_kb.numeric_archive import NumericArchive


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',default=str(ROOT/'tmp/source_review_20261021/numeric/numeric_archive.sqlite3'))
    sub=parser.add_subparsers(dest='command',required=True)
    recover=sub.add_parser('recover')
    recover.add_argument('--knowledge',default=str(ROOT/'data/marine_kb/knowledge.sqlite3'))
    recover.add_argument('--sites',default=str(ROOT/'data/forecast_runtime/sources/3b1e16f3cb91_m_fcst_site.sql'))
    recover.add_argument('--names',default=str(ROOT/'data/forecast_runtime/sources/6dcf33ed3ee0_站点名字对应关系(1).txt'))
    recover.add_argument('--coasts-sql',default=str(ROOT/'data/forecast_runtime/sources/791b3b1479c2_m_warn_tide_info_town(1).sql'))
    recover.add_argument('--profiles',help='Optional explicit temporary profiles keyed by source_id or original filename')
    recover.add_argument('--max-files',type=int)
    recover.add_argument('--output',help='Safe JSON manifest output; defaults beside staging database')
    query=sub.add_parser('query')
    for option in ['file','station','coast','time-token','start','end']:query.add_argument('--'+option)
    query.add_argument('--allow-assumed',action='store_true');query.add_argument('--limit',type=int,default=100)
    coast=sub.add_parser('coasts');coast.add_argument('--name',default='');coast.add_argument('--version',choices=['geojson','sql'])
    sub.add_parser('summary')
    args=parser.parse_args(argv)
    archive=NumericArchive(args.database,read_only=args.command!='recover')
    if args.command=='query':
        result=archive.query(file=args.file,station=args.station,coast=args.coast,time_token=args.time_token,start=args.start,end=args.end,
                             allow_assumed=args.allow_assumed,limit=args.limit)
    elif args.command=='coasts':result=archive.coasts(args.name,args.version)
    elif args.command=='summary':result=archive.summary()
    else:
        started=time.monotonic()
        archive.load_station_registry(args.sites,args.names)
        profiles=json.loads(Path(args.profiles).read_text('utf-8')) if args.profiles else {}
        with sqlite3.connect('file:'+Path(args.knowledge).resolve().as_posix()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute("SELECT source_id,filename,raw_path,sha256,category FROM sources WHERE status='pending_review' AND category IN ('模式结果','空间数据') ORDER BY filename,source_id")]
        workbooks=[row for row in rows if row['category']=='模式结果']
        if args.max_files is not None:workbooks=workbooks[:max(0,args.max_files)]
        results=[]
        for index,row in enumerate(workbooks,1):
            try:
                results.append(archive.recover_workbook(row['raw_path'],source_id=row['source_id'],filename=row['filename'],
                    expected_sha256=row['sha256'],profile=profiles.get(row['source_id']) or profiles.get(row['filename'])))
            except Exception as exc:
                results.append({'source_id':row['source_id'],'filename':row['filename'],'state':'error','error_type':type(exc).__name__})
            if index%50==0:
                print(json.dumps({'processed':index,'total':len(workbooks),'seconds':round(time.monotonic()-started,1)}),file=sys.stderr,flush=True)
        coastal=[]
        for row in rows:
            if row['category']!='空间数据':continue
            try:
                coastal.append(archive.recover_coasts(row['raw_path'],args.coasts_sql,source_id=row['source_id'],
                    expected_sha256=row['sha256'],profile=profiles.get(row['source_id']) or profiles.get(row['filename'])))
            except Exception as exc:
                coastal.append({'filename':row['filename'],'state':'error','error_type':type(exc).__name__})
        result={'schema_version':1,'seconds':round(time.monotonic()-started,2),'source_review_changed':False,
                'transmission_permissions_changed':False,'workbooks':results,'coasts':coastal,'summary':archive.summary()}
        output=Path(args.output) if args.output else Path(args.database).parent/'recovery_manifest.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        result={'manifest':str(output),'seconds':result['seconds'],'recovered_or_existing':sum(r['state']!='error' for r in results),
                'errors':sum(r['state']=='error' for r in results),'summary':result['summary'],'coasts':coastal}
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
