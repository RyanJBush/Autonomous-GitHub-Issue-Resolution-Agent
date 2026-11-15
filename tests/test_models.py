import json

import pytest

from conftest import PINNED
from ira import caps
from ira.evaluate import make_request
from ira.models import CannotPropose, Proposal, RequestError, SchemaError, load_request, parse_response

GOOD = {"schema_version": "ira.response.v1", "outcome": "propose", "base_commit": PINNED["base_commit"],
        "diff": "--- a/src/labels.py\n", "summary": "s", "assumptions": []}


def raw(obj):
    return json.dumps(obj).encode()


def code(data, max_bytes=caps.MAX_RESPONSE_BYTES):
    with pytest.raises(SchemaError) as info:
        parse_response(data, max_bytes)
    return info.value.code


def test_valid_proposal_and_cannot_propose():
    assert isinstance(parse_response(raw(GOOD), 32768), Proposal)
    cp = {"schema_version": "ira.response.v1", "outcome": "cannot_propose", "reason": "r"}
    assert isinstance(parse_response(raw(cp), 32768), CannotPropose)


@pytest.mark.parametrize("data,expected", [
    (b'{"a": 1, "a": 2}', "duplicate_key"),
    (b'{"outer": {"k": 1, "k": 1}}', "duplicate_key"),
    (b'{"x": NaN}', "non_finite_number"),
    (b'{"x": Infinity}', "non_finite_number"),
    (b'{"x": -Infinity}', "non_finite_number"),
    (b'{"x": 1} {"y": 2}', "invalid_json"),
    (b'', "invalid_json"),
    (b'\xff\xfe', "not_utf8"),
    (b'\xef\xbb\xbf{}', "bom_not_allowed"),
    (b'[]', "not_object"),
    (b'"text"', "not_object"),
    (b'[' * 100000 + b']' * 100000, "invalid_json"),
])
def test_strict_json(data, expected):
    assert code(data, 10_000_000) == expected


def test_size_cap_is_checked_before_parsing():
    assert code(b" " * 101 + raw(GOOD), 100) == "response_too_large"


@pytest.mark.parametrize("mutation", [
    {"extra": 1}, {"outcome": "apply"}, {"schema_version": "v2"}, {"base_commit": "ABC"},
    {"assumptions": ["a"] * 6}, {"assumptions": ["a" * 301]}, {"summary": ""}, {"summary": "s" * 501},
    {"summary": "line1\nline2"}, {"summary": "bidi ‮"}, {"summary": 5}, {"diff": ""}, {"reason": "both"},
])
def test_schema_violations(mutation):
    assert code(raw({**GOOD, **mutation})) == "schema_violation"


def test_schema_error_never_echoes_input():
    secretish = "INPUT-VALUE-MUST-NOT-APPEAR"
    with pytest.raises(SchemaError) as info:
        parse_response(raw({**GOOD, "summary": 7, "unknown": secretish}), 32768)
    assert secretish not in json.dumps(info.value.errors) + str(info.value)


def _req(**over):
    req = make_request("good_trim_collapse", "/tmp/repo", "r1")
    for dotted, value in over.items():
        node = req
        keys = dotted.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return json.dumps(req).encode()


def test_request_valid():
    assert load_request(_req()).run_id == "r1"


@pytest.mark.parametrize("over", [
    {"limits.max_patch_bytes": caps.MAX_PATCH_BYTES + 1}, {"limits.max_hunks": 0},
    {"limits.max_changed_lines": caps.MAX_CHANGED_LINES + 1}, {"attempts": 2}, {"run_id": "../x"},
    {"repository.allowlisted_file": "../src/labels.py"}, {"repository.allowlisted_file": "/abs/x.py"},
    {"repository.allowlisted_file": "tests/test_x.py"}, {"repository.allowlisted_file": "src/conftest.py"},
    {"repository.allowlisted_file": "src/.hidden.py"}, {"repository.allowlisted_file": "src/data.json"},
    {"adapter.kind": "live_model"}, {"issue.body": "b" * (caps.MAX_ISSUE_BODY_CHARS + 1)},
    {"execution.timeout_s": 0}, {"extra_field": True},
])
def test_request_rejections(over):
    with pytest.raises(RequestError):
        load_request(_req(**over))


def test_request_cannot_raise_caps_via_changed_lines():
    with pytest.raises(RequestError):
        load_request(_req(**{"limits.max_added_lines": 5, "limits.max_removed_lines": 5,
                             "limits.max_changed_lines": 11}))
