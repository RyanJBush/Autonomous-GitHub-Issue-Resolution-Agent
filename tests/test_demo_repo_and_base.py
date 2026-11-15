import os
import subprocess

import pytest

from conftest import PINNED
from ira import demo_repo
from ira.base import BaseVerificationError, verify
from ira.gitcmd import run_git

REQ = {"identity": PINNED["identity"], "base_commit": PINNED["base_commit"],
       "allowlisted_file": PINNED["allowlisted_file"]}


def git_in(repo, *args):
    env = {"GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo), "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return run_git(list(args), cwd=repo, env_extra=env)


def code_of(repo, request=REQ, pinned=PINNED):
    with pytest.raises(BaseVerificationError) as info:
        verify(repo, pinned, request)
    return info.value.code


def test_build_is_reproducible_and_matches_pin(tmp_path):
    first, second = demo_repo.build(tmp_path / "a"), demo_repo.build(tmp_path / "b")
    assert first == second == PINNED["base_commit"]


def test_build_ignores_injected_git_config(tmp_path, monkeypatch):
    # Hostile ambient config (as the development host's proxy injects) must not change the commit.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Someone Else")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2030-01-01T00:00:00+0000")
    assert demo_repo.build(tmp_path / "c") == PINNED["base_commit"]


def test_build_refuses_existing_destination(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(FileExistsError):
        demo_repo.build(tmp_path / "x")


def test_verify_accepts_pristine_build(built_repo):
    vb = verify(built_repo, PINNED, REQ)
    assert vb.base_commit == PINNED["base_commit"] and vb.allowlisted_blob == PINNED["allowlisted_blob"]
    assert ("clean_tree_including_untracked_and_ignored", "pass") in vb.checks


@pytest.mark.parametrize("field,value", [("identity", "other-repo"), ("base_commit", "1" * 40),
                                         ("allowlisted_file", "src/util.py")])
def test_request_must_equal_pinned(built_repo, field, value):
    assert code_of(built_repo, {**REQ, field: value}) == "request_not_pinned"


def test_modified_tracked_file_is_dirty(fresh_repo):
    (fresh_repo / "src" / "util.py").write_text("x = 1\n")
    assert code_of(fresh_repo) == "dirty_tree"


def test_untracked_file_is_dirty(fresh_repo):
    (fresh_repo / "src" / "extra.py").write_text("")
    assert code_of(fresh_repo) == "dirty_tree"


def test_ignored_untracked_file_is_dirty(fresh_repo):
    (fresh_repo / ".git" / "info").mkdir(exist_ok=True)
    (fresh_repo / ".git" / "info" / "exclude").write_text("*.pyc\n")
    (fresh_repo / "src" / "cached.pyc").write_bytes(b"\0")
    assert code_of(fresh_repo) == "dirty_tree"


def test_staged_change_is_dirty(fresh_repo):
    (fresh_repo / "new.txt").write_text("n\n")
    git_in(fresh_repo, "add", "--", "new.txt")
    assert code_of(fresh_repo) == "dirty_tree"


def test_head_moved_to_new_commit(fresh_repo):
    (fresh_repo / "src" / "util.py").write_text("x = 2\n")
    git_in(fresh_repo, "add", "--all")
    git_in(fresh_repo, "commit", "-q", "-m", "change")
    assert code_of(fresh_repo) == "head_mismatch"


def test_fsmonitor_config_rejected_before_status_runs(fresh_repo, tmp_path):
    marker = tmp_path / "fsmonitor-ran"
    with open(fresh_repo / ".git" / "config", "a") as handle:
        handle.write(f"[core]\n\tfsmonitor = touch {marker}\n")
    assert code_of(fresh_repo) == "git_config_not_allowed"
    assert not marker.exists()


@pytest.mark.parametrize("snippet", ["[filter \"x\"]\n\tclean = cat\n", "[include]\n\tpath = /etc/other\n",
                                     "[core]\n\thooksPath = /tmp\n", "[core]\n\tbare = true\n",
                                     "[remote \"origin\"]\n\turl = https://example.invalid/x\n"])
def test_unexpected_local_config_rejected(fresh_repo, snippet):
    with open(fresh_repo / ".git" / "config", "a") as handle:
        handle.write(snippet)
    assert code_of(fresh_repo) == "git_config_not_allowed"


def test_gitfile_instead_of_directory_rejected(tmp_path, fresh_repo):
    other = tmp_path / "other"
    other.mkdir()
    (other / ".git").write_text(f"gitdir: {fresh_repo / '.git'}\n")
    assert code_of(other) == "git_dir_invalid"


def test_symlinked_repo_path_rejected(tmp_path, built_repo):
    link = tmp_path / "link"
    link.symlink_to(built_repo, target_is_directory=True)
    assert code_of(link) == "repo_not_directory"


@pytest.mark.parametrize("rel", ["objects/info/alternates", "commondir", "shallow", "info/grafts"])
def test_redirecting_git_files_rejected(fresh_repo, rel):
    target = fresh_repo / ".git" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")
    assert code_of(fresh_repo) == "git_dir_invalid"


def test_symlink_or_executable_allowlisted_file_refused(tmp_path):
    # A repo whose allowlisted path is a symlink (mode 120000) is refused even if pinned to that commit.
    repo = tmp_path / "s"
    repo.mkdir()
    run_git(["init", "-q", "--template=", str(repo)], cwd=repo)
    (repo / "src").mkdir()
    os.symlink("/etc/hostname", repo / "src" / "labels.py")
    git_in(repo, "add", "--all")
    git_in(repo, "commit", "-q", "-m", "s")
    head = git_in(repo, "rev-parse", "HEAD").stdout.decode().strip()
    tree = git_in(repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
    pinned = {**PINNED, "base_commit": head, "base_tree": tree}
    assert code_of(repo, {**REQ, "base_commit": head}, pinned) == "allowlisted_file_mode"


def test_ambient_git_environment_does_not_leak(fresh_repo, monkeypatch):
    # GIT_DIR in the caller's environment must not redirect verification to another repository.
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor=touch /tmp/ira-should-not-exist'")
    assert verify(fresh_repo, PINNED, REQ).base_commit == PINNED["base_commit"]
    assert not os.path.exists("/tmp/ira-should-not-exist")


def test_real_git_available():
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0
