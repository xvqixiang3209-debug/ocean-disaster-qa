"""All recordings here use invented mocks; no business API or web is called."""
import hashlib
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from marine_kb.replay import (
    CassetteError, CassetteIncomplete, CassetteMismatch, CassettePrivacyError,
    ModelCassette, ReaderCassette, RecordedCallError, RecordingCaller, ReplayCaller,
    SearchCassette,
)


def record(path, **kwargs):
    return ModelCassette(path, mode="record", allow_sensitive_recording=True,
                         private_root=path.parent, **kwargs)


def ask(caller, prompt="模拟问题", **kwargs):
    return caller("mock-model", "仅模拟系统提示", prompt, stream=False,
                  temperature=0.1, max_tokens=4096, **kwargs)


def test_mock_model_roundtrip_keeps_bytes_and_usage_tuple(tmp_path):
    path = tmp_path / "mock.jsonl"
    answer = '{"answer":"不是一回事。\\n分别办理。[资料1]"}  \r\n'
    live = Mock(return_value=(answer, {"prompt_tokens": 39, "completion_tokens": 12}))
    with record(path) as cassette:
        expected = ask(RecordingCaller(live, cassette), return_usage=True)
    with ModelCassette(path) as cassette:
        actual = ask(ReplayCaller(cassette), return_usage=True)
    assert expected == actual
    assert isinstance(actual, tuple)
    assert actual[0].encode("utf-8") == answer.encode("utf-8")
    assert hashlib.sha256(actual[0].encode()).digest() == hashlib.sha256(answer.encode()).digest()
    assert live.call_count == 1


@pytest.mark.parametrize("change", ["prompt", "system", "model", "temperature", "option", "usage"])
def test_full_request_fingerprint_rejects_changes(tmp_path, change):
    path = tmp_path / "mock.jsonl"
    args, kwargs = ["mock-model", "system", "question"], {"stream": False, "temperature": .1}
    with record(path) as cassette:
        RecordingCaller(lambda *a, **k: "original", cassette)(*args, **kwargs)
    if change == "prompt": args[2] += "changed"
    if change == "system": args[1] += "changed"
    if change == "model": args[0] += "changed"
    if change == "temperature": kwargs["temperature"] = .2
    if change == "option": kwargs["response_format"] = {"type": "json_object"}
    if change == "usage": kwargs["return_usage"] = True
    cassette = ModelCassette(path)
    with pytest.raises(CassetteMismatch, match="request_or_order_mismatch"):
        ReplayCaller(cassette)(*args, **kwargs)
    assert cassette.summary()["consumed"] == 0
    cassette.close(complete=False)


