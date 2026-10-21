import copy
import csv
import hashlib
import io
import json
from datetime import datetime, timedelta

import pytest
from PIL import Image

from forecast_runtime.objective_report import CN, FIELDS, REGIONS, ObjectiveReport, ZHEJIANG_BOUNDS, _plot_svg


BEGIN = datetime(2026, 10, 20, 8, tzinfo=CN)


class Engine:
    def __init__(self, *, available_hours=72, image_error=False):
        self.calls = []
        self.available_hours = available_hours
        self.image_error = image_error

    def catalogue(self):
        return {'default_dataset_id': 'test-a', 'datasets': [
            {'dataset_id': value, 'batch': BEGIN.isoformat(), 'fields': {
                key: {'unit': unit, 'source_file_id': f'{key}-id'}
                for key, unit in [('wave', 'm'), ('wind', 'm/s'), ('pressure', 'hPa')]}}
            for value in ('test-a', 'test-b')]}

    def stats72(self, dataset_id, start=None, hours=72, bounds=None, sea_mask=None):
        self.calls.append({'dataset_id': dataset_id, 'start': start, 'hours': hours,
                           'bounds': bounds, 'sea_mask': sea_mask})
        requested = datetime.fromisoformat(start)
        requested_end = requested + timedelta(hours=hours)
        available_end = BEGIN + timedelta(hours=self.available_hours)
        fields = {}
        for key, unit, step, value in [('wave', 'm', 3, 8.12567), ('wind', 'm/s', 1, 32.75), ('pressure', 'hPa', 1, 990.25)]:
            series = []
            for index in range(0, self.available_hours + 1, step):
                stamp = BEGIN + timedelta(hours=index)
                if not requested <= stamp <= requested_end:
                    continue
                series.append({'time': stamp.isoformat(), 'maximum': {'value': value + 1, 'lon': 122.5, 'lat': 29.5},
                               'minimum': {'value': value, 'lon': 122.5, 'lat': 29.5},
                               'valid_count': 4, 'missing_count': 0, 'sea_count': 4})
            peak_time = series[-1]['time'] if series else None
            fields[key] = {'unit': unit, 'series': series, 'state': 'ready' if series else 'no_data',
                           'maximum': {'value': value + 1 if series else None, 'lon': 122.5, 'lat': 29.5, 'time': peak_time},
                           'minimum': {'value': value if series else None, 'lon': 122.5, 'lat': 29.5, 'time': peak_time},
                           'native_count': 9, 'sea_count': 4, 'valid_count': 4*len(series), 'missing_count': 0,
                           'available_start': BEGIN.isoformat(), 'available_end': available_end.isoformat(),
                           'series_start': series[0]['time'] if series else None, 'series_end': peak_time,
                           'partial': requested < BEGIN or requested_end > available_end or not series,
                           'source_files': [{'path': f'E:\\private\\{key}.nc', 'sha256': key[0] * 64, 'variable': key}]}
        return {'fields': fields, 'batch': BEGIN.isoformat(), 'provisional': True}

    def image_png(self, **kwargs):
        if self.image_error:
            raise RuntimeError('private/path/should/not/be/in/report')
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), '#147c93').save(buf, format='PNG')
        return buf.getvalue()


def build(tmp_path, engine=None, **kwargs):
    report = ObjectiveReport(engine or Engine(), tmp_path / 'reports', now=lambda: BEGIN)
    return report, report.report(**kwargs)


