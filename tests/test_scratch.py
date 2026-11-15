import pytest

from conftest import BASE_BYTES
from ira import gitcmd, scratch
from ira.policy import validate
from ira.scratch import ApplyFailed, apply_and_confirm

P = "src/labels.py"
GOOD = (f"--- a/{P}\n+++ b/{P}\n@@ -6,1 +6,1 @@\n-    return text.lower()\n"
        "+    return \" \".join(text.lower().split())\n")


def test_apply_confirms_and_cleans_up(tmp_path, limits):
    vp = validate(GOOD, P, BASE_BYTES, limits)
    conf = apply_and_confirm(BASE_BYTES, vp, GOOD, tmp_path)
    assert conf.matches_policy_content and conf.numstat == (1, 1, P)
    assert list(tmp_path.iterdir()) == []  # scratch removed


def test_zero_context_hunks_apply(tmp_path, limits):
    diff = f"--- a/{P}\n+++ b/{P}\n@@ -0,0 +1 @@\n+# top\n@@ -6 +7 @@\n-    return text.lower()\n+    return 1\n"
    vp = validate(diff, P, BASE_BYTES, limits)
    assert apply_and_confirm(BASE_BYTES, vp, diff, tmp_path).numstat == (2, 1, P)


def test_git_apply_failure_is_apply_failed(tmp_path, limits, monkeypatch):
    vp = validate(GOOD, P, BASE_BYTES, limits)
    real = scratch.run_git

    def fake(args, **kw):
        if args[0] == "apply":
            return gitcmd.GitResult(tuple(args), 1, b"", b"")
        return real(args, **kw)
    monkeypatch.setattr(scratch, "run_git", fake)
    with pytest.raises(ApplyFailed) as info:
        apply_and_confirm(BASE_BYTES, vp, GOOD, tmp_path)
    assert info.value.code == "git_apply_check_failed"


def test_content_disagreement_is_apply_failed(tmp_path, limits):
    vp = validate(GOOD, P, BASE_BYTES, limits)
    other = GOOD.replace('" ".join', '"-".join')  # git applies this; policy computed a different result
    with pytest.raises(ApplyFailed) as info:
        apply_and_confirm(BASE_BYTES, vp, other, tmp_path)
    assert info.value.code == "confirm_content"


def test_count_disagreement_is_apply_failed(tmp_path, limits):
    vp = validate(GOOD, P, BASE_BYTES, limits)
    extra = GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,1 +6,2 @@") + "+    # extra\n"
    with pytest.raises(ApplyFailed) as info:
        apply_and_confirm(BASE_BYTES, vp, extra, tmp_path)
    assert info.value.code == "confirm_numstat"


def test_scratch_ignores_hostile_ambient_git_config(tmp_path, limits, monkeypatch):
    marker = tmp_path / "hook-ran"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"touch {marker}")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hooksPath={tmp_path}'")
    vp = validate(GOOD, P, BASE_BYTES, limits)
    apply_and_confirm(BASE_BYTES, vp, GOOD, tmp_path / "work")
    assert not marker.exists()


def test_controlled_env_drops_credentials(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("GH_TOKEN", "x")
    env = gitcmd.controlled_env()
    assert "AWS_SECRET_ACCESS_KEY" not in env and "GH_TOKEN" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_run_git_rejects_unlisted_subcommands_and_newlines(tmp_path):
    for bad in (["push"], ["clone", "x"], ["fetch"], ["-c", "x=y", "status"], ["status", "a\nb"]):
        with pytest.raises(gitcmd.GitError):
            gitcmd.run_git(bad, cwd=tmp_path)
