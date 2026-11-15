import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ira import demo_repo  # noqa: E402
from ira.evaluate import make_request  # noqa: E402
from ira.models import Limits  # noqa: E402

PINNED = demo_repo.load_pinned()
BASE_BYTES = (ROOT / "demo" / "target" / PINNED["allowlisted_file"]).read_bytes()
CANARY = Path("/tmp/ira-m1-execution-canary")


@pytest.fixture(scope="session")
def built_repo(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "repo"
    demo_repo.build(path)
    return path


@pytest.fixture
def fresh_repo(tmp_path):
    path = tmp_path / "repo"
    demo_repo.build(path)
    return path


@pytest.fixture
def limits():
    return Limits(max_response_bytes=32768, max_patch_bytes=8192, max_hunks=3, max_added_lines=20,
                  max_removed_lines=20, max_changed_lines=30)


@pytest.fixture
def write_request(tmp_path):
    def _write(case_id, repo, run_id=None, **kw):
        req = make_request(case_id, repo, run_id or case_id, **kw)
        path = tmp_path / f"{run_id or case_id}.request.json"
        path.write_text(json.dumps(req))
        return path
    return _write


def fixture_response(case_id: str) -> dict:
    return json.loads((ROOT / "fixtures" / "responses" / f"{case_id}.json").read_text())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Every test is offline: any Python-level IPv4/IPv6 connection fails (docker and git run as subprocesses over
    local sockets or files and are unaffected)."""
    import socket
    real_connect = socket.socket.connect

    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError(f"network access blocked in tests: {address!r}")
        return real_connect(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("network access blocked in tests")))
