"""Independent feature gates and a request-wide bounded model-call budget."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import time


def enabled(name):
    return os.getenv(name, 'false').strip().lower() in {'true','1','yes','on'}


def mode(name):
    value = os.getenv(name, 'off').strip().lower()
    return value if value in {'off','shadow','on'} else 'off'


def active():
    return (any(enabled(name) for name in ('MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE','MARINE_CONNECTOR_V2'))
            or mode('MARINE_TYPED_GROUNDING') != 'off' or mode('MARINE_RELATION_GUARD') != 'off')


class AnswerBudgetExceeded(RuntimeError):
    pass


@dataclass
class AnswerBudget:
    max_calls: int = 10
    max_seconds: float = 60.0
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    exhausted: str | None = None

    def remaining(self):
        return max(0.0, self.max_seconds - (time.monotonic() - self.started))

    def take(self):
        if self.calls >= self.max_calls:
            self.exhausted = 'answer_call_budget_exceeded'
            raise AnswerBudgetExceeded(self.exhausted)
        if self.remaining() <= 0:
            self.exhausted = 'answer_time_budget_exceeded'
            raise AnswerBudgetExceeded(self.exhausted)
        self.calls += 1

    def summary(self):
        return {'calls':self.calls,'max_calls':self.max_calls,'max_seconds':self.max_seconds,
                'elapsed_seconds':round(time.monotonic()-self.started,3),'exhausted':self.exhausted}


_BUDGET = ContextVar('phase2_answer_budget', default=None)
_TURN_KEY = ContextVar('phase2_turn_fingerprint', default=None)


def current_turn_key():
    return _TURN_KEY.get() if enabled('MARINE_FACET_CONTRACT') else None


def set_turn_key(value):
    if enabled('MARINE_FACET_CONTRACT'):
        _TURN_KEY.set(value)


@contextmanager
def turn_context():
    token=_TURN_KEY.set(None)
    try:yield
    finally:_TURN_KEY.reset(token)


def current_budget():
    return _BUDGET.get()


@contextmanager
def budget_context():
    previous = current_budget()
    if previous is not None or not active():
        yield previous
        return
    budget = AnswerBudget()
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)
