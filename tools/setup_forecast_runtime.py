"""Import authorized forecast source files, retain raw data, and register local NC."""
import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import sys

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path)
    parser.add_argument('--grid-dir',type=Path)
    parser.add_argument('--geometry',type=Path)
    parser.add_argument('--credentials-script',type=Path)
    parser.add_argument('--sync',action='store_true')
    args=parser.parse_args()
    from forecast_runtime.service import ForecastRuntime
    from forecast_runtime.grids import inspect_grid
    runtime=ForecastRuntime(APP/'data/forecast_runtime')
    if args.source_dir:
        root=args.source_dir
        result=runtime.store.import_sources(root/'m_fcst_site.sql',root/'m_warn_tide_info_town(1).sql',root/'ZJTIDE2026.zip',root/'站点名字对应关系(1).txt',args.geometry, runtime.config().get('datafile_station_aliases'))
        print(json.dumps(result,ensure_ascii=False))
    if args.credentials_script:
        # Parse constants rather than execute the supplied download script.
        values={}
        for node in ast.walk(ast.parse(args.credentials_script.read_text(encoding='utf-8-sig'))):
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant):
                for target in node.targets:
                    if isinstance(target,ast.Attribute) and target.attr in ('_id','_code'):values[target.attr]=node.value.value
                    if isinstance(target,ast.Name) and target.id=='salt':values['salt']=node.value.value
        keys={'ZJOCEAN_GRID_ID':values['_id'],'ZJOCEAN_GRID_CODE':values['_code'],'ZJOCEAN_GRID_SALT':values['salt']}
        env=APP/'.env';content=env.read_text(encoding='utf-8-sig')
        for name,value in keys.items():
            if re.search(r'^'+name+r'=',content,re.M):content=re.sub(r'^'+name+r'=.*$',lambda _:name+'='+value,content,flags=re.M)
            else:content=content.rstrip('\n')+'\n'+name+'='+value+'\n'
        env.write_text(content,encoding='utf-8')
        print('Download credentials configured; values omitted.')
    if args.grid_dir:
        for path in args.grid_dir.rglob('*.nc'):
            target=runtime.store.root/'grids'/'initial'/path.name
            target.parent.mkdir(parents=True,exist_ok=True)
            if path.resolve()!=target.resolve():shutil.copyfile(path,target)
            record=inspect_grid(target);runtime.store.register_grid(record)
            print('Registered '+record['filename'])
    if args.sync:
        from dotenv import load_dotenv
        load_dotenv(APP/'.env')
        print(json.dumps(runtime.sync.run(),ensure_ascii=False))


if __name__=='__main__':main()
