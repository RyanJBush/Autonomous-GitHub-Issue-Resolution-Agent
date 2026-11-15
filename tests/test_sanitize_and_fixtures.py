import json
import subprocess
import sys

from conftest import PINNED, ROOT
from ira.adapters import SYNTHETIC_LABEL, load_cases
from ira.sanitize import code_span, fenced, visible, visible_block


def test_visible_escapes_and_truncates():
    assert visible("a\nb‮c\x1b", 100) == "a\\u000ab\\u202ec\\u001b"
    assert visible("x" * 50, 10).endswith("...(truncated)")


def test_visible_block_keeps_newlines_only():
    text, cut = visible_block("a\nb\tc\rd⁦", 100)
    assert text == "a\nb\\u0009c\\u000dd\\u2066" and not cut


def test_code_span_and_fence_cannot_be_closed_by_content():
    assert code_span("a`b", 50) == "``a`b``"
    out = fenced("```\nevil\n```", "diff")
    assert out.startswith("````diff\n") and out.endswith("````")


def test_fixtures_are_current_and_labeled():
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "make_fixtures.py"), "--check"])
    assert proc.returncode == 0, "fixtures drifted from tools/make_fixtures.py"
    manifest = json.loads((ROOT / "fixtures" / "cases.json").read_text())
    assert manifest["label"] == SYNTHETIC_LABEL and manifest["base_commit"] == PINNED["base_commit"]
    files = {p.name for p in (ROOT / "fixtures" / "responses").iterdir()}
    assert files == {c["file"] for c in load_cases() if c.get("file")}


def test_pinned_trusted_test_hash_matches_file():
    import hashlib
    t = PINNED["trusted_test"]
    assert hashlib.sha256((ROOT / t["path"]).read_bytes()).hexdigest() == t["sha256"]
    assert t["executed_in_m1"] is False
