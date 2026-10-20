"""P0 diagnostics and SDK usage tests. No network or live model calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from marine_kb.answer_trace import (
    AnswerTrace, current_trace, error_code, normalize_usage, sha256_text,
    trace_context, trace_enabled, trace_model_call,
)


def test_disabled_trace_does_not_hash_or_change_answer(monkeypatch):
    monkeypatch.delenv("MARINE_TRACE", raising=False)
    assert not trace_enabled()
    with patch("marine_kb.answer_trace.sha256_text", side_effect=AssertionError("off mode should not hash")):
        with trace_context() as trace:
            assert current_trace() is None
            with trace_model_call("generate", "private system", "private question") as call:
                answer = "实际回答\n"
                call.complete(answer, {"prompt_tokens": 2})
            trace.event("ignored", text="private")
            assert trace.record_evidence("generate", [{"citation": 1, "passages": []}]) == {}
            assert trace.to_dict() == {}
    assert answer.encode("utf-8") == "实际回答\n".encode("utf-8")


def test_model_trace_hashes_exact_bytes_and_preserves_unknown_usage(monkeypatch):
    monkeypatch.setenv("MARINE_TRACE", "true")
    with trace_context() as trace:
        with trace_model_call("generate", "system", "question\n", attempt=2,
                              system_id="STRUCTURED_ANSWER_PROMPT@abcd") as call:
            call.complete("answer\n", SimpleNamespace(prompt_tokens=12, completion_tokens=3))
    result = trace.to_dict()
    assert result["schema_version"] == 1
    assert current_trace() is None
    row, = result["model_calls"]
    assert row["stage"] == "generate" and row["attempt"] == 2
    assert row["system_id"] == "STRUCTURED_ANSWER_PROMPT@abcd"
    assert row["prompt_sha256"] == sha256_text("question\n")
    assert row["response_sha256"] == sha256_text("answer\n")
    assert row["prompt_tokens"] == 12 and row["completion_tokens"] == 3
    assert row["total_tokens"] is None
    assert row["latency_ms"] >= 0 and row["error_code"] is None
    assert "question" not in row and "answer" not in row and "system" not in row


def test_usage_is_provider_only_and_invalid_counts_are_not_estimated():
    assert normalize_usage(None) == dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens"))
    assert normalize_usage({"prompt_tokens": True, "completion_tokens": "12", "total_tokens": -1}) == normalize_usage(None)
    assert normalize_usage({"prompt_tokens": 3, "completion_tokens": 2})["total_tokens"] is None


def test_errors_are_recorded_without_suppressing_or_leaking_them():
    original = ValueError("delivery_span_not_in_answer")
    with trace_context(enabled=True) as trace:
        with pytest.raises(ValueError) as caught:
            with trace_model_call("completeness", "sys", "private"):
                raise original
    assert caught.value is original
    assert trace.to_dict()["model_calls"][0]["error_code"] == "delivery_span_not_in_answer"
    assert error_code(RuntimeError("Authorization: Bearer sk-do-not-export-this")) == "runtime_error"
    assert "sk-do-not" not in json.dumps(trace.to_dict())


def test_nested_trace_reuse_and_explicit_disable_restore_context():
    assert current_trace() is None
    with trace_context(enabled=True) as outer:
        with trace_context() as nested:
            assert nested is outer
        with trace_context(enabled=False) as disabled:
            assert current_trace() is None and disabled.to_dict() == {}
        assert current_trace() is outer
        second = AnswerTrace(enabled=True)
        with trace_context(trace=second):
            assert current_trace() is second
        assert current_trace() is outer
    assert current_trace() is None


def test_trace_isolated_across_threads():
    def request(index):
        with trace_context(enabled=True) as trace:
            with trace_model_call("generate", "sys", f"question{index}") as call:
                call.complete(f"answer{index}")
            return trace.to_dict()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(request, range(9)))
    assert len({result["trace_id"] for result in results}) == 9
    for index, result in enumerate(results):
        row, = result["model_calls"]
        assert row["response_sha256"] == sha256_text(f"answer{index}")
        assert row["call_id"] == 1


def test_trace_isolated_across_async_tasks():
    async def request(index):
        with trace_context(enabled=True) as trace:
            await asyncio.sleep(0)
            with trace_model_call("generate", "sys", str(index)) as call:
                await asyncio.sleep(0)
                call.complete(str(index))
            return trace.to_dict()
    async def run():
        return await asyncio.gather(*(request(index) for index in range(4)))
    results = asyncio.run(run())
    assert len({result["trace_id"] for result in results}) == 4
    assert [item["model_calls"][0]["response_sha256"] for item in results] == [sha256_text(str(i)) for i in range(4)]


def test_public_events_redact_prompts_prose_credentials_and_detach_snapshots():
    with trace_context(enabled=True) as trace:
        trace.event("coverage_review", error_code="unsupported_claim", attempt=2,
                    issues=[{"type": "unsupported_claim", "reason": "资料原文中的业务内容"}],
                    prompt="private prompt", text="private evidence", api_key="unit-test-secret",
                    untrusted_reason="Bearer sk-never-export-this-secret")
        result = trace.to_dict()
    encoded = json.dumps(result, ensure_ascii=False)
    for private in ("资料原文中的业务内容", "private prompt", "private evidence", "unit-test-secret", "sk-never-export"):
        assert private not in encoded
    fields = result["events"][0]["fields"]
    assert fields["error_code"] == "unsupported_claim"
    assert fields["issues"][0]["reason"]["redacted"] is True
    result["events"].clear()
    assert len(trace.to_dict()["events"]) == 1


def test_ascii_private_identifiers_and_exception_strings_are_not_public_codes():
    with trace_context(enabled=True) as trace:
        trace.event("private_ascii_event", **{
            "secret_ascii_identifier": "private_ascii_value",
            "issues": [{"secret_key": "full", "target_id": "private_ascii_id", "kind": "private_ascii_kind"}],
            "error_code": "account_123456789_private",
        })
        with trace_model_call("private_ascii_stage", "sys", "message", system_id="private_ascii_system") as call:
            call.complete("answer")
    encoded = json.dumps(trace.to_dict())
    for value in ("private_ascii", "secret_ascii_identifier", "secret_key", "account_123456789_private"):
        assert value not in encoded
    assert error_code(ValueError("account_123456789_private")) == "value_error"
    assert error_code(ValueError("delivery_span_not_in_answer")) == "delivery_span_not_in_answer"


def test_trace_caps_events_calls_and_nested_payloads_without_changing_results():
    with trace_context(enabled=True) as trace:
        for index in range(300):
            trace.event("coverage_review", issues=[{"kind": "unasked_gap"}] * 100)
        answers = []
        for index in range(140):
            with trace_model_call("generate", "sys", str(index)) as call:
                answer = str(index)
                call.complete(answer)
                answers.append(answer)
        nested = {"issues": {"issues": {"issues": {"issues": {"issues": {"issues": {"issues": {"issues": {"issues": {"issues": "private"}}}}}}}}}}
        second = AnswerTrace(enabled=True)
        second.event("coverage_review", **nested)
    result = trace.to_dict()
    assert answers == [str(index) for index in range(140)]
    assert len(result["events"]) == 256 and len(result["model_calls"]) == 128
    assert result["truncated"] == {"events": 44, "model_calls": 12}
    assert result["events"][0]["fields"]["issues"][-1] == {"_truncated_items": 36}
    assert '"truncated": true' in json.dumps(second.to_dict())


def test_evidence_view_records_each_actual_selected_passage():
    with trace_context(enabled=True) as trace:
        rows = trace.record_evidence("generate", [{"citation": 2, "passages": [
            {"id": 1, "text": "原始段落"}, {"id": 3, "text": "another"},
        ]}])
    assert rows == {"2": [{"passage_id": 1, "sha256": sha256_text("原始段落")},
                          {"passage_id": 3, "sha256": sha256_text("another")} ]}
    event, = trace.to_dict()["events"]
    assert event["fields"]["view_sha256"] == sha256_text(rows)
    assert "原始段落" not in json.dumps(event, ensure_ascii=False)


def _client(captured, response):
    class Client:
        def __init__(self, **options):
            captured.append(("client", options))
            def create(**kwargs):
                captured.append(("completion", kwargs))
                return response
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    return Client


_CONFIG = {"mock": {"api_key": "unit-test", "base_url": "https://example.invalid", "code": "mock", "provider": "deepseek"}}


def test_model_layer_legacy_signature_and_bytes_unchanged():
    from model_layer import call_model
    captured = []
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="文字\n"))])
    with patch("model_layer.MODELS", _CONFIG), patch("model_layer.OpenAI", _client(captured, response)):
        assert call_model("mock", "sys", "message", stream=False) == "文字\n"
    assert captured[1][1]["stream"] is False
    assert "return_usage" not in captured[1][1] and "stream_options" not in captured[1][1]


@pytest.mark.parametrize("usage,expected", [
    (SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
     {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}),
    (None, {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}),
])
def test_model_layer_opt_in_usage(usage, expected):
    from model_layer import call_model
    captured = []
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="unchanged"))], usage=usage)
    with patch("model_layer.MODELS", _CONFIG), patch("model_layer.OpenAI", _client(captured, response)):
        assert call_model("mock", "sys", "message", stream=False, return_usage=True) == ("unchanged", expected)


def test_stream_usage_updates_on_iteration_and_default_stays_generator():
    from model_layer import call_model
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))]),
              SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1, total_tokens=4))]
    captured = []
    with patch("model_layer.MODELS", _CONFIG), patch("model_layer.OpenAI", _client(captured, chunks)):
        generator, usage = call_model("mock", "sys", "message", return_usage=True)
        assert usage["total_tokens"] is None
        assert list(generator) == ["a"]
        assert usage == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
        default_generator = call_model("mock", "sys", "message")
        assert list(default_generator) == ["a"]
    completions = [options for kind, options in captured if kind == "completion"]
    assert completions[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in completions[1]
