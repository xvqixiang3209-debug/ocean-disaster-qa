"""Repeatable local inspection and point extraction for the project's L2/L5 NCs.

Uses the same reader and persistent point cache as the question-answer service.
Does not download data, call a model, or infer missing product definitions.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from forecast_runtime.grids import GridReader, inspect_grid
from forecast_runtime.parsed import ParsedGridStore
from forecast_runtime.storage import BEIJING, ForecastStore, local_now


def stamp(value, end=False):
    """Normalize before comparison: the shared reader compares ISO strings."""
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if len(value) == 10 and end:
        result += timedelta(hours=23, minutes=59, seconds=59)
    if result.tzinfo is None:
        result = result.replace(tzinfo=BEIJING)
    return result.astimezone(BEIJING).isoformat()


def warnings_for(record):
    warnings = ['模式预报数值，不是现场实测；解析成功不代表已完成预报精度验证。']
    if record.get('time_timezone_assumed'):
        warnings.append('文件时间未注明时区，本工具沿用系统假设：北京时间（+08:00）。')
    if record.get('time_origin') == 'filename_assumed':
        warnings.append('时间单位仅写initial time，时间基点按文件名批次假设取得。')
    notes = {
        'SWL': 'SWL仅标水位：纯增水还是总潮位、高程基准尚未核定；本工具不叠加天文潮。',
        'SSW': 'SSW由u10/v10计算矢量模；阵风、平均风及平均时段尚未核定。',
        'WAV': 'WAV按文件原始描述展示；L5最大有效波高的统计口径尚未核定，异常MWD不解释为波向。',
        'OCU': 'OCU由u/v计算矢量模；水层未说明，不能据此确认浴场离岸流。',
        'SST': 'SST按原单位展示；Celsis D拼写不规范，不在此自动换成已核定摄氏度。',
    }
    if record['product'] in notes:
        warnings.append(notes[record['product']])
    warnings.append('区域格点不代表具体浴场、礁石、滩涂或房屋，不能单独据此确认活动安全。')
    return warnings


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'point'):
        command = commands.add_parser(name, help='查看文件目录' if name == 'inspect' else '提取海点时间序列')
        command.add_argument('--input', '-i', type=Path, default=APP / 'data/forecast_runtime/grids',
                             help='现有L2/L5 NC文件或目录；目录递归查找.nc')
        command.add_argument('--batch', help='指定文件名中的起报批次，例如202610162000')
        command.add_argument('--level', choices=('L2', 'L5'))
        command.add_argument('--output', '-o', type=Path, help='JSON输出路径；point同时生成同名CSV')
        if name == 'point':
            command.add_argument('--lon', required=True, type=float)
            command.add_argument('--lat', required=True, type=float)
            command.add_argument('--start', required=True, help='ISO时间；未写时区按北京时间')
            command.add_argument('--end', required=True, help='ISO时间，包含该时刻；仅日期表示当天结束')
            command.add_argument('--variable', help='默认读取对应产品；SSW/OCU自动合成矢量模')
            command.add_argument('--max-distance', type=float, default=20, help='最近有效格点最大距离km，默认20')
            command.add_argument('--cache-dir', type=Path, default=APP / 'data/forecast_runtime',
                                 help='持久缓存目录，默认复用问答系统的forecast.sqlite3')
    return parser


def collect(args):
    source = args.input.resolve()
    if not source.exists():
        raise ValueError('输入文件或目录不存在：' + str(source))
    if args.batch:
        if not re.fullmatch(r'\d{12}', args.batch):
            raise ValueError('--batch格式为YYYYmmddHHMM，例如202610162000')
        datetime.strptime(args.batch, '%Y%m%d%H%M')
    paths = sorted(source.rglob('*.nc')) if source.is_dir() else [source]
    if args.batch:
        paths = [p for p in paths if '_' + args.batch + '_' in p.name]
    if args.level:
        paths = [p for p in paths if p.name.endswith('_' + args.level + '.nc')]
    if not paths:
        raise ValueError('没有匹配的NC文件，请检查--input、--batch和--level')
    records, errors, duplicates, seen = [], [], [], set()
    for path in paths:
        try:
            record = inspect_grid(path)
            identity = (record['id'], record['batch'], record['product'], record['level'])
            if identity in seen:
                duplicates.append(str(path.resolve()))
                continue
            seen.add(identity)
            records.append(record)
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            errors.append({'file': str(path.resolve()), 'error': str(exc),
                           'supported_format': 'NetCDF4/HDF5；一维lat/lon；time×lat×lon变量；现有L2/L5产品命名'})
    if args.command == 'point' and records:
        if len({r['batch'] for r in records}) > 1:
            raise ValueError('目录含多个起报批次，请指定--batch；不会拼接不同批次')
        products = {}
        for record in records:
            products.setdefault(record['product'], set()).add(record['level'])
        if any(len(levels) > 1 for levels in products.values()):
            raise ValueError('同一产品含多个L2/L5版本，请指定--level')
    return records, errors, duplicates


def validate_output(path, records, source, point):
    targets = [path, path.with_suffix('.csv')] if point else [path]
    if path.suffix.lower() != '.json':
        raise ValueError('--output需要以.json结尾；原始NC不会被覆盖')
    protected = {Path(r['path']).resolve() for r in records}
    if source.is_file():
        protected.add(source.resolve())
    for target in targets:
        if target.resolve() in protected:
            raise ValueError('输出路径不能覆盖输入文件')
        if target.exists() and not target.is_file():
            raise ValueError('输出路径已存在且不是文件：' + str(target))


def save(report, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    # Replace only complete output; interrupted writes do not leave a valid-looking report.
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(target)
    if report['kind'] != 'point':
        return
    csv_path = target.with_suffix('.csv')
    temporary = csv_path.with_name(csv_path.name + '.tmp')
    fields = ['filename', 'source_id', 'product', 'variable', 'unit', 'batch',
              'requested_lon', 'requested_lat', 'grid_lon', 'grid_lat', 'distance_km',
              'time', 'value', 'time_timezone_assumed', 'interpretation_status']
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in report['files']:
            record, result = item['metadata'], item.get('point', {})
            for value in result.get('series', []):
                writer.writerow({'filename': record['filename'], 'source_id': record['id'],
                                 'product': record['product'], 'variable': result['variable'],
                                 'unit': result['unit'], 'batch': record['batch'],
                                 'requested_lon': report['query']['lon'], 'requested_lat': report['query']['lat'],
                                 'grid_lon': result.get('grid_lon'), 'grid_lat': result.get('grid_lat'),
                                 'distance_km': result.get('distance_km'), **value,
                                 'time_timezone_assumed': record.get('time_timezone_assumed'),
                                 'interpretation_status': '产品口径待核定；详见同名JSON的warnings'})
    temporary.replace(csv_path)


def main(argv=None):
    args = make_parser().parse_args(argv)
    started = time.perf_counter()
    try:
        query = None
        if args.command == 'point':
            if not (math.isfinite(args.lon) and -180 <= args.lon <= 180
                    and math.isfinite(args.lat) and -90 <= args.lat <= 90):
                raise ValueError('经纬度必须为有限数值，经度-180至180，纬度-90至90')
            if not math.isfinite(args.max_distance) or args.max_distance <= 0:
                raise ValueError('--max-distance必须为正的有限数值')
            query = {'lon': args.lon, 'lat': args.lat, 'start': stamp(args.start), 'end': stamp(args.end, end=True)}
            if query['start'] > query['end']:
                raise ValueError('--end不能早于--start')
        records, errors, duplicates = collect(args)
        signature = hashlib.sha256(json.dumps({'kind': args.command, 'ids': [r['id'] for r in records],
                                               'query': query, 'variable': getattr(args, 'variable', None),
                                               'max_distance': getattr(args, 'max_distance', None)}, sort_keys=True).encode()).hexdigest()[:12]
        output = (args.output or APP / 'exports/nc_quick' / (args.command + '_' + signature + '.json')).resolve()
        validate_output(output, records, args.input, args.command == 'point')
        report = {'kind': args.command, 'status': 'ready', 'generated_at': local_now().isoformat(),
                  'files': [], 'errors': errors, 'duplicate_files_skipped': duplicates,
                  'output_json': str(output)}
        if query:
            report['query'] = query
            report['output_csv'] = str(output.with_suffix('.csv'))
            report['query_time_note'] = '查询时间统一为+08:00；未写时区的查询输入按北京时间。'
        # Inspect stays read-only. Point extraction shares the application's persistent cache.
        cache = ParsedGridStore(ForecastStore(args.cache_dir), GridReader()) if query and records else None
        for record in records:
            item = {'metadata': record, 'warnings': warnings_for(record)}
            if query:
                variable = args.variable or record['product']
                try:
                    if not any(v['name'] == variable for v in record['variables']):
                        raise ValueError('文件没有可读取的变量：' + variable)
                    result = cache.point(record, variable, args.lon, args.lat, query['start'], query['end'], args.max_distance)
                    series = result.get('series', [])
                    result['minimum'] = min(series, key=lambda row: row['value']) if series else None
                    result['maximum'] = max(series, key=lambda row: row['value']) if series else None
                    result['expected_samples'] = sum(query['start'] <= t <= query['end'] for t in record['times'])
                    result['valid_samples'] = len(series)
                    item['point'] = result
                except (OSError, ValueError, KeyError, TypeError, OverflowError, sqlite3.Error) as exc:
                    report['errors'].append({'file': record['filename'], 'error': str(exc)})
                    item['point'] = {'state': 'error', 'series': [], 'minimum': None, 'maximum': None}
            report['files'].append(item)
        if not records:
            report['status'] = 'error'
        elif query:
            ready = sum(bool(item['point'].get('series')) for item in report['files'])
            if not ready:
                report['status'] = 'error' if report['errors'] else 'no_data'
            elif report['errors'] or any(item['point'].get('state') != 'ready' or item['point'].get('partial') for item in report['files']):
                report['status'] = 'partial'
        elif report['errors']:
            report['status'] = 'partial'
        report['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        save(report, output)
        summary = {'status': report['status'], 'files': len(records), 'errors': len(report['errors']),
                   'output_json': str(output), 'elapsed_seconds': report['elapsed_seconds']}
        if query:
            summary['output_csv'] = report['output_csv']
            summary['cached_files'] = sum(bool(item['point'].get('parsed_cache_hit')) for item in report['files'])
        print(json.dumps(summary, ensure_ascii=False))
        return {'ready': 0, 'partial': 3, 'no_data': 3, 'error': 2}[report['status']]
    except sqlite3.Error as exc:
        print(json.dumps({'status': 'error', 'error': '缓存数据库不可用：' + str(exc),
                          'action': '检查缓存目录，或用--cache-dir指定另一个目录；未删除或重建原数据库。'},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
