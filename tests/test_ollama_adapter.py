import json
from types import SimpleNamespace

import pytest

from ira.adapters import AdapterError
from ira.ollama_adapter import OllamaAdapter
from ira.prompt import build_prompt

DIGEST = 'a' * 64


def spec():
    return SimpleNamespace(model='qwen3:4b', model_digest=DIGEST, timeout_s=30)


def prompt():
    return build_prompt(base_commit='b' * 40, path='src/labels.py', issue_title='A bug',
                        issue_body='Fix it.', source=b'def normalize_label(text):\n    return text.lower()\n', limits={})


def test_local_proposal_preserves_model_output_and_derives_diff(monkeypatch):
    adapter = OllamaAdapter(spec())
    raw = json.dumps({'replacement_return': "return ' '.join(text.split()).lower()", 'summary': 'Fix', 'assumptions': []})
    calls = []
    def fake(path, body=None):
        calls.append((path, body))
        if path == '/api/tags':
            return {'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]}
        return {'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': raw}
    monkeypatch.setattr(adapter, '_request', fake)
    normalized = json.loads(adapter.propose(prompt(), 32768))
    assert normalized['base_commit'] == 'b' * 40
    assert "-    return text.lower()\n+    return ' '.join(text.split()).lower()\n" in normalized['diff']
    assert adapter.record['raw_model_response'] == raw
    assert adapter.record['actual_prompt_version'] == 'ira.ollama.return.v1'
    assert adapter.record['actual_prompt_text'] == calls[1][1]['prompt']
    assert adapter.record['transformation'].startswith('model-proposed single return statement')
    assert calls[1][1]['stream'] is False
    with pytest.raises(AdapterError, match='attempt_limit'):
        adapter.propose(prompt(), 32768)


def test_digest_mismatch_stops_before_generation(monkeypatch):
    adapter = OllamaAdapter(spec())
    paths = []
    def fake(path, body=None):
        paths.append(path)
        return {'models': [{'name': 'qwen3:4b', 'digest': '0' * 64}]}
    monkeypatch.setattr(adapter, '_request', fake)
    with pytest.raises(AdapterError, match='model_digest_mismatch'):
        adapter.propose(prompt(), 32768)
    assert paths == ['/api/tags']


def test_incomplete_model_output_is_not_accepted(monkeypatch):
    adapter = OllamaAdapter(spec())
    def fake(path, body=None):
        return ({'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]} if path.endswith('tags') else
                {'model': 'qwen3:4b', 'done': True, 'done_reason': 'length', 'response': '{}'})
    monkeypatch.setattr(adapter, '_request', fake)
    with pytest.raises(AdapterError, match='ollama_truncated'):
        adapter.propose(prompt(), 32768)


def test_duplicate_model_json_keys_are_rejected(monkeypatch):
    adapter = OllamaAdapter(spec())
    def fake(path, body=None):
        return ({'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]} if path.endswith('tags') else
                {'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop',
                 'response': '{"replacement_return":"return 1","replacement_return":"return 2","summary":"x","assumptions":[]}'})
    monkeypatch.setattr(adapter, '_request', fake)
    with pytest.raises(AdapterError, match='ollama_invalid_file_proposal'):
        adapter.propose(prompt(), 32768)


def test_pipeline_blocks_local_model_execution_and_records_raw(monkeypatch, built_repo, tmp_path):
    from ira import runner
    from ira.evaluate import make_request

    request = make_request('good_trim_collapse', built_repo, 'local-test')
    request['adapter'] = {'kind': 'local_model', 'model': 'qwen3:4b', 'model_digest': DIGEST, 'timeout_s': 30}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    raw = json.dumps({'replacement_return': "return ' '.join(text.split()).lower()",
                      'summary': 'Normalize whitespace', 'assumptions': []})
    def fake(self, endpoint, body=None):
        return ({'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]} if endpoint.endswith('tags') else
                {'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': raw})
    monkeypatch.setattr(OllamaAdapter, '_request', fake)
    class Executor:
        def run(self, **kwargs):
            raise AssertionError('local model output must not reach execution')
    packet, output = runner.execute(path, tmp_path / 'output', work_root=tmp_path / 'work', executor=Executor())
    assert packet['terminal_status'] == 'blocked_boundary_unverified'
    assert packet['status_reason'] == 'execution_not_authorized:untrusted_execution_not_enabled'
    assert packet['adapter']['live']['raw_model_response'] == raw
    assert packet['execution']['candidate_executed'] is False
    assert 'Actual local model response' in (output / 'review.md').read_text()
