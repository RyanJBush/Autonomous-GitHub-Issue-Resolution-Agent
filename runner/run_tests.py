"""Trusted in-container test wrapper (pinned by hash in demo/pinned.json; mounted read-only at /trusted).

Runs the fixed pytest command in a child process and prints one JSON envelope line on stdout. Everything the child
writes goes to stderr. The child imports candidate code, so the envelope, the exit code and the JUnit XML are all
candidate-influenced evidence, not proof.
"""
import json
import os
import subprocess
import sys

JUNIT = "/tmp/ira/junit.xml"
MAX_JUNIT = 262_144
ARGS = ["-c", "/trusted/pytest.ini", "--rootdir", "/trusted", "--confcutdir", "/trusted", "-p", "no:cacheprovider",
        "-q", "-rA", "--junitxml", JUNIT, "/trusted/test_issue.py"]
BOOT = ("import sys; sys.path.insert(0, '/work/repo'); import pytest; "
        "raise SystemExit(pytest.main(%r))" % (ARGS,))


def main() -> int:
    os.makedirs("/tmp/ira", exist_ok=True)
    child = subprocess.run([sys.executable, "-I", "-B", "-c", BOOT], cwd="/trusted",
                           stdout=sys.stderr.fileno(), stderr=sys.stderr.fileno(), check=False)
    junit, truncated = None, False
    try:
        with open(JUNIT, "rb") as handle:
            data = handle.read(MAX_JUNIT + 1)
        truncated = len(data) > MAX_JUNIT
        junit = data[:MAX_JUNIT].decode("utf-8", "replace")
    except OSError:
        pass
    sys.stdout.write(json.dumps({"envelope": "ira.run_tests.v1", "pytest_exit": child.returncode,
                                 "junit": junit, "junit_truncated": truncated}) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
