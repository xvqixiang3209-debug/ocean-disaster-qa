"""P0 QA integration uses only fixed invented evidence and mocked responses."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import socket
from unittest.mock import patch

import pytest
from flask import Flask

from marine_kb.answer_trace import trace_context
from marine_kb.chat_api import create_chat_blueprint
from marine_kb.qa import MarineQuestionService
from marine_kb.replay import CassetteMismatch, ModelCassette, RecordingCaller, ReplayCaller
from test_marine_qa import FakeStore, source


QUESTION = "台风来临前要怎么做？"
ANSWER = "台风来临前，组织渔船回港避风。[资料1]"
META = {"as_of": "2026-10-20", "normalized_question": QUESTION,
        "subquestions": [{"id": "q1", "question": QUESTION}]}


@pytest.fixture(autouse=True)
def fixed_qa_clock(monkeypatch):
    """Record and replay the same question time, not whichever wall-clock second.

    The full HTTP route puts ``qa.datetime.now()`` into the generation payload's
    ``date``. Without this clock dependency fixture a record/replay pair crossing
    a second correctly fails strict request hashing, making this test flaky.
    Cassette hashing, request ordering and full-consumption checks stay enabled.
    """
    class FixedDateTime(datetime):
        current = datetime.fromisoformat("2026-10-20T12:00:00+08:00")

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr("marine_kb.qa.datetime", FixedDateTime)
    return FixedDateTime


def hit():
    return source("台风来临前，组织渔船回港避风。", title="模拟台风防御技术资料",
                  filename="mock-guidance.txt", status="approved", category="科普",
                  metadata={"external_allowed": True})


class UsageCaller:
    def __init__(self):
        self.options = []

    def __call__(self, model, system, prompt, *, return_usage=False, **options):
        self.options.append({**options, "return_usage": return_usage})
        if "normalized_question" in system:
            text = json.dumps({"normalized_question": json.loads(prompt)["question"]}, ensure_ascii=False)
        else:
            text = json.dumps({"answerability": "full", "answer": ANSWER,
                               "evidence_quotes": [{"citation": 1, "passage_id": 1}]}, ensure_ascii=False)
        return (text, {"prompt_tokens": 29, "completion_tokens": 13, "total_tokens": 42}) if return_usage else text


def qa(caller):
    return MarineQuestionService(FakeStore([hit()]), model_caller=caller,
        external_enabled=True, structured_answers=True, model_configured=True)


def record(path):
    return ModelCassette(path, mode="record", private_root=path.parent, allow_sensitive_recording=True)


def organized(service):
    return service._organized_answer(QUESTION, "mock-model", [hit()], deepcopy(META))


def test_organized_mock_record_replay_has_identical_answer_and_provider_usage(tmp_path):
    path, live = tmp_path / "mock-organized.jsonl", UsageCaller()
    with patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")):
        with record(path) as journal:
            with trace_context(enabled=True) as trace:
                before = organized(qa(RecordingCaller(live, journal)))
        before_trace = trace.to_dict()
        with ModelCassette(path) as journal:
            with trace_context(enabled=True) as trace:
                after = organized(qa(ReplayCaller(journal)))
        after_trace = trace.to_dict()
    assert before["answer"] == after["answer"] == ANSWER
    assert before["answer"].encode() == after["answer"].encode()
    assert before["rag_sources"] == after["rag_sources"]
    assert before_trace["model_calls"] and after_trace["model_calls"]
    for recorded, replayed in zip(before_trace["model_calls"], after_trace["model_calls"]):
        assert recorded["stage"] == replayed["stage"] == "generate"
        assert recorded["prompt_sha256"] == replayed["prompt_sha256"]
        assert recorded["response_sha256"] == replayed["response_sha256"]
        assert replayed["prompt_tokens"] == 29
        assert replayed["completion_tokens"] == 13
        assert replayed["total_tokens"] == 42
    assert all(row["return_usage"] for row in live.options)


def test_trace_disabled_or_enabled_preserves_mock_answer_bytes():
    off_caller, on_caller = UsageCaller(), UsageCaller()
    with patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")):
        with trace_context(enabled=False) as disabled:
            before = organized(qa(off_caller))
        with trace_context(enabled=True) as enabled:
            after = organized(qa(on_caller))
    assert before["answer"].encode() == after["answer"].encode()
    assert before["rag_sources"] == after["rag_sources"]
    assert disabled.to_dict() == {}
    assert enabled.to_dict()["model_calls"][0]["prompt_tokens"] == 29
    assert not any(row["return_usage"] for row in off_caller.options)
    assert all(row["return_usage"] for row in on_caller.options)


def client_for(service):
    app = Flask(__name__)
    app.register_blueprint(create_chat_blueprint(service))
    client = app.test_client()
    token = client.get("/api/assistant/session").json["token"]
    return client, {"X-Assistant-Token": token}


def test_http_mock_replay_trace_includes_planner_and_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("MARINE_TRACE", "true")
    path, live = tmp_path / "mock-http.jsonl", UsageCaller()
    with patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")):
        with record(path) as journal:
            client, headers = client_for(qa(RecordingCaller(live, journal)))
            before = client.post("/api/assistant/ask", json={"question": QUESTION}, headers=headers)
        assert before.status_code == 200, before.json
        with ModelCassette(path) as journal:
            client, headers = client_for(qa(ReplayCaller(journal)))
            after = client.post("/api/assistant/ask", json={"question": QUESTION}, headers=headers)
            assert after.status_code == 200, after.json
    assert before.json["answer"].encode() == after.json["answer"].encode()
    stages = [row["stage"] for row in after.json["trace"]["model_calls"]]
    assert "normalize" in stages and "generate" in stages
    assert all(row["prompt_tokens"] == 29 for row in after.json["trace"]["model_calls"])
    assert all(row["completion_tokens"] == 13 for row in after.json["trace"]["model_calls"])
    assert len(live.options) == len(stages)
    before_calls = before.json["trace"]["model_calls"]
    after_calls = after.json["trace"]["model_calls"]
    assert [(row["stage"], row["prompt_sha256"], row["response_sha256"]) for row in before_calls] == [
        (row["stage"], row["prompt_sha256"], row["response_sha256"]) for row in after_calls]


def test_http_replay_rejects_changed_query_clock(tmp_path, monkeypatch, fixed_qa_clock):
    """One changed second reproduces the failure and must still be rejected."""
    monkeypatch.setenv("MARINE_TRACE", "true")
    path, live = tmp_path / "mock-clock.jsonl", UsageCaller()
    with patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")):
        with record(path) as journal:
            client, headers = client_for(qa(RecordingCaller(live, journal)))
            before = client.post("/api/assistant/ask", json={"question": QUESTION}, headers=headers)
        assert before.status_code == 200, before.json
        fixed_qa_clock.current += timedelta(seconds=1)
        journal = ModelCassette(path)
        client, headers = client_for(qa(ReplayCaller(journal)))
        after = client.post("/api/assistant/ask", json={"question": QUESTION}, headers=headers)
        assert after.status_code == 503
        assert "answer" not in after.json
        # Normalization has no date and matches; generation includes the changed
        # query timestamp, so its recorded interaction must remain unconsumed.
        assert journal.summary()["consumed"] == 1
        with pytest.raises(CassetteMismatch, match="unconsumed_interactions"):
            journal.assert_consumed()
        journal.close(complete=False)


def test_http_unknown_prompt_reports_failure_without_live_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("MARINE_TRACE", "true")
    path, live = tmp_path / "mock-http.jsonl", UsageCaller()
    with record(path) as journal:
        client, headers = client_for(qa(RecordingCaller(live, journal)))
        assert client.post("/api/assistant/ask", json={"question": QUESTION}, headers=headers).status_code == 200
    calls_before = len(live.options)
    journal = ModelCassette(path)
    with patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")):
        client, headers = client_for(qa(ReplayCaller(journal)))
        result = client.post("/api/assistant/ask", json={"question": "台风来临前要怎么做，请具体说明？"}, headers=headers)
    assert result.status_code == 503
    assert "answer" not in result.json
    assert len(live.options) == calls_before
    # Existing QA catches provider exceptions and emits a user-safe HTTP error;
    # the offline harness must still assert consumption, rather than count this
    # HTTP interaction as a successful cassette replay.
    with pytest.raises(CassetteMismatch, match="unconsumed_interactions"):
        journal.assert_consumed()
    journal.close(complete=False)


def test_direct_replay_unknown_prompt_does_not_synthesize_an_answer(tmp_path):
    path = tmp_path / "mock-direct.jsonl"
    with record(path) as journal:
        RecordingCaller(UsageCaller(), journal)("mock-model", "system", "known", stream=False)
    journal = ModelCassette(path)
    with pytest.raises(CassetteMismatch, match="request_or_order_mismatch"):
        ReplayCaller(journal)("mock-model", "system", "unknown", stream=False)
    journal.close(complete=False)
