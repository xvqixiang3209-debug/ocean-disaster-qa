"""Recover dated raw API data from previously saved tool/HTTP evidence.

Never import a model answer, question or ideal-answer text into the data store.
"""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from forecast_runtime.storage import ForecastStore


def tool_records(value):
    if not isinstance(value,dict):return
    if value.get('kind')=='typhoons':yield value
    elif value.get('kind')=='bundle':
        for item in value.get('items',[]):yield from tool_records(item)
    elif isinstance(value.get('business_evidence'),dict):yield from tool_records(value['business_evidence'])
    elif isinstance(value.get('items'),list):
        for item in value['items']:yield from tool_records(item)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',action='append',type=Path,required=True)
    args=parser.parse_args();store=ForecastStore(ROOT/'data/forecast_runtime');rows=[]
    for path in args.input:
        data=path.read_bytes();value=json.loads(data.decode('utf-8-sig'));digest=hashlib.sha256(data).hexdigest()
        for record in tool_records(value):
            if record.get('state') not in {'ready','no_active_typhoons'} or not record.get('list'):continue
            origin={'kind':'captured_http_tool_evidence','file':str(path.resolve()),'sha256':digest}
            try:key=store.record_typhoon_snapshot(record,origin=origin,complete=False)
            except ValueError:continue
            rows.append({'id':key,'retrieved_at':record['retrieved_at'],'source_file':str(path.resolve())})
    result={'source_records':len(rows),'unique_snapshots':len({r['id'] for r in rows}),'records':rows,
            'note':'Only original structured API fields were recovered. No model answer or test ID is used at query time.'}
    output=ROOT/'tmp/gap137_upgrade_20261018/typhoon_archive_recovery.json'
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))

if __name__=='__main__':main()