def test_report_has_native_sea_extrema_regions_dates_and_downloads(tmp_path):
    engine = Engine()
    report, output = build(tmp_path, engine)
    result = output['objective_result']
    assert result['kind'] == 'objective_report' and result['state'] == 'ready'
    assert result['start'] == BEGIN.isoformat()
    assert result['end'] == (BEGIN+timedelta(hours=72)).isoformat()
    assert len(result['regions']) == 3
    assert engine.calls == [{'dataset_id': 'test-a', 'start': BEGIN.isoformat(), 'hours': 72,
                             'bounds': bounds, 'sea_mask': 'wave_valid'}
                            for bounds in [ZHEJIANG_BOUNDS, *(row[2] for row in REGIONS)]]
    assert result['fields']['wave']['maximum']['value'] == 9.12567
    assert '最低海平面气压：990.25 hPa' in output['summary']
    assert '不是正式预警' in output['summary']
    assert {'html', 'json', 'csv', 'wave_png', 'wind_png', 'pressure_png'} == set(output['artifacts'])
    for artifact in output['artifacts'].values():
        path = report.artifact_path(output['report_id'], artifact['filename'])
        assert artifact['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert artifact['url'].startswith('/api/objective/reports/' + output['report_id'] + '/')
    serialized = report.artifact_path(output['report_id'], 'report.json').read_text('utf8')
    assert 'private' not in serialized
    assert json.loads(serialized)['fields']['wave']['source_files'][0]['filename'] == 'wave.nc'


def test_csv_keeps_each_fields_native_step_and_full_numeric_precision(tmp_path):
    report, output = build(tmp_path)
    with report.artifact_path(output['report_id'], 'series.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    wave = [row for row in rows if row['region_id'] == 'all' and row['field'] == 'wave']
    wind = [row for row in rows if row['region_id'] == 'all' and row['field'] == 'wind']
    pressure = [row for row in rows if row['region_id'] == 'all' and row['field'] == 'pressure']
    assert len(wave) == 25 and len(wind) == len(pressure) == 73
    assert float(wave[0]['maximum']) == 9.12567
    assert wave[1]['valid_time'] == (BEGIN + timedelta(hours=3)).isoformat()
    assert wind[1]['valid_time'] == (BEGIN + timedelta(hours=1)).isoformat()
    assert set(row['mask'] for row in rows) == {'wave_valid'}


def test_explicit_dataset_and_date_not_replaced_by_default_or_today(tmp_path):
    engine = Engine(available_hours=90)
    start = BEGIN + timedelta(hours=3)
    _, output = build(tmp_path, engine, start=start.isoformat(), dataset_id='test-b')
    assert output['objective_result']['dataset_id'] == 'test-b'
    assert output['objective_result']['start'] == start.isoformat()
    assert all(call['dataset_id'] == 'test-b' and call['start'] == start.isoformat() for call in engine.calls)


def test_short_coverage_is_partial_without_changing_requested_end(tmp_path):
    _, output = build(tmp_path, Engine(available_hours=60))
    result = output['objective_result']
    assert result['state'] == 'partial'
    assert result['end'] == (BEGIN+timedelta(hours=72)).isoformat()
    assert result['fields']['wave']['series_end'] == (BEGIN+timedelta(hours=60)).isoformat()
    assert result['fields']['wave']['available_end'] == (BEGIN+timedelta(hours=60)).isoformat()
    assert result['missing_information']
    assert '未完整覆盖' in output['summary']


def test_outside_entire_file_has_no_fabricated_values_or_images(tmp_path):
    _, output = build(tmp_path, start=(BEGIN+timedelta(days=30)).isoformat())
    assert output['state'] == 'no_data'
    assert all(not value['series'] for value in output['objective_result']['fields'].values())
    assert not any(name.endswith('_png') for name in output['artifacts'])
    assert '没有可用数值' in output['summary']


def test_future_issue_batch_cannot_fill_a_historical_start(tmp_path):
    engine = Engine()
    requested = BEGIN - timedelta(hours=12)
    _, output = build(tmp_path, engine, start=requested.isoformat())
    assert output['state'] == 'no_data'
    assert output['objective_result']['start'] == requested.isoformat()
    assert not engine.calls
    assert '未使用未来起报资料回填' in output['summary']
    assert not any(key.endswith('_png') for key in output['artifacts'])


@pytest.mark.parametrize('kwargs', [{'start': '2026-02-30'}, {'start': ''}, {'dataset_id': 'not-existing'},
                                    {'hours': 0}, {'hours': 169}, {'hours': True}, {'hours': 72.0}])
def test_bad_inputs_are_not_silently_replaced(tmp_path, kwargs):
    report = ObjectiveReport(Engine(), tmp_path, now=lambda: BEGIN)
    with pytest.raises(ValueError):
        report.report(**kwargs)
    assert not list(tmp_path.iterdir())


def test_mid_hour_default_is_preserved_without_temporal_interpolation(tmp_path):
    start = BEGIN + timedelta(minutes=31)
    report = ObjectiveReport(Engine(available_hours=75), tmp_path, now=lambda: start)
    output = report.report()
    assert output['objective_result']['start'] == start.isoformat()
    assert output['objective_result']['fields']['wave']['series_start'] == (BEGIN+timedelta(hours=3)).isoformat()
    assert output['state'] == 'ready'


def test_shared_product_omits_user_input_and_links_local_images(tmp_path):
    report, output = build(tmp_path, question='<script>alert("x")</script>')
    text = report.artifact_path(output['report_id'], 'report.html').read_text('utf8')
    assert '<script>' not in text and '&lt;script&gt;' not in text
    assert 'alert' not in text
    assert 'src="wave_peak.png"' in text and 'href="series.csv"' in text
    assert '最低海平面气压' in text and 'P95' in text
    assert '不能从台风中心强度推导当地严重影响' in text
    assert 'fixed_station' not in text


def test_image_error_keeps_real_data_and_marks_report_partial(tmp_path):
    report, output = build(tmp_path, Engine(image_error=True))
    assert output['state'] == 'partial'
    assert output['objective_result']['fields']['wave']['maximum']['value'] == 9.12567
    assert len(output['objective_result']['artifact_errors']) == 3
    assert 'private/path' not in json.dumps(output, ensure_ascii=False)
    assert report.artifact_path(output['report_id'], 'series.csv').is_file()


@pytest.mark.parametrize('report_id,filename', [('../escape', 'report.json'), ('a'*32, '../private.env'),
                                             ('a'*32, 'report.exe'), ('A'*32, 'report.html')])
def test_artifact_paths_do_not_escape_root(tmp_path, report_id, filename):
    report = ObjectiveReport(Engine(), tmp_path)
    with pytest.raises(ValueError):
        report.artifact_path(report_id, filename)


def test_missing_sea_samples_not_treated_as_zero_or_complete(tmp_path):
    class MissingEngine(Engine):
        def stats72(self, *args, **kwargs):
            result = super().stats72(*args, **kwargs)
            result['fields']['wind']['missing_count'] = 5
            return result
    _, output = build(tmp_path, MissingEngine())
    assert output['state'] == 'partial'
    assert '5个海上格点-时次样本缺测' in output['summary']
    assert output['objective_result']['fields']['wind']['maximum']['value'] == 33.75


def test_native_curve_breaks_at_missing_frame_instead_of_interpolating_it():
    field = {'unit': 'm', 'series': [
        {'time': BEGIN.isoformat(), 'maximum': {'value': 1.25}},
        {'time': (BEGIN+timedelta(hours=3)).isoformat(), 'maximum': None},
        {'time': (BEGIN+timedelta(hours=6)).isoformat(), 'maximum': {'value': 2.75}},
    ]}
    svg = _plot_svg(field, FIELDS['wave'])
    assert svg.count('<polyline ') == 2
    assert svg.count('<circle ') == 2
    assert '10-20 08:00' in svg and '10-20 14:00' in svg


def test_report_returns_machine_readable_interpretation_profile(tmp_path):
    class ProvisionalEngine(Engine):
        def catalogue(self):
            result = super().catalogue()
            for dataset in result['datasets']:
                dataset['provisional'] = True
                dataset['profile'] = {'id': 'test-profile', 'time': 'UTC interpretation'}
            return result
    _, output = build(tmp_path, ProvisionalEngine())
    result = output['objective_result']
    assert result['profile']['id'] == 'test-profile'
    assert '数值来自已登记NC文件' in result['summary']
    assert any('UTC' in line for line in result['limitations'])


def test_evidence_contains_regional_statistics_and_native_time_values(tmp_path):
    _, output = build(tmp_path)
    text = output['evidence']
    assert all(label in text for _, label, _ in REGIONS)
    assert text.count('统计框W,S,E,N=') == 9
    assert '空间最大有效波高 (m)' in text
    assert '9.12567' in text and '990.25' in text
    assert '完整各原生时次在CSV中' in text and '未对缺少该时次的要素插值' in text
    assert 'SHA256' in text and 'private' not in text


def test_summary_and_html_describe_computed_regional_order_and_time_stage(tmp_path):
    class RegionalEngine(Engine):
        def stats72(self, *args, **kwargs):
            result = super().stats72(*args, **kwargs)
            bounds = tuple(kwargs['bounds'])
            for index, (_, _, zone) in enumerate(REGIONS):
                if bounds == zone:
                    result['fields']['wave']['maximum']['value'] = 5 + index
                    result['fields']['wind']['maximum']['value'] = 20 + index*5
            return result
    report, output = build(tmp_path, RegionalEngine())
    summary = output['summary']
    assert '南部参考统计框的最大有效波高较高（7 m）' in summary
    assert '北部参考统计框较低（5 m）' in summary
    assert '极值时段' in summary and '不代表当地灾害等级' in summary
    text = report.artifact_path(output['report_id'], 'report.html').read_text('utf8')
    assert '<h2>变化重点</h2>' in text
    assert '南部参考统计框的最大有效波高较高' in text


class L2L5Engine(Engine):
    """Deliberately return a stray pressure result to prove catalogue isolation."""
    def __init__(self, *, level='L5', **kwargs):
        super().__init__(**kwargs)
        self.level = level
        self.images = []

    def catalogue(self):
        result = super().catalogue()
        for dataset in result['datasets']:
            dataset.update(source_family='zjocean_l2_l5', level=self.level, provisional=True,
                           profile={'id': 'zjocean-provisional-v1', 'wind': 'temporary gust interpretation'})
            dataset['fields'].pop('pressure')
        return result

    def image_png(self, **kwargs):
        self.images.append(kwargs)
        return super().image_png(**kwargs)


def test_l5_wind_wave_report_ready_without_unrequested_pressure(tmp_path):
    engine = L2L5Engine()
    report, output = build(tmp_path, engine)
    result = output['objective_result']
    assert output['state'] == 'ready'
    assert result['available_fields'] == result['requested_fields'] == ['wave', 'wind']
    assert result['unavailable_fields'] == ['pressure']
    assert result['fields']['pressure']['state'] == 'not_provided'
    assert not result['fields']['pressure']['series']
    assert not result['fields']['pressure']['source_files']
    assert result['source_family'] == 'zjocean_l2_l5' and result['level'] == 'L5'
    assert all(call['field'] != 'pressure' for call in engine.images)
    assert 'pressure_png' not in output['artifacts']
    assert not result['missing_information']
    assert '最大海面风速（暂按阵风解释）：33.75 m/s' in output['summary']
    assert '最大海上10米风速' not in output['summary']
    assert '所选数据集未提供' in output['summary']
    assert 'UTC' not in output['summary']
    html_text = report.artifact_path(output['report_id'], 'report.html').read_text('utf8')
    assert '暂按阵风解释' in html_text and 'pressure_peak.png' not in html_text
    assert '海平面气压：原生时次空间极值' not in html_text
    assert '990.25' not in output['evidence']
    assert '空间最大海面风速（暂按阵风解释） (m/s)' in output['evidence']
    with report.artifact_path(output['report_id'], 'series.csv').open(encoding='utf-8-sig', newline='') as handle:
        assert {row['field'] for row in csv.DictReader(handle)} == {'wave', 'wind'}


def test_l2_l5_explicit_three_element_request_reports_pressure_only_gap(tmp_path):
    _, output = build(tmp_path, L2L5Engine(), requested_fields=['wave', 'wind', 'pressure'])
    result = output['objective_result']
    assert output['state'] == 'partial'
    assert result['fields']['wave']['maximum']['value'] == 9.12567
    assert result['missing_information'] == ['海平面气压：所选数据集未提供该要素，未使用其他批次或产品补齐。']
    assert len([name for name in output['artifacts'] if name.endswith('_png')]) == 2


def test_l2_l5_pressure_only_is_no_data_instead_of_borrowed_ecmwf(tmp_path):
    _, output = build(tmp_path, L2L5Engine(), requested_fields=['pressure'])
    assert output['state'] == 'no_data'
    assert output['objective_result']['fields']['pressure']['state'] == 'not_provided'
    assert '990.25' not in output['evidence']


def test_l2_partial_spatial_coverage_is_preserved_and_marks_requested_fields_partial(tmp_path):
    class PartialL2Engine(L2L5Engine):
        def stats72(self, *args, **kwargs):
            result = super().stats72(*args, **kwargs)
            for name in ('wave', 'wind'):
                result['fields'][name].update(spatial_partial=True, coverage_bounds=[120, 27, 125, 31],
                                              requested_bounds=list(kwargs['bounds']))
            return result
    _, output = build(tmp_path, PartialL2Engine(level='L2'))
    result = output['objective_result']
    assert output['state'] == 'partial'
    assert result['fields']['wave']['coverage_bounds'] == [120, 27, 125, 31]
    assert result['fields']['wind']['requested_bounds'] == list(ZHEJIANG_BOUNDS)
    assert '源网格未完整覆盖请求统计框' in output['summary']
    assert not any('海平面气压' in gap for gap in result['missing_information'])


def test_l2_l5_missing_wind_samples_still_block_complete_delivery(tmp_path):
    class MissingWindEngine(L2L5Engine):
        def stats72(self, *args, **kwargs):
            result = super().stats72(*args, **kwargs)
            result['fields']['wind']['missing_count'] = 6
            return result
    _, output = build(tmp_path, MissingWindEngine(), requested_fields=['wave', 'wind'])
    assert output['state'] == 'partial'
    assert '6个海上格点-时次样本缺测' in output['summary']


@pytest.mark.parametrize('fields', [[], 'wave', ['temperature'], [None], [{}]])
def test_invalid_requested_fields_are_rejected_before_reading_stats(tmp_path, fields):
    engine = L2L5Engine()
    with pytest.raises(ValueError, match='requested_fields'):
        build(tmp_path, engine, requested_fields=fields)
    assert not engine.calls
