"""Report products bind geography, provenance, time coverage and cache identity."""
import copy
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from forecast_runtime.objective_report import ObjectiveReport
from test_objective_report import BEGIN, Engine


ZHOUSHAN = {'id': 'zhoushan', 'label': '舟山近海', 'kind': 'reference_box', 'version': 'scope-test-v1',
            'bounds': [121.5, 29.3, 123.1, 31.0],
            'regions': [{'id': 'n', 'name': '舟山北部参考框', 'bounds': [121.5, 30.1, 123.1, 31.0]},
                        {'id': 's', 'name': '舟山南部参考框', 'bounds': [121.5, 29.3, 123.1, 30.1]}]}
ZHUJIAJIAN = {'id': 'zhujiajian', 'label': '朱家尖附近海域', 'kind': 'reference_box',
             'bounds': [122.3, 29.7, 122.6, 30.0], 'regions': []}


class ProductEngine(Engine):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.maximum_calls = []
        self.sha = 'a' * 64
        self.profile_version = 'temporary-v1'
        self.broken_time = False

    def catalogue(self):
        data = super().catalogue()
        for dataset in data['datasets']:
            dataset['profile'] = {'id': self.profile_version}
            for field in dataset['fields'].values():
                field['source_files'] = [{'filename': 'source.nc', 'sha256': self.sha}]
        return data

    def stats72(self, *, thresholds=None, **kwargs):
        data = super().stats72(**kwargs)
        for name, field in data['fields'].items():
            field['temporal_coverage'] = {'expected_count': len(field['series']),
                'available_count': len(field['series']) - int(self.broken_time),
                'complete': not self.broken_time, 'native_step_seconds': 10800 if name == 'wave' else 3600,
                'missing_times': [(BEGIN + timedelta(hours=6)).isoformat()] if self.broken_time else []}
            field['process'] = {'sample_count': len(field['series']), 'threshold_exceedances': []}
            if thresholds and name in thresholds:
                value = thresholds[name]
                value = value[0] if isinstance(value, list) else value
                threshold = value.get('value') if isinstance(value, dict) else value
                field['process']['threshold_exceedances'].append({'threshold': threshold, 'unit': field['unit'],
                    'comparison': '>', 'origin': 'user_supplied_screening', 'first': BEGIN.isoformat(),
                    'last': (BEGIN + timedelta(hours=3)).isoformat(), 'duration_hours': 3,
                    'maximum_fraction': .5, 'intervals': []})
        return data

    def image_temporal_maximum_png(self, **kwargs):
        self.maximum_calls.append(kwargs)
        return self.image_png(**kwargs)


def product(tmp_path, engine=None, **kwargs):
    engine = engine or ProductEngine()
    report = ObjectiveReport(engine, tmp_path, now=lambda: BEGIN)
    return report, engine, report.report(start=BEGIN.isoformat(), **kwargs)


