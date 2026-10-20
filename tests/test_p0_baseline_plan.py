"""The paid baseline runner defaults to a no-model, no-server dry run."""
import json
import sys

import pytest

from tools.record_p0_baseline import case_plan, main


def twelve(tmp_path):
    ids = ['M1','M2','M3','C1','C2','C3','A1','A2','A3','G1','G2','G3']
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps({'items':[{'id':i,'question':'模拟问题'+i} for i in ids]}),encoding='utf-8')
    return path


def test_scope_24_means_twelve_plus_twelve_additional_repeats(tmp_path):
    rows = case_plan(twelve(tmp_path))
    assert len(rows) == 24
    assert len({r['case_id'] for r in rows}) == 24
    assert sum(r['phase']=='baseline' for r in rows) == 12
    assert sum(r['phase']=='additional_repeat' for r in rows) == 12
    assert len(case_plan(twelve(tmp_path),'12')) == 12


def test_dry_run_does_not_import_server_or_invoke_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules,'server',None)
    assert main(['--cases',str(twelve(tmp_path))]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['mode']=='dry_run' and output['qa_transactions']==24


def test_run_requires_explicit_private_recording_before_server_import(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules,'server',None)
    with pytest.raises(SystemExit) as error:
        main(['--cases',str(twelve(tmp_path)),'--run'])
    assert error.value.code == 2


def test_duplicate_or_incomplete_cases_rejected(tmp_path):
    path=twelve(tmp_path)
    data=json.loads(path.read_text(encoding='utf-8'))
    data['items'][0]['id']='G3'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='twelve_unique'):
        case_plan(path)
