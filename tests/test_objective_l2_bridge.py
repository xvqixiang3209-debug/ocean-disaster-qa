"""L2/L5 map/report delivery contract, entirely offline with no model or HTTP."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from forecast_runtime.objective_bridge import answer_objective, objective_question, query_objective
from forecast_runtime.storage import BEIJING
from marine_kb.objective_context import product_question_plan


NOW = datetime(2026, 10, 20, 21, 43, tzinfo=BEIJING)
FIRST = datetime(2026, 10, 21, 8, tzinfo=BEIJING)
VALID = FIRST.isoformat()
L5 = 'zjocean_L5_202610202000_fixture'
ECMWF = 'ecmwf_202610192000_fixture'
BOUNDS = [118, 26, 126, 33]
from forecast_runtime.objective_scope import resolve_scope
ZHEJIANG_SCOPE = resolve_scope('浙江')


def dataset(identifier=L5):
    fields = ['wave', 'wind'] if identifier == L5 else ['wave', 'wind', 'pressure']
    return {'dataset_id': identifier, 'source_family': 'zjocean_l2_l5' if identifier == L5 else 'ecmwf',
            'level': 'L5' if identifier == L5 else None, 'label': '业务L5风浪',
            'batch': '2026-10-20T20:00:00+08:00',
            'fields': {name: {'valid_times': [(FIRST+timedelta(hours=h)).isoformat() for h in (0, 1, 6, 72)],
                              'coverage': BOUNDS, 'available_start': VALID,
                              'available_end': (FIRST+timedelta(hours=72)).isoformat()}
                       for name in fields}}


class Engine:
    def __init__(self):
        self.calls = []

    def catalogue(self):
        # ECMWF deliberately precedes L5: default selection must use coordinator.
        return {'datasets': [dataset(ECMWF), dataset()]}

    def frame(self, **kwargs):
        self.calls.append(kwargs)
        chosen = next(d for d in self.catalogue()['datasets'] if d['dataset_id'] == kwargs['dataset_id'])
        field, valid = kwargs['field'], kwargs['valid_time']
        if field not in chosen['fields']:
            return {'state': 'not_provided', 'field': field, 'valid_time': valid, 'stats': {}}
        if valid not in chosen['fields'][field]['valid_times']:
            raise ValueError('原生时次不存在')
        value, unit = {'wave': (6.125, 'm'), 'wind': (18.75, 'm/s'), 'pressure': (1001.5, 'hPa')}[field]
        return {'state': 'ready', 'dataset_id': kwargs['dataset_id'], 'field': field,
                'valid_time': valid, 'batch': chosen['batch'], 'unit': unit,
                'stats': {'maximum': {'value': value, 'lon': 122.5, 'lat': 28.25, 'time': valid},
                          'minimum': {'value': value, 'lon': 122.5, 'lat': 28.25, 'time': valid},
                          'valid_count': 125}, 'values': [[value]], 'u': [[1]], 'v': [[2]],
                'source_files': [{'filename': 'ZJOCEAN_WAV_L5.nc', 'sha256': 'a'*64}]}


class Sources:
    def __init__(self):
        self.ensure_calls, self.select_calls = [], []

    def ensure(self, *args, **kwargs):
        self.ensure_calls.append((args, kwargs))
        return {'state': 'ready', 'local_complete': True}

    def select(self, *args, **kwargs):
        self.select_calls.append((args, kwargs))
        return dataset()


class Reports:
    def __init__(self):
        self.calls = []

    def report(self, **kwargs):
        self.calls.append(kwargs)
        selected = kwargs['dataset_id']
        if selected not in {L5, ECMWF}:
            raise ValueError('数据集未登记')
        peak_time = (FIRST+timedelta(hours=6)).isoformat()
        begin = datetime.fromisoformat(kwargs['start'])
        result = {'dataset_id': selected, 'start': begin.isoformat(),
                  'end': (begin+timedelta(hours=kwargs['hours'])).isoformat(), 'hours': kwargs['hours'],
                  'fields': {'wave': {'maximum': {'value': 6.125, 'lon': 122.5, 'lat': 28.25,
                                                 'time': peak_time}, 'unit': 'm'},
                             'wind': {'maximum': {'value': 18.75, 'lon': 122.5, 'lat': 28.25,
                                                 'time': peak_time}, 'unit': 'm/s'}}}
        return {'state': 'ready', 'objective_result': result, 'summary': '真实程序结果：波高6.125 m。',
                'evidence': 'NC原生结果，峰值6.125 m。', 'artifacts': []}


@pytest.fixture
def runtime():
    records = {}
    return SimpleNamespace(objective=Engine(), objective_reports=Reports(), objective_sources=Sources(),
                           _local=SimpleNamespace(request_now=NOW), config_id=lambda: 'offline-test',
                           records=records, store=SimpleNamespace(state=lambda k, v: records.update({k: v})))


def qa():
    def forbidden(*args, **kwargs):
        pytest.fail('offline map delivery must not invoke a model or web search')
    # Field products must remain a data route even if a model is configured.
    return SimpleNamespace(external_enabled=True, model_caller=forbidden, model_name='unused',
                           crag_enabled=True, joint_evidence=forbidden, _organized_answer=forbidden,
                           _public_sources=lambda q, hits: hits)


@pytest.mark.parametrize('question', [
    '显示2026年10月21日08时浙江近海海面风分布图',
    '绘制浙江近海2026年10月21日08时波高分布图',
    '显示浙江近海浪高图',
    '显示舟山海上风分布图',
])
def test_common_field_words_enter_objective_route(question):
    assert objective_question(question)


@pytest.mark.parametrize('question', [
    '浙江明天08时海浪有多高、海面风有多大？',
    '2026年10月21日08时浙江近海波高是多少，海面风速多大？',
    '浙江近海未来24小时浪高和海面风最大分别多少？',
])
def test_dated_numeric_wind_wave_questions_enter_data_route_without_word_picture(question):
    assert objective_question(question)


@pytest.mark.parametrize('question', [
    '什么是海面风和有效波高？',
    '浙江海浪预警是什么意思？',
    '海面风速和阵风有什么区别？',
])
def test_general_concepts_are_not_routed_to_nc_data(question):
    assert not objective_question(question)


def test_default_selection_uses_coordinator_l5_and_exact_requested_time(runtime):
    result = query_objective(runtime, '显示2026年10月21日08时浙江近海海浪分布图')
    assert result['kind'] == 'objective_field' and result['dataset_id'] == L5
    assert result['valid_time'] == VALID
    assert runtime.objective_sources.ensure_calls
    args, kwargs = runtime.objective_sources.select_calls[0]
    assert args == (FIRST, FIRST)
    assert kwargs['fields'] == ['wave'] and kwargs['as_of'] == NOW
    assert runtime.objective.calls[0]['valid_time'] == VALID
    assert runtime.objective.calls[0]['dataset_id'] == L5
    assert not runtime.objective_reports.calls


@pytest.mark.parametrize('clock', ['08时30分', '08:30'])
def test_explicit_non_native_clock_never_uses_nearest_frame(runtime, clock):
    result = query_objective(runtime, f'显示2026年10月21日{clock}浙江近海海浪分布图')
    expected = FIRST.replace(minute=30).isoformat()
    assert result['state'] == 'no_data'
    assert [call['valid_time'] for call in runtime.objective.calls] == [expected]
    assert result['map_action']['valid_time'] == expected
    assert result['map_action']['field_state'] == 'no_data'
    assert '未插值、未替换时刻' in result['summary']


def test_pressure_missing_stays_with_l5_and_produces_clear_map_action(runtime):
    result = query_objective(runtime, '显示2026年10月21日08时浙江近海气压分布图')
    assert result['state'] == 'no_data'
    assert {c['dataset_id'] for c in runtime.objective.calls} == {L5}
    assert result['map_action']['field_state'] == 'not_provided'
    assert result['map_action']['dataset_id'] == L5
    assert result['map_action']['available_fields'] == ['wave', 'wind']
    assert '未借用其他批次或ECMWF数据' in result['summary']
    assert '1001.5' not in result['summary']


def test_real_frame_stats_source_and_map_action_survive_answer_delivery(runtime):
    result = answer_objective(runtime, '显示2026年10月21日08时浙江近海海浪分布图', qa(), {}, True)
    for detail in ('6.125 m', '122.5', '28.25', VALID, '125个'):
        assert detail in result['answer']
    meta = result['answer_meta']
    assert meta['used_mode'] == 'business_data' and meta['generation_used'] is False
    action = meta['map_action']
    assert action['dataset_id'] == L5 and action['field'] == 'wave'
    assert action['valid_time'] == VALID and action['bounds'] == ZHEJIANG_SCOPE['bounds']
    stored = next(iter(runtime.records.values()))
    assert stored['frames'][0]['stats']['maximum']['value'] == 6.125
    assert 'values' not in stored['frames'][0], 'dense grids do not belong in chat evidence'
    assert 'ZJOCEAN_WAV_L5.nc' in result['rag_sources'][0]['text']
    assert 'a'*64 in result['rag_sources'][0]['text']


def test_field_answer_returns_server_owned_context_for_next_map_question(runtime):
    result = answer_objective(runtime, '显示2026年10月21日08时浙江近海海浪分布图', qa(), {}, False)
    context = result['answer_meta']['objective_context']
    assert context['fields']==['wave']
    assert context['objective_scope_resolution']['scope']['id']=='zhejiang'
    assert {k:v for k,v in context.items() if k not in {'fields','objective_scope_resolution'}} == {'dataset_id': L5, 'start': VALID,
                       'end': (FIRST+timedelta(hours=1)).isoformat(), 'hours': 1,
                       'view_kind': 'field', 'field': 'wave', 'valid_time': VALID,
                       'bounds': ZHEJIANG_SCOPE['bounds'], 'scope': ZHEJIANG_SCOPE}
    plan = product_question_plan('同一时刻换成海面风', [{'objective_context': context}], now=NOW)
    assert plan['context_used'] and plan['objective_field'] == 'wind'
    assert L5 in plan['normalized_question']
    followup = answer_objective(runtime, plan['normalized_question'], qa(),
                                {**plan, 'current_turn_question': '同一时刻换成海面风'}, False)
    assert '18.75 m/s' in followup['answer']
    assert followup['answer_meta']['map_action']['field'] == 'wind'
    assert followup['answer_meta']['map_action']['valid_time'] == VALID
    assert followup['answer_meta']['map_action']['dataset_id'] == L5


def test_followup_clock_correction_changes_native_time_without_changing_dataset(runtime):
    original = answer_objective(runtime, '显示2026年10月21日08时浙江近海海浪分布图', qa(), {}, False)
    plan = product_question_plan('改成同一天09时的海面风',
                                [{'objective_context': original['answer_meta']['objective_context']}], now=NOW)
    result = answer_objective(runtime, plan['normalized_question'], qa(),
                              {**plan, 'current_turn_question': '改成同一天09时的海面风'}, False)
    assert result['answer_meta']['map_action']['valid_time'] == FIRST.replace(hour=9).isoformat()
    assert result['answer_meta']['map_action']['dataset_id'] == L5


def test_explicit_dataset_does_not_trigger_acquisition_or_default_selection(runtime):
    result = query_objective(runtime, f'显示2026年10月21日08时浙江近海海浪分布图，数据集ID {L5}')
    assert result['dataset_id'] == L5
    assert not runtime.objective_sources.ensure_calls
    assert not runtime.objective_sources.select_calls


def test_explicit_ecmwf_pressure_is_allowed_only_when_user_selects_that_dataset(runtime):
    result = query_objective(runtime, f'显示2026年10月21日08时浙江近海气压分布图，数据集ID {ECMWF}')
    assert result['dataset_id'] == ECMWF and result['state'] == 'ready'
    assert '1001.5 hPa' in result['summary']
    assert {c['dataset_id'] for c in runtime.objective.calls} == {ECMWF}
    assert not runtime.objective_sources.ensure_calls
    action = result['map_action']
    assert 'level' not in action or isinstance(action['level'], str), 'non-applicable level must be omitted, not null, for the strict UI contract'


def test_unknown_explicit_dataset_does_not_fall_back_to_ecmwf(runtime):
    result = answer_objective(runtime, '显示2026年10月21日08时浙江近海气压分布图，数据集ID unknown_20261021',
                              qa(), {}, False)
    assert result['answer_meta']['business_state'] == 'no_data'
    assert not runtime.objective.calls
    assert not runtime.objective_sources.ensure_calls
    assert result['answer_meta']['map_action']['type']=='objective_clear'


def test_report_map_action_uses_actual_peak_time_instead_of_report_start(runtime):
    result = query_objective(runtime, '从2026年10月21日08时起生成浙江未来72小时海浪报告')
    assert result['state'] == 'ready'
    assert runtime.objective_reports.calls[0]['dataset_id'] == L5
    assert result['map_action']['field'] == 'wave'
    assert result['map_action']['valid_time'] == (FIRST+timedelta(hours=6)).isoformat()
    assert result['map_action']['valid_time'] != result['start']
    assert result['map_action']['source_family'] == 'zjocean_l2_l5'


def test_same_field_multiple_requests_do_not_leak_previous_numeric_evidence(runtime):
    first = answer_objective(runtime, '显示2026年10月21日08时浙江近海海浪分布图', qa(), {}, False)
    second = answer_objective(runtime, '显示2026年10月21日08时浙江近海气压分布图', qa(), {}, False)
    assert first['answer_meta']['business_evidence_id'] != second['answer_meta']['business_evidence_id']
    assert '6.125' not in second['answer']
    assert second['answer_meta']['map_action']['field'] == 'pressure'
    assert second['answer_meta']['map_action']['field_state'] == 'not_provided'

