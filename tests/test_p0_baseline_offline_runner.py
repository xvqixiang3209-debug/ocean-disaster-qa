"""No external account, database or paid call is used by these checks."""
import json
import socket

import pytest

from marine_kb.replay import CassetteMismatch, ModelCassette, ReplayCaller
from tools.replay_p0_baseline import RecordedDictionaryOrder, frozen_datetime, network_blocked


def test_network_blocks_dns_and_connection_without_live_fallback():
    with network_blocked() as attempts:
        with pytest.raises(RuntimeError, match='offline_replay_network_blocked'):
            socket.getaddrinfo('example.com', 443)
        with socket.socket() as sock:
            with pytest.raises(RuntimeError, match='offline_replay_network_blocked'):
                sock.connect(('127.0.0.1', 8769))
    assert len(attempts) == 2


def test_frozen_clock_preserves_recorded_second_and_microseconds():
    cls = frozen_datetime('2026-10-20T00:50:13.346293+08:00')
    assert cls.now().isoformat() == '2026-10-20T00:50:13.346293'
    from datetime import timezone
    assert cls.now(timezone.utc).isoformat() == '2026-10-19T16:50:13.346293+00:00'


def test_restores_only_equal_dictionary_key_order_without_changing_values():
    order = RecordedDictionaryOrder()
    original = {'b': 2, 'a': {'y': 3, 'x': 4}}
    order.observe(original)
    restored = order.restore({'a': {'x': 4, 'y': 3}, 'b': 2})
    assert restored == original
    assert json.dumps(restored) == json.dumps(original)
    assert order.restored == 2
    assert not order.ambiguous


def test_ambiguous_recorded_order_is_never_guessed():
    order = RecordedDictionaryOrder()
    order.observe({'b': 2, 'a': 1})
    order.observe({'a': 1, 'b': 2})
    actual = {'b': 2, 'a': 1}
    restored = order.restore(actual)
    assert tuple(restored) == tuple(actual)
    assert len(order.ambiguous) == 1
    assert order.restored == 0


def test_semantically_different_dictionary_is_not_restored():
    order = RecordedDictionaryOrder()
    order.observe({'b': 2, 'a': 1})
    actual = {'a': 1, 'b': 99}
    restored = order.restore(actual)
    assert tuple(restored) == tuple(actual)
    assert restored['b'] == 99
    assert order.restored == 0


def test_strict_model_request_fingerprint_is_not_canonicalized(tmp_path):
    path = tmp_path / 'fixture.jsonl'
    with ModelCassette(path, mode='record', allow_sensitive_recording=True, private_root=tmp_path) as journal:
        journal.invoke('model', {'args': ('model', 'system', '{"b":2,"a":1}'), 'kwargs': {'stream': False}}, lambda: 'reply')
    journal = ModelCassette(path, ordering='per_key')
    try:
        caller = ReplayCaller(journal)
        with pytest.raises(CassetteMismatch):
            caller('model', 'system', '{"a":1,"b":2}', stream=False)
        with pytest.raises(CassetteMismatch):
            caller('model', 'system', '{"b":99,"a":1}', stream=False)
        assert caller('model', 'system', '{"b":2,"a":1}', stream=False) == 'reply'
        journal.assert_consumed()
    finally:
        journal.close(complete=False)
