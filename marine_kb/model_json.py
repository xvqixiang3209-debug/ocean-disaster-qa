"""Parse model JSON with a narrow syntax recovery; grounding remains separate."""
import json
import re


def model_json(raw):
    value=re.sub(r'^```(?:json)?\s*|\s*```$', '', str(raw)).strip()
    try:return json.loads(value)
    except json.JSONDecodeError as exc:
        if 'Invalid control character' not in exc.msg:raise
        # Models occasionally put a literal newline in a quoted JSON answer.
        # This repairs JSON syntax only, never values, citations or fields.
        return json.loads(value,strict=False)
