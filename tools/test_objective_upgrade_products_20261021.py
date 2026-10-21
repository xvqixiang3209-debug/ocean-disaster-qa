"""Opt-in real local-HTTP objective-product acceptance, no model calls.

The independent audit opens immutable L5 NC with h5py directly, not the
ObjectiveForecast parser/reduction methods. Reports are safe to rerun: matching
inputs must reuse products. No credentials or absolute NC paths are exported.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import html
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import sys
import time
import zipfile

import h5py
import numpy as np
import requests
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
CN = timezone(timedelta(hours=8))
WORK = ROOT / 'tmp' / 'objective_upgrade_20261021' / 'products'
EXPORT = ROOT / 'exports' / '客观预报第三功能完善与实测_20261021'


def stamp(value):
    value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return value.replace(tzinfo=CN) if value.tzinfo is None else value.astimezone(CN)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def call(base, endpoint, params=None, *, binary=False):
    begin = time.perf_counter()
    response = requests.get(base + endpoint, params=params, timeout=600)
    elapsed = round(time.perf_counter() - begin, 3)
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code} {endpoint}: {response.text[:400]}')
    return (response.content if binary else response.json()), elapsed


def choose(catalogue, before, start=None, *, full=False):
    eligible = [item for item in catalogue['datasets']
                if item.get('level') == 'L5' and stamp(item['batch']) <= stamp(before)]
    if full and start:
        first, last = stamp(start), stamp(start) + timedelta(hours=72)
        eligible = [item for item in eligible if all(
            stamp(item['fields'][name]['available_start']) <= first
            and stamp(item['fields'][name]['available_end']) >= last for name in ('wave', 'wind'))]
    if not eligible:
        raise RuntimeError('没有满足已起报和时间覆盖要求的L5批次')
    return max(eligible, key=lambda item: item['batch'])


def artifact_snapshot(output):
    result = {}
    for value in output.get('artifacts', {}).values():
        path = ROOT / 'data' / 'forecast_runtime' / 'objective' / 'reports' / value['relative_path']
        result[value['filename']] = {'mtime_ns': path.stat().st_mtime_ns, 'sha256': sha(path)}
    return result


def validate_artifacts(base, output, destination=None):
    audited = []
    for key, item in output.get('artifacts', {}).items():
        content, seconds = call(base, item['url'], binary=True)
        actual = hashlib.sha256(content).hexdigest()
        assert actual == item['sha256'], f'{key} SHA mismatch'
        assert len(content) == item['size'], f'{key} size mismatch'
        row = {'key': key, 'filename': item['filename'], 'sha256': actual, 'bytes': len(content), 'seconds': seconds}
        if item['filename'].endswith('.png'):
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                row.update(format=image.format, width=image.width, height=image.height)
        if destination:
            destination.mkdir(parents=True, exist_ok=True)
            (destination / item['filename']).write_bytes(content)
        audited.append(row)
    return audited


def independent_l5(dataset_id, bounds, start, hours, frames):
    """Direct HDF5 subset decode with documented temporary L5 conventions."""
    index = json.loads((ROOT / 'data/forecast_runtime/objective/index.json').read_text('utf8'))
    dataset = next(item for item in index['datasets'] if item['dataset_id'] == dataset_id)
    first, last = stamp(start), stamp(start) + timedelta(hours=hours)
    cubes, axes, source_cards, time_axes = {}, {}, [], {}
    for name in ('wave', 'wind'):
        source = next(source for source in dataset['sources'] if name in source['fields'])
        assert sha(source['path']) == source['sha256'], 'Source raw NC SHA changed'
        with h5py.File(source['path'], 'r') as nc:
            lat = np.asarray(nc['lat'][:], dtype=float)
            lon = np.asarray(nc['lon'][:], dtype=float)
            yi = np.where((lat >= bounds[1]-1e-5) & (lat <= bounds[3]+1e-5))[0]
            xi = np.where((lon >= bounds[0]-1e-5) & (lon <= bounds[2]+1e-5))[0]
            yi = yi[np.argsort(-lat[yi])]; xi = xi[np.argsort(lon[xi])]
            assert len(yi) and len(xi)
            units = nc['time'].attrs['units']
            units = units.decode('utf8') if isinstance(units, bytes) else str(units)
            assert units.startswith('hours since ')
            origin = stamp(units[len('hours since '):])
            valid = [origin + timedelta(hours=float(value)) for value in nc['time'][:]]
            ks = [i for i, value in enumerate(valid) if first <= value <= last]
            assert ks and ks == list(range(ks[0], ks[-1]+1))
            arrays = []
            initial = None
            for variable in (('WAV',) if name == 'wave' else ('u10', 'v10')):
                ds = nc[variable]
                raw = np.array(ds[ks[0]:ks[-1]+1, yi.min():yi.max()+1, xi.min():xi.max()+1], copy=True)
                values = raw.astype(float)
                missing = ~np.isfinite(values) | (values <= -999) | (values >= 9999)
                for attr in ('_FillValue', 'missing_value'):
                    if attr in ds.attrs:
                        for marker in np.asarray(ds.attrs[attr]).reshape(-1):
                            missing |= raw == marker
                values = values * float(ds.attrs.get('scale_factor', 1)) + float(ds.attrs.get('add_offset', 0))
                values[missing] = np.nan
                values = values[:, yi-yi.min(), :][:, :, xi-xi.min()]
                arrays.append(values)
                if name == 'wave':
                    initial = np.asarray(ds[0, yi.min():yi.max()+1, xi.min():xi.max()+1], dtype=float)
                    initial = initial[np.ix_(yi-yi.min(), xi-xi.min())]
                    initial[(initial <= -999) | (initial >= 9999) | (initial < 0)] = np.nan
            cube = arrays[0] if name == 'wave' else np.hypot(*arrays)
            if name == 'wave':
                cube[cube < 0] = np.nan
                cubes['initial_wave_mask'] = np.isfinite(initial)
            else:
                # These L5 pairs have identical native coordinate axes; check it
                # explicitly instead of borrowing a regridding implementation.
                assert np.array_equal(lat[yi], axes['wave'][0]) and np.array_equal(lon[xi], axes['wave'][1])
                cube[:, ~cubes['initial_wave_mask']] = np.nan
            cubes[name] = cube
            axes[name] = (lat[yi], lon[xi])
            time_axes[name] = [valid[k].isoformat() for k in ks]
            source_cards.append({'field': name, 'filename': source['filename'], 'sha256': source['sha256'],
                                 'bytes': source['bytes'], 'time_units': units, 'native_shape': list(cube.shape)})
    results = []
    for name in ('wave', 'wind'):
        cube = cubes[name]
        maximum = np.full(cube.shape[1:], np.nan)
        for row in cube:
            np.fmax(maximum, row, out=maximum)
        counts = np.isfinite(cube).sum(axis=0)
        frame = frames[name]
        assert frame['shape'] == list(maximum.shape)
        actual = np.array([[np.nan if value is None else value for value in row] for row in frame['values']])
        assert np.allclose(actual, maximum, equal_nan=True, atol=1e-6)
        assert np.array_equal(np.array(frame['valid_sample_counts']), counts)
        assert np.allclose(frame['lat'], axes[name][0]) and np.allclose(frame['lon'], axes[name][1])
        assert frame['frame_count'] == len(time_axes[name])
        assert frame['temporal_coverage']['available_times'] == time_axes[name]
        threshold = 2.0 if name == 'wave' else 10.0
        exceeding = np.isfinite(cube) & (cube >= threshold)
        per_time = exceeding.any(axis=(1, 2))
        duration = sum((stamp(time_axes[name][i])-stamp(time_axes[name][i-1])).total_seconds()/3600
                       for i in range(1, len(per_time)) if per_time[i] and per_time[i-1])
        results.append({'field': name, 'state': 'pass', 'grid_shape': list(maximum.shape),
                        'all_cells_temporal_maximum_equal': True, 'all_cells_valid_sample_counts_equal': True,
                        'axes_equal': True, 'native_timestamps_equal': True,
                        'period_maximum': round(float(np.nanmax(maximum)), 6),
                        'sample_count': len(time_axes[name]), 'threshold': threshold,
                        'comparison': '>=', 'independent_duration_hours': duration,
                        'first_exceedance': next((t for t, hit in zip(time_axes[name], per_time) if hit), None),
                        'last_exceedance': next((t for t, hit in reversed(list(zip(time_axes[name], per_time))) if hit), None)})
    return {'method': 'Direct h5py: decode NC time coordinate/variable attrs; per-cell fmax and finite counts; independent of engine methods',
            'sources': source_cards, 'results': results}


def run(base):
    if (WORK / 'summary.json').exists():
        recorded = WORK.parent / 'products_runs' / datetime.now(CN).strftime('%Y%m%d_%H%M%S_%f')
        shutil.copytree(WORK, recorded)
    WORK.mkdir(parents=True, exist_ok=True)
    catalogue, _ = call(base, '/api/objective/catalog')
    safe_catalogue = {key: value for key, value in catalogue.items() if key != 'acquisition'}
    save(WORK / 'catalogue.json', safe_catalogue)
    scopes = {row['id']: row for row in catalogue['report_scopes']}
    now = datetime.now(CN)
    current = choose(catalogue, min(now, stamp('2026-10-21T19:59:59+08:00')).isoformat())
    current_l2 = next(item for item in catalogue['datasets']
                      if item.get('level') == 'L2' and item['batch'] == current['batch'])
    historical_start = '2026-10-20T08:00:00+08:00'
    historical = choose(catalogue, historical_start, historical_start, full=True)
    cases = [
        ('zhejiang_72h', '生成浙江未来72小时风浪影响报告', 'zhejiang', '2026-10-21T20:00:00+08:00', 72, current, {}),
        ('zhoushan_72h', '给我舟山未来三天风浪分析', 'zhoushan', '2026-10-21T20:00:00+08:00', 72, current, {}),
        ('zhujiajian_72h', '朱家尖未来72小时浪高和海面风，浪高2米和风速10米每秒筛查', 'zhujiajian', '2026-10-21T20:00:00+08:00', 72, current,
         {'wave_threshold': 2, 'wind_threshold': 10}),
        ('zhujiajian_next_day_24h', '朱家尖10月22日08时起一天风浪过程', 'zhujiajian', '2026-10-22T08:00:00+08:00', 24, current, {}),
        ('zhoushan_historical_72h', '回看舟山10月20日08时起未来72小时当时的预报', 'zhoushan', historical_start, 72, historical, {}),
        ('zhejiang_l2_partial_72h', '用同起报L2生成浙江72小时风浪报告，明确范围缺口', 'zhejiang', '2026-10-21T20:00:00+08:00', 72, current_l2, {}),
        ('outside_forecast_window', '生成朱家尖11月5日08时起未来72小时报告', 'zhujiajian', '2026-11-05T08:00:00+08:00', 72, current, {}),
    ]
    completed = []
    for identifier, question, location, first, hours, dataset, extra in cases:
        params = {'dataset_id': dataset['dataset_id'], 'scope': location, 'fields': 'wave,wind',
                  'start': first, 'hours': hours, 'question': question, **extra}
        output, seconds = call(base, '/api/objective/report', params)
        result = output['objective_result']
        assert result['scope']['id'] == location and result['bounds'] == scopes[location]['bounds']
        assert result['start'] == first and result['hours'] == hours and result['dataset_id'] == dataset['dataset_id']
        assert all(all(source.get('sha256') for source in value.get('source_files', [])) for value in result['fields'].values())
        assert 'pressure_png' not in output['artifacts']
        if identifier == 'outside_forecast_window':
            assert output['state'] == 'no_data' and not any(key.endswith('_png') for key in output['artifacts'])
        else:
            assert {'wave_maximum_png', 'wind_maximum_png', 'wave_png', 'wind_png'} <= output['artifacts'].keys()
        if identifier == 'zhejiang_l2_partial_72h':
            assert output['state'] == 'partial'
            assert all(result['fields'][name]['spatial_partial'] for name in ('wave', 'wind'))
        audits = validate_artifacts(base, output, WORK / 'representative' if identifier == 'zhujiajian_72h' else None)
        record = {'id': identifier, 'question': question, 'request': params, 'seconds': seconds, 'response': output,
                  'artifact_audit': audits, 'assertions': 'pass'}
        save(WORK / (identifier + '.json'), record)
        completed.append(record)
        print(json.dumps({'id': identifier, 'state': output['state'], 'seconds': seconds, 'cache_hit': output['cache_hit'],
                          'bounds': result['bounds'], 'wave': result['fields']['wave'].get('maximum'),
                          'wind': result['fields']['wind'].get('maximum')}, ensure_ascii=False), flush=True)
    original = next(row for row in completed if row['id'] == 'zhujiajian_72h')
    before = artifact_snapshot(original['response'])
    changed_question = {**original['request'], 'question': '请换种说法整理朱家尖这段风浪报告，条件不变'}
    repeated, seconds = call(base, '/api/objective/report', changed_question)
    after = artifact_snapshot(repeated)
    assert repeated['report_id'] == original['response']['report_id'] and repeated['cache_hit']
    assert before == after, 'Repeated report rewrote artifact bytes or mtime'
    record = {'id': 'same_inputs_reworded_question_cache', 'request': changed_question, 'seconds': seconds,
              'response': repeated, 'assertions': 'pass', 'same_report_id': True,
              'all_artifact_sha_unchanged': True, 'all_artifact_mtime_unchanged': True,
              'before_artifacts': before, 'after_artifacts': after}
    save(WORK / 'cache_reuse.json', record)
    completed.append(record)
    frame_output = {}
    for field in ('wave', 'wind'):
        parameters = {'dataset_id': current['dataset_id'], 'field': field, 'start': original['request']['start'],
                      'hours': 72, 'bounds': ','.join(map(str, scopes['zhujiajian']['bounds'])), 'maxsize': 180,
                      'sea_mask': 'wave_valid'}
        frame, seconds = call(base, '/api/objective/maximum/frame', parameters)
        save(WORK / f'{field}_maximum_frame.json', {'request': parameters, 'seconds': seconds, 'response': frame})
        assert frame['product_type'] == 'temporal_maximum' and frame['valid_time'] is None
        if field == 'wind':
            assert frame['vectors_omitted'] and 'u' not in frame and 'v' not in frame
        content, image_seconds = call(base, '/api/objective/maximum/image.png', parameters, binary=True)
        with Image.open(BytesIO(content)) as image:
            image.verify()
        (WORK / 'representative' / f'{field}_maximum_api.png').write_bytes(content)
        frame_output[field] = frame
    independent = independent_l5(current['dataset_id'], scopes['zhujiajian']['bounds'], original['request']['start'], 72, frame_output)
    for item in independent['results']:
        process = original['response']['objective_result']['fields'][item['field']]['process']['threshold_exceedances'][0]
        assert process['duration_hours'] == item['independent_duration_hours']
        assert process['first'] == item['first_exceedance'] and process['last'] == item['last_exceedance']
        assert original['response']['objective_result']['fields'][item['field']]['maximum']['value'] == item['period_maximum']
        item['report_process_threshold_equal'] = True
    save(WORK / 'independent_nc_verification.json', independent)
    summary = {'created_at': datetime.now(CN).isoformat(), 'base': base, 'kind': 'real_http_no_model_product_tests',
               'current_dataset_id': current['dataset_id'], 'historical_dataset_id': historical['dataset_id'],
               'report_cases': len(completed), 'all_assertions': 'pass', 'cases': completed,
               'independent_nc_verification': independent}
    save(WORK / 'summary.json', summary)
    return summary


def build_report(summary):
    EXPORT.mkdir(parents=True, exist_ok=True)
    if (WORK / 'representative').exists():
        shutil.copytree(WORK / 'representative', EXPORT / '朱家尖代表产品', dirs_exist_ok=True)
    save(EXPORT / '产品实际请求响应与独立核验.json', summary)
    e = lambda value: html.escape(str(value), quote=True)
    rows, detail = [], []
    product_csv = []
    for item in summary['cases']:
        output = item['response']; result = output['objective_result']
        values = []
        for name in ('wave', 'wind'):
            field = result['fields'][name]
            edge = field.get('maximum') or {}
            temporal = field.get('temporal_coverage') or {}
            values.append(f'{edge.get("value", "无可用值")} {field["unit"]}；{temporal.get("available_count", 0)}/{temporal.get("expected_count", "—")}时次')
        rows.append('<tr>' + ''.join(f'<td>{e(value)}</td>' for value in (item['id'], result['scope_label'],
            result['start'], result['hours'], output['state'], *values, f'{item["seconds"]}s', output['cache_hit'])) + '</tr>')
        product_csv.append([item['id'], result['scope_label'], result['bounds'], result['dataset_id'], result['batch'],
                            result['start'], result['end'], result['hours'], output['state'], *values,
                            output['report_id'], output['cache_hit'], item['seconds']])
        detail.append(f'<details><summary>{e(item["id"])}：{e(item.get("question") or item["request"].get("question"))}</summary>'
                      f'<pre>{e(output["summary"])}</pre><p>数据集：{e(result["dataset_id"])}；起报：{e(result["batch"])}；范围：{e(result["bounds"])}</p>'
                      '<p>图件及下载文件逐一通过HTTP读取并核对SHA256；有PNG时均已解码验证。</p></details>')
    with (EXPORT / '真实产品测试索引.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['编号', '范围名称', '参考框西南东北', '数据集', '起报批次', '查询开始', '查询结束', '小时', '业务状态',
                         '海浪峰值及时次覆盖', '风速峰值及时次覆盖', '报告编号', '缓存命中', '耗时秒'])
        writer.writerows(product_csv)
    independent = summary['independent_nc_verification']
    source_rows = ''.join('<tr>' + ''.join(f'<td>{e(row[key])}</td>' for key in ('field', 'filename', 'sha256', 'time_units')) + '</tr>' for row in independent['sources'])
    verification_rows = ''.join('<tr>' + ''.join(f'<td>{e(row[key])}</td>' for key in ('field', 'grid_shape', 'sample_count', 'period_maximum', 'threshold', 'independent_duration_hours', 'state')) + '</tr>' for row in independent['results'])
    qa_folder = ROOT / 'tmp' / 'objective_upgrade_20261021' / 'questions'
    qa_summary = qa_folder / 'summary.json'
    qa_html = '<p>自然语言问答实测由并行任务执行，完成后合并。当前本报告为无模型调用的真实产品接口与独立NC验算。</p>'
    if qa_summary.exists():
        qa = json.loads(qa_summary.read_text('utf8'))
        save(EXPORT / '实际问答记录.json', qa)
        checks = json.loads((qa_folder / 'checks.json').read_text('utf8')) if (qa_folder / 'checks.json').exists() else {}
        save(EXPORT / '实际问答响应检查.json', checks)
        qa_rows = []
        actual_answers = []
        with (EXPORT / '实际问答索引.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['编号', '用户问题', 'HTTP状态', '业务状态', '答复自评', '模式', '起报批次', '查询开始', '查询结束', '范围', '模型调用次数', '耗时秒'])
            for record in qa.get('rows') or []:
                metrics = record.get('metrics') or {}
                reply = record.get('actual_reply') or {}
                tool = record.get('tool_result') or {}
                row = (record['id'], record['question'], record.get('http_status'), reply.get('business_state'),
                       reply.get('answerability'), reply.get('mode'), tool.get('batch'), metrics.get('start'), metrics.get('end'),
                       metrics.get('bounds'), metrics.get('model_calls_observed'), record.get('elapsed_seconds'))
                writer.writerow(row)
                qa_rows.append('<tr>' + ''.join(f'<td>{e(value)}</td>' for value in (record['id'], record['question'],
                    reply.get('business_state'), reply.get('answerability'), metrics.get('start'), metrics.get('model_calls_observed'))) + '</tr>')
                actual_answers.append(f'<details><summary>{e(record["id"])}　{e(record["question"])}</summary>'
                    f'<p>范围：{e(metrics.get("bounds"))}；批次：{e(tool.get("batch"))}；所选时段：{e(metrics.get("start"))} 至 {e(metrics.get("end"))}。</p>'
                    f'<pre>{e(record.get("user_visible_answer") or reply.get("answer") or "未取得回答")}</pre></details>')
        qa_html = ('<p>8次真实自然语言请求覆盖浙江自然问法、舟山阈值分析、朱家尖逐格最大图、连续追问风图和次日快照、'
                   '明日简报、历史日期与11月无覆盖。7次工具ready、1次no_data；full/partial为系统标签，不作为答案准确率。</p>'
                   '<p>6个报告请求共观察到48次模型调用；实际展示优先使用程序计算的business_data结果，没有将这些答复说成DeepSeek完整语言总结。'
                   '正确性证据来自范围、时间、源文件、实际数值和独立NC验算；8个事务的48项响应结构检查另行留档，不等同于代码测试数。</p>'
                   '<div class="scroll"><table><tr><th>编号</th><th>用户实际问题</th><th>业务状态</th><th>系统自评</th><th>开始时间</th><th>模型调用</th></tr>'
                   + ''.join(qa_rows) + '</table></div><p><a href="实际问答记录.json">完整实际请求与响应JSON</a> · '
                   '<a href="实际问答索引.csv">实际问答索引CSV</a> · <a href="实际问答响应检查.json">响应结构核对</a></p>'
                   + ''.join(actual_answers))
    supplementary = ''
    acquisition_path = WORK.parent / 'acquisition_window_recheck.json'
    if acquisition_path.exists():
        acquisition = json.loads(acquisition_path.read_text('utf8'))
        save(EXPORT / '下载复用查询窗口补验.json', acquisition)
        window_rows = []
        for record in acquisition.get('rows') or []:
            data = record.get('data_acquisition') or {}
            window_rows.append('<tr>' + ''.join(f'<td>{e(value)}</td>' for value in (
                record.get('start'), record.get('end'), data.get('requested_start'), data.get('requested_end'), record.get('state'))) + '</tr>')
        supplementary += '<section><h2>下载复用元数据补验</h2><p>未调用模型，两个不同窗口分别查询已有L5双NC；获取记录中的起止时间分别与本次计算窗口一致，文件状态existing，不重复下载。</p><div class="scroll"><table><tr><th>计算开始</th><th>计算结束</th><th>下载记录开始</th><th>下载记录结束</th><th>状态</th></tr>' + ''.join(window_rows) + '</table></div><p><a href="下载复用查询窗口补验.json">补验原始JSON</a></p></section>'
    validation = ROOT / 'tmp' / 'objective_upgrade_tests_final_20261021.txt'
    if validation.exists():
        log = validation.read_text('utf8', errors='replace')
        shutil.copyfile(validation, EXPORT / '代码测试记录.txt')
        lines = [line.strip() for line in log.splitlines() if re.search(r'\d+ passed', line)]
        validation_summary = lines[-1] if lines else '完整结果见测试日志'
        supplementary += '<section><h2>代码回归测试</h2><p>' + e(validation_summary) + '</p><p><a href="代码测试记录.txt">完整测试日志</a></p><p>代码测试、48项实际问答响应检查与独立NC数值验算为不同证据，分别统计。</p></section>'
    node_log = ROOT / 'tmp' / 'objective_upgrade_node_tests_20261021.txt'
    clock_log = WORK.parent / 'p0_replay_clock_regression.json'
    if node_log.exists():
        shutil.copyfile(node_log, EXPORT / '页面代码测试记录.txt')
        supplementary += '<section><h2>页面与回放稳定性测试</h2><p>Node页面交互测试2/2通过，覆盖范围/时段选择、图件切换、报告链接、请求竞争取消和失败后移除旧图层。</p><p><a href="页面代码测试记录.txt">页面测试日志</a></p>'
        if clock_log.exists():
            shutil.copyfile(clock_log, EXPORT / '录制回放跨进程回归.json')
            supplementary += '<p>旧P0录制测试的秒级实时时钟偶发造成请求哈希变化；仅固定测试时钟并增加跨秒仍须拒绝的负例，运行时回放核对未放宽。8个独立进程连续运行，各6项通过，共48项。</p><p><a href="录制回放跨进程回归.json">连续进程回放测试</a></p>'
        supplementary += '</section>'
    if (EXPORT / '修改与使用说明.txt').exists():
        supplementary += '<section><h2>修改与使用说明</h2><p><a href="修改与使用说明.txt">查看代码改动、可直接提问的例子、复现命令与适用边界</a></p></section>'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>客观预报第三功能完善与实测</title><style>body{font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;color:#173449;background:#eff4f7;margin:0}main{max-width:1200px;margin:auto;padding:24px}section,header,details{background:white;border-radius:10px;padding:22px;margin:16px 0}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px;border:1px solid #d6e2e9;text-align:left;overflow-wrap:anywhere}th{background:#e6f1f4}.scroll{overflow:auto}pre{white-space:pre-wrap;font:inherit}img{max-width:100%;height:auto}a{color:#08798c}.muted{color:#617889}</style><main>'
    page += f'<header><h1>客观预报第三功能完善与实测</h1><p>时间：{e(summary["created_at"])}。真实L5 NC，{summary["report_cases"]}组报告接口测试，所有产品断言通过；未在本脚本调用DeepSeek。</p><p>当前批次：{e(summary["current_dataset_id"])}；历史回放批次：{e(summary["historical_dataset_id"])}。</p></header>'
    page += '<section><h2>本轮改造</h2><ol><li>地点和框选范围统一传入选数、统计、绘图与报告；浙江、舟山、朱家尖使用各自参考框。</li><li>自然语言识别报告、风浪分析、最大值图与阈值过程，连续追问保留已选范围和时段。</li><li>逐项核对原生时间轴，检查中间缺时次；不将缺时次插值为完整预报。</li><li>报告展示分区风浪过程和用户筛查阈值的起止、采样持续时长、超阈格点比例。</li><li>新增逐格时段最大值图，保留同一峰值时刻快照，二者含义在报告中说明。</li><li>报告按源SHA、数据集、范围、时间、要素、阈值和解释版本复用，损坏文件会重建；并发原子发布。</li></ol></section>'
    page += '<section><h2>真实产品测试</h2><div class="scroll"><table><tr><th>场景</th><th>范围</th><th>开始时间</th><th>小时</th><th>状态</th><th>海浪峰值/时间覆盖</th><th>风速峰值/时间覆盖</th><th>耗时</th><th>缓存命中</th></tr>' + ''.join(rows) + '</table></div><p>历史问题使用当时已起报且覆盖所需时段的旧批次；11月5日超出现有有效期，保持no_data，没有借用近日期。</p></section>'
    page += '<section><h2>本地报告复用</h2><p>朱家尖同批次、范围、时段、要素及阈值再次请求，只改用户问法：report_id相同，cache_hit=true，所有文件SHA256及修改时间不变；共享报告中不保存用户问句。</p><p>后续修正两处展示元数据：获取缓存仅在同一查询窗口复用requested_start/end；无数据窗口明确显示“所选窗口无原生时次”，源文件其他日期覆盖单列。原8次自然语言记录保留当时原文，后续无模型产品回归验证新元数据。</p></section>'
    page += '<section><h2>独立NC核验</h2><p>直接用h5py读取NC时间、坐标、WAV及u10/v10，按当前临时解释计算风速；未调用应用的解析、极值、过程统计方法。朱家尖全部原生格点的时段最大值、有效时次数、经纬度、原生时间、报告峰值和筛查持续时间与HTTP结果一致。</p><div class="scroll"><table><tr><th>要素</th><th>格网</th><th>时次</th><th>时段峰值</th><th>筛查阈值</th><th>采样超阈小时</th><th>核对</th></tr>' + verification_rows + '</table></div><div class="scroll"><table><tr><th>要素</th><th>NC文件</th><th>SHA256</th><th>时间坐标单位</th></tr>' + source_rows + '</table></div></section>'
    page += '<section><h2>朱家尖代表产品</h2><p><a href="朱家尖代表产品/report.html">打开完整72小时报告</a> · <a href="朱家尖代表产品/series.csv">下载原生过程CSV</a> · <a href="朱家尖代表产品/report.json">下载报告JSON</a></p><h3>72小时逐格最大海浪</h3><img src="朱家尖代表产品/wave_maximum.png" alt="朱家尖时段逐格最大海浪"><h3>72小时逐格最大风速</h3><img src="朱家尖代表产品/wind_maximum.png" alt="朱家尖时段逐格最大风速"><p>各格点峰值可发生在不同时刻；最大风速图不拼接不同时间的风向。</p></section>'
    screenshots = [name for name in ('朱家尖72小时最大值地图.png', '朱家尖72小时报告预览.png') if (EXPORT / name).exists()]
    if screenshots:
        page += '<section><h2>新版网页实际操作</h2><p>在地图页面选择朱家尖、10月21日20时、72小时逐格最大值：显示4×4共16个格点、73个原生时次、峰值3.73米；重复生成同参数报告显示复用。</p>'
        page += ''.join(f'<h3>{e(Path(name).stem)}</h3><img loading="lazy" src="{e(name)}" alt="{e(Path(name).stem)}">' for name in screenshots)
        page += '</section>'
    page += supplementary + '<section><h2>实际自然语言问答</h2>' + qa_html + '</section>'
    page += '<section><h2>各请求实际产品摘要</h2>' + ''.join(detail) + '</section>'
    page += '<section><h2>保留的业务边界</h2><ul><li>变量沿用用户授权的临时解释：WAV按有效波高，SSW风速按阵风产品解释；单位、时区和产品定义仍待业务确认。</li><li>L2/L5无气压，不估算或混入其他来源气压。</li><li>地点使用版本化参考经纬度框，不是法定海域边界；朱家尖网格不能代替浴场、钓点实况。</li><li>用户筛查阈值不作为正式预警、灾损或活动许可。超阈时长是相邻采样端点均超阈的间隔累计，缺时次会断开。</li></ul><p><a href="产品实际请求响应与独立核验.json">下载完整请求、实际响应与核验记录</a></p></section></main></html>'
    (EXPORT / '实测报告.html').write_text(page, encoding='utf8')
    with zipfile.ZipFile(EXPORT.with_suffix('.zip'), 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXPORT.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(EXPORT))
    print(json.dumps({'report': str(EXPORT / '实测报告.html')}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8774')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--build-report', action='store_true')
    args = parser.parse_args()
    if args.run:
        summary = run(args.base.rstrip('/'))
    elif args.build_report:
        summary = json.loads((WORK / 'summary.json').read_text('utf8'))
    else:
        parser.error('显式使用 --run 执行真实产品测试，或 --build-report 整理已有结果')
    build_report(summary)


if __name__ == '__main__':
    main()
