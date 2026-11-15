import hashlib
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from ira.adapters import AdapterError
from ira.models import RequestError, load_request
from ira.ollama_adapter import OllamaAdapter, _MAX_WIRE
from ira.replay import LOCAL_RECORDING_LABEL, ReplayAdapter, write_recording
from ira.authorize import NotAuthorized, authorize
from conftest import ROOT
from test_ollama_adapter import DIGEST, spec, prompt


def test_request_proxy_bypassed_and_invalid_json_separate(monkeypatch):
    import urllib.request
    seen = []
    class Opener:
        def open(self, req, timeout):
            assert req.full_url == 'http://127.0.0.1:11434/api/tags'
            return io.BytesIO(b'not json')
    monkeypatch.setattr(urllib.request, 'ProxyHandler', lambda proxies: seen.append(proxies))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda handler: Opener())
    with pytest.raises(AdapterError, match='ollama_invalid_json'):
        OllamaAdapter(spec())._request('/api/tags')
    assert seen == [{}]


@pytest.mark.parametrize('result,reason', [
    (b'x' * (_MAX_WIRE + 1), 'ollama_response_too_large'),
    (b'[]', 'ollama_invalid_json'),
    (urllib.error.URLError('offline'), 'ollama_unavailable'),
])
def test_request_wire_failures(monkeypatch, result, reason):
    import urllib.request
    class Opener:
        def open(self, req, timeout):
            if isinstance(result, Exception):
                raise result
            return io.BytesIO(result)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda handler: Opener())
    with pytest.raises(AdapterError, match=reason):
        OllamaAdapter(spec())._request('/api/tags')


