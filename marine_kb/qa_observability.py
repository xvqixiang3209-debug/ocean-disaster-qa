"""P0 observation adapters. They do not change evidence or answer decisions."""
from contextlib import contextmanager
from contextvars import copy_context, ContextVar
from functools import wraps
import inspect

from .answer_trace import current_trace, error_code, sha256_text, trace_context, trace_model_call

_STAGE = ContextVar('qa_model_stage', default=None)


@contextmanager
def model_stage(stage):
    token = _STAGE.set(stage)
    try:
        yield
    finally:
        _STAGE.reset(token)


def submit_observed(pool, function, *args, **kwargs):
    """Propagate this request's diagnostics into a single worker invocation."""
    return pool.submit(copy_context().run, function, *args, **kwargs)


def trace_request(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        from .phase2 import budget_context,turn_context
        with trace_context(), budget_context(),turn_context():
            return function(*args, **kwargs)
    return wrapped


def attach_trace(result):
    trace = current_trace()
    if trace is not None and isinstance(result, dict):
        result.setdefault('answer_meta', {})['trace'] = trace.to_dict()
    from .phase2 import current_budget
    budget = current_budget()
    if budget is not None and isinstance(result, dict):
        result.setdefault('answer_meta', {})['answer_budget'] = budget.summary()
    return result


def traced_answer(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        from .phase2 import budget_context,turn_context
        with trace_context(), budget_context(),turn_context():
            return attach_trace(function(*args, **kwargs))
    return wrapped


def observed_model_call(caller, model, system, prompt, options, *, stage='unknown', attempt=1):
    """Get usage only from callers explicitly supporting the optional contract.

    No optimistic retry on TypeError: it could repeat a paid provider request.
    Legacy callers get precisely their original options and string result.
    """
    active = current_trace()
    stage = _STAGE.get() or stage
    if active is not None:
        attempt = 1 + sum(row['stage'] == stage for row in active.to_dict()['model_calls'])
    kwargs = dict(options)
    wants_usage = False
    if active is not None:
        try:
            wants_usage = 'return_usage' in inspect.signature(caller).parameters
        except (ValueError, TypeError):
            pass
        if wants_usage:
            kwargs['return_usage'] = True
    with trace_model_call(stage, system, prompt, attempt=attempt) as call:
        value = caller(model, system, prompt, **kwargs)
        usage = None
        if wants_usage and isinstance(value, tuple) and len(value) == 2:
            value, usage = value
        call.complete(value, usage)
        return str(value or '').strip()


def event(name, **fields):
    trace = current_trace()
    if trace is not None:
        trace.event(name, **fields)


def review_summary(review):
    if not isinstance(review, dict):
        return None
    return {
        'verdict': review.get('verdict'),
        'issues': [
            {'kind': issue.get('kind'), 'target_id': issue.get('target_id'),
             'part_id': issue.get('part_id'), 'issue_sha256': sha256_text(issue)}
            for issue in review.get('issues', []) if isinstance(issue, dict)
        ],
        'error_type': review.get('error_type'), 'error_code': review.get('error_code'),
    }


def evidence_view(stage, evidence):
    trace = current_trace()
    if trace is not None:
        return trace.record_evidence(stage, evidence)
    return None


def observe_failure(stage, exc, **fields):
    event('stage_failure', stage=stage, error_type=type(exc).__name__, error_code=error_code(exc), **fields)