def test_scope_owns_all_stats_images_labels_and_csv(tmp_path):
    report, engine, output = product(tmp_path, scope=ZHOUSHAN)
    result = output['objective_result']
    assert result['bounds'] == ZHOUSHAN['bounds'] and result['scope_label'] == '舟山近海'
    assert [list(call['bounds']) for call in engine.calls] == [ZHOUSHAN['bounds'], *[r['bounds'] for r in ZHOUSHAN['regions']]]
    assert all(list(call['bounds']) == ZHOUSHAN['bounds'] for call in engine.maximum_calls)
    text = report.artifact_path(output['report_id'], 'report.html').read_text('utf8')
    assert '<h1>舟山近海72小时' in text and '<title>舟山近海72小时' in text
    assert '浙江近海72小时' not in text and '舟山北部参考框' in text
    assert '分区风浪过程' in text and text.count('原生时次曲线') >= 7
    assert '各格点峰值可能发生在不同时刻' in text
    with report.artifact_path(output['report_id'], 'series.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    assert {row['scope_label'] for row in rows} == {'舟山近海'}
    assert '浙江近海参考统计框' not in {row['region_name'] for row in rows}
    assert {'wave_maximum_png', 'wind_maximum_png'} <= output['artifacts'].keys()
    assert 'pressure_maximum_png' not in output['artifacts']


def test_local_island_keeps_local_scope_and_does_not_borrow_zhejiang_regions(tmp_path):
    _, engine, output = product(tmp_path, scope=ZHUJIAJIAN)
    assert len(engine.calls) == 1
    assert output['objective_result']['regions'] == []
    assert '朱家尖附近海域72小时' in output['summary']
    assert output['objective_result']['bounds'] != [119.5, 26.5, 123.5, 31.5]


def test_user_threshold_process_and_internal_missing_times_are_delivered(tmp_path):
    engine = ProductEngine()
    engine.broken_time = True
    report, _, output = product(tmp_path, engine, scope=ZHOUSHAN, thresholds={'wave': [2], 'wind': {'value': 10}})
    assert output['state'] == 'partial'
    assert '预计25个，取得24个' in output['summary']
    assert '超阈持续时长3小时' in output['summary']
    assert '按相邻原生时次两端均超阈的间隔累计' in output['summary']
    assert '50%' in output['summary'] and '不表示官方预警' in output['summary']
    text = report.artifact_path(output['report_id'], 'report.html').read_text('utf8')
    assert '原生时次：24/25' in text and '筛查条件&gt;2 m' in text
    assert output['objective_result']['fields']['wave']['temporal_coverage']['missing_times']


def test_identical_inputs_reuse_atomic_product_without_saving_question(tmp_path):
    report, engine, first = product(tmp_path, scope=ZHOUSHAN, question='用户甲的私有问题')
    before = len(engine.calls), len(engine.maximum_calls)
    second = report.report(start=BEGIN.isoformat(), scope=copy.deepcopy(ZHOUSHAN), question='用户乙的私有问题')
    assert first['report_id'] == second['report_id']
    assert first['cache_hit'] is False and second['cache_hit'] is True
    assert before == (len(engine.calls), len(engine.maximum_calls))
    for name in ('report.html', 'report.json'):
        text = report.artifact_path(first['report_id'], name).read_text('utf8')
        assert '用户甲' not in text and '用户乙' not in text
    assert (tmp_path / first['report_id'] / '.complete.json').is_file()


def test_different_region_time_threshold_fields_source_and_profile_cannot_hit_old_cache(tmp_path):
    report, engine, original = product(tmp_path, scope=ZHOUSHAN)
    responses = [report.report(start=BEGIN.isoformat(), scope=ZHUJIAJIAN),
                 report.report(start=(BEGIN + timedelta(hours=3)).isoformat(), scope=ZHOUSHAN),
                 report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN, thresholds={'wave': 2}),
                 report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN, requested_fields=['wave']),
                 report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN, hours=24)]
    engine.sha = 'b' * 64
    responses.append(report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN))
    engine.profile_version = 'temporary-v2'
    responses.append(report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN))
    assert len({r['report_id'] for r in [original, *responses]}) == 8
    assert not any(r['cache_hit'] for r in responses)


def test_damaged_cache_is_rebuilt_instead_of_returning_half_a_product(tmp_path):
    report, engine, first = product(tmp_path, scope=ZHOUSHAN)
    path = report.artifact_path(first['report_id'], 'wave_maximum.png')
    path.write_bytes(b'truncated')
    count = len(engine.calls)
    second = report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN)
    assert second['report_id'] == first['report_id'] and not second['cache_hit']
    assert len(engine.calls) > count and path.read_bytes().startswith(b'\x89PNG')
    assert not list(tmp_path.glob('.invalid-*'))


def test_two_concurrent_requests_publish_exactly_one_product(tmp_path):
    engine = ProductEngine()
    report_a = ObjectiveReport(engine, tmp_path, now=lambda: BEGIN)
    report_b = ObjectiveReport(engine, tmp_path, now=lambda: BEGIN)
    def call(report):
        return report.report(start=BEGIN.isoformat(), scope=ZHOUSHAN)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(call, [report_a, report_b]))
    assert result[0]['report_id'] == result[1]['report_id']
    assert sum(r['cache_hit'] for r in result) == 1
    assert len(engine.calls) == 3
    assert len([p for p in tmp_path.iterdir() if not p.name.startswith('.')]) == 1
    assert not list(tmp_path.glob('*.lock'))


def test_future_issued_batch_is_not_used_or_reused_after_issuance(tmp_path):
    clock = [BEGIN - timedelta(hours=1)]
    engine = ProductEngine()
    report = ObjectiveReport(engine, tmp_path, now=lambda: clock[0])
    first = report.report(start=BEGIN.isoformat())
    assert first['state'] == 'no_data' and not engine.calls
    clock[0] = BEGIN
    second = report.report(start=BEGIN.isoformat())
    assert second['state'] == 'ready' and second['report_id'] != first['report_id']


@pytest.mark.parametrize('kwargs', [
    {'bounds': [122, 29, 121, 30]},
    {'bounds': [122, 29, 123, float('nan')]},
    {'scope': ZHOUSHAN, 'bounds': ZHUJIAJIAN['bounds']},
    {'scope': {**ZHUJIAJIAN, 'regions': ZHOUSHAN['regions']}},
    {'thresholds': {'wave': float('nan')}},
    {'thresholds': {'wind': {'value': 2, 'unit': 'km/h'}}},
])
def test_bad_geography_or_threshold_rejected_before_stats_or_product_directory(tmp_path, kwargs):
    engine = ProductEngine()
    with pytest.raises(ValueError):
        ObjectiveReport(engine, tmp_path, now=lambda: BEGIN).report(start=BEGIN.isoformat(), **kwargs)
    assert not engine.calls and not list(tmp_path.iterdir())
