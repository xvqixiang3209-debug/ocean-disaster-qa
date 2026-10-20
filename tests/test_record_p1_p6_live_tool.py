"""Runner safety and recording tests use fake HTTP/model dependencies only."""
import builtins
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

from tools.record_p1_p6_live import (ENVIRONMENT, EXPECTED_IDS, capture_sources,
    compare_sources, load_case_plan, main, record_run, runtime_files, source_hashes)


def plan_file(tmp_path):
    cases = [{'case_id': key, 'question': '原始问题  ' + key.split('_extra_')[0] + '？',
              'phase': 'baseline' if '_extra_' not in key else 'additional_repeat',
              'repeat': int(key[-1]) if '_extra_' in key else 1} for key in EXPECTED_IDS]
    path = tmp_path / 'old_plan.json'
    path.write_text(json.dumps({'cases': cases}, ensure_ascii=False), 'utf8')
    return path, cases


def fake_source(root):
    for relative, value in [('marine_kb/phase2.py', '# fake runtime flag source\n'),
                            ('data/domain/relation_lexicon.json', '{"relations": []}'),
                            ('tools/record_p1_p6_live.py', '# fake evaluation runner\n'),
                            ('tests/test_fake.py', '# offline test source\n')]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, 'utf8')
    (root / '.env').write_text('SECRET=never-read', 'utf8')


def fake_server(seen, root, mutate=False):
    def factory():
        import os
        assert all(os.environ.get(name) == value for name, value in ENVIRONMENT.items())
        app = Flask('offline-recording-tool-test')
        def model(*args, **kwargs):
            return 'offline mock model response'
        qa = SimpleNamespace(external_enabled=True, model_configured=True, model_caller=model,
                             web_supplement=SimpleNamespace(enabled=True, fetch=lambda q, **kw: ([], {'mock': True})),
                             _collect_local_hits=lambda *args: [])
        @app.get('/api/assistant/session')
        def session():
            return jsonify(token='mock-token')
        @app.post('/api/assistant/ask')
        def ask():
            question = request.json['question']
            seen.append(question)
            qa.web_supplement.fetch(question)
            qa._collect_local_hits(question, question, [], [], {}, False, None)
            answer = qa.model_caller('mock-model', 'mock-system', question, stream=False)
            if mutate and len(seen) == 1:
                (root / 'data/domain/relation_lexicon.json').write_text('{"changed":true}', 'utf8')
            return jsonify(answer=answer, mocked=True)
        return app, qa
    return factory


def test_exact_old_question_bytes_and_ids_are_preserved(tmp_path):
    path, expected = plan_file(tmp_path)
    loaded, sha = load_case_plan(path)
    assert loaded == expected and loaded[0]['question'] == '原始问题  M1？'
    assert len(sha) == 64
    data = json.loads(path.read_text('utf8'))
    data['cases'][12]['question'] += '改写'
    path.write_text(json.dumps(data, ensure_ascii=False), 'utf8')
    with pytest.raises(ValueError, match='repeated_question_changed'):
        load_case_plan(path)


def test_dryrun_never_imports_server_or_changes_environment(tmp_path, monkeypatch, capsys):
    path, _ = plan_file(tmp_path)
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == 'server':
            raise AssertionError('dryrun_must_not_import_application')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setenv('MARINE_TYPED_GROUNDING', 'shadow')
    assert main(['--p0-run-plan', str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['mode'] == 'dry_run' and out['paid_model_calls'] == 0
    assert not out['private_output_created']
    import os
    assert os.environ['MARINE_TYPED_GROUNDING'] == 'shadow'


@pytest.mark.parametrize('options', [[], ['--record-private'], ['--authorized-qa=24'],
                                    ['--record-private', '--authorized-qa=23']])
def test_live_main_rejects_missing_or_wrong_authorization(tmp_path, options):
    path, _ = plan_file(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(['--p0-run-plan', str(path), '--run'] + options)
    assert caught.value.code == 2


def test_snapshot_captures_runtime_and_lexicon_bytes_and_never_env(tmp_path):
    root, out = tmp_path / 'app', tmp_path / 'record'
    root.mkdir(); out.mkdir(); fake_source(root)
    names = {path.relative_to(root).as_posix() for path in runtime_files(root)}
    assert 'marine_kb/phase2.py' in names and 'data/domain/relation_lexicon.json' in names
    assert 'tools/record_p1_p6_live.py' in names and '.env' not in names
    before = capture_sources(out, 'before', root)
    assert (out / 'source_snapshot/data/domain/relation_lexicon.json').read_bytes() == (root / 'data/domain/relation_lexicon.json').read_bytes()
    assert compare_sources(before, source_hashes(root))['stable']
    (root / 'marine_kb/phase2.py').write_text('# changed', 'utf8')
    after = capture_sources(out, 'after', root)
    assert compare_sources(before, after)['changed'] == ['marine_kb/phase2.py']


def test_fake_24_http_transactions_one_attempt_each_with_complete_cassettes(tmp_path, monkeypatch):
    path, cases = plan_file(tmp_path)
    root = tmp_path / 'app'; root.mkdir(); fake_source(root)
    seen = []
    monkeypatch.setenv('MARINE_TYPED_GROUNDING', 'off')
    destination = record_run(cases, path, 'mockhash', root=root, server_factory=fake_server(seen, root))
    assert seen == [row['question'] for row in cases]
    assert len(list(destination.glob('*.result.json'))) == 24
    assert len(list(destination.glob('*.started.json'))) == 24
    assert len(list(destination.glob('*.cassette.jsonl'))) == 24
    complete = json.loads((destination / 'run_completed.json').read_text('utf8'))
    assert complete['qa_transactions_attempted'] == 24 and complete['automatic_transaction_retries'] == 0
    assert complete['source_stability']['stable']
    for case in cases:
        cassette = destination / (case['case_id'] + '.cassette.jsonl')
        journal = [json.loads(line) for line in cassette.read_text('utf8').splitlines()]
        assert journal[-1]['record_type'] == 'complete'
        result = json.loads((destination / (case['case_id'] + '.result.json')).read_text('utf8'))
        assert result['case'] == case and result['http_status'] == 200 and result['reply']['mocked']
        assert result['elapsed_seconds'] >= 0
    import os
    assert os.environ['MARINE_TYPED_GROUNDING'] == 'off'


def test_source_change_aborts_before_second_transaction_without_retry(tmp_path):
    path, cases = plan_file(tmp_path)
    root = tmp_path / 'app'; root.mkdir(); fake_source(root)
    seen = []
    with pytest.raises(RuntimeError, match='source_changed'):
        record_run(cases, path, 'mockhash', root=root, server_factory=fake_server(seen, root, mutate=True))
    assert seen == [cases[0]['question']]
    destination = next((root / 'tmp/replay').iterdir())
    assert json.loads((destination / 'run_completed.json').read_text('utf8'))['qa_transactions_attempted'] == 1
    assert not json.loads((destination / 'source_stability.json').read_text('utf8'))['stable']
    assert len(list(destination.glob('*.result.json'))) == 1