@pytest.mark.parametrize('response,reason', [
    ({'model': 'wrong', 'done': True, 'done_reason': 'stop', 'response': '{}'}, 'ollama_incomplete'),
    ({'model': 'qwen3:4b', 'done': False, 'done_reason': 'stop', 'response': '{}'}, 'ollama_incomplete'),
    ({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': ''}, 'ollama_empty'),
    ({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': 'x' * 33000}, 'ollama_response_too_large'),
    ({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': json.dumps({'replacement_return': 'return 1\nprint(2)', 'summary':'x','assumptions':[]})}, 'ollama_invalid_file_proposal'),
    ({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': json.dumps({'replacement_return': 'print(2)', 'summary':'x','assumptions':[]})}, 'ollama_invalid_file_proposal'),
    ({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': json.dumps({'replacement_return': 1, 'summary':'x','assumptions':[]})}, 'ollama_invalid_file_proposal'),
    ({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop', 'response': json.dumps({'replacement_return': 'return 1', 'summary':'x','assumptions':[], 'extra': 1})}, 'ollama_invalid_file_proposal'),
])
def test_model_response_failures(monkeypatch, response, reason):
    adapter = OllamaAdapter(spec())
    monkeypatch.setattr(adapter, '_request', lambda path, body=None:
                        {'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]} if path.endswith('tags') else response)
    with pytest.raises(AdapterError, match=reason):
        adapter.propose(prompt(), 32768)


def test_source_with_multiple_returns_is_rejected(monkeypatch):
    adapter = OllamaAdapter(spec())
    response = {'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop',
                'response': json.dumps({'replacement_return':'return 1', 'summary':'x', 'assumptions':[]})}
    monkeypatch.setattr(adapter, '_request', lambda path, body=None:
                        {'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]} if path.endswith('tags') else response)
    p = prompt().replace('    return text.lower()\\n', '    return text.lower()\\n    return text\\n')
    with pytest.raises(AdapterError, match='ollama_invalid_file_proposal'):
        adapter.propose(p, 32768)


def test_local_request_schema_validates_digest_and_model():
    req = json.loads((ROOT / 'demo/local/request.json').read_text())
    assert load_request(json.dumps(req).encode()).adapter.kind == 'local_model'
    for field, value in [('model_digest', 'bad'), ('model', 'unapproved')]:
        bad = json.loads(json.dumps(req))
        bad['adapter'][field] = value
        with pytest.raises(RequestError):
            load_request(json.dumps(bad).encode())


def test_local_recording_labels_and_raw_hash_binding(tmp_path, monkeypatch):
    raw_model = '{"replacement_return":"return text.lower()"}'
    raw_model_hash = hashlib.sha256(raw_model.encode()).hexdigest()
    live = {'provider': 'local Ollama', 'raw_model_response': raw_model, 'raw_model_sha256': raw_model_hash}
    path, digest = write_recording(tmp_path, run_id='local-1', raw=b'{}', live_record=live,
        prompt_sha256='a'*64, template_version='v1', template_sha256='b'*64,
        request_sha256='c'*64, base_commit='d'*40)
    assert json.loads(path.read_text())['label'] == LOCAL_RECORDING_LABEL
    adapter = ReplayAdapter(SimpleNamespace(recording_path=str(path), recording_sha256=digest, live_run_id='local-1'))
    identity = adapter.identity()
    assert identity['source_provider'] == 'local Ollama' and identity['raw_model_sha256'] == raw_model_hash
    entry = {'recording_sha256': digest, 'live_run_id': 'local-1', 'response_sha256': 'e'*64,
             'patch_sha256': 'f'*64, 'new_content_sha256': '1'*64, 'base_commit': 'd'*40,
             'allowlisted_file': 'src/labels.py', 'raw_model_sha256': '0'*64,
             'operator_approval': {'approved':True, 'approved_by':'operator','approved_at':'time',
                                   'reviewed_whole_resulting_file':True}}
    monkeypatch.setattr('ira.authorize.load_reviewed', lambda pinned: {'entries': [entry]})
    with pytest.raises(NotAuthorized, match='replay_raw_model_sha256_mismatch'):
        authorize(request_kind='replay_recording', adapter_identity=identity, response_sha256='e'*64,
                  patch_sha256='f'*64, new_content_sha256='1'*64, base_commit='d'*40,
                  allowlisted_file='src/labels.py', pinned={'base_commit':'d'*40}, prescreen_passed=True)
    del entry['raw_model_sha256']
    with pytest.raises(NotAuthorized, match='replay_raw_model_sha256_mismatch'):
        authorize(request_kind='replay_recording', adapter_identity=identity, response_sha256='e'*64,
                  patch_sha256='f'*64, new_content_sha256='1'*64, base_commit='d'*40,
                  allowlisted_file='src/labels.py', pinned={'base_commit':'d'*40}, prescreen_passed=True)
    entry['raw_model_sha256'] = raw_model_hash
    assert authorize(request_kind='replay_recording', adapter_identity=identity, response_sha256='e'*64,
                     patch_sha256='f'*64, new_content_sha256='1'*64, base_commit='d'*40,
                     allowlisted_file='src/labels.py', pinned={'base_commit':'d'*40}, prescreen_passed=True) == entry
    changed = json.loads(path.read_text())
    changed['live']['raw_model_response'] += 'tampered'
    path.write_text(json.dumps(changed))
    changed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(AdapterError, match='replay_model_raw_hash_mismatch'):
        ReplayAdapter(SimpleNamespace(recording_path=str(path), recording_sha256=changed_hash, live_run_id='local-1'))


def test_adapter_error_preserves_intermediate_prompt_role(monkeypatch, built_repo, tmp_path):
    from ira import runner
    from ira.evaluate import make_request

    request = make_request('good_trim_collapse', built_repo, 'local-error')
    request['adapter'] = {'kind': 'local_model', 'model': 'qwen3:4b', 'model_digest': DIGEST, 'timeout_s': 30}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    def fake(self, endpoint, body=None):
        if endpoint.endswith('tags'):
            return {'models': [{'name': 'qwen3:4b', 'digest': DIGEST}]}
        raise AdapterError('ollama_unavailable', 'simulated')
    monkeypatch.setattr(OllamaAdapter, '_request', fake)
    packet, _ = runner.execute(path, tmp_path / 'output', work_root=tmp_path / 'work')
    assert packet['terminal_status'] == 'adapter_error'
    assert packet['prompt']['role'] == 'intermediate template; actual prompt sent is in adapter.live'
    assert packet['adapter']['live']['actual_prompt_text']