def test_ordered_replay_rejects_reordering_without_consuming(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        caller = RecordingCaller(lambda model, system, prompt, **k: prompt, cassette)
        ask(caller, "A")
        ask(caller, "B")
    with ModelCassette(path) as cassette:
        caller = ReplayCaller(cassette)
        with pytest.raises(CassetteMismatch): ask(caller, "B")
        assert ask(caller, "A") == "A"
        assert ask(caller, "B") == "B"


def test_per_key_replay_allows_reordering_but_keeps_duplicate_order(tmp_path):
    path = tmp_path / "mock.jsonl"
    live = Mock(side_effect=["A1", "B1", "A2"])
    with record(path) as cassette:
        caller = RecordingCaller(live, cassette)
        assert [ask(caller, p) for p in ["A", "B", "A"]] == ["A1", "B1", "A2"]
    with ModelCassette(path, ordering="per_key") as cassette:
        caller = ReplayCaller(cassette)
        assert [ask(caller, p) for p in ["B", "A", "A"]] == ["B1", "A1", "A2"]
        with pytest.raises(CassetteMismatch): ask(caller, "A")


def test_unconsumed_recording_is_not_silent_success(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        ask(RecordingCaller(lambda *a, **k: "A", cassette))
    cassette = ModelCassette(path)
    with pytest.raises(CassetteMismatch, match="unconsumed_interactions"):
        cassette.assert_consumed()
    with pytest.raises(CassetteMismatch, match="unconsumed_interactions"):
        cassette.close()
    cassette.close(complete=False)


def test_reader_preserves_failed_status_snapshot_and_rejects_request_mutation(tmp_path):
    path = tmp_path / "mock.jsonl"
    rows = [{"url": "https://mock.gov.cn/article", "_primary_scope": "technical_reference"}]
    pages = [{**rows[0], "content": "模拟正文", "read_status": "partial",
              "read_at": "2026-10-20T00:00:00+00:00", "http_status": 200,
              "final_url": "https://mock.gov.cn/article", "document_links": []}]
    live = Mock()
    live.enrich.return_value = pages
    with record(path) as cassette:
        expected = ReaderCassette(live, cassette).enrich(rows)
    with ModelCassette(path) as cassette:
        reader = ReaderCassette(None, cassette)
        with pytest.raises(CassetteMismatch): reader.enrich([{**rows[0], "url": "https://other.gov.cn"}])
        assert reader.enrich(rows) == expected
    assert live.enrich.call_count == 1


def test_search_outcome_roundtrip_preserves_metadata(tmp_path):
    from typhoon.web_search import SearchOutcome
    path = tmp_path / "mock.jsonl"
    outcome = SearchOutcome(results=[{"title": "模拟", "url": "https://mock.gov.cn"}],
                            state="success", provider="mock",
                            attempts=[{"state": "success"}])
    live = Mock()
    live.search_with_status.return_value = outcome
    with record(path) as cassette:
        expected = SearchCassette(live, cassette).search_with_status("模拟查询", max_results=4)
    with ModelCassette(path) as cassette:
        actual = SearchCassette(None, cassette).search_with_status("模拟查询", max_results=4)
    assert actual == expected
    assert actual.metadata() == expected.metadata()
    assert actual.executed is True


def test_replay_dependencies_never_need_network(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        ask(RecordingCaller(lambda *a, **k: "模拟回答", cassette))
        ReaderCassette(Mock(enrich=Mock(return_value=[])), cassette).enrich([])
    with patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")):
        with ModelCassette(path) as cassette:
            assert ask(ReplayCaller(cassette)) == "模拟回答"
            assert ReaderCassette(None, cassette).enrich([]) == []
            with pytest.raises(CassetteError, match="cannot_have_live_fallback"):
                cassette.invoke("reader", {}, lambda: [])
            with pytest.raises(ValueError): ReaderCassette(Mock(), cassette)
            with pytest.raises(ValueError): SearchCassette(Mock(), cassette)


def test_private_recording_must_be_explicit_and_in_private_root(tmp_path):
    path = tmp_path / "mock.jsonl"
    with pytest.raises(CassettePrivacyError): ModelCassette(path, mode="record")
    with pytest.raises(CassettePrivacyError):
        ModelCassette(path, mode="record", allow_sensitive_recording=True,
                      private_root=tmp_path / "other")
    assert not path.exists()


def test_existing_recording_cannot_be_overwritten(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path):
        with pytest.raises(FileExistsError): record(path)


def test_credentials_and_streaming_rejected_before_call(tmp_path):
    path, live = tmp_path / "mock.jsonl", Mock()
    with record(path) as cassette:
        caller = RecordingCaller(live, cassette)
        with pytest.raises(CassettePrivacyError): ask(caller, api_key="fake-do-not-save")
        with pytest.raises(CassetteError, match="streaming_not_supported"):
            caller("mock", "system", "prompt", stream=True)
    live.assert_not_called()
    assert "fake-do-not-save" not in path.read_text(encoding="utf-8")


def test_aborted_context_is_explicitly_incomplete(tmp_path):
    path = tmp_path / "mock.jsonl"
    with pytest.raises(ValueError):
        with record(path) as cassette:
            ask(RecordingCaller(lambda *a, **k: "mock", cassette))
            raise ValueError("aborted")
    assert '"record_type":"interaction"' in path.read_text(encoding="utf-8")
    with pytest.raises(CassetteIncomplete): ModelCassette(path)


def test_truncation_and_tampering_are_rejected(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        ask(RecordingCaller(lambda *a, **k: "mock", cassette))
    original = path.read_text(encoding="utf-8")
    path.write_text(original[:-2], encoding="utf-8")
    with pytest.raises(CassetteIncomplete): ModelCassette(path)
    path.write_text(original.replace('"mock"', '"changed"'), encoding="utf-8")
    with pytest.raises(CassetteError, match="integrity_mismatch"): ModelCassette(path)


def test_recorded_error_preserves_safe_type_and_code(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        caller = RecordingCaller(Mock(side_effect=TimeoutError("model_timeout")), cassette)
        with pytest.raises(TimeoutError, match="model_timeout"): ask(caller)
    with ModelCassette(path) as cassette:
        with pytest.raises(TimeoutError, match="model_timeout"): ask(ReplayCaller(cassette))


def test_custom_recorded_error_never_imports_custom_exception(tmp_path):
    class FakeDependencyError(Exception): pass
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        caller = RecordingCaller(Mock(side_effect=FakeDependencyError("raw-secret-header")), cassette)
        with pytest.raises(FakeDependencyError): ask(caller)
    with ModelCassette(path) as cassette:
        with pytest.raises(RecordedCallError, match="dependency_call_failed"): ask(ReplayCaller(cassette))
    assert "raw-secret-header" not in path.read_text(encoding="utf-8")


def test_concurrent_recording_completes_and_replays_per_key(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        caller = RecordingCaller(lambda m, s, p, **k: p + "答", cassette)
        with ThreadPoolExecutor(max_workers=4) as pool:
            expected = list(pool.map(lambda i: ask(caller, str(i)), range(12)))
    with ModelCassette(path, ordering="per_key") as cassette:
        caller = ReplayCaller(cassette)
        with ThreadPoolExecutor(max_workers=4) as pool:
            actual = list(pool.map(lambda i: ask(caller, str(i)), reversed(range(12))))
    assert actual == list(reversed(expected))


def test_generic_local_fixture_interaction_needs_no_dataclass_import(tmp_path):
    path = tmp_path / "mock.jsonl"
    expected = ({"source_id": "mock-source", "text": "模拟资料"}, [1, 2])
    with record(path) as cassette:
        cassette.invoke("local_retrieval", {"query": "模拟"}, lambda: expected)
    with ModelCassette(path) as cassette:
        assert cassette.invoke("local_retrieval", {"query": "模拟"}) == expected


def test_summary_does_not_expose_raw_prompt_or_url(tmp_path):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        ask(RecordingCaller(lambda *a, **k: "PRIVATE_MOCK_RESPONSE", cassette), "PRIVATE_MOCK_PROMPT")
    cassette = ModelCassette(path)
    summary = json.dumps(cassette.summary())
    assert "PRIVATE_MOCK" not in summary
    assert str(path) not in summary
    cassette.close(complete=False)


def test_mock_qa_full_answer_bytes_match_on_replay(tmp_path):
    # The store and web supplement here are fixed invented fixtures. No old
    # final answer is represented as a recording of unavailable G3 model calls.
    from test_always_hybrid import AlwaysHybridTests, Supplement, QUESTION
    path = tmp_path / "mock-qa.jsonl"
    qa = AlwaysHybridTests().service(Supplement())
    with record(path) as cassette:
        qa.model_caller = RecordingCaller(qa.model_caller, cassette)
        before = qa.answer(QUESTION, allow_web=True)
    replayed = AlwaysHybridTests().service(Supplement())
    with ModelCassette(path) as cassette:
        replayed.model_caller = ReplayCaller(cassette)
        after = replayed.answer(QUESTION, allow_web=True)
    assert before["answer"].encode("utf-8") == after["answer"].encode("utf-8")
    assert before["answer_meta"]["answer_policy"] == after["answer_meta"]["answer_policy"]


def test_cli_inspection_does_not_reveal_private_content(tmp_path, capsys):
    from tools.replay.__main__ import main
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        ask(RecordingCaller(lambda *a, **k: "PRIVATE_MOCK_RESPONSE", cassette), "PRIVATE_MOCK_PROMPT")
    assert main(["inspect", "--cassette", str(path)]) == 0
    output = capsys.readouterr().out
    assert "PRIVATE_MOCK" not in output
    assert '"request_replay": "not_executed"' in output
    path.write_text("incomplete", encoding="utf-8")
    assert main(["inspect", "--cassette", str(path)]) == 2


@pytest.mark.parametrize("damage", ["boolean_schema", "boolean_count", "invalid_seq", "nan"])
def test_malformed_journal_fails_with_cassette_error(tmp_path, damage):
    path = tmp_path / "mock.jsonl"
    with record(path) as cassette:
        ask(RecordingCaller(lambda *a, **k: "mock", cassette))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if damage == "boolean_schema": rows[0]["schema_version"] = True
    if damage == "boolean_count": rows[-1]["count"] = True
    if damage == "invalid_seq": rows[1]["seq"] = "0"
    if damage == "nan": rows[1]["unexpected"] = float("nan")
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(CassetteError): ModelCassette(path)


def test_recording_wrapper_retains_usage_signature_and_legacy_replay_omits_it(tmp_path):
    import inspect
    path = tmp_path / "mock.jsonl"
    def live(model, system, prompt, *, return_usage=False, **kwargs):
        return ("answer", {"prompt_tokens": 2}) if return_usage else "answer"
    with record(path) as cassette:
        caller = RecordingCaller(live, cassette)
        assert "return_usage" in inspect.signature(caller).parameters
        assert ask(caller) == "answer"
    with ModelCassette(path) as cassette:
        caller = ReplayCaller(cassette)
        assert "return_usage" not in inspect.signature(caller).parameters
        assert ask(caller) == "answer"
